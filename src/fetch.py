"""Step 1 of the pipeline — collect.

Pulls one endpoint per indicator from the World Bank Open Data API, caches
the raw JSON on disk, and flattens the nested response into a tidy
DataFrame with one row per (country, year, indicator).

Run standalone::

    python -m src.fetch
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd
import requests

from . import config

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """Raised when the API cannot be reached or answers with an error body."""


# --------------------------------------------------------------------------
# Caching
# --------------------------------------------------------------------------


def _cache_path(indicator_id: str, countries: Sequence[str], start: int, end: int) -> Path:
    """Deterministic filename so the same query always maps to the same file."""
    stamp = f"{'-'.join(sorted(countries))}_{start}-{end}"
    return config.RAW_DIR / f"{indicator_id}_{stamp}.json"


def _is_fresh(path: Path, ttl_hours: float) -> bool:
    if ttl_hours <= 0 or not path.exists():
        return False
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    )
    return age < timedelta(hours=ttl_hours)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def _request_json(url: str, params: dict[str, Any]) -> Any:
    """GET with retries and exponential backoff; returns parsed JSON."""
    delay = config.RETRY_BACKOFF
    last_error: Exception | None = None

    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt == config.MAX_RETRIES:
                break
            log.warning(
                "request failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt,
                config.MAX_RETRIES,
                exc,
                delay,
            )
            time.sleep(delay)
            delay *= 2

    raise FetchError(f"GET {url} failed after {config.MAX_RETRIES} attempts: {last_error}")


def _unwrap(payload: Any, indicator_id: str) -> list[dict[str, Any]]:
    """Pull the record list out of the World Bank's ``[meta, rows]`` envelope.

    The API answers 200 OK even for bad indicator codes, signalling the
    problem inside the body instead — so the shape has to be checked here.
    """
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        if "message" in payload[0]:
            message = payload[0]["message"]
            raise FetchError(f"API rejected '{indicator_id}': {message}")

    if not isinstance(payload, list) or len(payload) < 2:
        raise FetchError(f"unexpected response shape for '{indicator_id}': {payload!r:.200}")

    rows = payload[1]
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise FetchError(f"unexpected row payload for '{indicator_id}'")
    return rows


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def fetch_indicator(
    indicator_id: str,
    countries: Sequence[str] | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Fetch one indicator for many countries; returns raw API records."""
    countries = list(countries or config.COUNTRIES)
    start_year = start_year or config.START_YEAR
    end_year = end_year or config.END_YEAR

    path = _cache_path(indicator_id, countries, start_year, end_year)
    if use_cache and _is_fresh(path, config.CACHE_TTL_HOURS):
        log.info("cache hit  %-18s %s", indicator_id, path.name)
        return json.loads(path.read_text(encoding="utf-8"))

    url = f"{config.API_BASE_URL}/country/{';'.join(countries)}/indicator/{indicator_id}"
    params = {
        "format": "json",
        "per_page": config.PAGE_SIZE,
        "date": f"{start_year}:{end_year}",
    }

    log.info("fetching   %-18s %d countries %d-%d", indicator_id, len(countries), start_year, end_year)
    records = _unwrap(_request_json(url, params), indicator_id)

    path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    log.info("saved raw  %-18s %d records -> %s", indicator_id, len(records), path.name)
    return records


def records_to_frame(records: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """Flatten nested API records into flat columns.

    The API nests country and indicator as sub-objects; ``json_normalize``
    would give us dotted names, so the fields we actually use are picked
    out explicitly instead.
    """
    rows = [
        {
            "country_code": rec.get("countryiso3code") or "",
            "country": (rec.get("country") or {}).get("value", ""),
            "indicator_id": (rec.get("indicator") or {}).get("id", ""),
            "year": rec.get("date"),
            "value": rec.get("value"),
        }
        for rec in records
    ]

    frame = pd.DataFrame(rows, columns=["country_code", "country", "indicator_id", "year", "value"])
    if frame.empty:
        return frame

    frame["year"] = pd.to_numeric(frame["year"], errors="coerce").astype("Int64")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    return frame


def fetch_all(
    indicators: dict[str, dict[str, object]] | None = None,
    countries: Sequence[str] | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Fetch every configured indicator and stack them into one long frame.

    Columns: ``country_code, country, indicator_id, indicator, year, value``.
    """
    indicators = indicators or config.INDICATORS
    frames: list[pd.DataFrame] = []

    for indicator_id, meta in indicators.items():
        records = fetch_indicator(indicator_id, countries, start_year, end_year, use_cache)
        frame = records_to_frame(records)
        if frame.empty:
            log.warning("no rows returned for %s — skipping", indicator_id)
            continue
        frame["indicator"] = meta["name"]
        frames.append(frame)

    if not frames:
        raise FetchError("every indicator came back empty — nothing to build a pipeline on")

    combined = pd.concat(frames, ignore_index=True)
    combined = combined[["country_code", "country", "indicator_id", "indicator", "year", "value"]]

    out = config.RAW_DIR / "combined_raw.csv"
    combined.to_csv(out, index=False)
    log.info("combined   %d rows across %d indicators -> %s", len(combined), len(frames), out.name)
    return combined


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    df = fetch_all()
    print(df.head(10).to_string(index=False))
    print(f"\n{len(df):,} raw rows | {df['country_code'].nunique()} countries "
          f"| {df['indicator'].nunique()} indicators")
