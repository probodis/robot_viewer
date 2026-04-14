from pydantic import BaseModel


class ScannerArchive(BaseModel):
    """Scanner archive metadata with a presigned download URL.

    Attributes:
        filename: Archive file name (e.g. "2026-04-07_16-11-05.zip").
        url: Presigned URL for downloading the archive.
    """

    filename: str
    url: str
