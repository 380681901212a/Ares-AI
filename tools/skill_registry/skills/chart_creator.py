"""
chart_creator — AresAI SkillRegistry

Creates professional matplotlib charts from JSON data and saves them as PNG files.

Config:
    context_data_path: str — path to JSON data source
    charts: list of {
        title:           str — chart title
        chart_type:      str — 'bar' | 'horizontal_bar' | 'pie' | 'line'
        data_key:        str — key in context_data.json to use as data source
        output_filename: str — e.g. 'workspace/chart_specs.png'
        xlabel:          str — x-axis label (optional)
        ylabel:          str — y-axis label (optional)
        color_scheme:    str — 'premium' | 'default'
    }

Returns:
    str — comma-separated list of generated PNG file paths
"""

import json
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for servers
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np


# ── Premium colour palette ─────────────────────────────────────────────────────
_PREMIUM_COLORS = [
    "#1A1A1A", "#BB0000", "#4A90D9", "#2ECC71",
    "#F39C12", "#9B59B6", "#1ABC9C", "#E74C3C",
]
_BG_COLOR  = "#F8F8F8"
_GRID_COLOR = "#E0E0E0"


def _setup_style(ax: plt.Axes, title: str, xlabel: str, ylabel: str) -> None:
    ax.set_facecolor(_BG_COLOR)
    ax.figure.patch.set_facecolor("white")
    ax.set_title(title, fontsize=14, fontweight="bold", color="#1A1A1A", pad=14)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=11, color="#444444")
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=11, color="#444444")
    ax.tick_params(colors="#555555")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(_GRID_COLOR)
    ax.spines["bottom"].set_color(_GRID_COLOR)
    ax.yaxis.grid(True, color=_GRID_COLOR, linewidth=0.8)
    ax.set_axisbelow(True)


def _extract_kv(data) -> tuple[list[str], list[float]]:
    """Extract labels and numeric values from various data shapes."""
    labels, values = [], []
    if isinstance(data, dict):
        for k, v in data.items():
            try:
                labels.append(str(k).replace("_", " ").title())
                values.append(float(str(v).replace(",", "").replace(" ", "").split()[0]))
            except (ValueError, IndexError):
                pass
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                keys = list(item.keys())
                if len(keys) >= 2:
                    try:
                        labels.append(str(item[keys[0]]))
                        values.append(float(str(item[keys[1]]).replace(",", "").split()[0]))
                    except (ValueError, IndexError):
                        pass
    return labels, values


def _make_bar(ax: plt.Axes, labels: list, values: list, color_scheme: str, horizontal: bool) -> None:
    colors = _PREMIUM_COLORS[:len(labels)] if color_scheme == "premium" else None
    if horizontal:
        bars = ax.barh(labels, values, color=colors or "#1A1A1A", edgecolor="white", linewidth=0.5)
        for bar in bars:
            w = bar.get_width()
            ax.text(w + max(values) * 0.01, bar.get_y() + bar.get_height() / 2,
                    f"{w:.1f}", va="center", fontsize=9, color="#333333")
    else:
        bars = ax.bar(labels, values, color=colors or "#1A1A1A", edgecolor="white", linewidth=0.5)
        for bar in bars:
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2, h + max(values) * 0.01,
                    f"{h:.1f}", ha="center", fontsize=9, color="#333333")
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=9)


def _make_pie(ax: plt.Axes, labels: list, values: list, color_scheme: str) -> None:
    colors = _PREMIUM_COLORS[:len(labels)] if color_scheme == "premium" else None
    wedges, texts, autotexts = ax.pie(
        values, labels=labels, colors=colors,
        autopct="%1.1f%%", startangle=140,
        wedgeprops={"edgecolor": "white", "linewidth": 1.5},
    )
    for at in autotexts:
        at.set_fontsize(9)
        at.set_color("white")


def _make_line(ax: plt.Axes, labels: list, values: list, color_scheme: str) -> None:
    color = _PREMIUM_COLORS[0] if color_scheme == "premium" else "#1A1A1A"
    ax.plot(labels, values, color=color, linewidth=2.5, marker="o", markersize=6)
    ax.fill_between(range(len(labels)), values, alpha=0.08, color=color)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=9)


def _make_chart(chart_cfg: dict, context: dict, workdir: str) -> str | None:
    title       = chart_cfg.get("title", "Chart")
    chart_type  = chart_cfg.get("chart_type", "bar")
    data_key    = chart_cfg.get("data_key", "")
    out_rel     = chart_cfg.get("output_filename", f"workspace/{data_key}_chart.png")
    xlabel      = chart_cfg.get("xlabel", "")
    ylabel      = chart_cfg.get("ylabel", "Значення")
    color_scheme = chart_cfg.get("color_scheme", "premium")

    data = context.get(data_key)
    if not data:
        print(f"[chart_creator] Warning: key '{data_key}' not found in context, skipping chart.")
        return None

    labels, values = _extract_kv(data)
    if not labels or not values:
        print(f"[chart_creator] Warning: could not extract numeric data from '{data_key}', skipping.")
        return None

    fig, ax = plt.subplots(figsize=(10, 5))

    if chart_type == "pie":
        _make_pie(ax, labels, values, color_scheme)
    elif chart_type == "line":
        _setup_style(ax, title, xlabel, ylabel)
        _make_line(ax, labels, values, color_scheme)
    elif chart_type == "horizontal_bar":
        _setup_style(ax, title, xlabel, ylabel)
        _make_bar(ax, labels, values, color_scheme, horizontal=True)
    else:
        _setup_style(ax, title, xlabel, ylabel)
        _make_bar(ax, labels, values, color_scheme, horizontal=False)

    if chart_type != "pie":
        ax.set_title(title, fontsize=14, fontweight="bold", color="#1A1A1A", pad=14)

    plt.tight_layout()
    out_abs = os.path.join(workdir, out_rel)
    os.makedirs(os.path.dirname(out_abs), exist_ok=True)
    plt.savefig(out_abs, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[chart_creator] ✅ Chart saved: {out_rel}")
    return out_rel


def execute(config: dict, workdir: str = ".") -> str:
    """Create one or more charts from JSON context data."""
    ctx_rel  = config.get("context_data_path", "workspace/context_data.json")
    charts   = config.get("charts", [])

    ctx_abs = os.path.join(workdir, ctx_rel)
    context: dict = {}
    if os.path.exists(ctx_abs):
        with open(ctx_abs, "r", encoding="utf-8") as f:
            context = json.load(f)

    if not charts:
        return "Error: No charts defined in chart_creator config."

    generated: list[str] = []
    for chart_cfg in charts:
        result = _make_chart(chart_cfg, context, workdir)
        if result:
            generated.append(result)

    if not generated:
        return "Error: No charts could be generated (missing or non-numeric data)."

    return ", ".join(generated)
