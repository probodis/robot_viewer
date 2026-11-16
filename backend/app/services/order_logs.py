"""Explicit, bounded log-window reads; never discover files by calendar date."""

import base64
import gzip
import logging
import zlib
from pathlib import Path

from adapters.order_assets import validate_order_identity
from app.infrastructure.filesystem.log_window_extractor import parse_timestamp
from app.ports.order_data import OrdersRepository
from app.services.errors import DiagnosticLogUnavailable

logger = logging.getLogger("robot_viewer")
MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_INPUT_LINES = 200_000
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_ORDER_SECONDS = 3600


class DiagnosticWindowUnavailable(ValueError):
    """Order metadata does not define a bounded diagnostic window."""


def resolve_text_log(data_dir: Path, machine_id: str, log_key: str) -> Path:
    """Resolve one text-only key and reject traversal, absolute paths and symlink escapes."""
    validate_order_identity(machine_id, 1.0)
    parts = log_key.split("/")
    if (
        len(log_key) > 512
        or len(parts) < 2
        or any(part in ("", ".", "..") for part in parts)
        or any(c in log_key for c in ("\\", "%", ":"))
        or any(ord(c) < 32 or ord(c) == 127 for c in log_key)
        or not log_key.endswith((".txt", ".txt.gz", ".log", ".log.gz"))
    ):
        raise ValueError("Invalid text log key.")
    base = (data_dir / machine_id / "logs").resolve()
    path = (base / log_key).resolve()
    if not base.is_relative_to(data_dir.resolve()) or not path.is_relative_to(base):
        raise ValueError("Invalid text log key.")
    return path


class OrderLogWindowService:
    def __init__(self, repository: OrdersRepository, data_dir: Path):
        self._repository = repository
        self._data_dir = data_dir

    def get_window(self, machine_id: str, order_id: float, log_key: str) -> dict | None:
        validate_order_identity(machine_id, order_id)
        path = resolve_text_log(self._data_dir, machine_id, log_key)
        record = self._repository.get_order(machine_id, order_id)
        if record is None:
            return None
        if record.total_time is None or record.total_time > MAX_ORDER_SECONDS:
            raise DiagnosticWindowUnavailable(
                "Order has no bounded diagnostic time window."
            )
        if not path.is_file():
            raise FileNotFoundError("Requested text log is unavailable.")
        start, end = order_id - 5, order_id + record.total_time + 5
        scanned = output_bytes = 0
        output = []
        last_ts = None
        found_timestamp = truncated = False
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rb") as stream:
                for _ in range(MAX_INPUT_LINES):
                    raw = stream.readline(MAX_INPUT_BYTES - scanned + 1)
                    if not raw:
                        break
                    scanned += len(raw)
                    if scanned > MAX_INPUT_BYTES:
                        truncated = True
                        break
                    text = raw.decode("utf-8", errors="replace")
                    timestamp = parse_timestamp(text)
                    if timestamp is not None:
                        last_ts = timestamp
                        found_timestamp = True
                    if last_ts is not None and start <= last_ts <= end:
                        output_bytes += len(text.encode("utf-8"))
                        if output_bytes > MAX_OUTPUT_BYTES:
                            truncated = True
                            break
                        output.append(text)
                else:
                    truncated = True
        except (OSError, EOFError, zlib.error):
            raise DiagnosticLogUnavailable(
                "Requested text log could not be read."
            ) from None
        status = (
            "available" if output else ("empty" if found_timestamp else "no_timestamp")
        )
        logger.info(
            "event=order_log_window_loaded machine_id=%s order_id=%s log_key=%s bytes_scanned=%s truncated=%s status=%s",
            machine_id,
            order_id,
            log_key,
            scanned,
            truncated,
            status,
        )
        return {
            "log_key": log_key,
            "text": base64.b64encode("".join(output).encode("utf-8")).decode("ascii"),
            "truncated": truncated,
            "status": status,
            "extra_weight_points": [],
            "extra_weight_points_status": "not_available",
        }
