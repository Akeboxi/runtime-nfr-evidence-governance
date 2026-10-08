"""Partial transform knowledge must not certify upstream measurement semantics."""
import unittest
from slo_evidence.experiment_io import make_card, evidence_store, ASSESSMENT_VERSION
from slo_evidence import summarize, decide


class MeasurementAssessmentAudit(unittest.TestCase):
    def test_local_transform_does_not_verify_source_definition_or_sampling(self):
        frozen = {"card_id":"fixture", "subject_id":"service", "sli":"latency", "unit":"ms",
                  "history_start":"2026-01-01T00:00:00Z", "history_end":"2026-01-02T00:00:00Z",
                  "evidence_provenance":{"dataset_hash":"a"*64}}
        card = make_card(frozen, summarize([10.] * 1440), source_id="fixture", source_hash="a"*64,
                         query_version="fixture/1")
        result = decide(card, at=card["window_end"], evidence_store=evidence_store())
        self.assertEqual(card["measurement_assessment_version"], ASSESSMENT_VERSION)
        self.assertEqual(result["missing_measurement_dimensions"], ["M1","M2","M4"])
        self.assertEqual(result["route"], "B")
        self.assertIn("not upstream", card["measurement_evidence"]["M3"]["scope"])


if __name__ == "__main__": unittest.main()
