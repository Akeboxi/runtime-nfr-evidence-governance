"""Generate Phase-3 Runtime NFR figures from frozen inputs.

The script preserves the Phase-2 figure bundle and creates a new 175 mm-wide
version.  It adds the Phase-3 transition/stratification evidence, expert
strata, and prediction calibration without changing the frozen rules or
evaluation population.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from matplotlib import patches
import numpy as np
import pandas as pd
from PIL import Image

import generate_runtime_nfr_jos_figures as base
import generate_runtime_nfr_jos_figures_v2 as v2


FINAL_WIDTH_IN = v2.FINAL_WIDTH_IN
TEXT = v2.TEXT
MUTED = v2.MUTED
GRID = v2.GRID
BLUE = v2.BLUE
ORANGE = v2.ORANGE
PURPLE = v2.PURPLE
GRAY = v2.GRAY
GREEN = v2.GREEN
RED = v2.RED
LIGHT_BLUE = v2.LIGHT_BLUE
LIGHT_ORANGE = v2.LIGHT_ORANGE
LIGHT_PURPLE = v2.LIGHT_PURPLE
LIGHT_GRAY = v2.LIGHT_GRAY
STATUS_ORDER = v2.STATUS_ORDER
STATUS_LABELS = v2.STATUS_LABELS
STATUS_COLORS = v2.STATUS_COLORS

_REPO: Path
_PHASE3: dict[str, Any]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _panel_title(ax: plt.Axes, letter: str, title: str) -> None:
    ax.text(
        -0.14,
        1.04,
        letter,
        transform=ax.transAxes,
        fontsize=9.2,
        fontweight="bold",
        va="bottom",
        clip_on=False,
    )
    ax.set_title(title, loc="left", pad=6, fontweight="bold")


def _save_without_pdf(
    fig: plt.Figure,
    output: Path,
    stem: str,
) -> list[Path]:
    """Write editable SVG and PNG previews only during content optimization."""
    written: list[Path] = []
    for ext in ("svg", "png"):
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
    gray_path = gray_dir / f"{stem}_gray.png"
    with Image.open(png) as image:
        image.convert("L").save(gray_path)
    written.append(gray_path)
    return written


def _history_slice(card: dict[str, Any], csv_path: Path, value: str) -> pd.DataFrame:
    raw = pd.read_csv(csv_path, parse_dates=["timestamp"])
    start = pd.Timestamp(card["history_start"])
    end = pd.Timestamp(card["history_end"])
    result = raw.loc[
        (raw["timestamp"] >= start) & (raw["timestamp"] < end),
        ["timestamp", value],
    ].copy()
    if value == "error_ratio":
        result[value] = result[value] / 100.0
    return result


def _find_card(card_id: str) -> dict[str, Any]:
    for row in _PHASE3["cards"]:
        if row["card_id"] == card_id:
            return row
    raise KeyError(card_id)


def _small_trace(
    ax: plt.Axes,
    frame: pd.DataFrame,
    value: str,
    threshold: float,
    *,
    color: str,
    log_scale: bool = False,
) -> None:
    if frame.empty:
        ax.text(0.5, 0.5, "遥测片段不可用", ha="center", va="center", color=MUTED)
        ax.axis("off")
        return
    ax.plot(frame["timestamp"], frame[value], color=color, lw=0.7, alpha=0.72)
    ax.axhline(threshold, color=RED, lw=0.8, ls="--")
    if log_scale:
        positive = frame.loc[frame[value] > 0, value]
        if not positive.empty:
            ax.set_yscale("log")
            ax.set_ylim(max(float(positive.min()) * 0.8, 1.0), float(frame[value].max()) * 1.2)
    ax.set_xlim(frame["timestamp"].min(), frame["timestamp"].max())
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=6.2, length=2)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color=GRID, lw=0.35)


def figure_01(
    cases_payload: dict[str, Any],
    threshold_payload: dict[str, Any],
    output: Path,
) -> list[Path]:
    cases = cases_payload["cases"]
    insufficient = cases["insufficient_evidence"]
    zero_card = _find_card("acad-5b596b2c1d26a4f1")
    candidate_card = _find_card("acad-276a29d905cc7a23")
    tail_card = _find_card("acad-5c1dd41ea3a4b721")
    record = (
        _REPO
        / "dataset/tenant_ccf_0606_0614_seg_0004/records/record_0000/metrics/app"
    )
    zero_trace = _history_slice(zero_card, record / "emailservice.csv", "error_ratio")
    candidate_trace = _history_slice(
        candidate_card, record / "adservice.csv", "error_ratio"
    )
    tail_trace = _history_slice(tail_card, record / "cartservice.csv", "rrt")

    fig = plt.figure(figsize=(FINAL_WIDTH_IN, 5.0))
    gs = fig.add_gridspec(
        5,
        3,
        height_ratios=[0.18, 1, 1, 1, 1],
        width_ratios=[0.28, 0.42, 0.30],
        hspace=0.42,
        wspace=0.34,
    )
    headers = ["候选边界与证据状态", "证据窗口", "治理动作"]
    for col, title in enumerate(headers):
        ax = fig.add_subplot(gs[0, col])
        ax.axis("off")
        ax.text(0, 0.5, title, fontsize=8.4, fontweight="bold", va="center")
        ax.plot([0, 1], [0.12, 0.12], transform=ax.transAxes, color=TEXT, lw=0.8)

    rows = [
        (
            insufficient,
            "error_ratio = 0\n覆盖 25.0%，n=360",
            "补足 24 h 历史后再判断",
            GRAY,
        ),
        (
            zero_card,
            "error_ratio = 0\n覆盖 92.8%，n=1,337",
            "补参考目标或测量分辨率",
            ORANGE,
        ),
        (
            candidate_card,
            "error_ratio = 0.0127\n覆盖 95.6%，n=1,377",
            "转交利益相关者确认",
            BLUE,
        ),
        (
            tail_card,
            "latency = 3,353.4 ms\n长尾比 = 14.25",
            "审查负载与尾部分布",
            PURPLE,
        ),
    ]
    trace_payloads: list[tuple[pd.DataFrame | None, str, float, bool]] = [
        (None, "", 0.0, False),
        (zero_trace, "error_ratio", float(zero_card["quantile_value"]), False),
        (
            candidate_trace,
            "error_ratio",
            float(candidate_card["quantile_value"]),
            False,
        ),
        (tail_trace, "rrt", float(tail_card["quantile_value"]), True),
    ]
    status_faces = {
        "insufficient_evidence": LIGHT_GRAY,
        "threshold_unresolved": LIGHT_ORANGE,
        "needs_context_review": LIGHT_PURPLE,
        "candidate_for_stakeholder_review": LIGHT_BLUE,
    }

    for ridx, ((card, label, action, color), trace_info) in enumerate(
        zip(rows, trace_payloads), start=1
    ):
        left = fig.add_subplot(gs[ridx, 0])
        left.axis("off")
        left.add_patch(
            patches.FancyBboxPatch(
                (0.01, 0.05),
                0.96,
                0.90,
                boxstyle="round,pad=0.012",
                facecolor=status_faces[card["evidence_status"]],
                edgecolor=color,
                linewidth=0.9,
            )
        )
        left.text(0.06, 0.58, label, fontsize=7.3, va="center", linespacing=1.25)
        left.text(
            0.06,
            0.18,
            STATUS_LABELS[card["evidence_status"]],
            fontsize=7.0,
            color=color,
            fontweight="bold",
        )

        middle = fig.add_subplot(gs[ridx, 1])
        frame, value, card_threshold, log_scale = trace_info
        if frame is None:
            middle.set_xlim(0, 1440)
            middle.set_ylim(0, 1)
            middle.barh(
                [0.5],
                [360],
                height=0.28,
                color=GRAY,
                left=0,
            )
            middle.barh(
                [0.5],
                [1080],
                height=0.28,
                color=LIGHT_GRAY,
                edgecolor=GRID,
                left=360,
            )
            middle.text(180, 0.5, "360", ha="center", va="center", color="white")
            middle.axvline(1440, color=RED, ls="--", lw=0.8)
            middle.set_xticks([0, 720, 1440], ["0", "12 h", "24 h"])
            middle.set_yticks([])
            middle.spines[["top", "right", "left"]].set_visible(False)
            middle.set_xlabel("冻结卡片的有效观测覆盖", fontsize=7.0)
        else:
            _small_trace(
                middle,
                frame,
                value,
                card_threshold,
                color=color,
                log_scale=log_scale,
            )
            unit = "比例" if value == "error_ratio" else "ms（对数轴）"
            middle.set_ylabel(unit, fontsize=7.0)
            middle.set_xlabel(
                f"可访问历史窗末端遥测片段（n={len(frame)}）",
                fontsize=7.0,
            )

        right = fig.add_subplot(gs[ridx, 2])
        right.axis("off")
        right.annotate(
            action,
            xy=(0.50, 0.50),
            xytext=(0.50, 0.50),
            ha="center",
            va="center",
            fontsize=7.2,
            color=color,
            fontweight="bold",
            bbox=dict(
                boxstyle="round,pad=0.42",
                facecolor="white",
                edgecolor=color,
                linewidth=0.9,
            ),
        )

    hidden = threshold_payload["evidence_actions_hidden_by_minimal_baseline"]
    fig.text(
        0.05,
        0.015,
        (
            f"仅输出阈值会隐藏 {hidden['count']:,}/{threshold_payload['population']:,} "
            f"（{hidden['rate']:.1%}）张卡的证据或动作信息；候选边界不等于正式 SLO。"
        ),
        fontsize=7.2,
        fontweight="bold",
    )
    fig.subplots_adjust(left=0.055, right=0.985, top=0.97, bottom=0.075)
    return v2._save(fig, output, "figure_01_evidence_aware_governance")


def figure_02(
    governance: dict[str, Any],
    ablation: dict[str, Any],
    threshold: dict[str, Any],
    output: Path,
) -> list[Path]:
    transitions = _PHASE3["transitions"]
    strata = _PHASE3["strata"]
    fig = plt.figure(figsize=(FINAL_WIDTH_IN, 4.45))
    gs = fig.add_gridspec(
        2,
        2,
        height_ratios=[1.05, 0.95],
        width_ratios=[1.18, 0.82],
        hspace=0.52,
        wspace=0.46,
    )
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])
    ax_c = fig.add_subplot(gs[1, :])

    ax_a.set_xlim(0, 1)
    ax_a.set_ylim(0, 1)
    ax_a.axis("off")
    _panel_title(ax_a, "a", "8,668 张候选卡被路由至四种互斥状态")
    ax_a.add_patch(
        patches.FancyBboxPatch(
            (0.02, 0.33),
            0.20,
            0.34,
            boxstyle="round,pad=0.01",
            facecolor="white",
            edgecolor=TEXT,
            linewidth=0.9,
        )
    )
    ax_a.text(0.12, 0.50, "冻结候选\n8,668", ha="center", va="center", fontweight="bold")
    counts = threshold["governance_counts"]
    total = sum(counts.values())
    y0 = 0.08
    for status in STATUS_ORDER:
        height = 0.84 * counts[status] / total
        center = y0 + height / 2
        ax_a.add_patch(
            patches.FancyBboxPatch(
                (0.63, y0),
                0.34,
                height * 0.92,
                boxstyle="round,pad=0.006",
                facecolor=STATUS_COLORS[status],
                edgecolor="white",
                linewidth=0.5,
            )
        )
        ax_a.add_patch(
            patches.Polygon(
                [(0.22, 0.36), (0.22, 0.64), (0.63, y0 + height * 0.92), (0.63, y0)],
                closed=True,
                facecolor=STATUS_COLORS[status],
                edgecolor="none",
                alpha=0.18,
            )
        )
        ax_a.text(
            0.80,
            center,
            f"{STATUS_LABELS[status]}  {counts[status]:,}（{counts[status]/total:.1%}）",
            ha="center",
            va="center",
            fontsize=7.0,
            color="white" if status != "threshold_unresolved" else TEXT,
            fontweight="bold",
        )
        y0 += height

    sli_rows = {
        row["value"]: row for row in strata["dimensions"]["sli"]["rows"]
    }
    sli_keys = ["error_ratio", "latency"]
    rates = [sli_rows[key]["hidden_rate"] * 100 for key in sli_keys]
    bars = ax_b.barh(
        np.arange(2),
        rates,
        color=[ORANGE, BLUE],
        height=0.52,
    )
    ax_b.set_yticks([0, 1], ["错误率", "延迟"])
    ax_b.invert_yaxis()
    ax_b.set_xlim(0, 75)
    ax_b.set_xlabel("证据/动作信息被隐藏的卡片比例（%）")
    for bar, value in zip(bars, rates):
        ax_b.text(
            value + 1.5,
            bar.get_y() + bar.get_height() / 2,
            f"{value:.1f}%",
            va="center",
            fontsize=7.1,
        )
    v2._clean(ax_b, grid_axis="x")
    _panel_title(ax_b, "b", "threshold-only 暴露并非均匀分布")

    scenarios = [
        ("删除历史门禁", "drop_history"),
        ("删除分辨率门禁", "drop_resolution"),
        ("删除长尾门禁", "drop_tail"),
        ("尾比 9.5", "tail_cutoff_9.5"),
        ("尾比 10.5", "tail_cutoff_10.5"),
    ]
    destinations = [
        "insufficient_evidence",
        "threshold_unresolved",
        "needs_context_review",
        "candidate_for_stakeholder_review",
    ]
    matrix = np.zeros((len(scenarios), len(destinations)), dtype=int)
    for row_idx, (_, scenario) in enumerate(scenarios):
        for item in transitions["scenarios"][scenario]["nonzero_transitions"]:
            if item["changed"]:
                matrix[row_idx, destinations.index(item["to_status"])] += item["count"]
    image = ax_c.imshow(matrix, cmap="Blues", aspect="auto", vmin=0)
    for row in range(matrix.shape[0]):
        for col in range(matrix.shape[1]):
            if matrix[row, col] > 0:
                ax_c.text(
                    col,
                    row,
                    f"{matrix[row, col]:,}",
                    ha="center",
                    va="center",
                    fontsize=7.3,
                    color="white" if matrix[row, col] > matrix.max() * 0.42 else TEXT,
                    fontweight="bold",
                )
    ax_c.set_yticks(np.arange(len(scenarios)), [label for label, _ in scenarios])
    ax_c.set_xticks(
        np.arange(4),
        ["历史不足", "阈值未解析", "上下文审查", "转交确认"],
    )
    ax_c.tick_params(length=0)
    ax_c.spines[:].set_visible(False)
    _panel_title(ax_c, "c", "反事实规则审计显示迁移目的地，而非规则优劣")
    fig.colorbar(image, ax=ax_c, fraction=0.018, pad=0.015, label="迁移卡数")
    fig.subplots_adjust(left=0.12, right=0.965, top=0.93, bottom=0.12)
    return v2._save(fig, output, "figure_02_governance_rules_and_strata")


def figure_04(
    raw: pd.DataFrame,
    per_card: pd.DataFrame,
    summary: dict[str, Any],
    output: Path,
) -> list[Path]:
    strata = _PHASE3["expert"]
    fig = plt.figure(figsize=(FINAL_WIDTH_IN, 3.55))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.12, 0.82, 1.05], wspace=0.53)
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])
    ax_c = fig.add_subplot(gs[0, 2])

    ordered = per_card.sort_values(
        ["threshold_plausibility_median", "governance_appropriateness_median"]
    ).reset_index(drop=True)
    y = np.arange(len(ordered))
    low = ordered["threshold_plausibility_median"].to_numpy()
    high = ordered["governance_appropriateness_median"].to_numpy()
    for yi, left, right in zip(y, low, high):
        ax_a.plot([left, right], [yi, yi], color="#AAB5BB", lw=0.65, zorder=1)
    ax_a.scatter(low, y, s=14, color=ORANGE, label="阈值合理性", zorder=2)
    ax_a.scatter(high, y, s=14, color=BLUE, label="治理适当性", zorder=2)
    ax_a.set_xlim(0.7, 5.3)
    ax_a.set_xticks([1, 2, 3, 4, 5])
    ax_a.set_yticks([0, 11, 23, 35, 47], ["1", "12", "24", "36", "48"])
    ax_a.set_xlabel("专家评分（1—5）")
    ax_a.set_ylabel("卡片（按阈值评分排序）")
    ax_a.legend(loc="lower right", fontsize=7.0)
    v2._clean(ax_a, grid_axis="x")
    _panel_title(ax_a, "a", "卡级配对评分保留两个不同判断对象")

    cohorts = ["remediation", "generalization"]
    cohort_labels = ["修复队列", "留出队列"]
    medians = [strata["by_cohort"][key]["median_delta"] for key in cohorts]
    q1 = [strata["by_cohort"][key]["q1_delta"] for key in cohorts]
    q3 = [strata["by_cohort"][key]["q3_delta"] for key in cohorts]
    xpos = np.arange(2)
    ax_b.errorbar(
        xpos,
        medians,
        yerr=[np.array(medians) - np.array(q1), np.array(q3) - np.array(medians)],
        fmt="o",
        color=BLUE,
        ecolor=BLUE,
        capsize=4,
        lw=1.2,
    )
    ax_b.axhline(0, color=TEXT, lw=0.8)
    ax_b.set_xticks(xpos, cohort_labels)
    ax_b.set_ylim(-0.2, 4.2)
    ax_b.set_ylabel("治理评分 − 阈值评分")
    v2._clean(ax_b, grid_axis="y")
    _panel_title(ax_b, "b", "两队列的配对差异")
    ax_b.text(
        0.04,
        0.04,
        "唯一 STATE_ERROR 异议：1/144",
        transform=ax_b.transAxes,
        ha="left",
        va="bottom",
        fontsize=7.0,
        color=RED,
        fontweight="bold",
    )

    status_rows = strata["by_governance_status"]
    labels = [STATUS_LABELS[s] for s in STATUS_ORDER]
    threshold_low = [
        status_rows[s]["threshold_low_1_or_2_count"] / status_rows[s]["rows"] * 100
        for s in STATUS_ORDER
    ]
    governance_high = [
        status_rows[s]["governance_high_4_or_5_count"] / status_rows[s]["rows"] * 100
        for s in STATUS_ORDER
    ]
    yy = np.arange(4)
    ax_c.barh(yy + 0.16, threshold_low, height=0.30, color=ORANGE, label="阈值低分")
    ax_c.barh(
        yy - 0.16,
        governance_high,
        height=0.30,
        color=BLUE,
        label="治理高分",
    )
    ax_c.set_yticks(yy, labels)
    ax_c.invert_yaxis()
    ax_c.set_xlim(0, 105)
    ax_c.set_xlabel("专家—卡配对比例（%）")
    ax_c.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, -0.34),
        ncol=2,
        fontsize=7.0,
    )
    v2._clean(ax_c, grid_axis="x")
    _panel_title(ax_c, "c", "四种治理状态的分层结果")
    fig.subplots_adjust(left=0.075, right=0.985, top=0.88, bottom=0.19)
    return v2._save(fig, output, "figure_04_expert_paired_distinction")


def figure_06(
    prediction: dict[str, Any],
    paired: dict[str, Any],
    topology: dict[str, Any],
    output: Path,
) -> list[Path]:
    fig = plt.figure(figsize=(FINAL_WIDTH_IN, 4.75))
    gs = fig.add_gridspec(2, 2, hspace=0.62, wspace=0.62)
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])
    ax_c = fig.add_subplot(gs[1, 0])
    ax_d = fig.add_subplot(gs[1, 1])

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
    values = [prediction["overall"][model]["pr_auc"] for model in model_order]
    colors = [GRAY, MUTED, ORANGE, BLUE, GREEN, PURPLE]
    yy = np.arange(len(model_order))
    ax_a.hlines(yy, 0, values, color=GRID, lw=1.1)
    ax_a.scatter(values, yy, s=32, c=colors, edgecolor="white", lw=0.5)
    for y, value in zip(yy, values):
        ax_a.text(value + 0.015, y, f"{value:.3f}", va="center", fontsize=7.0)
    ax_a.set_yticks(yy, model_labels)
    ax_a.invert_yaxis()
    ax_a.set_xlim(0, 0.9)
    ax_a.set_xlabel("OOF PR-AUC")
    v2._clean(ax_a, grid_axis="x")
    _panel_title(ax_a, "a", "同一滚动时间协议下的支持性信号")

    pair_labels = ["应用−类别先验", "应用−持续性", "+基础设施−应用", "+拓扑−基础设施"]
    pairs = paired["paired_event_bootstrap"]
    deltas = [item["metrics"]["pr_auc"]["observed_delta"] for item in pairs]
    lows = [item["metrics"]["pr_auc"]["ci95_low"] for item in pairs]
    highs = [item["metrics"]["pr_auc"]["ci95_high"] for item in pairs]
    ypos = np.arange(4)
    ax_b.axvline(0, color=RED, lw=0.8, ls="--")
    for y, delta, low, high in zip(ypos, deltas, lows, highs):
        color = BLUE if low > 0 else GRAY
        ax_b.plot([low, high], [y, y], color=color, lw=1.5)
        ax_b.scatter(delta, y, s=30, color=color, edgecolor="white", lw=0.5)
    ax_b.set_yticks(ypos, pair_labels)
    ax_b.invert_yaxis()
    ax_b.set_xlim(-0.08, 0.72)
    ax_b.set_xlabel("配对 PR-AUC 差值（95% CI）")
    v2._clean(ax_b, grid_axis="x")
    _panel_title(ax_b, "b", "事件级配对 bootstrap（2,000 次）")

    fold_values = [
        row["pr_auc"] for _, row in sorted(prediction["by_fold"]["app_hgb"].items())
    ]
    service_values = [
        row["pr_auc"]
        for row in prediction["by_service"]["app_hgb"].values()
        if np.isfinite(row["pr_auc"])
    ]
    ax_c.boxplot(
        [fold_values, service_values],
        labels=["五个时间折", "11 个服务"],
        patch_artist=True,
        widths=0.52,
        boxprops=dict(facecolor=LIGHT_BLUE, edgecolor=BLUE),
        medianprops=dict(color=TEXT, lw=1.2),
        whiskerprops=dict(color=BLUE),
        capprops=dict(color=BLUE),
        flierprops=dict(marker="o", markersize=3, markerfacecolor=ORANGE, markeredgecolor="none"),
    )
    ax_c.scatter(
        np.full(len(fold_values), 1) + np.linspace(-0.08, 0.08, len(fold_values)),
        fold_values,
        s=16,
        color=BLUE,
        zorder=3,
    )
    ax_c.scatter(
        np.full(len(service_values), 2) + np.linspace(-0.10, 0.10, len(service_values)),
        service_values,
        s=12,
        color=ORANGE,
        zorder=3,
    )
    ax_c.set_ylabel("应用-HGB PR-AUC")
    ax_c.set_ylim(0, 1.03)
    v2._clean(ax_c, grid_axis="y")
    _panel_title(ax_c, "c", "时间稳定性不消除服务异质性")

    calibration = prediction["calibration_10_equal_width"]["app_hgb"]
    mean_p = [row["mean_probability"] for row in calibration]
    observed = [row["observed_rate"] for row in calibration]
    sizes = [max(16, row["n"] / 10) for row in calibration]
    ax_d.plot([0, 1], [0, 1], color=GRAY, ls="--", lw=0.8)
    ax_d.plot(mean_p, observed, color=BLUE, lw=1.1)
    ax_d.scatter(
        mean_p,
        observed,
        s=sizes,
        color=BLUE,
        alpha=0.72,
        edgecolor="white",
        lw=0.5,
    )
    ax_d.set_xlim(0, 1)
    ax_d.set_ylim(0, 1)
    ax_d.set_xlabel("平均预测概率")
    ax_d.set_ylabel("观测发生率")
    v2._clean(ax_d, grid_axis="both")
    _panel_title(ax_d, "d", "校准曲线显示概率解释边界")
    overall = prediction["overall"]["app_hgb"]
    ax_d.text(
        0.03,
        0.96,
        f"Brier={overall['brier']:.3f}；ECE={overall['ece_10_equal_width']:.3f}",
        transform=ax_d.transAxes,
        ha="left",
        va="top",
        fontsize=7.0,
        fontweight="bold",
    )
    fig.subplots_adjust(left=0.13, right=0.985, top=0.93, bottom=0.12)
    return v2._save(fig, output, "figure_06_prediction_stability_and_scope")


def generate(repo: Path, output: Path) -> dict[str, Any]:
    global _REPO, _PHASE3
    _REPO = repo
    phase3 = repo / "checkpoints/runtime_nfr_v3_academic/phase3_content_analysis_v1"
    cards_path = (
        repo
        / "checkpoints/runtime_nfr_v3_academic/governance_cards/academic_governance_cards.json"
    )
    _PHASE3 = {
        "transitions": _load_json(phase3 / "governance_transition_matrix.json"),
        "strata": _load_json(phase3 / "threshold_only_strata.json"),
        "expert": _load_json(phase3 / "expert_strata_summary.json"),
        "cards": _load_json(cards_path),
    }
    assert _PHASE3["transitions"]["population"] == 8668
    assert _PHASE3["strata"]["hidden_evidence_or_action"]["count"] == 4426
    assert _PHASE3["expert"]["population"]["ratings"] == 144

    v2._final_style()
    v2._panel_title = _panel_title
    base._save_figure = _save_without_pdf
    base.figure_01 = figure_01
    base.figure_02 = figure_02
    base.figure_03 = v2.figure_03
    base.figure_04 = figure_04
    base.figure_05 = v2.figure_05
    base.figure_06 = figure_06
    manifest = base.generate(repo, output)
    extra_inputs = {
        "phase3_transitions": phase3 / "governance_transition_matrix.json",
        "phase3_threshold_strata": phase3 / "threshold_only_strata.json",
        "phase3_expert_strata": phase3 / "expert_strata_summary.json",
        "governance_cards": cards_path,
        "figure1_raw_emailservice": (
            repo
            / "dataset/tenant_ccf_0606_0614_seg_0004/records/record_0000/metrics/app/emailservice.csv"
        ),
        "figure1_raw_adservice": (
            repo
            / "dataset/tenant_ccf_0606_0614_seg_0004/records/record_0000/metrics/app/adservice.csv"
        ),
        "figure1_raw_cartservice": (
            repo
            / "dataset/tenant_ccf_0606_0614_seg_0004/records/record_0000/metrics/app/cartservice.csv"
        ),
    }
    manifest["inputs"].update(
        {name: base._file_record(path, repo) for name, path in extra_inputs.items()}
    )
    manifest["protocol"] = "runtime-nfr-jos-figures/3"
    manifest["description"] = (
        "Phase-3 claim-led figures drawn at 175 mm from frozen cards, Phase-2 "
        "analyses, and versioned Phase-3 derived analyses."
    )
    manifest["claims"].update(
        {
            "F01": "similar numeric candidates can carry different evidence responsibilities and actions",
            "F02": "frozen gates route cards to four states and have auditable counterfactual destinations",
            "F04": "expert ratings distinguish threshold plausibility from governance appropriateness across cohorts and states",
            "F06": "prediction is a supporting signal with temporal stability, service heterogeneity, and calibration limits",
        }
    )
    manifest["layout_contract"] = {
        "intended_width_mm": 175,
        "intended_width_in": FINAL_WIDTH_IN,
        "minimum_body_font_pt": 7,
        "target_body_font_pt": 7,
        "primary_format": "SVG",
        "raster_format": "300 dpi PNG",
        "pdf_status": "not generated in the content-optimization stage",
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
            "checkpoints/runtime_nfr_v3_academic/phase3_figures_jos_v3"
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
