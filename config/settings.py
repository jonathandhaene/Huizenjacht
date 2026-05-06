"""
Application configuration loaded from environment variables / .env file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Search criteria ───────────────────────────────────────────────────────
    search_region: str = Field("Vlaamse Ardennen", description="Target region name")
    search_postal_codes: str = Field(
        "9600,9620,9630,9660,9680,9688,9690,9700,9750,9770,9790",
        description="Comma-separated list of postal codes to include",
    )
    max_price: int = Field(600_000, description="Maximum asking price in EUR")
    min_bedrooms: int = Field(3, description="Minimum number of bedrooms")
    min_land_area: int = Field(5_000, description="Minimum land/plot area in m²")
    keywords: str = Field(
        "landelijk,hoeve,boerderij,weiland,stal,schuur,B&B,gastenverblijf,agrarisch",
        description="Comma-separated keywords that should appear in listings",
    )

    # ── Notification ──────────────────────────────────────────────────────────
    notification_email: str = Field("jonathan.dhaene@gmail.com")
    smtp_host: str = Field("smtp.gmail.com")
    smtp_port: int = Field(587)
    smtp_username: str = Field("")
    smtp_password: str = Field("")
    smtp_from: str = Field("Huizenjacht <no-reply@huizenjacht.local>")

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: str = Field("", description="OpenAI API key")
    openai_model: str = Field("gpt-4o")

    # ── GitHub Models (free OpenAI-compatible inference for GitHub users) ─────
    # Used as a fallback when openai_api_key is not set. Authenticates with a
    # personal access token (or the GITHUB_TOKEN exposed inside Actions).
    github_token: str = Field(
        "",
        description="GitHub PAT or Actions token used for GitHub Models inference",
    )
    github_models_base_url: str = Field(
        "https://models.github.ai/inference",
        description="Base URL of the GitHub Models OpenAI-compatible endpoint",
    )
    github_models_model: str = Field(
        "openai/gpt-4o-mini",
        description="Model id on GitHub Models (publisher/name format)",
    )

    # ── Cache ─────────────────────────────────────────────────────────────────
    cache_dir: str = Field(".cache")

    # ── Scheduling ────────────────────────────────────────────────────────────
    daily_run_time: str = Field("07:00")

    # ── Social media ──────────────────────────────────────────────────────────
    facebook_email: Optional[str] = Field(None)
    facebook_password: Optional[str] = Field(None)

    # ── Collaborative web app ─────────────────────────────────────────────────
    user1_name: str = Field("Jonathan", description="First partner's display name")
    user2_name: str = Field("", description="Second partner's display name")
    github_owner: str = Field("jonathandhaene", description="GitHub repo owner")
    github_repo: str = Field("Huizenjacht", description="GitHub repo name")
    github_branch: str = Field("main", description="Branch where docs/ data lives")

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = Field("INFO")

    # ── Computed helpers ──────────────────────────────────────────────────────

    @property
    def postal_code_list(self) -> List[str]:
        return [p.strip() for p in self.search_postal_codes.split(",") if p.strip()]

    @property
    def keyword_list(self) -> List[str]:
        return [k.strip().lower() for k in self.keywords.split(",") if k.strip()]

    @property
    def cache_path(self) -> Path:
        p = Path(self.cache_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @model_validator(mode="before")
    @classmethod
    def _coerce_empty_int_fields(cls, values: Any) -> Any:
        """Drop empty-string env-var values for integer fields so pydantic uses field defaults."""
        int_fields = {"max_price", "min_bedrooms", "min_land_area", "smtp_port"}
        if isinstance(values, dict):
            for field in int_fields:
                if values.get(field) == "":
                    del values[field]
        return values

    @field_validator("log_level")
    @classmethod
    def upper_log_level(cls, v: str) -> str:
        return v.upper()


# Singleton — import this everywhere
settings = Settings()
