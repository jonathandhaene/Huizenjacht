"""
Application configuration loaded from environment variables / .env file.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator
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

    # ── Cache ─────────────────────────────────────────────────────────────────
    cache_dir: str = Field(".cache")

    # ── Scheduling ────────────────────────────────────────────────────────────
    daily_run_time: str = Field("07:00")

    # ── Social media ──────────────────────────────────────────────────────────
    facebook_email: Optional[str] = Field(None)
    facebook_password: Optional[str] = Field(None)

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

    @field_validator("log_level")
    @classmethod
    def upper_log_level(cls, v: str) -> str:
        return v.upper()


# Singleton — import this everywhere
settings = Settings()
