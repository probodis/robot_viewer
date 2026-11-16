"""Bounded reads of machine-scoped orders through PostgREST.

The caller owns the HTTP client and its connection pool. No retries or file
searches occur here. Errors never include URLs, credentials or response bodies.
"""

import logging
import time

import httpx
from pydantic import ValidationError

from app.schemas.order_assets import OrderRecord
from app.services.errors import InvalidUpstreamResponse, UpstreamUnavailable

logger = logging.getLogger("robot_viewer")
ORDER_COLUMNS = "machine_id,uid,telemetry_storage_file,video_storage_file,total_time"


class PostgrestOrdersRepository:
    def __init__(self, client: httpx.Client):
        self._client = client

    def get_order(self, machine_id: str, order_id: float) -> OrderRecord | None:
        started = time.perf_counter()
        try:
            response = self._client.get(
                "orders",
                params={
                    "machine_id": f"eq.{machine_id}",
                    "uid": f"eq.{order_id}",
                    "select": ORDER_COLUMNS,
                    "limit": "1",
                },
            )
        except httpx.RequestError:
            logger.warning(
                "event=order_metadata_failed machine_id=%s order_id=%s reason=transport",
                machine_id,
                order_id,
            )
            raise UpstreamUnavailable(
                "Order metadata service is unavailable."
            ) from None
        if response.status_code in (408, 429) or response.status_code >= 500:
            raise UpstreamUnavailable("Order metadata service is unavailable.")
        if response.status_code != 200:
            logger.warning(
                "event=order_metadata_failed machine_id=%s order_id=%s status=%s",
                machine_id,
                order_id,
                response.status_code,
            )
            raise InvalidUpstreamResponse(
                "Order metadata service rejected the request."
            )
        try:
            rows = response.json()
            if not isinstance(rows, list) or len(rows) > 1:
                raise ValueError
            record = OrderRecord.model_validate(rows[0]) if rows else None
            if record is not None and (
                record.machine_id != machine_id or record.uid != order_id
            ):
                raise ValueError
        except (ValueError, TypeError, ValidationError):
            raise InvalidUpstreamResponse(
                "Order metadata service returned invalid data."
            ) from None
        logger.info(
            "event=order_metadata_loaded machine_id=%s order_id=%s found=%s duration_ms=%.1f",
            machine_id,
            order_id,
            record is not None,
            (time.perf_counter() - started) * 1000,
        )
        return record
