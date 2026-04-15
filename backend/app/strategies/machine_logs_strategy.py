import logging
import re
from pathlib import Path

from app.schemas.log_file import LogFileInfo


logger = logging.getLogger("robot_viewer")

BACKUP_RE = re.compile(r"_\d{12}\.")


class MachineLogsStrategy:
    """List and resolve text log files for a machine, excluding backup copies."""

    def __init__(self, data_dir: Path):
        self._data_dir = data_dir

    def _machine_logs_dir(self, machine_id: str) -> Path:
        return self._data_dir / machine_id / "logs"

    @staticmethod
    def _validate_machine_id(machine_id: str) -> None:
        """Reject ``machine_id`` values that could escape the data directory."""
        if not machine_id or ".." in machine_id or "/" in machine_id or "\\" in machine_id:
            raise ValueError(f"Invalid machine_id: '{machine_id}'")

    def list_log_files(self, machine_id: str) -> list[LogFileInfo]:
        """Scan the machine's log directory tree and return non-backup text files.

        Args:
            machine_id: Machine identifier.

        Returns:
            List of ``LogFileInfo`` sorted by ``log_key``.

        Raises:
            FileNotFoundError: If the machine's logs directory does not exist.
        """
        self._validate_machine_id(machine_id)
        logs_dir = self._machine_logs_dir(machine_id)

        if not logs_dir.is_dir():
            logger.warning(f"Logs directory does not exist: path={logs_dir}")
            raise FileNotFoundError(
                f"Logs directory not found for machine '{machine_id}'"
            )

        result: list[LogFileInfo] = list()

        for file_path in logs_dir.rglob("*"):
            if not file_path.is_file():
                continue

            if BACKUP_RE.search(file_path.name):
                continue

            log_key = str(file_path.relative_to(logs_dir))
            result.append(
                LogFileInfo(
                    filename=file_path.name,
                    log_key=log_key,
                    size_bytes=file_path.stat().st_size,
                )
            )

        result.sort(key=lambda item: item.log_key)
        logger.info(
            f"Listed log files: machine_id={machine_id} total={len(result)}"
        )
        return result

    def resolve_log_file(self, machine_id: str, log_key: str) -> Path:
        """Resolve ``log_key`` to a validated filesystem path.

        Args:
            machine_id: Machine identifier.
            log_key: Relative path inside the machine's logs directory
                     (as returned by :meth:`list_log_files`).

        Returns:
            Absolute ``Path`` to the log file.

        Raises:
            ValueError: If the resolved path escapes the logs directory.
            FileNotFoundError: If the file does not exist.
        """
        self._validate_machine_id(machine_id)
        logs_dir = self._machine_logs_dir(machine_id)
        file_path = (logs_dir / log_key).resolve()

        if not file_path.is_relative_to(logs_dir.resolve()):
            logger.warning(
                f"Path traversal attempt blocked: machine_id={machine_id} log_key={log_key}"
            )
            raise ValueError(f"Invalid log key: '{log_key}'")

        if not file_path.is_file():
            raise FileNotFoundError(
                f"Log file '{log_key}' not found for machine '{machine_id}'"
            )

        return file_path
