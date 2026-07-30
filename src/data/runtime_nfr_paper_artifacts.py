"""Generate traceable Runtime NFR manuscript cases and publication figures."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


PAPER_ARTIFACT_PROTOCOL = "runtime-nfr-v3-paper-artifacts/1"
STATUS_ORDER = [
    "insufficient_evidence",
    "threshold_unresolved",
    "needs_context_review",
    "candidate_for_stakeholder_review",
]
STATUS_LABELS = ["历史证据不足", "阈值未解析", "需上下文审查", "可转交确认"]
STATUS_COLORS = ["#9CA3AF", "#E69F00", "#CC6677", "#4477AA"]


def _load(path_value: str | Path) -> tuple[Any, dict[str, Any]]:
    path = Path(path_value)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload, {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def _save_figure(fig: plt.Figure, output: Path, stem: str) -> list[Path]:
    paths = []
    for extension in ("png", "svg", "pdf"):
        path = output / f"{stem}.{extension}"
        fig.savefig(
            path,
            dpi=300 if extension == "png" else None,
            bbox_inches="tight",
            facecolor="white",
        )
        paths.append(path)
    plt.close(fig)
    return paths


def _style() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [
                "Microsoft YaHei",
                "SimHei",
                "Arial Unicode MS",
                "DejaVu Sans",
            ],
            "axes.unicode_minus": False,
            "font.size": 9,
            "axes.titlesize": 11,
            "axes.labelsize": 10,
            "legend.fontsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
        }
    )


def _case_payload(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "card_id": card["card_id"],
        "service": card["subject_id"],
        "sli": card["sli"],
        "unit": card["unit"],
        "raw_threshold": card["raw_threshold"],
        "valid_samples": card["valid_samples"],
        "history_coverage": card["quality"]["history_coverage"],
        "evidence_status": card["evidence_status"],
        "governance_flags": card["governance_flags"],
        "recommended_next_action": card["recommended_next_action"],
        "latency_tail_ratio": card["applicability_context"].get(
            "latency_tail_ratio"
        ),
        "parent_card_id": card["parent_card_id"],
    }


def _select_cases(cards: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_status = {
        status: [card for card in cards if card["evidence_status"] == status]
        for status in STATUS_ORDER
    }
    insufficient = sorted(
        by_status["insufficient_evidence"],
        key=lambda card: (
            abs(float(card["quality"]["history_coverage"]) - 0.25),
            card["card_id"],
        ),
    )[0]
    unresolved = sorted(
        by_status["threshold_unresolved"],
        key=lambda card: (
            abs(float(card["raw_threshold"])),
            -float(card["quality"]["history_coverage"]),
            card["card_id"],
        ),
    )[0]
    context = sorted(
        by_status["needs_context_review"],
        key=lambda card: (
            abs(
                float(
                    card["applicability_context"].get(
                        "latency_tail_ratio", float("inf")
                    )
                )
                - 9.997
            ),
            card["card_id"],
        ),
    )[0]
    candidate_pool = [
        card
        for card in by_status["candidate_for_stakeholder_review"]
        if card["applicability_context"].get("latency_tail_ratio") is not None
    ]
    candidate = sorted(
        candidate_pool,
        key=lambda card: (
            abs(
                float(card["applicability_context"]["latency_tail_ratio"])
                - 9.997
            ),
            card["card_id"],
        ),
    )[0]
    return {
        "insufficient_evidence": _case_payload(insufficient),
        "threshold_unresolved": _case_payload(unresolved),
        "needs_context_review": _case_payload(context),
        "candidate_for_stakeholder_review": _case_payload(candidate),
    }


def build_runtime_nfr_paper_artifacts(
    output_dir: str | Path,
    *,
    governance_summary_path: str | Path,
    governance_cards_path: str | Path,
    round1_summary_path: str | Path,
    round2_summary_path: str | Path,
    external_summary_path: str | Path,
    feature_ablation_summary_path: str | Path,
    paired_bootstrap_path: str | Path,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _style()
    source_values = {
        "governance_summary": governance_summary_path,
        "governance_cards": governance_cards_path,
        "round1": round1_summary_path,
        "round2": round2_summary_path,
        "external": external_summary_path,
        "feature_ablation": feature_ablation_summary_path,
        "paired_bootstrap": paired_bootstrap_path,
    }
    loaded: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    for name, path in source_values.items():
        loaded[name], sources[name] = _load(path)

    governance = loaded["governance_summary"]
    cases = _select_cases(loaded["governance_cards"])
    cases_path = output / "representative_cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "protocol": PAPER_ARTIFACT_PROTOCOL,
                "selection": (
                    "deterministic representative cases from frozen cards; "
                    "the stakeholder-review candidate is the frozen latency "
                    "boundary case nearest tail ratio 9.997"
                ),
                "cases": cases,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    case_lines = [
        "# Runtime NFR 代表性案例",
        "",
        "| 治理状态 | 服务 | SLI | 原始阈值 | 覆盖率 | 有效样本 | 尾比率 | 后续动作 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for status in STATUS_ORDER:
        case = cases[status]
        tail = case["latency_tail_ratio"]
        case_lines.append(
            f"| `{status}` | `{case['service']}` | `{case['sli']}` | "
            f"{case['raw_threshold']:.6g} | {case['history_coverage']:.1%} | "
            f"{case['valid_samples']} | "
            f"{tail:.3f}" if tail is not None else
            f"| `{status}` | `{case['service']}` | `{case['sli']}` | "
            f"{case['raw_threshold']:.6g} | {case['history_coverage']:.1%} | "
            f"{case['valid_samples']} | —"
        )
        case_lines[-1] += f" | `{case['recommended_next_action']}` |"
    (output / "REPRESENTATIVE_CASES.md").write_text(
        "\n".join(case_lines) + "\n", encoding="utf-8"
    )

    generated: list[Path] = [cases_path, output / "REPRESENTATIVE_CASES.md"]

    # Figure 1: research chain.
    fig, ax = plt.subplots(figsize=(10.8, 2.3))
    ax.axis("off")
    labels = ["历史遥测", "候选边界", "证据审计", "治理分流", "多源实证", "论文主张"]
    colors = ["#E8EEF5", "#DCEBFA", "#FFF1CC", "#FADBD8", "#DDF1E4", "#DCEBFA"]
    xs = np.linspace(0.06, 0.94, len(labels))
    for index, (x, label, color) in enumerate(zip(xs, labels, colors)):
        ax.text(
            x,
            0.55,
            label,
            ha="center",
            va="center",
            fontsize=10,
            bbox=dict(boxstyle="round,pad=0.55", fc=color, ec="#334155", lw=1.0),
        )
        if index < len(labels) - 1:
            ax.annotate(
                "",
                xy=(xs[index + 1] - 0.065, 0.55),
                xytext=(x + 0.065, 0.55),
                arrowprops=dict(arrowstyle="->", lw=1.2, color="#334155"),
            )
    ax.text(
        0.55,
        0.16,
        "证据不足时的拒绝或转交也是方法输出，而非失败样本",
        ha="center",
        color="#7F1D1D",
        fontsize=9,
    )
    generated.extend(_save_figure(fig, output, "figure_01_research_chain"))

    # Figure 2: status distribution and service/status heatmap.
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.2), gridspec_kw={"width_ratios": [1, 2.2]})
    counts = [governance["status_counts"][status] for status in STATUS_ORDER]
    bars = axes[0].barh(STATUS_LABELS, counts, color=STATUS_COLORS)
    axes[0].invert_yaxis()
    axes[0].set_xlabel("候选边界数量")
    axes[0].set_title("(a) 治理状态分布")
    axes[0].grid(axis="x", color="#E5E7EB", ls="--", lw=0.7)
    for bar, count in zip(bars, counts):
        axes[0].text(
            bar.get_width() + max(counts) * 0.02,
            bar.get_y() + bar.get_height() / 2,
            f"{count:,}",
            va="center",
            fontsize=8,
        )
    services = sorted(governance["service"])
    matrix = np.array(
        [
            [
                governance["by_status"][status]["service"].get(service, 0)
                for service in services
            ]
            for status in STATUS_ORDER
        ],
        dtype=float,
    )
    shares = matrix / np.maximum(matrix.sum(axis=0, keepdims=True), 1)
    image = axes[1].imshow(shares, aspect="auto", cmap="Blues", vmin=0, vmax=1)
    axes[1].set_xticks(range(len(services)))
    axes[1].set_xticklabels(services, rotation=45, ha="right")
    axes[1].set_yticks(range(len(STATUS_LABELS)))
    axes[1].set_yticklabels(STATUS_LABELS)
    axes[1].set_title("(b) 服务内治理状态比例")
    fig.colorbar(image, ax=axes[1], fraction=0.025, pad=0.02, label="服务内比例")
    fig.tight_layout()
    generated.extend(_save_figure(fig, output, "figure_02_governance_overview"))

    # Figure 3: exploratory tail-ratio sensitivity.
    sensitivity = governance["exploratory_tail_ratio_sensitivity"]["cutoffs"]
    cutoffs = ["9.5", "10.0", "10.5"]
    context_counts = [sensitivity[value]["needs_context_review"] for value in cutoffs]
    candidate_counts = [
        sensitivity[value]["candidate_for_stakeholder_review"] for value in cutoffs
    ]
    fig, ax = plt.subplots(figsize=(7.4, 4.1))
    x = np.arange(len(cutoffs))
    width = 0.34
    ax.bar(x - width / 2, context_counts, width, label="需上下文审查", color="#CC6677")
    ax.bar(x + width / 2, candidate_counts, width, label="可转交确认", color="#4477AA")
    ax.axvline(1, color="#111827", lw=1, ls="--")
    ax.set_xticks(x)
    ax.set_xticklabels(cutoffs)
    ax.set_xlabel("长尾比率阈值（探索性）")
    ax.set_ylabel("候选边界数量")
    ax.set_title("长尾规则敏感性（主规则固定为 10.0）")
    ax.legend(
        frameon=False,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.17),
        ncol=2,
    )
    ax.text(
        0.5,
        0.04,
        f"边界案例尾比率≈{cases['candidate_for_stakeholder_review']['latency_tail_ratio']:.3f}",
        transform=ax.transAxes,
        color="#8B1E3F",
        ha="center",
    )
    ax.grid(axis="y", color="#E5E7EB", ls="--", lw=0.7)
    fig.subplots_adjust(bottom=0.25)
    generated.extend(_save_figure(fig, output, "figure_03_tail_sensitivity"))

    # Figure 4: expert distinction between threshold and governance.
    round1 = loaded["round1"]
    round2 = loaded["round2"]
    labels = ["第一轮\n阈值合理性", "第一轮\n运行可操作性", "第二轮\n阈值合理性", "第二轮\n治理适当性"]
    dimensions = [
        round1["dimensions"]["threshold_plausibility"],
        round1["dimensions"]["runtime_actionability"],
        round2["overall"]["dimensions"]["threshold_plausibility"],
        round2["overall"]["dimensions"]["governance_appropriateness"],
    ]
    medians = np.array([value["median"] for value in dimensions], dtype=float)
    lower = medians - np.array([value["q1"] for value in dimensions], dtype=float)
    upper = np.array([value["q3"] for value in dimensions], dtype=float) - medians
    fig, ax = plt.subplots(figsize=(8.0, 4.2))
    bars = ax.bar(
        np.arange(4),
        medians,
        color=["#D9A441", "#6A9FB5", "#D9A441", "#3F7CAC"],
        edgecolor="white",
    )
    ax.errorbar(
        np.arange(4),
        medians,
        yerr=np.vstack([lower, upper]),
        fmt="none",
        ecolor="#1F2937",
        capsize=4,
        lw=1.2,
    )
    ax.set_ylim(0, 5.5)
    ax.set_ylabel("评分中位数（1–5）")
    ax.set_xticks(np.arange(4))
    ax.set_xticklabels(labels)
    ax.set_title("专家区分阈值可信度与治理合理性")
    for bar, value in zip(bars, medians):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 0.12, f"{value:.1f}", ha="center")
    ax.grid(axis="y", color="#E5E7EB", ls="--", lw=0.7)
    generated.extend(_save_figure(fig, output, "figure_04_expert_distinction"))

    # Figure 5: external validation pathway.
    external = loaded["external"]
    fig, ax = plt.subplots(figsize=(9.0, 2.8))
    ax.axis("off")
    ext_labels = [
        "RCAEval RE1-OB\n125 个独立案例",
        "统一接口\n10 个服务",
        f"治理卡\n{external['cards']:,} 张",
        "历史窗口不足\n100% 拒绝操作化",
    ]
    ext_x = np.linspace(0.12, 0.88, 4)
    for index, (x_value, label) in enumerate(zip(ext_x, ext_labels)):
        ax.text(
            x_value,
            0.56,
            label,
            ha="center",
            va="center",
            bbox=dict(
                boxstyle="round,pad=0.55",
                fc="#EEF2F7" if index < 3 else "#FDECEC",
                ec="#475569",
            ),
        )
        if index < 3:
            ax.annotate(
                "",
                xy=(ext_x[index + 1] - 0.105, 0.56),
                xytext=(x_value + 0.105, 0.56),
                arrowprops=dict(arrowstyle="->", color="#475569", lw=1.2),
            )
    ax.text(
        0.5,
        0.12,
        "支持流程与拒绝治理可移植性；不提供阈值正确率",
        ha="center",
        color="#7F1D1D",
    )
    generated.extend(_save_figure(fig, output, "figure_05_external_transfer"))

    # Figure 6: model ablation and paired PR-AUC deltas.
    ablation = loaded["feature_ablation"]
    bootstrap = loaded["paired_bootstrap"]["paired_event_bootstrap"]
    model_order = ablation["models"]
    model_labels = [
        "类别先验",
        "Persistence",
        "App Logistic",
        "App HGB",
        "App+Infra",
        "App+Infra+Topo",
    ]
    pr_auc = [ablation["summary_by_model"][model]["pr_auc"] for model in model_order]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.2), gridspec_kw={"width_ratios": [1.25, 1]})
    bars = axes[0].bar(
        np.arange(len(model_order)),
        pr_auc,
        color=["#C8C8C8", "#A8C8E8", "#1B3D6E", "#3F7CAC", "#D9A441", "#9B5F45"],
        edgecolor="white",
    )
    axes[0].set_xticks(np.arange(len(model_order)))
    axes[0].set_xticklabels(model_labels, rotation=35, ha="right")
    axes[0].set_ylabel("PR-AUC")
    axes[0].set_ylim(0, 0.9)
    axes[0].set_title("(a) 六模型预测消融")
    axes[0].grid(axis="y", color="#E5E7EB", ls="--", lw=0.7)
    for bar, value in zip(bars, pr_auc):
        axes[0].text(bar.get_x() + bar.get_width() / 2, value + 0.015, f"{value:.3f}", ha="center", fontsize=7)

    delta_labels = []
    deltas = []
    lows = []
    highs = []
    for row in bootstrap:
        metric = row["metrics"]["pr_auc"]
        delta_labels.append(f"{row['challenger']}\nvs {row['reference']}")
        deltas.append(metric["observed_delta"])
        lows.append(metric["ci95_low"])
        highs.append(metric["ci95_high"])
    y = np.arange(len(deltas))
    axes[1].errorbar(
        deltas,
        y,
        xerr=np.vstack([np.array(deltas) - np.array(lows), np.array(highs) - np.array(deltas)]),
        fmt="o",
        color="#1B3D6E",
        ecolor="#64748B",
        capsize=3,
    )
    axes[1].axvline(0, color="#9B1C1C", ls="--", lw=1)
    axes[1].set_yticks(y)
    axes[1].set_yticklabels(delta_labels)
    axes[1].invert_yaxis()
    axes[1].set_xlabel("配对 PR-AUC 差值（95% CI）")
    axes[1].set_title("(b) 事件级配对增量")
    axes[1].grid(axis="x", color="#E5E7EB", ls="--", lw=0.7)
    fig.tight_layout()
    generated.extend(_save_figure(fig, output, "figure_06_prediction_ablation"))

    manifest = {
        "protocol": PAPER_ARTIFACT_PROTOCOL,
        "sources": sources,
        "representative_cases": str(cases_path.resolve()),
        "figures": [
            {
                "path": str(path.resolve()),
                "bytes": path.stat().st_size,
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
            for path in generated
            if path.suffix in {".png", ".svg", ".pdf"}
        ],
        "manual_numeric_transcription_allowed": False,
    }
    manifest_path = output / "paper_artifact_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {**manifest, "manifest": str(manifest_path.resolve()), "cases": cases}
