"""Chart theme — one palette, two modes, applied to Matplotlib.

Colors are assigned by *role* (accent, baseline, context, ink, grid) rather
than picked per chart, so every figure in ``figures/`` reads as one system.

The categorical hues are slots 1 and 2 of a colorblind-safe order; the pair
was checked with a CVD validator against both surfaces before use:

    light  blue #2a78d6 / orange #eb6834 — worst-pair CVD dE 24.7, all checks pass
    dark   blue #3987e5 / orange #d95926 — worst-pair CVD dE 26.8, all checks pass

Every chart that leans on color also carries a second channel (direct
labels, dashes, or a legend), so identity is never colour-alone.
"""

from __future__ import annotations

from dataclasses import dataclass

import matplotlib as mpl
from matplotlib.colors import LinearSegmentedColormap


@dataclass(frozen=True)
class Theme:
    """Every colour a figure is allowed to use, named by its job."""

    name: str
    surface: str        # chart background
    page: str           # figure background
    ink: str            # titles, hero numbers
    ink_secondary: str  # subtitles, annotations
    ink_muted: str      # tick labels, axis text
    grid: str           # hairline gridlines
    axis: str           # baseline / spines
    accent: str         # categorical slot 1 — the focus series
    accent_2: str       # categorical slot 2 — the baseline series
    context: str        # de-emphasised background series
    positive: str       # diverging pole: change in the good direction
    negative: str       # diverging pole: change in the bad direction
    neutral: str        # diverging midpoint


LIGHT = Theme(
    name="light",
    surface="#fcfcfb",
    page="#f9f9f7",
    ink="#0b0b0b",
    ink_secondary="#52514e",
    ink_muted="#898781",
    grid="#e1e0d9",
    axis="#c3c2b7",
    accent="#2a78d6",
    accent_2="#eb6834",
    context="#c3c2b7",
    positive="#2a78d6",
    negative="#e34948",
    neutral="#f0efec",
)

DARK = Theme(
    name="dark",
    surface="#1a1a19",
    page="#0d0d0d",
    ink="#ffffff",
    ink_secondary="#c3c2b7",
    ink_muted="#898781",
    grid="#2c2c2a",
    axis="#383835",
    accent="#3987e5",
    accent_2="#d95926",
    context="#52514e",
    positive="#3987e5",
    negative="#e66767",
    neutral="#383835",
)

THEMES: dict[str, Theme] = {"light": LIGHT, "dark": DARK}

FONT_STACK = ["Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"]


def apply(theme: Theme) -> None:
    """Push the theme into Matplotlib's rcParams.

    Chrome is deliberately recessive: hairline horizontal grid only, no top
    or right spines, muted tick text, no tick marks.
    """
    mpl.rcParams.update(
        {
            "figure.facecolor": theme.page,
            "figure.dpi": 130,
            "savefig.dpi": 200,
            "savefig.facecolor": theme.page,
            "savefig.bbox": "tight",
            "axes.facecolor": theme.surface,
            "axes.edgecolor": theme.axis,
            "axes.linewidth": 1.0,
            "axes.grid": True,
            "axes.grid.axis": "y",
            "axes.labelcolor": theme.ink_secondary,
            "axes.labelsize": 10,
            "axes.titlesize": 13,
            "axes.titlecolor": theme.ink,
            "axes.titleweight": "semibold",
            "axes.titlelocation": "left",
            "axes.titlepad": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": theme.grid,
            "grid.linewidth": 0.8,
            "text.color": theme.ink,
            "xtick.color": theme.ink_muted,
            "ytick.color": theme.ink_muted,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "xtick.major.size": 0,
            "ytick.major.size": 0,
            "lines.linewidth": 2.0,
            "lines.markersize": 5,
            "lines.solid_capstyle": "round",
            "legend.frameon": False,
            "legend.fontsize": 9,
            "legend.labelcolor": theme.ink_secondary,
            "font.family": "sans-serif",
            "font.sans-serif": FONT_STACK,
            "font.size": 10,
        }
    )


def diverging_cmap(theme: Theme) -> LinearSegmentedColormap:
    """Two poles through a neutral midpoint — never a rainbow."""
    return LinearSegmentedColormap.from_list(
        "datapulse_diverging",
        [theme.negative, theme.neutral, theme.positive],
        N=256,
    )
