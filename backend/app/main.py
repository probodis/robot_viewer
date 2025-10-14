import json
from pathlib import Path
import datetime
import re


from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse

from app.schemas.telemetry import OrderTelemetry
from ._version import __version__ as backend_version

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
VIDEO_DIR = DATA_DIR / "videos"

ORDERS_DIR = DATA_DIR / "orders"
START_ORDER_DIR = DATA_DIR / "start_order"

def find_suitable_files(timestamp: float) -> dict | None:
    """
    Given a timestamp (UTC float), returns the file names for orders and start_order
    that may contain this timestamp. Search is based only on the file name (date).
    """
    dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
    for delta in range(0, 3):
        check_date = dt - datetime.timedelta(days=delta)
        print(f"Checking date: {check_date}")
        date_str = check_date.strftime("%Y-%m-%d")
        print(f"Looking for files with date: {date_str}")
        orders_file = ORDERS_DIR / f"{date_str}_orders.txt"
        print(f"Orders file path: {orders_file}")
        start_order_file = START_ORDER_DIR / f"{date_str}_start_order.txt"
        if orders_file.exists() and start_order_file.exists():
            return {"orders": orders_file, "start_order": start_order_file}

    return None

def fetch_telemetry_data(order_id: float, file: Path) -> dict | None:
    tolerance = 2.0
    pattern = re.compile(r"'start_time':\s*([\d]+\.[\d]+|[\d]+)")

    with file.open("r", encoding="utf-8") as f:
        for line in f:
            match = pattern.search(line)
            if match:
                start_time = float(match.group(1))
                if abs(start_time - order_id) <= tolerance:
                    json_str = line.replace("'", '"')
                    # Replace Python literals with JSON literals
                    json_str = json_str.replace('None', 'null').replace('True', 'true').replace('False', 'false')
                    try:
                        data = json.loads(json_str)
                        return data
                    except Exception:
                        return None
    return None


def fetch_order_data(order_id: float) -> OrderTelemetry | None:
    files = find_suitable_files(order_id)
    if not files:
        return None

    start_order = fetch_telemetry_data(order_id, files["start_order"])
    if not start_order:
        return None

    # Compose OrderTelemetry fields
    # Required fields: order_id, start_time, end_time, video_filename, motors

    # Try to find a matching video file in VIDEO_DIR within ±2 seconds of order_id
    video_filename = start_order.get("video_filename", "")
    if not video_filename:
        # Search for video file by timestamp in filename
        import os
        from datetime import datetime, timezone, timedelta
        found = False
        order_dt = datetime.fromtimestamp(order_id, tz=timezone.utc)
        for delta in range(-2, 3):
            candidate_dt = order_dt + timedelta(seconds=delta)
            candidate_name = candidate_dt.strftime("%Y-%m-%d_%H-%M-%S.mp4")
            candidate_path = VIDEO_DIR / candidate_name
            if candidate_path.is_file():
                video_filename = candidate_name
                found = True
                break
        if not found:
            video_filename = ""

    # Build motors dict
    motors = {}
    # Known motor names (from your example)
    motor_names = [
        "truck", "screen", "revolver", "screw", "pump", "lifter", "spade", "clearance", "mixer"
    ]
    for motor in motor_names:
        # Compose keys for each metric
        velocity = {
            "time": start_order.get(f"{motor}_velocity_time", []),
            "value": start_order.get(f"{motor}_velocity_value", [])
        }
        position = {
            "time": start_order.get(f"{motor}_position_time", []),
            "value": start_order.get(f"{motor}_position_value", [])
        }
        state = {
            "time": start_order.get(f"{motor}_state_time", []),
            "value": start_order.get(f"{motor}_state_value", [])
        }
        # Optional weight sensor (e.g., for screen)
        weight = None
        if f"{motor}_weight_time" in start_order and f"{motor}_weight_value" in start_order:
            weight = {
                "time": start_order.get(f"{motor}_weight_time", []),
                "value": start_order.get(f"{motor}_weight_value", [])
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
            video_filename=video_filename,
            motors=motors
        )
        return order_telemetry
    except Exception as e:
        # If model validation fails, return None
        return None

@app.get("/api/v1/orders/{order_id}", response_model=OrderTelemetry)
def get_order_telemetry(order_id: str):
    """Returns the complete telemetry data for a specific order."""
    order_data = fetch_order_data(float(order_id))
    if not order_data:
        raise HTTPException(
            status_code=404,
            detail=f"Order with ID '{order_id}' not found."
        )
    return order_data


@app.get("/videos/{video_filename}")
async def get_video(video_filename: str):
    """Serves a video file from the 'videos' directory."""
    video_path = VIDEO_DIR / video_filename
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="Video file not found.")
    return FileResponse(video_path)


@app.get("/api/v1/version")
def get_version():
    """Returns the backend application version."""
    return {"version": backend_version}
