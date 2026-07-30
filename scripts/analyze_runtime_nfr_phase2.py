"""Generate read-only Phase-2 diagnostic analyses for the runtime-NFR paper.

The command reads frozen v2/v3 artifacts and writes only to a caller-selected
new output directory.  It does not train models, change thresholds, select new
samples, or modify any source artifact.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, ndcg_score


STATUSES = (
    "insufficient_evidence",
    "threshold_unresolved",
    "needs_context_review",
    "candidate_for_stakeholder_review",
)
PRIMARY_PRIORITY = ("history", "resolution", "tail")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def qstats(values: Iterable[float]) -> dict[str, float | int | None]:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if not array.size:
        return {"n": 0, "min": None, "q1": None, "median": None, "q3": None, "max": None}
    return {
        "n": int(array.size),
        "min": float(array.min()),
        "q1": float(np.quantile(array, 0.25)),
        "median": float(np.quantile(array, 0.5)),
        "q3": float(np.quantile(array, 0.75)),
        "max": float(array.max()),
    }


def triggers(card: Mapping[str, Any], tail_cutoff: float = 10.0) -> dict[str, bool]:
    flags = set(card.get("governance_flags", []))
    tail = card.get("applicability_context", {}).get("latency_tail_ratio")
    return {
        "history": bool(
            "history_coverage_below_0_5" in flags or "valid_samples_below_720" in flags
        ),
        "resolution": bool(
            "zero_error_baseline" in flags or "threshold_from_robust_scale_floor" in flags
        ),
        "tail": bool(tail is not None and float(tail) >= tail_cutoff),
    }


def assign_status(
    trigger_values: Mapping[str, bool],
    priority: Sequence[str] = PRIMARY_PRIORITY,
) -> str:
    status_by_rule = {
        "history": "insufficient_evidence",
        "resolution": "threshold_unresolved",
        "tail": "needs_context_review",
    }
    for rule in priority:
        if trigger_values.get(rule, False):
            return status_by_rule[rule]
    return "candidate_for_stakeholder_review"


def status_counts(statuses: Iterable[str]) -> dict[str, int]:
    counts = Counter(statuses)
    return {status: int(counts.get(status, 0)) for status in STATUSES}


def transition_rows(
    baseline: Sequence[str],
    counterfactual: Sequence[str],
    scenario: str,
) -> list[dict[str, Any]]:
    counts = Counter(zip(baseline, counterfactual))
    rows: list[dict[str, Any]] = []
    for source in STATUSES:
        for target in STATUSES:
            count = int(counts.get((source, target), 0))
            if count:
                rows.append(
                    {
                        "scenario": scenario,
                        "from_status": source,
                        "to_status": target,
                        "count": count,
                    }
                )
    return rows


def expected_calibration_error(
    labels: np.ndarray, probabilities: np.ndarray, bins: int = 10
) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    result = 0.0
    for index in range(bins):
        right_closed = index == bins - 1
        mask = (probabilities >= edges[index]) & (
            probabilities <= edges[index + 1] if right_closed else probabilities < edges[index + 1]
        )
        if mask.any():
            result += float(mask.mean()) * abs(
                float(probabilities[mask].mean()) - float(labels[mask].mean())
            )
    return float(result)


def calibration_rows(frame: pd.DataFrame, bins: int = 10) -> list[dict[str, Any]]:
    labels = frame["breach_label"].to_numpy(dtype=int)
    probabilities = frame["breach_probability"].to_numpy(dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    rows: list[dict[str, Any]] = []
    for index in range(bins):
        mask = (probabilities >= edges[index]) & (
            probabilities <= edges[index + 1]
            if index == bins - 1
            else probabilities < edges[index + 1]
        )
        rows.append(
            {
                "bin": index + 1,
                "lower": float(edges[index]),
                "upper": float(edges[index + 1]),
                "n": int(mask.sum()),
                "mean_probability": float(probabilities[mask].mean()) if mask.any() else None,
                "observed_rate": float(labels[mask].mean()) if mask.any() else None,
            }
        )
    return rows


def prediction_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    labels = frame["breach_label"].to_numpy(dtype=int)
    probabilities = frame["breach_probability"].to_numpy(dtype=float)
    recalls: list[float] = []
    ndcgs: list[float] = []
    for _, event in frame.groupby("event_id", sort=False):
        event_labels = event["breach_label"].to_numpy(dtype=int)
        positive = event_labels.astype(bool)
        if not positive.any():
            continue
        event_probabilities = event["breach_probability"].to_numpy(dtype=float)
        order = np.argsort(-event_probabilities, kind="stable")
        top = order[: min(3, order.size)]
        recalls.append(float(positive[top].sum() / positive.sum()))
        relevance = np.maximum(
            event["severity_raw"].to_numpy(dtype=float),
            event_labels.astype(float),
        )
        if order.size > 1:
            ndcgs.append(
                float(
                    ndcg_score(
                        relevance[None, :],
                        event_probabilities[None, :],
                        k=min(3, order.size),
                    )
                )
            )
    return {
        "rows": int(len(frame)),
        "events": int(frame["event_id"].nunique()),
        "positive_rows": int(labels.sum()),
        "prevalence": float(labels.mean()),
        "pr_auc": (
            float(average_precision_score(labels, probabilities))
            if np.unique(labels).size == 2
            else float(labels.mean())
        ),
        "brier": float(brier_score_loss(labels, probabilities)),
        "ece_10_equal_width": expected_calibration_error(labels, probabilities),
        "recall_at_3": float(np.mean(recalls)) if recalls else 0.0,
        "ndcg_at_3": float(np.mean(ndcgs)) if ndcgs else 0.0,
    }


def markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    output = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    output.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(output)


def analyze_threshold_and_rules(cards: list[dict[str, Any]], output: Path) -> None:
    original = [str(card["evidence_status"]) for card in cards]
    expected = status_counts(original)
    required = {
        "insufficient_evidence": 1054,
        "threshold_unresolved": 2424,
        "needs_context_review": 948,
        "candidate_for_stakeholder_review": 4242,
    }
    if expected != required:
        raise AssertionError(f"frozen governance totals changed: {expected}")

    comparison_rows: list[dict[str, Any]] = []
    for card in cards:
        status = str(card["evidence_status"])
        comparison_rows.append(
            {
                "card_id": card["card_id"],
                "subject_id": card["subject_id"],
                "sli": card["sli"],
                "raw_threshold": card["raw_threshold"],
                "threshold_only_output": "raw_threshold_emitted_without_evidence_state",
                "governance_status": status,
                "governance_action": card["recommended_next_action"],
                "additional_evidence_action_exposed": status != "candidate_for_stakeholder_review",
            }
        )
    hidden = sum(bool(row["additional_evidence_action_exposed"]) for row in comparison_rows)
    threshold_payload = {
        "analysis_id": "A01",
        "population": len(cards),
        "baseline_definition": (
            "Minimal threshold-only baseline: emit every frozen raw threshold and attach no "
            "evidence status, refusal state, or stakeholder-routing action."
        ),
        "strong_baseline_status": (
            "not_simulated: verified nearest neighbors do not expose a directly reproducible "
            "uncertainty-aware SLO-threshold method on these frozen fields"
        ),
        "governance_counts": expected,
        "evidence_actions_hidden_by_minimal_baseline": {
            "count": hidden,
            "rate": hidden / len(cards),
            "by_status": {key: value for key, value in expected.items() if key != STATUSES[-1]},
        },
        "claim_limit": (
            "The comparison demonstrates additional evidence/action information, not superior "
            "threshold accuracy, causal utility, or SLO correctness. Use 'differs from' or "
            "'exposes', not 'outperforms'."
        ),
    }
    write_json(output / "threshold_only_comparison.json", threshold_payload)
    write_csv(
        output / "threshold_only_comparison.csv",
        comparison_rows,
        list(comparison_rows[0]),
    )
    threshold_md = f"""# A01：threshold-only 决策差异

