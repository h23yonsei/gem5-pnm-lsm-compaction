#!/usr/bin/env python3
"""
Draw figures/throughput-light.svg and figures/throughput-dark.svg from the committed runs: write and
read throughput of each configuration over the single-core host it is compared with.

    python tools/plot_results.py        # needs matplotlib

The figures are committed; the README shows the one that matches the reader's GitHub theme.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

from check_results import REPO, device  # noqa: E402

# (label, baseline run, compared run, series)
ROWS = [
    ("Capstone, run 1", "baseline_v3", "pnm_v3", "capstone"),
    ("Capstone, run 2", "baseline_clean", "pnm_clean", "capstone"),
    ("PNM unit, current code", "baseline_post", "pnm_post", "pnm"),
    ("Second core (control)", "baseline_post", "baseline_2core_post", "control"),
]
SERIES = {  # legend text; colors per theme below
    "pnm": "PNM unit, current code",
    "control": "Second general-purpose core (control)",
    "capstone": "PNM unit, capstone runs (see Limitations)",
}
THEMES = {
    "light": {"pnm": "#2a78d6", "control": "#eb6834", "capstone": "#898781",
              "ink": "#0b0b0b", "ink2": "#52514e", "grid": "#e1e0d9", "axis": "#c3c2b7"},
    "dark": {"pnm": "#3987e5", "control": "#d95926", "capstone": "#898781",
             "ink": "#ffffff", "ink2": "#c3c2b7", "grid": "#2c2c2a", "axis": "#383835"},
}
PANELS = [("Write throughput", "fillrandom.ops"), ("Read throughput", "readrandom.ops")]


def gains() -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for _, key in PANELS:
        out[key] = []
        for _, base, run, _ in ROWS:
            b, r = device(base)[key], device(run)[key]
            out[key].append(100.0 * (r - b) / b)
    return out


def bar(ax, y: float, value: float, height: float, color: str) -> None:
    """A horizontal bar, square at the baseline, with a 4 px rounded data end."""
    ax.barh(y, max(value - 1.2, 0), height=height, color=color, linewidth=0)
    ax.add_patch(FancyBboxPatch((max(value - 2.4, 0), y - height / 2), min(2.4, value), height,
                                boxstyle="round,pad=0,rounding_size=1.2", mutation_aspect=0.08,
                                linewidth=0, facecolor=color))


def draw(theme: str, data: dict[str, list[float]]) -> None:
    t = THEMES[theme]
    plt.rcParams.update({"font.family": ["Segoe UI", "DejaVu Sans"], "font.size": 10,
                         "svg.fonttype": "path", "svg.hashsalt": "gem5-pnm"})
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 2.9), sharey=True)
    fig.patch.set_alpha(0)
    ys = list(range(len(ROWS)))[::-1]
    for ax, (title, key) in zip(axes, PANELS):
        ax.set_facecolor("none")
        for y, (label, *_ , series), v in zip(ys, ROWS, data[key]):
            bar(ax, y, v, 0.56, t[series])
            ax.text(v + 1.5, y, f"+{v:.0f}%", va="center", ha="left", color=t["ink"], fontsize=10)
        ax.set_xlim(0, 72)
        ax.set_title(title, loc="left", color=t["ink"], fontsize=11, fontweight="semibold", pad=8)
        ax.set_xticks([0, 20, 40, 60])
        ax.set_xticklabels(["0", "+20%", "+40%", "+60%"])
        ax.tick_params(axis="x", colors=t["ink2"], length=0, labelsize=9)
        ax.tick_params(axis="y", length=0)
        ax.grid(axis="x", color=t["grid"], linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right", "bottom"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color(t["axis"])
    axes[0].set_yticks(ys)
    axes[0].set_yticklabels([r[0] for r in ROWS], color=t["ink2"], fontsize=10)
    handles = [plt.Rectangle((0, 0), 1, 1, color=t[s], linewidth=0) for s in ("pnm", "control", "capstone")]
    fig.legend(handles, [SERIES[s] for s in ("pnm", "control", "capstone")], loc="lower left",
               bbox_to_anchor=(0.01, -0.02), ncol=3, frameon=False, fontsize=9, labelcolor=t["ink2"],
               handlelength=1.0, handleheight=1.0, columnspacing=1.6)
    fig.text(0.01, 0.99, "Throughput over the single-core host that runs its own compaction",
             color=t["ink2"], fontsize=9, va="top")
    fig.subplots_adjust(left=0.19, right=0.99, top=0.80, bottom=0.21, wspace=0.08)
    out = REPO / "figures" / f"throughput-{theme}.svg"
    out.parent.mkdir(exist_ok=True)
    fig.savefig(out, format="svg", metadata={"Date": None})
    plt.close(fig)
    print("wrote", out.relative_to(REPO))


def main() -> None:
    data = gains()
    for theme in THEMES:
        draw(theme, data)


if __name__ == "__main__":
    main()
