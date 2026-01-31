from pydantic import BaseModel


class MachineVideo(BaseModel):
    """Machine video metadata with a presigned URL.

    Attributes:
        filename: Video file name (e.g. "2025-09-04_12-30-01.mp4").
        url: Presigned URL for downloading/streaming the video.
    """

    filename: str
    url: str
