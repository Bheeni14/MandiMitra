"""Regression tests: the test suite must never upload to real Supabase Storage.

Background: tests that preserved raw files used ``RawZoneStorage(raw_root=tmp_path ...)``. That only
isolates *local* paths; ``_maybe_upload_supabase`` still read credentials from the developer's
``.env`` and uploaded the artifacts to a real bucket. These tests pin the fix.

Everything here uses dummy credentials and in-memory fake ``supabase`` modules: no network I/O.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from config import get_settings
from ingestion.ceda_importer import CedaImporter
from ingestion.storage import RawZoneStorage
from tests import test_ingestion


def _write_ceda_csv(path: Path) -> Path:
    pd.DataFrame(
        [
            {
                "Arrival Date": "10/11/2025",
                "State Name": "Maharashtra",
                "District Name": "Nashik",
                "Market Name": "Lasalgaon",
                "Commodity Name": "Onion",
                "Arrivals (Tonnes)": 125.5,
            }
        ]
    ).to_csv(path, index=False)
    return path


@pytest.fixture()
def worst_case_supabase(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Simulate the dangerous situation: valid-looking credentials AND a working SDK are present.

    The SDK is an in-memory fake, so if any code path *does* try to upload, it is counted instead
    of leaving the machine.
    """
    monkeypatch.setenv("SUPABASE_URL", "https://dummy-project.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "dummy-service-role-key")
    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "dummy-bucket")
    monkeypatch.setenv("RAW_STORAGE_UPLOAD_ENABLED", "true")  # worst case: uploads explicitly enabled
    get_settings(refresh=True)
    assert get_settings().has_supabase_storage is True
    assert get_settings().storage_upload_active is True  # the hazard is genuinely armed

    bucket = MagicMock()
    client = SimpleNamespace(storage=SimpleNamespace(from_=MagicMock(return_value=bucket)))
    create_client = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "supabase", SimpleNamespace(create_client=create_client))
    return SimpleNamespace(create_client=create_client, bucket=bucket)


def test_default_test_environment_has_supabase_credentials_blanked() -> None:
    """Whatever the developer's .env holds, tests start with remote storage disabled."""
    settings = get_settings()
    assert settings.has_supabase_storage is False
    assert not settings.supabase_url
    assert not settings.supabase_service_role_key


def test_real_supabase_client_construction_is_blocked_loudly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    supabase_guard: SimpleNamespace,
) -> None:
    """Even if credentials appear, the real SDK's create_client is guarded and cannot be swallowed."""
    real_sdk = pytest.importorskip("supabase")
    # Precondition checked BEFORE any call: the guard really is installed on the real SDK.
    assert real_sdk.create_client.__name__ == "_blocked_create_client"

    monkeypatch.setenv("SUPABASE_URL", "https://dummy-project.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "dummy-service-role-key")
    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "dummy-bucket")
    monkeypatch.setenv("RAW_STORAGE_UPLOAD_ENABLED", "true")
    get_settings(refresh=True)

    storage = RawZoneStorage(raw_root=tmp_path / "raw", cache_dir=tmp_path / "cache")
    with pytest.raises(supabase_guard.blocked_exc):
        storage.save_raw_json("ceda", {"records": []}, tag="guard", upload_remote=True)
    assert supabase_guard.calls == ["create_client"]


def test_control_plain_storage_would_upload_under_worst_case(
    tmp_path: Path,
    worst_case_supabase: SimpleNamespace,
) -> None:
    """Control: proves this harness can detect an upload (so the zero-upload assertions mean something)."""
    storage = RawZoneStorage(raw_root=tmp_path / "raw", cache_dir=tmp_path / "cache")
    storage.save_raw_json("ceda", {"records": []}, tag="control", upload_remote=True)
    assert worst_case_supabase.create_client.call_count == 1
    assert worst_case_supabase.bucket.upload.call_count == 1


def test_isolated_storage_never_uploads_even_with_credentials_present(
    tmp_path: Path,
    isolated_storage: RawZoneStorage,
    worst_case_supabase: SimpleNamespace,
) -> None:
    csv_path = _write_ceda_csv(tmp_path / "ceda_sample.csv")

    result = CedaImporter(storage=isolated_storage).import_csv_files([csv_path], preserve_raw=True)

    assert result.status == "success"
    assert len(result.raw_files) == 1 and tmp_path in result.raw_files[0].parents
    assert worst_case_supabase.create_client.call_count == 0
    assert worst_case_supabase.bucket.upload.call_count == 0
    # The importer did try to dispatch an upload; the isolated hook intercepted it.
    assert len(isolated_storage.blocked_upload_attempts) == 1  # type: ignore[attr-defined]
    assert isolated_storage.blocked_upload_attempts[0].startswith("ceda/")  # type: ignore[attr-defined]


def test_ceda_test_body_performs_no_supabase_upload_under_worst_case(
    tmp_path: Path,
    isolated_storage: RawZoneStorage,
    supabase_guard: SimpleNamespace,
    worst_case_supabase: SimpleNamespace,
) -> None:
    """Run the actual CEDA/APY ingestion test body with credentials + SDK present: zero upload ops."""
    test_ingestion.test_ceda_and_apy_csv_inspection_and_import(tmp_path, isolated_storage, supabase_guard)

    assert worst_case_supabase.create_client.call_count == 0
    assert worst_case_supabase.bucket.upload.call_count == 0
    assert supabase_guard.calls == []


def test_upload_is_off_by_default_even_with_credentials_and_sdk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worst_case_supabase: SimpleNamespace,
) -> None:
    """Approved decision: raw files stay local. Credentials alone never upload, with plain storage."""
    monkeypatch.delenv("RAW_STORAGE_UPLOAD_ENABLED", raising=False)
    get_settings(refresh=True)
    assert get_settings().has_supabase_storage is True
    assert get_settings().storage_upload_active is False

    storage = RawZoneStorage(raw_root=tmp_path / "raw", cache_dir=tmp_path / "cache")
    path = storage.save_raw_json("agmarknet", {"records": [1]}, tag="local_only", upload_remote=True)

    assert path.exists()  # raw file is kept locally
    assert worst_case_supabase.create_client.call_count == 0
    assert worst_case_supabase.bucket.upload.call_count == 0
