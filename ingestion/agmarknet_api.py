"""Live Agmarknet Daily Prices ingestion client (data.gov.in resource 9ef84268-d588-465a-a308-a864a43d0070)."""

from __future__ import annotations

import logging
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from config import AGMARKNET_API_BASE, AGMARKNET_RESOURCE_ID, TARGET_STATES, get_settings
from ingestion.storage import RawZoneStorage

logger = logging.getLogger(__name__)

# Hard ceiling on pages per fetch. At limit=500 this is 200,000 records, an order of magnitude above
# the ~17,800 records the daily feed carries, so it only guards against a runaway pagination loop.
DEFAULT_MAX_PAGES = 400


def build_fetch_reconciliation(
    records: list[dict[str, Any]],
    total_reported: int | None,
    pages_fetched: int,
    truncated: bool = False,
    error_message: str | None = None,
    required_states: Iterable[str] = TARGET_STATES,
) -> dict[str, Any]:
    """Reported-versus-fetched accounting and state coverage for one Agmarknet pull.

    ``complete`` is True only when the feed's own reported total was reached without truncation or
    error. State names are normalised with the same rules as the ETL so coverage matches what the
    warehouse will store.
    """
    from etl.cleaning import normalize_state_name

    state_counts: Counter[str] = Counter()
    seen: set[tuple[Any, ...]] = set()
    exact_duplicates = 0
    for rec in records:
        state = normalize_state_name(rec.get("state")) or "(missing)"
        state_counts[state] += 1
        fingerprint = tuple(
            rec.get(k) for k in (
                "state", "district", "market", "commodity", "variety", "grade",
                "arrival_date", "min_price", "max_price", "modal_price",
            )
        )
        if fingerprint in seen:
            exact_duplicates += 1
        else:
            seen.add(fingerprint)

    fetched = len(records)
    shortfall = max(0, total_reported - fetched) if total_reported is not None else None
    complete = (
        error_message is None
        and not truncated
        and total_reported is not None
        and fetched >= total_reported
    )
    required = sorted({normalize_state_name(s) for s in required_states})
    return {
        "total_reported": total_reported,
        "rows_fetched": fetched,
        "pages_fetched": pages_fetched,
        "shortfall_vs_reported": shortfall,
        "truncated": truncated,
        "complete": complete,
        "exact_duplicate_records": exact_duplicates,
        "state_count": len([s for s in state_counts if s != "(missing)"]),
        "records_by_state": dict(sorted(state_counts.items())),
        "missing_required_states": [s for s in required if s not in state_counts],
    }


