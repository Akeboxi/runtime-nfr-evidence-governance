"""Generate Phase-3 read-only content analyses for the Runtime-NFR paper.

This command only derives stratified summaries and compact audit tables from
already frozen Phase-2 inputs.  It does not retrain models, change governance
rules, select a new cutoff, or overwrite Phase-2 artifacts.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd

try:
    from scripts.analyze_runtime_nfr_phase2 import (
        PRIMARY_PRIORITY,
        STATUSES,
        assign_status,
        sha256,
        status_counts,
        triggers,
    )
except ModuleNotFoundError:  # Support direct execution: python scripts/<file>.py
    from analyze_runtime_nfr_phase2 import (  # type: ignore[no-redef]
        PRIMARY_PRIORITY,
        STATUSES,
        assign_status,
        sha256,
        status_counts,
        triggers,
    )


FROZEN_COUNTS = {
    "insufficient_evidence": 1054,
    "threshold_unresolved": 2424,
    "needs_context_review": 948,
    "candidate_for_stakeholder_review": 4242,
}


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write an empty table: {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    output = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    output.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(output)


def evidence_date(card: Mapping[str, Any]) -> str:
    timestamp = str(card["evidence_provenance"]["evidence_cutoff"])
    return timestamp[:10]


def threshold_only_strata(
    cards: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dimensions: dict[str, Callable[[Mapping[str, Any]], str]] = {
        "service": lambda card: str(card["subject_id"]),
        "sli": lambda card: str(card["sli"]),
        "segment": lambda card: str(card["observation_context"]["segment_id"]),
        "event_date": evidence_date,
        "coverage_bucket": lambda card: str(
            card["applicability_context"]["history_coverage_bucket"]
        ),
    }
    payload_dimensions: dict[str, Any] = {}
    flat_rows: list[dict[str, Any]] = []
    for dimension, getter in dimensions.items():
        grouped: dict[str, Counter[str]] = defaultdict(Counter)
        for card in cards:
            grouped[getter(card)][str(card["evidence_status"])] += 1
        rows: list[dict[str, Any]] = []
        for value, counts in sorted(grouped.items()):
            total = int(sum(counts.values()))
            hidden = total - int(counts.get("candidate_for_stakeholder_review", 0))
            row = {
                "dimension": dimension,
                "value": value,
                "total": total,
                "hidden_evidence_or_action": hidden,
                "hidden_rate": hidden / total,
                **{status: int(counts.get(status, 0)) for status in STATUSES},
            }
            rows.append(row)
            flat_rows.append(row)
        if sum(row["total"] for row in rows) != len(cards):
            raise AssertionError(f"{dimension} strata do not sum to the frozen population")
        payload_dimensions[dimension] = {
            "groups": len(rows),
            "rows": rows,
            "minimum_hidden_rate": min(row["hidden_rate"] for row in rows),
            "maximum_hidden_rate": max(row["hidden_rate"] for row in rows),
        }
    hidden_total = sum(
        str(card["evidence_status"]) != "candidate_for_stakeholder_review"
        for card in cards
    )
    payload = {
        "analysis_id": "P3-A01",
        "population": len(cards),
        "threshold_only_definition": (
            "Emit each frozen raw threshold without evidence state, refusal reason, "
            "or responsibility route."
        ),
        "hidden_evidence_or_action": {
            "count": hidden_total,
            "rate": hidden_total / len(cards),
        },
        "dimensions": payload_dimensions,
        "claim_limit": (
            "The strata describe where evidence/action responsibility would be hidden. "
            "They are not error rates, accuracy estimates, or evidence that governance "
            "outperforms a threshold estimator."
        ),
    }
    return payload, flat_rows


def counterfactual_transitions(
    cards: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    baseline = [str(card["evidence_status"]) for card in cards]
    trigger_sets = [triggers(card) for card in cards]
    scenarios: dict[str, list[str]] = {}
    for dropped in PRIMARY_PRIORITY:
        scenarios[f"drop_{dropped}"] = [
            assign_status({**values, dropped: False}) for values in trigger_sets
        ]
    scenarios["swap_history_resolution"] = [
        assign_status(values, ("resolution", "history", "tail")) for values in trigger_sets
    ]
    scenarios["swap_resolution_tail"] = [
        assign_status(values, ("history", "tail", "resolution")) for values in trigger_sets
    ]
    for cutoff in (9.5, 10.0, 10.5):
        scenarios[f"tail_cutoff_{cutoff:g}"] = [
            assign_status(triggers(card, cutoff)) for card in cards
        ]

    flat_rows: list[dict[str, Any]] = []
    summaries: dict[str, Any] = {}
    for scenario, counterfactual in scenarios.items():
        matrix = Counter(zip(baseline, counterfactual))
        changed = sum(left != right for left, right in zip(baseline, counterfactual))
        rows = []
        for source in STATUSES:
            for target in STATUSES:
                count = int(matrix.get((source, target), 0))
                row = {
                    "scenario": scenario,
                    "from_status": source,
                    "to_status": target,
                    "count": count,
                    "changed": source != target,
                }
                rows.append(row)
                flat_rows.append(row)
        if sum(row["count"] for row in rows) != len(cards):
            raise AssertionError(f"{scenario} transition matrix is not lossless")
        summaries[scenario] = {
            "changed_cards": changed,
            "status_counts": status_counts(counterfactual),
            "nonzero_transitions": [row for row in rows if row["count"]],
        }
    payload = {
        "analysis_id": "P3-A02",
        "population": len(cards),
        "baseline_status_counts": status_counts(baseline),
        "scenarios": summaries,
        "claim_limit": (
            "Every scenario is a counterfactual audit of the frozen policy. "
            "No scenario is used to select an optimal rule, priority, or cutoff."
        ),
    }
    return payload, flat_rows


def expert_strata(
    root: Path,
) -> dict[str, Any]:
    phase2 = read_json(root / "phase2_analysis_v1" / "expert_paired_analysis.json")
    summary = read_json(root / "expert_review_round2" / "summary.json")
    if phase2["overall_paired"]["rows"] != 144:
        raise AssertionError("expert paired population changed")
    return {
        "analysis_id": "P3-A03",
        "population": phase2["population"],
        "overall_paired": phase2["overall_paired"],
        "by_cohort": phase2["by_cohort"],
        "by_governance_status": phase2["by_status"],
        "by_reviewer": phase2["by_reviewer"],
        "state_error_comment_rows": summary["state_error_comment_rows"],
        "primary_endpoint": summary["primary_endpoint"],
        "claim_limit": (
            "The expert evidence distinguishes threshold plausibility from governance "
            "appropriateness. It is not stakeholder approval and cross-round changes "
            "are not causal effects."
        ),
    }


def directory_inventory(path: Path) -> dict[str, Any]:
    files = sorted(item for item in path.rglob("*") if item.is_file())
    return {
        "path": str(path.resolve()),
        "files": len(files),
        "bytes": int(sum(item.stat().st_size for item in files)),
    }


def reproducibility_workload(
    root: Path,
    v2_root: Path,
    repository_root: Path,
    elapsed_seconds: float,
) -> dict[str, Any]:
    round1 = read_json(v2_root / "expert_review" / "summary.json")
    round2 = read_json(root / "expert_review_round2" / "summary.json")
    manual = read_json(root / "manual_audit_results" / "manual_audit_summary.json")
    external = read_json(root / "external_dataset" / "rcaeval_re1_ob" / "adapter_audit.json")
    topology = read_json(v2_root / "topology_gate" / "topology_gate_report.json")
    topology_evidence = read_json(v2_root / "topology_gate" / "topology_evidence.json")
    oof = pd.read_csv(root / "feature_ablation" / "oof_predictions.csv.gz")
    test_files = sorted((repository_root / "tests").glob("test_runtime_nfr*.py"))
    test_functions = 0
    for path in test_files:
        test_functions += sum(
            line.startswith("def test_")
            for line in path.read_text(encoding="utf-8").splitlines()
        )
    workload = {
        "internal_governance_cards": 8668,
        "expert_rounds": 2,
        "expert_reviewers": (
            int(round1["validation"]["reviewers"])
            + int(round2["overall"]["ratings"] / round2["overall"]["cards"])
        ),
        "expert_card_review_units": (
            int(round1["validation"]["cards"]) + int(round2["overall"]["cards"])
        ),
        "expert_rating_rows": (
            int(round1["validation"]["rows"]) + int(round2["overall"]["ratings"])
        ),
        "manual_control_judgments": int(manual["completeness"]["control_ratings"]),
        "manual_group_judgments": int(manual["completeness"]["pair_ratings"]),
        "external_cases": int(external["source_cases"]),
        "external_services": int(external["canonical_observations"]["services"]),
        "external_observation_rows": int(external["canonical_observations"]["rows"]),
        "external_governance_cards": len(
            read_json(root / "external_dataset" / "rcaeval_re1_ob" / "governance_cards.json")
        ),
        "prediction_models": int(oof["model"].nunique()),
        "prediction_events": int(oof["event_id"].nunique()),
        "prediction_oof_rows": int(len(oof)),
        "topology_events": int(topology["target_mapping"]["events"]),
        "topology_hashes": len(
            {str(row["topology_hash"]) for row in topology_evidence}
        ),
        "runtime_nfr_test_files": len(test_files),
        "runtime_nfr_test_functions_discovered": test_functions,
    }
    inventories = [
        directory_inventory(root / "governance_cards"),
        directory_inventory(root / "expert_review_round2"),
        directory_inventory(root / "manual_audit_results"),
        directory_inventory(root / "external_dataset" / "rcaeval_re1_ob"),
        directory_inventory(root / "feature_ablation"),
        directory_inventory(root / "phase2_analysis_v1"),
    ]
    return {
        "analysis_id": "P3-A04",
        "workload": workload,
        "artifact_inventory": inventories,
        "reproduction_commands": [
            (
                "python scripts/analyze_runtime_nfr_phase2.py "
                "--output-dir checkpoints/runtime_nfr_v3_academic/phase2_analysis_v1"
            ),
            (
                "python scripts/analyze_runtime_nfr_phase3.py "
                "--output-dir checkpoints/runtime_nfr_v3_academic/"
                "phase3_content_analysis_v1"
            ),
            "pytest -q tests/test_runtime_nfr*.py",
            "python scripts/audit_runtime_nfr_submission.py",
        ],
        "resource_reporting": {
            "historical_pipeline_wall_clock": "not_recorded",
            "historical_pipeline_cpu": "not_recorded",
            "historical_pipeline_peak_memory": "not_recorded",
            "phase3_read_only_analysis_elapsed_seconds": elapsed_seconds,
        },
        "claim_limit": (
            "Counts and artifact sizes describe auditable workload. Historical CPU, "
            "memory, and wall-clock cost were not captured uniformly and are not estimated."
        ),
    }


def source_paths(root: Path, v2_root: Path) -> list[Path]:
    return [
        root / "governance_cards" / "academic_governance_cards.json",
        root / "phase2_analysis_v1" / "expert_paired_analysis.json",
        root / "expert_review_round2" / "summary.json",
        root / "manual_audit_results" / "manual_audit_summary.json",
        root / "external_dataset" / "rcaeval_re1_ob" / "adapter_audit.json",
        root / "external_dataset" / "rcaeval_re1_ob" / "governance_cards.json",
        root / "feature_ablation" / "oof_predictions.csv.gz",
        v2_root / "expert_review" / "summary.json",
        v2_root / "topology_gate" / "topology_gate_report.json",
        v2_root / "topology_gate" / "topology_evidence.json",
    ]


def run(root: Path, v2_root: Path, output: Path, repository_root: Path) -> None:
    started = time.perf_counter()
    sources = source_paths(root, v2_root)
    missing = [str(path) for path in sources if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing frozen sources: {missing}")
    before = {str(path.resolve()): sha256(path) for path in sources}
    output.mkdir(parents=True, exist_ok=True)

    cards = read_json(root / "governance_cards" / "academic_governance_cards.json")
    if len(cards) != 8668 or status_counts(
        str(card["evidence_status"]) for card in cards
    ) != FROZEN_COUNTS:
        raise AssertionError("frozen governance population changed")

    strata_payload, strata_rows = threshold_only_strata(cards)
    write_json(output / "threshold_only_strata.json", strata_payload)
    write_csv(output / "threshold_only_strata.csv", strata_rows)
    sli_rows = strata_payload["dimensions"]["sli"]["rows"]
    service_rows = strata_payload["dimensions"]["service"]["rows"]
    strata_md = "# P3-A01：threshold-only 责任暴露分层\n\n"
    strata_md += (
        "最小 threshold-only 输出会隐藏 "
        f"{strata_payload['hidden_evidence_or_action']['count']:,}/{len(cards):,}"
        f"（{strata_payload['hidden_evidence_or_action']['rate']:.1%}）张卡的证据状态"
        "或责任动作。该比例不是错误率或准确率。\n\n"
    )
    strata_md += "## 按 SLI\n\n"
    strata_md += markdown_table(
        ["SLI", "卡片", "隐藏责任", "比例"],
        [
            (
                row["value"],
                row["total"],
                row["hidden_evidence_or_action"],
                f"{row['hidden_rate']:.1%}",
            )
            for row in sli_rows
        ],
    )
    strata_md += "\n\n## 服务范围\n\n"
    strata_md += (
        f"11 个服务的隐藏比例为 "
        f"{min(row['hidden_rate'] for row in service_rows):.1%}–"
        f"{max(row['hidden_rate'] for row in service_rows):.1%}；"
        "完整 service、segment、date 和 coverage 分层见 JSON/CSV。\n"
    )
    (output / "threshold_only_strata.md").write_text(strata_md, encoding="utf-8")

    transition_payload, transition_table = counterfactual_transitions(cards)
    write_json(output / "governance_transition_matrix.json", transition_payload)
    write_csv(output / "governance_transition_matrix.csv", transition_table)
    transition_md = "# P3-A02：原状态→反事实状态迁移\n\n"
    transition_md += markdown_table(
        ["场景", "改变卡片"],
        [
            (scenario, values["changed_cards"])
            for scenario, values in transition_payload["scenarios"].items()
        ],
    )
    transition_md += (
        "\n\n完整 4×4 迁移矩阵见 CSV/JSON。所有场景仅解释冻结政策如何改变处置，"
        "不用于重选规则、优先级或长尾阈值。\n"
    )
    (output / "governance_transition_matrix.md").write_text(
        transition_md, encoding="utf-8"
    )

    expert_payload = expert_strata(root)
    write_json(output / "expert_strata_summary.json", expert_payload)
    expert_md = """# P3-A03：专家结果分层

