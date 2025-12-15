import logging
import base64
from pathlib import Path
from multiprocessing import Process, Queue
from typing import Dict, TypedDict
from app.utils import open_text
from datetime import datetime, timezone
import re


logger = logging.getLogger("robot_viewer")

MAX_PROCESSES = 2


class LogWindowResult(TypedDict):
    rel_path: str
    path: str
    text: str


def _process_file(file_path: Path, rel_path: str, window_start: float, window_end: float, queue: Queue):
    try:
        lines_in_window = []
        last_ts = None
        found_any_ts = False

        offset_prefixes = ["subapps/", "console/"]
        offset_h = -8.0 if any(rel_path.startswith(prefix) for prefix in offset_prefixes) else 0.0

        with open_text(file_path) as f:
            for line in f:
                ts_utc = parse_timestamp(line=line, offset_h=offset_h)
                if ts_utc is not None:
                    found_any_ts = True
                    last_ts = ts_utc

                if last_ts is not None and window_start <= last_ts <= window_end:
                    lines_in_window.append(line)
                elif last_ts is not None and last_ts > window_end:
                    break  # прошли окно → выходим

        if found_any_ts and lines_in_window:
            text = "".join(lines_in_window)
        else:
            text = (
                "=== No time window found in this file ===\n"
                "To open full file, double-click the tab.\n"
            )

        encoded_text = base64.b64encode(text.encode("utf-8")).decode("ascii")
        queue.put((rel_path, {"path": str(file_path), "text": encoded_text}))

    except Exception as e:
        logger.error(f"Failed to extract log window from {file_path}: {e}")
        text = f"=== Failed to extract log window: {e} ==="
        encoded_text = base64.b64encode(text.encode("utf-8")).decode("ascii")
        queue.put((rel_path, {"path": str(file_path), "text": encoded_text}))


def parse_timestamp(line: str, offset_h: float = 0) -> float | None:
    """
    Return parsed timestamp as float seconds (naive → UTC naive).
    
    Args:
        line (str): Log line to parse.
        offset_h (float): Offset in hours to apply to the parsed timestamp.
    """
    # Regex patterns for timestamps:
    #   [YYYY-MM-DD HH:MM:SS]
    #   YYYY-MM-DD HH:MM:SS(.sss)
    ts_pattern = re.compile(r"^\[?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]?")
    
    m = ts_pattern.match(line)
    if m:
        ts_str = m.group(1)
        # try microseconds first
        try:
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        # naive datetime → timestamp (still naive)
        return dt.timestamp() - offset_h * 3600.0
    return None


def fetch_all_order_logs(order_id: float, end_order_ts: float, log_files: Dict[str, Path]) -> Dict[str, Dict[str, str]]:
    """
    Extract log windows for all files related to an order, using multiprocessing for efficiency.

    Args:
        order_id (float): Start timestamp of the order.
        end_order_ts (float): End timestamp of the order.
        log_files (dict[str, Path]): Mapping of relative log file paths to their absolute Path objects.

    Returns:
        dict[str, dict[str, str]]: Mapping of relative paths to extracted log window data.
    """
    window_start = order_id - 5.0
    window_end = end_order_ts + 5.0

    result: Dict[str, Dict[str, str]] = dict()
    queue = Queue()

    processes = []
    for rel_path, file_path in log_files.items():
        p = Process(target=_process_file, args=(file_path, rel_path, window_start, window_end, queue))
        processes.append((p, rel_path, file_path))

    # Start all processes first to maximize parallelism
    for p, rel_path, file_path in processes:
        logger.info(f"level=INFO module=log_window_extractor func=fetch_all_order_logs msg=Starting process for file {rel_path}")
        p.start()

    # Join all processes with a reasonable timeout
    join_timeout = 30  # seconds, increase if needed for slow environments
    for p, rel_path, file_path in processes:
        p.join(timeout=join_timeout)
        if p.is_alive():
            p.terminate()
            logger.warning(
                f"level=WARNING module=log_window_extractor func=fetch_all_order_logs msg=Timeout reached for file {rel_path}"
            )
            text = "=== Timeout reached while extracting log window ==="
            encoded_text = base64.b64encode(text.encode("utf-8")).decode("ascii")
            result[rel_path] = {"path": str(file_path), "text": encoded_text}
        else:
            logger.info(
                f"level=INFO module=log_window_extractor func=fetch_all_order_logs msg=Process finished for file {rel_path}"
            )

    while not queue.empty():
        rel_path, data = queue.get()
        result[rel_path] = data

    return result
