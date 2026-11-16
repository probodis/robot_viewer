import gzip
import zlib
import logging
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from adapters.s3 import get_s3_client
from configs.config import get_config
from datetime import datetime
from contextlib import asynccontextmanager
from threading import BoundedSemaphore
from pydantic import ValidationError
import httpx

import base64

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.schemas.telemetry import OrderTelemetry
from app.schemas.scanner import ScannerArchive
from app.schemas.video import MachineVideo
from app.schemas.log_file import LogFileInfo
from app.constants import DATA_DIR, LOGS_DIR
from adapters.postgrest import PostgrestOrdersRepository
from adapters.order_assets import S3OrderAssets
from app.services.order_viewer import OrderViewerService
from app.services.default_order_logs import AutomaticOrderLogsService
from app.services.order_logs import (
    OrderLogWindowService,
    DiagnosticWindowUnavailable,
    resolve_text_log,
    MAX_INPUT_BYTES,
)
from app.services.errors import (
    InvalidUpstreamResponse,
    UpstreamUnavailable,
    DiagnosticLogUnavailable,
)
from app.strategies.scanner_strategy import ScannerArchiveStrategy
from app.strategies.machine_logs_strategy import MachineLogsStrategy
from ._version import __version__ as backend_version


# Setup logging to file with daily rotation and custom filename format

LOGS_DIR.mkdir(parents=True, exist_ok=True)


class CustomDailyFileHandler(TimedRotatingFileHandler):
    """
    Custom handler to format log filename as YYYY-MM-DD_robot_viewer.log
    """

    def __init__(self, logs_dir: Path, **kwargs):
        self.logs_dir = logs_dir
        # Set initial filename
        filename = self._get_filename()
        super().__init__(
            filename=filename,
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8",
            **kwargs,
        )

    def _get_filename(self):
        date_str = datetime.now().strftime("%Y-%m-%d")
        return str(self.logs_dir / f"{date_str}_robot_viewer.log")

    def doRollover(self):
        self.baseFilename = self._get_filename()
        super().doRollover()


logger = logging.getLogger("robot_viewer")
logger.setLevel(logging.DEBUG)

