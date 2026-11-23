"""Pre-deployment safety: workflow gates, init_db seeding policy, daily capture caps."""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from config import ROOT_DIR, Settings
from database.connection import DatabaseManager
from ingestion.agmarknet_api import AgmarknetClient
from ingestion.scheduler import run_daily_price_ingestion
from scripts import init_db as init_db_mod

WORKFLOW = ROOT_DIR / ".github" / "workflows" / "daily_ingestion.yml"
GITIGNORE = ROOT_DIR / ".gitignore"
ENV_EXAMPLE = ROOT_DIR / ".env.example"


def test_project_is_not_inside_a_git_work_tree() -> None:
    """Release tree must not silently nest under another repo; do not init git here."""
    assert not (ROOT_DIR / ".git").exists()
    cursor = ROOT_DIR
    while True:
        parent = cursor.parent
        if parent == cursor:
            break
        if (parent / ".git").exists():
            pytest.fail(f"Found enclosing git metadata at {parent}")
        cursor = parent


def test_gitignore_excludes_secrets_raw_data_backups_and_generated_artifacts() -> None:
    text = GITIGNORE.read_text(encoding="utf-8")
    required = [
        ".env",
        "*.env",
        "credentials.json",
        "*.pem",
        "*.key",
        "backups/",
        "*.dump",
        "*.backup",
        "*.db",
        "*.sqlite",
        "data/raw/*",
        "data/cache/",
        ".dist/",
        "frontend/.next/",
        "frontend/node_modules/",
        ".venv/",
    ]
    for token in required:
        assert token in text, f".gitignore missing {token!r}"
    assert "!.env.example" in text


def test_env_example_documents_safe_default_feature_switches() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for key in ("RAW_STORAGE_UPLOAD_ENABLED=false", "GEMINI_ENABLED=false", "ADVISOR_PERSIST=false"):
        assert key in text
    s = Settings(
        DATABASE_URL="",
        DEMO_MODE="true",
        SUPABASE_URL="https://x.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="k",
        SUPABASE_STORAGE_BUCKET="b",
        GEMINI_API_KEY="g",
    )
    assert s.storage_upload_active is False
    assert s.gemini_active is False
    assert s.advisor_persistence_allowed(is_demo_db=False) is False


def _workflow_active_body() -> str:
    """Strip full-line comments so disabled schedule blocks are not treated as active."""
    lines = []
    for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def test_daily_ingestion_workflow_schedule_enabled_but_never_on_push() -> None:
    raw = WORKFLOW.read_text(encoding="utf-8")
    active = _workflow_active_body()

    assert "push:" not in active
    assert "pull_request:" not in active
    # Schedule is active after supervised first-run approval.
    assert "schedule:" in active
    assert 'cron: "30 1 * * *"' in active
    assert "workflow_dispatch:" in active
    assert "APPLY-PRODUCTION-INGESTION" in active
    assert "confirm_production_writes" in active
    # Manual runs still require the confirmation phrase; scheduled runs are allowed.
    assert "github.event_name == 'schedule'" in active.replace("\n", " ")
    assert "confirm_production_writes == 'APPLY-PRODUCTION-INGESTION'" in active.replace("\n", " ")

    # No silent 5000-record truncation in the default capture command.
    assert '--max-records "${{ github.event.inputs.max_records || \'5000\' }}"' not in raw
    assert "default: \"5000\"" not in active
    assert "python scripts/ingest_all.py --daily" in active
    # CI must not auto-seed PostgreSQL reference dimensions.
    assert "init_db.py --seed-reference" not in active
    assert "python scripts/init_db.py" in active


def test_init_db_postgresql_path_never_seeds_without_explicit_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    seeded = MagicMock(return_value={"commodities": 1, "crops": 1, "mappings": 1})
    monkeypatch.setattr(init_db_mod, "seed_reference_dimensions", seeded)
    monkeypatch.setattr(init_db_mod, "configure_logging", lambda: None)
    monkeypatch.setattr(
        init_db_mod,
        "get_settings",
        lambda refresh=False: SimpleNamespace(validate_startup=lambda: {"database": "redacted"}),
    )

    fake_db = MagicMock()
    fake_db.backend = "postgresql"
    fake_db.health_check.return_value = (True, "ok")
    fake_db.initialize_schema.return_value = None
    monkeypatch.setattr(init_db_mod, "DatabaseManager", lambda *a, **k: fake_db)
    monkeypatch.setattr(
        "sys.argv",
        ["init_db.py"],
    )
    init_db_mod.main()
    fake_db.initialize_schema.assert_called_once()
    seeded.assert_not_called()

    monkeypatch.setattr("sys.argv", ["init_db.py", "--seed-reference"])
    init_db_mod.main()
    seeded.assert_called_once_with(fake_db)


def test_init_db_demo_sqlite_still_seeds_reference_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "demo_safety.db"
    monkeypatch.setattr(init_db_mod, "DEMO_DB_PATH", db_path)
    monkeypatch.setattr(init_db_mod, "configure_logging", lambda: None)
    monkeypatch.setattr(
        init_db_mod,
        "get_settings",
        lambda refresh=False: SimpleNamespace(validate_startup=lambda: {"database": str(db_path)}),
    )
    monkeypatch.setattr("sys.argv", ["init_db.py", "--demo"])
    init_db_mod.main()

    db = DatabaseManager(database_url=f"sqlite:///{db_path.as_posix()}")
    with db.readonly_connection() as conn:
        from sqlalchemy import text

        commodities = conn.execute(text("SELECT COUNT(*) FROM dim_commodity")).scalar()
        crops = conn.execute(text("SELECT COUNT(*) FROM dim_crop")).scalar()
        mappings = conn.execute(text("SELECT COUNT(*) FROM map_commodity_crop")).scalar()
    assert int(commodities) >= 1
    assert int(crops) >= 1
    assert int(mappings) >= 1


def test_daily_capture_defaults_do_not_silently_truncate_pagination() -> None:
    assert inspect.signature(run_daily_price_ingestion).parameters["max_records"].default is None
    assert inspect.signature(AgmarknetClient.fetch_daily_prices).parameters["max_records"].default is None

    import scripts.ingest_all as ingest_all

    # Reconstruct the argparse default without executing ingestion.
    parser_src = Path(ingest_all.__file__).read_text(encoding="utf-8")
    assert 'default=None' in parser_src
    assert '"--max-records"' in parser_src
    assert "fetch the complete feed" in parser_src
