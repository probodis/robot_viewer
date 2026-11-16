"""Minimal read interfaces used by the viewer application services."""

from typing import Protocol, Any
from app.schemas.order_assets import OrderRecord


class OrdersRepository(Protocol):
    def get_order(self, machine_id: str, order_id: float) -> OrderRecord | None: ...


class OrderAssets(Protocol):
    def read_telemetry(
        self, machine_id: str, log_key: str
    ) -> dict[str, Any] | None: ...
    def create_video_url(self, machine_id: str, log_key: str) -> str: ...
