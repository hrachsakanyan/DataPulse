"""Tests for the parts of the pipeline that make judgement calls.

Everything here runs on synthetic frames — no network, no cached files — so
the suite is fast and deterministic.

    pytest
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import analyze, clean, fetch


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def make_raw(values: dict[str, list[float | None]], years: range = range(2000, 2006)) -> pd.DataFrame:
    """Build a raw-shaped frame: one entry per country, values by year."""
    rows = [
        {
            "country_code": code,
            "country": f"Country {code}",
            "indicator_id": "TEST.IND",
            "indicator": "life_expectancy",
            "year": year,
            "value": value,
        }
        for code, series in values.items()
        for year, value in zip(years, series)
    ]
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------


def test_records_to_frame_flattens_nested_payload():
    records = [
        {
            "countryiso3code": "ARM",
            "country": {"id": "AM", "value": "Armenia"},
            "indicator": {"id": "SP.DYN.LE00.IN", "value": "Life expectancy"},
            "date": "2020",
            "value": 73.4,
        }
    ]

    frame = fetch.records_to_frame(records)

    assert frame.loc[0, "country_code"] == "ARM"
    assert frame.loc[0, "country"] == "Armenia"
    assert frame.loc[0, "year"] == 2020
    assert frame.loc[0, "value"] == pytest.approx(73.4)


def test_records_to_frame_handles_empty_input():
    assert fetch.records_to_frame([]).empty


def test_unwrap_raises_on_api_error_body():
    """The API answers 200 with the error inside the body — catch that."""
    payload = [{"message": [{"id": "120", "value": "Invalid value"}]}]

    with pytest.raises(fetch.FetchError, match="rejected"):
        fetch._unwrap(payload, "BAD.CODE")


# --------------------------------------------------------------------------
# clean
# --------------------------------------------------------------------------


def test_structural_filter_drops_aggregate_rows():
    raw = make_raw({"ARM": [70, 71, 72, 73, 74, 75]})
    raw.loc[len(raw)] = {
        "country_code": "",  # regional aggregate
        "country": "Europe & Central Asia",
        "indicator_id": "TEST.IND",
        "indicator": "life_expectancy",
        "year": 2000,
        "value": 76.0,
    }

    result = clean.drop_structural_junk(raw, 2000, 2005)

    assert set(result["country_code"]) == {"ARM"}


def test_negative_values_become_missing():
    raw = make_raw({"ARM": [70, -5, 72, 73, 74, 75]})

    result = clean.null_impossible_values(raw)

    assert result["value"].isna().sum() == 1
    assert result["value"].min() == 70


def test_sparse_series_are_dropped():
    """A series covering under half the period cannot carry a trend."""
    raw = make_raw({
        "ARM": [70, 71, 72, 73, 74, 75],
        "GEO": [70, None, None, None, None, 75],  # 33% coverage
    })

    tidy = clean.clean(raw, start_year=2000, end_year=2005)

    assert set(tidy["country_code"]) == {"ARM"}


def test_short_gaps_are_interpolated_and_flagged():
    raw = make_raw({"ARM": [70, None, 72, 73, 74, 75]})

    tidy = clean.clean(raw, start_year=2000, end_year=2005)
    filled = tidy[tidy["year"] == 2001].iloc[0]

    assert filled["value"] == pytest.approx(71.0)
    assert bool(filled["is_imputed"]) is True
    assert tidy[tidy["year"] != 2001]["is_imputed"].sum() == 0


def test_long_gaps_are_left_missing():
    """Three consecutive holes exceed the two-year limit."""
    raw = make_raw({"ARM": [70, None, None, None, 74, 75]})

    tidy = clean.clean(raw, start_year=2000, end_year=2005, min_coverage=0.4, max_gap=2)

    assert tidy[tidy["year"].between(2001, 2003)]["value"].isna().all()


def test_edges_are_never_extrapolated():
    raw = make_raw({"ARM": [None, 71, 72, 73, 74, None]})

    tidy = clean.clean(raw, start_year=2000, end_year=2005)

    assert pd.isna(tidy[tidy["year"] == 2000]["value"].iloc[0])
    assert pd.isna(tidy[tidy["year"] == 2005]["value"].iloc[0])


def test_duplicates_collapse_to_one_row_per_key():
    raw = make_raw({"ARM": [70, 71, 72, 73, 74, 75]})
    raw = pd.concat([raw, raw.iloc[[0]]], ignore_index=True)

    result = clean.deduplicate(raw)

    assert len(result) == 6


def test_missing_years_become_explicit_rows():
    raw = make_raw({"ARM": [70, 71, 72]}, years=range(2000, 2003))

    result = clean.complete_grid(raw, 2000, 2005)

    assert len(result) == 6
    assert result[result["year"] > 2002]["value"].isna().all()


def test_to_wide_gives_one_row_per_country_year():
    raw = pd.concat([
        make_raw({"ARM": [70, 71, 72, 73, 74, 75]}),
        make_raw({"ARM": [20, 19, 18, 17, 16, 15]}).assign(indicator="infant_mortality"),
    ], ignore_index=True)

    wide = clean.to_wide(clean.clean(raw, start_year=2000, end_year=2005))

    assert len(wide) == 6
    assert {"life_expectancy", "infant_mortality"} <= set(wide.columns)


# --------------------------------------------------------------------------
# analyze
# --------------------------------------------------------------------------


def test_trend_summary_uses_observed_endpoints():
    """A series that stops early is measured over its own span."""
    raw = make_raw({"ARM": [70, 71, 72, 73, None, None]})

    tidy = clean.clean(raw, start_year=2000, end_year=2005)
    row = analyze.trend_summary(tidy).iloc[0]

    assert row["first_year"] == 2000
    assert row["last_year"] == 2003
    assert row["abs_change"] == pytest.approx(3.0)


def test_improvement_direction_respects_indicator_polarity():
    """Falling infant mortality is an improvement; falling life expectancy is not."""
    falling = make_raw({"ARM": [20, 19, 18, 17, 16, 15]})

    mortality = analyze.trend_summary(
        clean.clean(falling.assign(indicator="infant_mortality"), 2000, 2005)
    ).iloc[0]
    life = analyze.trend_summary(clean.clean(falling, 2000, 2005)).iloc[0]

    assert bool(mortality["improved"]) is True
    assert bool(life["improved"]) is False


def test_slope_matches_a_known_linear_series():
    raw = make_raw({"ARM": [70, 72, 74, 76, 78, 80]})

    row = analyze.trend_summary(clean.clean(raw, 2000, 2005)).iloc[0]

    assert row["slope_per_year"] == pytest.approx(2.0)


def test_cagr_is_undefined_for_non_positive_endpoints():
    assert np.isnan(analyze._cagr(0.0, 10.0, 5))
    assert analyze._cagr(100.0, 200.0, 10) == pytest.approx(0.0717735, rel=1e-4)


def test_rank_latest_orders_by_polarity():
    """Lower infant mortality ranks first; higher life expectancy ranks first."""
    raw = pd.concat([
        make_raw({"ARM": [20, 19, 18, 17, 16, 15], "GEO": [10, 9, 8, 7, 6, 5]}).assign(
            indicator="infant_mortality"
        ),
        make_raw({"ARM": [70, 71, 72, 73, 74, 75], "GEO": [60, 61, 62, 63, 64, 65]}),
    ], ignore_index=True)

    tidy = clean.clean(raw, start_year=2000, end_year=2005)

    assert analyze.rank_latest(tidy, "infant_mortality").iloc[0]["country_code"] == "GEO"
    assert analyze.rank_latest(tidy, "life_expectancy").iloc[0]["country_code"] == "ARM"


def test_year_over_year_change_is_the_annual_difference():
    raw = make_raw({"ARM": [70, 72, 74, 76, 78, 80]})

    yoy = analyze.year_over_year(clean.clean(raw, 2000, 2005))

    assert pd.isna(yoy.iloc[0]["yoy_change"])
    assert yoy.iloc[1]["yoy_change"] == pytest.approx(2.0)
