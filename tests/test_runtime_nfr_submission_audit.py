from pathlib import Path

from scripts.audit_runtime_nfr_submission import audit, expand_citation_group


def test_expand_citation_group() -> None:
    assert expand_citation_group("1,3-5,7—8") == {1, 3, 4, 5, 7, 8}


def test_current_submission_candidate_when_present() -> None:
    manuscript = Path("docs/manuscript/runtime_nfr/RUNTIME_NFR_MANUSCRIPT.md")
    closure = Path("docs/manuscript/runtime_nfr/phase2_revision/PHASE2_CLOSURE_REPORT.md")
    phase2 = Path("checkpoints/runtime_nfr_v3_academic/phase2_analysis_v1")
    figures = Path(
        "checkpoints/runtime_nfr_v3_academic/"
        "phase3_figures_jos_v3/FIGURE_MANIFEST.json"
    )
    if not all(
        path.exists()
        for path in (
            manuscript,
            closure,
            phase2 / "ANALYSIS_MANIFEST.json",
            figures,
        )
    ):
        return
    result = audit(manuscript, closure, phase2, figures)
    assert result["pass"] is True
    assert result["checks"]["candidate_formula_matches_frozen_code"] is True
    assert result["checks"]["governance_gates_match_frozen_code"] is True
    assert result["checks"]["stale_tail_formula_is_absent"] is True
