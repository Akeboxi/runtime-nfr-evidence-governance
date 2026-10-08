"""Evidence tasks, not normative SLO sufficiency or human-judgment verification.

v1-corrected is an audit-only reconstruction of the old numeric predicates.
v1.2 requires explicit M1--M4 evidence and supports scoped task resolution.
The distinction must not be removed when reporting historical results.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Mapping

import numpy as np

VERSION = "slo-evidence/1.2.0"
ROUTES = ("A", "B", "C", "D")
STATUS_NAMES = {
    "A": "insufficient_evidence", "B": "threshold_unresolved",
    "C": "needs_context_review", "D": "candidate_for_stakeholder_review",
}


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def timestamp(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamps must declare timezone")
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class Policy:
    window_minutes: int = 1440
    coverage_min: float = 0.5
    tail_cutoff: float = 10.0
    diagnostic_quantile: float = 0.95
    version: str = VERSION

    def __post_init__(self):
        if isinstance(self.window_minutes, bool) or int(self.window_minutes) != self.window_minutes or self.window_minutes <= 0:
            raise ValueError("window_minutes must be a positive integer")
        if not math.isfinite(self.coverage_min) or not 0 < self.coverage_min <= 1:
            raise ValueError("coverage_min must be in (0, 1]")
        if not math.isfinite(self.tail_cutoff) or self.tail_cutoff <= 0:
            raise ValueError("tail cutoff must be positive")
        if not 0 < self.diagnostic_quantile < 1:
            raise ValueError("invalid diagnostic quantile")

    @property
    def minimum_samples(self) -> int:
        return int((Decimal(str(self.coverage_min)) * Decimal(self.window_minutes))
                   .to_integral_value(rounding=ROUND_CEILING))

    def history_missing(self, valid_samples: int) -> bool:
        if isinstance(valid_samples, bool) or int(valid_samples) != valid_samples or not 0 <= valid_samples <= self.window_minutes:
            raise ValueError("invalid minute count")
        return valid_samples < self.minimum_samples


def summarize(values, quantile=0.95) -> dict:
    """Use actual finite observations for zero status; an empty window is not all-zero."""
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if (x < 0).any():
        raise ValueError("negative SLI observation")
    median = float(np.median(x)) if len(x) else 0.0
    mad = float(np.median(np.abs(x - median))) if len(x) else 0.0
    return {
        "valid_samples": int(len(x)), "quantile_level": float(quantile),
        "quantile_value": float(np.quantile(x, quantile)) if len(x) else 0.0,
        "median": median, "mad": mad, "robust_scale": max(1.4826 * mad, 1e-6),
        "nonzero_count": int(np.count_nonzero(x)),
        "max_value": float(x.max()) if len(x) else None,
        "all_zero": bool(len(x) and np.count_nonzero(x) == 0),
    }


def statistical_value(stats: dict, sli: str, robust_k=3.0) -> float:
    if stats["valid_samples"] == 0:
        return 0.0  # A provisional value only; history gate will block it.
    if sli == "error_ratio" and stats["all_zero"]:
        return 0.0
    return max(stats["quantile_value"], stats["median"] + robust_k * stats["robust_scale"])


def legacy_route(stats: dict, sli: str, policy=Policy(), robust_k=3.0) -> dict:
    """Corrected v1 predicates for comparison, NOT v1.2 measurement qualification."""
    b = statistical_value(stats, sli, robust_k)
    flags = []
    if policy.history_missing(stats["valid_samples"]):
        flags.append("history_insufficient")
    if sli == "error_ratio":
        if stats["all_zero"]:
            flags.append("zero_error_baseline")
        floor = stats["median"] + robust_k * 1e-6
        if (math.isclose(stats["mad"], 0.0) and stats["quantile_value"] <= floor + 1e-15
                and math.isclose(b, max(stats["quantile_value"], floor), rel_tol=0, abs_tol=1e-15)):
            flags.append("threshold_from_robust_scale_floor")
    tail = (stats["quantile_value"] - stats["median"]) / stats["robust_scale"]
    if sli == "latency" and tail >= policy.tail_cutoff:
        flags.append("context_tail")
    route = ("A" if "history_insufficient" in flags else
             "B" if any(f in flags for f in ("zero_error_baseline", "threshold_from_robust_scale_floor")) else
             "C" if "context_tail" in flags else "D")
    return {"route": route, "flags": flags, "value": b, "tail_ratio": tail if sli == "latency" else None}


def scope_hash(card: dict, policy=Policy()) -> str:
    """Exclude ID/parent/closures, so a genuine carry has the same immutable scope."""
    return canonical_hash({k: card[k] for k in (
        "subject_id", "sli", "unit", "window_start", "window_end", "observations_sha256",
        "measurement_definition", "generator", "candidate_value", "statistics", "provenance"
    )} | {"policy": asdict(policy)})


def _evidence_valid(refs, evidence_store: Mapping[str, bytes]) -> bool:
    if not isinstance(refs, list) or not refs:
        return False
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("uri") not in evidence_store:
            return False
        data = evidence_store[ref["uri"]]
        if not data or hashlib.sha256(data).hexdigest() != ref.get("sha256"):
            return False
    return True


def _validate_card(card: dict, policy: Policy, at: str):
    for key in ("card_id", "subject_id", "sli", "unit", "measurement_definition"):
        if not isinstance(card.get(key), str) or not card[key].strip():
            raise ValueError(f"missing {key}")
    if card["sli"] not in {"latency", "error_ratio"}:
        raise ValueError("unsupported SLI")
    expected_unit = "ms" if card["sli"] == "latency" else "ratio"
    if card["unit"] != expected_unit:
        raise ValueError("unit does not match SLI")
    value = card.get("candidate_value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid candidate value")
    if card["sli"] == "error_ratio" and value > 1:
        raise ValueError("ratio candidate exceeds 1")
    start, end = timestamp(card["window_start"]), timestamp(card["window_end"])
    if (end-start).total_seconds() != policy.window_minutes * 60 or end > timestamp(at):
        raise ValueError("window/policy/evaluation-time mismatch")
    p = card.get("provenance", {})
    for key in ("source_id", "source_sha256", "query_version", "evidence_cutoff"):
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ValueError(f"missing provenance.{key}")
    for digest in (p["source_sha256"], card.get("observations_sha256", "")):
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid provenance hash")
    if timestamp(p["evidence_cutoff"]) > end:
        raise ValueError("evidence after audit cutoff")
    g = card.get("generator", {})
    for key in ("id", "version", "kind", "fit_end", "input_end"):
        if not str(g.get(key, "")).strip():
            raise ValueError(f"missing generator.{key}")
    if timestamp(g["fit_end"]) >= end or timestamp(g["input_end"]) >= end:
        raise ValueError("generator reads audit/future information")
    s = card["statistics"]
    policy.history_missing(s["valid_samples"])
    if s["quantile_level"] != policy.diagnostic_quantile:
        raise ValueError("diagnostic quantile does not match policy")
    if not 0 <= s["nonzero_count"] <= s["valid_samples"]:
        raise ValueError("inconsistent nonzero count")
    if isinstance(s["nonzero_count"], bool) or int(s["nonzero_count"]) != s["nonzero_count"]:
        raise ValueError("nonzero count must be an integer")
    if s["all_zero"] != bool(s["valid_samples"] and s["nonzero_count"] == 0):
        raise ValueError("inconsistent all_zero statistic")
    for key in ("quantile_value", "median", "mad", "robust_scale"):
        if not math.isfinite(s[key]) or s[key] < 0:
            raise ValueError(f"invalid statistic {key}")
    if s["robust_scale"] < 1e-6:
        raise ValueError("invalid robust scale")
    maximum = s.get("max_value")
    if s["valid_samples"]:
        if maximum is None or not math.isfinite(maximum) or maximum < 0:
            raise ValueError("missing/invalid observed maximum")
        if (maximum == 0) != s["all_zero"] or max(s["quantile_value"], s["median"]) > maximum:
            raise ValueError("inconsistent observed statistics")
    elif maximum is not None:
        raise ValueError("empty history cannot have an observed maximum")


def decide(card: dict, *, at: str, evidence_store: Mapping[str, bytes] | None = None,
           policy=Policy()) -> dict:
    """Validate records and reroute; this does not authenticate human judgment.

    Evidence-store bytes and role labels are supplied by the calling application.
    Cryptographic identity, source authority and semantic truth remain external.
    """
    store = evidence_store or {}
    _validate_card(card, policy, at)
    scope = scope_hash(card, policy)
    missing = [m for m in ("M1", "M2", "M3", "M4")
               if card.get("measurement_evidence", {}).get(m, {}).get("status") != "verified"
               or not _evidence_valid(card.get("measurement_evidence", {}).get(m, {}).get("refs"), store)]
    s = card["statistics"]
    tail = ((s["quantile_value"]-s["median"])/s["robust_scale"] if card["sli"] == "latency" else None)
    triggers = {"history": policy.history_missing(s["valid_samples"]),
                "measurement": bool(missing), "context": tail is not None and tail >= policy.tail_cutoff}
    accepted, rejected, seen = [], [], set()
    resolved = set()
    for record in card.get("closures", []):
        rid = record.get("record_id")
        reason = None
        role_options = {"context": {"service_owner", "sre_context_reviewer"},
                        "measurement": {"measurement_owner", "observability_reviewer"}}
        task = record.get("task_type")
        if not rid or rid in seen:
            reason = "missing_or_duplicate_record_id"
        elif task not in role_options or record.get("status") != "confirmed":
            reason = "not_a_confirmed_closable_task"
        elif record.get("scope_hash") != scope:
            reason = "stale_scope_or_policy"
        elif record.get("reviewer_role") not in role_options[task] or not str(record.get("reviewer_id", "")).strip():
            reason = "missing_or_ineligible_reviewer"
        elif not str(record.get("resolution", "")).strip() or not _evidence_valid(record.get("refs"), store):
            reason = "missing_or_unverifiable_evidence"
        else:
            try:
                reviewed, expires, now = timestamp(record["reviewed_at"]), timestamp(record["expires_at"]), timestamp(at)
                if not timestamp(card["window_end"]) <= reviewed <= now < expires:
                    reason = "invalid_review_time_or_expired"
            except (KeyError, TypeError, ValueError):
                reason = "invalid_review_time_or_expired"
        if not reason and task == "measurement" and not set(missing).issubset(set(record.get("resolved_dimensions", []))):
            reason = "measurement_dimensions_unresolved"
        if reason:
            rejected.append({"record_id": rid, "reason": reason})
        else:
            resolved.add(task)
            accepted.append(rid)
        seen.add(rid)
    tasks = [t for t, triggered in triggers.items() if triggered and t not in resolved]
    route = next((r for t, r in (("history", "A"), ("measurement", "B"), ("context", "C")) if t in tasks), "D")
    return {"version": VERSION, "policy": asdict(policy), "route": route, "evidence_status": STATUS_NAMES[route],
            "scope_hash": scope, "triggered_facts": triggers, "open_tasks": tasks,
            "missing_measurement_dimensions": missing, "accepted_closure_ids": accepted,
            "rejected_closures": rejected, "tail_ratio": tail,
            "expected_minutes": policy.window_minutes, "minimum_samples": policy.minimum_samples,
            "business_slo_approved": False}


def derive_child(parent: dict, updated: dict, *, mode: str, policy=Policy()) -> dict:
    if mode not in {"carry", "regen"}:
        raise ValueError("mode must be carry or regen")
    child = copy.deepcopy(updated)
    same = scope_hash(parent, policy) == scope_hash(child, policy)
    if mode == "carry" and not same:
        raise ValueError("carry cannot change observations, window, definition, value, generator or policy scope")
    child["parent_card_id"] = parent["card_id"]
    child["derivation"] = mode
    child["card_id"] = "child-" + canonical_hash({"parent": parent["card_id"], "content": child})[:20]
    return child
