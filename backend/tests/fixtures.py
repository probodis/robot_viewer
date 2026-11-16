"""Regression fixtures use the cross-midnight order from the November incident."""
from copy import deepcopy
from unittest.mock import Mock

MACHINE_ID = "cb-3-0007"
ORDER_ID = 1794011078.328
TELEMETRY_KEY = "start_order_converted/1794011078.328.json"
VIDEO_KEY = "videos/2026-11-07_00-24-38.mp4"
SIGNED_VIDEO_URL = "https://video.example.invalid/object?signature=not-a-secret"


def order_record(**overrides):
    from app.schemas.order_assets import OrderRecord
    fields = {
        "machine_id": MACHINE_ID,
        "uid": ORDER_ID,
        "telemetry_storage_file": TELEMETRY_KEY,
        "video_storage_file": VIDEO_KEY,
        "total_time": 16.82596731185913,
    }
    fields.update(overrides)
    return OrderRecord(**fields)


def telemetry_data(**overrides):
    data = {
        "start_time": 1794011078.318,
        "end_time": 1794011095.167,
        "truck_velocity_time": [0.0, 0.2],
        "truck_velocity_value": [0.0, 11.616],
        "truck_position_time": [0.0, 0.2],
        "truck_position_value": [0.0, 1.0],
        "truck_state_time": [0.0, 0.2],
        "truck_state_value": ["idle", "moving"],
    }
    data.update(deepcopy(overrides))
    return data


def service_dependencies(record=None, telemetry=None):
    repository = Mock(spec=["get_order"])
    repository.get_order.return_value = record if record is not None else order_record()
    assets = Mock(spec=["read_telemetry", "create_video_url"])
    assets.read_telemetry.return_value = telemetry if telemetry is not None else telemetry_data()
    assets.create_video_url.return_value = SIGNED_VIDEO_URL
    return repository, assets
