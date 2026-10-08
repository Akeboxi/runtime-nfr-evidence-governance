"""Behavioral contracts for scoped evidence closure and alternate generators."""
import copy
import hashlib
import unittest

from slo_evidence import Policy, summarize, statistical_value, legacy_route, decide, scope_hash, derive_child


def example(sli="latency", *, complete=True, tail=True):
    # Explicit synthetic software-test evidence, never expert or production evidence.
    data = b"SYNTHETIC contract fixture: not an actual reviewer confirmation"
    refs = [{"uri": "fixture://contract", "sha256": hashlib.sha256(data).hexdigest()}]
    values = [1.] * 1300 + [20.] * 140 if tail else [float(i % 10 + 1) for i in range(1440)]
    if sli == "error_ratio":
        values = [0.] * 1438 + [.01, .02]
    stats = summarize(values)
    card = {
        "card_id": "fixture-parent", "subject_id": "service", "sli": sli,
        "unit": "ms" if sli == "latency" else "ratio",
        "window_start": "2026-01-01T00:00:00Z", "window_end": "2026-01-02T00:00:00Z",
        "observations_sha256": "b" * 64, "measurement_definition": "fixture definition/1",
        "candidate_value": statistical_value(stats, sli), "statistics": stats,
        "generator": {"id": "statistical", "version": "1", "kind": "statistical",
                      "fit_end": "2026-01-01T23:59:00Z", "input_end": "2026-01-01T23:59:00Z"},
        "provenance": {"source_id": "fixture", "source_sha256": "a" * 64,
                       "query_version": "1", "evidence_cutoff": "2026-01-02T00:00:00Z"},
        "measurement_evidence": {m: {"status": "verified" if complete else "unknown", "refs": refs if complete else []}
                                 for m in ("M1", "M2", "M3", "M4")},
        "closures": [],
    }
    return card, {"fixture://contract": data}, refs


def close(card, refs, task="context"):
    return {"record_id": "closure-" + task, "task_type": task, "status": "confirmed",
            "scope_hash": scope_hash(card), "reviewer_id": "synthetic-reviewer",
            "reviewer_role": "sre_context_reviewer" if task == "context" else "measurement_owner",
            "reviewed_at": "2026-01-02T01:00:00Z", "expires_at": "2026-01-03T00:00:00Z",
            "resolution": "synthetic scoped explanation", "refs": refs,
            "resolved_dimensions": ["M1", "M2", "M3", "M4"]}


def check(card, store=None, at="2026-01-02T02:00:00Z", policy=Policy()):
    return decide(card, at=at, evidence_store=store, policy=policy)


