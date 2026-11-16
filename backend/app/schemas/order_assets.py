"""Order metadata returned by the existing read-only PostgREST service."""

from pydantic import BaseModel, ConfigDict, Field


class OrderRecord(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    machine_id: str
    uid: float = Field(gt=0)
    telemetry_storage_file: str | None = None
    video_storage_file: str | None = None
    total_time: float | None = Field(default=None, ge=0)
