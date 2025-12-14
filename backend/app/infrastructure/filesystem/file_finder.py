import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from app.constants import DATA_DIR

logger = logging.getLogger(__name__)


def find_suitable_files(machine_id: str, timestamp: float) -> dict[str, Path]:
    """
    Search for all suitable log files for a machine up to a given timestamp.
    Returns a dict with keys as relative paths from machine_id/logs and values as Path objects.
    For each subdirectory and suffix, only the latest file <= target_date is returned.
    """
    start_time = time.perf_counter()
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    target_date = dt.date()

    logs_base = DATA_DIR / machine_id / "logs"
    result: dict[str, Path] = {}

    logger.info(f"Searching files for machine_id={machine_id} and date={target_date}, logs_base={logs_base}")

    # Iterate over all subdirectories of logs_base
    for subdir in [d for d in logs_base.iterdir() if d.is_dir()]:
        latest_files: dict[str, Path] = {}  # suffix -> Path
        for file_path in sorted(subdir.iterdir(), reverse=True):
            if not file_path.is_file():
                continue

            # Match pattern: YYYY-MM-DD_suffix.txt or YYYY-MM-DD_suffix.txt.gz
            match = re.match(r"(\d{4}-\d{2}-\d{2})_(.+)\.txt(\.gz)?$", file_path.name)
            if not match:
                continue

            file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
            suffix = match.group(2)

            if file_date <= target_date and suffix not in latest_files:
                latest_files[suffix] = file_path
                logger.debug(f"Adding file {file_path} for suffix {suffix}")

        # Add found files to the result dict with relative path as key
        for f in latest_files.values():
            rel_path = f.relative_to(logs_base)
            result[str(rel_path)] = f

    end_time = time.perf_counter()
    logger.info(f"step=find_suitable_files status=completed duration={end_time - start_time:.3f}s, found {len(result)} files")

    return result