"""Optional step — export.

Turns the analysis tables into a Markdown report with the figures embedded,
so a pipeline run ends with something a human can read rather than a folder
of CSVs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import config

log = logging.getLogger(__name__)

# Captions keyed by the numeric prefix each figure filename carries.
FIGURE_CAPTIONS = {
    "01": "Focus country against its peers",
    "02": "Every indicator for the focus country",
    "03": "Health spending vs life expectancy",
    "04": "Change across the whole period",
    "05": "How the indicators move together",
}

NOMINAL_CAVEAT = (
    "current US$, so the movement mixes real spending growth with inflation "
    "and exchange-rate changes"
)


def _verdict(indicator: str, improved: object) -> str:
    """Plain-language direction, softened for nominal money series.

    ``improved`` arrives as a Python bool, a NumPy bool or None depending on
    the column's dtype, so it is tested for truthiness rather than identity.
    """
    if improved is None or pd.isna(improved):
        return "unchanged"
    if config.is_nominal(indicator):
        return "a nominal rise" if improved else "a nominal fall"
    return "an improvement" if improved else "a decline"


def _change_phrase(row: pd.Series) -> str:
    """``+5.4 years (+7.5%) — an improvement``."""
    indicator = str(row["indicator"])
    unit = config.unit_for(indicator)
    phrase = f"{row['abs_change']:+,.1f} {unit} ({row['pct_change']:+.1f}%) — {_verdict(indicator, row['improved'])}"
    return phrase + (f" *({NOMINAL_CAVEAT})*" if config.is_nominal(indicator) else "")


def _headline_insights(tables: dict[str, pd.DataFrame], focus: str) -> list[str]:
    """The handful of sentences worth putting at the top of the report."""
    trends = tables["trend_summary"]
    lines: list[str] = []

    for row in trends[trends["country_code"] == focus].itertuples():
        caveat = f" ({NOMINAL_CAVEAT})" if config.is_nominal(row.indicator) else ""
        lines.append(
            f"**{config.label_for(row.indicator)}** in {row.country} moved from "
            f"{row.first_value:,.1f} ({row.first_year}) to {row.last_value:,.1f} ({row.last_year}) — "
            f"{row.abs_change:+,.1f} {config.unit_for(row.indicator)}, "
            f"{_verdict(row.indicator, row.improved)}{caveat}."
        )

    corr = tables.get("indicator_correlation")
    if corr is not None and not corr.empty:
        off_diagonal = corr.where(corr.abs() < 1.0).stack()
        if not off_diagonal.empty:
            a, b = off_diagonal.abs().idxmax()
            lines.append(
                f"The strongest relationship in the panel is **{config.label_for(a)}** vs "
                f"**{config.label_for(b)}** (r = {corr.loc[a, b]:.2f} across all country-years)."
            )

    return lines


def _life_expectancy_table(trends: pd.DataFrame) -> list[str]:
    """Ranked country table with each row's own endpoint years shown.

    Endpoints differ per country, so the years belong in the cells rather
    than in a single column header that would silently misstate them.
    """
    subset = trends[trends["indicator"] == "life_expectancy"].sort_values(
        "abs_change", ascending=False
    )

    rows = [
        "| Country | First observation | Latest observation | Change (years) | CAGR |",
        "| --- | --- | --- | --- | --- |",
    ]
    rows += [
        f"| {row.country} | {row.first_value:,.2f} ({row.first_year}) "
        f"| {row.last_value:,.2f} ({row.last_year}) | {row.abs_change:+,.2f} "
        f"| {row.cagr * 100:+.2f}%/yr |"
        for row in subset.itertuples()
    ]
    return rows


def build_report(
    tidy: pd.DataFrame,
    tables: dict[str, pd.DataFrame],
    figures: list[Path],
    focus: str | None = None,
) -> Path:
    """Write ``reports/insights.md`` and return its path."""
    focus = focus or config.FOCUS_COUNTRY
    trends = tables["trend_summary"]
    focus_name = tidy.loc[tidy["country_code"] == focus, "country"].iloc[0]
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    parts: list[str] = [
        "# DataPulse — health indicator insights",
        "",
        f"*Generated {generated} · {config.START_YEAR}–{config.END_YEAR} · "
        f"{tidy['country_code'].nunique()} countries · {tidy['indicator'].nunique()} indicators · "
        f"focus: {focus_name}*",
        "",
        "## Headline insights",
        "",
    ]
    parts += [f"- {line}" for line in _headline_insights(tables, focus)]

    parts += ["", "## Life expectancy by country", ""]
    parts += _life_expectancy_table(trends)

    parts += ["", f"## Every indicator for {focus_name}", "", "| Indicator | First | Latest | Change |", "| --- | --- | --- | --- |"]
    parts += [
        f"| {config.label_for(row.indicator)} | {row.first_value:,.1f} ({row.first_year}) "
        f"| {row.last_value:,.1f} ({row.last_year}) | {_change_phrase(pd.Series(row._asdict()))} |"
        for row in trends[trends["country_code"] == focus].itertuples()
    ]

    parts += ["", "## Figures", ""]
    for path in figures:
        relative = path.relative_to(config.PROJECT_ROOT).as_posix()
        caption = FIGURE_CAPTIONS.get(path.stem.split("_")[0], path.stem.replace("_", " "))
        parts += [f"### {caption}", "", f"![{caption}](../{relative})", ""]

    parts += [
        "## Method",
        "",
        "1. **Collect** — one World Bank endpoint per indicator, cached as raw JSON in `data/raw/`.",
        "2. **Clean** — structural filtering, de-duplication, an explicit country x year x indicator "
        f"grid, series below {config.MIN_COVERAGE:.0%} coverage dropped, gaps of at most "
        f"{config.MAX_INTERPOLATION_GAP} years interpolated and flagged in `is_imputed`.",
        "3. **Analyse** — trends, year-over-year change, rankings, gap to the world baseline, "
        "indicator correlations.",
        "4. **Visualise** — five figures rendered in light and dark themes.",
        "",
        "### Reading the numbers",
        "",
        "- Endpoints are each series' own first and last **observed** year, which is why the "
        "years differ between rows.",
        "- Health expenditure is reported in **current US$** — nominal, so it is not comparable "
        "across years in real terms.",
        "- Interpolated points are marked in the data (`is_imputed`) and drawn hollow in the charts.",
        "",
        "*Source: World Bank Open Data — https://data.worldbank.org*",
        "",
    ]

    path = config.REPORTS_DIR / "insights.md"
    path.write_text("\n".join(parts), encoding="utf-8")
    log.info("report      %s", path.relative_to(config.PROJECT_ROOT))
    return path