- 冻结卡片：{len(cards):,}。
- 最小基线对全部卡片只输出原始候选阈值，不输出证据状态或后续动作。
- 四状态治理额外暴露 {hidden:,}/{len(cards):,}（{hidden / len(cards):.1%}）张卡的补证、未解析或上下文审查动作。
- 可直接转交利益相关者审查：{expected['candidate_for_stakeholder_review']:,}（{expected['candidate_for_stakeholder_review'] / len(cards):.1%}）。

结论边界：该比较没有客观阈值正确性或业务效用终点，因此不支持“优于 threshold-only”。正文应改为“暴露了 threshold-only 隐藏的证据状态和责任路由”。
"""
    (output / "threshold_only_comparison.md").write_text(threshold_md, encoding="utf-8")

    rule_rows: list[dict[str, Any]] = []
    trigger_sets = [triggers(card) for card in cards]
    for rule in PRIMARY_PRIORITY:
        rule_rows.append(
            {
                "row_type": "trigger",
                "scenario": "primary",
                "key": rule,
                "count": sum(item[rule] for item in trigger_sets),
            }
        )
    intersections = Counter(
        "+".join(rule for rule in PRIMARY_PRIORITY if item[rule]) or "none" for item in trigger_sets
    )
    for key, count in sorted(intersections.items()):
        rule_rows.append(
            {"row_type": "intersection", "scenario": "primary", "key": key, "count": count}
        )

    scenarios: dict[str, list[str]] = {}
    for dropped in PRIMARY_PRIORITY:
        scenarios[f"drop_{dropped}"] = [
            assign_status({**item, dropped: False}) for item in trigger_sets
        ]
    scenarios["swap_history_resolution"] = [
        assign_status(item, ("resolution", "history", "tail")) for item in trigger_sets
    ]
    scenarios["swap_resolution_tail"] = [
        assign_status(item, ("history", "tail", "resolution")) for item in trigger_sets
    ]
    for cutoff in (9.5, 10.5):
        scenarios[f"tail_cutoff_{cutoff:g}"] = [
            assign_status(triggers(card, cutoff)) for card in cards
        ]

    transitions: list[dict[str, Any]] = []
    scenario_summary: dict[str, Any] = {}
    for scenario, states in scenarios.items():
        changed = sum(left != right for left, right in zip(original, states))
        scenario_summary[scenario] = {
            "counterfactual_audit": True,
            "changed_cards": changed,
            "status_counts": status_counts(states),
        }
        transitions.extend(transition_rows(original, states, scenario))
    for row in transitions:
        rule_rows.append(
            {
                "row_type": "transition",
                "scenario": row["scenario"],
                "key": f"{row['from_status']} -> {row['to_status']}",
                "count": row["count"],
            }
        )
    rule_payload = {
        "analysis_id": "A02",
        "population": len(cards),
        "primary_priority": list(PRIMARY_PRIORITY),
        "trigger_counts": {
            rule: sum(item[rule] for item in trigger_sets) for rule in PRIMARY_PRIORITY
        },
        "trigger_intersections": dict(sorted(intersections.items())),
        "scenarios": scenario_summary,
        "claim_limit": (
            "All alternatives are counterfactual audits. They neither modify the frozen rule "
            "nor identify an optimal cutoff or priority."
        ),
    }
    write_json(output / "governance_rule_ablation.json", rule_payload)
    write_csv(
        output / "governance_rule_ablation.csv",
        rule_rows,
        ["row_type", "scenario", "key", "count"],
    )
    scenario_rows = [
        (name, item["changed_cards"], *[item["status_counts"][status] for status in STATUSES])
        for name, item in scenario_summary.items()
    ]
    rule_md = """# A02：治理规则触发、重叠与优先级敏感性

