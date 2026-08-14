"""Step 4 of the pipeline — visualise.

Five figures, one visual system. Each function takes prepared tables plus a
theme and writes a PNG into ``figures/`` (``figures/dark/`` for dark mode).

Design rules held throughout:
  * the form follows the data's job — emphasis for "one series is the point",
    small multiples for "same country, different units", diverging for polarity
  * colour is assigned by role from a CVD-validated pair (see ``theme.py``)
  * identity never rests on colour alone — direct labels, dashes and legends
  * chrome recedes: hairline horizontal grid, no top/right spines, muted ticks

Run standalone::

    python -m src.visualize
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: figures are files, never windows

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import analyze, config, theme as theme_module
from .theme import Theme

log = logging.getLogger(__name__)

SOURCE_NOTE = "Source: World Bank Open Data (api.worldbank.org)"


# --------------------------------------------------------------------------
# Shared chart furniture
# --------------------------------------------------------------------------


def _output_dir(theme: Theme) -> Path:
    directory = config.FIGURES_DIR if theme.name == "light" else config.FIGURES_DIR / "dark"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _titles(ax, title: str, subtitle: str, theme: Theme) -> None:
    """Bold title with a plain-language subtitle underneath it.

    Both offsets are in points, not axes fractions — a fraction would scale
    with figure height and collide on the taller charts.
    """
    ax.set_title(title, pad=30)
    ax.annotate(
        subtitle,
        xy=(0, 1),
        xycoords="axes fraction",
        xytext=(0, 10),
        textcoords="offset points",
        fontsize=10,
        color=theme.ink_secondary,
        ha="left",
        va="bottom",
        annotation_clip=False,
    )


def _clamp_to_zero(ax, values: pd.Series) -> None:
    """Keep a non-negative quantity off a negative axis.

    Padding a series that bottoms out near zero otherwise produces a
    y-axis reading -250 US$ of health spending.
    """
    if values.empty or values.min() < 0:
        return
    low, high = ax.get_ylim()
    if low < 0:
        ax.set_ylim(0, high)


def _footnote(fig, theme: Theme, extra: str = "") -> None:
    note = f"{SOURCE_NOTE}{'  ·  ' + extra if extra else ''}"
    fig.text(0.0, -0.02, note, fontsize=8, color=theme.ink_muted, ha="left", va="top")


def _save(fig, theme: Theme, filename: str) -> Path:
    path = _output_dir(theme) / filename
    fig.savefig(path)
    plt.close(fig)
    log.info("figure      %s", path.relative_to(config.PROJECT_ROOT))
    return path


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    """WCAG relative luminance from linear-corrected sRGB components."""
    channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb[:3]]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _readable_ink(fill: tuple[float, float, float], theme: Theme) -> str:
    """Whichever of the theme's two inks contrasts more with this fill.

    A fixed value threshold gets this wrong on the mid-ramp cells, where the
    same colour is light in one theme's context and dark in the other.
    """
    fill_luminance = _relative_luminance(fill)

    def contrast(hex_color: str) -> float:
        rgb = tuple(int(hex_color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
        other = _relative_luminance(rgb)
        lighter, darker = max(fill_luminance, other), min(fill_luminance, other)
        return (lighter + 0.05) / (darker + 0.05)

    return max((theme.ink, theme.surface), key=contrast)


def _declutter_labels(entries: list[tuple[float, str, str]], min_gap: float) -> list[tuple[float, str, str]]:
    """Nudge end-of-line labels apart so none overlap.

    ``entries`` are ``(y, text, colour)``. Positions are adjusted downward
    through a sorted pass, which is enough for the handful of lines a chart
    of this size carries.
    """
    ordered = sorted(entries, key=lambda item: item[0])
    adjusted: list[tuple[float, str, str]] = []
    previous = -np.inf

    for y, text, color in ordered:
        y = max(y, previous + min_gap)
        adjusted.append((y, text, color))
        previous = y

    return adjusted


# --------------------------------------------------------------------------
# 1 — emphasis line chart
# --------------------------------------------------------------------------


def plot_focus_trend(
    tidy: pd.DataFrame,
    theme: Theme,
    indicator: str = "life_expectancy",
    focus: str | None = None,
) -> Path:
    """One country against its peers over time.

    Emphasis rather than eight categorical hues: the focus country carries
    the accent, the world aggregate is the dashed baseline, every other
    country recedes to grey and is identified by a direct label.
    """
    focus = focus or config.FOCUS_COUNTRY
    data = tidy[(tidy["indicator"] == indicator) & tidy["value"].notna()]

    fig, ax = plt.subplots(figsize=(10, 5.8))
    label_entries: list[tuple[float, str, str]] = []

    for code, group in data.groupby("country_code"):
        group = group.sort_values("year")
        if code == focus:
            style = dict(color=theme.accent, linewidth=2.6, zorder=5)
        elif code == "WLD":
            style = dict(color=theme.accent_2, linewidth=2.0, linestyle=(0, (5, 3)), zorder=4)
        else:
            style = dict(color=theme.context, linewidth=1.4, zorder=2)

        ax.plot(group["year"], group["value"], **style)

        # Imputed points are drawn hollow so a filled gap never passes for
        # a reported observation.
        imputed = group[group["is_imputed"]]
        if not imputed.empty and code in (focus, "WLD"):
            ax.plot(
                imputed["year"],
                imputed["value"],
                "o",
                markersize=5,
                markerfacecolor=theme.surface,
                markeredgecolor=style["color"],
                markeredgewidth=1.4,
                linestyle="none",
                zorder=style["zorder"] + 1,
            )

        last = group.iloc[-1]
        label_entries.append((float(last["value"]), code, style["color"]))

    span = data["value"].max() - data["value"].min()
    for y, text, color in _declutter_labels(label_entries, min_gap=span * 0.045):
        weight = "semibold" if text in (focus, "WLD") else "normal"
        ax.annotate(
            text,
            xy=(data["year"].max(), y),
            xytext=(8, 0),
            textcoords="offset points",
            va="center",
            fontsize=9,
            fontweight=weight,
            color=color if text in (focus, "WLD") else theme.ink_muted,
        )

    focus_name = data.loc[data["country_code"] == focus, "country"].iloc[0]
    _titles(
        ax,
        f"{config.label_for(indicator)}, {focus_name} vs peers",
        f"{config.axis_label(indicator)} · {int(data['year'].min())}–{int(data['year'].max())}",
        theme,
    )
    ax.set_xlabel("Year")
    ax.set_ylabel(config.axis_label(indicator))
    ax.margins(x=0.02)
    ax.set_xlim(data["year"].min(), data["year"].max() + (data["year"].max() - data["year"].min()) * 0.06)

    # A legend, because more than one series is on screen; the grey entry
    # stands for the whole peer group.
    handles = [
        plt.Line2D([], [], color=theme.accent, linewidth=2.6, label=f"{focus_name} (focus)"),
        plt.Line2D([], [], color=theme.accent_2, linewidth=2.0, linestyle=(0, (5, 3)), label="World average"),
        plt.Line2D([], [], color=theme.context, linewidth=1.4, label="Other countries"),
    ]
    ax.legend(handles=handles, loc="lower right", ncol=1)
    # Only explain the hollow marker when one is actually on the chart.
    drawn_imputed = data[data["country_code"].isin([focus, "WLD"])]["is_imputed"].any()
    _footnote(fig, theme, "hollow markers = interpolated gap" if drawn_imputed else "")

    return _save(fig, theme, f"01_{indicator}_trend.png")


# --------------------------------------------------------------------------
# 2 — small multiples
# --------------------------------------------------------------------------


def plot_small_multiples(
    tidy: pd.DataFrame,
    theme: Theme,
    focus: str | None = None,
) -> Path:
    """All indicators for one country — one panel each.

    The indicators share no unit, so they get separate panels rather than a
    second y-axis: a dual-axis chart would invent a relationship between
    two arbitrary scales.
    """
    focus = focus or config.FOCUS_COUNTRY
    indicators = [name for name in config.INDICATOR_NAMES if name in set(tidy["indicator"])]

    rows = int(np.ceil(len(indicators) / 2))
    fig, axes = plt.subplots(rows, 2, figsize=(11, 3.3 * rows), sharex=True)
    axes = np.atleast_1d(axes).ravel()

    focus_name = tidy.loc[tidy["country_code"] == focus, "country"].iloc[0]

    for ax, indicator in zip(axes, indicators):
        panel = tidy[tidy["indicator"] == indicator]
        country = panel[(panel["country_code"] == focus) & panel["value"].notna()].sort_values("year")
        world = panel[(panel["country_code"] == "WLD") & panel["value"].notna()].sort_values("year")

        if not world.empty:
            ax.plot(world["year"], world["value"], color=theme.context, linewidth=1.6,
                    linestyle=(0, (5, 3)), zorder=2)
        ax.plot(country["year"], country["value"], color=theme.accent, linewidth=2.4, zorder=4)

        if not country.empty:
            last = country.iloc[-1]
            ax.plot([last["year"]], [last["value"]], "o", markersize=8, color=theme.accent, zorder=5)
            ax.annotate(
                f"{last['value']:,.1f}",
                xy=(last["year"], last["value"]),
                xytext=(-4, 10),
                textcoords="offset points",
                fontsize=9,
                fontweight="semibold",
                color=theme.ink,
                ha="right",
            )

        ax.set_title(config.label_for(indicator), fontsize=11, pad=24)
        ax.annotate(
            config.unit_for(indicator),
            xy=(0, 1), xycoords="axes fraction",
            xytext=(0, 7), textcoords="offset points",
            fontsize=8.5, color=theme.ink_muted, ha="left", va="bottom",
            annotation_clip=False,
        )
        ax.margins(y=0.22)
        _clamp_to_zero(ax, panel["value"].dropna())

    for ax in axes[len(indicators):]:
        ax.set_visible(False)

    handles = [
        plt.Line2D([], [], color=theme.accent, linewidth=2.4, label=focus_name),
        plt.Line2D([], [], color=theme.context, linewidth=1.6, linestyle=(0, (5, 3)), label="World average"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=2, bbox_to_anchor=(0.5, -0.03))

    fig.suptitle(
        f"{focus_name}: every indicator, {config.START_YEAR}–{config.END_YEAR}",
        x=0.02, y=1.0, ha="left", fontsize=14, fontweight="semibold", color=theme.ink,
    )
    fig.tight_layout(rect=(0, 0.02, 1, 0.97))
    _footnote(fig, theme)

    return _save(fig, theme, f"02_{focus.lower()}_indicator_panels.png")


# --------------------------------------------------------------------------
# 3 — scatter
# --------------------------------------------------------------------------


def _common_latest_year(wide: pd.DataFrame, columns: list[str], min_share: float = 0.6) -> int | None:
    """Newest year where most countries report every requested column."""
    subset = wide.dropna(subset=columns)
    if subset.empty:
        return None
    counts = subset.groupby("year")["country_code"].nunique()
    needed = max(2, int(np.ceil(wide["country_code"].nunique() * min_share)))
    eligible = counts[counts >= needed]
    return int(eligible.index.max()) if not eligible.empty else int(counts.index.max())


def plot_spend_vs_outcome(
    wide: pd.DataFrame,
    theme: Theme,
    x: str = "health_expenditure_pc",
    y: str = "life_expectancy",
    focus: str | None = None,
) -> Path:
    """Does spending more per person buy more years of life?

    Every point is direct-labelled, so the two colours carry emphasis only —
    which keeps this all-pairs form well inside the safe series count.
    """
    focus = focus or config.FOCUS_COUNTRY
    year = _common_latest_year(wide, [x, y])
    if year is None:
        raise ValueError(f"no year has both {x} and {y}")

    snapshot = wide[(wide["year"] == year)].dropna(subset=[x, y])

    fig, ax = plt.subplots(figsize=(9, 6))

    is_focus = snapshot["country_code"] == focus
    ax.scatter(
        snapshot.loc[~is_focus, x], snapshot.loc[~is_focus, y],
        s=110, color=theme.context, edgecolor=theme.surface, linewidth=1.5, zorder=3,
    )
    ax.scatter(
        snapshot.loc[is_focus, x], snapshot.loc[is_focus, y],
        s=190, color=theme.accent, edgecolor=theme.surface, linewidth=1.8, zorder=5,
    )

    for row in snapshot.itertuples():
        focused = row.country_code == focus
        ax.annotate(
            row.country_code,
            xy=(getattr(row, x), getattr(row, y)),
            xytext=(0, 13 if focused else 11),
            textcoords="offset points",
            ha="center",
            fontsize=9.5 if focused else 9,
            fontweight="semibold" if focused else "normal",
            color=theme.accent if focused else theme.ink_secondary,
        )

    # Trend line fitted in log space, because spending spans two orders of
    # magnitude and a linear fit would be dominated by the richest country.
    if len(snapshot) >= 3:
        log_x = np.log10(snapshot[x].astype(float))
        slope, intercept = np.polyfit(log_x, snapshot[y].astype(float), 1)
        grid = np.linspace(log_x.min(), log_x.max(), 100)
        ax.plot(10**grid, slope * grid + intercept, color=theme.ink_muted,
                linewidth=1.4, linestyle=(0, (4, 3)), zorder=2)
        correlation = float(np.corrcoef(log_x, snapshot[y].astype(float))[0, 1])
        ax.annotate(
            f"log-linear fit · r = {correlation:.2f}",
            xy=(0.98, 0.06), xycoords="axes fraction", ha="right",
            fontsize=9, color=theme.ink_muted,
        )

    ax.set_xscale("log")
    ax.set_xlabel(f"{config.axis_label(x)} — log scale")
    ax.set_ylabel(config.axis_label(y))
    _titles(
        ax,
        "Health spending vs life expectancy",
        f"One point per country · {year} · World aggregate (WLD) included for scale",
        theme,
    )
    ax.margins(0.12)
    _footnote(fig, theme)

    return _save(fig, theme, "03_spend_vs_life_expectancy.png")


# --------------------------------------------------------------------------
# 4 — diverging change bars
# --------------------------------------------------------------------------


def plot_change_ranking(
    trends: pd.DataFrame,
    theme: Theme,
    indicator: str = "life_expectancy",
    focus: str | None = None,
) -> Path:
    """How far each country moved over the whole period.

    Sign is the story, so the colour job is polarity: the diverging pair
    marks improvement against decline, and every bar carries its own value
    label so the reading never depends on hue.
    """
    focus = focus or config.FOCUS_COUNTRY
    subset = trends[trends["indicator"] == indicator].copy()
    if subset.empty:
        raise ValueError(f"no trend rows for {indicator}")

    subset = subset.sort_values("abs_change")
    colors = [theme.positive if bool(flag) else theme.negative for flag in subset["improved"].fillna(True)]

    fig, ax = plt.subplots(figsize=(9, 0.42 * len(subset) + 2.6))
    positions = np.arange(len(subset))

    ax.barh(
        positions, subset["abs_change"], height=0.5,
        color=colors, edgecolor=theme.surface, linewidth=2,
        zorder=3,
    )

    ax.set_yticks(positions, subset["country"])
    # The focus country is picked out by weight, not by a third colour —
    # colour here already carries the improved/worsened split.
    for tick, code in zip(ax.get_yticklabels(), subset["country_code"]):
        if code == focus:
            tick.set_fontweight("semibold")
            tick.set_color(theme.ink)
    ax.axvline(0, color=theme.axis, linewidth=1.2, zorder=4)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True)

    span = max(abs(subset["abs_change"].min()), abs(subset["abs_change"].max())) or 1
    for position, value in zip(positions, subset["abs_change"]):
        offset = span * 0.02
        ax.annotate(
            f"{value:+.1f}",
            xy=(value + (offset if value >= 0 else -offset), position),
            va="center",
            ha="left" if value >= 0 else "right",
            fontsize=9,
            fontweight="semibold",
            color=theme.ink_secondary,
        )

    first_year = int(subset["first_year"].min())
    last_year = int(subset["last_year"].max())
    _titles(
        ax,
        f"Change in {config.label_for(indicator).lower()}, {first_year}–{last_year}",
        f"Difference between each country's first and last observation ({config.unit_for(indicator)})",
        theme,
    )
    ax.set_xlabel(f"Change ({config.unit_for(indicator)})")
    ax.margins(x=0.16)
    _footnote(fig, theme)

    return _save(fig, theme, f"04_{indicator}_change.png")


# --------------------------------------------------------------------------
# 5 — correlation heatmap
# --------------------------------------------------------------------------


def plot_correlation_heatmap(corr: pd.DataFrame, theme: Theme) -> Path:
    """How the four indicators move together across all country-years."""
    labels = [config.label_for(name) for name in corr.columns]
    values = corr.to_numpy(dtype=float)

    fig, ax = plt.subplots(figsize=(7.6, 6.4))
    mesh = ax.imshow(
        values,
        cmap=theme_module.diverging_cmap(theme),
        vmin=-1, vmax=1,
    )

    ax.set_xticks(range(len(labels)), labels, rotation=28, ha="right")
    ax.set_yticks(range(len(labels)), labels)
    ax.grid(visible=False)
    for spine in ax.spines.values():
        spine.set_visible(False)

    # 2px surface gap between cells, drawn as minor gridlines.
    ax.set_xticks(np.arange(-0.5, len(labels), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(labels), 1), minor=True)
    ax.grid(which="minor", color=theme.surface, linewidth=2, visible=True)
    ax.tick_params(which="minor", length=0)

    for (row, col), value in np.ndenumerate(values):
        # Text sits on the cell, so it takes the ink that survives that fill.
        ax.text(
            col, row, f"{value:.2f}",
            ha="center", va="center", fontsize=10,
            fontweight="semibold" if abs(value) > 0.55 else "normal",
            color=_readable_ink(mesh.cmap(mesh.norm(value)), theme),
        )

    bar = fig.colorbar(mesh, ax=ax, shrink=0.72, pad=0.03)
    bar.set_label("Pearson correlation", color=theme.ink_secondary, fontsize=9)
    bar.outline.set_visible(False)
    bar.ax.tick_params(colors=theme.ink_muted, labelsize=8.5)

    _titles(
        ax,
        "How the indicators move together",
        f"All country-years, {config.START_YEAR}–{config.END_YEAR}, world aggregate excluded",
        theme,
    )
    _footnote(fig, theme)

    return _save(fig, theme, "05_indicator_correlation.png")


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def render_all(
    tidy: pd.DataFrame,
    wide: pd.DataFrame,
    tables: dict[str, pd.DataFrame],
    theme_name: str = "light",
) -> list[Path]:
    """Render every figure in one theme and return the paths written."""
    theme = theme_module.THEMES[theme_name]
    theme_module.apply(theme)

    paths = [
        plot_focus_trend(tidy, theme),
        plot_small_multiples(tidy, theme),
        plot_spend_vs_outcome(wide, theme),
        plot_change_ranking(tables["trend_summary"], theme),
        plot_correlation_heatmap(tables["indicator_correlation"], theme),
    ]
    log.info("figures     %d rendered in %s theme", len(paths), theme_name)
    return paths


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    from .clean import clean, to_wide
    from .fetch import fetch_all

    tidy_df = clean(fetch_all())
    wide_df = to_wide(tidy_df)
    render_all(tidy_df, wide_df, analyze.analyze(tidy_df, wide_df))
