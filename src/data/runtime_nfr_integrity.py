"""Integrity snapshots that keep Runtime NFR v1/v2 evidence immutable."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
from typing import Any, Sequence


INTEGRITY_PROTOCOL = "runtime-nfr-v3-legacy-integrity/1"


def _file_row(path: Path, workspace: Path) -> dict[str, Any]:
    return {
        "path": path.resolve().relative_to(workspace.resolve()).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def freeze_or_audit_legacy_integrity(
    roots: Sequence[str | Path],
    output_dir: str | Path,
    *,
    workspace: str | Path = ".",
) -> dict[str, Any]:
    workspace_path = Path(workspace).resolve()
    resolved_roots = [Path(root).resolve() for root in roots if Path(root).exists()]
    rows = sorted(
        (
            _file_row(path, workspace_path)
            for root in resolved_roots
            for path in root.rglob("*")
            if path.is_file()
        ),
        key=lambda row: row["path"],
    )
    current = {
        "protocol": INTEGRITY_PROTOCOL,
        "workspace": str(workspace_path),
        "roots": [
            root.relative_to(workspace_path).as_posix() for root in resolved_roots
        ],
        "files": rows,
        "file_count": len(rows),
        "tree_sha256": sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    baseline_path = output / "legacy_v1_v2_integrity_baseline.json"
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline_by_path = {row["path"]: row for row in baseline["files"]}
        current_by_path = {row["path"]: row for row in rows}
        added = sorted(set(current_by_path) - set(baseline_by_path))
        removed = sorted(set(baseline_by_path) - set(current_by_path))
        changed = sorted(
            path
            for path in set(current_by_path) & set(baseline_by_path)
            if current_by_path[path]["sha256"] != baseline_by_path[path]["sha256"]
            or current_by_path[path]["bytes"] != baseline_by_path[path]["bytes"]
        )
        frozen_now = False
    else:
        baseline_path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        added: list[str] = []
        removed: list[str] = []
        changed: list[str] = []
        frozen_now = True
    audit = {
        "protocol": INTEGRITY_PROTOCOL,
        "baseline": str(baseline_path.resolve()),
        "baseline_created_now": frozen_now,
        "current_file_count": len(rows),
        "current_tree_sha256": current["tree_sha256"],
        "added": added,
        "removed": removed,
        "changed": changed,
        "pass": not added and not removed and not changed,
    }
    audit_path = output / "legacy_v1_v2_integrity_audit.json"
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return audit