- 总体 144 个专家—卡配对中，治理适当性高于阈值合理性 143 次、相等 1 次、下降 0 次。
- 修复队列 72/72 为正；留出队列 71/72 为正、1 次相等。
- 四状态配对差中位数依次为 3、3、2.5 和 1。
- 保留 1 条 `STATE_ERROR` 边界异议；它限制 10.0 的解释，不用于重选门限。

该证据支持专家区分两类评价对象，不构成利益相关者批准或跨轮因果效果。
"""
    (output / "expert_strata_summary.md").write_text(expert_md, encoding="utf-8")

    workload_payload = reproducibility_workload(
        root,
        v2_root,
        repository_root,
        time.perf_counter() - started,
    )
    write_json(output / "reproducibility_workload.json", workload_payload)
    workload_md = "# P3-A04：可审计工作量与复现成本\n\n"
    workload_md += markdown_table(
        ["工作单元", "数量"],
        workload_payload["workload"].items(),
    )
    workload_md += (
        "\n\n历史 CPU、峰值内存和墙钟时间未统一记录，保持 `not_recorded`；"
        "不进行事后估算或为计时重跑重型实验。\n"
    )
    (output / "reproducibility_workload.md").write_text(
        workload_md, encoding="utf-8"
    )

    after = {str(path.resolve()): sha256(path) for path in sources}
    if before != after:
        raise AssertionError("a frozen source changed during Phase-3 analysis")
    output_files = sorted(
        path
        for path in output.iterdir()
        if path.is_file() and path.name != "PHASE3_ANALYSIS_MANIFEST.json"
    )
    manifest = {
        "protocol": "runtime-nfr-phase3-read-only-content-analysis/1",
        "analyses": ["P3-A01", "P3-A02", "P3-A03", "P3-A04"],
        "frozen_status_counts": FROZEN_COUNTS,
        "threshold_only_hidden": 4426,
        "source_files_unchanged": True,
        "source_files": [
            {
                "path": path,
                "sha256_before": before[path],
                "sha256_after": after[path],
            }
            for path in sorted(before)
        ],
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
    write_json(output / "PHASE3_ANALYSIS_MANIFEST.json", manifest)


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
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path("."),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    run(
        arguments.input_root,
        arguments.v2_root,
        arguments.output_dir,
        arguments.repository_root,
    )
