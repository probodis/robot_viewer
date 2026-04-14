import logging
import re
from datetime import datetime, timezone

from adapters.s3 import S3Client


logger = logging.getLogger("robot_viewer")

SCANNER_S3_PREFIX = "xcubes/{machine_id}/logs/scanner/"
SCANNER_FILENAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.zip$")
TIMESTAMP_FMT = "%Y-%m-%d_%H-%M-%S"
DEFAULT_TOLERANCE_SEC = 60.0


class ScannerArchiveStrategy:
    """Locate the scanner archive on S3 closest to a given order timestamp."""

    def __init__(self, s3_client: S3Client, tolerance_sec: float = DEFAULT_TOLERANCE_SEC):
        self._s3 = s3_client
        self._tolerance = tolerance_sec

    def find_archive_key(self, machine_id: str, order_id: float) -> tuple[str, str] | None:
        """Return ``(s3_key, filename)`` of the best-matching archive, or *None*.

        Searches the scanner prefix for the UTC date derived from *order_id*
        and picks the file whose embedded timestamp is closest (within tolerance).
        """
        order_dt = datetime.fromtimestamp(order_id, tz=timezone.utc)
        date_str = order_dt.strftime("%Y-%m-%d")

        prefix = SCANNER_S3_PREFIX.format(machine_id=machine_id) + date_str
        keys = self._s3.list_files_by_prefix(prefix)

        if not keys:
            logger.info(
                f"No scanner archives found: machine_id={machine_id} prefix='{prefix}'"
            )
            return None

        return self._pick_closest(keys, order_id)

    def _pick_closest(
        self, keys: list[str], order_id: float
    ) -> tuple[str, str] | None:
        best_key: str | None = None
        best_filename: str | None = None
        best_diff = float("inf")

        for key in keys:
            filename = key.rsplit("/", 1)[-1]
            match = SCANNER_FILENAME_RE.search(filename)
            if not match:
                continue

            file_dt = datetime.strptime(match.group(1), TIMESTAMP_FMT).replace(
                tzinfo=timezone.utc
            )
            diff = abs(file_dt.timestamp() - order_id)

            if diff < best_diff:
                best_diff = diff
                best_key = key
                best_filename = filename

        if best_key is None or best_diff > self._tolerance:
            logger.warning(
                f"No scanner archive within tolerance: order_id={order_id} "
                f"best_diff={best_diff:.1f}s tolerance={self._tolerance}s"
            )
            return None

        logger.info(
            f"Scanner archive matched: key='{best_key}' diff={best_diff:.1f}s"
        )
        return best_key, best_filename
