"""Generate readability-first Runtime NFR figures at the final 175 mm width.

This module reuses the frozen-data loader, output manifest, and multi-format
writer from ``generate_runtime_nfr_jos_figures.py`` while replacing all six
plot functions.  The figures are drawn at their intended publication width
(175 mm = 6.89 in), with 7--9 pt body text and fewer, non-redundant panels.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from matplotlib import patches
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd

import generate_runtime_nfr_jos_figures as base


FINAL_WIDTH_IN = 175 / 25.4
TEXT = "#273238"
MUTED = "#66747C"
GRID = "#D8DEE2"
BLUE = "#2F6F9F"
ORANGE = "#D28E00"
PURPLE = "#835A9B"
GRAY = "#747B80"
GREEN = "#3B806C"
RED = "#A34545"
LIGHT_BLUE = "#E7F0F6"
LIGHT_ORANGE = "#FAEFD2"
LIGHT_PURPLE = "#F0E8F4"
LIGHT_GRAY = "#EFF1F2"
STATUS_ORDER = base.STATUS_ORDER
STATUS_LABELS = {
    "insufficient_evidence": "历史不足",
    "threshold_unresolved": "阈值未解析",
    "needs_context_review": "上下文审查",
    "candidate_for_stakeholder_review": "转交确认",
}
STATUS_COLORS = {
    "insufficient_evidence": GRAY,
    "threshold_unresolved": ORANGE,
    "needs_context_review": PURPLE,
    "candidate_for_stakeholder_review": BLUE,
}


def _final_style() -> None:
    base._style()
    plt.rcParams.update(
        {
            "font.size": 8.0,
            "axes.titlesize": 8.8,
            "axes.labelsize": 8.0,
            "xtick.labelsize": 7.2,
            "ytick.labelsize": 7.2,
            "legend.fontsize": 7.1,
            "axes.edgecolor": TEXT,
            "text.color": TEXT,
            "axes.labelcolor": TEXT,
            "xtick.color": TEXT,
            "ytick.color": TEXT,
        }
    )


def _panel_title(ax: plt.Axes, letter: str, title: str) -> None:
    ax.text(
        -0.04,
        1.04,
        letter,
        transform=ax.transAxes,
        fontsize=9.2,
        fontweight="bold",
        va="bottom",
        clip_on=False,
    )
    ax.set_title(title, loc="left", pad=6, fontweight="bold")


def _clean(ax: plt.Axes, *, grid_axis: str | None = None) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if grid_axis:
        ax.grid(
            axis=grid_axis,
            color=GRID,
            linewidth=0.45,
            zorder=0,
        )


def _save(fig: plt.Figure, output: Path, stem: str) -> list[Path]:
    fig.set_facecolor("white")
    return base._save_figure(fig, output, stem)


def _box(
    ax: plt.Axes,
    x: float,
    y: float,
    w: float,
    h: float,
    text: str,
    *,
    face: str = "white",
    edge: str = GRID,
    color: str = TEXT,
    size: float = 7.2,
    weight: str = "normal",
    align: str = "left",
) -> None:
    rect = patches.FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.006,rounding_size=0.008",
        facecolor=face,
        edgecolor=edge,
        linewidth=0.8,
    )
    ax.add_patch(rect)
    tx = x + 0.012 if align == "left" else x + w / 2
    ax.text(
        tx,
        y + h / 2,
        text,
        ha=align,
        va="center",
        fontsize=size,
        fontweight=weight,
        linespacing=1.22,
        color=color,
    )


def figure_01(
    cases_payload: dict[str, Any],
    threshold_payload: dict[str, Any],
    output: Path,
) -> list[Path]:
    cases = cases_payload["cases"]
    rows = [
        (
            cases["insufficient_evidence"],
            "error_ratio = 0\n覆盖 25.0%，n=360",
            "24 h 历史不足",
            "补充历史后再操作化",
        ),
        (
            cases["threshold_unresolved"],
            "error_ratio = 0\n覆盖 99.9%，n=1,439",
            "零错误基线；分辨率不足",
            "补参考目标或测量分辨率",
        ),
        (
            cases["needs_context_review"],
            "latency = 3,423.2 ms\n长尾比 = 10.035",
            "长尾比达到冻结触发器 10.0",
            "审查工作负载与尾部分布",
        ),
        (
            cases["candidate_for_stakeholder_review"],
            "latency = 3,428.6 ms\n长尾比 = 9.997",
            "未触发三类证据门禁",
            "转交利益相关者确认",
        ),
    ]

    fig, ax = plt.subplots(figsize=(FINAL_WIDTH_IN, 3.62))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    headers = [("候选边界", 0.02), ("证据审计", 0.35), ("治理动作", 0.68)]
    widths = [0.29, 0.29, 0.30]
    for (label, x), width in zip(headers, widths):
        ax.text(x, 0.945, label, fontsize=8.4, fontweight="bold", va="bottom")
        ax.plot([x, x + width], [0.928, 0.928], color=TEXT, lw=0.9)

    y_positions = [0.735, 0.545, 0.355, 0.165]
    for (case, candidate, audit, action), y in zip(rows, y_positions):
        status = case["evidence_status"]
        color = STATUS_COLORS[status]
        _box(ax, 0.02, y, 0.29, 0.145, candidate, face="white", edge=GRID)
        _box(
            ax,
            0.35,
            y,
            0.29,
            0.145,
            audit,
            face={
                "insufficient_evidence": LIGHT_GRAY,
                "threshold_unresolved": LIGHT_ORANGE,
                "needs_context_review": LIGHT_PURPLE,
                "candidate_for_stakeholder_review": LIGHT_BLUE,
            }[status],
            edge=color,
        )
        _box(
            ax,
            0.68,
            y,
            0.30,
            0.145,
            action,
            face="white",
            edge=color,
            color=color,
            weight="bold",
        )
        for x0, x1 in ((0.31, 0.35), (0.64, 0.68)):
            ax.annotate(
                "",
                xy=(x1 - 0.005, y + 0.0725),
                xytext=(x0 + 0.005, y + 0.0725),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=0.9),
            )
        ax.add_patch(
            patches.Rectangle(
                (0.007, y),
                0.006,
                0.145,
                facecolor=color,
                edgecolor="none",
            )
        )

    hidden = threshold_payload["evidence_actions_hidden_by_minimal_baseline"]
    ax.text(
        0.02,
        0.035,
        (
            f"全集：8,668 张候选卡；只输出数值会隐藏 "
            f"{hidden['count']:,} 张（{hidden['rate']:.1%}）卡的证据或动作信息。"
        ),
        fontsize=7.5,
        fontweight="bold",
        va="center",
    )
    ax.text(
        0.98,
        0.035,
        "候选边界 ≠ 正式 SLO",
        fontsize=7.5,
        color=RED,
        fontweight="bold",
        ha="right",
        va="center",
    )
    fig.subplots_adjust(left=0.025, right=0.99, top=0.98, bottom=0.03)
    return _save(fig, output, "figure_01_evidence_aware_governance")


def figure_02(
    governance: dict[str, Any],
    ablation: dict[str, Any],
    threshold: dict[str, Any],
    output: Path,
) -> list[Path]:
    fig = plt.figure(figsize=(FINAL_WIDTH_IN, 4.45))
    gs = fig.add_gridspec(
        2,
        2,
        height_ratios=[1.25, 1],
        width_ratios=[0.94, 1.06],
        hspace=0.58,
        wspace=0.48,
    )
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])
    ax_c = fig.add_subplot(gs[1, :])

    counts = threshold["governance_counts"]
    labels = [STATUS_LABELS[s] for s in STATUS_ORDER]
    values = [counts[s] for s in STATUS_ORDER]
    colors = [STATUS_COLORS[s] for s in STATUS_ORDER]
    y = np.arange(4)
    ax_a.barh(y, values, color=colors, height=0.58, zorder=2)
    ax_a.set_yticks(y, labels)
    ax_a.invert_yaxis()
    ax_a.set_xlabel("候选卡数")
    ax_a.set_xlim(0, 4700)
    for yi, value in zip(y, values):
        ax_a.text(
            value + 80,
            yi,
            f"{value:,}\n({value / 8668:.1%})",
            va="center",
            fontsize=7.0,
        )
    _clean(ax_a, grid_axis="x")
    _panel_title(ax_a, "a", "互斥治理状态")

    sli_rows = {row["sli"]: row for row in governance["strata"]["sli"]}
    sli_labels = ["错误率", "延迟"]
    sli_keys = ["error_ratio", "latency"]
    left = np.zeros(2)
    for status in STATUS_ORDER:
        vals = np.array([sli_rows[k][status] for k in sli_keys])
        ax_b.barh(
            np.arange(2),
            vals,
            left=left,
            height=0.5,
            color=STATUS_COLORS[status],
            label=STATUS_LABELS[status],
        )
        for yi, (start, val) in enumerate(zip(left, vals)):
            if val >= 350:
                ax_b.text(
                    start + val / 2,
                    yi,
                    f"{val:,}",
                    ha="center",
                    va="center",
                    fontsize=7.0,
                    color="white" if status != "threshold_unresolved" else TEXT,
                    fontweight="bold",
                )
        left += vals
    ax_b.set_yticks(np.arange(2), sli_labels)
    ax_b.invert_yaxis()
    ax_b.set_xlim(0, 4334)
    ax_b.set_xlabel("每类 SLI 均为 4,334 张")
    _clean(ax_b, grid_axis="x")
    _panel_title(ax_b, "b", "状态依赖于 SLI 与证据缺口")
    handles = [
        patches.Patch(color=STATUS_COLORS[s], label=STATUS_LABELS[s])
        for s in STATUS_ORDER
    ]
    ax_b.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.30),
        ncol=2,
        frameon=False,
        columnspacing=1.0,
        handlelength=1.1,
    )

    scenarios = [
        ("删除历史门禁", "drop_history"),
        ("删除分辨率门禁", "drop_resolution"),
        ("删除长尾门禁", "drop_tail"),
        ("交换历史/分辨率", "swap_history_resolution"),
        ("交换分辨率/长尾", "swap_resolution_tail"),
        ("长尾触发器 9.5", "tail_cutoff_9.5"),
        ("长尾触发器 10.5", "tail_cutoff_10.5"),
    ]
    changed = [ablation["scenarios"][key]["changed_cards"] for _, key in scenarios]
    xpos = np.arange(len(scenarios))
    ax_c.vlines(xpos, 0, changed, color=GRID, lw=2.0, zorder=1)
    ax_c.scatter(
        xpos,
        changed,
        s=42,
        c=[GRAY, ORANGE, PURPLE, BLUE, GREEN, MUTED, MUTED],
        zorder=3,
        edgecolor="white",
        linewidth=0.6,
    )
    for x, val in zip(xpos, changed):
        ax_c.text(x, val + 100, f"{val:,}", ha="center", va="bottom", fontsize=7.1)
    ax_c.set_xticks(
        xpos,
        [
            "删历史",
            "删分辨率",
            "删长尾",
            "换历史/\n分辨率",
            "换分辨率/\n长尾",
            "尾比 9.5",
            "尾比 10.5",
        ],
    )
    ax_c.set_ylabel("改变状态的卡数")
    ax_c.set_ylim(0, 2850)
    _clean(ax_c, grid_axis="y")
    _panel_title(ax_c, "c", "反事实规则审计（不用于重选规则）")
    fig.subplots_adjust(left=0.105, right=0.985, top=0.95, bottom=0.11)
    return _save(fig, output, "figure_02_governance_rules_and_strata")


def figure_03(
    cases_payload: dict[str, Any],
    ablation: dict[str, Any],
    output: Path,
) -> list[Path]:
    cases = cases_payload["cases"]
    context = cases["needs_context_review"]
    candidate = cases["candidate_for_stakeholder_review"]

    fig, (ax_a, ax_b) = plt.subplots(
        1,
        2,
        figsize=(FINAL_WIDTH_IN, 2.72),
        gridspec_kw={"width_ratios": [1.25, 0.9], "wspace": 0.42},
    )
    _panel_title(ax_a, "a", "冻结触发器两侧的代表性案例")
    ax_a.axvspan(9.96, 10.0, color=LIGHT_BLUE, alpha=0.8, zorder=0)
    ax_a.axvspan(10.0, 10.07, color=LIGHT_PURPLE, alpha=0.8, zorder=0)
    ax_a.axvline(10.0, color=TEXT, lw=1.0, ls="--")
    points = [
        (
            candidate["latency_tail_ratio"],
            1,
            BLUE,
            f"转交确认\n9.997；3,428.6 ms",
        ),
        (
            context["latency_tail_ratio"],
            0,
            PURPLE,
            f"上下文审查\n10.035；3,423.2 ms",
        ),
    ]
    for x, y, color, label in points:
        ax_a.scatter(x, y, s=55, color=color, edgecolor="white", lw=0.7, zorder=3)
        ax_a.text(
            x + (0.002 if x < 10 else -0.002),
            y + 0.13,
            label,
            ha="left" if x < 10 else "right",
            va="bottom",
            fontsize=7.2,
            color=color,
            fontweight="bold",
        )
    ax_a.text(10.0, 1.55, "10.0", ha="center", va="bottom", fontsize=7.3)
    ax_a.set_xlim(9.96, 10.07)
    ax_a.set_ylim(-0.35, 1.75)
    ax_a.set_yticks([])
    ax_a.set_xlabel("延迟长尾比")
    _clean(ax_a, grid_axis="x")

    _panel_title(ax_b, "b", "触发器敏感性")
    cutoffs = [9.5, 10.0, 10.5]
    changed = [
        ablation["scenarios"]["tail_cutoff_9.5"]["changed_cards"],
        0,
        ablation["scenarios"]["tail_cutoff_10.5"]["changed_cards"],
    ]
    colors = [MUTED, TEXT, MUTED]
    ax_b.bar(cutoffs, changed, width=0.22, color=colors, zorder=2)
    for x, val in zip(cutoffs, changed):
        ax_b.text(x, val + 0.8, str(val), ha="center", va="bottom", fontsize=7.5)
    ax_b.set_xticks(cutoffs, ["9.5\n反事实", "10.0\n冻结", "10.5\n反事实"])
    ax_b.set_ylabel("改变状态的卡数")
    ax_b.set_ylim(0, 23)
    _clean(ax_b, grid_axis="y")
    ax_b.text(
        0.98,
        0.96,
        "触发器不是自然真值",
        transform=ax_b.transAxes,
        ha="right",
        va="top",
        fontsize=7.2,
        color=RED,
        fontweight="bold",
    )
    fig.subplots_adjust(left=0.08, right=0.985, top=0.90, bottom=0.22)
    return _save(fig, output, "figure_03_tail_boundary_and_sensitivity")


def figure_04(
    raw: pd.DataFrame,
    per_card: pd.DataFrame,
    summary: dict[str, Any],
    output: Path,
) -> list[Path]:
    fig, (ax_a, ax_b) = plt.subplots(
        1,
        2,
        figsize=(FINAL_WIDTH_IN, 3.35),
        gridspec_kw={"width_ratios": [1.12, 0.88], "wspace": 0.48},
    )

    _panel_title(ax_a, "a", "48 张卡的配对中位数")
    ordered = per_card.sort_values(
        ["threshold_plausibility_median", "governance_appropriateness_median"]
    ).reset_index(drop=True)
    y = np.arange(len(ordered))
    x0 = ordered["threshold_plausibility_median"].to_numpy()
    x1 = ordered["governance_appropriateness_median"].to_numpy()
    for yi, low, high in zip(y, x0, x1):
        ax_a.plot([low, high], [yi, yi], color="#AAB5BB", lw=0.65, zorder=1)
    ax_a.scatter(x0, y, s=15, color=ORANGE, label="阈值可信度", zorder=2)
    ax_a.scatter(x1, y, s=15, color=BLUE, label="治理适当性", zorder=2)
    ax_a.set_xlim(0.7, 5.3)
    ax_a.set_xticks([1, 2, 3, 4, 5])
    ax_a.set_ylim(-2, len(ordered) + 1)
    ax_a.set_yticks([0, 11, 23, 35, 47], ["1", "12", "24", "36", "48"])
    ax_a.set_xlabel("专家评分（1–5）")
    ax_a.set_ylabel("按阈值评分排序的卡")
    ax_a.legend(loc="lower right", frameon=False)
    _clean(ax_a, grid_axis="x")

    _panel_title(ax_b, "b", "专家—卡配对频数（n=144）")
    matrix = pd.crosstab(
        raw["governance_appropriateness"],
        raw["threshold_plausibility"],
    ).reindex(index=range(1, 6), columns=range(1, 6), fill_value=0)
    image = ax_b.imshow(
        matrix.to_numpy(),
        origin="lower",
        cmap="Blues",
        vmin=0,
        vmax=max(1, int(matrix.to_numpy().max())),
        aspect="equal",
    )
    max_count = int(matrix.to_numpy().max())
    for row in range(5):
        for col in range(5):
            value = int(matrix.iloc[row, col])
            if value:
                ax_b.text(
                    col,
                    row,
                    str(value),
                    ha="center",
                    va="center",
                    fontsize=8.2,
                    color="white" if value > max_count * 0.48 else TEXT,
                    fontweight="bold",
                )
    ax_b.plot([-0.5, 4.5], [-0.5, 4.5], color=RED, ls="--", lw=0.9)
    ax_b.set_xticks(range(5), range(1, 6))
    ax_b.set_yticks(range(5), range(1, 6))
    ax_b.set_xlabel("阈值可信度")
    ax_b.set_ylabel("治理适当性")
    fig.subplots_adjust(left=0.09, right=0.985, top=0.88, bottom=0.17)
    return _save(fig, output, "figure_04_expert_paired_distinction")


def figure_05(external: dict[str, Any], output: Path) -> list[Path]:
    fig = plt.figure(figsize=(FINAL_WIDTH_IN, 3.45))
    gs = fig.add_gridspec(
        1,
        3,
        width_ratios=[1.0, 1.05, 0.95],
        wspace=0.48,
    )
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])
    ax_c = fig.add_subplot(gs[0, 2])

    ax_a.axis("off")
    ax_a.set_xlim(0, 1)
    ax_a.set_ylim(0, 1)
    ax_a.text(
        -0.08,
        1.02,
        "a",
        transform=ax_a.transAxes,
        fontsize=9.2,
        fontweight="bold",
        va="bottom",
        clip_on=False,
    )
    ax_a.text(
        0.0,
        1.02,
        "外部字段兼容性",
        transform=ax_a.transAxes,
        fontsize=8.8,
        fontweight="bold",
        va="bottom",
    )
    mapping_rows = [
        ("事件时间", "可映射", GREEN),
        ("服务与延迟*", "可映射", GREEN),
        ("请求计数", "缺失", RED),
        ("错误计数", "缺失", RED),
    ]
    ax_a.text(0.02, 0.84, "规范接口", fontsize=7.4, fontweight="bold")
    ax_a.text(0.73, 0.84, "状态", fontsize=7.4, fontweight="bold", ha="center")
    for idx, (field, state, color) in enumerate(mapping_rows):
        y = 0.70 - idx * 0.17
        ax_a.plot([0.02, 0.96], [y - 0.07, y - 0.07], color=GRID, lw=0.55)
        ax_a.text(0.02, y, field, fontsize=7.4, va="center")
        ax_a.scatter(0.64, y, s=28, color=color, edgecolor="white", lw=0.5)
        ax_a.text(0.73, y, state, fontsize=7.2, va="center", ha="center", color=color)
    ax_a.text(
        0.02,
        0.08,
        "* 秒→毫秒换算未获独立文档确认",
        fontsize=7.0,
        color=MUTED,
        va="bottom",
    )

    _panel_title(ax_b, "b", "可用历史远低于 24 h 门禁")
    ext = external["external"]
    internal = external["internal_insufficient_reference"]
    ax_b.set_xscale("log")
    rows = [
        (
            "RCAEval",
            ext["pre_event_seconds"]["minimum"],
            ext["pre_event_seconds"]["median"],
            ext["pre_event_seconds"]["maximum"],
            PURPLE,
        ),
        (
            "内部历史不足组",
            internal["valid_samples"]["min"] * 60,
            internal["valid_samples"]["median"] * 60,
            internal["valid_samples"]["max"] * 60,
            GRAY,
        ),
    ]
    for yi, (label, lo, med, hi, color) in enumerate(rows):
        ax_b.plot([lo, hi], [yi, yi], color=color, lw=4.5, solid_capstyle="round")
        ax_b.scatter(med, yi, s=34, color="white", edgecolor=color, lw=1.3, zorder=3)
    ax_b.axvline(86400, color=RED, lw=1.2, ls="--")
    ax_b.text(
        86400,
        1.28,
        "24 h",
        color=RED,
        ha="right",
        va="bottom",
        fontsize=7.2,
        fontweight="bold",
    )
    ax_b.set_yticks([0, 1], [rows[0][0], rows[1][0]])
    ax_b.set_xlim(8, 110000)
    ax_b.set_ylim(-0.55, 1.45)
    ax_b.set_xticks([10, 60, 360, 3600, 86400], ["10 s", "1 min", "6 min", "1 h", "24 h"])
    ax_b.set_xlabel("事件前历史长度（对数轴）")
    _clean(ax_b, grid_axis="x")
    ax_b.text(
        0.02,
        0.03,
        "外部中位数 360 s；覆盖中位数 0.49%",
        transform=ax_b.transAxes,
        fontsize=7.0,
        color=PURPLE,
        va="bottom",
    )

    _panel_title(ax_c, "c", "拒绝治理保持冻结门禁")
    ax_c.set_xlim(0, 1)
    ax_c.set_ylim(0, 1)
    ax_c.axis("off")
    boxes = [
        (0.68, "125 个案例\n10 个服务", LIGHT_BLUE, BLUE),
        (0.40, "1,250 张\n治理卡", "white", TEXT),
        (0.12, "1,250 张拒绝\n0 张转交", LIGHT_PURPLE, PURPLE),
    ]
    for y, label, face, edge in boxes:
        _box(
            ax_c,
            0.12,
            y,
            0.76,
            0.16,
            label,
            face=face,
            edge=edge,
            align="center",
            size=7.6,
            weight="bold",
        )
    for y0, y1 in ((0.675, 0.565), (0.395, 0.285)):
        ax_c.annotate(
            "",
            xy=(0.50, y1),
            xytext=(0.50, y0),
            arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=0.9),
        )
    ax_c.text(
        0.50,
        0.03,
        "未缩短 24 h 门禁",
        fontsize=7.1,
        color=RED,
        fontweight="bold",
        ha="center",
    )
    fig.subplots_adjust(left=0.045, right=0.985, top=0.88, bottom=0.17)
    return _save(fig, output, "figure_05_external_refusal_governance")


def figure_06(
    prediction: dict[str, Any],
    paired: dict[str, Any],
    topology: dict[str, Any],
    output: Path,
) -> list[Path]:
    fig = plt.figure(figsize=(FINAL_WIDTH_IN, 4.35))
    gs = fig.add_gridspec(
        2,
        2,
        height_ratios=[1.12, 0.88],
        width_ratios=[0.88, 1.12],
        hspace=0.58,
        wspace=0.72,
    )
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])
    ax_c = fig.add_subplot(gs[1, :])

    overall = prediction["overall"]
    model_order = [
        "category_prior",
        "persistence",
        "app_logistic",
        "app_hgb",
        "app_infra_logistic",
        "app_infra_topology_logistic",
    ]
    model_labels = [
        "类别先验",
        "持续性基线",
        "应用-Logistic",
        "应用-HGB",
        "应用+基础设施",
        "应用+基础设施+拓扑",
    ]
    values = [overall[m]["pr_auc"] for m in model_order]
    colors = [GRAY, MUTED, ORANGE, BLUE, GREEN, PURPLE]
    y = np.arange(len(model_order))
    ax_a.hlines(y, 0, values, color=GRID, lw=1.2, zorder=1)
    ax_a.scatter(values, y, s=35, c=colors, edgecolor="white", lw=0.6, zorder=2)
    for yi, value in zip(y, values):
        ax_a.text(value + 0.018, yi, f"{value:.3f}", va="center", fontsize=7.0)
    ax_a.set_yticks(y, model_labels)
    ax_a.invert_yaxis()
    ax_a.set_xlim(0, 0.9)
    ax_a.set_xlabel("OOF PR-AUC")
    _clean(ax_a, grid_axis="x")
    _panel_title(ax_a, "a", "同一滚动时间协议下的模型信号")

    pairs = paired["paired_event_bootstrap"]
    pair_labels = [
        "应用−类别先验",
        "应用−持续性",
        "+基础设施−应用",
        "+拓扑−基础设施",
    ]
    deltas = []
    lows = []
    highs = []
    for item in pairs:
        metric = item["metrics"]["pr_auc"]
        deltas.append(metric["observed_delta"])
        lows.append(metric["ci95_low"])
        highs.append(metric["ci95_high"])
    yy = np.arange(4)
    ax_b.axvline(0, color=RED, lw=0.9, ls="--")
    for yi, delta, low, high in zip(yy, deltas, lows, highs):
        color = BLUE if low > 0 else GRAY
        ax_b.plot([low, high], [yi, yi], color=color, lw=1.7)
        ax_b.scatter(delta, yi, s=34, color=color, edgecolor="white", lw=0.6, zorder=3)
        ax_b.text(
            high + 0.012,
            yi,
            f"{delta:+.3f}",
            va="center",
            fontsize=7.0,
            color=color,
        )
    ax_b.set_yticks(yy, pair_labels)
    ax_b.tick_params(axis="y", labelsize=7.0)
    ax_b.invert_yaxis()
    ax_b.set_xlim(-0.075, 0.71)
    ax_b.set_xlabel("配对 PR-AUC 差值（95% CI）")
    _clean(ax_b, grid_axis="x")
    _panel_title(ax_b, "b", "事件级 2,000 次配对 bootstrap")

    folds = topology["development_residual_informativeness"]["folds"]
    fold_ids = [row["fold"] for row in folds]
    fold_deltas = [row["pr_auc_delta"] for row in folds]
    fold_colors = [BLUE if value > 0 else GRAY for value in fold_deltas]
    ax_c.axhline(0, color=TEXT, lw=0.8)
    ax_c.bar(fold_ids, fold_deltas, width=0.56, color=fold_colors, zorder=2)
    for x, value in zip(fold_ids, fold_deltas):
        ax_c.text(
            x,
            value + (0.0007 if value >= 0 else -0.0007),
            f"{value:+.3f}",
            ha="center",
            va="bottom" if value >= 0 else "top",
            fontsize=7.0,
        )
    ax_c.set_xticks(fold_ids, [f"折 {x + 1}" for x in fold_ids])
    ax_c.set_ylabel("开发期 PR-AUC 增量")
    ax_c.set_ylim(-0.0145, 0.0065)
    _clean(ax_c, grid_axis="y")
    _panel_title(ax_c, "c", "当前静态/不完整拓扑未通过稳定增量门禁")
    ax_c.text(
        0.99,
        0.92,
        "仅 1/5 折为正；门禁要求 ≥4/5",
        transform=ax_c.transAxes,
        ha="right",
        va="top",
        fontsize=7.3,
        color=RED,
        fontweight="bold",
    )
    fig.subplots_adjust(left=0.13, right=0.985, top=0.93, bottom=0.11)
    return _save(fig, output, "figure_06_prediction_stability_and_scope")


def generate(repo: Path, output: Path) -> dict[str, Any]:
    _final_style()
    base.figure_01 = figure_01
    base.figure_02 = figure_02
    base.figure_03 = figure_03
    base.figure_04 = figure_04
    base.figure_05 = figure_05
    base.figure_06 = figure_06
    manifest = base.generate(repo, output)
    manifest["protocol"] = "runtime-nfr-jos-figures/2"
    manifest["description"] = (
        "Readability-first JOS figures drawn at the final 175 mm width, "
        "with non-redundant panels and a 7 pt minimum body-text target."
    )
    manifest["layout_contract"] = {
        "intended_width_mm": 175,
        "intended_width_in": FINAL_WIDTH_IN,
        "minimum_body_font_pt": 7,
        "primary_format": "SVG",
        "raster_format": "300 dpi PNG",
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
            "checkpoints/runtime_nfr_v3_academic/phase2_figures_jos_v2"
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
