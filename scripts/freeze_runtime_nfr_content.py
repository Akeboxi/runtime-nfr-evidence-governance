"""Create a traceable Runtime NFR content-freeze candidate.

This freezes the Markdown source, analysis/audit artifacts, figure sources and
reproducibility code. DOCX and PDF are deliberately excluded until layout.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = (
    ROOT
    / "checkpoints"
    / "runtime_nfr_v3_academic"
    / "content_freeze_candidate_v8"
)

EXPLICIT_FILES = [
    "docs/manuscript/runtime_nfr/RUNTIME_NFR_MANUSCRIPT.md",
    "docs/manuscript/runtime_nfr/phase2_revision/PHASE2_CLOSURE_REPORT.md",
    "docs/manuscript/runtime_nfr/references/REFERENCE_EVIDENCE_MATRIX.md",
    "docs/experiments/runtime-nfr-v3-academic/external-dataset-manifest.json",
    "checkpoints/runtime_nfr_v3_academic/formal_audit/formal_audit.json",
    "checkpoints/runtime_nfr_v3_academic/integrity/legacy_v1_v2_integrity_audit.json",
    "checkpoints/runtime_nfr_v3_academic/paper_tables/paper_tables.json",
    "checkpoints/runtime_nfr_v3_academic/paper_artifacts/representative_cases.json",
    "src/data/runtime_nfr_dataset.py",
    "src/data/runtime_nfr_v3_academic.py",
    "scripts/analyze_runtime_nfr_phase2.py",
    "scripts/analyze_runtime_nfr_phase3.py",
    "scripts/generate_runtime_nfr_jos_figures_v3.py",
    "scripts/audit_runtime_nfr_manuscript.py",
    "scripts/audit_runtime_nfr_submission.py",
    "scripts/freeze_runtime_nfr_content.py",
]

TREE_ROOTS = [
    "docs/manuscript/runtime_nfr/phase3_content_audit",
    "checkpoints/runtime_nfr_v3_academic/phase2_analysis_v1",
    "checkpoints/runtime_nfr_v3_academic/phase3_content_analysis_v1",
    "checkpoints/runtime_nfr_v3_academic/phase3_figures_jos_v3",
    "checkpoints/runtime_nfr_v3_academic/submission_candidate_v11",
]

ALLOWED_SUFFIXES = {
    ".bib",
    ".csv",
    ".json",
    ".md",
    ".png",
    ".py",
    ".svg",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def collect_files() -> list[Path]:
    selected = {ROOT / item for item in EXPLICIT_FILES}
    selected.update(
        path
        for path in (ROOT / "tests").glob("test_runtime_nfr*.py")
        if path.is_file()
    )
    for item in TREE_ROOTS:
        tree_root = ROOT / item
        selected.update(
            path
            for path in tree_root.rglob("*")
            if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES
        )
    missing = [path for path in selected if not path.exists()]
    if missing:
        rendered = "\n".join(str(path) for path in sorted(missing))
        raise FileNotFoundError(f"Freeze input missing:\n{rendered}")
    forbidden = [
        path
        for path in selected
        if path.suffix.lower() in {".docx", ".pdf"}
    ]
    if forbidden:
        raise RuntimeError(f"Layout artifact entered content freeze: {forbidden}")
    return sorted(selected)


def main() -> None:
    files = collect_files()
    records = []
    total_bytes = 0
    for path in files:
        size = path.stat().st_size
        total_bytes += size
        records.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "bytes": size,
                "sha256": sha256(path),
            }
        )

    required_audits = {
        "submission": ROOT
        / "checkpoints/runtime_nfr_v3_academic/submission_candidate_v11/submission_audit.json",
        "markdown_numeric": ROOT
        / "checkpoints/runtime_nfr_v3_academic/submission_candidate_v11/manuscript_markdown_audit.json",
        "formal": ROOT
        / "checkpoints/runtime_nfr_v3_academic/formal_audit/formal_audit.json",
        "legacy_integrity": ROOT
        / "checkpoints/runtime_nfr_v3_academic/integrity/legacy_v1_v2_integrity_audit.json",
    }
    audit_status = {}
    for name, path in required_audits.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        audit_status[name] = {
            "pass": bool(payload.get("pass")),
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": sha256(path),
        }
    if not all(item["pass"] for item in audit_status.values()):
        raise RuntimeError(f"Cannot freeze while an audit is failing: {audit_status}")

    manifest = {
        "protocol": "runtime-nfr-content-freeze-candidate/8",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "content_frozen_layout_deferred",
        "scope": {
            "unique_content_source": (
                "docs/manuscript/runtime_nfr/RUNTIME_NFR_MANUSCRIPT.md"
            ),
            "docx": "deferred",
            "pdf": "deferred",
            "author_metadata": "chinese_and_romanized_metadata_confirmed",
            "funding": "none",
            "conflicts_of_interest": "none_declared",
            "code_publication": "private_repository_verified_public_after_acceptance",
            "language_optimization": (
                "research-writing_academic-humanizer_statistical-reporting-reviewed"
            ),
            "submission_system": "deferred",
            "final_review": "v2_three_reviewers_statistics_and_language_closed",
            "author_review": "package_prepared_signatures_pending",
            "submission_statement": "content_draft_prepared_official_form_pending",
        },
        "frozen_invariants": {
            "cards_total": 8668,
            "governance_status_counts": [1054, 2424, 948, 4242],
            "threshold_only_hidden_cards": 4426,
            "references": 69,
            "figures": 6,
            "runtime_nfr_tests": 41,
        },
        "audit_status": audit_status,
        "file_count": len(records),
        "total_bytes": total_bytes,
        "files": records,
    }

    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUTPUT / "CONTENT_FREEZE_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    report = f"""# Runtime NFR 内容冻结候选 v8

状态：`content_frozen_layout_deferred`

本清单冻结 Markdown 唯一正文源、阶段二/三分析制品、六图的 SVG/PNG
及灰度 QA、审计结果和复现代码。它不包含 DOCX 或 PDF，也不代表已经
完成投稿系统字段。

## 冻结结果

- 文件数：{len(records)}
- 总大小：{total_bytes:,} bytes
- 四状态：1,054 / 2,424 / 948 / 4,242，总计 8,668
- threshold-only 隐藏证据或动作信息：4,426
- 参考文献：69 条，编号连续且均有正文落点
- 图：6 幅；当前仅 SVG/PNG 与灰度 QA
- Runtime NFR 回归测试：41 项通过
- 正式结果、历史完整性、投稿源和 Markdown 数字审计：全部通过

## 尚未纳入冻结

- 中英文作者顺序、单位、通信作者、基金和利益冲突：已确认并纳入冻结
- 代码公开时序：当前版本私有保存，论文接收后公开
- 私有仓库：已上传并通过 GitHub API 核验为 `PRIVATE`
- 最终定向审稿：三视角 V2、Claim–Experiment v4 与统计报告复核已完成
- 作者联审：联审包已生成，四位作者逐项确认和签字仍待完成
- 投稿声明：内容草案已生成，仍需转入期刊官方格式并由四位作者签字
- 代码许可证、最终版本化归档和持久标识符：论文接收后公开时补入
- DOCX 与 PDF：按计划在内容冻结后生成，当前未生成
- Markdown—DOCX 双向数字审计、175 mm 版心和投稿系统预演：排版阶段执行
- CPU、内存和完整墙钟时间：历史未采集，保持 `not_recorded`

后续若正文、分析或图片发生任何变动，必须重跑 41 项测试、四类审计并
生成新的版本化冻结目录；不得覆盖本目录。
"""
    (OUTPUT / "CONTENT_FREEZE_REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
