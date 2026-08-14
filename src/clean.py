"""Step 2 of the pipeline — clean.

Turns the raw API dump into a trustworthy tidy dataset:

1. drop structurally broken rows (no ISO-3 code, no year, out of range)
2. de-duplicate on (country, indicator, year)
3. null out impossible values (these indicators are never negative)
4. expand to a complete country x indicator x year grid so gaps are explicit
5. drop series too sparse to describe a trend
6. interpolate short gaps only, and mark every imputed point
7. pivot to a wide analysis table

Run standalone::

    python -m src.clean
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import config

log = logging.getLogger(__name__)

KEY_COLUMNS = ["country_code", "indicator", "year"]


# --------------------------------------------------------------------------
# Individual cleaning steps
# --------------------------------------------------------------------------


def drop_structural_junk(df: pd.DataFrame, start_year: int, end_year: int) -> pd.DataFrame:
    """Remove rows that cannot be placed on the country/year grid."""
    before = len(df)

    df = df[df["country_code"].notna()]
    # Regional aggregates come back with an empty ISO-3 code; a real country
    # code is always three letters.
    df = df[df["country_code"].astype(str).str.fullmatch(r"[A-Z]{3}")]
    df = df.dropna(subset=["year"])
    df = df.astype({"year": int})
    df = df[df["year"].between(start_year, end_year)]

    log.info("structural  dropped %d of %d rows", before - len(df), before)
    return df


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """Keep one observation per country/indicator/year — the last revision."""
    before = len(df)
    df = df.sort_values(KEY_COLUMNS).drop_duplicates(subset=KEY_COLUMNS, keep="last")
    dropped = before - len(df)
    if dropped:
        log.info("duplicates  dropped %d rows", dropped)
    return df


def null_impossible_values(df: pd.DataFrame) -> pd.DataFrame:
    """Negative rates, expenditures and life expectancies are data errors."""
    df = df.copy()
    bad = df["value"] < 0
    if bad.any():
        log.info("validation  nulled %d negative values", int(bad.sum()))
        df.loc[bad, "value"] = np.nan
    return df


def complete_grid(df: pd.DataFrame, start_year: int, end_year: int) -> pd.DataFrame:
    """Make every missing (country, indicator, year) an explicit NaN row.

    Without this a series that simply stops reporting looks identical to one
    that ends at the last requested year.
    """
    keys = (
        df.groupby(["country_code", "indicator"], as_index=False)
        .agg(country=("country", "first"), indicator_id=("indicator_id", "first"))
    )
    years = pd.DataFrame({"year": range(start_year, end_year + 1)})

    grid = keys.merge(years, how="cross")
    filled = grid.merge(df[KEY_COLUMNS + ["value"]], on=KEY_COLUMNS, how="left")

    added = len(filled) - len(df)
    if added:
        log.info("grid        added %d explicit-gap rows", added)
    return filled


def coverage(df: pd.DataFrame) -> pd.DataFrame:
    """Share of years carrying a real value, per country/indicator series."""
    return (
        df.groupby(["country_code", "indicator"])["value"]
        .agg(observed="count", years="size")
        .assign(coverage=lambda x: x["observed"] / x["years"])
        .reset_index()
        .sort_values("coverage")
    )


def drop_sparse_series(df: pd.DataFrame, min_coverage: float) -> pd.DataFrame:
    """Discard series with too few observations to trend honestly."""
    cov = coverage(df)
    sparse = cov[cov["coverage"] < min_coverage]

    if sparse.empty:
        return df

    for row in sparse.itertuples():
        log.info(
            "sparse      dropped %s/%s (%.0f%% coverage)",
            row.country_code,
            row.indicator,
            row.coverage * 100,
        )

    keys = set(zip(sparse["country_code"], sparse["indicator"]))
    mask = [key not in keys for key in zip(df["country_code"], df["indicator"])]
    return df[mask]


def _fill_short_runs(series: pd.Series, max_gap: int) -> pd.Series:
    """Interpolate only those NaN runs no longer than ``max_gap``.

    ``interpolate(limit=n)`` is not this: it fills the first *n* values of
    every run, so a five-year hole would come back two-thirds invented.
    Runs are measured first, then long ones are put back to NaN.

    ``limit_area="inside"`` keeps the fill strictly between real
    observations, so a series is never extrapolated past its own edges.
    """
    missing = series.isna()
    run_id = (missing != missing.shift()).cumsum()
    run_length = missing.groupby(run_id).transform("size")

    interpolated = series.interpolate(method="linear", limit_area="inside")
    return interpolated.mask(missing & (run_length > max_gap))


def interpolate_short_gaps(df: pd.DataFrame, max_gap: int) -> pd.DataFrame:
    """Linearly fill runs of at most ``max_gap`` missing years."""
    df = df.sort_values(["country_code", "indicator", "year"]).copy()
    df["is_imputed"] = False

    original_missing = df["value"].isna()
    df["value"] = df.groupby(["country_code", "indicator"])["value"].transform(
        _fill_short_runs, max_gap=max_gap
    )

    df["is_imputed"] = original_missing & df["value"].notna()
    filled = int(df["is_imputed"].sum())
    if filled:
        log.info("interpolate filled %d gaps of <=%d years", filled, max_gap)
    return df


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def clean(
    raw: pd.DataFrame,
    start_year: int | None = None,
    end_year: int | None = None,
    min_coverage: float | None = None,
    max_gap: int | None = None,
) -> pd.DataFrame:
    """Run the full cleaning chain and return the tidy long frame."""
    start_year = start_year or config.START_YEAR
    end_year = end_year or config.END_YEAR
    min_coverage = config.MIN_COVERAGE if min_coverage is None else min_coverage
    max_gap = config.MAX_INTERPOLATION_GAP if max_gap is None else max_gap

    tidy = (
        raw.pipe(drop_structural_junk, start_year, end_year)
        .pipe(deduplicate)
        .pipe(null_impossible_values)
        .pipe(complete_grid, start_year, end_year)
        .pipe(drop_sparse_series, min_coverage)
        .pipe(interpolate_short_gaps, max_gap)
    )

    tidy = tidy[
        ["country_code", "country", "indicator_id", "indicator", "year", "value", "is_imputed"]
    ].reset_index(drop=True)

    log.info(
        "clean       %d rows | %d countries | %d indicators | %.1f%% populated",
        len(tidy),
        tidy["country_code"].nunique(),
        tidy["indicator"].nunique(),
        tidy["value"].notna().mean() * 100,
    )
    return tidy


def to_wide(tidy: pd.DataFrame) -> pd.DataFrame:
    """One row per country-year, one column per indicator."""
    wide = (
        tidy.pivot_table(
            index=["country_code", "country", "year"],
            columns="indicator",
            values="value",
        )
        .reset_index()
        .rename_axis(columns=None)
    )

    ordered = [c for c in config.INDICATOR_NAMES if c in wide.columns]
    return wide[["country_code", "country", "year", *ordered]].sort_values(
        ["country_code", "year"], ignore_index=True
    )


def save(tidy: pd.DataFrame, wide: pd.DataFrame) -> None:
    tidy.to_csv(config.PROCESSED_DIR / "clean_long.csv", index=False)
    wide.to_csv(config.PROCESSED_DIR / "clean_wide.csv", index=False)
    log.info("saved       clean_long.csv + clean_wide.csv -> %s", config.PROCESSED_DIR)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    from .fetch import fetch_all

    tidy_df = clean(fetch_all())
    wide_df = to_wide(tidy_df)
    save(tidy_df, wide_df)
    print(wide_df.head(12).to_string(index=False))
