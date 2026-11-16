"""Read one exact S3 object; never discover assets by listing or guessing names."""

import json
import math
import re
from pathlib import PurePosixPath

from botocore.exceptions import BotoCoreError, ClientError

from adapters.s3 import S3Client
from app.services.errors import (
    InvalidStorageReference,
    InvalidTelemetry,
    UpstreamUnavailable,
)

MACHINE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def validate_order_identity(machine_id: str, order_id: float) -> None:
    if not MACHINE_PATTERN.fullmatch(machine_id):
        raise ValueError("Invalid machine identifier.")
    if not math.isfinite(order_id) or order_id <= 0:
        raise ValueError("Order ID must be a finite positive timestamp.")


def asset_key(machine_id: str, log_key: str, kind: str) -> str:
    validate_order_identity(machine_id, 1.0)
    prefix, suffix = {
        "telemetry": ("start_order_converted", ".json"),
        "video": ("videos", ".mp4"),
    }[kind]
    if not isinstance(log_key, str) or len(log_key) > 1024:
        raise InvalidStorageReference("Invalid storage reference.")
    parts = log_key.split("/")
    if (
        len(parts) != 2
        or parts[0] != prefix
        or not parts[1].endswith(suffix)
        or any(part in ("", ".", "..") for part in parts)
        or any(c in log_key for c in ("\\", "%", ":"))
        or any(ord(c) < 32 or ord(c) == 127 for c in log_key)
    ):
        raise InvalidStorageReference("Invalid storage reference.")
    return str(PurePosixPath("xcubes", machine_id, "logs", *parts))


def _reject_constant(value: str):
    raise ValueError("Non-finite JSON number.")


class S3OrderAssets:
    def __init__(self, s3_client: S3Client, max_bytes: int = 2 * 1024 * 1024):
        if max_bytes <= 0:
            raise ValueError("Telemetry byte limit must be positive.")
        self._s3 = s3_client
        self._max_bytes = max_bytes

    def read_telemetry(self, machine_id: str, log_key: str) -> dict | None:
        key = asset_key(machine_id, log_key, "telemetry")
        try:
            response = self._s3.client.get_object(Bucket=self._s3.bucket_name, Key=key)
            body = response["Body"]
            try:
                if response.get("ContentLength", 0) > self._max_bytes:
                    raise InvalidTelemetry("Telemetry exceeds the size limit.")
                content = body.read(self._max_bytes + 1)
                if len(content) > self._max_bytes:
                    raise InvalidTelemetry("Telemetry exceeds the size limit.")
            finally:
                body.close()
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in (
                "NoSuchKey",
                "404",
                "NotFound",
            ):
                return None
            raise UpstreamUnavailable("Telemetry storage is unavailable.") from None
        except (BotoCoreError, OSError):
            raise UpstreamUnavailable("Telemetry storage is unavailable.") from None
        try:
            data = json.loads(content, parse_constant=_reject_constant)
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, UnicodeError):
            raise InvalidTelemetry("Telemetry is not a valid JSON object.") from None

    def create_video_url(self, machine_id: str, log_key: str) -> str:
        key = asset_key(machine_id, log_key, "video")
        try:
            return self._s3.get_presigned_url(PurePosixPath(key))
        except (BotoCoreError, ClientError, OSError):
            raise UpstreamUnavailable("Video signing is unavailable.") from None
