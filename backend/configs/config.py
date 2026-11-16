"""Backend-only storage and read-only Data API settings."""

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    AWS_ACCESS_KEY_ID: SecretStr
    AWS_SECRET_ACCESS_KEY: SecretStr
    DATA_BUCKET_NAME: str
    AWS_S3_REGION: str = "us-west-2"
    POSTGREST_BASE_URL: AnyHttpUrl
    POSTGREST_API_KEY: SecretStr
    POSTGREST_TIMEOUT_SECONDS: float = Field(default=5, gt=0, le=30)
    POSTGREST_CONNECT_TIMEOUT_SECONDS: float = Field(default=3, gt=0, le=10)
    S3_READ_TIMEOUT_SECONDS: float = Field(default=10, gt=0, le=30)
    TELEMETRY_MAX_BYTES: int = Field(default=2 * 1024 * 1024, gt=0, le=10 * 1024 * 1024)

    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

    @field_validator("POSTGREST_BASE_URL")
    @classmethod
    def validate_postgrest_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username or value.password or value.query or value.fragment:
            raise ValueError(
                "PostgREST URL must not contain credentials, query or fragment."
            )
        return value

    @field_validator("POSTGREST_API_KEY")
    @classmethod
    def validate_postgrest_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("PostgREST API key is required.")
        return value


def get_config() -> Config:
    """Read configuration from the process environment; no browser secrets."""
    return Config()
