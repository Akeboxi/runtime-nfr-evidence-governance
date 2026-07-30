"""Build manuscript-ready Runtime NFR tables only from frozen machine outputs."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
from typing import Any, Mapping

import numpy as np
import pandas as pd


PAPER_TABLE_PROTOCOL = "runtime-nfr-v3-paper-tables/1"


def _load(path_value: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = Path(path_value)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload, {
        "path": str(path.resolve()),
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def _dimension_row(name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "dimension": name,
        "median": payload.get("median"),
        "iqr": payload.get("iqr"),
        "low_score_rate_1_or_2": payload.get("low_score_rate_1_or_2"),
        "ordinal_alpha": payload.get("krippendorff_alpha_ordinal"),
    }


def build_matched_control_manual_audit_sample(
    matched_rows_path: str | Path,
    output_dir: str | Path,
    *,
    count: int = 20,
    seed: int = 20260723,
) -> dict[str, Any]:
    """Select a fixed, diverse checklist sample without altering the analysis."""

    source = Path(matched_rows_path)
    rows = list(json.loads(source.read_text(encoding="utf-8")))
    if len(rows) < count:
        raise ValueError(f"only {len(rows)} matched rows available; {count} required")
    rng = np.random.default_rng(seed)
    rng.shuffle(rows)
    selected: list[dict[str, Any]] = []
    seen: dict[str, set[str]] = {
        "segment_id": set(),
        "app_id": set(),
        "direction": set(),
    }
    while rows and len(selected) < count:
        best_index = 0
        best_score = -1
        for index, row in enumerate(rows):
            difference = float(row["event_minus_control"])
            direction = "positive" if difference > 0 else "negative" if difference < 0 else "zero"
            strata = {
                "segment_id": str(row["segment_id"]),
                "app_id": str(row["app_id"]),
                "direction": direction,
            }
            score = sum(value not in seen[key] for key, value in strata.items())
            if score > best_score:
                best_index = index
                best_score = score
        winner = rows.pop(best_index)
        difference = float(winner["event_minus_control"])
        direction = "positive" if difference > 0 else "negative" if difference < 0 else "zero"
        seen["segment_id"].add(str(winner["segment_id"]))
        seen["app_id"].add(str(winner["app_id"]))
        seen["direction"].add(direction)
        selected.append({**winner, "direction": direction})
    frame = pd.DataFrame(selected)
    for column in (
        "event_window_verified",
        "control_non_event_verified",
        "same_app_verified",
        "hour_of_day_match_verified",
        "request_volume_match_verified",
        "auditor_notes",
    ):
        frame[column] = ""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    checklist = output / "matched_control_manual_audit_20.csv"
    frame.to_csv(checklist, index=False, encoding="utf-8-sig")
    manifest = {
        "protocol": PAPER_TABLE_PROTOCOL,
        "source": str(source.resolve()),
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
        "seed": seed,
        "requested_rows": count,
        "selected_rows": len(selected),
        "selection": (
            "fixed-seed greedy diversity over segment, application, and "
            "event-minus-control direction"
        ),
        "analysis_values_changed": False,
        "checklist": str(checklist.resolve()),
    }
    manifest_path = output / "matched_control_manual_audit_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def build_runtime_nfr_paper_tables(
    output_dir: str | Path,
    *,
    round1_summary_path: str | Path,
    governance_summary_path: str | Path,
    round2_summary_path: str | Path,
    external_manifest_audit_path: str | Path,
    external_governance_summary_path: str | Path,
    feature_ablation_summary_path: str | Path,
    topology_gate_path: str | Path,
    validity_audit_path: str | Path,
    manual_audit_summary_path: str | Path | None = None,
) -> dict[str, Any]:
    source_values = {
        "round1": round1_summary_path,
        "governance": governance_summary_path,
        "round2": round2_summary_path,
        "external_audit": external_manifest_audit_path,
        "external_governance": external_governance_summary_path,
        "feature_ablation": feature_ablation_summary_path,
        "topology": topology_gate_path,
        "validity": validity_audit_path,
    }
    if manual_audit_summary_path is not None:
        source_values["manual_audit"] = manual_audit_summary_path
    loaded: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, Any]] = {}
    for name, path in source_values.items():
        loaded[name], sources[name] = _load(path)

    round1 = loaded["round1"]
    governance = loaded["governance"]
    round2 = loaded["round2"]
    external_audit = loaded["external_audit"]
    external_governance = loaded["external_governance"]
    ablation = loaded["feature_ablation"]
    topology = loaded["topology"]
    validity = loaded["validity"]
    manual_audit = loaded.get("manual_audit")

    table_rq1 = [
        {
            "governance_status": status,
            "cards": int(governance["status_counts"][status]),
            "proportion": float(governance["status_proportions"][status]),
        }
        for status in governance["status_counts"]
    ]
    table_round1 = [
        _dimension_row(name, values)
        for name, values in round1["dimensions"].items()
    ]
    table_round2 = [
        {
            **_dimension_row(name, values),
            "card_median_at_least_4": round2["overall"][
                "card_median_at_least_4"
            ][name],
        }
        for name, values in round2["overall"]["dimensions"].items()
    ]
    table_external = {
        "dataset": external_audit["dataset"],
        "decision": external_audit["decision"],
        "prediction_protocol_compatible": external_audit[
            "prediction_protocol_compatible"
        ],
        "cards": external_governance["cards"],
        "status_counts": external_governance["status_counts"],
        "history_window_shortened": external_governance[
            "history_window_shortened"
        ],
        "threshold_correctness_claim_allowed": external_governance[
            "threshold_correctness_claim_allowed"
        ],
    }
    table_prediction = [
        {"model": model, **metrics}
        for model, metrics in ablation["summary_by_model"].items()
    ]
    table_manual_audit = None
    if manual_audit is not None:
        table_manual_audit = {
            "reviewers": manual_audit["completeness"]["reviewers"],
            "controls_per_reviewer": manual_audit["completeness"][
                "controls_per_reviewer"
            ],
            "pairs_per_reviewer": manual_audit["completeness"][
                "pairs_per_reviewer"
            ],
            "control_ratings": manual_audit["completeness"]["control_ratings"],
            "pair_ratings": manual_audit["completeness"]["pair_ratings"],
            "control_conclusions": manual_audit["control_conclusions"],
            "pair_conclusions": manual_audit["pair_conclusions"],
            "unanimity": manual_audit["unanimity"],
            "chance_corrected_agreement": manual_audit["unanimity"][
                "chance_corrected_agreement"
            ],
            "claim_boundary": manual_audit["claim_boundary"],
        }
    app_prior_gate = bool(
        ablation["primary_increment_gate"].get("app_logistic", False)
    )
    if external_audit["decision"] == "reject":
        route = "C"
    elif app_prior_gate:
        route = "A"
    else:
        route = "B"
    claims = {
        "round1_core_cards_supportive": round1["core_supportive_cards"],
        "round1_threshold_plausibility_median": round1["dimensions"][
            "threshold_plausibility"
        ]["median"],
        "round2_primary_cards": round2["overall"][
            "governance_cards_median_at_least_4"
        ],
        "round2_primary_rate": round2["primary_endpoint"]["observed_rate"],
        "round2_threshold_cards_median_at_least_4": round2["overall"][
            "card_median_at_least_4"
        ]["threshold_plausibility"],
        "external_decision": external_audit["decision"],
        "topology_gate_pass": topology["graph_development_gate"]["pass"],
        "construct_validity_gate_pass": validity["construct_validity_gate"]["pass"],
        "selected_route": route,
        "manual_audit_complete": manual_audit is not None,
    }
    result = {
        "protocol": PAPER_TABLE_PROTOCOL,
        "sources": sources,
        "tables": {
            "rq1_governance_distribution": table_rq1,
            "round1_expert_review": table_round1,
            "round2_expert_review": table_round2,
            "external_validation": table_external,
            "prediction_ablation": table_prediction,
            "manual_audit": table_manual_audit,
        },
        "claims": claims,
        "manual_numeric_transcription_allowed": False,
    }
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "paper_tables.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# Runtime NFR 论文冻结表格",
        "",
        "_本文件由冻结 JSON 自动生成，不允许手工改写数值。_",
        "",
        "## RQ1/RQ2：治理状态分布",
        "",
        "| 状态 | 卡片数 | 比例 |",
        "| --- | ---: | ---: |",
    ]
    for row in table_rq1:
        lines.append(
            f"| `{row['governance_status']}` | {row['cards']} | {row['proportion']:.1%} |"
        )
    lines.extend(
        [
            "",
            "## RQ3：二次独立专家评审",
            "",
            "| 维度 | 中位数 | IQR | 1–2 分比例 | 卡片中位数≥4 |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in table_round2:
        lines.append(
            f"| `{row['dimension']}` | {row['median']:.1f} | {row['iqr']:.1f} | "
            f"{row['low_score_rate_1_or_2']:.1%} | {row['card_median_at_least_4']}/48 |"
        )
    if table_manual_audit is not None:
        lines.extend(
            [
                "",
                "## RQ4：匹配对照人工核查",
                "",
                "| 核查人 | 每人逐对照 | 每人组级 | 逐对照判断 | 组级判断 |",
                "| ---: | ---: | ---: | ---: | ---: |",
                (
                    f"| {table_manual_audit['reviewers']} | "
                    f"{table_manual_audit['controls_per_reviewer']} | "
                    f"{table_manual_audit['pairs_per_reviewer']} | "
                    f"{table_manual_audit['control_ratings']} | "
                    f"{table_manual_audit['pair_ratings']} |"
                ),
                "",
                (
                    "- 结论：逐对照通过 "
                    f"{table_manual_audit['control_conclusions'].get('通过', 0)}/"
                    f"{table_manual_audit['control_ratings']}；组级通过 "
                    f"{table_manual_audit['pair_conclusions'].get('通过', 0)}/"
                    f"{table_manual_audit['pair_ratings']}。"
                ),
                "- 结论无变异，仅报告原始一致率，不计算机会校正一致性系数。",
                "- 主张边界：人工核查支持匹配实现与证据重建一致性，不构成因果效应或阈值正确性验证。",
            ]
        )
    lines.extend(
        [
            "",
            "## RQ4：预测消融",
            "",
            "| 模型 | PR-AUC | Recall@3 | NDCG@3 | Brier | ECE |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in table_prediction:
        lines.append(
            f"| `{row['model']}` | {row['pr_auc']:.4f} | {row['recall_at_3']:.4f} | "
            f"{row['ndcg_at_3']:.4f} | {row['brier']:.4f} | {row['ece']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## 自动路线判定",
            "",
            f"- 外部数据决策：`{external_audit['decision']}`",
            f"- App-only Logistic 相对先验的 PR-AUC 增量门槛：`{app_prior_gate}`",
            f"- 当前路线：**{route}**",
            "",
            "该路线由冻结规则自动判定；外部数据、治理规则或模型不得据此回调。",
        ]
    )
    markdown_path = output / "PAPER_TABLES.md"
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        **result,
        "json": str(json_path.resolve()),
        "markdown": str(markdown_path.resolve()),
    }