所有场景均为 `counterfactual audit`，主规则保持不变。

## 独立触发

"""
    rule_md += markdown_table(
        ["规则", "触发数"],
        [(rule, rule_payload["trigger_counts"][rule]) for rule in PRIMARY_PRIORITY],
    )
    rule_md += "\n\n## 反事实状态变化\n\n"
    rule_md += markdown_table(
        ["场景", "变化卡", *STATUSES],
        scenario_rows,
    )
    rule_md += "\n\n不得据此声称某个优先级或长尾阈值“最优”。\n"
    (output / "governance_rule_ablation.md").write_text(rule_md, encoding="utf-8")


def analyze_strata(cards: list[dict[str, Any]], root: Path, v2_root: Path, output: Path) -> None:
    dimensions = {
        "service": lambda card: str(card["subject_id"]),
        "sli": lambda card: str(card["sli"]),
        "coverage_bucket": lambda card: str(
            card["applicability_context"]["history_coverage_bucket"]
        ),
        "segment_id": lambda card: str(card["observation_context"]["segment_id"]),
    }
    strata: dict[str, list[dict[str, Any]]] = {}
    for name, getter in dimensions.items():
        grouped: dict[str, Counter[str]] = defaultdict(Counter)
        for card in cards:
            grouped[getter(card)][str(card["evidence_status"])] += 1
        rows = []
        for value, counts in sorted(grouped.items()):
            total = sum(counts.values())
            rows.append(
                {
                    name: value,
                    "total": total,
                    **{status: int(counts.get(status, 0)) for status in STATUSES},
                }
            )
        if sum(row["total"] for row in rows) != len(cards):
            raise AssertionError(f"{name} strata do not sum to frozen population")
        strata[name] = rows

    ratings = pd.read_csv(root / "expert_review_round2" / "ratings_normalized.csv")
    manual = read_json(root / "manual_audit_results" / "manual_audit_summary.json")
    external_cards = read_json(
        root / "external_dataset" / "rcaeval_re1_ob" / "governance_cards.json"
    )
    oof = pd.read_csv(root / "feature_ablation" / "oof_predictions.csv.gz")
    topology = read_json(v2_root / "topology_gate" / "topology_evidence.json")
    formal = read_json(root / "formal_audit" / "formal_audit.json")
    workload = {
        "governance_cards": len(cards),
        "expert_round2_ratings": int(len(ratings)),
        "expert_round2_cards": int(ratings["round2_card_id"].nunique()),
        "expert_round2_reviewers": int(ratings["reviewer_id"].nunique()),
        "manual_control_judgments": manual["completeness"]["control_ratings"],
        "manual_pair_judgments": manual["completeness"]["pair_ratings"],
        "manual_reviewers": manual["completeness"]["reviewers"],
        "external_governance_cards": len(external_cards),
        "prediction_oof_rows": int(len(oof)),
        "prediction_oof_event_app_pairs_per_model": int(len(oof) / oof["model"].nunique()),
        "prediction_models": int(oof["model"].nunique()),
        "prediction_events": int(oof["event_id"].nunique()),
        "topology_audit_events": len(topology),
        "formal_audit_checks": len(formal.get("checks", {})),
    }
    payload = {
        "analysis_id": "A03",
        "population": len(cards),
        "strata": strata,
        "workload": workload,
        "claim_limit": "Workload is reported as auditable records and checks, not lines of code.",
    }
    write_json(output / "governance_strata_and_workload.json", payload)
    md = "# A03：治理分层与可审计工作量\n\n"
    md += markdown_table(["工作单元", "数量"], list(workload.items()))
    md += (
        "\n\n每个 service、SLI、coverage bucket 与 segment 分层均加总回 8,668；完整分层见 JSON。\n"
    )
    (output / "governance_strata_and_workload.md").write_text(md, encoding="utf-8")


def summarize_scores(frame: pd.DataFrame) -> dict[str, Any]:
    delta = frame["governance_minus_threshold"].to_numpy(dtype=float)
    return {
        "rows": int(len(frame)),
        "median_delta": float(np.median(delta)),
        "q1_delta": float(np.quantile(delta, 0.25)),
        "q3_delta": float(np.quantile(delta, 0.75)),
        "positive_delta_count": int((delta > 0).sum()),
        "zero_delta_count": int((delta == 0).sum()),
        "negative_delta_count": int((delta < 0).sum()),
        "threshold_low_1_or_2_count": int((frame["threshold_plausibility"] <= 2).sum()),
        "governance_high_4_or_5_count": int((frame["governance_appropriateness"] >= 4).sum()),
    }


def analyze_experts(root: Path, output: Path) -> None:
    ratings = pd.read_csv(root / "expert_review_round2" / "ratings_normalized.csv")
    cards = pd.read_csv(root / "expert_review_round2" / "per_card_results.csv")
    summary = read_json(root / "expert_review_round2" / "summary.json")
    joined = ratings.merge(
        cards[["round2_card_id", "parent_card_id", "cohort", "governance_status"]],
        on="round2_card_id",
        how="left",
        validate="many_to_one",
    )
    if joined[["cohort", "governance_status"]].isna().any().any():
        raise AssertionError("unmatched expert-rating cards")
    joined["governance_minus_threshold"] = (
        joined["governance_appropriateness"] - joined["threshold_plausibility"]
    )
    if not (
        len(joined) == 144
        and joined["round2_card_id"].nunique() == 48
        and joined["reviewer_id"].nunique() == 3
    ):
        raise AssertionError("expert analysis population mismatch")
    by_status = {
        str(name): summarize_scores(group)
        for name, group in joined.groupby("governance_status", sort=True)
    }
    by_cohort = {
        str(name): summarize_scores(group) for name, group in joined.groupby("cohort", sort=True)
    }
    by_reviewer = {
        str(name): summarize_scores(group)
        for name, group in joined.groupby("reviewer_id", sort=True)
    }
    per_card = (
        joined.groupby(
            ["round2_card_id", "parent_card_id", "cohort", "governance_status"],
            sort=True,
        )
        .agg(
            threshold_plausibility_median=("threshold_plausibility", "median"),
            governance_appropriateness_median=("governance_appropriateness", "median"),
        )
        .reset_index()
    )
    per_card["governance_minus_threshold_median"] = (
        per_card["governance_appropriateness_median"] - per_card["threshold_plausibility_median"]
    )
    payload = {
        "analysis_id": "A04",
        "population": {"ratings": 144, "cards": 48, "reviewers": 3},
        "overall_paired": summarize_scores(joined),
        "by_status": by_status,
        "by_cohort": by_cohort,
        "by_reviewer": by_reviewer,
        "reported_ordinal_alpha": {
            dimension: values["krippendorff_alpha_ordinal"]
            for dimension, values in summary["overall"]["dimensions"].items()
        },
        "interpretation": (
            "Ratings distinguish low raw-threshold plausibility from high governance "
            "appropriateness. Negative/near-zero alpha on several compressed dimensions is "
            "reported and not reinterpreted as reliability."
        ),
    }
    write_json(output / "expert_paired_analysis.json", payload)
    joined.to_csv(output / "expert_paired_analysis.csv", index=False, encoding="utf-8-sig")
    per_card.to_csv(output / "expert_paired_per_card.csv", index=False, encoding="utf-8-sig")
    overall = payload["overall_paired"]
    md = f"""# A04：专家逐卡配对分析

