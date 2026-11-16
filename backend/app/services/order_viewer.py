"""Compose viewer data from explicit order references, independently per asset."""

import logging
import math
import time

from pydantic import ValidationError

from adapters.order_assets import validate_order_identity
from app.ports.order_data import OrdersRepository, OrderAssets
from app.schemas.telemetry import OrderTelemetry, MotorNodeData
from app.services.errors import (
    InvalidStorageReference,
    InvalidTelemetry,
    UpstreamUnavailable,
)

logger = logging.getLogger("robot_viewer")
MOTOR_NAMES = (
    "truck",
    "screen",
    "revolver",
    "screw",
    "pump",
    "lifter",
    "spade",
    "clearance",
    "mixer",
)


def _build_motors(data: dict) -> dict[str, MotorNodeData]:
    motors = {}
    for name in MOTOR_NAMES:
        series = {}
        for metric in ("velocity", "position", "state", "weight"):
            times = data.get(f"{name}_{metric}_time", [])
            values = data.get(f"{name}_{metric}_value", [])
            if (
                not isinstance(times, list)
                or not isinstance(values, list)
                or len(times) != len(values)
            ):
                raise InvalidTelemetry("Telemetry contains inconsistent time series.")
            if metric == "weight" and not times:
                continue
            series[metric] = {"time": times, "value": values}
        motors[name] = MotorNodeData.model_validate(series)
    return motors


def _parse_telemetry(
    data: dict, order_id: float
) -> tuple[float, float, dict[str, MotorNodeData]]:
    try:
        start = float(data["start_time"])
        end = float(data["end_time"])
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or abs(start - order_id) > 3
            or end < start
        ):
            raise ValueError
        return start, end, _build_motors(data)
    except (KeyError, ValueError, TypeError, OverflowError, ValidationError):
        raise InvalidTelemetry(
            "Telemetry is invalid or belongs to another order."
        ) from None


class OrderViewerService:
    def __init__(self, repository: OrdersRepository, assets: OrderAssets):
        self._repository = repository
        self._assets = assets

    def get_order(self, machine_id: str, order_id: float) -> OrderTelemetry | None:
        validate_order_identity(machine_id, order_id)
        started = time.perf_counter()
        record = self._repository.get_order(machine_id, order_id)
        if record is None:
            return None
        start, end = order_id, order_id + (record.total_time or 0)
        if not math.isfinite(end):
            end = order_id
        motors = {}
        telemetry_status = "not_recorded"
        if record.telemetry_storage_file:
            try:
                data = self._assets.read_telemetry(
                    machine_id, record.telemetry_storage_file
                )
                if data is None:
                    telemetry_status = "not_uploaded"
                else:
                    start, end, motors = _parse_telemetry(data, order_id)
                    telemetry_status = "available"
            except (InvalidTelemetry, InvalidStorageReference):
                telemetry_status = "invalid"
            except UpstreamUnavailable:
                telemetry_status = "unavailable"
        video_url = ""
        video_status = "not_recorded"
        if record.video_storage_file:
            try:
                video_url = self._assets.create_video_url(
                    machine_id, record.video_storage_file
                )
                video_status = "referenced"
            except (InvalidStorageReference, UpstreamUnavailable):
                video_status = "unavailable"
        logger.info(
            "event=order_view_loaded machine_id=%s order_id=%s telemetry_status=%s video_status=%s duration_ms=%.1f",
            machine_id,
            order_id,
            telemetry_status,
            video_status,
            (time.perf_counter() - started) * 1000,
        )
        return OrderTelemetry(
            order_id=str(record.uid),
            start_time=start,
            end_time=end,
            logs={},
            video_path=video_url,
            motors=motors,
            extra_weight_points=[],
            telemetry_status=telemetry_status,
            video_status=video_status,
            diagnostics_status="separate_request",
        )
