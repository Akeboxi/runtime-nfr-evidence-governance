#!/usr/bin/env python3
"""Validate sample isolation, balancing, order integrity, and expert blinding."""

from __future__ import annotations

import csv
import json
import zipfile
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {
    "stratum", "M1", "M2", "M3", "M4", "measurement_explainable", "v1_route",
    "v1_1_route", "source_academic_card_id", "source_parent_card_id", "source_event_id",
    "source_evidence_status", "source_governance_flags", "answer", "protocol_route",
}


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    key = rows(ROOT / "researcher_only/private_answer_key.csv")
    orders = rows(ROOT / "researcher_only/randomization_orders.csv")
    checks = {
        "exactly_24_cards": len(key) == 24 and len({x["card_id"] for x in key}) == 24,
        "exact_strata": Counter(x["stratum"] for x in key) == Counter({"B_stable": 6, "B_to_D": 6, "A_stable": 4, "C_stable": 4, "D_stable": 4}),
        "v1_routes_expected": Counter(x["v1_route"] for x in key) == Counter({"B": 12, "A": 4, "C": 4, "D": 4}),
        "v1_1_routes_expected": Counter(x["v1_1_route"] for x in key) == Counter({"D": 10, "B": 6, "A": 4, "C": 4}),
        "unique_events": len({x["source_event_id"] for x in key}) == 24,
        "service_cap_three": max(Counter(x["service"] for x in key).values()) <= 3,
        "three_orders_of_24": len(orders) == 72 and all(len([x for x in orders if x["evaluator_id"] == eid]) == 24 for eid in ("V11-E01", "V11-E02", "V11-E03")),
        "orders_same_card_set": all({x["card_id"] for x in orders if x["evaluator_id"] == eid} == {x["card_id"] for x in key} for eid in ("V11-E01", "V11-E02", "V11-E03")),
        "orders_are_distinct": len({tuple(x["card_id"] for x in sorted((r for r in orders if r["evaluator_id"] == eid), key=lambda z: int(z["presented_order"]))) for eid in ("V11-E01", "V11-E02", "V11-E03")}) == 3,
        "all_source_resolution_unestablished": all(x["source_measurement_resolution"] == "not_independently_established" for x in key),
        "scenario_status_explicit": all(x["scenario_evidence_status"] == "controlled_measurement_scenario" for x in key),
        "measurement_predicate_consistent": all(int(x["measurement_explainable"]) == int(all(int(x[m]) for m in ("M1", "M2", "M3", "M4"))) for x in key),
    }
    visible = json.loads((ROOT / "data/evaluation_cards.json").read_text(encoding="utf-8"))
    checks["expert_json_has_no_private_fields"] = all(not (set(x) & FORBIDDEN) for x in visible)
    checks["scenario_label_visible_where_added"] = all("情景设定" in x["M4_special_value"] for x, k in zip(visible, key) if k["stratum"] != "B_stable")
    for eid in ("V11-E01", "V11-E02", "V11-E03"):
        folder = ROOT / "expert_send" / eid
        expected = {
            f"01_{eid}_任务说明.docx", f"02_{eid}_独立性与完成声明.docx",
            f"03_{eid}_评价问卷.xlsx", f"04_{eid}_返回文件清单.txt",
        }
        checks[f"{eid}_four_files"] = {p.name for p in folder.iterdir() if p.is_file()} == expected
        zip_path = ROOT / "expert_send" / f"{eid}_专家评价包.zip"
        checks[f"{eid}_zip_exists"] = zip_path.exists()
        if zip_path.exists():
            with zipfile.ZipFile(zip_path) as z:
                checks[f"{eid}_zip_exact_files"] = {Path(n).name for n in z.namelist()} == expected
                private_tokens = (
                    b"B_to_D", b"B_stable", b"v1_route", b"v1_1_route",
                    b"source_parent_card_id", b"source_event_id", b"measurement_explainable",
                )
                leaked = False
                for name in z.namelist():
                    payload = z.read(name)
                    if any(token in payload for token in private_tokens):
                        leaked = True
                    if name.endswith((".docx", ".xlsx")):
                        with zipfile.ZipFile(Path(z.extract(name, ROOT / "qa/_zip_scan"))) as office:
                            office_payload = b"\n".join(office.read(n) for n in office.namelist())
                            if any(token in office_payload for token in private_tokens):
                                leaked = True
                checks[f"{eid}_zip_no_private_tokens"] = not leaked
        xlsx_path = folder / f"03_{eid}_评价问卷.xlsx"
        if xlsx_path.exists():
            with zipfile.ZipFile(xlsx_path) as office:
                workbook_xml = b"\n".join(office.read(n) for n in office.namelist() if n.endswith(".xml"))
            checks[f"{eid}_xlsx_has_choice_validation"] = b'"A,B,C,D"' in workbook_xml or b"A,B,C,D" in workbook_xml
            checks[f"{eid}_xlsx_has_confidence_validation"] = (
                b'operator="between"' in workbook_xml
                and (b"<formula1>1</formula1>" in workbook_xml or b"<x:formula1>1</x:formula1>" in workbook_xml)
                and (b"<formula2>5</formula2>" in workbook_xml or b"<x:formula2>5</x:formula2>" in workbook_xml)
            )
    result = {"protocol": "runtime-nfr-bd-boundary-validation-audit/1", "checks": checks, "passed": sum(checks.values()), "total": len(checks), "pass": all(checks.values())}
    out = ROOT / "qa/package_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