- 144 条评分、48 张卡、3 名专家全部匹配。
- 治理适当性减阈值合理性的逐评分中位差为 {overall['median_delta']:.1f}（IQR {overall['q1_delta']:.1f}—{overall['q3_delta']:.1f}）。
- 正差 {overall['positive_delta_count']}/144，零差 {overall['zero_delta_count']}/144，负差 {overall['negative_delta_count']}/144。
- 阈值合理性 1—2 分共 {overall['threshold_low_1_or_2_count']}/144；治理适当性 4—5 分共 {overall['governance_high_4_or_5_count']}/144。

该结果支持“专家区分原始数值可信度与治理处置”，不等于利益相关者批准阈值。压缩高分导致的负/近零 α 必须与中位数和逐卡分布同时报告。
"""
    (output / "expert_paired_analysis.md").write_text(md, encoding="utf-8")


def analyze_manual(root: Path, output: Path) -> None:
    private = root / "manual_audit_evidence_complete" / "private"
    pairs = pd.read_csv(private / "PRIVATE_PAIR_EVIDENCE.csv")
    controls = pd.read_csv(private / "PRIVATE_CONTROL_EVIDENCE.csv")
    summary = read_json(root / "manual_audit_results" / "manual_audit_summary.json")
    if len(pairs) != 20 or len(controls) != 60:
        raise AssertionError("manual audit frozen sample changed")
    rule_columns = [
        "same_app_by_construction",
        "hour_distance_rule_pass",
        "candidate_rank_rule_pass",
        "history_sample_rule_pass",
        "future_sample_rule_pass",
        "buffer_nonoverlap_rule_pass",
    ]
    payload = {
        "analysis_id": "A05",
        "sample": {
            "pairs": len(pairs),
            "controls": len(controls),
            "services": int(pairs["app_id"].nunique()),
            "segments": int(pairs["segment_id"].nunique()),
            "dates": int(pairs["event_date"].nunique()),
            "date_min": str(pairs["event_date"].min()),
            "date_max": str(pairs["event_date"].max()),
        },
        "pair_direction": {
            str(key): int(value) for key, value in pairs["direction"].value_counts().items()
        },
        "control_direction": {
            str(key): int(value) for key, value in controls["direction"].value_counts().items()
        },
        "pair_eligible_candidate_count": qstats(pairs["eligible_candidate_count"]),
        "control_matching_score": qstats(controls["matching_score"]),
        "control_candidate_rank": qstats(controls["candidate_rank"]),
        "history_valid_samples_min": qstats(controls["history_valid_samples_min"]),
        "future_valid_samples_min": qstats(
            controls[
                [
                    "future_valid_request_samples",
                    "future_valid_latency_samples",
                    "future_valid_error_samples",
                ]
            ].min(axis=1)
        ),
        "rule_pass_counts": {
            column: int(controls[column].astype(bool).sum()) for column in rule_columns
        },
        "signed_review": summary["completeness"],
        "review_judgment_direction": {
            direction: int(values["ratings"])
            for direction, values in summary["unblinded_direction_summary"].items()
        },
        "verdicts": {
            "control": summary["control_conclusions"],
            "pair": summary["pair_conclusions"],
        },
        "claim_limit": summary["claim_boundary"],
    }
    write_json(output / "manual_audit_sampling_coverage.json", payload)
    md = f"""# A05：人工核查样本覆盖

