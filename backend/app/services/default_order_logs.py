"""Automatic legacy file selection for a separate, resource-bounded logs request.

Filename dates are intentionally a compatibility heuristic, not proof that a
file contains the order. Actual windows use UTC record timestamps and retain
legacy chronological early exit after the order window. Manual reads remain
available for non-monotonic files. The main order/video/telemetry service never
calls this module.
"""

import base64
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from adapters.order_assets import validate_order_identity
from app.ports.order_data import OrdersRepository
from app.services.errors import DiagnosticLogUnavailable
from app.services.order_logs import (
    DiagnosticWindowUnavailable,
    LogReadBudget,
    MAX_ORDER_SECONDS,
    read_log_window,
    resolve_text_log,
)

logger = logging.getLogger("robot_viewer")
LOG_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})_(.+)\.txt(\.gz)?\Z")
BACKUP_SUFFIX = re.compile(r"_\d{12}\Z")
EXCLUDED_FOLDERS = frozenset({"videos", "scanner", "start_order_converted"})


@dataclass(frozen=True)
class DefaultLogLimits:
    max_files: int = 32
    max_entries: int = 100_000
    max_input_bytes: int = 32 * 1024 * 1024
    max_output_bytes: int = 2 * 1024 * 1024
    timeout_seconds: float = 5.0


@dataclass
class SelectionBudget:
    limits: DefaultLogLimits
    deadline: float
    entries: int = 0
    truncated: bool = False

    def consume_entry(self) -> bool:
        if (
            self.entries >= self.limits.max_entries
            or time.perf_counter() >= self.deadline
        ):
            self.truncated = True
            return False
        self.entries += 1
        return True


@dataclass(frozen=True)
class DefaultLogSelection:
    keys: tuple[str, ...]
    truncated: bool


def select_default_logs(
    data_dir: Path, machine_id: str, order_id: float, budget: SelectionBudget
) -> DefaultLogSelection:
    """Select the newest date<=order for each folder/suffix, preferring gzip.

    Enumeration is non-recursive and capped by entries and the request deadline.
    Binary artifact folders and symlinks are never entered. Invalid filename
    dates and timestamp-suffixed backups are ignored, as in the old selector.
    """
    validate_order_identity(machine_id, order_id)
    try:
        target_date = datetime.fromtimestamp(order_id, timezone.utc).date()
    except (ValueError, OverflowError, OSError):
        raise ValueError("Invalid order timestamp.") from None
    base = (data_dir / machine_id / "logs").resolve()
    if not base.is_relative_to(data_dir.resolve()):
        raise ValueError("Invalid machine logs directory.")
    if not base.is_dir():
        return DefaultLogSelection((), False)
    folders = []
    with os.scandir(base) as entries:
        for entry in entries:
            if not budget.consume_entry():
                break
            if entry.name not in EXCLUDED_FOLDERS and entry.is_dir(
                follow_symlinks=False
            ):
                folders.append(entry.name)
    selected: dict[tuple[str, str], tuple[date, bool, str]] = {}
    for folder in sorted(folders):
        if (
            budget.entries >= budget.limits.max_entries
            or time.perf_counter() >= budget.deadline
        ):
            budget.truncated = True
            break
        try:
            with os.scandir(base / folder) as entries:
                for entry in entries:
                    if not budget.consume_entry():
                        break
                    match = LOG_NAME.fullmatch(entry.name)
                    if not match or not entry.is_file(follow_symlinks=False):
                        continue
                    suffix = match.group(2)
                    if BACKUP_SUFFIX.search(suffix):
                        continue
                    try:
                        file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
                    except ValueError:
                        continue
                    if file_date > target_date:
                        continue
                    key = (folder, suffix)
                    candidate = (
                        file_date,
                        bool(match.group(3)),
                        f"{folder}/{entry.name}",
                    )
                    if key not in selected or candidate[:2] > selected[key][:2]:
                        selected[key] = candidate
        except OSError:
            budget.truncated = True
            logger.warning(
                "event=default_log_folder_failed machine_id=%s folder=%s",
                machine_id,
                folder,
            )
    keys = sorted(candidate[2] for candidate in selected.values())
    if len(keys) > budget.limits.max_files:
        budget.truncated = True
        keys = keys[: budget.limits.max_files]
    return DefaultLogSelection(tuple(keys), budget.truncated)


def _entry(key: str, text: str, status: str, truncated: bool = False) -> dict:
    return {
        "path": key,
        "text": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "status": status,
        "truncated": truncated,
    }


class AutomaticOrderLogsService:
    def __init__(
        self,
        repository: OrdersRepository,
        data_dir: Path,
        limits: DefaultLogLimits | None = None,
    ):
        self._repository = repository
        self._data_dir = data_dir
        self._limits = limits or DefaultLogLimits()

    def get_logs(self, machine_id: str, order_id: float) -> dict | None:
        validate_order_identity(machine_id, order_id)
        started = time.perf_counter()
        record = self._repository.get_order(machine_id, order_id)
        if record is None:
            return None
        if record.total_time is None or record.total_time > MAX_ORDER_SECONDS:
            raise DiagnosticWindowUnavailable(
                "Order has no bounded diagnostic time window."
            )
        deadline = started + self._limits.timeout_seconds
        selection_budget = SelectionBudget(self._limits, deadline)
        selection = select_default_logs(
            self._data_dir, machine_id, order_id, selection_budget
        )
        read_budget = LogReadBudget(
            self._limits.max_input_bytes, self._limits.max_output_bytes, deadline
        )
        logs = {}
        partial = selection.truncated
        truncated = selection.truncated
        loaded = 0
        for key in selection.keys:
            if read_budget.exhausted:
                logs[key] = _entry(
                    key,
                    "=== Automatic log reading reached its limit ===\nOpen this file manually to inspect it.\n",
                    "not_loaded",
                    True,
                )
                partial = truncated = True
                continue
            try:
                path = resolve_text_log(self._data_dir, machine_id, key)
                result = read_log_window(
                    path,
                    order_id - 5,
                    order_id + record.total_time + 5,
                    read_budget,
                    stop_at_window_end=True,
                )
                loaded += 1
                text = result.text
                if not text:
                    text = (
                        "=== No supported timestamps found in this file ===\n"
                        if result.status == "no_timestamp"
                        else "=== No time window found in this file ===\n"
                    )
                    text += "Double-click the tab to open the full file.\n"
                logs[key] = _entry(key, text, result.status, result.truncated)
                partial |= result.truncated
                truncated |= result.truncated
            except (DiagnosticLogUnavailable, OSError, ValueError) as error:
                partial = True
                logger.warning(
                    "event=default_log_file_failed machine_id=%s order_id=%s log_key=%r reason=%s",
                    machine_id,
                    order_id,
                    key,
                    type(error).__name__,
                )
                logs[key] = _entry(
                    key,
                    "=== This log file could not be read ===\nOpen the full file or retry logs loading.\n",
                    "error",
                )
        status = "partial" if partial else ("available" if logs else "empty")
        logger.info(
            "event=default_order_logs_loaded machine_id=%s order_id=%s selected_files=%s loaded_files=%s entries_scanned=%s bytes_scanned=%s truncated=%s status=%s duration_ms=%.1f",
            machine_id,
            order_id,
            len(logs),
            loaded,
            selection_budget.entries,
            read_budget.input_bytes,
            truncated,
            status,
            (time.perf_counter() - started) * 1000,
        )
        return {
            "logs": logs,
            "status": status,
            "truncated": truncated,
            "selected_files": len(logs),
            "loaded_files": loaded,
        }
