import logging
import base64
from pathlib import Path
from typing import Dict, Iterator, TypedDict
from app.utils import open_text
from datetime import datetime, timezone
import re


logger = logging.getLogger("robot_viewer")

TS_PATTERN = re.compile(r"^\[?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]?")

OFFSET_PREFIXES = ("subapps/", "console/")

# Safety limits
MAX_BYTES_PER_FILE = 10 * 1024 * 1024 # 10 MB hard cap
MAX_LINES_PER_FILE = 200_000 # absolute line limit


def parse_timestamp(line: str, offset_h: float = 0) -> float | None:
    """
    Return parsed timestamp as float seconds (naive → UTC naive).
    
    Args:
        line (str): Log line to parse.
        offset_h (float): Offset in hours to apply to the parsed timestamp.
    """    
    m = TS_PATTERN.match(line)
    if not m:
        return None

    try:
        dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
        )
    except ValueError:
        return None

    return dt.timestamp() - offset_h * 3600.0


def iter_lines_with_limits(path: Path) -> Iterator[str]:
    """Yield lines from file with size and line-count protection."""
    total_bytes = 0
    line_count = 0

    with open_text(path) as f:
        for line in f:
            line_count += 1
            total_bytes += len(line)

            if line_count > MAX_LINES_PER_FILE:
                logger.warning(
                    "level=WARNING module=log_window_extractor func=iter_lines_with_limits "
                    "msg=Line limit exceeded path=%s lines=%d",
                    path,
                    line_count,
                )
                break

            if total_bytes > MAX_BYTES_PER_FILE:
                logger.warning(
                    "level=WARNING module=log_window_extractor func=iter_lines_with_limits "
                    "msg=Byte limit exceeded path=%s bytes=%d",
                    path,
                    total_bytes,
                )
                break

            yield line


def extract_log_window(
    *,
    file_path: Path,
    rel_path: str,
    window_start: float,
    window_end: float,
) -> Dict[str, str]:
    """Extract log lines inside time window using lazy scanning and early exit."""

    offset_h = -8.0 if rel_path.startswith(OFFSET_PREFIXES) else 0.0

    logger.info(
        "level=INFO module=log_window_extractor func=extract_log_window "
        "msg=Start rel_path=%s path=%s window=(%s,%s) offset_h=%s",
        rel_path,
        file_path,
        window_start,
        window_end,
        offset_h,
    )

    lines_in_window: list[str] = []
    last_ts: float | None = None
    found_any_ts = False

    for line in iter_lines_with_limits(file_path):
        ts = parse_timestamp(line, offset_h)

        if ts is not None:
            found_any_ts = True
            last_ts = ts

            # Early skip: file is entirely before window
            if last_ts < window_start:
                continue

            # Early exit: file is already past window
            if last_ts > window_end:
                logger.info(
                    "level=INFO module=log_window_extractor func=extract_log_window "
                    "msg=Window end reached rel_path=%s",
                    rel_path,
                )
                break

        if last_ts is not None and window_start <= last_ts <= window_end:
            lines_in_window.append(line)

    if found_any_ts and lines_in_window:
        text = "".join(lines_in_window)
        logger.info(
            "level=INFO module=log_window_extractor func=extract_log_window "
            "msg=Extracted rel_path=%s lines=%d",
            rel_path,
            len(lines_in_window),
        )
    else:
        text = (
            "=== No time window found in this file ===\n"
            "To open full file, double-click the tab.\n"
        )
        logger.info(
            "level=INFO module=log_window_extractor func=extract_log_window "
            "msg=No window found rel_path=%s",
            rel_path,
        )

    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return {"path": str(file_path), "text": encoded}


def fetch_all_order_logs(
    order_id: float,
    end_order_ts: float,
    log_files: Dict[str, Path],
) -> Dict[str, Dict[str, str]]:
    """Extract log windows for all files sequentially (I/O-optimized)."""

    window_start = order_id - 5.0
    window_end = end_order_ts + 5.0

    logger.info(
        "level=INFO module=log_window_extractor func=fetch_all_order_logs "
        "msg=Start order_id=%s window=(%s,%s) files=%d",
        order_id,
        window_start,
        window_end,
        len(log_files),
    )

    result: Dict[str, Dict[str, str]] = {}

    for rel_path, path in log_files.items():
        try:
            result[rel_path] = extract_log_window(
                file_path=path,
                rel_path=rel_path,
                window_start=window_start,
                window_end=window_end,
            )
        except Exception as e:
            logger.error(
                "level=ERROR module=log_window_extractor func=fetch_all_order_logs "
                "msg=Failed rel_path=%s error=%s",
                rel_path,
                e,
            )
            text = f"=== Failed to extract log window: {e} ==="
            encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
            result[rel_path] = {"path": str(path), "text": encoded}

    logger.info(
        "level=INFO module=log_window_extractor func=fetch_all_order_logs "
        "msg=Finished result_count=%d",
        len(result),
    )

    return result