class EvidenceContracts(unittest.TestCase):
    def test_decimal_minima(self):
        for minutes, fraction, expected in ((720,.4,288),(1080,.4,432),(1440,.4,576),(1440,.5,720)):
            p = Policy(minutes, fraction)
            self.assertEqual(p.minimum_samples, expected)
            self.assertTrue(p.history_missing(expected-1))
            self.assertFalse(p.history_missing(expected))

    def test_sparse_errors_are_not_all_zero(self):
        s = summarize([0.] * 98 + [.01, .02])
        self.assertEqual(s["quantile_value"], 0)
        self.assertEqual(s["nonzero_count"], 2)
        self.assertFalse(s["all_zero"])
        self.assertEqual(statistical_value(s, "error_ratio"), 3e-6)
        d = legacy_route(s, "error_ratio")
        self.assertNotIn("zero_error_baseline", d["flags"])
        self.assertIn("threshold_from_robust_scale_floor", d["flags"])

    def test_empty_and_nonfinite_are_not_zero_history(self):
        s = summarize([float("nan"), float("inf")])
        self.assertEqual(s["valid_samples"], 0)
        self.assertFalse(s["all_zero"])
        self.assertEqual(legacy_route(s, "error_ratio")["route"], "A")

    def test_true_zero_statistical_value(self):
        self.assertEqual(statistical_value(summarize([0.] * 1440), "error_ratio"), 0)

    def test_note_alone_does_not_close_context(self):
        c, s, _ = example()
        c["closures"] = [{"record_id": "note", "task_type": "context", "resolution": "looks normal"}]
        self.assertEqual(check(c, s)["route"], "C")

    def test_verified_scoped_closure_allows_d_not_approval(self):
        c, s, refs = example()
        c["closures"] = [close(c, refs)]
        result = check(c, s)
        self.assertEqual(result["route"], "D")
        self.assertTrue(result["triggered_facts"]["context"])
        self.assertFalse(result["business_slo_approved"])

    def test_context_closure_does_not_bypass_measurement(self):
        c, s, refs = example(complete=False)
        c["closures"] = [close(c, refs)]
        self.assertEqual(check(c, s)["route"], "B")

    def test_unknown_measurement_needs_all_missing_dimensions(self):
        c, s, refs = example(complete=False, tail=False)
        r = close(c, refs, "measurement")
        r["resolved_dimensions"] = ["M1"]
        c["closures"] = [r]
        self.assertEqual(check(c, s)["route"], "B")
        r["resolved_dimensions"] = ["M1", "M2", "M3", "M4"]
        self.assertEqual(check(c, s)["route"], "D")

    def test_history_cannot_be_waived(self):
        c, s, refs = example(complete=False)
        c["statistics"] = summarize([1.] * 100)
        c["closures"] = [close(c, refs, "measurement"), close(c, refs, "history")]
        self.assertEqual(check(c, s)["route"], "A")

    def test_expiry_wrong_role_hash_future_and_stale(self):
        for key, value in (("expires_at", "2026-01-02T01:30:00Z"), ("reviewer_role", "anyone"),
                           ("reviewed_at", "2026-01-02T03:00:00Z"), ("scope_hash", "bad")):
            c, s, refs = example()
            r = close(c, refs); r[key] = value; c["closures"] = [r]
            self.assertEqual(check(c, s)["route"], "C", key)
        c, s, refs = example(); c["closures"] = [close(c, refs)]
        self.assertEqual(check(c, {"fixture://contract": b"tampered"})["route"], "B")

    def test_carry_is_immutable_and_no_duplicate_task(self):
        parent, s, refs = example(); saved = copy.deepcopy(parent)
        updated = copy.deepcopy(parent); updated["closures"] = [close(parent, refs)] * 2
        child = derive_child(parent, updated, mode="carry")
        self.assertEqual(parent, saved)
        result = check(child, s)
        self.assertEqual(result["route"], "D")
        self.assertEqual(len(result["accepted_closure_ids"]), 1)
        self.assertEqual(len(result["rejected_closures"]), 1)
        updated["candidate_value"] += 1
        with self.assertRaises(ValueError): derive_child(parent, updated, mode="carry")
        regen = derive_child(parent, updated, mode="regen")
        self.assertEqual(check(regen, s)["route"], "C")

    def test_changed_window_definition_or_policy_invalidates_closure(self):
        for key, value in (("measurement_definition", "other"), ("observations_sha256", "c"*64)):
            c, s, refs = example(); c["closures"] = [close(c, refs)]; c[key] = value
            self.assertEqual(check(c, s)["route"], "C")
        c, s, refs = example(); c["closures"] = [close(c, refs)]
        self.assertEqual(check(c, s, policy=Policy(tail_cutoff=9))["route"], "C")
        c["window_start"] = "2025-12-31T23:00:00Z"; c["window_end"] = "2026-01-01T23:00:00Z"
        c["provenance"]["evidence_cutoff"] = c["window_end"]
        c["generator"]["fit_end"] = c["generator"]["input_end"] = "2026-01-01T22:59:00Z"
        self.assertEqual(check(c, s)["route"], "C")

    def test_incomplete_sources_and_future_generator_are_rejected(self):
        for mutate in (lambda c:c["provenance"].pop("source_sha256"),
                       lambda c:c.update(unit="seconds"),
                       lambda c:c["generator"].update(fit_end=c["window_end"]),
                       lambda c:c.update(candidate_value=float("nan"))):
            c, s, _ = example(); mutate(c)
            with self.assertRaises(ValueError): check(c, s)

    def test_alternate_generator_uses_same_h_and_same_diagnostics(self):
        c, s, _ = example(); original = check(c, s)
        c["generator"].update(id="quantile_regressor", kind="learned")
        c["candidate_value"] = 22.
        alternative = check(c, s)
        self.assertEqual(original["route"], alternative["route"])
        self.assertEqual(original["tail_ratio"], alternative["tail_ratio"])
        self.assertNotEqual(original["scope_hash"], alternative["scope_hash"])


if __name__ == "__main__":
    unittest.main()
