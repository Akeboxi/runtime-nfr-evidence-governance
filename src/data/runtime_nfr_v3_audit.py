"""Cross-artifact audit for the frozen Runtime NFR v3 research results."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import math
from typing import Any, Sequence

import pandas as pd

from .runtime_nfr_dataset import RuntimeNFRSample
from .runtime_nfr_external import validate_external_split_manifest


FORMAL_AUDIT_PROTOCOL = "runtime-nfr-v3-formal-audit/2"


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def audit_runtime_nfr_v3_formal_results(
    samples: Sequence[RuntimeNFRSample],
    output_dir: str | Path,
    *,
    round2_ratings_path: str | Path,
    round2_source_hashes_path: str | Path,
    v2_cards_path: str | Path,
    academic_cards_path: str | Path,
    external_split_path: str | Path,
    external_manifest_audit_path: str | Path,
    oof_predictions_path: str | Path,
    feature_ablation_summary_path: str | Path,
    legacy_integrity_audit_path: str | Path,
    frozen_result_paths: Sequence[str | Path],
    formal_baseline_path: str | Path | None = None,
) -> dict[str, Any]:
    round2 = pd.read_csv(round2_ratings_path)
    source_hashes = json.loads(
        Path(round2_source_hashes_path).read_text(encoding="utf-8")
    )
    source_hashes_ok = all(
        Path(row["frozen_path"]).is_file()
        and _hash(Path(row["frozen_path"])) == row["sha256"]
        for row in source_hashes
    )
    round2_checks = {
        "exactly_144_rows": len(round2) == 144,
        "exactly_three_reviewers": round2["reviewer_id"].nunique() == 3,
        "exactly_48_cards": round2["round2_card_id"].nunique() == 48,
        "each_reviewer_has_48_cards": bool(
            (round2.groupby("reviewer_id")["round2_card_id"].nunique() == 48).all()
        ),
        "six_source_files_hash_match": len(source_hashes) == 6
        and source_hashes_ok,
    }

    v2_cards = json.loads(Path(v2_cards_path).read_text(encoding="utf-8"))
    academic_cards = json.loads(
        Path(academic_cards_path).read_text(encoding="utf-8")
    )
    v2_by_id = {str(card["card_id"]): card for card in v2_cards}
    academic_by_parent = {
        str(card["parent_card_id"]): card for card in academic_cards
    }
    threshold_checks = {
        "same_card_count": len(v2_cards) == len(academic_cards) == 8668,
        "same_parent_card_set": set(v2_by_id) == set(academic_by_parent),
        "raw_threshold_exactly_preserved": all(
            math.isclose(
                float(source["threshold"]),
                float(academic_by_parent[card_id]["raw_threshold"]),
                rel_tol=0.0,
                abs_tol=0.0,
            )
            for card_id, source in v2_by_id.items()
        ),
    }

    split = json.loads(Path(external_split_path).read_text(encoding="utf-8"))
    validate_external_split_manifest(split)
    external_audit = json.loads(
        Path(external_manifest_audit_path).read_text(encoding="utf-8")
    )
    external_checks = {
        "train_validation_test_disjoint": True,
        "not_used_for_selection": external_audit["checks"][
            "not_used_for_selection"
        ],
        "accepted_role_explicit": external_audit["decision"]
        in {"validity_only", "validity_and_prediction"},
    }

    oof = pd.read_csv(oof_predictions_path)
    model_pairs = {
        model: set(map(tuple, group[["event_id", "app_id"]].to_numpy()))
        for model, group in oof.groupby("model")
    }
    pair_sets = list(model_pairs.values())
    ablation = json.loads(
        Path(feature_ablation_summary_path).read_text(encoding="utf-8")
    )
    oof_checks = {
        "all_six_models_present": len(model_pairs) == 6,
        "each_model_event_app_once": bool(
            all(
                not group.duplicated(["event_id", "app_id"]).any()
                for _, group in oof.groupby("model")
            )
        ),
        "same_event_app_set_all_models": bool(
            pair_sets and all(pairs == pair_sets[0] for pairs in pair_sets[1:])
        ),
        "locked_test_not_used": ablation["locked_test_used"] is False,
        "forbidden_inputs_absent": not ablation["forbidden_inputs_used"],
    }
    temporal_checks = {
        "all_label_windows_nonoverlapping_after_input": all(
            sample.metadata.label_start >= sample.metadata.input_end
            and sample.metadata.label_end > sample.metadata.label_start
            for sample in samples
        ),
        "all_boundary_windows_end_before_input_end": all(
            sample.metadata.boundary_end <= sample.metadata.input_end
            for sample in samples
        ),
        "events_audited": len(samples),
    }
    legacy_audit = json.loads(
        Path(legacy_integrity_audit_path).read_text(encoding="utf-8")
    )
    integrity_checks = {
        "legacy_v1_v2_integrity_pass": legacy_audit["pass"] is True,
    }

    result_rows = [
        {
            "path": str(Path(path).resolve()),
            "sha256": _hash(Path(path)),
        }
        for path in frozen_result_paths
    ]
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    baseline_path = (
        Path(formal_baseline_path)
        if formal_baseline_path is not None
        else output / "formal_result_hashes_baseline.json"
    )
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    if baseline_path.exists():
        baseline_rows = json.loads(
            baseline_path.read_text(encoding="utf-8")
        )
        result_replay_equal = baseline_rows == result_rows
        baseline_created_now = False
    else:
        baseline_path.write_text(
            json.dumps(result_rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result_replay_equal = True
        baseline_created_now = True
    integrity_checks["formal_results_match_frozen_hashes"] = result_replay_equal

    check_groups = {
        "round2": round2_checks,
        "thresholds": threshold_checks,
        "external": external_checks,
        "oof": oof_checks,
        "temporal": temporal_checks,
        "integrity": integrity_checks,
    }
    boolean_values = [
        value
        for group in check_groups.values()
        for key, value in group.items()
        if key != "events_audited"
    ]
    result = {
        "protocol": FORMAL_AUDIT_PROTOCOL,
        "checks": check_groups,
        "formal_result_baseline": str(baseline_path.resolve()),
        "formal_result_baseline_created_now": baseline_created_now,
        "pass": bool(all(boolean_values)),
    }
    audit_path = output / "formal_audit.json"
    audit_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
