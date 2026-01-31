import json
import gzip
import logging
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from adapters.s3 import get_s3_client
from configs.config import get_config
from datetime import datetime, timezone

import re
from typing import Any
import base64

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.schemas.telemetry import OrderTelemetry
from app.schemas.video import MachineVideo
from app.utils import open_text
from app.constants import DATA_DIR, LOGS_DIR
from app.strategies.order_strategy import OrdersStrategy
from app.strategies.sauce_weight_strategy import SauceWeightStrategy
from app.infrastructure.filesystem.file_finder import find_suitable_files
from app.infrastructure.filesystem.file_selectors import TelemetryFileSelector, OrderFileSelector, SauceWeightFileSelector
from app.infrastructure.filesystem.log_window_extractor import fetch_all_order_logs
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
        super().__init__(filename=filename, when="midnight", interval=1, backupCount=30, encoding="utf-8", **kwargs)

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
    datefmt="%Y-%m-%dT%H:%M:%S%z"
)
handler.setFormatter(formatter)
logger.addHandler(handler)


app = FastAPI(
    title="Robot Telemetry API",
    description="API for serving pre-processed robot telemetry data and videos.",
    version="1.0.0"
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
        status_code = getattr(response, 'status_code', 'N/A') if response is not None else 'N/A'
        logger.info(f"Request finished: method={request.method} url={request.url} duration={duration:.3f}s status_code={status_code}")

# Allow Cross-Origin Resource Sharing (CORS) for the frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)


def fetch_telemetry_data(order_id: float, file: Path) -> dict[str, Any] | None:
    """
    Fetch telemetry data for a given order_id from a file.
    Args:
        order_id (float): Order identifier.
        file (Path): Path to telemetry file.
    Returns:
        dict[str, Any] | None: Telemetry data or None if not found.
    """
    start_time = time.perf_counter()

    tolerance = 3.0
    pattern = re.compile(r"'start_time':\s*([\d]+\.[\d]+|[\d]+)")

    try:
        with open_text(file) as f:
            for line in f:
                match = pattern.search(line)
                if match:
                    start_time = float(match.group(1))
                    if abs(start_time - order_id) <= tolerance:
                        json_str = line.replace("'", '"')
                        json_str = json_str.replace('None', 'null').replace('True', 'true').replace('False', 'false')
                        try:
                            data = json.loads(json_str)
                            logger.info(f"Telemetry data found for order_id={order_id} in file={file}, duration={time.perf_counter() - start_time:.3f}s")
                            return data
                        except Exception as e:
                            logger.error(f"Failed to parse telemetry data for order_id={order_id} in file={file}, duration={time.perf_counter() - start_time:.3f}s: {e}")
                            return None
    except Exception as e:
        logger.error(f"Error reading telemetry file {file}: {e}")
    logger.warning(f"No telemetry data found for order_id={order_id} in file={file}, duration={time.perf_counter() - start_time:.3f}s")
    return None

def find_video_file(machine_id: str, order_id: float) -> str | None:
    """
    Search for a matching video file in S3 within ±3 seconds of order_id and return its presigned URL if found.
    """
    start_time = time.perf_counter()

    config = get_config()
    s3_client = get_s3_client(config)

    tolerance = 3.0

    # Generate possible timestamps within tolerance window
    possible_timestamps = [order_id + offset for offset in range(-int(tolerance), int(tolerance) + 1)]
    checked_files = list()
    for ts in possible_timestamps:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        filename = dt.strftime("%Y-%m-%d_%H-%M-%S.mp4")
        s3_key = Path(f"xcubes/{machine_id}/logs/videos/{filename}")
        checked_files.append(str(s3_key))
        try:
            if s3_client.is_file_exist(s3_key=s3_key):
                logger.info(f"Found video file for machine_id={machine_id}, order_id={order_id}: {s3_key}, duration={time.perf_counter() - start_time:.3f}s")
                return s3_client.get_presigned_url(s3_key)
        except Exception as e:
            logger.error(f"Error checking video file existence for {s3_key}, duration={time.perf_counter() - start_time:.3f}s: {e}")
            continue

    logger.warning(f"No video file found for machine_id={machine_id}, order_id={order_id}, checked_files={checked_files}, duration={time.perf_counter() - start_time:.3f}s")
    return None


def fetch_order_data(machine_id: str, order_id: float) -> OrderTelemetry | None:
    start_time = time.perf_counter()

    logger.info(f"Find suitable files for machine_id={machine_id}, order_id={order_id}")
    files = find_suitable_files(machine_id, order_id)
    if not files:
        logger.warning(f"No files found for machine_id={machine_id}, order_id={order_id}")
        return None
    logger.info(f"Suitable files found for machine_id={machine_id}, order_id={order_id}: {list(files.keys())}")
    
    logger.info(f"Selecting telemetry file for machine_id={machine_id}, order_id={order_id}")
    telemetry_file = TelemetryFileSelector().select(files)
    if telemetry_file is None:
        logger.warning(f"No start_order telemetry file found for machine_id={machine_id}, order_id={order_id}")
        return None
    logger.info(f"Selected telemetry file for machine_id={machine_id}, order_id={order_id}: {telemetry_file}")

    logger.info(f"Fetching telemetry data for machine_id={machine_id}, order_id={order_id}")
    order_telemetry = fetch_telemetry_data(order_id, telemetry_file)
    if not order_telemetry:
        logger.warning(f"No start_order data found for machine_id={machine_id}, order_id={order_id}")
        return None
    logger.info(f"Found start_order data for order_id={order_id}")

    logger.info(f"Selecting orders file for machine_id={machine_id}, order_id={order_id}")
    orders_file = OrderFileSelector().select(files)
    logger.info(f"Selected orders file for machine_id={machine_id}, order_id={order_id}: {orders_file}")
    order_info = None
    if orders_file:
        order_strategy = OrdersStrategy()
        order_info = order_strategy.fetch_order(order_id=order_id, path=orders_file)
        if order_info:
            order_telemetry["start_time"] = order_info.start_time.timestamp()
            order_telemetry["end_time"] = order_info.end_time.timestamp()
            logger.info(f"Fetched order info from orders file for machine_id={machine_id}, order_id={order_id}")
        else:
            logger.warning(f"No order info found in orders file for machine_id={machine_id}, order_id={order_id}, orders_file={orders_file}")
    else:
        logger.warning(f"No orders file found for machine_id={machine_id}, order_id={order_id}")

    logger.info(f"Selecting sauce weight file for machine_id={machine_id}, order_id={order_id}")
    sauce_weight_file = SauceWeightFileSelector().select(files)
    logger.info(f"Selected sauce weight file for machine_id={machine_id}, order_id={order_id}: {sauce_weight_file}")
    sauce_points = list()
    if sauce_weight_file and order_info:
        sauce_strategy = SauceWeightStrategy()
        sauce_points = sauce_strategy.fetch_points(
            order=order_info,
            path=sauce_weight_file,
            name_prefix="Sauce"
        )
        if len(sauce_points) == 0:
            logger.warning(f"No sauce weight points found in sauce weight file for machine_id={machine_id}, order_id={order_id}, sauce_weight_file={sauce_weight_file}")
        else:
            logger.info(f"Fetched {len(sauce_points)} sauce weight points for machine_id={machine_id}, order_id={order_id}")

    logger.info(f"Searching for video file for machine_id={machine_id}, order_id={order_id}")
    video_url = find_video_file(machine_id, order_id)
    logger.info(f"Video URL found: {video_url is not None} for machine_id={machine_id}, order_id={order_id}")

    end_order_ts = order_telemetry.get("end_time", 0.0)

    logger.info(f"Fetching all order logs for machine_id={machine_id}, order_id={order_id}")
    all_order_logs = fetch_all_order_logs(order_id=order_id, end_order_ts=end_order_ts, log_files=files)
    all_order_logs = dict(sorted(all_order_logs.items()))
    # all_order_logs = dict()
    logger.info(f"Fetched {len(all_order_logs)} log files for machine_id={machine_id}, order_id={order_id}")

    logger.info(f"Composing motors data for machine_id={machine_id}, order_id={order_id}")
    motors = dict()
    motor_names = [
        "truck", "screen", "revolver", "screw", "pump", "lifter", "spade", "clearance", "mixer"
    ]
    for motor in motor_names:
        # Compose keys for each metric
        velocity = {
            "time": order_telemetry.get(f"{motor}_velocity_time", list()),
            "value": order_telemetry.get(f"{motor}_velocity_value", list())
        }
        position = {
            "time": order_telemetry.get(f"{motor}_position_time", list()),
            "value": order_telemetry.get(f"{motor}_position_value", list())
        }
        state = {
            "time": order_telemetry.get(f"{motor}_state_time", list()),
            "value": order_telemetry.get(f"{motor}_state_value", list())
        }
        # Optional weight sensor (e.g., for screen)
        weight = None
        if f"{motor}_weight_time" in order_telemetry and f"{motor}_weight_value" in order_telemetry:
            weight = {
                "time": order_telemetry.get(f"{motor}_weight_time", list()),
                "value": order_telemetry.get(f"{motor}_weight_value", list())
            }
        # Only add weight if present and non-empty
        if weight and (weight["time"] or weight["value"]):
            motor_data = {
                "velocity": velocity,
                "position": position,
                "state": state,
                "weight": weight
            }
        else:
            motor_data = {
                "velocity": velocity,
                "position": position,
                "state": state
            }
        motors[motor] = motor_data
    logger.info(f"Motors data composed for machine_id={machine_id}, order_id={order_id}")

    # Compose OrderTelemetry
    try:
        order_telemetry = OrderTelemetry(
            order_id=str(order_id),
            start_time=order_telemetry.get("start_time", 0.0),
            end_time=end_order_ts,
            logs=all_order_logs or dict(),
            video_path=video_url if video_url else "",
            motors=motors,
            extra_weight_points=sauce_points
        )
        logger.info(f"OrderTelemetry composed for machine_id={machine_id}, order_id={order_id}, duration={time.perf_counter() - start_time:.3f}s")
        return order_telemetry
    except Exception as e:
        logger.error(f"OrderTelemetry validation failed for machine_id={machine_id}, order_id={order_id}, duration={time.perf_counter() - start_time:.3f}s: {e}")
        return None


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
        logger.error(f"Failed to list machine videos: machine_id={machine_id} prefix='{folder}' error={repr(e)}")
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
def get_order_telemetry(machine_id: str, order_id: float):
    """
    Returns the complete telemetry data for a specific order.
    Args:
        machine_id (str): Machine identifier.
        order_id (float): Order identifier.
    Returns:
        OrderTelemetry: Telemetry data for the order.
    """
    start_time = time.perf_counter()
    logger.info(f"API request: /api/v1/orders/ machine_id={machine_id}, order_id={order_id}")

    order_data = fetch_order_data(machine_id=machine_id, order_id=order_id)

    if not order_data:
        logger.warning(f"Order with ID '{order_id}' not found for machine_id={machine_id}, duration={time.perf_counter() - start_time:.3f}s")
        raise HTTPException(
            status_code=404,
            detail=f"Order with ID '{order_id}' not found."
        )

    total_time = time.perf_counter() - start_time
    logger.info(f"Order telemetry returned for machine_id={machine_id}, order_id={order_id}, duration={total_time:.3f}s")
    return order_data


@app.get("/api/v1/log")
def get_log_file(machine_id: str, log_key: str):
    """
    Returns the content of a specific log file for a machine.
    Args:
        machine_id (str): Machine identifier.
        log_key (str): Relative path to the log file.
    Returns:
        dict: Log file content encoded in base64.
    """
    start_time = time.perf_counter()
    logger.info(f"API request: /api/v1/logs/ machine_id={machine_id}, log_key={log_key}")

    log_file_path = DATA_DIR / machine_id / "logs" / log_key
    if not log_file_path.exists() or not log_file_path.is_file():
        logger.error(f"Log file '{log_key}' not found for machine_id={machine_id}, duration={time.perf_counter() - start_time:.3f}s")
        raise HTTPException(
            status_code=404,
            detail=f"Log file '{log_key}' not found for machine '{machine_id}'."
        )

    try:
        if str(log_file_path).endswith('.gz'):
            with gzip.open(log_file_path, "rt", encoding="utf-8") as f:
                content = f.read()
        else:
            with log_file_path.open("r", encoding="utf-8") as f:
                content = f.read()
        
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("ascii")

        total_time = time.perf_counter() - start_time
        logger.info(f"Log file '{log_key}' returned for machine_id={machine_id}, duration={total_time:.3f}s")
        return {
            "path": str(log_file_path),
            "text": encoded_content
        }
    except Exception as e:
        logger.error(f"Error reading log file '{log_key}' for machine_id={machine_id}, duration={time.perf_counter() - start_time:.3f}s: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error reading log file '{log_key}' for machine '{machine_id}'."
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
