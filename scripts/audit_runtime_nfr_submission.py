"""Audit the Runtime NFR submission candidate without changing frozen results."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

REFERENCE_RE = re.compile(r"^\[(\d+)\]\s", re.MULTILINE)
CITATION_RE = re.compile(r"\[(\d+(?:[—–-]\d+)?(?:,\d+(?:[—–-]\d+)?)*)\]")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expand_citation_group(group: str) -> set[int]:
    numbers: set[int] = set()
    for part in group.split(","):
        match = re.fullmatch(r"(\d+)[—–-](\d+)", part)
        if match:
            start, end = map(int, match.groups())
            numbers.update(range(start, end + 1))
        else:
            numbers.add(int(part))
    return numbers


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def audit(
    manuscript: Path,
    closure_report: Path,
    phase2_dir: Path,
    figure_manifest: Path,
    candidate_source: Path = Path("src/data/runtime_nfr_dataset.py"),
    governance_source: Path = Path("src/data/runtime_nfr_v3_academic.py"),
) -> dict[str, Any]:
    text = manuscript.read_text(encoding="utf-8-sig")
    candidate_code = candidate_source.read_text(encoding="utf-8")
    governance_code = governance_source.read_text(encoding="utf-8")
    reference_heading = text.find("## 参考文献")
    if reference_heading < 0:
        raise AssertionError("reference heading not found")
    body = text[:reference_heading]
    references = text[reference_heading:]

    reference_numbers = [int(value) for value in REFERENCE_RE.findall(references)]
    cited_numbers: set[int] = set()
    for group in CITATION_RE.findall(body):
        cited_numbers.update(expand_citation_group(group))

    threshold = read_json(phase2_dir / "threshold_only_comparison.json")
    prediction = read_json(phase2_dir / "prediction_stability_and_calibration.json")
    topology = read_json(phase2_dir / "topology_negative_result_scope.json")
    analysis_manifest = read_json(phase2_dir / "ANALYSIS_MANIFEST.json")
    figures = read_json(figure_manifest)
    closure = closure_report.read_text(encoding="utf-8")

    status_counts = threshold["governance_counts"]
    expected_status_counts = {
        "insufficient_evidence": 1054,
        "threshold_unresolved": 2424,
        "needs_context_review": 948,
        "candidate_for_stakeholder_review": 4242,
    }

    hard_forbidden_patterns = {
        "absolute_first_claim": r"本文(?:首次|率先)(?:提出|实现|发现)",
        "automatic_correct_slo_claim": r"本文.{0,12}自动(?:发现|生成)(?:了)?正确 SLO",
        "automatic_formal_slo_claim": r"本文.{0,12}自动生成(?:了)?正式 SLO",
        "governance_outperforms_claim": r"证据感知治理(?:显著)?优于",
        "external_threshold_accuracy_claim": (
            r"外部(?:数据|验证).{0,20}(?:表明|证明|支持).{0,12}阈值(?:准确|正确)"
        ),
        "universal_topology_failure_claim": r"结果(?:证明|表明).{0,20}(?:拓扑|GNN).{0,10}无效",
    }
    forbidden_hits = {
        name: matches
        for name, pattern in hard_forbidden_patterns.items()
        if (matches := re.findall(pattern, text))
    }

    checks = {
        "references_are_1_through_69": reference_numbers == list(range(1, 70)),
        "all_references_are_cited": set(reference_numbers) == cited_numbers,
        "no_undefined_citations": cited_numbers.issubset(set(reference_numbers)),
        "status_counts_match_frozen_values": status_counts == expected_status_counts,
        "status_count_total_is_8668": sum(status_counts.values()) == 8668,
        "threshold_only_hidden_is_4426": (
            threshold["evidence_actions_hidden_by_minimal_baseline"]["count"] == 4426
        ),
        "threshold_only_hidden_share_is_0_511": abs(
            threshold["evidence_actions_hidden_by_minimal_baseline"]["rate"] - (4426 / 8668)
        )
        < 1e-12,
        "prediction_population_is_frozen": prediction["population"]
        == {
            "event_app_pairs_per_model": 2226,
            "events": 209,
            "folds": 5,
            "models": 6,
            "rows": 13356,
        },
        "topology_population_is_frozen": topology["events"] == 394
        and topology["unique_topology_hashes"] == 22,
        "topology_mapping_boundary_is_present": "86.0%" in body and "67.6%" in body,
        "prediction_construct_proximity_is_present": "14 个故障前 `exceedance` 特征" in body,
        "candidate_formula_matches_frozen_code": all(
            token in candidate_code
            for token in (
                "quantile: float = 0.95",
                "robust_scale = max(1.4826 * mad, 1e-6)",
                "threshold = max(quantile_value, median + 3.0 * robust_scale)",
            )
        )
        and all(
            token in body
            for token in (
                r"s=\max(1.4826\times \mathrm{MAD},10^{-6})",
                r"Q_{0.95}(X)",
                r"b=\max\left(Q_{0.95}(X),m+3s\right)",
            )
        ),
        "governance_gates_match_frozen_code": all(
            token in governance_code
            for token in (
                "coverage < 0.5",
                "valid_samples < 720",
                "tail_ratio = (q95 - median) / max(robust_scale, ROBUST_SCALE_FLOOR)",
                "tail_ratio >= 10.0",
            )
        )
        and all(
            token in body
            for token in (
                r"C_h\geq 0.5",
                r"n_v\geq 720",
                r"R_t = \frac{Q_{0.95}(L)-m}{s}",
                r"R_t=10.0",
            )
        ),
        "stale_tail_formula_is_absent": (r"Q_{0.99}(L)" not in body and r"Q_{0.50}(L)" not in body),
        "analysis_inputs_unchanged": analysis_manifest["source_files_unchanged"] is True,
        "figure_protocol_is_v3": figures["protocol"] == "runtime-nfr-jos-figures/3",
        "figure_layout_is_175mm": figures["layout_contract"]["intended_width_mm"] == 175,
        "figure_minimum_font_is_7pt": figures["layout_contract"]["minimum_body_font_pt"] >= 7,
        "closure_report_has_w01_to_w11": all(
            f"| W{index:02d} |" in closure for index in range(1, 12)
        ),
        "closure_report_has_no_failed_status": "| 未通过 |" not in closure,
        "primary_dataset_is_public_and_versioned": (
            "AIOps Challenge 2025 Dataset" in body
            and "57c36fa46fb2f4dec19b5f5ca9cbf5a90f9c9e00" in body
            and "CC BY-NC 4.0" in body
        ),
        "independent_protocol_dataset_is_public_and_versioned": (
            "独立协议审计数据集" in body
            and "14590730" in body
            and "CC BY 4.0" in body
        ),
        "restricted_internal_data_conflict_is_absent": not any(
            phrase in body
            for phrase in (
                "内部数据来自有限的云原生环境",
                "不含受限原始数据",
                "可能受第三方许可或隐私限制的原始遥测不直接公开",
            )
        ),
        "author_order_and_affiliation_are_present": all(
            phrase in body
            for phrase in (
                "**作者：** 谢华澄，张宇，吕嘉琪，杜庆峰*",
                "**单位：** 同济大学计算机科学与技术学院",
                "**英文署名：** Xie Hua Cheng, Zhang Yu, Lv Jia Qi, Du Qing Feng*",
                (
                    "**英文单位：** School of Computer Science and Technology, "
                    "Tongji University, Shanghai 201804, China"
                ),
                "**通信作者：** 杜庆峰（Du Qing Feng，Du_cloud@tongji.edu.cn）",
            )
        ),
        "funding_and_conflict_disclosures_are_present": (
            "**基金项目：** 无。" in body
            and "全体作者声明不存在与本研究相关的利益冲突" in body
        ),
        "code_publication_commitment_is_present": (
            "不受保密或商业公开限制" in body
            and "已保存在仅作者可访问的私有代码仓库" in body
            and "论文接收后公开代码与可复现派生制品" in body
        ),
        "no_hard_forbidden_claims": not forbidden_hits,
    }

    return {
        "protocol": "runtime-nfr-submission-audit/8",
        "pass": all(checks.values()),
        "checks": checks,
        "reference_audit": {
            "reference_count": len(reference_numbers),
            "cited_count": len(cited_numbers),
            "uncited": sorted(set(reference_numbers) - cited_numbers),
            "undefined": sorted(cited_numbers - set(reference_numbers)),
        },
        "forbidden_claim_hits": forbidden_hits,
        "frozen_values": {
            "governance_status_counts": status_counts,
            "prediction_population": prediction["population"],
            "topology_events": topology["events"],
            "topology_hashes": topology["unique_topology_hashes"],
        },
        "files": {
            "manuscript": {"path": str(manuscript.resolve()), "sha256": sha256(manuscript)},
            "closure_report": {
                "path": str(closure_report.resolve()),
                "sha256": sha256(closure_report),
            },
            "analysis_manifest": {
                "path": str((phase2_dir / "ANALYSIS_MANIFEST.json").resolve()),
                "sha256": sha256(phase2_dir / "ANALYSIS_MANIFEST.json"),
            },
            "figure_manifest": {
                "path": str(figure_manifest.resolve()),
                "sha256": sha256(figure_manifest),
            },
            "candidate_source": {
                "path": str(candidate_source.resolve()),
                "sha256": sha256(candidate_source),
            },
            "governance_source": {
                "path": str(governance_source.resolve()),
                "sha256": sha256(governance_source),
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manuscript",
        type=Path,
        default=Path("docs/manuscript/runtime_nfr/RUNTIME_NFR_MANUSCRIPT.md"),
    )
    parser.add_argument(
        "--closure-report",
        type=Path,
        default=Path("docs/manuscript/runtime_nfr/phase2_revision/PHASE2_CLOSURE_REPORT.md"),
    )
    parser.add_argument(
        "--phase2-dir",
        type=Path,
        default=Path("checkpoints/runtime_nfr_v3_academic/phase2_analysis_v1"),
    )
    parser.add_argument(
        "--figure-manifest",
        type=Path,
        default=Path(
            "checkpoints/runtime_nfr_v3_academic/" "phase3_figures_jos_v3/FIGURE_MANIFEST.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "checkpoints/runtime_nfr_v3_academic/" "submission_candidate_v10/submission_audit.json"
        ),
    )
    args = parser.parse_args()
    result = audit(
        args.manuscript,
        args.closure_report,
        args.phase2_dir,
        args.figure_manifest,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["pass"]:
        raise RuntimeError("Runtime NFR submission audit failed")


if __name__ == "__main__":
    main()
