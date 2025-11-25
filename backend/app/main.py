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
from ._version import __version__ as backend_version


# Setup logging to file with daily rotation and custom filename format
LOGS_DIR = Path("/logs")
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

# Allow Cross-Origin Resource Sharing (CORS) for the frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

DATA_DIR = Path("/data")
PROCESSED_DATA_DIR = DATA_DIR / "processed_data"

ORDERS_DIR = DATA_DIR / "orders"
START_ORDER_DIR = DATA_DIR / "start_order"


def parse_timestamp(line: str, offset_h: float = 0) -> float | None:
    """
    Return parsed timestamp as float seconds (naive → UTC naive).
    
    Args:
        line (str): Log line to parse.
        offset_h (float): Offset in hours to apply to the parsed timestamp.
    """
    # Regex patterns for timestamps:
    #   [YYYY-MM-DD HH:MM:SS]
    #   YYYY-MM-DD HH:MM:SS(.sss)
    ts_pattern = re.compile(r"^\[?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]?")
    
    m = ts_pattern.match(line)
    if m:
        ts_str = m.group(1)
        # try microseconds first
        try:
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        # naive datetime → timestamp (still naive)
        return dt.timestamp() - offset_h * 3600.0
    return None


def fetch_all_order_logs(order_id: float, end_order_ts: float, log_files: dict[str, Path]) -> dict[str, dict[str, str]]:
    """
    Extracts a window of log lines from each file, starting 5 seconds before order_id and ending 5 seconds after end_ts.

    Args:
        order_id (float): Start timestamp of the order.
        end_order_ts (float): End timestamp of the order.
        log_files (dict[str, Path]): Mapping from relative file path to Path object.

    Returns:
        dict[str, dict[str, str]]: Mapping from relative file path to dict with 'path' and base64-encoded 'text'.
    """
    window_start = order_id - 5.0
    window_end = end_order_ts + 5.0

    result: dict[str, dict[str, str]] = dict()
    for rel_path, file_path in log_files.items():
        lines_in_window = list()
        last_ts: float | None = None
        found_any_ts = False

        offset_h = -8.0 if rel_path.startswith("subapps/") else 0.0

        if str(file_path).endswith(".gz"):
            open_func = lambda: gzip.open(file_path, "rt", encoding="utf-8")
        else:
            open_func = lambda: file_path.open("r", encoding="utf-8")

        try:
            with open_func() as f:
                for line in f:
                    # Try to parse timestamp from this line
                    ts_utc = parse_timestamp(line=line, offset_h=offset_h)

                    if ts_utc is not None:
                        found_any_ts = True

                        last_ts = ts_utc

                    # Use last found timestamp for lines without timestamps
                    if last_ts is not None and window_start <= last_ts <= window_end:
                        lines_in_window.append(line)
                        
                    elif last_ts is not None and last_ts > window_end:
                        # We passed the end of the window, stop reading the file
                        break

            if found_any_ts and lines_in_window:
                text = "".join(lines_in_window)
                encoded_text = base64.b64encode(text.encode("utf-8")).decode("ascii")
                result[rel_path] = {
                    "path": str(file_path),
                    "text": encoded_text
                }

        except Exception as e:
            logger.error(f"Failed to extract log window from {file_path}: {e}")

    return result


