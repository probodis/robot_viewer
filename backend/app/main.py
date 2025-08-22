import json
from pathlib import Path

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

TELEMETRY_CACHE: dict[str, OrderTelemetry] = dict()


def load_telemetry_data():
    """
    Finds all processed .json files, validates them with Pydantic,
    and loads them into the in-memory TELEMETRY_CACHE.
    """
    # Clear the cache to reload the data
    TELEMETRY_CACHE.clear()
    print("--- Loading processed data into memory ---")
    if not PROCESSED_DATA_DIR.exists():
        print(f"Warning: Processed data directory not found at {PROCESSED_DATA_DIR}")
        return

    loaded_count = 0
    for json_file in PROCESSED_DATA_DIR.glob("*.json"):
        try:
            order_id = json_file.stem
            with open(json_file, "r") as f:
                data = json.load(f)
                TELEMETRY_CACHE[order_id] = OrderTelemetry(**data)
            loaded_count += 1
        except Exception as e:
            print(f"Failed to load or validate {json_file.name}. Error: {e}")

    print(f"Successfully loaded and validated {loaded_count} order(s). API is ready.")


@app.on_event("startup")
def startup_event():
    """Load data on startup."""
    load_telemetry_data()


@app.get("/api/v1/orders", response_model=list[str])
def get_order_list():
    """
    Reloads telemetry data from files and returns a list of available order IDs.
    """
    load_telemetry_data()  # Reload data on each request to pick up changes
    if not TELEMETRY_CACHE:
        return []
    return list(TELEMETRY_CACHE.keys())


@app.get("/api/v1/orders/{order_id}", response_model=OrderTelemetry)
def get_order_telemetry(order_id: str):
    """Returns the complete telemetry data for a specific order."""
    order_data = TELEMETRY_CACHE.get(order_id)
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
