"""Raw zone storage (append-only local raw artifacts + optional Supabase Storage) and API response cache."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import DATA_CACHE_DIR, DATA_RAW_DIR, get_settings

logger = logging.getLogger(__name__)


class RawZoneStorage:
    """Preserves unchanged raw JSON/CSV artifacts locally and optionally in Supabase Storage."""

    def __init__(self, raw_root: Path | None = None, cache_dir: Path | None = None) -> None:
        self.raw_root = raw_root or DATA_RAW_DIR
        self.cache_dir = cache_dir or DATA_CACHE_DIR
        self.raw_root.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def save_raw_json(
        self,
        source: str,
        payload: Any,
        tag: str = "page",
        upload_remote: bool = True,
    ) -> Path:
        """Append-only save of unchanged JSON payload with fetch timestamp."""
        day_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dest_dir = self.raw_root / source / day_str
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{source}_{tag}_{self._timestamp()}.json"
        dest_path = dest_dir / filename
        dest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        if upload_remote:
            self._maybe_upload_supabase(dest_path, f"{source}/{day_str}/{filename}", "application/json")
        return dest_path

    def save_raw_file(
        self,
        source: str,
        src_path: Path,
        upload_remote: bool = True,
    ) -> Path:
        """Copy an incoming raw CSV/JSON file unchanged into the timestamped raw zone."""
        day_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dest_dir = self.raw_root / source / day_str
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_name = f"{src_path.stem}_{self._timestamp()}{src_path.suffix}"
        dest_path = dest_dir / dest_name
        shutil.copy2(src_path, dest_path)

        if upload_remote:
            ctype = "text/csv" if src_path.suffix.lower() == ".csv" else "application/octet-stream"
            self._maybe_upload_supabase(dest_path, f"{source}/{day_str}/{dest_name}", ctype)
        return dest_path

    def _maybe_upload_supabase(self, local_path: Path, remote_key: str, content_type: str) -> bool:
        """Upload a raw artifact only when RAW_STORAGE_UPLOAD_ENABLED=true and credentials exist.

        Raw files always stay in the local raw zone; having Supabase credentials configured is
        not, by itself, permission to upload. Existing Storage objects are never deleted here.
        """
        settings = get_settings()
        if not settings.storage_upload_active:
            return False
        try:
            from supabase import create_client

            client = create_client(settings.supabase_url, settings.supabase_service_role_key)
            bucket = settings.supabase_storage_bucket
            with local_path.open("rb") as fh:
                client.storage.from_(bucket).upload(
                    path=remote_key,
                    file=fh,
                    file_options={"content-type": content_type, "upsert": "true"},
                )
            logger.info("Uploaded raw artifact to Supabase Storage: %s/%s", bucket, remote_key)
            return True
        except Exception as exc:
            logger.warning("Supabase Storage upload skipped/failed for %s: %s", remote_key, exc)
            return False

    # ------------------------------------------------------------------
    # Disk response caching
    # ------------------------------------------------------------------
    def _cache_path(self, key_parts: dict[str, Any]) -> Path:
        digest = hashlib.sha256(
            json.dumps(key_parts, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:24]
        return self.cache_dir / f"{digest}.json"

    def get_cached_json(self, key_parts: dict[str, Any], ttl_hours: int | None = None) -> Any | None:
        settings = get_settings()
        ttl = settings.api_cache_ttl_hours if ttl_hours is None else ttl_hours
        if ttl <= 0:
            return None
        path = self._cache_path(key_parts)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            cached_at = datetime.fromisoformat(payload["cached_at"])
            if datetime.now(timezone.utc) - cached_at <= timedelta(hours=ttl):
                return payload["data"]
        except Exception:
            return None
        return None

    def set_cached_json(self, key_parts: dict[str, Any], data: Any) -> Path:
        path = self._cache_path(key_parts)
        wrapper = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "key_parts": key_parts,
            "data": data,
        }
        path.write_text(json.dumps(wrapper, ensure_ascii=False), encoding="utf-8")
        return path
