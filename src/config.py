"""Central configuration for the DataPulse pipeline.

Everything that a user might want to tweak (which countries, which
indicators, where files land, how long the cache stays fresh) lives here so
the pipeline modules stay free of magic values.
"""

from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
FIGURES_DIR = PROJECT_ROOT / "figures"
REPORTS_DIR = PROJECT_ROOT / "reports"

for _directory in (RAW_DIR, PROCESSED_DIR, FIGURES_DIR, REPORTS_DIR):
    _directory.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

# World Bank Open Data. Public, no API key, generous rate limits.
# Docs: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392
API_BASE_URL = "https://api.worldbank.org/v2"

# Max rows the API returns per request. 20 000 is the documented ceiling and
# comfortably fits every query this project makes in a single page.
PAGE_SIZE = 20_000

REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 3
RETRY_BACKOFF = 1.5  # seconds, doubled after every failed attempt

# Raw API responses are cached on disk; re-runs within this window skip the
# network entirely. Set to 0 to always hit the API.
CACHE_TTL_HOURS = 24

# --------------------------------------------------------------------------
# What we analyse
# --------------------------------------------------------------------------

# ISO-3 codes. "WLD" is the World Bank's world aggregate and acts as a
# baseline to compare individual countries against.
COUNTRIES = ["ARM", "GEO", "TUR", "POL", "DEU", "JPN", "USA", "WLD"]

# The country the narrative and the single-country figures focus on.
FOCUS_COUNTRY = "ARM"

START_YEAR = 2000
END_YEAR = 2024

# Each entry becomes one API endpoint call and one column in the tidy frame.
# `higher_is_better` drives how rankings and trend arrows are interpreted.
INDICATORS: dict[str, dict[str, object]] = {
    "SP.DYN.LE00.IN": {
        "name": "life_expectancy",
        "label": "Life expectancy at birth",
        "unit": "years",
        "higher_is_better": True,
    },
    "SH.XPD.CHEX.PC.CD": {
        "name": "health_expenditure_pc",
        "label": "Health expenditure per capita",
        "unit": "current US$",
        "higher_is_better": True,
        # "Current US$" is nominal: a rise mixes real spending growth with
        # inflation and exchange-rate moves. Flagged so the report never
        # reads a nominal jump as a health improvement.
        "nominal": True,
    },
    "SP.DYN.IMRT.IN": {
        "name": "infant_mortality",
        "label": "Infant mortality rate",
        "unit": "per 1,000 live births",
        "higher_is_better": False,
    },
    "SH.MED.PHYS.ZS": {
        "name": "physicians_per_1k",
        "label": "Physicians",
        "unit": "per 1,000 people",
        "higher_is_better": True,
    },
}

# Convenience lookups built from INDICATORS.
INDICATOR_NAMES: list[str] = [str(meta["name"]) for meta in INDICATORS.values()]
NAME_TO_META: dict[str, dict[str, object]] = {
    str(meta["name"]): meta for meta in INDICATORS.values()
}


def label_for(name: str) -> str:
    """Human-readable label for a tidy column name, e.g. ``life_expectancy``."""
    meta = NAME_TO_META.get(name)
    return str(meta["label"]) if meta else name.replace("_", " ").title()


def unit_for(name: str) -> str:
    """Unit string for a tidy column name; empty when unknown."""
    meta = NAME_TO_META.get(name)
    return str(meta["unit"]) if meta else ""


def is_nominal(name: str) -> bool:
    """True for money series quoted in current prices, not adjusted."""
    return bool(NAME_TO_META.get(name, {}).get("nominal", False))


def axis_label(name: str) -> str:
    """``"Life expectancy at birth (years)"`` — ready for a chart axis."""
    unit = unit_for(name)
    return f"{label_for(name)} ({unit})" if unit else label_for(name)


# --------------------------------------------------------------------------
# Cleaning rules
# --------------------------------------------------------------------------

# Drop a country/indicator series entirely if fewer than this share of the
# requested years carry a value — too sparse to describe a trend honestly.
MIN_COVERAGE = 0.5

# Gaps of at most this many consecutive years are interpolated; longer holes
# are left as NaN so charts break the line instead of inventing data.
MAX_INTERPOLATION_GAP = 2
