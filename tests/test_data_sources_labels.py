"""Data Sources status labels must describe credential presence, never an active connection."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.dependencies import get_app_settings, get_db
from backend.main import create_app
from backend.routers.pipeline import CREDENTIALS_PRESENT_LABEL
from config import Settings
from database.connection import DatabaseManager

DUMMY_SECRET_VALUES = ("dummy-service-role-key", "dummy-data-gov-key", "dummy-gemini-key")


def _components(populated_db: DatabaseManager, settings: Settings) -> dict[str, dict]:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: populated_db
    app.dependency_overrides[get_app_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            resp = client.get("/api/data-sources/status")
        assert resp.status_code == 200
        body = resp.json()
        return {c["component"]: c for c in body["components"]} | {"__raw__": json.dumps(body)}
    finally:
        app.dependency_overrides.clear()


def test_credentials_alone_never_enable_storage_or_gemini(populated_db: DatabaseManager) -> None:
    """Credentials present but switches off: Storage/Gemini are reported as disabled by configuration."""
    settings = Settings(
        DATABASE_URL="",
        DEMO_MODE="true",
        SUPABASE_URL="https://dummy-project.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="dummy-service-role-key",
        SUPABASE_STORAGE_BUCKET="dummy-bucket",
        DATA_GOV_IN_API_KEY="dummy-data-gov-key",
        GEMINI_API_KEY="dummy-gemini-key",
    )
    comps = _components(populated_db, settings)

    assert CREDENTIALS_PRESENT_LABEL == "Credentials present (not tested)"
    assert comps["Agmarknet API (data.gov.in)"]["configured"] is True
    assert comps["Agmarknet API (data.gov.in)"]["status_label"] == CREDENTIALS_PRESENT_LABEL
    assert comps["Supabase Raw Object Storage"]["configured"] is False
    assert comps["Supabase Raw Object Storage"]["status_label"].startswith("Uploads disabled by configuration")
    assert comps["Google Gemini API"]["configured"] is False
    assert comps["Google Gemini API"]["status_label"].startswith("Disabled by configuration")

    for name, comp in comps.items():
        if name in ("__raw__", "Warehouse Database"):
            continue
        label = comp["status_label"].lower()
        assert "connected" not in label and "healthy" not in label
    assert not any(secret in comps["__raw__"] for secret in DUMMY_SECRET_VALUES)


def test_explicit_switches_plus_credentials_report_enabled_not_tested(populated_db: DatabaseManager) -> None:
    settings = Settings(
        DATABASE_URL="",
        DEMO_MODE="true",
        SUPABASE_URL="https://dummy-project.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="dummy-service-role-key",
        SUPABASE_STORAGE_BUCKET="dummy-bucket",
        RAW_STORAGE_UPLOAD_ENABLED="true",
        GEMINI_API_KEY="dummy-gemini-key",
        GEMINI_ENABLED="true",
    )
    comps = _components(populated_db, settings)
    assert comps["Supabase Raw Object Storage"]["configured"] is True
    assert comps["Supabase Raw Object Storage"]["status_label"] == "Upload enabled (not tested)"
    assert comps["Google Gemini API"]["configured"] is True
    assert comps["Google Gemini API"]["status_label"] == "Enabled (not tested)"


def test_enabled_switch_without_credentials_stays_disabled(populated_db: DatabaseManager) -> None:
    settings = Settings(
        DATABASE_URL="",
        DEMO_MODE="true",
        SUPABASE_URL="",
        SUPABASE_SERVICE_ROLE_KEY="",
        RAW_STORAGE_UPLOAD_ENABLED="true",
        GEMINI_API_KEY=" ",
        GEMINI_ENABLED="true",
        DATA_GOV_IN_API_KEY=" ",
    )
    comps = _components(populated_db, settings)
    assert comps["Supabase Raw Object Storage"]["configured"] is False
    assert comps["Google Gemini API"]["configured"] is False
    assert comps["Agmarknet API (data.gov.in)"]["status_label"] == "Key Not Configured"


@pytest.mark.parametrize("field", ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"])
def test_storage_requires_all_credentials_even_when_enabled(populated_db: DatabaseManager, field: str) -> None:
    kwargs = {
        "SUPABASE_URL": "https://dummy-project.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "dummy-service-role-key",
        "SUPABASE_STORAGE_BUCKET": "dummy-bucket",
    }
    kwargs[field] = ""
    settings = Settings(
        DATABASE_URL="", DEMO_MODE="true", DATA_GOV_IN_API_KEY=" ", GEMINI_API_KEY=" ",
        RAW_STORAGE_UPLOAD_ENABLED="true", **kwargs,
    )
    comps = _components(populated_db, settings)
    assert comps["Supabase Raw Object Storage"]["configured"] is False
    assert comps["Supabase Raw Object Storage"]["status_label"].startswith("Uploads disabled by configuration")
