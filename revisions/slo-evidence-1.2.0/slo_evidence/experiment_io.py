"""Explicit conversion of frozen observational cards to the v1.2 interface."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import hashlib
from .core import canonical_hash, statistical_value

ASSESSMENT_VERSION = "measurement-audit/20260930"

LOCAL_DEFINITION = b"""Derived-series definition, not certification of source instrumentation.
M1 UNKNOWN: latency is the numeric rrt column aggregated by minute, unit ms as declared
by the frozen dataset adapter; error_ratio=(error+timeout)/request clipped 0..1.
This is not a request-level percentile. No equivalence to user-experience SLI is asserted.
The upstream rrt unit/meaning and error-timeout overlap are not independently established.
M2 UNKNOWN: timestamps floored to minute; window [start,end); finite values only;
request<=0 excluded for error_ratio; count is distinct minute count.
Source sampling/collection and missingness are not established by this local transform.
M3: source error/timeout nulls filled 0 by the legacy transform; same-minute
ratios and rrt averaged, not request-weighted. Service-specific known fault
intervals removed offline using the frozen graph mapping. Historical slots,
source and query hashes recorded. These explain the derived computation only.
M4 UNKNOWN: source collection precision, zero-vs-missing upstream semantics,
and sampling fidelity have not been independently verified. Code is not proof.
"""


def utc(value):
    d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.tzinfo is None:
        # Frozen source mapping uses source_start_time in UTC; converted CSVs lost the suffix.
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


def evidence_store():
    return {"artifact://local-derived-measurement-definition": LOCAL_DEFINITION}


def make_card(frozen, stats, *, source_id, source_hash, query_version,
              generator=None, value=None, observation_hash=None):
    end = datetime.fromisoformat(utc(frozen["history_end"]))
    ref = {"uri":"artifact://local-derived-measurement-definition", "sha256":hashlib.sha256(LOCAL_DEFINITION).hexdigest()}
    if generator is None:
        generator = {"id":"robust-statistical", "version":"1-corrected", "kind":"statistical",
                     "fit_end":(end-timedelta(minutes=1)).isoformat(), "input_end":(end-timedelta(minutes=1)).isoformat(),
                     "quantile":0.95,"robust_k":3.0}
    result = {
        "card_id":frozen["card_id"]+"-"+generator["id"], "parent_card_id":frozen["card_id"],
        "subject_id":frozen["subject_id"],"sli":frozen["sli"],"unit":frozen["unit"],
        "window_start":utc(frozen["history_start"]),"window_end":utc(frozen["history_end"]),
        "observations_sha256":observation_hash or canonical_hash({"source":source_hash,"window":[frozen["history_start"],frozen["history_end"]],
                                                                  "subject":frozen["subject_id"],"sli":frozen["sli"],"query":query_version}),
        "observation_hash_kind":"content" if observation_hash else "source-window-query-reference",
        "measurement_definition":"derived-minute-series/legacy-preserved/2",
        "measurement_assessment_version": ASSESSMENT_VERSION,
        "statistics":stats,"candidate_value":statistical_value(stats,frozen["sli"]) if value is None else value,
        "generator":generator,
        "provenance":{"source_id":source_id,"source_sha256":source_hash,"query_version":query_version,
                      "evidence_cutoff":utc(frozen["history_end"]),"original_dataset_hash":frozen["evidence_provenance"]["dataset_hash"]},
        "measurement_evidence":{
            "M1":{"status":"unknown", "refs":[ref], "scope":"local derived formula only",
                  "reason":"upstream rrt meaning/unit and error-timeout counting semantics unverified"},
            "M2":{"status":"unknown", "refs":[ref], "scope":"local minute window only",
                  "reason":"source sampling and collection process unverified"},
            "M3":{"status":"verified", "refs":[ref], "scope":"converted source fields to minute values and candidate; not upstream instrumentation"}},
        "closures":[],
    }
    result["measurement_evidence"]["M4"] = {"status":"unknown","refs":[],"reason":"source precision and upstream special-value semantics unverified"}
    return result