handler = CustomDailyFileHandler(logs_dir=LOGS_DIR)
formatter = logging.Formatter(
    fmt="level=%(levelname)s time=%(asctime)s module=%(module)s func=%(funcName)s msg=%(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
handler.setFormatter(formatter)
logger.addHandler(handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Own one connection pool per worker and close it on worker shutdown."""
    try:
        config = get_config()
    except ValidationError as error:
        fields = ",".join(
            ".".join(str(part) for part in item["loc"])
            for item in error.errors(include_input=False)
        )
        logger.error("event=backend_configuration_invalid fields=%s", fields)
        raise RuntimeError(
            "Robot Viewer backend configuration is invalid; check configured field names in logs."
        ) from None
    with httpx.Client(
        base_url=str(config.POSTGREST_BASE_URL).rstrip("/") + "/",
        headers={"X-API-Key": config.POSTGREST_API_KEY.get_secret_value()},
        timeout=httpx.Timeout(
            config.POSTGREST_TIMEOUT_SECONDS,
            connect=config.POSTGREST_CONNECT_TIMEOUT_SECONDS,
        ),
        limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        follow_redirects=False,
    ) as client, httpx.Client(
        base_url=str(config.POSTGREST_BASE_URL).rstrip("/") + "/",
        headers={"X-API-Key": config.POSTGREST_API_KEY.get_secret_value()},
        timeout=httpx.Timeout(
            config.POSTGREST_TIMEOUT_SECONDS,
            connect=config.POSTGREST_CONNECT_TIMEOUT_SECONDS,
        ),
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        follow_redirects=False,
    ) as diagnostics_client:
        storage = get_s3_client(config)
        try:
            repository = PostgrestOrdersRepository(client)
            app.state.order_service = OrderViewerService(
                repository, S3OrderAssets(storage, config.TELEMETRY_MAX_BYTES)
            )
            diagnostics_repository = PostgrestOrdersRepository(diagnostics_client)
            app.state.order_log_service = OrderLogWindowService(
                diagnostics_repository, DATA_DIR
            )
            app.state.default_order_log_service = AutomaticOrderLogsService(
                diagnostics_repository, DATA_DIR
            )
            app.state.diagnostics_gate = BoundedSemaphore(1)
            logger.info("event=order_dependencies_ready version=%s", backend_version)
            yield
        finally:
            storage.client.close()


def get_order_service(request: Request) -> OrderViewerService:
    return request.app.state.order_service


def get_order_log_service(request: Request) -> OrderLogWindowService:
    return request.app.state.order_log_service


def get_default_order_log_service(request: Request) -> AutomaticOrderLogsService:
    return request.app.state.default_order_log_service


def reserve_diagnostics_slot(request: Request):
    """Reject excess log work instead of consuming every shared request thread.

    Each worker admits at most one automatic/manual log-window request, and its
    dedicated metadata pool cannot exhaust the main video/telemetry HTTP pool.
    """
    gate = request.app.state.diagnostics_gate
    if not gate.acquire(blocking=False):
        raise HTTPException(
            status_code=503,
            detail="Log diagnostics are busy. Retry logs shortly.",
            headers={"Retry-After": "2"},
        )
    try:
        yield
    finally:
        gate.release()


app = FastAPI(
    title="Robot Telemetry API",
    description="API for serving pre-processed robot telemetry data and videos.",
    version=backend_version,
    lifespan=lifespan,
)


# Middleware to log request start/end and duration for all API calls
@app.middleware("http")
async def log_request_time(request, call_next):
    """
    Logs the start, end, and duration of each HTTP request for performance monitoring.
    Args:
        request: FastAPI request object.
        call_next: Function to process the request.
    Returns:
        Response object from downstream handler.
    """
    start_time = time.perf_counter()
    logger.info(f"Request started: method={request.method} url={request.url}")
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        duration = time.perf_counter() - start_time
        status_code = (
            getattr(response, "status_code", "N/A") if response is not None else "N/A"
        )
        logger.info(
            f"Request finished: method={request.method} url={request.url} duration={duration:.3f}s status_code={status_code}"
        )


# Allow Cross-Origin Resource Sharing (CORS) for the frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)


def _get_latest_machine_videos(machine_id: str, limit: int = 10) -> list[MachineVideo]:
    """Return latest available videos for a machine.

    Args:
        machine_id: Machine identifier.
        limit: Maximum number of videos to return.

    Returns:
        List of MachineVideo with presigned URLs.
    """
    start_time = time.perf_counter()

    config = get_config()
    s3_client = get_s3_client(config)

    folder = Path(f"xcubes/{machine_id}/logs/videos/")
    logger.info(f"Listing machine videos: machine_id={machine_id} prefix='{folder}'")

    try:
        keys = s3_client.list_files_in_folder(folder=folder, limit=10, reverse=True)
    except Exception as e:
        logger.error(
            f"Failed to list machine videos: machine_id={machine_id} prefix='{folder}' error={repr(e)}"
        )
        raise

    if not keys:
        logger.info(
            f"No machine videos found: machine_id={machine_id} prefix='{folder}' duration={time.perf_counter() - start_time:.3f}s"
        )
        return list()

    items: list[tuple[str, str]] = []
    for key in keys:
        filename = key.split("/")[-1]
        items.append((key, filename))

    # Filenames include timestamp; lexicographic sort matches chronological order.
    items.sort(key=lambda it: it[1])

    latest = items[-limit:]
    result: list[MachineVideo] = []
    for key, filename in latest:
        try:
            url = s3_client.get_presigned_url(Path(key))
        except Exception as e:
            logger.error(
                f"Failed to generate presigned URL for video: machine_id={machine_id} key='{key}' error={repr(e)}"
            )
            continue
        result.append(MachineVideo(filename=filename, url=url))

    logger.info(
        f"Machine videos listed: machine_id={machine_id} found={len(keys)} returned={len(result)} duration={time.perf_counter() - start_time:.3f}s"
    )
    return result


@app.get("/api/v1/orders/", response_model=OrderTelemetry)
def get_order_telemetry(
    machine_id: str,
    order_id: float,
    service: OrderViewerService = Depends(get_order_service),
):
    """Read one indexed order and its referenced JSON; no filesystem discovery."""
    try:
        result = service.get_order(machine_id, order_id)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="Invalid machine identifier or order timestamp."
        ) from None
    except UpstreamUnavailable:
        raise HTTPException(
            status_code=503, detail="Order metadata service is temporarily unavailable."
        ) from None
    except InvalidUpstreamResponse:
        raise HTTPException(
            status_code=502,
            detail="Order metadata service returned an invalid response.",
        ) from None
    if result is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    return result


@app.get("/api/v1/orders/logs")
def get_default_order_logs(
    machine_id: str,
    order_id: float,
    service: AutomaticOrderLogsService = Depends(get_default_order_log_service),
    _slot: None = Depends(reserve_diagnostics_slot),
):
    """Load the legacy default log tabs separately from order/video/telemetry."""
    try:
        result = service.get_logs(machine_id, order_id)
    except DiagnosticWindowUnavailable:
        raise HTTPException(
            status_code=409, detail="Order has no bounded diagnostic time window."
        ) from None
    except ValueError:
        raise HTTPException(
            status_code=422, detail="Invalid machine identifier or order timestamp."
        ) from None
    except InvalidUpstreamResponse:
        raise HTTPException(
            status_code=502,
            detail="Order metadata service returned an invalid response.",
        ) from None
    except (UpstreamUnavailable, OSError):
        raise HTTPException(
            status_code=503, detail="Order logs are temporarily unavailable."
        ) from None
    if result is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    return result


@app.get("/api/v1/orders/log-window")
def get_order_log_window(
    machine_id: str,
    order_id: float,
    log_key: str,
    service: OrderLogWindowService = Depends(get_order_log_service),
    _slot: None = Depends(reserve_diagnostics_slot),
):
    """Read a bounded UTC window from one explicitly selected text log."""
    try:
        result = service.get_window(machine_id, order_id, log_key)
    except DiagnosticWindowUnavailable:
        raise HTTPException(
            status_code=409, detail="Order has no bounded diagnostic time window."
        ) from None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid machine identifier, order timestamp or text log key.",
        ) from None
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail="Requested text log is unavailable."
        ) from None
    except DiagnosticLogUnavailable:
        raise HTTPException(
            status_code=503, detail="Requested text log could not be read."
        ) from None
    except UpstreamUnavailable:
        raise HTTPException(
            status_code=503, detail="Order metadata service is temporarily unavailable."
        ) from None
    except InvalidUpstreamResponse:
        raise HTTPException(
            status_code=502,
            detail="Order metadata service returned an invalid response.",
        ) from None
    except OSError:
        raise HTTPException(
            status_code=503, detail="Requested text log could not be read."
        ) from None
    if result is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    return result


@app.get("/api/v1/orders/scanner", response_model=ScannerArchive)
def get_scanner_archive(machine_id: str, order_id: float):
    """Return a presigned URL for the pizza-scanner archive of a given order.

    Searches ``xcubes/{machine_id}/logs/scanner/`` on S3 for the ``.zip``
    file whose timestamp is closest to *order_id* (within 60 s tolerance).
    """
    start_time = time.perf_counter()
    logger.info(
        f"API request: /api/v1/orders/scanner machine_id={machine_id}, order_id={order_id}"
    )

    config = get_config()
    s3_client = get_s3_client(config)

    strategy = ScannerArchiveStrategy(s3_client)
    try:
        result = strategy.find_archive_key(machine_id=machine_id, order_id=order_id)
    except Exception as e:
        logger.error(
            f"Scanner archive lookup failed: machine_id={machine_id} order_id={order_id} "
            f"duration={time.perf_counter() - start_time:.3f}s error={repr(e)}"
        )
        raise HTTPException(
            status_code=500, detail="Failed to search for scanner archive."
        )

    if result is None:
        logger.warning(
            f"Scanner archive not found: machine_id={machine_id} order_id={order_id} "
            f"duration={time.perf_counter() - start_time:.3f}s"
        )
        raise HTTPException(
            status_code=404,
            detail=f"Scanner archive not found for machine '{machine_id}', order '{order_id}'.",
        )

    s3_key, filename = result

    try:
        url = s3_client.get_presigned_url(Path(s3_key), expires_in=60 * 60 * 24)
    except Exception as e:
        logger.error(
            f"Presigned URL generation failed: key='{s3_key}' "
            f"duration={time.perf_counter() - start_time:.3f}s error={repr(e)}"
        )
        raise HTTPException(status_code=500, detail="Failed to generate download URL.")

    logger.info(
        f"Scanner archive returned: machine_id={machine_id} order_id={order_id} "
        f"file='{filename}' duration={time.perf_counter() - start_time:.3f}s"
    )
    return ScannerArchive(filename=filename, url=url)


@app.get("/api/v1/log")
def get_log_file(machine_id: str, log_key: str):
    """Read one explicit text log with the same path and size limits as diagnostics."""
    try:
        path = resolve_text_log(DATA_DIR, machine_id, log_key)
        if not path.is_file():
            raise FileNotFoundError
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rb") as stream:
            content = stream.read(MAX_INPUT_BYTES + 1)
        if len(content) > MAX_INPUT_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Text log exceeds the 10 MiB limit; download the file instead.",
            )
        text = content.decode("utf-8", errors="replace")
    except ValueError:
        raise HTTPException(
            status_code=422, detail="Invalid machine identifier or text log key."
        ) from None
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail="Requested text log is unavailable."
        ) from None
    except (OSError, EOFError, zlib.error):
        raise HTTPException(
            status_code=503, detail="Requested text log could not be read."
        ) from None
    logger.info(
        "event=full_text_log_loaded machine_id=%s log_key=%s bytes=%s",
        machine_id,
        log_key,
        len(content),
    )
    return {
        "path": log_key,
        "text": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }


@app.get("/api/v1/machine/logs", response_model=list[LogFileInfo])
def list_machine_logs(machine_id: str):
    """Return a list of available text log files for a machine.

    Excludes backup copies whose stem ends with 12 digits (``_NNNNNNNNNNNN``).

    Args:
        machine_id: Machine identifier.

    Returns:
        Sorted list of log file metadata.
    """
    start_time = time.perf_counter()
    logger.info(f"API request: /api/v1/machine/logs machine_id={machine_id}")

    strategy = MachineLogsStrategy(DATA_DIR)
    try:
        files = strategy.list_log_files(machine_id)
    except ValueError:
        logger.warning(
            f"Invalid machine_id rejected: machine_id={machine_id} "
            f"duration={time.perf_counter() - start_time:.3f}s"
        )
        raise HTTPException(status_code=400, detail="Invalid machine identifier.")
    except FileNotFoundError:
        logger.warning(
            f"Logs directory not found: machine_id={machine_id} "
            f"duration={time.perf_counter() - start_time:.3f}s"
        )
        raise HTTPException(
            status_code=404,
            detail=f"Logs directory not found for machine '{machine_id}'.",
        )
    except Exception as e:
        logger.error(
            f"Failed to list log files: machine_id={machine_id} "
            f"duration={time.perf_counter() - start_time:.3f}s error={repr(e)}"
        )
        raise HTTPException(status_code=500, detail="Failed to list log files.")

    logger.info(
        f"Machine logs listed: machine_id={machine_id} count={len(files)} "
        f"duration={time.perf_counter() - start_time:.3f}s"
    )
    return files


@app.get("/api/v1/machine/logs/download")
def download_machine_log(machine_id: str, log_key: str):
    """Download a specific log file for a machine as-is.

    The ``log_key`` parameter should be a ``relative_path`` value obtained
    from the ``GET /api/v1/machine/logs`` listing endpoint.

    Args:
        machine_id: Machine identifier.
        log_key: Relative path to the log file inside the machine's logs directory.

    Returns:
        Streaming file response with ``Content-Disposition: attachment``.
    """
    start_time = time.perf_counter()
    logger.info(
        f"API request: /api/v1/machine/logs/download machine_id={machine_id} log_key={log_key}"
    )

    strategy = MachineLogsStrategy(DATA_DIR)
    try:
        file_path = strategy.resolve_log_file(machine_id, log_key)
    except ValueError:
        logger.warning(
            f"Invalid log key rejected: machine_id={machine_id} log_key={log_key} "
            f"duration={time.perf_counter() - start_time:.3f}s"
        )
        raise HTTPException(status_code=400, detail="Invalid log key.")
    except FileNotFoundError:
        logger.warning(
            f"Log file not found: machine_id={machine_id} log_key={log_key} "
            f"duration={time.perf_counter() - start_time:.3f}s"
        )
        raise HTTPException(
            status_code=404,
            detail=f"Log file '{log_key}' not found for machine '{machine_id}'.",
        )

    media_type = (
        "application/gzip"
        if file_path.name.endswith(".gz")
        else "text/plain; charset=utf-8"
    )

    def _iter_file():
        chunk_size = 64 * 1024
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    logger.info(
        f"Streaming log file: machine_id={machine_id} log_key={log_key} "
        f"file={file_path} duration={time.perf_counter() - start_time:.3f}s"
    )
    return StreamingResponse(
        _iter_file(),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{file_path.name}"'},
    )


@app.get("/api/v1/version")
def get_version():
    """
    Returns the backend application version.
    Returns:
        dict: Version information.
    """
    logger.info("API request: /api/v1/version")
    return {"version": backend_version}


@app.get("/api/v1/machine/videos", response_model=list[MachineVideo])
def get_machine_videos(machine_id: str):
    """Return last 10 available videos for a machine.

    Args:
        machine_id: Machine identifier.

    Returns:
        List of videos (filename + presigned URL). Returns an empty list if nothing found.
    """
    start_time = time.perf_counter()
    logger.info(f"API request: /api/v1/machine/videos machine_id={machine_id}")

    try:
        videos = _get_latest_machine_videos(machine_id=machine_id, limit=10)
    except Exception as e:
        logger.error(
            f"Machine videos endpoint failed: machine_id={machine_id} duration={time.perf_counter() - start_time:.3f}s error={repr(e)}"
        )
        raise HTTPException(status_code=500, detail="Failed to list machine videos.")

    logger.info(
        f"Machine videos returned: machine_id={machine_id} count={len(videos)} duration={time.perf_counter() - start_time:.3f}s"
    )
    return videos
