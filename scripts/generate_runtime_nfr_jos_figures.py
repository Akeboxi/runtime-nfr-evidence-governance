"""Generate the second-generation Runtime NFR figures in a JOS-oriented style.

The script only reads frozen manuscript artifacts and Phase-2 analyses.  It
writes a new output directory and never overwrites the original six figures.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import patches
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont, ImageOps


STATUS_ORDER = [
    "insufficient_evidence",
    "threshold_unresolved",
    "needs_context_review",
    "candidate_for_stakeholder_review",
]
STATUS_LABELS = {
    "insufficient_evidence": "历史不足",
    "threshold_unresolved": "阈值未解析",
    "needs_context_review": "上下文审查",
    "candidate_for_stakeholder_review": "可转交确认",
}
STATUS_COLORS = {
    "insufficient_evidence": "#7A7A7A",
    "threshold_unresolved": "#D28E00",
    "needs_context_review": "#8A5DA2",
    "candidate_for_stakeholder_review": "#2F6F9F",
}
STATUS_HATCHES = {
    "insufficient_evidence": "///",
    "threshold_unresolved": "...",
    "needs_context_review": "xxx",
    "candidate_for_stakeholder_review": "\\\\\\",
}
TEXT = "#2F3437"
GRID = "#D9DEE2"
RED = "#9E3D3D"
LIGHT_BLUE = "#DCEAF4"
LIGHT_ORANGE = "#F6E6BE"
LIGHT_GREEN = "#DDEFE8"
LIGHT_GRAY = "#F2F3F4"


def _style() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [
                "Microsoft YaHei",
                "SimHei",
                "Arial",
                "DejaVu Sans",
            ],
            "axes.unicode_minus": False,
            "font.size": 8.5,
            "axes.titlesize": 9.5,
            "axes.labelsize": 8.5,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.5,
            "legend.fontsize": 7.3,
            "axes.edgecolor": TEXT,
            "axes.linewidth": 0.8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_record(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "relative_path": str(path.resolve().relative_to(root.resolve())),
        "bytes": path.stat().st_size,
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def _panel(ax: plt.Axes, letter: str, title: str) -> None:
    ax.text(
        -0.06,
        1.05,
        letter,
        transform=ax.transAxes,
        fontsize=10.5,
        fontweight="bold",
        va="bottom",
        color=TEXT,
    )
    ax.set_title(title, loc="left", pad=7, color=TEXT, fontweight="bold")


def _grid_y(ax: plt.Axes) -> None:
    ax.grid(axis="y", color=GRID, linewidth=0.45, linestyle="-", zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _save_figure(
    fig: plt.Figure,
    output: Path,
    stem: str,
) -> list[Path]:
    written: list[Path] = []
    for ext in ("svg", "pdf", "png"):
        path = output / f"{stem}.{ext}"
        fig.savefig(
            path,
            dpi=300 if ext == "png" else None,
            bbox_inches="tight",
            facecolor="white",
        )
        written.append(path)
    plt.close(fig)
    png = output / f"{stem}.png"
    gray_dir = output / "qa_grayscale"
    gray_dir.mkdir(exist_ok=True)
    with Image.open(png) as image:
        image.convert("L").save(gray_dir / f"{stem}_gray.png")
    written.append(gray_dir / f"{stem}_gray.png")
    return written


def _rect_text(
    ax: plt.Axes,
    xy: tuple[float, float],
    width: float,
    height: float,
    text: str,
    *,
    fc: str = "white",
    ec: str = TEXT,
    lw: float = 0.9,
    fontsize: float = 7.2,
    color: str = TEXT,
    ha: str = "left",
    hatch: str | None = None,
    rounded: bool = True,
    fontweight: str = "normal",
) -> patches.Patch:
    x, y = xy
    if rounded:
        patch = patches.FancyBboxPatch(
            (x, y),
            width,
            height,
            boxstyle="round,pad=0.008,rounding_size=0.008",
            facecolor=fc,
            edgecolor=ec,
            linewidth=lw,
            hatch=hatch,
        )
    else:
        patch = patches.Rectangle(
            (x, y),
            width,
            height,
            facecolor=fc,
            edgecolor=ec,
            linewidth=lw,
            hatch=hatch,
        )
    ax.add_patch(patch)
    tx = x + width / 2 if ha == "center" else x + 0.012
    ax.text(
        tx,
        y + height / 2,
        text,
        ha=ha,
        va="center",
        fontsize=fontsize,
        color=color,
        fontweight=fontweight,
        linespacing=1.25,
    )
    return patch


def _arrow(
    ax: plt.Axes,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    color: str = TEXT,
    lw: float = 1.1,
    style: str = "-|>",
    linestyle: str = "-",
    connectionstyle: str = "arc3,rad=0",
) -> None:
    ax.annotate(
        "",
        xy=end,
        xytext=start,
        arrowprops={
            "arrowstyle": style,
            "color": color,
            "lw": lw,
            "linestyle": linestyle,
            "connectionstyle": connectionstyle,
            "shrinkA": 1,
            "shrinkB": 1,
        },
    )


def _status_legend() -> list[patches.Patch]:
    return [
        patches.Patch(
            facecolor=STATUS_COLORS[s],
            edgecolor=TEXT,
            hatch=STATUS_HATCHES[s],
            label=STATUS_LABELS[s],
            linewidth=0.6,
        )
        for s in STATUS_ORDER
    ]


def figure_01(
    cases_payload: dict[str, Any],
    threshold_payload: dict[str, Any],
    output: Path,
) -> list[Path]:
    cases = cases_payload["cases"]
    fig, ax = plt.subplots(figsize=(13.2, 6.4))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(0.015, 0.97, "a", fontsize=11, fontweight="bold", va="top")
    ax.text(0.042, 0.97, "同值/近值候选", fontsize=10, fontweight="bold", va="top")
    ax.text(0.405, 0.97, "b", fontsize=11, fontweight="bold", va="top")
    ax.text(0.432, 0.97, "证据审计与优先级治理", fontsize=10, fontweight="bold", va="top")
    ax.text(0.712, 0.97, "c", fontsize=11, fontweight="bold", va="top")
    ax.text(0.739, 0.97, "治理动作（不是自动 SLO）", fontsize=10, fontweight="bold", va="top")

    # Pair 1: identical zero-valued error-ratio candidates.
    ax.add_patch(
        patches.FancyBboxPatch(
            (0.015, 0.55),
            0.365,
            0.34,
            boxstyle="round,pad=0.008",
            facecolor="white",
            edgecolor="#A7ADB2",
            linewidth=0.8,
            linestyle="--",
        )
    )
    ax.text(
        0.028,
        0.865,
        "同为 error_ratio = 0",
        fontsize=8.6,
        fontweight="bold",
        color=TEXT,
    )
    c_a = cases["insufficient_evidence"]
    c_b = cases["threshold_unresolved"]
    _rect_text(
        ax,
        (0.032, 0.605),
        0.15,
        0.21,
        (
            f"emailservice\n候选 0\n覆盖 {c_a['history_coverage']:.1%} · n={c_a['valid_samples']}\n"
            "H 历史/样本不足\nR 零错误基线"
        ),
        fc="#F5F5F5",
        ec=STATUS_COLORS["insufficient_evidence"],
        fontsize=7.3,
    )
    _rect_text(
        ax,
        (0.205, 0.605),
        0.15,
        0.21,
        (
            f"recommendationservice\n候选 0\n覆盖 {c_b['history_coverage']:.1%} · n={c_b['valid_samples']:,}\n"
            "H 通过\nR 零错误/分辨率"
        ),
        fc="#FFF8E8",
        ec=STATUS_COLORS["threshold_unresolved"],
        fontsize=7.1,
    )
    ax.annotate(
        "相同数值 ≠ 相同证据责任",
        xy=(0.197, 0.588),
        xytext=(0.197, 0.565),
        ha="center",
        va="top",
        fontsize=7.4,
        color=RED,
    )

    # Pair 2: close latency candidates around the frozen tail trigger.
    ax.add_patch(
        patches.FancyBboxPatch(
            (0.015, 0.16),
            0.365,
            0.34,
            boxstyle="round,pad=0.008",
            facecolor="white",
            edgecolor="#A7ADB2",
            linewidth=0.8,
            linestyle="--",
        )
    )
    ax.text(
        0.028,
        0.475,
        "cartservice：候选阈值仅相差约 5.4 ms",
        fontsize=8.6,
        fontweight="bold",
        color=TEXT,
    )
    c_c = cases["needs_context_review"]
    c_d = cases["candidate_for_stakeholder_review"]
    _rect_text(
        ax,
        (0.032, 0.215),
        0.15,
        0.21,
        (
            f"候选 {c_c['raw_threshold']:,.3f} ms\n覆盖 {c_c['history_coverage']:.1%} · n={c_c['valid_samples']:,}\n"
            f"尾比率 {c_c['latency_tail_ratio']:.3f}\nT 触发上下文审查"
        ),
        fc="#F4ECF7",
        ec=STATUS_COLORS["needs_context_review"],
        fontsize=7.3,
    )
    _rect_text(
        ax,
        (0.205, 0.215),
        0.15,
        0.21,
        (
            f"候选 {c_d['raw_threshold']:,.3f} ms\n覆盖 {c_d['history_coverage']:.1%} · n={c_d['valid_samples']:,}\n"
            f"尾比率 {c_d['latency_tail_ratio']:.3f}\nH/R/T 均通过"
        ),
        fc="#EAF2F8",
        ec=STATUS_COLORS["candidate_for_stakeholder_review"],
        fontsize=7.3,
    )
    ax.text(
        0.197,
        0.185,
        "10.0 是审查触发器，不是自然真值",
        ha="center",
        fontsize=7.4,
        color=RED,
    )

    # Evidence gates.
    gate_x, gate_w = 0.43, 0.22
    gates = [
        (0.69, "H  历史覆盖与有效样本", LIGHT_BLUE),
        (0.49, "R  参考目标与测量分辨率", LIGHT_ORANGE),
        (0.29, "T  工作负载与长尾上下文", "#EFE6F3"),
    ]
    for y, label, color in gates:
        _rect_text(
            ax,
            (gate_x, y),
            gate_w,
            0.115,
            label,
            fc=color,
            fontsize=8.0,
            ha="center",
            fontweight="bold",
        )
    ax.text(
        0.54,
        0.18,
        "冻结优先级：history → resolution → tail",
        ha="center",
        fontsize=7.8,
        color=TEXT,
    )
    _arrow(ax, (0.54, 0.68), (0.54, 0.61), color="#78838B")
    _arrow(ax, (0.54, 0.48), (0.54, 0.41), color="#78838B")
    _arrow(ax, (0.38, 0.72), (0.43, 0.745), color=STATUS_COLORS["insufficient_evidence"])
    _arrow(ax, (0.38, 0.67), (0.43, 0.545), color=STATUS_COLORS["threshold_unresolved"])
    _arrow(ax, (0.38, 0.33), (0.43, 0.345), color=STATUS_COLORS["needs_context_review"])
    _arrow(
        ax,
        (0.38, 0.29),
        (0.43, 0.745),
        color=STATUS_COLORS["candidate_for_stakeholder_review"],
        connectionstyle="arc3,rad=-0.22",
    )

    # Governance actions.
    action_x, action_w = 0.735, 0.245
    actions = [
        (
            0.70,
            "补充历史，禁止直接操作化",
            "insufficient_evidence",
        ),
        (
            0.52,
            "补充参考目标/分辨率或征询输入",
            "threshold_unresolved",
        ),
        (
            0.34,
            "审查工作负载与尾分布",
            "needs_context_review",
        ),
        (
            0.16,
            "转交利益相关者确认\n（不含 SLO 批准）",
            "candidate_for_stakeholder_review",
        ),
    ]
    for y, label, status in actions:
        _rect_text(
            ax,
            (action_x, y),
            action_w,
            0.105,
            label,
            fc="white",
            ec=STATUS_COLORS[status],
            hatch=STATUS_HATCHES[status],
            fontsize=7.8,
            ha="center",
            fontweight="bold",
        )
    for y0, y1, status in [
        (0.745, 0.752, "insufficient_evidence"),
        (0.545, 0.572, "threshold_unresolved"),
        (0.345, 0.392, "needs_context_review"),
        (0.29, 0.212, "candidate_for_stakeholder_review"),
    ]:
        _arrow(
            ax,
            (0.65, y0),
            (action_x, y1),
            color=STATUS_COLORS[status],
        )
    ax.text(
        0.858,
        0.075,
        "候选 ≠ SLO    ·    专家评价 ≠ 利益相关者批准",
        ha="center",
        fontsize=8.0,
        color=RED,
        fontweight="bold",
    )

    hidden = threshold_payload["evidence_actions_hidden_by_minimal_baseline"]
    ax.add_patch(
        patches.FancyBboxPatch(
            (0.015, 0.02),
            0.65,
            0.095,
            boxstyle="round,pad=0.006",
            facecolor="#F7F9FA",
            edgecolor="#9AA3A9",
            linewidth=0.8,
        )
    )
    ax.text(
        0.34,
        0.068,
        (
            f"冻结全集：8,668 张候选卡；最小 threshold-only 输出会隐藏 "
            f"{hidden['count']:,} 张（{hidden['rate']:.1%}）的证据/动作信息"
        ),
        ha="center",
        va="center",
        fontsize=7.9,
        color=TEXT,
    )
    ax.text(
        0.34,
        0.036,
        "该比较说明额外暴露证据责任与治理动作，不说明阈值准确率或因果效用更高。",
        ha="center",
        va="center",
        fontsize=7.0,
        color=RED,
    )
    return _save_figure(fig, output, "figure_01_evidence_aware_governance")


def figure_02(
    governance: dict[str, Any],
    ablation: dict[str, Any],
    threshold: dict[str, Any],
    output: Path,
) -> list[Path]:
    fig, axes = plt.subplots(2, 2, figsize=(12.5, 8.1))
    fig.subplots_adjust(wspace=0.33, hspace=0.56)

    ax = axes[0, 0]
    _panel(ax, "a", "四类治理状态（全量 n=8,668）")
    counts = [threshold["governance_counts"][s] for s in STATUS_ORDER]
    y = np.arange(len(STATUS_ORDER))
    bars = ax.barh(
        y,
        counts,
        color=[STATUS_COLORS[s] for s in STATUS_ORDER],
        edgecolor=TEXT,
        linewidth=0.6,
        hatch=[STATUS_HATCHES[s] for s in STATUS_ORDER],
        zorder=2,
    )
    ax.set_yticks(y, [STATUS_LABELS[s] for s in STATUS_ORDER])
    ax.invert_yaxis()
    ax.set_xlabel("候选卡数")
    ax.set_xlim(0, 4900)
    for bar, count in zip(bars, counts):
        ax.text(
            count + 70,
            bar.get_y() + bar.get_height() / 2,
            f"{count:,}  ({count / 8668:.1%})",
            va="center",
            fontsize=7.4,
        )
    _grid_y(ax)
    ax.grid(axis="x", color=GRID, linewidth=0.45)

    ax = axes[0, 1]
    _panel(ax, "b", "SLI 内的状态构成")
    sli_rows = {row["sli"]: row for row in governance["strata"]["sli"]}
    sli_order = ["error_ratio", "latency"]
    left = np.zeros(2)
    for status in STATUS_ORDER:
        vals = np.array(
            [sli_rows[sli][status] / sli_rows[sli]["total"] for sli in sli_order]
        )
        bars = ax.barh(
            np.arange(2),
            vals,
            left=left,
            color=STATUS_COLORS[status],
            edgecolor="white",
            linewidth=0.7,
            hatch=STATUS_HATCHES[status],
            label=STATUS_LABELS[status],
        )
        for i, (bar, frac) in enumerate(zip(bars, vals)):
            n = sli_rows[sli_order[i]][status]
            if frac >= 0.085:
                ax.text(
                    left[i] + frac / 2,
                    bar.get_y() + bar.get_height() / 2,
                    f"{n:,}\n{frac:.0%}",
                    ha="center",
                    va="center",
                    fontsize=6.8,
                    color="white" if status != "threshold_unresolved" else TEXT,
                    fontweight="bold",
                )
        left += vals
    ax.set_yticks(np.arange(2), ["error_ratio\n(n=4,334)", "latency\n(n=4,334)"])
    ax.invert_yaxis()
    ax.set_xlim(0, 1)
    ax.set_xlabel("SLI 内比例")
    ax.set_xticks(np.linspace(0, 1, 6), [f"{int(x * 100)}%" for x in np.linspace(0, 1, 6)])
    ax.legend(
        handles=_status_legend(),
        loc="upper center",
        bbox_to_anchor=(0.5, -0.14),
        ncol=2,
        frameon=False,
        fontsize=6.8,
    )
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax = axes[1, 0]
    _panel(ax, "c", "规则触发、交集与优先级")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    gates = [
        (0.04, 0.65, "H  history\n独立触发 1,054", LIGHT_BLUE),
        (0.36, 0.65, "R  resolution\n独立触发 2,735", LIGHT_ORANGE),
        (0.68, 0.65, "T  tail\n独立触发 1,082", "#EFE6F3"),
    ]
    for x, y0, label, color in gates:
        _rect_text(
            ax,
            (x, y0),
            0.25,
            0.19,
            label,
            fc=color,
            ha="center",
            fontsize=8.0,
            fontweight="bold",
        )
    _arrow(ax, (0.29, 0.745), (0.36, 0.745))
    _arrow(ax, (0.61, 0.745), (0.68, 0.745))
    ax.text(
        0.50,
        0.91,
        "冻结优先级：history → resolution → tail",
        ha="center",
        fontsize=8.0,
        color=RED,
        fontweight="bold",
    )
    _rect_text(
        ax,
        (0.16, 0.37),
        0.30,
        0.13,
        "H ∩ R = 311",
        fc="white",
        ec="#8E989E",
        ha="center",
        fontsize=8.0,
    )
    _rect_text(
        ax,
        (0.54, 0.37),
        0.30,
        0.13,
        "H ∩ T = 134",
        fc="white",
        ec="#8E989E",
        ha="center",
        fontsize=8.0,
    )
    ax.text(
        0.50,
        0.19,
        "规则触发数可以重叠；最终状态互斥并加总为 8,668",
        ha="center",
        fontsize=7.6,
        color=TEXT,
    )
    ax.text(
        0.50,
        0.10,
        "优先级把证据缺口映射为补历史、补分辨率、审查上下文或转交确认",
        ha="center",
        fontsize=7.2,
        color="#5C666C",
    )

    ax = axes[1, 1]
    _panel(ax, "d", "反事实规则审计：发生状态变化的卡数")
    names = [
        "删除 history",
        "删除 resolution",
        "删除 tail",
        "交换 H/R",
        "交换 R/T",
    ]
    scenario_names = [
        "drop_history",
        "drop_resolution",
        "drop_tail",
        "swap_history_resolution",
        "swap_resolution_tail",
    ]
    values = [ablation["scenarios"][name]["changed_cards"] for name in scenario_names]
    y = np.arange(len(names))
    ax.hlines(y, 0, values, color="#AAB2B7", linewidth=1.2, zorder=1)
    ax.scatter(values, y, color="#2F6F9F", s=48, zorder=2, edgecolor=TEXT, linewidth=0.5)
    for yi, value in zip(y, values):
        ax.text(value + 55, yi, f"{value:,}", va="center", fontsize=7.5)
    ax.set_yticks(y, names)
    ax.invert_yaxis()
    ax.set_xlabel("状态发生变化的候选卡数")
    ax.set_xlim(0, 2750)
    ax.grid(axis="x", color=GRID, linewidth=0.45)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.text(
        0.99,
        -0.22,
        "反事实审计，不用于重选规则",
        transform=ax.transAxes,
        ha="right",
        color=RED,
        fontsize=7.2,
    )
    return _save_figure(fig, output, "figure_02_governance_rules_and_strata")


def figure_03(
    cases_payload: dict[str, Any],
    ablation: dict[str, Any],
    output: Path,
) -> list[Path]:
    cases = cases_payload["cases"]
    context = cases["needs_context_review"]
    candidate = cases["candidate_for_stakeholder_review"]
    fig, axes = plt.subplots(
        1,
        3,
        figsize=(13.0, 4.25),
        gridspec_kw={"width_ratios": [1.08, 0.82, 1.35]},
    )
    fig.subplots_adjust(wspace=0.35)

    ax = axes[0]
    _panel(ax, "a", "冻结长尾审查触发器附近的真实卡")
    ax.set_xlim(9.45, 10.55)
    ax.set_ylim(-0.7, 1.25)
    ax.axvline(9.5, color="#AAB2B7", linestyle="--", linewidth=0.9)
    ax.axvline(10.0, color=RED, linestyle="-", linewidth=1.35)
    ax.axvline(10.5, color="#AAB2B7", linestyle="--", linewidth=0.9)
    ax.scatter(
        [candidate["latency_tail_ratio"]],
        [0.25],
        s=70,
        facecolors="white",
        edgecolors=STATUS_COLORS["candidate_for_stakeholder_review"],
        linewidth=1.5,
        zorder=3,
    )
    ax.scatter(
        [context["latency_tail_ratio"]],
        [0.78],
        s=70,
        marker="D",
        color=STATUS_COLORS["needs_context_review"],
        edgecolors=TEXT,
        linewidth=0.5,
        zorder=3,
    )
    ax.annotate(
        f"尾比率 {candidate['latency_tail_ratio']:.3f}\n候选 {candidate['raw_threshold']:,.3f} ms\n可转交确认",
        xy=(candidate["latency_tail_ratio"], 0.25),
        xytext=(9.62, -0.28),
        arrowprops=dict(arrowstyle="-", color=STATUS_COLORS["candidate_for_stakeholder_review"], lw=0.8),
        fontsize=7.2,
        ha="center",
        color=TEXT,
    )
    ax.annotate(
        f"尾比率 {context['latency_tail_ratio']:.3f}\n候选 {context['raw_threshold']:,.3f} ms\n上下文审查",
        xy=(context["latency_tail_ratio"], 0.78),
        xytext=(10.31, 0.84),
        arrowprops=dict(arrowstyle="-", color=STATUS_COLORS["needs_context_review"], lw=0.8),
        fontsize=7.2,
        ha="center",
        color=TEXT,
    )
    ax.text(
        10.0,
        1.13,
        "主规则 10.0",
        ha="center",
        color=RED,
        fontsize=7.5,
        fontweight="bold",
    )
    ax.set_xlabel("延迟长尾比率")
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["top"].set_visible(False)

    ax = axes[1]
    _panel(ax, "b", "相对主规则的卡级变化")
    cutoffs = [9.5, 10.0, 10.5]
    changed = [
        ablation["scenarios"]["tail_cutoff_9.5"]["changed_cards"],
        0,
        ablation["scenarios"]["tail_cutoff_10.5"]["changed_cards"],
    ]
    colors = [STATUS_COLORS["needs_context_review"], "#B8BFC4", STATUS_COLORS["candidate_for_stakeholder_review"]]
    bars = ax.bar(
        cutoffs,
        changed,
        width=0.28,
        color=colors,
        edgecolor=TEXT,
        linewidth=0.6,
        hatch=["xxx", "", "\\\\\\"],
        zorder=2,
    )
    for bar, value in zip(bars, changed):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + 0.8,
            str(value),
            ha="center",
            fontsize=8,
            fontweight="bold",
        )
    ax.set_xticks(cutoffs)
    ax.set_xlabel("反事实 cutoff")
    ax.set_ylabel("相对 10.0 改变状态的卡数")
    ax.set_ylim(0, 23)
    _grid_y(ax)
    ax.text(
        9.5,
        4.2,
        "9 张\n转入审查",
        ha="center",
        fontsize=6.9,
        color="white",
        fontweight="bold",
    )
    ax.text(
        10.5,
        6.2,
        "19 张\n释放为转交",
        ha="center",
        fontsize=6.9,
        color="white",
        fontweight="bold",
    )

    ax = axes[2]
    _panel(ax, "c", "近值候选的证据卡对照")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    _rect_text(
        ax,
        (0.03, 0.26),
        0.42,
        0.56,
        (
            "cartservice / latency\n"
            f"候选：{context['raw_threshold']:,.3f} ms\n"
            f"覆盖：{context['history_coverage']:.1%}\n"
            f"有效样本：{context['valid_samples']:,}\n"
            f"尾比率：{context['latency_tail_ratio']:.3f}\n\n"
            "状态：上下文审查\n"
            "动作：审查工作负载与尾分布"
        ),
        fc="#F4ECF7",
        ec=STATUS_COLORS["needs_context_review"],
        fontsize=7.5,
    )
    _rect_text(
        ax,
        (0.55, 0.26),
        0.42,
        0.56,
        (
            "cartservice / latency\n"
            f"候选：{candidate['raw_threshold']:,.3f} ms\n"
            f"覆盖：{candidate['history_coverage']:.1%}\n"
            f"有效样本：{candidate['valid_samples']:,}\n"
            f"尾比率：{candidate['latency_tail_ratio']:.3f}\n\n"
            "状态：可转交确认\n"
            "动作：进入利益相关者确认"
        ),
        fc="#EAF2F8",
        ec=STATUS_COLORS["candidate_for_stakeholder_review"],
        fontsize=7.5,
    )
    _arrow(ax, (0.45, 0.53), (0.55, 0.53), style="<->", color=RED)
    ax.text(
        0.50,
        0.61,
        "候选差约 5.4 ms",
        ha="center",
        fontsize=7.0,
        color=RED,
        fontweight="bold",
    )
    ax.text(
        0.50,
        0.12,
        "10.0 是上下文审查触发器，不是自然真值；\n敏感性分析不用于重新选择阈值。",
        ha="center",
        va="center",
        fontsize=7.2,
        color=RED,
    )
    return _save_figure(fig, output, "figure_03_tail_boundary_and_sensitivity")


def figure_04(
    raw: pd.DataFrame,
    per_card: pd.DataFrame,
    summary: dict[str, Any],
    output: Path,
) -> list[Path]:
    fig = plt.figure(figsize=(12.8, 8.7))
    gs = fig.add_gridspec(2, 2, wspace=0.34, hspace=0.42)
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])
    ax_c = fig.add_subplot(gs[1, 0])
    ax_d = fig.add_subplot(gs[1, 1])

    _panel(ax_a, "a", "48 张卡的阈值—治理配对中位数")
    rng = np.random.default_rng(20260726)
    for status in STATUS_ORDER:
        subset = per_card[per_card["governance_status"] == status]
        for _, row in subset.iterrows():
            jitter = rng.uniform(-0.025, 0.025)
            ax_a.plot(
                [0 + jitter, 1 + jitter],
                [
                    row["threshold_plausibility_median"],
                    row["governance_appropriateness_median"],
                ],
                color=STATUS_COLORS[status],
                alpha=0.34,
                linewidth=1.0,
            )
            ax_a.scatter(
                [0 + jitter, 1 + jitter],
                [
                    row["threshold_plausibility_median"],
                    row["governance_appropriateness_median"],
                ],
                s=16,
                facecolor="white" if status == "candidate_for_stakeholder_review" else STATUS_COLORS[status],
                edgecolor=STATUS_COLORS[status],
                linewidth=0.65,
                zorder=3,
            )
    ax_a.set_xlim(-0.18, 1.18)
    ax_a.set_ylim(0.7, 5.3)
    ax_a.set_xticks([0, 1], ["阈值可信度", "治理适当性"])
    ax_a.set_yticks([1, 2, 3, 4, 5])
    ax_a.set_ylabel("卡级中位数（1–5）")
    ax_a.grid(axis="y", color=GRID, linewidth=0.45)
    ax_a.legend(
        handles=[
            Line2D([0], [0], color=STATUS_COLORS[s], marker="o", label=STATUS_LABELS[s])
            for s in STATUS_ORDER
        ],
        loc="lower left",
        fontsize=6.5,
        frameon=False,
    )
    ax_a.text(
        0.98,
        0.04,
        "48 卡 × 3 专家 = 144 个配对判断",
        transform=ax_a.transAxes,
        ha="right",
        fontsize=7.0,
        color=TEXT,
    )
    ax_a.spines["top"].set_visible(False)
    ax_a.spines["right"].set_visible(False)

    _panel(ax_b, "b", "144 个专家—卡配对判断的联合频数")
    freq = (
        raw.groupby(["threshold_plausibility", "governance_appropriateness"])
        .size()
        .reset_index(name="n")
    )
    sizes = 28 + 16 * freq["n"]
    ax_b.scatter(
        freq["threshold_plausibility"],
        freq["governance_appropriateness"],
        s=sizes,
        color="#6B9BC3",
        alpha=0.78,
        edgecolor=TEXT,
        linewidth=0.6,
        zorder=2,
    )
    for _, row in freq.iterrows():
        ax_b.text(
            row["threshold_plausibility"],
            row["governance_appropriateness"],
            str(int(row["n"])),
            ha="center",
            va="center",
            fontsize=7.0,
            fontweight="bold",
            color="white" if row["n"] >= 4 else TEXT,
            zorder=3,
        )
    ax_b.plot([1, 5], [1, 5], linestyle="--", color=RED, linewidth=0.9)
    ax_b.set_xlim(0.6, 5.4)
    ax_b.set_ylim(0.6, 5.4)
    ax_b.set_xticks([1, 2, 3, 4, 5])
    ax_b.set_yticks([1, 2, 3, 4, 5])
    ax_b.set_xlabel("阈值可信度")
    ax_b.set_ylabel("治理适当性")
    ax_b.grid(color=GRID, linewidth=0.45)
    overall = summary["overall_paired"]
    ax_b.text(
        0.04,
        0.96,
        (
            f"对角线上方 {overall['positive_delta_count']}/144\n"
            f"相等 {overall['zero_delta_count']}/144；下降 {overall['negative_delta_count']}/144"
        ),
        transform=ax_b.transAxes,
        va="top",
        fontsize=7.2,
        color=TEXT,
    )

    _panel(ax_c, "c", "不同治理状态下的配对差 Δ")
    data = [
        raw.loc[raw["governance_status"] == s, "governance_minus_threshold"].to_numpy()
        for s in STATUS_ORDER
    ]
    box = ax_c.boxplot(
        data,
        positions=np.arange(4),
        widths=0.55,
        patch_artist=True,
        showfliers=False,
        medianprops={"color": RED, "linewidth": 1.3},
        whiskerprops={"color": TEXT, "linewidth": 0.8},
        capprops={"color": TEXT, "linewidth": 0.8},
        boxprops={"color": TEXT, "linewidth": 0.7},
    )
    for patch, status in zip(box["boxes"], STATUS_ORDER):
        patch.set_facecolor(STATUS_COLORS[status])
        patch.set_alpha(0.55)
        patch.set_hatch(STATUS_HATCHES[status])
    for idx, (status, values) in enumerate(zip(STATUS_ORDER, data)):
        jitter = rng.uniform(-0.16, 0.16, len(values))
        ax_c.scatter(
            idx + jitter,
            values,
            s=12,
            color=STATUS_COLORS[status],
            alpha=0.48,
            edgecolor="none",
            zorder=3,
        )
    ax_c.axhline(0, color=RED, linestyle="--", linewidth=0.8)
    ax_c.set_xticks(np.arange(4), [STATUS_LABELS[s] for s in STATUS_ORDER], rotation=18, ha="right")
    ax_c.set_ylabel("Δ = 治理适当性 − 阈值可信度")
    ax_c.set_ylim(-0.35, 4.5)
    _grid_y(ax_c)
    ax_c.text(
        0.98,
        0.95,
        f"总体中位数 {overall['median_delta']:.0f}\nIQR {overall['q1_delta']:.0f}–{overall['q3_delta']:.0f}",
        transform=ax_c.transAxes,
        ha="right",
        va="top",
        fontsize=7.2,
    )

    _panel(ax_d, "d", "队列与专家分层")
    group_labels: list[str] = []
    low_rates: list[float] = []
    high_rates: list[float] = []
    for key, label in [("remediation", "修复集"), ("generalization", "留出集")]:
        item = summary["by_cohort"][key]
        group_labels.append(label)
        low_rates.append(item["threshold_low_1_or_2_count"] / item["rows"])
        high_rates.append(item["governance_high_4_or_5_count"] / item["rows"])
    for key, label in [
        ("rev_r2_01", "专家1"),
        ("rev_r2_02", "专家2"),
        ("rev_r2_03", "专家3"),
    ]:
        item = summary["by_reviewer"][key]
        group_labels.append(label)
        low_rates.append(item["threshold_low_1_or_2_count"] / item["rows"])
        high_rates.append(item["governance_high_4_or_5_count"] / item["rows"])
    x = np.arange(len(group_labels))
    width = 0.35
    ax_d.bar(
        x - width / 2,
        low_rates,
        width,
        color="#D9A441",
        edgecolor=TEXT,
        linewidth=0.5,
        hatch="...",
        label="阈值低分（1–2）",
    )
    ax_d.bar(
        x + width / 2,
        high_rates,
        width,
        color="#4D86B3",
        edgecolor=TEXT,
        linewidth=0.5,
        hatch="\\\\\\",
        label="治理高分（4–5）",
    )
    ax_d.set_xticks(x, group_labels)
    ax_d.set_ylim(0, 1.12)
    ax_d.set_yticks(np.linspace(0, 1, 6), [f"{int(v * 100)}%" for v in np.linspace(0, 1, 6)])
    ax_d.set_ylabel("组内比例")
    _grid_y(ax_d)
    ax_d.legend(loc="lower left", frameon=False, ncol=2)
    alpha = summary["reported_ordinal_alpha"]
    ax_d.text(
        0.02,
        -0.28,
        (
            f"总体：阈值低分 106/144（73.6%）；治理高分 143/144（99.3%）。\n"
            f"ordinal α：阈值 {alpha['threshold_plausibility']:.3f}；治理 {alpha['governance_appropriateness']:.3f}。"
            "治理评分近天花板，α 的解释受零/低变异限制。"
        ),
        transform=ax_d.transAxes,
        fontsize=7.0,
        color=TEXT,
        va="top",
    )
    return _save_figure(fig, output, "figure_04_expert_paired_distinction")


def figure_05(external: dict[str, Any], output: Path) -> list[Path]:
    fig = plt.figure(figsize=(13.0, 4.8))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.15, 0.95, 1.28], wspace=0.34)
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])
    ax_c = fig.add_subplot(gs[0, 2])

    _panel(ax_a, "a", "外部字段到治理卡的接口映射")
    rows = ["事件时间", "服务与延迟", "延迟单位", "请求数", "错误数"]
    cols = ["外部源", "规范接口", "治理卡"]
    states = [
        ["mapped", "mapped", "mapped"],
        ["mapped", "mapped", "mapped"],
        ["assumed", "assumed", "assumed"],
        ["missing", "missing", "missing"],
        ["missing", "missing", "missing"],
    ]
    marker = {"mapped": "o", "assumed": "^", "missing": "x"}
    color = {"mapped": "#2F6F9F", "assumed": "#D28E00", "missing": "#7A7A7A"}
    for i, row in enumerate(rows):
        for j, col in enumerate(cols):
            ax_a.add_patch(
                patches.Rectangle(
                    (j, i),
                    1,
                    1,
                    facecolor="#F8FAFB",
                    edgecolor="#C7CDD1",
                    linewidth=0.7,
                )
            )
            state = states[i][j]
            scatter_kwargs = {
                "marker": marker[state],
                "s": 55 if state != "missing" else 45,
                "linewidth": 1.25,
                "zorder": 3,
            }
            if state == "mapped":
                scatter_kwargs.update(
                    {"facecolor": "white", "edgecolor": color[state]}
                )
            else:
                scatter_kwargs.update({"color": color[state]})
            ax_a.scatter([j + 0.5], [i + 0.5], **scatter_kwargs)
    ax_a.set_xlim(0, 3)
    ax_a.set_ylim(5, 0)
    ax_a.set_xticks(np.arange(3) + 0.5, cols)
    ax_a.xaxis.tick_top()
    ax_a.set_yticks(np.arange(5) + 0.5, rows)
    ax_a.tick_params(length=0)
    for spine in ax_a.spines.values():
        spine.set_visible(False)
    ax_a.text(
        0.0,
        -0.10,
        "○ 已映射    △ 单位假设未独立记录    × 不可用",
        transform=ax_a.transAxes,
        fontsize=7.0,
        color=TEXT,
    )

    _panel(ax_b, "b", "24 h 历史门禁与外部可用窗口")
    ext = external["external"]
    protocol = external["protocol"]
    required = protocol["required_history_seconds"]
    minimum = ext["pre_event_seconds"]["minimum"]
    median = ext["pre_event_seconds"]["median"]
    maximum = ext["pre_event_seconds"]["maximum"]
    ax_b.set_xscale("log")
    ax_b.hlines(1, minimum, maximum, color="#2F6F9F", linewidth=9, alpha=0.68)
    ax_b.scatter([median], [1], color="white", edgecolor=TEXT, s=42, zorder=3)
    ax_b.vlines(required, 0.55, 1.45, color=RED, linewidth=1.4)
    ax_b.text(
        required,
        1.48,
        "冻结要求\n86,400 s（24 h）",
        ha="center",
        va="bottom",
        fontsize=7.2,
        color=RED,
        fontweight="bold",
    )
    ax_b.text(
        maximum,
        0.72,
        f"外部范围 {minimum:,}–{maximum:,} s\n中位数 {median:,.0f} s",
        ha="right",
        fontsize=7.2,
        color=TEXT,
    )
    ax_b.set_xlim(250, 130000)
    ax_b.set_ylim(0.45, 1.65)
    ax_b.set_yticks([1], ["125 个案例"])
    ax_b.set_xlabel("注入前历史长度（秒，对数轴）")
    ax_b.grid(axis="x", color=GRID, linewidth=0.45, which="both")
    ax_b.spines["top"].set_visible(False)
    ax_b.spines["right"].set_visible(False)
    ax_b.text(
        0.0,
        -0.23,
        (
            f"外部覆盖中位数 {ext['history_coverage']['median']:.2%}；"
            f"内部历史不足卡中位数 {external['internal_insufficient_reference']['history_coverage']['median']:.1%}。\n"
            "外部窗口远短于冻结要求，且未缩短治理门禁。"
        ),
        transform=ax_b.transAxes,
        fontsize=7.0,
        color=TEXT,
        va="top",
    )

    _panel(ax_c, "c", "适配器到拒绝治理的完整链")
    ax_c.set_xlim(0, 1)
    ax_c.set_ylim(0, 1)
    ax_c.axis("off")
    flow = [
        (0.02, 0.67, 0.16, "125\n案例", LIGHT_BLUE),
        (0.22, 0.67, 0.16, "10\n服务", LIGHT_BLUE),
        (0.42, 0.67, 0.18, "44,980\n观测", LIGHT_BLUE),
        (0.64, 0.67, 0.16, "1,250\n治理卡", LIGHT_ORANGE),
        (0.84, 0.67, 0.14, "1,250/1,250\n历史不足", "#F2DEDE"),
    ]
    for x, y, width, label, fc in flow:
        _rect_text(
            ax_c,
            (x, y),
            width,
            0.16,
            label,
            fc=fc,
            ec=RED if "历史" in label else TEXT,
            fontsize=7.6,
            ha="center",
            fontweight="bold",
        )
    for left, right in zip(flow[:-1], flow[1:]):
        _arrow(
            ax_c,
            (left[0] + left[2], 0.75),
            (right[0], 0.75),
            color=TEXT,
        )
    _rect_text(
        ax_c,
        (0.18, 0.40),
        0.64,
        0.13,
        "history_window_shortened = false  →  拒绝操作化",
        fc="#FFF5F5",
        ec=RED,
        fontsize=8.0,
        color=RED,
        ha="center",
        fontweight="bold",
    )
    _rect_text(
        ax_c,
        (0.08, 0.12),
        0.84,
        0.18,
        (
            "支持：适配器与拒绝治理流程可迁移\n"
            "不支持：阈值正确率或内部预测协议的外部验证"
        ),
        fc="white",
        ec="#8E989E",
        fontsize=7.6,
        ha="center",
    )
    return _save_figure(fig, output, "figure_05_external_refusal_governance")


def figure_06(
    prediction: dict[str, Any],
    paired: dict[str, Any],
    topology: dict[str, Any],
    output: Path,
) -> list[Path]:
    fig = plt.figure(figsize=(12.8, 8.5))
    gs = fig.add_gridspec(2, 2, wspace=0.34, hspace=0.42)
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])
    ax_c = fig.add_subplot(gs[1, 0])
    ax_d = fig.add_subplot(gs[1, 1])

    models = [
        "category_prior",
        "persistence",
        "app_logistic",
        "app_hgb",
        "app_infra_logistic",
        "app_infra_topology_logistic",
    ]
    labels = [
        "类别先验",
        "持续性",
        "应用 Logistic",
        "应用 HGB",
        "应用+基础设施",
        "应用+基础设施+拓扑",
    ]
    values = [prediction["overall"][m]["pr_auc"] for m in models]
    colors = ["#B8BFC4", "#8EB6D8", "#2F6F9F", "#1F5A85", "#D6A23D", "#A87558"]
    hatches = ["//", "..", "\\\\", "xx", "..", "//"]
    _panel(ax_a, "a", "六模型 OOF PR-AUC")
    y = np.arange(len(models))
    bars = ax_a.barh(
        y,
        values,
        color=colors,
        edgecolor=TEXT,
        linewidth=0.6,
        hatch=hatches,
        zorder=2,
    )
    ax_a.set_yticks(y, labels)
    ax_a.invert_yaxis()
    ax_a.set_xlabel("PR-AUC")
    ax_a.set_xlim(0, 0.88)
    for bar, value in zip(bars, values):
        ax_a.text(value + 0.012, bar.get_y() + bar.get_height() / 2, f"{value:.3f}", va="center", fontsize=7.3)
    ax_a.grid(axis="x", color=GRID, linewidth=0.45)
    ax_a.spines["top"].set_visible(False)
    ax_a.spines["right"].set_visible(False)
    ax_a.text(
        0.98,
        0.03,
        "209 事件；每模型 2,226 个 event-app OOF 对",
        transform=ax_a.transAxes,
        ha="right",
        fontsize=7.0,
        color=TEXT,
    )

    _panel(ax_b, "b", "事件级配对 bootstrap 的 PR-AUC 差值")
    boot = paired["paired_event_bootstrap"]
    comp_labels = [
        "应用 Logistic − 类别先验",
        "应用 Logistic − 持续性",
        "应用+基础设施 − 应用",
        "应用+基础设施+拓扑 − 应用+基础设施",
    ]
    obs = np.array([row["metrics"]["pr_auc"]["observed_delta"] for row in boot])
    low = np.array([row["metrics"]["pr_auc"]["ci95_low"] for row in boot])
    high = np.array([row["metrics"]["pr_auc"]["ci95_high"] for row in boot])
    y = np.arange(len(boot))
    ax_b.axvline(0, color=RED, linestyle="--", linewidth=0.9)
    ax_b.errorbar(
        obs,
        y,
        xerr=[obs - low, high - obs],
        fmt="o",
        color="#2F6F9F",
        ecolor="#5B6972",
        elinewidth=1.0,
        capsize=3,
        markersize=5,
        zorder=3,
    )
    ax_b.set_yticks(y, comp_labels)
    ax_b.invert_yaxis()
    ax_b.set_xlabel("配对 PR-AUC 差值（95% CI）")
    ax_b.set_xlim(-0.06, 0.70)
    ax_b.grid(axis="x", color=GRID, linewidth=0.45)
    ax_b.spines["top"].set_visible(False)
    ax_b.spines["right"].set_visible(False)
    for yi, value, lo, hi in zip(y, obs, low, high):
        ax_b.text(
            min(hi + 0.015, 0.67),
            yi,
            f"{value:+.3f} [{lo:+.3f}, {hi:+.3f}]",
            va="center",
            fontsize=6.8,
        )
    ax_b.text(
        0.99,
        0.03,
        "重采样单位 event_id；2,000 次",
        transform=ax_b.transAxes,
        ha="right",
        fontsize=7.0,
    )

    _panel(ax_c, "c", "折级稳定性与开发期拓扑方向门禁")
    fold_ids = list(range(5))
    hgb = [prediction["by_fold"]["app_hgb"][str(i)]["pr_auc"] for i in fold_ids]
    topo_folds = topology["development_residual_informativeness"]["folds"]
    topo_delta = [row["pr_auc_delta"] for row in topo_folds]
    ax_c.plot(
        fold_ids,
        hgb,
        color="#1F5A85",
        marker="o",
        linewidth=1.3,
        markersize=5,
        label="应用 HGB PR-AUC",
    )
    for x, value in zip(fold_ids, hgb):
        ax_c.text(x, value + 0.008, f"{value:.3f}", ha="center", fontsize=6.8, color="#1F5A85")
    ax_c.set_xticks(fold_ids, [f"折 {i + 1}" for i in fold_ids])
    ax_c.set_ylabel("应用 HGB PR-AUC", color="#1F5A85")
    ax_c.set_ylim(0.68, 0.88)
    ax_c.tick_params(axis="y", labelcolor="#1F5A85")
    ax_c.grid(axis="y", color=GRID, linewidth=0.45)
    ax_c2 = ax_c.twinx()
    ax_c2.axhline(0, color=RED, linestyle="--", linewidth=0.8)
    ax_c2.plot(
        fold_ids,
        topo_delta,
        color="#8A5DA2",
        marker="D",
        linestyle=":",
        linewidth=1.2,
        markersize=4,
        label="拓扑残差信息增量",
    )
    for x, value in zip(fold_ids, topo_delta):
        ax_c2.text(x, value - 0.0010, f"{value:+.4f}", ha="center", va="top", fontsize=6.3, color="#6F4783")
    ax_c2.set_ylabel("拓扑 ΔPR-AUC", color="#8A5DA2")
    ax_c2.set_ylim(-0.0145, 0.007)
    ax_c2.tick_params(axis="y", labelcolor="#8A5DA2")
    ax_c.spines["top"].set_visible(False)
    ax_c2.spines["top"].set_visible(False)
    handles = [
        Line2D([0], [0], color="#1F5A85", marker="o", label="应用 HGB PR-AUC"),
        Line2D([0], [0], color="#8A5DA2", marker="D", linestyle=":", label="拓扑 ΔPR-AUC"),
    ]
    ax_c.legend(handles=handles, loc="lower left", frameon=False)
    ax_c.text(
        0.99,
        0.04,
        "拓扑仅 1/5 折为正；门禁要求 ≥4/5",
        transform=ax_c.transAxes,
        ha="right",
        fontsize=7.0,
        color=RED,
    )

    _panel(ax_d, "d", "应用 HGB 的 10 等宽箱可靠性")
    cal = prediction["calibration_10_equal_width"]["app_hgb"]
    prob = np.array([row["mean_probability"] for row in cal])
    rate = np.array([row["observed_rate"] for row in cal])
    n = np.array([row["n"] for row in cal])
    ax_d.plot([0, 1], [0, 1], color=RED, linestyle="--", linewidth=0.9, label="理想校准")
    ax_d.plot(prob, rate, color="#2F6F9F", linewidth=1.0, alpha=0.7)
    ax_d.scatter(
        prob,
        rate,
        s=24 + 0.16 * np.sqrt(n) * 10,
        color="#4D86B3",
        edgecolor=TEXT,
        linewidth=0.55,
        alpha=0.85,
        zorder=3,
    )
    for x, yv, count in zip(prob, rate, n):
        if count >= 100:
            ax_d.text(x, yv + 0.045, f"n={count}", ha="center", fontsize=6.2)
    ax_d.set_xlim(-0.02, 1.02)
    ax_d.set_ylim(-0.02, 1.02)
    ax_d.set_xlabel("分箱内平均预测概率")
    ax_d.set_ylabel("分箱内观测率")
    ax_d.grid(color=GRID, linewidth=0.45)
    ax_d.spines["top"].set_visible(False)
    ax_d.spines["right"].set_visible(False)
    overall = prediction["overall"]["app_hgb"]
    ax_d.text(
        0.04,
        0.94,
        f"Brier={overall['brier']:.4f}\nECE={overall['ece_10_equal_width']:.4f}",
        transform=ax_d.transAxes,
        va="top",
        fontsize=7.2,
        color=TEXT,
    )
    return _save_figure(fig, output, "figure_06_prediction_stability_and_scope")


def _contact_sheet(
    image_paths: Iterable[Path],
    output: Path,
    filename: str,
    *,
    columns: int = 2,
) -> Path:
    paths = list(image_paths)
    cell_w, cell_h = 900, 620
    rows = (len(paths) + columns - 1) // columns
    canvas = Image.new("RGB", (columns * cell_w, rows * cell_h), "white")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
    for index, path in enumerate(paths):
        with Image.open(path) as image:
            thumb = ImageOps.contain(image.convert("RGB"), (cell_w - 30, cell_h - 70))
        col, row = index % columns, index // columns
        x = col * cell_w + (cell_w - thumb.width) // 2
        y = row * cell_h + 50 + (cell_h - 70 - thumb.height) // 2
        canvas.paste(thumb, (x, y))
        draw.text((col * cell_w + 14, row * cell_h + 12), path.stem, fill=TEXT, font=font)
        draw.rectangle(
            (
                col * cell_w,
                row * cell_h,
                (col + 1) * cell_w - 1,
                (row + 1) * cell_h - 1,
            ),
            outline="#C7CDD1",
            width=1,
        )
    path = output / filename
    canvas.save(path, dpi=(150, 150))
    return path


def generate(repo: Path, output: Path) -> dict[str, Any]:
    _style()
    output.mkdir(parents=True, exist_ok=True)
    analysis = repo / "checkpoints/runtime_nfr_v3_academic/phase2_analysis_v1"
    paper = repo / "checkpoints/runtime_nfr_v3_academic/paper_artifacts"
    feature = repo / "checkpoints/runtime_nfr_v3_academic/feature_ablation"

    inputs = {
        "representative_cases": paper / "representative_cases.json",
        "threshold_only": analysis / "threshold_only_comparison.json",
        "governance_strata": analysis / "governance_strata_and_workload.json",
        "governance_ablation": analysis / "governance_rule_ablation.json",
        "expert_raw": analysis / "expert_paired_analysis.csv",
        "expert_per_card": analysis / "expert_paired_per_card.csv",
        "expert_summary": analysis / "expert_paired_analysis.json",
        "external": analysis / "external_protocol_compatibility.json",
        "prediction": analysis / "prediction_stability_and_calibration.json",
        "paired_bootstrap": feature / "paired_bootstrap.json",
        "topology": analysis / "topology_negative_result_scope.json",
    }
    original_figures = [
        paper / "figure_01_research_chain.png",
        paper / "figure_02_governance_overview.png",
        paper / "figure_03_tail_sensitivity.png",
        paper / "figure_04_expert_distinction.png",
        paper / "figure_05_external_transfer.png",
        paper / "figure_06_prediction_ablation.png",
    ]
    missing = [str(path) for path in inputs.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing frozen inputs:\n" + "\n".join(missing))

    cases = _load_json(inputs["representative_cases"])
    threshold = _load_json(inputs["threshold_only"])
    governance = _load_json(inputs["governance_strata"])
    ablation = _load_json(inputs["governance_ablation"])
    expert_raw = pd.read_csv(inputs["expert_raw"])
    expert_per_card = pd.read_csv(inputs["expert_per_card"])
    expert_summary = _load_json(inputs["expert_summary"])
    external = _load_json(inputs["external"])
    prediction = _load_json(inputs["prediction"])
    paired = _load_json(inputs["paired_bootstrap"])
    topology = _load_json(inputs["topology"])

    assert sum(threshold["governance_counts"].values()) == 8668
    assert len(expert_raw) == 144
    assert len(expert_per_card) == 48
    assert external["external"]["cards"] == 1250
    assert prediction["population"]["events"] == 209

    generated: list[Path] = []
    generated.extend(figure_01(cases, threshold, output))
    generated.extend(figure_02(governance, ablation, threshold, output))
    generated.extend(figure_03(cases, ablation, output))
    generated.extend(figure_04(expert_raw, expert_per_card, expert_summary, output))
    generated.extend(figure_05(external, output))
    generated.extend(figure_06(prediction, paired, topology, output))

    color_pngs = sorted(
        path
        for path in generated
        if path.suffix == ".png" and "qa_grayscale" not in path.parts
    )
    contact = _contact_sheet(color_pngs, output, "CONTACT_SHEET_COLOR.png")
    gray_pngs = sorted((output / "qa_grayscale").glob("*_gray.png"))
    gray_contact = _contact_sheet(
        gray_pngs,
        output,
        "CONTACT_SHEET_GRAYSCALE.png",
    )
    generated.extend([contact, gray_contact])
    if all(path.is_file() for path in original_figures):
        comparison_paths: list[Path] = []
        for old_path, new_path in zip(original_figures, color_pngs):
            comparison_paths.extend([old_path, new_path])
        comparison = _contact_sheet(
            comparison_paths,
            output,
            "COMPARISON_OLD_VS_NEW.png",
            columns=2,
        )
        generated.append(comparison)
    concept_draft = output / "figure_01_evidence_aware_governance_concept_draft.png"
    if concept_draft.is_file():
        generated.append(concept_draft)

    manifest = {
        "protocol": "runtime-nfr-jos-figures/1",
        "description": (
            "Second-generation JOS-oriented figures generated from frozen "
            "paper artifacts and Phase-2 analysis products."
        ),
        "preserves_original_figures": True,
        "claims": {
            "F01": "same or close candidate values can require different evidence-governance actions",
            "F02": "auditable rules and priority produce four mutually exclusive governance states",
            "F03": "10.0 is a frozen context-review trigger; sensitivity is not cutoff selection",
            "F04": "experts distinguish threshold plausibility from governance appropriateness",
            "F05": "external evidence supports adapter/refusal portability, not threshold correctness",
            "F06": "application telemetry is supportive; current infrastructure/topology adds no stable increment",
        },
        "inputs": {name: _file_record(path, repo) for name, path in inputs.items()},
        "outputs": [_file_record(path, repo) for path in generated],
    }
    manifest_path = output / "FIGURE_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "checkpoints/runtime_nfr_v3_academic/phase2_figures_jos_v1"
        ),
    )
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = args.output
    if not output.is_absolute():
        output = repo / output
    manifest = generate(repo, output.resolve())
    print(
        json.dumps(
            {
                "output": str(output.resolve()),
                "input_count": len(manifest["inputs"]),
                "output_count": len(manifest["outputs"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
