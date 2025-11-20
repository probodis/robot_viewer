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
logger.setLevel(logging.INFO)

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

def find_suitable_files(machine_id: str, timestamp: float) -> dict[str, Path] | None:
    """
    Search for suitable orders and start_order files for machine_id and timestamp.
    """
    start_time = time.perf_counter()
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    date_ts = dt.date()
    logger.info(f"Searching files for machine_id={machine_id} and date={date_ts}")
    orders_dir = DATA_DIR / machine_id / "logs" / "orders"
    start_order_dir = DATA_DIR / machine_id / "logs" / "start_order"

    def find_file(dir_path: Path, prefix: str):
        files = sorted(
            [f for f in dir_path.glob(f"*_{prefix}.txt*") if f.is_file()],
            key=lambda f: f.name,
            reverse=True
        )
        for f in files:
            match = re.match(r"(\d{4}-\d{2}-\d{2})_" + prefix + r"\.txt(\.gz)?$", f.name)
            if match:
                file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
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


def fetch_order_data(machine_id: str, order_id: float) -> OrderTelemetry | None:
    start_time = time.perf_counter()

    files = find_suitable_files(machine_id, order_id)
    if not files:
        logger.warning(f"No files found for machine_id={machine_id}, order_id={order_id}")
        return None

    start_order = fetch_telemetry_data(order_id, files["start_order"])
    if not start_order:
        logger.warning(f"No start_order data found for machine_id={machine_id}, order_id={order_id}")
        return None

    logger.info(f"Found start_order data for order_id={order_id}")

    video_url = find_video_file(machine_id, order_id)

    motors = dict()
    motor_names = [
        "truck", "screen", "revolver", "screw", "pump", "lifter", "spade", "clearance", "mixer"
    ]
    for motor in motor_names:
        # Compose keys for each metric
        velocity = {
            "time": start_order.get(f"{motor}_velocity_time", list()),
            "value": start_order.get(f"{motor}_velocity_value", list())
        }
        position = {
            "time": start_order.get(f"{motor}_position_time", list()),
            "value": start_order.get(f"{motor}_position_value", list())
        }
        state = {
            "time": start_order.get(f"{motor}_state_time", list()),
            "value": start_order.get(f"{motor}_state_value", list())
        }
        # Optional weight sensor (e.g., for screen)
        weight = None
        if f"{motor}_weight_time" in start_order and f"{motor}_weight_value" in start_order:
            weight = {
                "time": start_order.get(f"{motor}_weight_time", list()),
                "value": start_order.get(f"{motor}_weight_value", list())
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
            start_time=start_order.get("start_time", 0.0),
            end_time=start_order.get("end_time", 0.0),
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
