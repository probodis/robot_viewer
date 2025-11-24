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

def get_all_logs_files(machine_id: str, date_ts: str) -> dict[str, Path]:
    """
    Return all log files in subfolders of base_dir whose names start with the given date and end with .txt or .txt.gz.
    Prefer .txt.gz over .txt if both exist.

    Args:
        machine_id (str): Machine identifier.
        date_ts (str): Date string in 'YYYY-MM-DD' format.

    Returns:
        dict[str, Path]: Mapping from relative file path (str) to Path object.
    """
    # Accept date_ts as string and use isoformat directly for prefix
    result: dict[str, Path] = dict()
    base_dir = DATA_DIR / machine_id / "logs"
    date_prefix = f"{date_ts}_"

    # Walk through all subfolders in base_dir
    for subfolder in base_dir.glob("*"):
        if not subfolder.is_dir():
            continue

        # Find all .txt and .txt.gz files with the correct prefix
        txt_files = list(subfolder.glob(f"{date_prefix}*.txt"))
        gz_files = list(subfolder.glob(f"{date_prefix}*.txt.gz"))

        # Build a set of base filenames (without .gz) for deduplication
        gz_basenames = set(f.with_suffix("").name for f in gz_files)

        # Add .txt.gz files first (preferred)
        for gz_file in gz_files:
            key = str(gz_file.relative_to(base_dir))
            result[key] = gz_file

        # Add .txt files only if there is no corresponding .txt.gz
        for txt_file in txt_files:
            if txt_file.name in gz_basenames:
                continue  # Skip if .txt.gz exists
            key = str(txt_file.relative_to(base_dir))
            result[key] = txt_file

    return result


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

    # Regex patterns for timestamps:
    #   [YYYY-MM-DD HH:MM:SS]
    #   YYYY-MM-DD HH:MM:SS(.sss)
    ts_patterns = [
        re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]"),
        re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)")
    ]

    def parse_timestamp(line: str) -> float | None:
        """Return parsed timestamp as float seconds (naive → UTC naive)."""
        for pat in ts_patterns:
            m = pat.match(line)
            if m:
                ts_str = m.group(1)
                # try microseconds first
                try:
                    dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
                except ValueError:
                    try:
                        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        return None
                # naive datetime → timestamp (still naive)
                return dt.timestamp()
        return None

    for rel_path, file_path in log_files.items():
        lines_in_window = list()
        last_ts: float | None = None
        found_any_ts = False

        # time offset for this file (difference between file local time and UTC)
        file_offset: float | None = None

        if str(file_path).endswith(".gz"):
            open_func = lambda: gzip.open(file_path, "rt", encoding="utf-8")
        else:
            open_func = lambda: file_path.open("r", encoding="utf-8")

        try:
            with open_func() as f:
                for line in f:
                    # Try to parse timestamp from this line
                    ts_local = parse_timestamp(line)

                    if ts_local is not None:
                        found_any_ts = True

                        # Detect timezone offset using the first timestamp in this file
                        if file_offset is None:
                            # Very simple timezone detection:
                            # Compare the first log timestamp with expected order_id time.
                            # file_offset = (local time in file) - (UTC time)
                            file_offset = ts_local - order_id

                        # Convert file-local timestamp to UTC-corrected timestamp
                        ts_corrected = ts_local - file_offset
                        last_ts = ts_corrected

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


def find_suitable_files(machine_id: str, timestamp: float) -> dict[str, Path] | None:
    """
    Search for suitable orders and start_order files for machine_id and timestamp.
    """
    start_time = time.perf_counter()
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    date_ts = dt.date()
    orders_dir = DATA_DIR / machine_id / "logs" / "orders"
    start_order_dir = DATA_DIR / machine_id / "logs" / "start_order"
    logger.info(f"Searching files for machine_id={machine_id} and date={date_ts}, orders_dir={orders_dir}, start_order_dir={start_order_dir}")

    def find_file(dir_path: Path, prefix: str):
        files = sorted(
            [f for f in dir_path.glob(f"*_{prefix}.txt*") if f.is_file()],
            key=lambda f: f.name,
            reverse=True
        )
        logger.debug(f"Found {len(files)} files in {dir_path} for prefix={prefix}")
        for f in files:
            match = re.match(r"(\d{4}-\d{2}-\d{2})_" + prefix + r"\.txt(\.gz)?$", f.name)
            if match:
                file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
                logger.debug(f"Checking file {f} with date {file_date} against target date {date_ts}")
                if file_date <= date_ts:
                    
                    end_time = time.perf_counter()
                    logger.info(f"step=find_suitable_files status=completed duration={end_time - start_time:.3f}s")
                    return f
        end_time = time.perf_counter()
        logger.info(f"step=find_suitable_files status=completed duration={end_time - start_time:.3f}s")
        return None

    orders_file = find_file(orders_dir, "orders")
    start_order_file = find_file(start_order_dir, "start_order")

    if orders_file and start_order_file:
        logger.info(f"Found files: orders_file={orders_file}, start_order_file={start_order_file}, duration={time.perf_counter() - start_time:.3f}s")
        return {"orders": orders_file, "start_order": start_order_file}
    
    logger.warning(f"No suitable orders_file and start_order_file found for machine_id={machine_id} and date={date_ts}, duration={time.perf_counter() - start_time:.3f}s")

    return None

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


def extract_date_prefix(file_path: Path) -> str | None:
    """
    Extracts the date prefix (YYYY-MM-DD) from any file path in the logs directory, not just _orders files.

    Args:
        file_path (Path): Path to the log file.

    Returns:
        str | None: Extracted date prefix or None if not found.
    """
    # Accepts any file with a YYYY-MM-DD_ prefix in its name
    match = re.match(r".*/(\d{4}-\d{2}-\d{2})_.*\.txt(\.gz)?$", str(file_path))
    if match:
        return match.group(1)
    return None


def fetch_order_data(machine_id: str, order_id: float) -> OrderTelemetry | None:
    start_time = time.perf_counter()

    files = find_suitable_files(machine_id, order_id)
    if not files:
        logger.warning(f"No files found for machine_id={machine_id}, order_id={order_id}")
        return None
    
    # fetch_all_text_logs(machine_id)

    order_telemetry = fetch_telemetry_data(order_id, files["start_order"])
    if not order_telemetry:
        logger.warning(f"No start_order data found for machine_id={machine_id}, order_id={order_id}")
        return None

    logger.info(f"Found start_order data for order_id={order_id}")

    video_url = find_video_file(machine_id, order_id)

    end_order_ts = order_telemetry.get("end_time", 0.0)

    file_ts_prefix = extract_date_prefix(files["orders"])
    all_order_logs = None
    if file_ts_prefix is not None:
        log_files = get_all_logs_files(machine_id=machine_id, date_ts=file_ts_prefix)

        all_order_logs = fetch_all_order_logs(order_id=order_id, end_order_ts=end_order_ts, log_files=log_files)

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