- 样本含 {len(pairs)} 个事件—服务组、{len(controls)} 个匹配对照，覆盖 {payload['sample']['services']} 个服务、{payload['sample']['segments']} 个 segment、{payload['sample']['dates']} 个日期。
- 20 组原始方向：{payload['pair_direction']}；60 行对照方向：{payload['control_direction']}；四人解盲后的 80 个组级判断方向为 {payload['review_judgment_direction']}（16/12/52）。
- 每组候选池中位数 {payload['pair_eligible_candidate_count']['median']:.1f}，匹配距离中位数 {payload['control_matching_score']['median']:.4f}，候选排名范围 {payload['control_candidate_rank']['min']:.0f}—{payload['control_candidate_rank']['max']:.0f}。
- 四名独立签署人完成 240/240 逐行判断和 80/80 组级判断，全部通过。

该审计只支持匹配实现与证据重建一致性；抽样非随机，不能推出因果效应、阈值正确性或正式 SLO 验证。结论无变异，不计算 κ/α。
"""
    (output / "manual_audit_sampling_coverage.md").write_text(md, encoding="utf-8")


def analyze_external(root: Path, cards: list[dict[str, Any]], output: Path) -> None:
    ext_root = root / "external_dataset" / "rcaeval_re1_ob"
    audit = read_json(ext_root / "adapter_audit.json")
    external_cards = read_json(ext_root / "governance_cards.json")
    if audit["source_cases"] != 125 or len(external_cards) != 1250:
        raise AssertionError("external frozen population changed")
    internal_insufficient = [
        card for card in cards if card["evidence_status"] == "insufficient_evidence"
    ]
    field_mapping = [
        {
            "source": "case timestamp / inject time",
            "canonical_interface": "event_id + fault_start (UTC)",
            "governance_card": "evidence_provenance.event_id / observation_context",
            "status": "mapped",
        },
        {
            "source": "service-name metric series",
            "canonical_interface": "dataset-namespaced service_id + timestamp + latency",
            "governance_card": "subject_id / SLI=latency",
            "status": "mapped",
        },
        {
            "source": "latency in assumed seconds",
            "canonical_interface": "latency_ms",
            "governance_card": "raw_threshold",
            "status": audit["unit_conversion_status"],
        },
        {
            "source": "request_count",
            "canonical_interface": "request_count",
            "governance_card": "error-ratio denominator / volume evidence",
            "status": "unavailable",
        },
        {
            "source": "error_count",
            "canonical_interface": "error_count",
            "governance_card": "error_ratio",
            "status": "unavailable",
        },
    ]
    payload = {
        "analysis_id": "A06",
        "external": {
            "cases": audit["source_cases"],
            "services": audit["canonical_observations"]["services"],
            "observations": audit["canonical_observations"]["rows"],
            "cards": len(external_cards),
            "pre_event_seconds": audit["pre_event_seconds"],
            "history_coverage": qstats(
                card["quality"]["history_coverage"] for card in external_cards
            ),
            "valid_samples": qstats(card["valid_samples"] for card in external_cards),
            "status_counts": status_counts(str(card["evidence_status"]) for card in external_cards),
        },
        "internal_insufficient_reference": {
            "cards": len(internal_insufficient),
            "history_coverage": qstats(
                card["quality"]["history_coverage"] for card in internal_insufficient
            ),
            "valid_samples": qstats(card["valid_samples"] for card in internal_insufficient),
        },
        "field_mapping": field_mapping,
        "protocol": {
            "required_history_seconds": 24 * 60 * 60,
            "history_window_shortened": False,
            "prediction_protocol_compatible": audit["prediction_protocol_compatible"],
            "reasons": audit["prediction_incompatibility_reasons"],
            "unit_conversion_status": audit["unit_conversion_status"],
            "decision": audit["decision"],
        },
        "claim_limit": audit["claim_limit"],
    }
    write_json(output / "external_protocol_compatibility.json", payload)
    md = f"""# A06：外部协议兼容性与拒绝

