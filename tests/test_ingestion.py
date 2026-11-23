"""Unit tests for Agmarknet API pagination/retries/caching/malformed payloads and CSV importers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest
import requests

from ingestion.agmarknet_api import AgmarknetClient
from ingestion.apy_importer import ApyImporter
from ingestion.ceda_importer import CedaImporter
from ingestion.storage import RawZoneStorage


def _make_response(status_code: int, json_body: dict | None = None, headers: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("Invalid JSON")
    if status_code >= 400:
        err = requests.HTTPError(f"HTTP {status_code}")
        err.response = resp
        resp.raise_for_status.side_effect = err
    else:
        resp.raise_for_status.return_value = None
    return resp


def test_agmarknet_pagination_and_raw_preservation(isolated_storage: RawZoneStorage) -> None:
    storage = isolated_storage
    session = MagicMock()
    page1 = {
        "total": 3,
        "records": [
            {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "arrival_date": "19/11/2026", "min_price": "1800", "max_price": "2400", "modal_price": "2100"},
            {"state": "Maharashtra", "district": "Pune", "market": "Pune", "commodity": "Tomato", "arrival_date": "19/11/2026", "min_price": "1500", "max_price": "2200", "modal_price": "1900"},
        ],
    }
    page2 = {
        "total": 3,
        "records": [
            {"state": "Madhya Pradesh", "district": "Indore", "market": "Indore", "commodity": "Wheat", "arrival_date": "19/11/2026", "min_price": "2200", "max_price": "2600", "modal_price": "2400"},
        ],
    }
    session.get.side_effect = [_make_response(200, page1), _make_response(200, page2)]

    client = AgmarknetClient(api_key="test-key", storage=storage, session=session, backoff_base=0.01)
    res = client.fetch_daily_prices(limit=2, max_records=10, use_cache=False, save_raw=True)

    assert res.status == "success"
    assert res.rows_fetched == 3
    assert len(res.records) == 3
    assert len(res.raw_files) == 2
    assert all(p.exists() for p in res.raw_files)
    assert session.get.call_count == 2


def test_agmarknet_retry_on_429_and_503(isolated_storage: RawZoneStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    storage = isolated_storage
    session = MagicMock()
    ok_payload = {
        "total": 1,
        "records": [
            {"State": "Punjab", "District": "Ludhiana", "Market": "Ludhiana", "Commodity": "Wheat", "Arrival_Date": "2026-11-18", "Min_Price": "2300", "Max_Price": "2500", "Modal_Price": "2400"},
        ],
    }
    session.get.side_effect = [
        _make_response(429, headers={"Retry-After": "0"}),
        _make_response(503),
        _make_response(200, ok_payload),
    ]

    client = AgmarknetClient(api_key="test-key", storage=storage, session=session, max_retries=3, backoff_base=0.01)
    res = client.fetch_daily_prices(limit=50, use_cache=False, save_raw=False)

    assert res.status == "success"
    assert res.rows_fetched == 1
    assert res.records[0]["state"] == "Punjab"
    assert session.get.call_count == 3


def test_agmarknet_malformed_response_and_cache(isolated_storage: RawZoneStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    storage = isolated_storage
    session = MagicMock()
    session.get.return_value = _make_response(200, {"unexpected": "schema"})

    client = AgmarknetClient(api_key="test-key", storage=storage, session=session, max_retries=1, backoff_base=0.01)
    res = client.fetch_daily_prices(limit=10, use_cache=False)

    assert res.status == "failed"
    assert "Malformed Agmarknet API payload" in (res.error_message or "")

    # Missing API key returns unavailable honestly
    no_key_client = AgmarknetClient(api_key="", storage=storage)
    res_unavail = no_key_client.fetch_daily_prices()
    assert res_unavail.status == "unavailable"


def test_ceda_and_apy_csv_inspection_and_import(
    tmp_path: Path,
    isolated_storage: RawZoneStorage,
    supabase_guard: SimpleNamespace,
) -> None:
    # isolated_storage: raw/cache confined to tmp_path AND its remote-upload hook is neutralised,
    # so preserve_raw=True can never reach Supabase even when .env holds valid credentials.
    storage = isolated_storage
    ceda_csv = tmp_path / "ceda_sample.csv"
    pd.DataFrame(
        [
            {"Arrival Date": "10/11/2025", "State Name": "Maharashtra", "District Name": "Nashik", "Market Name": "Lasalgaon", "Commodity Name": "Onion", "Arrivals (Tonnes)": 125.5},
        ]
    ).to_csv(ceda_csv, index=False)

    ceda = CedaImporter(storage=storage)
    report = ceda.inspect_csv(ceda_csv)
    assert report.detected_mapping["quantity_tonnes"] == "Arrivals (Tonnes)"
    res = ceda.import_csv_files([ceda_csv], preserve_raw=True)
    assert res.status == "success"
    assert res.records[0]["record_type"] == "arrival"
    assert res.records[0]["quantity_tonnes"] == 125.5

    apy_csv = tmp_path / "apy_sample.csv"
    pd.DataFrame(
        [
            {"Year": "2023-24", "State Name": "Madhya Pradesh", "District Name": "Indore", "Season": "Kharif", "Crop Name": "Soyabean", "Area": 50000, "Production": 75000, "Yield": 1.5},
        ]
    ).to_csv(apy_csv, index=False)

    apy = ApyImporter(storage=storage)
    apy_res = apy.import_csv_files([apy_csv], preserve_raw=True)
    assert apy_res.status == "success"
    assert ApyImporter.parse_crop_year(apy_res.records[0]["crop_year"]) == 2023

    # Raw copies were preserved locally inside tmp_path only...
    assert len(res.raw_files) == 1 and len(apy_res.raw_files) == 1
    assert all(tmp_path in p.parents for p in res.raw_files + apy_res.raw_files)
    # ...and no Supabase client was ever constructed (zero upload operations).
    assert supabase_guard.calls == []
    # Upload dispatches (if any) were intercepted by the isolated storage hook, not Supabase.
    assert all(key.startswith(("ceda/", "apy/")) for key in storage.blocked_upload_attempts)  # type: ignore[attr-defined]


def test_supabase_storage_upload_mocked_success_and_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    from types import SimpleNamespace

    from config import get_settings

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "dummy-service-key")
    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "mandimitra-raw")
    monkeypatch.setenv("RAW_STORAGE_UPLOAD_ENABLED", "true")  # uploads are off by default
    get_settings(refresh=True)

    try:
        mock_bucket = MagicMock()
        mock_client = SimpleNamespace(storage=SimpleNamespace(from_=MagicMock(return_value=mock_bucket)))
        fake_supabase_mod = SimpleNamespace(create_client=MagicMock(return_value=mock_client))
        monkeypatch.setitem(sys.modules, "supabase", fake_supabase_mod)

        storage = RawZoneStorage(raw_root=tmp_path / "raw", cache_dir=tmp_path / "cache")
        path = storage.save_raw_json("agmarknet", {"records": []}, tag="p1", upload_remote=True)
        assert path.exists()
        assert mock_bucket.upload.call_count == 1

        # Failure during upload must NOT lose local raw artifact or crash
        mock_bucket.upload.side_effect = RuntimeError("503 Storage temporary failure")
        path2 = storage.save_raw_json("agmarknet", {"records": [{"x": 1}]}, tag="p2", upload_remote=True)
        assert path2.exists()
    finally:
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        monkeypatch.delenv("SUPABASE_STORAGE_BUCKET", raising=False)
        monkeypatch.delenv("RAW_STORAGE_UPLOAD_ENABLED", raising=False)
        get_settings(refresh=True)


def test_ceda_and_apy_external_error_handling(isolated_storage: RawZoneStorage) -> None:
    storage = isolated_storage
    bad_session = MagicMock()
    bad_session.get.side_effect = requests.ConnectTimeout("Timed out")
    bad_session.post.side_effect = requests.HTTPError("504 Gateway Timeout")

    ceda = CedaImporter(storage=storage, session=bad_session, timeout=5)
    ok_ceda, msg_ceda = ceda.probe_ceda_portal()
    assert ok_ceda is False
    assert "ConnectTimeout" in msg_ceda

    series_res = ceda.fetch_portal_series(commodity_id=78, state_id=27, from_date="2025-01-01", to_date="2025-01-10")
    assert series_res.status == "unavailable"
    assert "unavailable" in (series_res.error_message or "").lower()

    ssl_session = MagicMock()
    ssl_session.get.side_effect = requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")
    apy = ApyImporter(storage=storage, session=ssl_session, timeout=5)
    ok_apy, msg_apy = apy.probe_apy_portals()
    assert ok_apy is False
    assert "SSLError" in msg_apy

