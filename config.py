"""Centralized configuration and validation for MandiMitra."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
DATA_RAW_DIR = DATA_DIR / "raw"
DATA_SAMPLE_DIR = DATA_DIR / "sample"
DATA_CACHE_DIR = DATA_RAW_DIR / "_cache"
DOCS_DIR = ROOT_DIR / "docs"
DATABASE_DIR = ROOT_DIR / "database"
DEMO_DB_PATH = DATA_SAMPLE_DIR / "mandimitra_demo.db"
TUNED_THRESHOLDS_PATH = DATA_SAMPLE_DIR / "tuned_thresholds.json"
LIVE_TUNED_THRESHOLDS_PATH = DATA_DIR / "live_tuned_thresholds.json"

AGMARKNET_RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
AGMARKNET_API_BASE = f"https://api.data.gov.in/resource/{AGMARKNET_RESOURCE_ID}"
CEDA_BASE_URL = "https://agmarknet.ceda.ashoka.edu.in"
APY_REPORT_URL = "https://data.desagri.gov.in/website/crops-apy-report-web"
APY_ALT_URL = "https://aps.dac.gov.in/APY/Public_Report1.aspx"


@dataclass(frozen=True)
class CommoditySpec:
    name: str
    category: str
    storable: bool
    crop_name: str
    crop_type: str


# 18 target Indian agricultural commodities spanning perishables & storables
TARGET_COMMODITIES: tuple[CommoditySpec, ...] = (
    CommoditySpec("Tomato", "vegetable", False, "Tomato", "Horticulture"),
    CommoditySpec("Onion", "vegetable", False, "Onion", "Horticulture"),
    CommoditySpec("Cabbage", "vegetable", False, "Cabbage", "Horticulture"),
    CommoditySpec("Cauliflower", "vegetable", False, "Cauliflower", "Horticulture"),
    CommoditySpec("Brinjal", "vegetable", False, "Brinjal", "Horticulture"),
    CommoditySpec("Green Chilli", "vegetable", False, "Chillies", "Spice"),
    CommoditySpec("Potato", "vegetable", True, "Potato", "Horticulture"),
    CommoditySpec("Garlic", "spice", True, "Garlic", "Spice"),
    CommoditySpec("Turmeric", "spice", True, "Turmeric", "Spice"),
    CommoditySpec("Wheat", "cereal", True, "Wheat", "Cereal"),
    CommoditySpec("Rice", "cereal", True, "Rice", "Cereal"),
    CommoditySpec("Maize", "cereal", True, "Maize", "Cereal"),
    CommoditySpec("Soybean", "oilseed", True, "Soyabean", "Oilseed"),
    CommoditySpec("Groundnut", "oilseed", True, "Groundnut", "Oilseed"),
    CommoditySpec("Mustard", "oilseed", True, "Rapeseed & Mustard", "Oilseed"),
    CommoditySpec("Chickpea", "pulse", True, "Gram", "Pulse"),
    CommoditySpec("Pigeon Pea (Tur)", "pulse", True, "Arhar/Tur", "Pulse"),
    CommoditySpec("Green Gram (Moong)", "pulse", True, "Moong(Green Gram)", "Pulse"),
)

TARGET_STATES: tuple[str, ...] = (
    "Maharashtra",
    "Uttar Pradesh",
    "Madhya Pradesh",
    "Rajasthan",
    "Gujarat",
    "Karnataka",
    "Punjab",
    "Haryana",
    "West Bengal",
    "Bihar",
    "Andhra Pradesh",
    "Tamil Nadu",
)


class SignalThresholds(BaseModel):
    """Configurable deterministic rule thresholds (tuned via backtesting)."""

    momentum_wait_pct: float = Field(default=3.0, ge=0.0, le=50.0)
    momentum_sell_pct: float = Field(default=-3.0, ge=-50.0, le=0.0)
    seasonal_peak_pct: float = Field(default=10.0, ge=0.0, le=100.0)
    seasonal_below_pct: float = Field(default=-2.0, ge=-50.0, le=50.0)
    arrivals_rise_pct: float = Field(default=15.0, ge=0.0, le=200.0)
    arrivals_not_rising_pct: float = Field(default=5.0, ge=-50.0, le=100.0)
    high_volatility_cv: float = Field(default=0.20, ge=0.01, le=2.0)
    min_obs_recent_7d: int = Field(default=2, ge=1)
    min_obs_prior_7d: int = Field(default=2, ge=1)
    min_obs_90d: int = Field(default=10, ge=3)
    threshold_source: str = Field(default="initial_proposal_placeholder")

    @classmethod
    def load_tuned_or_default(cls, is_demo: bool | None = None) -> "SignalThresholds":
        """Load demo-tuned thresholds ONLY in Demo Mode; live mode uses live_tuned_thresholds.json or defaults."""
        demo = get_settings().is_demo_mode if is_demo is None else is_demo
        target_path = TUNED_THRESHOLDS_PATH if demo else LIVE_TUNED_THRESHOLDS_PATH
        if target_path.exists():
            try:
                data = json.loads(target_path.read_text(encoding="utf-8"))
                return cls(**data)
            except Exception:
                pass
        return cls()


class Settings(BaseSettings):
    """Application settings loaded safely from environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(default="", alias="DATABASE_URL")
    data_gov_in_api_key: str = Field(default="", alias="DATA_GOV_IN_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3.8-flash", alias="GEMINI_MODEL")
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    supabase_service_role_key: str = Field(default="", alias="SUPABASE_SERVICE_ROLE_KEY")
    supabase_storage_bucket: str = Field(default="mandimitra-raw", alias="SUPABASE_STORAGE_BUCKET")
    demo_mode: Literal["auto", "true", "false"] = Field(default="auto", alias="DEMO_MODE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    request_timeout_seconds: int = Field(default=30, ge=5, le=180, alias="REQUEST_TIMEOUT_SECONDS")
    api_cache_ttl_hours: int = Field(default=12, ge=0, le=720, alias="API_CACHE_TTL_HOURS")
    # Production-readiness switches: every outward side effect is opt-in and defaults to OFF.
    # Credentials alone never enable an upload or an AI call.
    raw_storage_upload_enabled: bool = Field(default=False, alias="RAW_STORAGE_UPLOAD_ENABLED")
    gemini_enabled: bool = Field(default=False, alias="GEMINI_ENABLED")
    advisor_persist_enabled: bool = Field(default=False, alias="ADVISOR_PERSIST")
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        alias="CORS_ORIGINS",
    )
    # Optional regex for additional allowed origins, e.g. Vercel preview URLs:
    # ^https://mandimitra-[a-z0-9-]+\.vercel\.app$
    cors_origin_regex: str = Field(default="", alias="CORS_ORIGIN_REGEX")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @field_validator("demo_mode", mode="before")
    @classmethod
    def _normalize_demo_mode(cls, v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        s = str(v or "auto").strip().lower()
        return s if s in {"auto", "true", "false"} else "auto"

    @property
    def has_database_url(self) -> bool:
        return bool(self.database_url and self.database_url.strip())

    @property
    def is_demo_mode(self) -> bool:
        if self.demo_mode == "true":
            return True
        if self.demo_mode == "false":
            return False
        return not self.has_database_url

    @property
    def has_supabase_storage(self) -> bool:
        return bool(
            self.supabase_url.strip()
            and self.supabase_service_role_key.strip()
            and self.supabase_storage_bucket.strip()
        )

    @property
    def storage_upload_active(self) -> bool:
        """Raw artifacts are uploaded to Supabase Storage only when explicitly enabled AND configured."""
        return self.raw_storage_upload_enabled and self.has_supabase_storage

    @property
    def has_data_gov_key(self) -> bool:
        return bool(self.data_gov_in_api_key and self.data_gov_in_api_key.strip())

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key.strip())

    @property
    def gemini_active(self) -> bool:
        """Gemini is called only when explicitly enabled AND a key is configured."""
        return self.gemini_enabled and self.has_gemini_key

    def advisor_persistence_allowed(self, is_demo_db: bool) -> bool:
        """Advisor signals/briefings are written to a non-demo database only when ADVISOR_PERSIST=true.

        The disposable Demo SQLite warehouse keeps persisting so demo history works unchanged.
        """
        return bool(is_demo_db or self.advisor_persist_enabled)

    def safe_db_summary(self) -> str:
        if self.is_demo_mode:
            return f"sqlite:///{DEMO_DB_PATH.name} (DEMO MODE)"
        parsed = urlparse(self.database_url)
        host = parsed.hostname or "unknown-host"
        port = parsed.port or 5432
        db = parsed.path.lstrip("/") or "postgres"
        return f"postgresql://{host}:{port}/{db}"

    def validate_startup(self) -> dict[str, Any]:
        """Validate configuration at startup without raising when demo fallback is active."""
        DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
        DATA_SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
        DATA_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        warnings: list[str] = []
        if not self.has_database_url:
            if self.demo_mode == "false":
                raise ValueError(
                    "DEMO_MODE=false requires DATABASE_URL to be configured in .env."
                )
            warnings.append(
                "DATABASE_URL is not set; running with clearly labelled Demo SQLite warehouse."
            )
        if not self.has_data_gov_key:
            warnings.append("DATA_GOV_IN_API_KEY is not set; live Agmarknet API pulls require a key.")
        if not self.has_gemini_key:
            warnings.append("GEMINI_API_KEY is not set; briefings will use validated template fallback.")
        elif not self.gemini_enabled:
            warnings.append(
                "Gemini is disabled (GEMINI_ENABLED is not true); briefings use the deterministic template fallback."
            )
        if not self.has_supabase_storage:
            warnings.append("Supabase Storage credentials not set; raw files are preserved locally in data/raw/.")
        elif not self.raw_storage_upload_enabled:
            warnings.append(
                "Supabase Storage credentials are present but raw uploads are disabled "
                "(RAW_STORAGE_UPLOAD_ENABLED is not true); raw files are preserved locally in data/raw/."
            )
        if not self.is_demo_mode and not self.advisor_persist_enabled:
            warnings.append(
                "Advisor persistence is off (ADVISOR_PERSIST is not true); recommendations are computed "
                "without writing analytics.market_signals or analytics.briefings."
            )

        return {
            "mode": "demo" if self.is_demo_mode else "postgresql",
            "database": self.safe_db_summary(),
            "has_database_url": self.has_database_url,
            "has_supabase_storage": self.has_supabase_storage,
            "raw_storage_upload_enabled": self.storage_upload_active,
            "has_data_gov_key": self.has_data_gov_key,
            "has_gemini_key": self.has_gemini_key,
            "gemini_enabled": self.gemini_active,
            "advisor_persist_enabled": self.advisor_persist_enabled,
            "warnings": warnings,
        }


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return "<not set>"
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"


def configure_logging(level: str | None = None) -> None:
    lvl = getattr(logging, (level or get_settings().log_level).upper(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


_cached_settings: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    global _cached_settings
    if _cached_settings is None or refresh:
        _cached_settings = Settings()
    return _cached_settings
