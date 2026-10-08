"""Academic governance and independent expert-review workflow for runtime NFR cards.

The v3 layer wraps frozen v2 candidate thresholds.  It never changes the
threshold, never approves a business SLO, and keeps sampling/analysis metadata
separate from the blinded expert packet.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import csv
import json
import math
import re
import shutil
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from slo_evidence import Policy

from .runtime_nfr_v2 import EXPERT_DIMENSIONS, krippendorff_alpha_ordinal


ACADEMIC_CARD_SCHEMA = "runtime-nfr-boundary-card/v2-academic"
ACADEMIC_PROTOCOL = "runtime_nfr_v3_academic"
ROUND2_REVIEWER_IDS = ("rev_r2_01", "rev_r2_02", "rev_r2_03")
ROUND2_DIMENSIONS = (
    "threshold_plausibility",
    "runtime_actionability",
    "evidence_flag_correctness",
    "governance_appropriateness",
    "overall_revision_value",
)
GOVERNANCE_STATUSES = (
    "insufficient_evidence",
    "threshold_unresolved",
    "needs_context_review",
    "candidate_for_stakeholder_review",
)
FIRST_ROUND_REVIEWER_IDS = ("rev_sre_01", "rev_sre_02", "rev_sre_03")
ROBUST_SCALE_FLOOR = 1e-6
PROVENANCE_REQUIRED_FIELDS = (
    "protocol",
    "dataset_hash",
    "event_id",
    "record_id",
    "query_version",
    "evidence_cutoff",
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quantiles(values: Sequence[float]) -> dict[str, float | None]:
    clean = np.asarray([float(value) for value in values if pd.notna(value)], dtype=float)
    if clean.size == 0:
        return {"median": None, "q1": None, "q3": None, "iqr": None}
    q1, median, q3 = np.quantile(clean, [0.25, 0.5, 0.75])
    return {
        "median": float(median),
        "q1": float(q1),
        "q3": float(q3),
        "iqr": float(q3 - q1),
    }


def _read_csv_with_possible_preamble(path: Path) -> pd.DataFrame:
    """Read a rating CSV even if explanatory lines were inserted above its header."""

    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.lstrip("\ufeff").startswith("reviewer_id,")
        ),
        None,
    )
    if header_index is None:
        raise ValueError(f"rating header not found in {path}")
    reader = csv.DictReader(lines[header_index:])
    return pd.DataFrame(list(reader), columns=reader.fieldnames)


def _reviewer_id_from_declaration(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    candidates = re.findall(r"rev_[A-Za-z0-9_]+", text)
    if not candidates:
        raise ValueError(f"reviewer ID not found in declaration: {path}")
    unique = list(dict.fromkeys(candidates))
    if len(unique) != 1:
        raise ValueError(f"ambiguous reviewer IDs in declaration {path}: {unique}")
    return unique[0]


def _validate_first_round(
    frame: pd.DataFrame,
    *,
    expected_reviewers: Sequence[str] = FIRST_ROUND_REVIEWER_IDS,
    expected_cards_per_reviewer: int = 48,
) -> dict[str, Any]:
    required = {"reviewer_id", "card_id", *EXPERT_DIMENSIONS}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"first-round ratings missing columns: {missing}")
    if frame.duplicated(["reviewer_id", "card_id"]).any():
        rows = frame.loc[frame.duplicated(["reviewer_id", "card_id"], keep=False)]
        raise ValueError(f"duplicate reviewer/card rows: {rows[['reviewer_id', 'card_id']].to_dict('records')}")
    actual_reviewers = tuple(sorted(frame["reviewer_id"].astype(str).unique()))
    if actual_reviewers != tuple(sorted(expected_reviewers)):
        raise ValueError(f"unexpected first-round reviewer IDs: {actual_reviewers}")
    normalized = frame.copy()
    for dimension in EXPERT_DIMENSIONS:
        normalized[dimension] = pd.to_numeric(normalized[dimension], errors="coerce")
        if normalized[dimension].isna().any():
            raise ValueError(f"{dimension} must be complete")
        if not normalized[dimension].between(1, 5).all():
            raise ValueError(f"{dimension} ratings must be in [1, 5]")
    card_sets: dict[str, set[str]] = {}
    for reviewer_id, group in normalized.groupby("reviewer_id"):
        if len(group) != expected_cards_per_reviewer:
            raise ValueError(
                f"{reviewer_id} has {len(group)} cards, expected {expected_cards_per_reviewer}"
            )
        card_sets[str(reviewer_id)] = set(group["card_id"].astype(str))
    reference = card_sets[expected_reviewers[0]]
    if any(cards != reference for cards in card_sets.values()):
        raise ValueError("first-round reviewers did not score the same card set")
    return {
        "rows": int(len(normalized)),
        "reviewers": len(card_sets),
        "cards": len(reference),
        "reviewer_ids": list(expected_reviewers),
        "cards_per_reviewer": {
            reviewer_id: len(cards) for reviewer_id, cards in card_sets.items()
        },
        "complete_scores": True,
        "no_duplicate_reviewer_card_rows": True,
        "identical_card_sets": True,
    }


def _dimension_summary(frame: pd.DataFrame, dimensions: Sequence[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for dimension in dimensions:
        values = pd.to_numeric(frame[dimension], errors="coerce")
        grouped = [
            group[dimension].dropna().astype(float).tolist()
            for _, group in frame.groupby(frame.columns[1], sort=True)
        ]
        result[dimension] = {
            **_quantiles(values.tolist()),
            "low_score_count_1_or_2": int((values <= 2).sum()),
            "low_score_rate_1_or_2": float((values <= 2).mean()),
            "krippendorff_alpha_ordinal": krippendorff_alpha_ordinal(grouped),
        }
    return result


def _comment_themes(frame: pd.DataFrame) -> list[dict[str, Any]]:
    themes = {
        "history_or_sample_evidence": ("样本", "历史", "覆盖", "数据不足", "evidence"),
        "tail_or_workload_context": ("长尾", "差距过大", "分布", "负载", "工作负载"),
        "zero_or_resolution_limit": ("为0", "为 0", "零错误", "分辨率", "下限"),
        "threshold_too_high": ("过高", "偏高", "宽松", "掩盖"),
        "threshold_too_low": ("过低", "偏低", "严格", "误报"),
        "reference_or_stakeholder_needed": ("参考", "业务", "利益相关者", "slo", "目标"),
    }
    output = []
    for name, keywords in themes.items():
        matched = [
            {
                "reviewer_id": str(row["reviewer_id"]),
                "card_id": str(row["card_id"]),
                "comment": str(row.get("comments", "")),
            }
            for _, row in frame.iterrows()
            if any(keyword.lower() in str(row.get("comments", "")).lower() for keyword in keywords)
        ]
        output.append({"theme": name, "count": len(matched), "examples": matched[:5]})
    return output


def finalize_first_round_expert_review(
    replies_dir: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Freeze, normalize, validate, and summarize the completed first review."""

    source_root = Path(replies_dir)
    output = Path(output_dir)
    raw_output = output / "frozen_raw"
    frames: list[pd.DataFrame] = []
    file_manifest: list[dict[str, Any]] = []
    declarations: list[dict[str, Any]] = []
    for label in ("A", "B", "C"):
        reviewer_dir = source_root / label
        rating_path = reviewer_dir / "expert_ratings_template.csv"
        declaration_path = reviewer_dir / "评审者声明信息.md"
        if not rating_path.exists() or not declaration_path.exists():
            raise FileNotFoundError(f"missing first-round files for reviewer folder {label}")
        declaration_id = _reviewer_id_from_declaration(declaration_path)
        declarations.append(
            {
                "source_folder": label,
                "reviewer_id": declaration_id,
                "declaration_path": str(declaration_path.resolve()),
            }
        )
        frame = _read_csv_with_possible_preamble(rating_path)
        source_ids = sorted(frame["reviewer_id"].dropna().astype(str).unique().tolist())
        frame["source_csv_reviewer_id"] = frame["reviewer_id"]
        frame["reviewer_id"] = declaration_id
        frames.append(frame)
        for source_path in (rating_path, declaration_path):
            destination = raw_output / label / source_path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            file_manifest.append(
                {
                    "source_folder": label,
                    "file_name": source_path.name,
                    "source_path": str(source_path.resolve()),
                    "source_sha256": _sha256_file(source_path),
                    "frozen_copy_path": str(destination.resolve()),
                    "frozen_copy_sha256": _sha256_file(destination),
                    "source_csv_reviewer_ids": source_ids
                    if source_path == rating_path
                    else None,
                    "authoritative_reviewer_id": declaration_id,
                }
            )
    if [item["reviewer_id"] for item in declarations] != list(FIRST_ROUND_REVIEWER_IDS):
        raise ValueError(f"declaration IDs do not match protocol: {declarations}")
    ratings = pd.concat(frames, ignore_index=True)
    validation = _validate_first_round(ratings)
    for dimension in EXPERT_DIMENSIONS:
        ratings[dimension] = pd.to_numeric(ratings[dimension], errors="raise")
    ratings = ratings.sort_values(["reviewer_id", "card_id"]).reset_index(drop=True)
    ratings_path = output / "ratings.csv"
    ratings_path.parent.mkdir(parents=True, exist_ok=True)
    ratings.to_csv(ratings_path, index=False, encoding="utf-8-sig")

    per_card = ratings.groupby("card_id", sort=True)[list(EXPERT_DIMENSIONS)].median()
    per_card["threshold_plausibility_low_score_rate"] = (
        ratings.assign(
            threshold_low=pd.to_numeric(ratings["threshold_plausibility"], errors="coerce") <= 2
        )
        .groupby("card_id")["threshold_low"]
        .mean()
    )
    per_card["runtime_actionability_low_score_rate"] = (
        ratings.assign(
            runtime_low=pd.to_numeric(ratings["runtime_actionability"], errors="coerce") <= 2
        )
        .groupby("card_id")["runtime_low"]
        .mean()
    )
    per_card = per_card.reset_index()
    per_card_path = output / "per_card_medians.csv"
    per_card.to_csv(per_card_path, index=False, encoding="utf-8-sig")

    disagreements: list[dict[str, Any]] = []
    for (card_id, dimension), values in ratings.melt(
        id_vars=["reviewer_id", "card_id"],
        value_vars=list(EXPERT_DIMENSIONS),
        var_name="dimension",
        value_name="score",
    ).groupby(["card_id", "dimension"])["score"]:
        numeric = pd.to_numeric(values, errors="coerce")
        spread = float(numeric.max() - numeric.min())
        if spread >= 2:
            disagreements.append(
                {
                    "card_id": str(card_id),
                    "dimension": str(dimension),
                    "minimum": float(numeric.min()),
                    "maximum": float(numeric.max()),
                    "spread": spread,
                }
            )

    dimensions = _dimension_summary(ratings, EXPERT_DIMENSIONS)
    supportive = (per_card[["clarity", "measurability", "traceability"]] >= 4).all(axis=1)
    remediation = per_card.loc[
        per_card["threshold_plausibility"] <= 3, "card_id"
    ].astype(str).tolist()
    summary = {
        "protocol": ACADEMIC_PROTOCOL,
        "review_round": 1,
        "validation": validation,
        "dimensions": dimensions,
        "core_supportive_cards": int(supportive.sum()),
        "core_supportive_rate": float(supportive.mean()),
        "threshold_plausibility_card_median_distribution": {
            str(int(score)): int(count)
            for score, count in per_card["threshold_plausibility"].value_counts().sort_index().items()
        },
        "remediation_card_count_median_at_most_3": len(remediation),
        "remediation_parent_card_ids": remediation,
        "ceiling_effect_note": (
            "Clarity, measurability, and traceability are concentrated at the maximum score; "
            "negative alpha for these dimensions is not interpreted as substantive opposition."
        ),
        "claim_limit": (
            "The review supports candidate specification expression quality, not formal business "
            "SLO approval."
        ),
        "disagreement_cells_spread_at_least_2": disagreements,
        "exploratory_comment_themes": _comment_themes(ratings),
    }
    _write_json(output / "summary.json", summary)
    _write_json(output / "validation.json", validation)
    _write_json(output / "source_hashes.json", file_manifest)
    _write_json(output / "reviewer_declarations_index.json", declarations)
    _write_json(output / "disagreements.json", disagreements)
    _write_json(output / "comment_themes_exploratory.json", summary["exploratory_comment_themes"])
    report = _first_round_report(summary)
    (output / "FIRST_ROUND_FROZEN_REPORT.md").write_text(report, encoding="utf-8")
    return {
        "ratings": str(ratings_path.resolve()),
        "summary": str((output / "summary.json").resolve()),
        "report": str((output / "FIRST_ROUND_FROZEN_REPORT.md").resolve()),
        "rows": len(ratings),
        "remediation_cards": len(remediation),
    }


