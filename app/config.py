from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HANDSFREE_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "info"
    allowed_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    camera_enabled: bool = False
    camera_index: int = 0
    speech_provider: str = "none"
    browser_provider: str = "dry-run"
    telemetry_queue_size: int = Field(default=100, ge=1, le=10000)
    telemetry_rate_hz: float = Field(default=10.0, gt=0, le=60)
    tracking_min_confidence: float = Field(default=0.7, ge=0, le=1)
    stability_radius_px: float = Field(default=20.0, gt=0)
    stability_window_ms: int = Field(default=200, gt=0)
    direct_click_debounce_ms: int = Field(default=500, gt=0)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def split_origins(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

