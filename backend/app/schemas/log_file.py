from pydantic import BaseModel


class LogFileInfo(BaseModel):
    """Metadata for a single machine log file.

    Attributes:
        filename: File name (e.g. "2026-04-07_telemetry.txt.gz").
        log_key: Path relative to the machine's logs directory,
                 used as identifier for the download endpoint.
        size_bytes: File size in bytes on disk.
    """

    filename: str
    log_key: str
    size_bytes: int