def _first_round_report(summary: Mapping[str, Any]) -> str:
    threshold = summary["dimensions"]["threshold_plausibility"]
    return f"""# Runtime NFR 第一轮专家评审冻结报告

_本报告记录冻结后的第一轮结果；匿名 ID 以专家声明文件为准。_

---

## 📋 冻结结论

- 三位专家 ID：`rev_sre_01`、`rev_sre_02`、`rev_sre_03`
- 标准化评分：144 行，三位专家各 48 张卡，卡片集合一致
- 核心表达门槛：48/48 张卡通过清晰性、可测量性和可追溯性门槛
- 阈值合理性：Median={threshold["median"]:g}，IQR={threshold["q1"]:g}–{threshold["q3"]:g}，ordinal Krippendorff’s α={threshold["krippendorff_alpha_ordinal"]:.3f}
- 阈值合理性中位数不高于 3：{summary["remediation_card_count_median_at_most_3"]}/48 张

## ⚠️ 解释边界

清晰性、可测量性和可追溯性评分存在明显天花板效应，因此这些维度上的负 α
不解释为专家反对。评审只支持候选规约的表达质量，不构成正式业务 SLO 批准。

## 🔍 后续用途

阈值合理性中位数不高于 3 的卡片进入第二轮“修复队列”。原始阈值和第一轮评分
均保持不变；第二轮只检验新增治理信息能否帮助拒绝不适合直接操作化的候选阈值。
"""


def _academic_card_id(parent_card_id: str) -> str:
    value = f"{ACADEMIC_CARD_SCHEMA}|{parent_card_id}"
    return f"acad-{sha256(value.encode('utf-8')).hexdigest()[:16]}"


