"""Agents configuration. Values come from the environment or the git-ignored .env only."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from fl_domain.common import StageKey

__all__ = ["DEFAULT_STAGE_BUDGETS", "Settings", "get_settings"]

# record_missing: answer from what is recorded, record only what is not (a gap-filling pass).
LlmMode = Literal["live", "record", "record_missing", "replay"]

# Token budget per stage run (docs/02 §4). A stage that would exceed it pauses and asks
# the user to continue (ADR-025).
DEFAULT_STAGE_BUDGETS: dict[StageKey, int] = {
    "intake": 30_000,
    "bom": 25_000,
    "routing": 30_000,
    "quality": 15_000,
    "capacity": 8_000,
    "inventory": 10_000,
    "workorders_shifts": 10_000,
    "utilities": 10_000,
    "layout": 10_000,
    "simulation": 10_000,
    "finance": 10_000,
    "reports": 10_000,
    "vendors": 60_000,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    log_level: str = "info"
    database_url: str = "postgresql+asyncpg://factory:factory@localhost:5432/factory"
    queue_database_url: str = "postgresql://factory:factory@localhost:5432/factory"
    redis_url: str = "redis://localhost:6379/0"

    # Files (named Docker volumes, ADR-024)
    uploads_dir: Path = Path("/data/uploads")
    derived_dir: Path = Path("/data/derived")

    # LLM router (ADR-004): model names live in config, never in code.
    llm_mode: LlmMode = "live"
    cassette_dir: Path = Path("/app/cassettes")
    llm_role_reasoner: str = "groq/openai/gpt-oss-120b"
    llm_role_extractor: str = "groq/openai/gpt-oss-20b"
    llm_role_writer: str = "groq/openai/gpt-oss-120b"
    llm_role_vision: str = "gemini/gemini-3.5-flash"
    groq_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 3

    # Public web access for vendor research (docs/09 §2, docs/11 §3). Replay reads recorded
    # search results and pages so the end-to-end check needs no network and no quota.
    web_mode: LlmMode = "live"
    web_fixture_dir: Path = Path("/app/web-fixtures")
    tavily_api_key: SecretStr = SecretStr("")
    web_user_agent: str = "FactoryLayoutResearchBot/0.1 (public pages only; respects robots.txt)"
    web_timeout_s: float = 12.0
    web_max_bytes: int = 1_000_000
    web_min_delay_s: float = 1.0
    vendor_cache_enabled: bool = True
    # Defaults from the P0 spike; confirm against the provider consoles (P0 §5c).
    role_rpm: dict[str, int] = Field(
        default_factory=lambda: {"reasoner": 30, "extractor": 30, "writer": 30, "vision": 10}
    )
    role_tpm: dict[str, int] = Field(
        default_factory=lambda: {
            "reasoner": 8000,
            "extractor": 6000,
            "writer": 8000,
            "vision": 250000,
        }
    )
    stage_budgets: dict[str, int] = Field(
        default_factory=lambda: {str(k): v for k, v in DEFAULT_STAGE_BUDGETS.items()}
    )

    tool_call_budgets: dict[str, int] = Field(default_factory=lambda: {"bom": 12})

    # Pipeline worker
    pipeline_concurrency: int = 2  # docs/02 §3: at most 2 pipelines executing at once
    file_job_timeout_s: float = 240.0  # must exceed the worker's CAD time limit (180 s)

    def role_model(self, role: str) -> str:
        return {
            "reasoner": self.llm_role_reasoner,
            "extractor": self.llm_role_extractor,
            "writer": self.llm_role_writer,
            "vision": self.llm_role_vision,
        }[role]

    def provider_key(self, model: str) -> str:
        """The provider key for a model, or "" if unset or still a .env.example placeholder."""
        key = ""
        if model.startswith("groq/"):
            key = self.groq_api_key.get_secret_value()
        elif model.startswith("gemini/"):
            key = self.gemini_api_key.get_secret_value()
        return "" if not key or "placeholder" in key.lower() else key

    def search_key(self) -> str:
        """The Tavily key, or "" if unset or still a .env.example placeholder."""
        key = self.tavily_api_key.get_secret_value()
        return "" if not key or "placeholder" in key.lower() else key

    def tool_calls_for(self, stage: str, overrides: dict[str, int] | None = None) -> int:
        """Tool-call limit for a stage's tool loop (S2: 12). Override key: '<stage>_tools'."""
        if overrides and f"{stage}_tools" in overrides:
            return int(overrides[f"{stage}_tools"])
        return int(self.tool_call_budgets.get(stage, 12))

    def budget_for(self, stage: str, overrides: dict[str, int] | None = None) -> int:
        if overrides and stage in overrides:
            return int(overrides[stage])
        return int(self.stage_budgets.get(stage, 10_000))


@lru_cache
def get_settings() -> Settings:
    return Settings()
