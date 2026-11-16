from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Values come from the environment / git-ignored .env only."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    log_level: str = "info"
    database_url: str = "postgresql+asyncpg://factory:factory@localhost:5432/factory"
    redis_url: str = "redis://localhost:6379/0"
    public_web_origin: str = "http://localhost:5173"

    project_retention_days: int = 30
    # Workers write a heartbeat into Redis; readiness fails if one is older than this.
    heartbeat_max_age_s: int = 30
    required_workers: list[str] = Field(default_factory=lambda: ["worker", "cad-worker", "agents"])

    # Uploads (docs/11 §2): allowed types, size limits, max files per project.
    uploads_dir: Path = Path("/data/uploads")
    derived_dir: Path = Path("/data/derived")
    max_files_per_project: int = 20
    max_cad_mb: int = 50
    max_image_mb: int = 10
    max_pdf_mb: int = 20
    max_table_mb: int = 10
    image_max_px: int = 2048

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.public_web_origin.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