def _round2_card_id(parent_card_id: str) -> str:
    value = f"round2|20260723|{parent_card_id}"
    return f"r2-{sha256(value.encode('utf-8')).hexdigest()[:16]}"


def _coverage_bucket(coverage: float) -> str:
    if coverage < 0.5:
        return "lt_0_5"
    if coverage < 0.75:
        return "0_5_to_lt_0_75"
    return "ge_0_75"


@dataclass(frozen=True)
class GovernanceDecision:
    evidence_status: str
    governance_flags: tuple[str, ...]
    recommended_next_action: str
    latency_tail_ratio: float | None


def validate_academic_card_provenance(card: Mapping[str, Any]) -> None:
    """Enforce provenance completeness before a card enters governance routing.

    The current event-indexed experiment uses ``event_id`` as the reproducible
    audit-anchor identifier.  Missing or malformed provenance is a construction
    failure, not a fifth governance state and not a card that may be silently
    dropped from the frozen population.
    """

    provenance = card.get("evidence_provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("evidence_provenance must be a mapping")
    missing = [
        field
        for field in PROVENANCE_REQUIRED_FIELDS
        if not str(provenance.get(field, "")).strip()
    ]
    if missing:
        raise ValueError(f"incomplete evidence provenance: {', '.join(missing)}")
    dataset_hash = str(provenance["dataset_hash"]).strip().lower()
    if SHA256_PATTERN.fullmatch(dataset_hash) is None:
        raise ValueError("evidence_provenance.dataset_hash must be a 64-character SHA-256")


def academic_governance_decision(card: Mapping[str, Any]) -> GovernanceDecision:
    """Apply deterministic flags and precedence without modifying the raw threshold."""

    validate_academic_card_provenance(card)
    sli = str(card["sli"])
    coverage = float(card.get("quality", {}).get("history_coverage", 0.0))
    valid_samples = int(card["valid_samples"])
    q95 = float(card["quantile_value"])
    median = float(card["median"])
    mad = float(card["mad"])
    robust_scale = float(card["robust_scale"])
    raw_threshold = float(card["threshold"])
    flags: list[str] = []
    if coverage < 0.5:
        flags.append("history_coverage_below_0_5")
    if valid_samples < Policy().minimum_samples:
        flags.append("valid_samples_below_720")

    if sli == "error_ratio":
        # Never infer all-zero history from Q95/median/MAD alone. Old cards
        # without the raw fact remain conservatively unresolved, with a distinct flag.
        actual_nonzero = card.get("nonzero_count")
        known_zero = (valid_samples > 0 and actual_nonzero == 0)
        if known_zero:
            flags.append("zero_error_baseline")
        elif actual_nonzero is None and math.isclose(q95, 0.0) and math.isclose(median, 0.0) and math.isclose(mad, 0.0):
            flags.append("zero_summary_unverified")
        floor_threshold = median + 3.0 * ROBUST_SCALE_FLOOR
        if (
            math.isclose(mad, 0.0)
            and q95 <= floor_threshold + 1e-15
            and math.isclose(raw_threshold, max(q95, floor_threshold), rel_tol=0.0, abs_tol=1e-15)
        ):
            flags.append("threshold_from_robust_scale_floor")

    tail_ratio: float | None = None
    if sli == "latency":
        tail_ratio = (q95 - median) / max(robust_scale, ROBUST_SCALE_FLOOR)
        if tail_ratio >= 10.0:
            flags.append("latency_tail_ratio_at_least_10")

    if "history_coverage_below_0_5" in flags or "valid_samples_below_720" in flags:
        status = "insufficient_evidence"
        action = "collect_more_history_before_operationalization"
    elif (
        "zero_error_baseline" in flags
        or "zero_summary_unverified" in flags
        or "threshold_from_robust_scale_floor" in flags
    ):
        status = "threshold_unresolved"
        action = "obtain_reference_target_measurement_resolution_or_stakeholder_input"
    elif "latency_tail_ratio_at_least_10" in flags:
        status = "needs_context_review"
        action = "review_workload_mix_and_tail_distribution"
    else:
        status = "candidate_for_stakeholder_review"
        action = "continue_to_stakeholder_review_without_slo_approval"
    return GovernanceDecision(status, tuple(flags), action, tail_ratio)


def build_academic_governance_cards(
    v2_cards: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    academic_cards: list[dict[str, Any]] = []
    for card in v2_cards:
        decision = academic_governance_decision(card)
        raw_threshold = float(card["threshold"])
        academic = {
            **dict(card),
            "card_id": _academic_card_id(str(card["card_id"])),
            "schema": ACADEMIC_CARD_SCHEMA,
            "numeric_rule_revision": "v1-corrected-20260929",
            "parent_card_id": str(card["card_id"]),
            "raw_threshold": raw_threshold,
            "measurement_resolution": {
                "observed_source_resolution": "not_independently_established",
                "robust_scale_floor": ROBUST_SCALE_FLOOR,
                "threshold_was_rounded_or_clipped": False,
            },
            "evidence_status": decision.evidence_status,
            "governance_flags": list(decision.governance_flags),
            "recommended_next_action": decision.recommended_next_action,
            "review_round": 2,
            "valid_observation_rule": (
                str(card.get("zero_traffic_rule", ""))
                + "; use only finite minute-level observations within the historical window"
            ),
            "reference_status": "no_external_reference_objective_supplied",
            "applicability_context": {
                "scope": "candidate per-service runtime NFR boundary",
                "business_slo_approved": False,
                "requires_stakeholder_confirmation": True,
                "history_coverage_bucket": _coverage_bucket(
                    float(card.get("quality", {}).get("history_coverage", 0.0))
                ),
                "latency_tail_ratio": decision.latency_tail_ratio,
            },
        }
        academic.pop("threshold", None)
        academic_cards.append(academic)
    return academic_cards


def _nested_counts(cards: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    return dict(Counter(str(card.get(key, "missing")) for card in cards))


def summarize_governance_cards(cards: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(card["evidence_status"]) for card in cards)
    total = len(cards)
    by_status: dict[str, Any] = {}
    for status in GOVERNANCE_STATUSES:
        group = [card for card in cards if card["evidence_status"] == status]
        by_status[status] = {
            "count": len(group),
            "proportion": len(group) / max(1, total),
            "sli": _nested_counts(group, "sli"),
            "service": _nested_counts(group, "subject_id"),
            "coverage_bucket": dict(
                Counter(
                    str(card["applicability_context"]["history_coverage_bucket"])
                    for card in group
                )
            ),
        }
    sensitivity: dict[str, Any] = {}
    for cutoff in (9.5, 10.0, 10.5):
        candidate_count = 0
        context_count = 0
        for card in cards:
            status = str(card["evidence_status"])
            if status in {"insufficient_evidence", "threshold_unresolved"}:
                continue
            ratio = card.get("applicability_context", {}).get(
                "latency_tail_ratio"
            )
            if ratio is not None and float(ratio) >= cutoff:
                context_count += 1
            else:
                candidate_count += 1
        sensitivity[str(cutoff)] = {
            "needs_context_review": context_count,
            "candidate_for_stakeholder_review": candidate_count,
            "cards_reclassified_relative_to_10": None,
        }
    reference_context = sensitivity["10.0"]["needs_context_review"]
    for values in sensitivity.values():
        values["cards_reclassified_relative_to_10"] = abs(
            values["needs_context_review"] - reference_context
        )
    return {
        "protocol": ACADEMIC_PROTOCOL,
        "schema": ACADEMIC_CARD_SCHEMA,
        "cards": total,
        "status_counts": {status: status_counts.get(status, 0) for status in GOVERNANCE_STATUSES},
        "status_proportions": {
            status: status_counts.get(status, 0) / max(1, total)
            for status in GOVERNANCE_STATUSES
        },
        "by_status": by_status,
        "sli": _nested_counts(cards, "sli"),
        "service": _nested_counts(cards, "subject_id"),
        "coverage_bucket": dict(
            Counter(
                str(card["applicability_context"]["history_coverage_bucket"])
                for card in cards
            )
        ),
        "exploratory_tail_ratio_sensitivity": {
            "cutoffs": sensitivity,
            "frozen_primary_cutoff": 10.0,
            "primary_rule_changed": False,
            "interpretation": (
                "Exploratory robustness only; no cutoff is selected from these results."
            ),
        },
        "raw_threshold_preserved": True,
        "business_slo_approval_claimed": False,
    }


def write_academic_governance_cards(
    v2_cards_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    source_path = Path(v2_cards_path)
    v2_cards = json.loads(source_path.read_text(encoding="utf-8"))
    source_hash_before = _sha256_file(source_path)
    cards = build_academic_governance_cards(v2_cards)
    for source, wrapped in zip(v2_cards, cards):
        if not math.isclose(
            float(source["threshold"]),
            float(wrapped["raw_threshold"]),
            rel_tol=0.0,
            abs_tol=0.0,
        ):
            raise AssertionError(f"raw threshold changed for {source['card_id']}")
    output = Path(output_dir)
    cards_path = output / "academic_governance_cards.json"
    _write_json(cards_path, cards)
    summary = summarize_governance_cards(cards)
    summary["source_v2_cards_path"] = str(source_path.resolve())
    summary["source_v2_cards_sha256_before"] = source_hash_before
    summary["source_v2_cards_sha256_after"] = _sha256_file(source_path)
    summary["source_v2_unchanged"] = (
        summary["source_v2_cards_sha256_before"] == summary["source_v2_cards_sha256_after"]
    )
    _write_json(output / "governance_summary.json", summary)
    return {
        "cards": str(cards_path.resolve()),
        "summary": str((output / "governance_summary.json").resolve()),
        **summary,
    }


def _card_private_strata(
    card: Mapping[str, Any],
    traffic_quartiles: Mapping[str, int] | None,
) -> dict[str, Any]:
    provenance = card.get("evidence_provenance", {})
    parent_card_id = str(card["parent_card_id"])
    return {
        "service": str(card["subject_id"]),
        "sli": str(card["sli"]),
        "target_type": provenance.get("target_type"),
        "traffic_quartile": None
        if traffic_quartiles is None
        else traffic_quartiles.get(parent_card_id),
        "coverage_bucket": str(card["applicability_context"]["history_coverage_bucket"]),
    }


def _diverse_sample(
    candidates: Sequence[Mapping[str, Any]],
    *,
    count: int,
    rng: np.random.Generator,
    traffic_quartiles: Mapping[str, int] | None,
) -> list[Mapping[str, Any]]:
    pool = list(candidates)
    rng.shuffle(pool)
    selected: list[Mapping[str, Any]] = []
    seen: dict[str, set[Any]] = defaultdict(set)
    while pool and len(selected) < count:
        scored = []
        for index, card in enumerate(pool):
            strata = _card_private_strata(card, traffic_quartiles)
            novelty = sum(
                1 for key, value in strata.items() if value is not None and value not in seen[key]
            )
            scored.append((novelty, -index, index, strata))
        _, _, winner_index, strata = max(scored)
        winner = pool.pop(winner_index)
        selected.append(winner)
        for key, value in strata.items():
            if value is not None:
                seen[key].add(value)
    if len(selected) != count:
        raise ValueError(f"only {len(selected)} eligible cards available; {count} required")
    return selected


def select_round2_cards(
    academic_cards: Sequence[Mapping[str, Any]],
    first_round_per_card: pd.DataFrame,
    *,
    seed: int = 20260723,
    per_status: int = 6,
    traffic_quartiles: Mapping[str, int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select 24 remediation and 24 held-out generalization cards."""

    required = {"card_id", "threshold_plausibility"}
    missing = required - set(first_round_per_card.columns)
    if missing:
        raise ValueError(f"first-round per-card file missing columns: {sorted(missing)}")
    first_round_ids = set(first_round_per_card["card_id"].astype(str))
    remediation_ids = set(
        first_round_per_card.loc[
            pd.to_numeric(first_round_per_card["threshold_plausibility"], errors="coerce") <= 3,
            "card_id",
        ].astype(str)
    )
    if len(first_round_ids) != 48:
        raise ValueError(f"expected 48 first-round cards, found {len(first_round_ids)}")
    if len(remediation_ids) != 24:
        raise ValueError(f"expected 24 remediation cards, found {len(remediation_ids)}")
    by_parent = {str(card["parent_card_id"]): card for card in academic_cards}
    missing_remediation = sorted(remediation_ids - set(by_parent))
    if missing_remediation:
        raise ValueError(f"remediation cards missing from academic cards: {missing_remediation}")
    remediation = [by_parent[parent] for parent in sorted(remediation_ids)]
    rng = np.random.default_rng(seed)
    generalization: list[Mapping[str, Any]] = []
    for status in GOVERNANCE_STATUSES:
        candidates = [
            card
            for card in academic_cards
            if card["evidence_status"] == status
            and str(card["parent_card_id"]) not in first_round_ids
        ]
        generalization.extend(
            _diverse_sample(
                candidates,
                count=per_status,
                rng=rng,
                traffic_quartiles=traffic_quartiles,
            )
        )
    selected: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    for cohort, cards in (("remediation", remediation), ("generalization", generalization)):
        for card in cards:
            round2_id = _round2_card_id(str(card["parent_card_id"]))
            payload = dict(card)
            payload["round2_card_id"] = round2_id
            selected.append(payload)
            manifest.append(
                {
                    "round2_card_id": round2_id,
                    "academic_card_id": card["card_id"],
                    "parent_card_id": card["parent_card_id"],
                    "cohort": cohort,
                    "governance_status": card["evidence_status"],
                    **_card_private_strata(card, traffic_quartiles),
                }
            )
    if len(selected) != 48 or len({item["round2_card_id"] for item in selected}) != 48:
        raise AssertionError("round2 selection must contain 48 unique cards")
    if {
        item["parent_card_id"] for item in manifest if item["cohort"] == "generalization"
    } & first_round_ids:
        raise AssertionError("generalization cohort overlaps first-round cards")
    return selected, manifest


def _blinded_round2_card(card: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(card)
    payload.pop("parent_card_id", None)
    payload.pop("card_id", None)
    provenance = dict(payload.get("evidence_provenance", {}))
    for key in ("event_id", "record_id", "fault_family", "target_type", "dataset_hash"):
        provenance.pop(key, None)
    payload["evidence_provenance"] = provenance
    context = dict(payload.get("observation_context", {}))
    for key in ("segment_id", "topology_hash"):
        context.pop(key, None)
    payload["observation_context"] = context
    applicability = dict(payload.get("applicability_context", {}))
    applicability.pop("history_coverage_bucket", None)
    payload["applicability_context"] = applicability
    return payload


def _reviewer_seed(base_seed: int, reviewer_id: str) -> int:
    digest = sha256(f"{base_seed}|{reviewer_id}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16) % (2**32)


def _round2_protocol_markdown(seed: int) -> str:
    return f"""# Runtime NFR 独立二次专家评审协议修正案

_协议冻结日期：2026-07-23；固定抽样种子：`{seed}`。_

---

## 📋 研究目的

本轮检验新增学术治理层能否识别证据不足、阈值未解析和需要上下文审查的候选
阈值，并给出适当处置。它不检验正式业务 SLO，也不要求专家把原始阈值评高。

## 🔒 独立性与盲化

- 三位专家必须未参与阈值公式、卡片生成和第一轮评分
- 专家不得接触第一轮评分、评论、私有抽样清单或模型结果
- 三位专家看到相同的 48 张卡，但顺序由匿名 ID 确定性随机化
- 专家材料隐藏修复/泛化队列、第一轮卡片 ID、故障、未来越界和预测信息

## 🎯 预设终点

主要终点为至少 75% 卡片的 `governance_appropriateness` 中位数不低于 4。
次要终点包括五个维度的中位数、IQR、1–2 分比例和 ordinal Krippendorff’s α，
并分别报告修复队列、泛化队列和四类治理状态。

## ⚠️ 解释边界

不同专家和不同卡片之间的两轮均值不得解释为因果改进。修复队列的变化只作为
辅助描述，泛化队列作为主要独立验证依据。负结果、分歧和状态错误意见全部保留；
失败不触发第三轮阈值调参。
"""


def _round2_invitation_markdown(reviewer_ids: Sequence[str]) -> str:
    ids = "、".join(f"`{reviewer_id}`" for reviewer_id in reviewer_ids)
    return f"""# Runtime NFR 治理层独立专家评审邀请

_预计用时 45–60 分钟；请在收到的专属材料中独立完成评分。_

---

## 👤 专家资格

本轮邀请三位未看过第一轮材料的新专家。每位专家至少有 3 年相关经验；总体应
至少覆盖一位 SRE/运维/云原生可观测性专家和一位需求工程/软件质量/运行时验证
专家，第三位优先为云平台架构、可靠性或跨领域专家。预分配匿名 ID 为：{ids}。

不得参与阈值公式、卡片生成或第一轮评分；不得接触第一轮分数、评论、私有抽样
清单或模型结果。请如实填写利益冲突和既往材料接触情况。

## 📦 需要完成的文件

1. 阅读本人专属的 `round2_cards_<reviewer_id>.json`
2. 填写 `round2_ratings_<reviewer_id>.csv` 中全部 48 行
3. 填写 `reviewer_declaration_<reviewer_id>.md`
4. 原样返回 CSV 和声明文件，不改列名、卡片 ID 或行数

## 📏 五个评分维度

| 字段 | 要判断的问题 |
| --- | --- |
| `threshold_plausibility` | 原始阈值是否可作为候选起点 |
| `runtime_actionability` | 加入治理状态后是否能支持运行时处置 |
| `evidence_flag_correctness` | 证据不足或异常标志是否准确 |
| `governance_appropriateness` | 继续确认、拒绝操作化或要求上下文审查是否合理 |
| `overall_revision_value` | 相比只输出阈值，治理信息是否有实际增量 |

所有维度使用 1–5 分：1=明显不合适，2=较不合适，3=不确定/一般，4=较合适，
5=非常合适。评分对象是治理层；不要求把原始阈值评高。

## ✍️ 填表要求

- 每张卡的五个维度都必须填写 1、2、3、4 或 5
- `reviewer_id`、`round2_card_id`、列名和行数不得修改
- 评为 1–2 分，或认为治理状态错误时，必须在 `comments` 中说明原因
- 认为状态错误时，请以 `STATE_ERROR:` 开头，并写明建议状态或下一步处置
- 评论可使用中文或英文；不要写姓名、单位等身份信息
- 请独立完成，不与其他专家交换评分或讨论具体卡片
- 本研究承诺无论结果是否支持治理方法，都将完整报告

## ✅ 提交前检查

- CSV 恰好 48 行，五项评分完整
- 无重复或缺失卡片
- 声明文件写明角色、相关年限、利益冲突和既往材料接触情况
- 若曾接触第一轮材料，必须明确说明；该结果不进入独立验证主分析
"""


def _declaration_markdown(reviewer_id: str) -> str:
    return f"""# 二次评审专家声明

_请填写后与评分 CSV 一并返回。_

---

## 👤 匿名信息

- 匿名 `reviewer_id`：`{reviewer_id}`
- 角色类别：
- 相关工作年限：
- 是否确认具有至少 3 年相关经验：是 / 否
- 相关专业经验简述：

## 🔒 独立性声明

- 是否参与阈值公式或卡片生成：否 / 是（请说明）
- 是否参与第一轮评分：否 / 是（请说明）
- 是否看过第一轮分数、评论或私有抽样清单：否 / 是（请说明）
- 是否看过相关模型结果或未来越界信息：否 / 是（请说明）
- 是否与其他本轮专家交换评分：否 / 是（请说明）
- 需要披露的利益冲突：无 / 有（请说明）

## ✅ 完成确认

- 已独立完成全部 48 张卡：是 / 否
- 1–2 分或状态错误意见均已解释：是 / 否
- 填写日期：
"""


def write_round2_expert_package(
    output_dir: str | Path,
    *,
    selected_cards: Sequence[Mapping[str, Any]],
    private_manifest: Sequence[Mapping[str, Any]],
    reviewer_ids: Sequence[str] = ROUND2_REVIEWER_IDS,
    seed: int = 20260723,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if len(selected_cards) != 48:
        raise ValueError("round2 expert package requires exactly 48 selected cards")
    round2_ids = {str(card["round2_card_id"]) for card in selected_cards}
    if len(round2_ids) != 48:
        raise ValueError("round2 card IDs must be unique")
    (output / "ROUND2_PROTOCOL_AMENDMENT.md").write_text(
        _round2_protocol_markdown(seed), encoding="utf-8"
    )
    (output / "EXPERT_INVITATION_ROUND2.md").write_text(
        _round2_invitation_markdown(reviewer_ids), encoding="utf-8"
    )
    _write_json(output / "sampling_and_parent_mapping_private.json", list(private_manifest))
    packages: dict[str, Any] = {}
    ordered_ids: dict[str, list[str]] = {}
    for reviewer_id in reviewer_ids:
        rng = np.random.default_rng(_reviewer_seed(seed, reviewer_id))
        order = rng.permutation(len(selected_cards)).tolist()
        cards = [_blinded_round2_card(selected_cards[index]) for index in order]
        ids = [str(card["round2_card_id"]) for card in cards]
        ordered_ids[reviewer_id] = ids
        card_path = output / f"round2_cards_{reviewer_id}.json"
        rating_path = output / f"round2_ratings_{reviewer_id}.csv"
        declaration_path = output / f"reviewer_declaration_{reviewer_id}.md"
        _write_json(
            card_path,
            {
                "protocol": ACADEMIC_PROTOCOL,
                "review_round": 2,
                "reviewer_id": reviewer_id,
                "card_count": len(cards),
                "cards": cards,
            },
        )
        rows = [
            {
                "reviewer_id": reviewer_id,
                "round2_card_id": card["round2_card_id"],
                **{dimension: "" for dimension in ROUND2_DIMENSIONS},
                "comments": "",
            }
            for card in cards
        ]
        pd.DataFrame(rows).to_csv(rating_path, index=False, encoding="utf-8-sig")
        declaration_path.write_text(_declaration_markdown(reviewer_id), encoding="utf-8")
        packages[reviewer_id] = {
            "cards": str(card_path.resolve()),
            "ratings": str(rating_path.resolve()),
            "declaration": str(declaration_path.resolve()),
        }
    if any(set(ids) != round2_ids for ids in ordered_ids.values()):
        raise AssertionError("reviewer packages do not contain the same card set")
    if len({tuple(ids) for ids in ordered_ids.values()}) != len(reviewer_ids):
        raise AssertionError("reviewer package order must differ")
    prohibited = (
        "parent_card_id",
        '"cohort"',
        '"fault_family"',
        '"target_type"',
        '"event_id"',
        '"record_id"',
        '"traffic_quartile"',
        '"breach"',
        '"prediction"',
        '"future"',
    )
    for reviewer_id in reviewer_ids:
        text = Path(packages[reviewer_id]["cards"]).read_text(encoding="utf-8").lower()
        leaked = [token for token in prohibited if token.lower() in text]
        if leaked:
            raise AssertionError(f"private fields leaked to {reviewer_id}: {leaked}")
    manifest = {
        "protocol": ACADEMIC_PROTOCOL,
        "review_round": 2,
        "seed": seed,
        "reviewer_ids": list(reviewer_ids),
        "card_count": 48,
        "identical_card_sets": True,
        "distinct_reviewer_orders": True,
        "private_fields_absent": True,
        "packages": packages,
    }
    _write_json(output / "round2_package_manifest.json", manifest)
    return manifest


def build_round2_expert_package(
    academic_cards_path: str | Path,
    first_round_per_card_path: str | Path,
    output_dir: str | Path,
    *,
    seed: int = 20260723,
    traffic_quartiles_path: str | Path | None = None,
    traffic_quartiles: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    cards = json.loads(Path(academic_cards_path).read_text(encoding="utf-8"))
    first_round = pd.read_csv(first_round_per_card_path)
    traffic = dict(traffic_quartiles) if traffic_quartiles is not None else None
    if traffic_quartiles_path:
        rows = json.loads(Path(traffic_quartiles_path).read_text(encoding="utf-8"))
        traffic = {
            str(row["card_id"]): int(row["traffic_quartile"])
            for row in rows
            if "card_id" in row and "traffic_quartile" in row
        }
    selected, private_manifest = select_round2_cards(
        cards,
        first_round,
        seed=seed,
        traffic_quartiles=traffic,
    )
    manifest = write_round2_expert_package(
        output_dir,
        selected_cards=selected,
        private_manifest=private_manifest,
        seed=seed,
    )
    status_counts = Counter(
        item["governance_status"]
        for item in private_manifest
        if item["cohort"] == "generalization"
    )
    manifest["selection"] = {
        "remediation_cards": sum(
            item["cohort"] == "remediation" for item in private_manifest
        ),
        "generalization_cards": sum(
            item["cohort"] == "generalization" for item in private_manifest
        ),
        "generalization_status_counts": dict(status_counts),
        "traffic_quartile_available": traffic is not None,
    }
    _write_json(Path(output_dir) / "round2_package_manifest.json", manifest)
    return manifest


def _validate_round2_ratings(
    frame: pd.DataFrame,
    expected_card_ids: set[str],
) -> pd.DataFrame:
    required = {"reviewer_id", "round2_card_id", *ROUND2_DIMENSIONS, "comments"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"round2 ratings missing columns: {missing}")
    if frame.duplicated(["reviewer_id", "round2_card_id"]).any():
        raise ValueError("duplicate reviewer/round2_card_id rows")
    reviewer_ids = set(frame["reviewer_id"].astype(str))
    if reviewer_ids != set(ROUND2_REVIEWER_IDS):
        raise ValueError(f"round2 reviewer IDs must be {ROUND2_REVIEWER_IDS}")
    for reviewer_id, group in frame.groupby("reviewer_id"):
        ids = set(group["round2_card_id"].astype(str))
        if ids != expected_card_ids:
            raise ValueError(f"{reviewer_id} did not rate the expected 48-card set")
    numeric = frame.copy()
    for dimension in ROUND2_DIMENSIONS:
        numeric[dimension] = pd.to_numeric(numeric[dimension], errors="coerce")
        if numeric[dimension].isna().any():
            raise ValueError(f"{dimension} must be complete")
        if not numeric[dimension].between(1, 5).all():
            raise ValueError(f"{dimension} ratings must be in [1, 5]")
    low_or_wrong = numeric[list(ROUND2_DIMENSIONS)].le(2).any(axis=1)
    missing_comment = numeric["comments"].fillna("").astype(str).str.strip().eq("")
    if (low_or_wrong & missing_comment).any():
        raise ValueError("every 1-2 score row must include a comment")
    return numeric


def _round2_group_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "cards": 0,
            "ratings": 0,
            "dimensions": {},
            "card_median_at_least_4": {},
            "card_median_at_least_4_rate": {},
            "governance_cards_median_at_least_4": 0,
            "governance_cards_median_at_least_4_rate": None,
        }
    dimensions = _dimension_summary(
        frame.rename(columns={"round2_card_id": "card_id"}),
        ROUND2_DIMENSIONS,
    )
    card_medians = frame.groupby("round2_card_id")[list(ROUND2_DIMENSIONS)].median()
    appropriate = card_medians["governance_appropriateness"] >= 4
    return {
        "cards": int(frame["round2_card_id"].nunique()),
        "ratings": int(len(frame)),
        "dimensions": dimensions,
        "card_median_at_least_4": {
            dimension: int((card_medians[dimension] >= 4).sum())
            for dimension in ROUND2_DIMENSIONS
        },
        "card_median_at_least_4_rate": {
            dimension: float((card_medians[dimension] >= 4).mean())
            for dimension in ROUND2_DIMENSIONS
        },
        "governance_cards_median_at_least_4": int(appropriate.sum()),
        "governance_cards_median_at_least_4_rate": float(appropriate.mean()),
    }


def _freeze_round2_sources(
    ratings_paths: Sequence[str | Path],
    declaration_paths: Sequence[str | Path] | None,
    output_dir: Path,
) -> list[dict[str, Any]]:
    sources: list[tuple[str, Path]] = [
        (f"ratings_{reviewer_id}.csv", Path(path))
        for reviewer_id, path in zip(ROUND2_REVIEWER_IDS, ratings_paths)
    ]
    if declaration_paths is not None:
        if len(declaration_paths) != len(ROUND2_REVIEWER_IDS):
            raise ValueError("round2 source freeze requires exactly three declarations")
        sources.extend(
            (f"declaration_{reviewer_id}.md", Path(path))
            for reviewer_id, path in zip(ROUND2_REVIEWER_IDS, declaration_paths)
        )
    frozen_dir = output_dir / "source_evidence"
    frozen_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    for frozen_name, source in sources:
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = frozen_dir / frozen_name
        source_hash = _sha256_file(source)
        if destination.exists() and _sha256_file(destination) != source_hash:
            raise RuntimeError(
                f"refusing to overwrite changed frozen round2 evidence: {destination}"
            )
        if not destination.exists():
            shutil.copy2(source, destination)
        manifest.append(
            {
                "kind": "declaration" if frozen_name.endswith(".md") else "ratings",
                "reviewer_id": frozen_name.removeprefix("ratings_")
                .removeprefix("declaration_")
                .removesuffix(".csv")
                .removesuffix(".md"),
                "source_path": str(source.resolve()),
                "frozen_path": str(destination.resolve()),
                "bytes": source.stat().st_size,
                "sha256": source_hash,
            }
        )
    _write_json(output_dir / "source_evidence_hashes.json", manifest)
    return manifest


def _round2_declaration_audit(
    declaration_paths: Sequence[str | Path] | None,
) -> dict[str, Any]:
    if declaration_paths is None:
        return {
            "provided": False,
            "independent_main_analysis_eligible": None,
            "reviewers": {},
        }
    reviewers: dict[str, Any] = {}
    for reviewer_id, path_value in zip(ROUND2_REVIEWER_IDS, declaration_paths):
        path = Path(path_value)
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        id_present = reviewer_id in text
        experience_match = re.search(r"(\d+)\s*年", text)
        experience_years = int(experience_match.group(1)) if experience_match else None
        negative_independence_items = (
            "是否参与阈值公式或卡片生成：否",
            "是否参与第一轮评分：否",
            "是否看过第一轮分数、评论或私有抽样清单：否",
            "是否看过相关模型结果或未来越界信息：否",
            "是否与其他本轮专家交换评分：否",
        )
        independence_complete = all(item in text for item in negative_independence_items)
        no_conflict = "利益冲突：无" in text or "需要披露的利益冲突：无" in text
        reviewers[reviewer_id] = {
            "path": str(path.resolve()),
            "reviewer_id_present": id_present,
            "experience_years_lower_bound": experience_years,
            "experience_at_least_3_years": (
                experience_years is not None and experience_years >= 3
            ),
            "independence_items_all_negative": independence_complete,
            "no_declared_conflict": no_conflict,
            "role_mentions_sre_or_observability": any(
                token in lower for token in ("sre", "devops", "可观测性", "运维")
            ),
            "role_mentions_requirements_or_quality": any(
                token in lower for token in ("需求工程", "软件质量", "运行时验证")
            ),
        }
    eligible = all(
        row["reviewer_id_present"]
        and row["experience_at_least_3_years"]
        and row["independence_items_all_negative"]
        and row["no_declared_conflict"]
        for row in reviewers.values()
    )
    role_coverage = {
        "sre_operations_observability": any(
            row["role_mentions_sre_or_observability"] for row in reviewers.values()
        ),
        "requirements_quality_runtime_validation": any(
            row["role_mentions_requirements_or_quality"] for row in reviewers.values()
        ),
    }
    return {
        "provided": True,
        "independent_main_analysis_eligible": eligible and all(role_coverage.values()),
        "required_role_coverage": role_coverage,
        "reviewers": reviewers,
    }


def summarize_round2_expert_review(
    ratings_paths: Sequence[str | Path],
    private_manifest_path: str | Path,
    output_dir: str | Path,
    *,
    first_round_ratings_path: str | Path | None = None,
    declaration_paths: Sequence[str | Path] | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    source_manifest = _freeze_round2_sources(
        ratings_paths, declaration_paths, output
    )
    declaration_audit = _round2_declaration_audit(declaration_paths)
    private = pd.DataFrame(
        json.loads(Path(private_manifest_path).read_text(encoding="utf-8"))
    )
    expected = set(private["round2_card_id"].astype(str))
    if len(expected) != 48:
        raise ValueError("private round2 manifest must contain 48 cards")
    frames = [_read_csv_with_possible_preamble(Path(path)) for path in ratings_paths]
    ratings = _validate_round2_ratings(pd.concat(frames, ignore_index=True), expected)
    merged = ratings.merge(private, on="round2_card_id", how="left", validate="many_to_one")
    if merged["cohort"].isna().any():
        raise ValueError("round2 rating could not be mapped to private manifest")
    overall = _round2_group_summary(merged)
    primary_rate = overall["governance_cards_median_at_least_4_rate"]
    per_card = (
        merged.groupby(
            ["round2_card_id", "parent_card_id", "cohort", "governance_status"],
            as_index=False,
        )[list(ROUND2_DIMENSIONS)]
        .median()
    )
    low_cases = merged.loc[
        merged[list(ROUND2_DIMENSIONS)].le(2).any(axis=1),
        [
            "reviewer_id",
            "round2_card_id",
            "parent_card_id",
            "cohort",
            "governance_status",
            *ROUND2_DIMENSIONS,
            "comments",
        ],
    ]
    state_error_mask = (
        merged["comments"]
        .fillna("")
        .astype(str)
        .str.contains(
            r"STATE_ERROR:|状态错误|建议状态|应改为|should be classified",
            case=False,
            regex=True,
        )
    )
    state_error_comments = merged.loc[
        state_error_mask,
        [
            "reviewer_id",
            "round2_card_id",
            "parent_card_id",
            "cohort",
            "governance_status",
            "comments",
        ],
    ]
    summary: dict[str, Any] = {
        "protocol": ACADEMIC_PROTOCOL,
        "review_round": 2,
        "primary_endpoint": {
            "definition": (
                "at least 75% of cards have median governance_appropriateness >= 4"
            ),
            "observed_rate": primary_rate,
            "threshold": 0.75,
            "pass": bool(primary_rate is not None and primary_rate >= 0.75),
        },
        "overall": overall,
        "by_cohort": {
            cohort: _round2_group_summary(group)
            for cohort, group in merged.groupby("cohort")
        },
        "by_governance_status": {
            status: _round2_group_summary(
                merged.loc[merged["governance_status"] == status]
            )
            for status in GOVERNANCE_STATUSES
        },
        "by_reviewer_and_governance_status": {
            reviewer_id: {
                status: _round2_group_summary(
                    merged.loc[
                        (merged["reviewer_id"] == reviewer_id)
                        & (merged["governance_status"] == status)
                    ]
                )
                for status in GOVERNANCE_STATUSES
            }
            for reviewer_id in ROUND2_REVIEWER_IDS
        },
        "low_score_rows": int(len(low_cases)),
        "state_error_comment_rows": int(len(state_error_comments)),
        "source_evidence": source_manifest,
        "declaration_audit": declaration_audit,
        "interpretation_limits": [
            "The review validates governance handling, not a business SLO.",
            "Cross-round differences are descriptive and are not causal estimates.",
            "The held-out generalization cohort is the primary independent validation evidence.",
            "All negative results and disagreements are retained.",
        ],
    }
    if first_round_ratings_path is not None:
        first = pd.read_csv(first_round_ratings_path)
        first_medians = (
            first.groupby("card_id", as_index=False)["runtime_actionability"]
            .median()
            .rename(columns={"runtime_actionability": "round1_runtime_actionability_median"})
        )
        remediation = per_card.loc[
            per_card["cohort"] == "remediation",
            ["parent_card_id", "runtime_actionability"],
        ].rename(columns={"runtime_actionability": "round2_runtime_actionability_median"})
        paired = remediation.merge(
            first_medians,
            left_on="parent_card_id",
            right_on="card_id",
            how="left",
            validate="one_to_one",
        )
        paired["descriptive_median_difference"] = (
            paired["round2_runtime_actionability_median"]
            - paired["round1_runtime_actionability_median"]
        )
        summary["remediation_runtime_actionability_descriptive"] = {
            "cards": len(paired),
            "round1": _quantiles(paired["round1_runtime_actionability_median"].tolist()),
            "round2": _quantiles(paired["round2_runtime_actionability_median"].tolist()),
            "difference": _quantiles(paired["descriptive_median_difference"].tolist()),
            "causal_interpretation_allowed": False,
        }
    output.mkdir(parents=True, exist_ok=True)
    ratings.to_csv(output / "ratings_normalized.csv", index=False, encoding="utf-8-sig")
    per_card.to_csv(output / "per_card_results.csv", index=False, encoding="utf-8-sig")
    low_cases.to_csv(output / "low_score_and_disputed_cases.csv", index=False, encoding="utf-8-sig")
    state_error_comments.to_csv(
        output / "state_error_comments.csv",
        index=False,
        encoding="utf-8-sig",
    )
    _write_json(output / "summary.json", summary)
    (output / "ROUND2_REVIEW_REPORT.md").write_text(
        _round2_report_markdown(summary), encoding="utf-8"
    )
    return summary


def _round2_report_markdown(summary: Mapping[str, Any]) -> str:
    primary = summary["primary_endpoint"]
    observed = primary["observed_rate"]
    observed_text = "NA" if observed is None else f"{observed:.1%}"
    result = "通过" if primary["pass"] else "未通过"
    overall = summary["overall"]
    threshold_cards = overall["card_median_at_least_4"]["threshold_plausibility"]
    remediation = summary["by_cohort"]["remediation"][
        "governance_cards_median_at_least_4"
    ]
    generalization = summary["by_cohort"]["generalization"][
        "governance_cards_median_at_least_4"
    ]
    return f"""# Runtime NFR 独立二次专家评审报告

_本报告按冻结协议同时保留支持性结果、负结果和专家分歧。_

---

## 🎯 主要终点

`governance_appropriateness` 卡片中位数不低于 4 的比例为 {observed_text}；
预设门槛为 75%，结果：{result}。

## 📌 冻结结果

- 修复队列：{remediation}/24 张达到治理适当性门槛
- 泛化队列：{generalization}/24 张达到治理适当性门槛
- 原始阈值合理性中位数不低于 4：{threshold_cards}/48 张
- 低分行：{summary["low_score_rows"]}；状态错误评论：{summary["state_error_comment_rows"]}

## 📊 分层报告

完整数值见同目录 `summary.json` 和 `per_card_results.csv`。结果分别按总体、
修复队列、泛化队列和四类治理状态报告。

## ⚠️ 解释边界

本轮验证治理层，不验证正式业务 SLO。两轮专家不同，且泛化队列使用新卡片，
因此跨轮差异只作描述，不作因果改进解释。泛化队列是主要独立验证依据。
`runtime_actionability` 应解释为治理或决策可操作性，不代表所有原始阈值均可立即部署。
"""


EXTERNAL_REQUIRED_FIELDS = (
    "dataset_name",
    "dataset_version",
    "source_url",
    "license",
    "frozen_sha256",
    "frozen_at",
    "time_range_start",
    "time_range_end",
    "intended_role",
    "independent_run_count",
    "service_level_sli_available",
    "estimated_adapter_effort_days",
    "history_window_minutes_available",
    "early_input_minutes_available",
    "future_window_minutes_available",
    "reference_slo_completeness",
)


def audit_external_dataset_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    missing = [field for field in EXTERNAL_REQUIRED_FIELDS if not manifest.get(field)]
    role_ok = manifest.get("intended_role") == "external_test_only"
    isolation = manifest.get("used_for_rule_threshold_feature_or_model_selection") is False
    hash_value = str(manifest.get("frozen_sha256", ""))
    hash_ok = bool(re.fullmatch(r"[0-9a-fA-F]{64}", hash_value))
    temporal_ok = bool(
        manifest.get("time_range_start")
        and manifest.get("time_range_end")
        and str(manifest["time_range_start"]) <= str(manifest["time_range_end"])
    )
    independent_runs = int(manifest.get("independent_run_count") or 0)
    adapter_days = float(manifest.get("estimated_adapter_effort_days") or float("inf"))
    history_minutes = float(manifest.get("history_window_minutes_available") or 0)
    early_minutes = float(manifest.get("early_input_minutes_available") or 0)
    future_minutes = float(manifest.get("future_window_minutes_available") or 0)
    reference_status = str(manifest.get("reference_slo_completeness") or "")
    reference_ok = reference_status in {"absent", "threshold_only", "complete"}
    checks = {
        "required_metadata_complete": not missing,
        "sha256_format_valid": hash_ok,
        "external_test_only": role_ok,
        "not_used_for_selection": isolation,
        "time_range_order_valid": temporal_ok,
        "at_least_100_independent_runs": independent_runs >= 100,
        "service_level_sli_available": manifest.get("service_level_sli_available") is True,
        "adapter_effort_within_two_days": adapter_days <= 2.0,
        "reference_slo_status_valid": reference_ok,
        "frozen_history_window_available": history_minutes >= 1440,
        "frozen_early_input_window_available": early_minutes >= 5,
        "frozen_future_window_available": future_minutes >= 15,
    }
    provenance_checks = (
        "required_metadata_complete",
        "sha256_format_valid",
        "external_test_only",
        "not_used_for_selection",
        "time_range_order_valid",
        "at_least_100_independent_runs",
        "service_level_sli_available",
        "adapter_effort_within_two_days",
        "reference_slo_status_valid",
    )
    provenance_pass = all(checks[name] for name in provenance_checks)
    prediction_pass = provenance_pass and all(
        checks[name]
        for name in (
            "frozen_history_window_available",
            "frozen_early_input_window_available",
            "frozen_future_window_available",
        )
    )
    if not provenance_pass:
        decision = "reject"
    elif prediction_pass:
        decision = "validity_and_prediction"
    else:
        decision = "validity_only"
    return {
        "protocol": ACADEMIC_PROTOCOL,
        "dataset": manifest.get("dataset_name"),
        "missing_fields": missing,
        "checks": checks,
        "pass": decision != "reject",
        "decision": decision,
        "prediction_protocol_compatible": prediction_pass,
        "reference_threshold_evaluation_allowed": reference_status == "complete",
        "claim_limit": (
            "An accepted manifest establishes provenance, minimum scale, construct availability, "
            "and test-only isolation. validity_only does not permit threshold-correctness or "
            "prediction claims; even validity_and_prediction does not establish construct equivalence."
        ),
    }