def find_suitable_files(machine_id: str, timestamp: float) -> dict[str, Path]:
    """
    Search for all suitable log files for a machine up to a given timestamp.
    Returns a dict with keys as relative paths from machine_id/logs and values as Path objects.
    For each subdirectory and suffix, only the latest file <= target_date is returned.
    """
    start_time = time.perf_counter()
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    target_date = dt.date()

    logs_base = DATA_DIR / machine_id / "logs"
    result: dict[str, Path] = {}

    logger.info(f"Searching files for machine_id={machine_id} and date={target_date}, logs_base={logs_base}")

    # Iterate over all subdirectories of logs_base
    for subdir in [d for d in logs_base.iterdir() if d.is_dir()]:
        latest_files: dict[str, Path] = {}  # suffix -> Path
        for file_path in sorted(subdir.iterdir(), reverse=True):
            if not file_path.is_file():
                continue

            # Match pattern: YYYY-MM-DD_suffix.txt or YYYY-MM-DD_suffix.txt.gz
            match = re.match(r"(\d{4}-\d{2}-\d{2})_(.+)\.txt(\.gz)?$", file_path.name)
            if not match:
                continue

            file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
            suffix = match.group(2)

            if file_date <= target_date and suffix not in latest_files:
                latest_files[suffix] = file_path
                logger.debug(f"Adding file {file_path} for suffix {suffix}")

        # Add found files to the result dict with relative path as key
        for f in latest_files.values():
            rel_path = f.relative_to(logs_base)
            result[str(rel_path)] = f

    end_time = time.perf_counter()
    logger.info(f"step=find_suitable_files status=completed duration={end_time - start_time:.3f}s, found {len(result)} files")

    return result


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

    # Determine if the file is gzipped or plain text
    if str(file).endswith('.gz'):
        open_func = lambda: gzip.open(file, "rt", encoding="utf-8")
    else:
        open_func = lambda: file.open("r", encoding="utf-8")

    try:
        with open_func() as f:
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

    s3_videos_folder = Path(f"xcubes/{machine_id}/logs/videos/")
    video_files = s3_client.list_files_in_folder(s3_videos_folder)

    for key in sorted(video_files, reverse=True):
        # file name stamp: YYYY-MM-DD_HH-MM-SS.mp4
        filename = Path(key).name
        match = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})\.mp4$", filename)
        if match:
            dt_str = f"{match.group(1)} {match.group(2)}:{match.group(3)}:{match.group(4)}"
            try:
                file_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                file_ts = file_dt.replace(tzinfo=timezone.utc).timestamp()
                if abs(file_ts - order_id) <= tolerance:
                    logger.info(f"Found video file for machine_id={machine_id}, order_id={order_id}: {key}, duration={time.perf_counter() - start_time:.3f}s")
                    return s3_client.get_presigned_url(Path(key))
            except Exception as e:
                logger.error(f"Error parsing video file date for {filename}, duration={time.perf_counter() - start_time:.3f}s: {e}")
                continue
    
    logger.warning(f"No video file found for machine_id={machine_id}, order_id={order_id}, duration={time.perf_counter() - start_time:.3f}s")
    return None


def get_telemetry_file_path(files: dict[str, Path]) -> Path | None:
    """
    Get the telemetry file path from the files dictionary.
    Args:
        files (dict[str, Path]): Mapping from relative file path to Path object.
    Returns:
        Path | None: Path to telemetry file or None if not found.
    """
    for rel_path, path in files.items():
        if rel_path.endswith("_start_order.txt") or rel_path.endswith("_start_order.txt.gz"):
            return path
    return None


def fetch_order_data(machine_id: str, order_id: float) -> OrderTelemetry | None:
    start_time = time.perf_counter()

    files = find_suitable_files(machine_id, order_id)
    if not files:
        logger.warning(f"No files found for machine_id={machine_id}, order_id={order_id}")
        return None
    
    # fetch_all_text_logs(machine_id)
    telemetry_file = get_telemetry_file_path(files)
    if telemetry_file is None:
        logger.warning(f"No start_order telemetry file found for machine_id={machine_id}, order_id={order_id}")
        return None
    order_telemetry = fetch_telemetry_data(order_id, telemetry_file)
    if not order_telemetry:
        logger.warning(f"No start_order data found for machine_id={machine_id}, order_id={order_id}")
        return None

    logger.info(f"Found start_order data for order_id={order_id}")

    video_url = find_video_file(machine_id, order_id)

    end_order_ts = order_telemetry.get("end_time", 0.0)

    all_order_logs = fetch_all_order_logs(order_id=order_id, end_order_ts=end_order_ts, log_files=files)

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

    # Compose OrderTelemetry
    try:
        order_telemetry = OrderTelemetry(
            order_id=str(order_id),
            start_time=order_telemetry.get("start_time", 0.0),
            end_time=end_order_ts,
            logs=all_order_logs or dict(),
            video_path=video_url if video_url else "",
            motors=motors
        )
        logger.info(f"OrderTelemetry composed for machine_id={machine_id}, order_id={order_id}, duration={time.perf_counter() - start_time:.3f}s")
        return order_telemetry
    except Exception as e:
        logger.error(f"OrderTelemetry validation failed for machine_id={machine_id}, order_id={order_id}, duration={time.perf_counter() - start_time:.3f}s: {e}")
        return None


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


@app.get("/api/v1/version")
def get_version():
    """
    Returns the backend application version.
    Returns:
        dict: Version information.
    """
    logger.info("API request: /api/v1/version")
    return {"version": backend_version}
