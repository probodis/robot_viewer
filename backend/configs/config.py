from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr

class Config(BaseSettings):

    AWS_ACCESS_KEY_ID: SecretStr
    AWS_SECRET_ACCESS_KEY: SecretStr
    DATA_BUCKET_NAME: str

    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

def get_config() -> Config:
    """Load and return the application configuration from environment variables."""
    return Config()  # type: ignore