@dataclass
class IngestionResult:
    source_name: str
    status: str  # success | partial_success | failed | unavailable
    records: list[dict[str, Any]] = field(default_factory=list)
    raw_files: list[Path] = field(default_factory=list)
    rows_fetched: int = 0
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgmarknetClient:
    """Fetches daily mandi price records from data.gov.in with pagination, retries, backoff, caching, and raw preservation."""

    FIELD_ALIASES: dict[str, tuple[str, ...]] = {
        "state": ("state", "State", "state_name"),
        "district": ("district", "District", "district_name"),
        "market": ("market", "Market", "market_name"),
        "commodity": ("commodity", "Commodity", "commodity_name"),
        "variety": ("variety", "Variety"),
        "grade": ("grade", "Grade"),
        "arrival_date": ("arrival_date", "Arrival_Date", "date"),
        "min_price": ("min_price", "Min_Price", "Min Price (Rs./Quintal)"),
        "max_price": ("max_price", "Max_Price", "Max Price (Rs./Quintal)"),
        "modal_price": ("modal_price", "Modal_Price", "Modal Price (Rs./Quintal)"),
    }

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = AGMARKNET_API_BASE,
        storage: RawZoneStorage | None = None,
        timeout: int | None = None,
        max_retries: int = 4,
        backoff_base: float = 1.5,
        session: requests.Session | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = (api_key if api_key is not None else settings.data_gov_in_api_key).strip()
        self.base_url = base_url
        self.storage = storage or RawZoneStorage()
        self.timeout = timeout or settings.request_timeout_seconds
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.session = session or requests.Session()

    def _request_page(
        self,
        limit: int,
        offset: int,
        filters: dict[str, str] | None = None,
        use_cache: bool = True,
        cache_scope: dict[str, Any] | None = None,
        write_cache: bool = True,
    ) -> tuple[dict[str, Any], bool]:
        """Fetch one page, retrying 429/5xx with Retry-After or exponential backoff.

        ``cache_scope`` ties a cached page to the day and to the feed state it was fetched from, so
        a page cached before the feed changed (or on an earlier day) can never be served later.
        """
        params: dict[str, Any] = {
            "api-key": self.api_key,
            "format": "json",
            "limit": limit,
            "offset": offset,
        }
        for k, v in (filters or {}).items():
            if v:
                params[f"filters[{k}]"] = v

        cache_key = self._cache_key(limit, offset, filters, cache_scope)
        if use_cache:
            cached = self.storage.get_cached_json(cache_key)
            if isinstance(cached, dict) and "records" in cached:
                logger.debug("Using cached Agmarknet page offset=%d limit=%d", offset, limit)
                return cached, True

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(
                    self.base_url,
                    params=params,
                    timeout=self.timeout,
                    headers={"User-Agent": "MandiMitra/1.0 (+HCL-Capstone)"},
                )
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    retry_after = resp.headers.get("Retry-After")
                    wait_s = (
                        float(retry_after)
                        if retry_after and retry_after.replace(".", "", 1).isdigit()
                        else (self.backoff_base ** attempt) + random.uniform(0.05, 0.35)
                    )
                    logger.warning(
                        "Agmarknet HTTP %d on offset=%d (attempt %d/%d); retrying in %.2fs",
                        resp.status_code,
                        offset,
                        attempt + 1,
                        self.max_retries + 1,
                        wait_s,
                    )
                    if attempt < self.max_retries:
                        time.sleep(wait_s)
                        continue
                    resp.raise_for_status()

                resp.raise_for_status()
                payload = resp.json()
                if not isinstance(payload, dict) or "records" not in payload or not isinstance(payload["records"], list):
                    raise ValueError(
                        f"Malformed Agmarknet API payload at offset={offset}: missing list 'records' key"
                    )
                if write_cache:
                    self.storage.set_cached_json(cache_key, payload)
                return payload, False
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    wait_s = (self.backoff_base ** attempt) + random.uniform(0.05, 0.25)
                    logger.warning(
                        "Agmarknet request failed on offset=%d (%s); retrying in %.2fs",
                        offset,
                        exc,
                        wait_s,
                    )
                    time.sleep(wait_s)
                else:
                    break

        assert last_exc is not None
        raise last_exc

    @classmethod
    def normalize_raw_record(cls, raw: dict[str, Any]) -> dict[str, Any]:
        """Extract canonical raw keys regardless of API casing quirks."""
        out: dict[str, Any] = {}
        for target_key, aliases in cls.FIELD_ALIASES.items():
            val = None
            for alias in aliases:
                if alias in raw:
                    val = raw[alias]
                    break
            out[target_key] = val
        return out

    @staticmethod
    def _cache_key(
        limit: int,
        offset: int,
        filters: dict[str, str] | None,
        cache_scope: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Cache identity of a page: resource, paging, filters AND the day/feed state it came from."""
        return {
            "resource_id": AGMARKNET_RESOURCE_ID,
            "limit": limit,
            "offset": offset,
            "filters": filters or {},
            "scope": cache_scope or {},
        }

    @staticmethod
    def _feed_scope(payload: dict[str, Any], fetch_date: date) -> dict[str, Any]:
        """Day plus the feed's own total and update stamp: any change invalidates cached pages."""
        return {
            "fetch_date": fetch_date.isoformat(),
            "feed_total": payload.get("total"),
            "feed_updated": payload.get("updated_date") or payload.get("updated"),
        }

    def fetch_daily_prices(
        self,
        limit: int = 500,
        max_records: int | None = None,
        filters: dict[str, str] | None = None,
        use_cache: bool = True,
        save_raw: bool = True,
        max_pages: int = DEFAULT_MAX_PAGES,
        fetch_date: date | None = None,
    ) -> IngestionResult:
        """Paginate through the complete Agmarknet daily feed and preserve raw JSON pages.

        By default the whole feed is fetched; ``max_records`` is an explicit cap that marks the
        result as truncated. The first page is always requested live. Its total and update stamp
        scope the cache for the remaining pages, so cached pages are only reused for the same day
        and the same feed state. Retries on 429/5xx honour ``Retry-After`` and back off exponentially.
        """
        if not self.api_key:
            msg = "DATA_GOV_IN_API_KEY is not configured; live Agmarknet fetch cannot authenticate."
            logger.warning(msg)
            return IngestionResult(
                source_name="agmarknet_api",
                status="unavailable",
                error_message=msg,
                metadata={"resource_id": AGMARKNET_RESOURCE_ID},
            )

        fetch_day = fetch_date or datetime.now(timezone.utc).date()
        offset = 0
        all_records: list[dict[str, Any]] = []
        raw_files: list[Path] = []
        pages_fetched = 0
        cache_hits = 0
        total_reported: int | None = None
        total_drift: list[int] = []
        scope: dict[str, Any] | None = None
        truncated = False
        error_message: str | None = None

        try:
            while True:
                if max_records is not None and len(all_records) >= max_records:
                    truncated = total_reported is None or len(all_records) < total_reported
                    break
                if pages_fetched >= max_pages:
                    truncated = True
                    error_message = f"Stopped after the {max_pages}-page safety ceiling."
                    break

                page_limit = limit if max_records is None else min(limit, max_records - len(all_records))
                first_page = scope is None
                payload, from_cache = self._request_page(
                    limit=page_limit,
                    offset=offset,
                    filters=filters,
                    use_cache=use_cache and not first_page,
                    cache_scope=scope,
                    write_cache=not first_page,
                )
                pages_fetched += 1
                if from_cache:
                    cache_hits += 1

                if first_page:
                    scope = self._feed_scope(payload, fetch_day)
                    if use_cache:
                        self.storage.set_cached_json(
                            self._cache_key(page_limit, offset, filters, scope), payload
                        )

                if save_raw and not from_cache:
                    raw_path = self.storage.save_raw_json(
                        source="agmarknet",
                        payload=payload,
                        tag=f"offset_{offset}",
                    )
                    raw_files.append(raw_path)

                page_total: int | None = None
                if payload.get("total") is not None:
                    try:
                        page_total = int(payload["total"])
                    except (TypeError, ValueError):
                        page_total = None
                if total_reported is None:
                    total_reported = page_total
                elif page_total is not None and page_total != total_reported:
                    total_drift.append(page_total)

                records = payload.get("records", [])
                if not records:
                    break

                for rec in records:
                    if isinstance(rec, dict):
                        all_records.append(self.normalize_raw_record(rec))

                offset += len(records)
                if len(records) < page_limit:
                    break
                if total_reported is not None and offset >= total_reported:
                    break
        except Exception as exc:
            logger.error("Agmarknet ingestion failed after %d pages: %s", pages_fetched, exc)
            error_message = str(exc)

        reconciliation = build_fetch_reconciliation(
            all_records,
            total_reported=total_reported,
            pages_fetched=pages_fetched,
            truncated=truncated,
            error_message=error_message,
        )
        metadata: dict[str, Any] = {
            "resource_id": AGMARKNET_RESOURCE_ID,
            "pages_fetched": pages_fetched,
            "cache_hits": cache_hits,
            "total_reported": total_reported,
            "total_drift_observed": total_drift,
            "filters": filters or {},
            "fetch_date": fetch_day.isoformat(),
            "fetch_reconciliation": reconciliation,
            "note": "Live daily price API does not carry arrival tonnage.",
        }
        if error_message is None and not reconciliation["complete"]:
            if truncated:
                error_message = f"Fetch truncated by max_records={max_records}: {len(all_records)} of {total_reported} records."
            elif total_reported is not None:
                error_message = (
                    f"Incomplete fetch: {len(all_records)} of {total_reported} reported records "
                    f"(shortfall {reconciliation['shortfall_vs_reported']})."
                )
        if error_message is None:
            status = "success"
        else:
            status = "partial_success" if all_records else "failed"
        return IngestionResult(
            source_name="agmarknet_api",
            status=status,
            records=all_records,
            raw_files=raw_files,
            rows_fetched=len(all_records),
            error_message=error_message,
            metadata=metadata,
        )
