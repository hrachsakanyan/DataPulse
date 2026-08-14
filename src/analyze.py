"""Step 3 of the pipeline — analyse.

Reduces the clean panel to the handful of tables the charts and the report
are built from: trend summaries, year-over-year movement, rankings, the gap
against the world baseline, and indicator correlations.

Run standalone::

    python -m src.analyze
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import config

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _linear_slope(years: pd.Series, values: pd.Series) -> float:
    """Least-squares change per year; NaN when fewer than two real points."""
    mask = values.notna()
    if mask.sum() < 2:
        return float("nan")
    return float(np.polyfit(years[mask].astype(float), values[mask].astype(float), 1)[0])


def _cagr(first: float, last: float, years: float) -> float:
    """Compound annual growth rate; undefined for non-positive endpoints."""
    if years <= 0 or not np.isfinite(first) or not np.isfinite(last) or first <= 0 or last <= 0:
        return float("nan")
    return float((last / first) ** (1 / years) - 1)


def _is_improvement(indicator: str, change: float) -> bool | None:
    """Did the series move in the direction that counts as good?"""
    meta = config.NAME_TO_META.get(indicator)
    if meta is None or not np.isfinite(change) or change == 0:
        return None
    return change > 0 if meta["higher_is_better"] else change < 0


# --------------------------------------------------------------------------
# Core tables
# --------------------------------------------------------------------------


def latest_snapshot(tidy: pd.DataFrame) -> pd.DataFrame:
    """Most recent real observation per country and indicator."""
    observed = tidy[tidy["value"].notna()]
    idx = observed.groupby(["country_code", "indicator"])["year"].idxmax()
    return (
        observed.loc[idx, ["country_code", "country", "indicator", "year", "value"]]
        .rename(columns={"year": "latest_year", "value": "latest_value"})
        .sort_values(["indicator", "country_code"], ignore_index=True)
    )


def trend_summary(tidy: pd.DataFrame) -> pd.DataFrame:
    """One row per country/indicator describing how the series moved.

    Endpoints are the first and last *observed* years, so a series that stops
    reporting early is measured over its own span rather than the requested one.
    """
    rows: list[dict[str, object]] = []

    for (code, indicator), group in tidy.groupby(["country_code", "indicator"], sort=True):
        group = group.sort_values("year")
        observed = group[group["value"].notna()]
        if observed.empty:
            continue

        first, last = observed.iloc[0], observed.iloc[-1]
        span = float(last["year"] - first["year"])
        change = float(last["value"] - first["value"])
        pct_change = change / first["value"] * 100 if first["value"] else float("nan")

        rows.append(
            {
                "country_code": code,
                "country": group["country"].iloc[0],
                "indicator": indicator,
                "first_year": int(first["year"]),
                "first_value": float(first["value"]),
                "last_year": int(last["year"]),
                "last_value": float(last["value"]),
                "abs_change": change,
                "pct_change": pct_change,
                "cagr": _cagr(float(first["value"]), float(last["value"]), span),
                "slope_per_year": _linear_slope(group["year"], group["value"]),
                "observations": int(observed.shape[0]),
                "improved": _is_improvement(indicator, change),
            }
        )

    summary = pd.DataFrame(rows)
    log.info("trends      summarised %d country/indicator series", len(summary))
    return summary


def year_over_year(tidy: pd.DataFrame) -> pd.DataFrame:
    """Absolute and percentage change against the previous year."""
    df = tidy.sort_values(["country_code", "indicator", "year"]).copy()
    grouped = df.groupby(["country_code", "indicator"])["value"]
    df["yoy_change"] = grouped.diff()
    df["yoy_pct"] = grouped.pct_change() * 100
    return df[["country_code", "country", "indicator", "year", "value", "yoy_change", "yoy_pct"]]


def rolling_mean(tidy: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    """Add a centred rolling mean that smooths single-year reporting noise."""
    df = tidy.sort_values(["country_code", "indicator", "year"]).copy()
    df[f"rolling_{window}y"] = df.groupby(["country_code", "indicator"])["value"].transform(
        lambda s: s.rolling(window, center=True, min_periods=2).mean()
    )
    return df


def rank_latest(tidy: pd.DataFrame, indicator: str) -> pd.DataFrame:
    """Rank countries on their latest value, best first."""
    snapshot = latest_snapshot(tidy)
    subset = snapshot[snapshot["indicator"] == indicator].copy()
    if subset.empty:
        return subset

    higher_is_better = bool(config.NAME_TO_META.get(indicator, {}).get("higher_is_better", True))
    subset = subset.sort_values("latest_value", ascending=not higher_is_better, ignore_index=True)
    subset.insert(0, "rank", range(1, len(subset) + 1))
    return subset


def gap_to_baseline(tidy: pd.DataFrame, baseline: str = "WLD") -> pd.DataFrame:
    """Each country's distance from the baseline series, year by year."""
    base = (
        tidy[tidy["country_code"] == baseline]
        .set_index(["indicator", "year"])["value"]
        .rename("baseline_value")
    )
    if base.empty:
        log.warning("baseline %s not present — gap table skipped", baseline)
        return pd.DataFrame()

    df = tidy[tidy["country_code"] != baseline].join(base, on=["indicator", "year"])
    df["gap"] = df["value"] - df["baseline_value"]
    df["gap_pct"] = df["gap"] / df["baseline_value"] * 100
    return df[
        ["country_code", "country", "indicator", "year", "value", "baseline_value", "gap", "gap_pct"]
    ]


def indicator_correlation(wide: pd.DataFrame, exclude: tuple[str, ...] = ("WLD",)) -> pd.DataFrame:
    """Pairwise correlation between indicators across all country-years."""
    subset = wide[~wide["country_code"].isin(exclude)]
    columns = [c for c in config.INDICATOR_NAMES if c in subset.columns]
    return subset[columns].corr(method="pearson")


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def analyze(tidy: pd.DataFrame, wide: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Build every analysis table and persist them to ``data/processed``."""
    tables = {
        "latest_snapshot": latest_snapshot(tidy),
        "trend_summary": trend_summary(tidy),
        "year_over_year": year_over_year(tidy),
        "gap_to_baseline": gap_to_baseline(tidy),
        "indicator_correlation": indicator_correlation(wide),
    }

    for name, table in tables.items():
        if table.empty:
            continue
        include_index = name == "indicator_correlation"
        table.to_csv(config.PROCESSED_DIR / f"{name}.csv", index=include_index)

    log.info("analysis    wrote %d tables -> %s", len(tables), config.PROCESSED_DIR)
    return tables


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    from .clean import clean, to_wide
    from .fetch import fetch_all

    tidy_df = clean(fetch_all())
    wide_df = to_wide(tidy_df)
    results = analyze(tidy_df, wide_df)

    print("\n--- trend summary (life expectancy) ---")
    trends = results["trend_summary"]
    print(
        trends[trends["indicator"] == "life_expectancy"]
        .loc[:, ["country", "first_year", "first_value", "last_year", "last_value", "abs_change"]]
        .round(2)
        .to_string(index=False)
    )
