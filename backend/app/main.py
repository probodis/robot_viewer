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
from app.utils import open_text
from app.constants import DATA_DIR, LOGS_DIR
from app.strategies.order_strategy import OrdersStrategy
from app.strategies.sauce_weight_strategy import SauceWeightStrategy
from app.infrastructure.filesystem.file_finder import find_suitable_files
from app.infrastructure.filesystem.file_selectors import TelemetryFileSelector, OrderFileSelector, SauceWeightFileSelector
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
    try:
        response = await call_next(request)
        return response
    finally:
        duration = time.perf_counter() - start_time
        logger.info(f"Request finished: method={request.method} url={request.url} duration={duration:.3f}s status_code={getattr(response, 'status_code', 'N/A')}")

# Allow Cross-Origin Resource Sharing (CORS) for the frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)


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

        offset_prefixes = ["subapps/", "console/"]
        offset_h = -8.0 if any(rel_path.startswith(prefix) for prefix in offset_prefixes) else 0.0

        logger.info(f"Extracting log window from {file_path} for order_id={order_id}, window=({window_start}, {window_end}), offset_h={offset_h}")

        try:
            with open_text(file_path) as f:
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
                
                logger.info(f"Extracted {len(lines_in_window)} lines from {file_path} for order_id={order_id}")
            else:
                # No lines in window, show fallback message
                text = (
                    "=== No time window found in this file ===\n"
                    "To open full file, double-click the tab.\n"
                )
                logger.info(f"No window found in {file_path} for order_id={order_id}, fallback message returned")
            
            encoded_text = base64.b64encode(text.encode("utf-8")).decode("ascii")
            result[rel_path] = {
                "path": str(file_path),
                "text": encoded_text
            }
            
        except Exception as e:
            logger.error(f"Failed to extract log window from {file_path}: {e}")

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
