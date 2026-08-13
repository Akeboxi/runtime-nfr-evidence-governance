#!/usr/bin/env python3
"""Create the submission-grade blinded-routing validation figure."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np


MM = 1 / 25.4
ROUTE_SHORT = {
    "A": "A  补充历史证据",
    "B": "B  澄清测量/目标",
    "C": "C  上下文审查",
    "D": "D  目标所有者审查",
}
ROUTE_COMPACT = {
    "A": "A  历史证据",
    "B": "B  测量/目标",
    "C": "C  上下文",
    "D": "D  目标所有者",
}


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    package_root = Path(__file__).resolve().parents[1]
    analysis_dir = package_root / "analysis/blinded_routing_v1"
    confusion = read_csv(analysis_dir / "confusion_matrix.csv")
    per_state = read_csv(analysis_dir / "per_state.csv")
    per_difficulty = read_csv(analysis_dir / "per_difficulty.csv")
    summary = json.loads((analysis_dir / "summary.json").read_text(encoding="utf-8"))

    matrix = np.array(
        [[int(row[f"expert_{choice}"]) for choice in "ABCD"] for row in confusion],
        dtype=int,
    )
    row_pct = matrix / matrix.sum(axis=1, keepdims=True) * 100

    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Microsoft YaHei", "SimHei", "Arial", "DejaVu Sans"],
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "font.size": 7,
            "axes.linewidth": 0.7,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "xtick.major.width": 0.6,
            "ytick.major.width": 0.6,
        }
    )

    fig = plt.figure(figsize=(175 * MM, 92 * MM), constrained_layout=False)
    grid = fig.add_gridspec(
        2,
        2,
        width_ratios=[1.42, 1.0],
        height_ratios=[1.1, 0.72],
        left=0.19,
        right=0.985,
        bottom=0.14,
        top=0.92,
        wspace=0.48,
        hspace=0.62,
    )

    ax = fig.add_subplot(grid[:, 0])
    image = ax.imshow(matrix, cmap="Blues", vmin=0, vmax=24, aspect="equal")
    for i in range(4):
        for j in range(4):
            value = matrix[i, j]
            color = "white" if value >= 13 else "#14213d"
            weight = "bold" if i == j else "normal"
            ax.text(
                j,
                i,
                f"{value}\n({row_pct[i, j]:.1f}%)",
                ha="center",
                va="center",
                color=color,
                fontsize=7.2,
                fontweight=weight,
                linespacing=1.25,
            )
    ax.set_xticks(range(4), ["A", "B", "C", "D"])
    ax.set_yticks(range(4), [ROUTE_SHORT[x] for x in "ABCD"])
    ax.set_xlabel("评价者盲态选择", labelpad=5)
    ax.set_ylabel("冻结协议主路由", labelpad=7)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    cbar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.035)
    cbar.ax.set_title("判断数", fontsize=6, pad=3)
    cbar.ax.tick_params(labelsize=6, width=0.5)
    ax.text(-0.23, 1.06, "a", transform=ax.transAxes, fontsize=9, fontweight="bold")

    overall = summary["primary"]["rate"] * 100
    ax_state = fig.add_subplot(grid[0, 1])
    state_rates = [float(row["agreement_rate"]) * 100 for row in per_state]
    y = np.arange(4)
    colors = ["#4f8fc0", "#9ecae1", "#6baed6", "#5b8db8"]
    bars = ax_state.barh(y, state_rates, height=0.58, color=colors, edgecolor="none")
    ax_state.set_yticks(y, [ROUTE_COMPACT[x] for x in "ABCD"])
    ax_state.invert_yaxis()
    ax_state.set_xlim(0, 100)
    ax_state.set_xlabel("盲态一致率（%）")
    ax_state.axvline(overall, color="#555555", lw=0.8, ls=(0, (3, 2)))
    ax_state.text(
        1.0,
        1.02,
        f"虚线：总体 {overall:.1f}%",
        transform=ax_state.transAxes,
        ha="right",
        va="bottom",
        color="#444444",
        fontsize=6.3,
    )
    for bar, rate in zip(bars, state_rates):
        ax_state.text(rate + 1.5, bar.get_y() + bar.get_height() / 2, f"{rate:.1f}", va="center", fontsize=6.5)
    ax_state.spines["left"].set_visible(False)
    ax_state.tick_params(axis="y", length=0)
    ax_state.grid(axis="x", color="#e5e5e5", lw=0.5)
    ax_state.set_axisbelow(True)
    ax_state.text(-0.18, 1.12, "b", transform=ax_state.transAxes, fontsize=9, fontweight="bold")

    ax_diff = fig.add_subplot(grid[1, 1])
    diff_labels = ["明显案例", "临界/重叠案例"]
    diff_map = {row["difficulty"]: float(row["agreement_rate"]) * 100 for row in per_difficulty}
    diff_rates = [diff_map["obvious"], diff_map["boundary"]]
    diff_bars = ax_diff.barh(
        np.arange(2), diff_rates, height=0.52, color=["#7aa6c2", "#b8c5ce"], edgecolor="none"
    )
    ax_diff.set_yticks(np.arange(2), diff_labels)
    ax_diff.invert_yaxis()
    ax_diff.set_xlim(0, 100)
    ax_diff.set_xlabel("盲态一致率（%）")
    for bar, rate in zip(diff_bars, diff_rates):
        ax_diff.text(rate + 1.5, bar.get_y() + bar.get_height() / 2, f"{rate:.1f}", va="center", fontsize=6.5)
    ax_diff.spines["left"].set_visible(False)
    ax_diff.tick_params(axis="y", length=0)
    ax_diff.grid(axis="x", color="#e5e5e5", lw=0.5)
    ax_diff.set_axisbelow(True)
    ax_diff.text(
        0.0,
        -0.58,
        f"n=3名新评价者；32张卡；96个判断；卡片多数票一致 {summary['card_majority']['cards_matching_protocol']}/32",
        transform=ax_diff.transAxes,
        fontsize=6.2,
        color="#444444",
    )

    stem = analysis_dir / "figure_04_blinded_routing_validation"
    fig.savefig(stem.with_suffix(".svg"), bbox_inches="tight", facecolor="white")
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight", facecolor="white")
    fig.savefig(stem.with_suffix(".png"), dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(stem.with_suffix(".tiff"), dpi=600, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    qa = {
        "figure": "figure_04_blinded_routing_validation",
        "backend": "python-matplotlib",
        "final_size_mm": [175, 92],
        "source_data": ["confusion_matrix.csv", "per_state.csv", "per_difficulty.csv", "summary.json"],
        "n_definition": "3 new evaluators x 32 frozen cards = 96 repeated expert-card judgments",
        "interval": "overall 95% interval from 10,000 card-cluster bootstrap resamples",
        "integrity": "all cells are direct counts; row percentages use 24 judgments per frozen route",
        "claim_boundary": summary["claim_boundary"],
    }
    (analysis_dir / "figure_04_qa.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(qa, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