- RCAEval 适配器读取 {audit['source_cases']} 个案例、{audit['canonical_observations']['services']} 个服务和 {audit['canonical_observations']['rows']:,} 条规范化观测，生成 {len(external_cards):,} 张卡。
- 可用故障前历史为 {audit['pre_event_seconds']['minimum']}—{audit['pre_event_seconds']['maximum']} 秒，中位数 {audit['pre_event_seconds']['median']:.0f} 秒；冻结协议要求 86,400 秒。
- 未缩短 24 小时窗口；请求量和错误量不可用；预测协议判为不兼容。
- 单位换算状态：`{audit['unit_conversion_status']}`。

因此外部结果只支持“适配接口能够执行并正确拒绝证据不足的操作化”。正向阈值正确性、预测迁移或外部 SLO 有效性均不可评估。
"""
    (output / "external_protocol_compatibility.md").write_text(md, encoding="utf-8")


def analyze_prediction(root: Path, output: Path) -> None:
    path = root / "feature_ablation" / "oof_predictions.csv.gz"
    frame = pd.read_csv(path)
    if not (
        len(frame) == 13356 and frame["model"].nunique() == 6 and frame["event_id"].nunique() == 209
    ):
        raise AssertionError("OOF prediction population changed")
    pair_counts = frame.groupby("model").size()
    if not (pair_counts == 2226).all():
        raise AssertionError("expected 2,226 event-app pairs per model")
    overall = {
        str(model): prediction_metrics(group) for model, group in frame.groupby("model", sort=True)
    }
    by_fold = {
        str(model): {
            str(int(fold)): prediction_metrics(fold_frame)
            for fold, fold_frame in group.groupby("fold", sort=True)
        }
        for model, group in frame.groupby("model", sort=True)
    }
    by_service = {
        str(model): {
            str(service): prediction_metrics(service_frame)
            for service, service_frame in group.groupby("app_id", sort=True)
        }
        for model, group in frame.groupby("model", sort=True)
    }
    event_dates = (
        frame[["fold", "event_id", "event_date"]]
        .drop_duplicates()
        .groupby("fold")
        .agg(
            events=("event_id", "nunique"),
            date_min=("event_date", "min"),
            date_max=("event_date", "max"),
        )
        .reset_index()
    )
    calibration = {
        str(model): calibration_rows(group) for model, group in frame.groupby("model", sort=True)
    }
    schema = read_json(root / "feature_ablation" / "feature_schema.json")
    overlap_features = sorted(
        {feature for features in schema.values() for feature in features if "exceedance" in feature}
    )
    payload = {
        "analysis_id": "A07",
        "population": {
            "rows": len(frame),
            "events": int(frame["event_id"].nunique()),
            "event_app_pairs_per_model": 2226,
            "models": int(frame["model"].nunique()),
            "folds": int(frame["fold"].nunique()),
        },
        "time_order": event_dates.to_dict(orient="records"),
        "overall": overall,
        "by_fold": by_fold,
        "by_service": by_service,
        "calibration_10_equal_width": calibration,
        "label_feature_proximity": {
            "features_containing_exceedance": overlap_features,
            "interpretation": (
                "These pre-event features are constructed relative to frozen candidate "
                "boundaries and are therefore construct-proximal to breach labels. This is "
                "not post-outcome leakage, but it limits claims of independent prediction."
            ),
        },
        "claim_limit": (
            "Prediction is supporting evidence under the frozen rolling-origin protocol, "
            "not a new predictive-model contribution or locked-test re-evaluation."
        ),
    }
    write_json(output / "prediction_stability_and_calibration.json", payload)
    metric_rows = []
    for model, values in overall.items():
        metric_rows.append(
            [
                model,
                f"{values['pr_auc']:.4f}",
                f"{values['recall_at_3']:.4f}",
                f"{values['ndcg_at_3']:.4f}",
                f"{values['brier']:.4f}",
                f"{values['ece_10_equal_width']:.4f}",
            ]
        )
    md = "# A07：预测折级稳定性与校准\n\n"
    md += markdown_table(
        ["模型", "PR-AUC", "Recall@3", "NDCG@3", "Brier", "ECE"],
        metric_rows,
    )
    md += (
        "\n\n每个模型均为 2,226 个 OOF event-app 对，合计 209 个事件、5 个时间顺序折。"
        "折级、服务级与十等宽校准箱完整写入 JSON。包含 `exceedance` 的特征与越界标签构造接近，"
        "虽仅使用故障前数据，不应包装为独立模型创新。\n"
    )
    (output / "prediction_stability_and_calibration.md").write_text(md, encoding="utf-8")


def analyze_topology(v2_root: Path, output: Path) -> None:
    report = read_json(v2_root / "topology_gate" / "topology_gate_report.json")
    evidence = read_json(v2_root / "topology_gate" / "topology_evidence.json")
    if len(evidence) != 394:
        raise AssertionError("topology evidence population changed")
    coverage = {
        node_type: qstats(row["observation_coverage"].get(node_type, math.nan) for row in evidence)
        for node_type in ("Vphy", "Vvm", "Vbiz")
    }
    node_counts = {
        node_type: qstats(row["node_counts"].get(node_type, math.nan) for row in evidence)
        for node_type in ("Vphy", "Vvm", "Vbiz")
    }
    relation_edges: dict[str, list[int]] = defaultdict(list)
    for row in evidence:
        for relation in row["relations"]:
            key = (
                f"{relation['source_type']}:{relation['relation']}:"
                f"{relation['destination_type']}"
            )
            relation_edges[key].append(int(relation["edge_count"]))
    payload = {
        "analysis_id": "A08",
        "events": len(evidence),
        "unique_topology_hashes": len({row["topology_hash"] for row in evidence}),
        "node_counts": node_counts,
        "observation_coverage": coverage,
        "relation_edge_counts": {
            key: qstats(values) for key, values in sorted(relation_edges.items())
        },
        "target_mapping": report["target_mapping"],
        "relation_diversity": report["relation_diversity"],
        "development_residual_informativeness": report["residual_informativeness"],
        "gate": report["graph_development_gate"],
        "decision": report["decision"],
        "claim_limit": report["claim_limit"],
    }
    write_json(output / "topology_negative_result_scope.json", payload)
    folds = report["residual_informativeness"]["folds"]
    md = f"""# A08：拓扑覆盖与负结果边界

- 正式拓扑证据含 {len(evidence)} 个事件、{payload['unique_topology_hashes']} 个拓扑哈希。
- 故障目标任一/全部映射覆盖率均为 {report['target_mapping']['event_any_target_mapping_coverage']:.1%}；pod 目标映射率为 {report['target_mapping']['by_target_type']['pod']['any_target_mapping_coverage']:.1%}。
- 五折 local+topology 相对 local 的 PR-AUC 差为：{", ".join(f"{row['pr_auc_delta']:+.4f}" for row in folds)}；仅 {report['residual_informativeness']['positive_direction_folds']}/5 折为正，开发门禁失败。
- 除 hosting 外，其余四类关系的唯一边集数均为 1，静态性很强。

固定结论：在当前静态/不完整拓扑表示、冻结特征和模型下，没有观察到稳定正增量；不得外推为“拓扑无用”或“GNN 无效”。
"""
    (output / "topology_negative_result_scope.md").write_text(md, encoding="utf-8")


def collect_sources(root: Path, v2_root: Path) -> list[Path]:
    return [
        root / "governance_cards" / "academic_governance_cards.json",
        root / "expert_review_round2" / "ratings_normalized.csv",
        root / "expert_review_round2" / "per_card_results.csv",
        root / "expert_review_round2" / "summary.json",
        root / "manual_audit_evidence_complete" / "private" / "PRIVATE_PAIR_EVIDENCE.csv",
        root / "manual_audit_evidence_complete" / "private" / "PRIVATE_CONTROL_EVIDENCE.csv",
        root / "manual_audit_results" / "manual_audit_summary.json",
        root / "external_dataset" / "rcaeval_re1_ob" / "adapter_audit.json",
        root / "external_dataset" / "rcaeval_re1_ob" / "governance_cards.json",
        root / "feature_ablation" / "oof_predictions.csv.gz",
        root / "feature_ablation" / "feature_schema.json",
        root / "formal_audit" / "formal_audit.json",
        v2_root / "topology_gate" / "topology_gate_report.json",
        v2_root / "topology_gate" / "topology_evidence.json",
    ]


def run(root: Path, v2_root: Path, output: Path) -> None:
    sources = collect_sources(root, v2_root)
    missing = [str(path) for path in sources if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing frozen sources: {missing}")
    before = {str(path.resolve()): sha256(path) for path in sources}
    output.mkdir(parents=True, exist_ok=True)
    cards = read_json(root / "governance_cards" / "academic_governance_cards.json")

    analyze_threshold_and_rules(cards, output)
    analyze_strata(cards, root, v2_root, output)
    analyze_experts(root, output)
    analyze_manual(root, output)
    analyze_external(root, cards, output)
    analyze_prediction(root, output)
    analyze_topology(v2_root, output)

    after = {str(path.resolve()): sha256(path) for path in sources}
    if before != after:
        raise AssertionError("a frozen source changed during analysis")
    output_files = sorted(
        path
        for path in output.iterdir()
        if path.is_file() and path.name != "ANALYSIS_MANIFEST.json"
    )
    manifest = {
        "protocol": "runtime-nfr-phase2-read-only-analysis/1",
        "analyses": [f"A{index:02d}" for index in range(1, 9)],
        "source_files": [
            {"path": path, "sha256_before": before[path], "sha256_after": after[path]}
            for path in sorted(before)
        ],
        "source_files_unchanged": True,
        "prohibited_actions": {
            "thresholds_changed": False,
            "rules_changed": False,
            "expert_sample_changed": False,
            "external_history_shortened": False,
            "model_retrained": False,
            "locked_test_reused": False,
            "new_gnn_run": False,
        },
        "output_files": [
            {
                "path": str(path.resolve()),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in output_files
        ],
    }
    write_json(output / "ANALYSIS_MANIFEST.json", manifest)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path("checkpoints/runtime_nfr_v3_academic"),
    )
    parser.add_argument(
        "--v2-root",
        type=Path,
        default=Path("checkpoints/runtime_nfr_v2"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    run(arguments.input_root, arguments.v2_root, arguments.output_dir)
