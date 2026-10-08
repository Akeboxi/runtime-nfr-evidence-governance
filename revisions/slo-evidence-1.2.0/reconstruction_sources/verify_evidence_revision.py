"""Verify deployed current functions against all corrected reconstruction rows."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from slo_evidence import decide, file_hash
from slo_evidence.experiment_io import make_card, evidence_store, utc
from src.data.runtime_nfr_v3_academic import academic_governance_decision
from scripts.analyze_runtime_nfr_robustness_v1 import route_rows


def main():
    p=argparse.ArgumentParser();p.add_argument("--project",required=True);p.add_argument("--rebuild",required=True);p.add_argument("--output",required=True)
    a=p.parse_args();root=Path(a.project);src=Path(a.rebuild);out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    cards=json.loads((root/"experiments/analysis_outputs/governance_cards/academic_governance_cards.json").read_text(encoding="utf-8"))
    statistics=pd.read_csv(src/"reconstructed_statistics_corrected.csv.gz")
    baseline=statistics[(statistics.window_hours==24)&(statistics["quantile"]==.95)].set_index("card_id")
    fields=["valid_samples","quantile_level","quantile_value","median","mad","robust_scale","nonzero_count","max_value","all_zero"]
    rows=[];legacy_mismatch=[];sourcehash=file_hash(src/"reconstructed_statistics_corrected.csv.gz")
    for c in cards:
        s={k:baseline.loc[c["card_id"],k] for k in fields}
        s={k:(v.item() if hasattr(v,"item") else v) for k,v in s.items()}
        s["max_value"]=None if pd.isna(s["max_value"]) else s["max_value"]
        old_input={**c,**s,"threshold":c["raw_threshold"]}
        old_result=academic_governance_decision(old_input)
        if old_result.evidence_status!=c["evidence_status"]:legacy_mismatch.append(c["card_id"])
        current=make_card(c,s,source_id="reconstructed_statistics_corrected.csv.gz",source_hash=sourcehash,
                          query_version="legacy-offline-transform-preserved/1")
        result=decide(current,at=utc(c["history_end"]),evidence_store=evidence_store())
        rows.append({"card_id":c["card_id"],"old_v1_route":c["evidence_status"],"current_v12_route":result["route"],
                     "open_tasks":"|".join(result["open_tasks"]),"missing_dimensions":"|".join(result["missing_measurement_dimensions"]),
                     "tail_fact":result["triggered_facts"]["context"],"scope_hash":result["scope_hash"]})
    grid=pd.read_csv(src/"robustness_grid_corrected.csv"); mismatches=[]
    names=["insufficient_evidence","threshold_unresolved","needs_context_review","candidate_for_stakeholder_review"]
    for r in grid.itertuples():
        subset=statistics[(statistics.window_hours==r.window_hours)&(statistics["quantile"]==r.quantile)]
        counts=Counter(route_rows(subset,r.coverage_min,r.tail_ratio,r.robust_k))
        if any(counts[name]!=getattr(r,name) for name in names):mismatches.append(r.config_id)
    pd.DataFrame(rows).to_csv(out/"v12_existing_pool_routes.csv",index=False)
    summary={"legacy_current_function_route_mismatches":legacy_mismatch,"production_grid_mismatches":mismatches,
             "current_v12_counts":dict(Counter(r["current_v12_route"] for r in rows)),
             "M4_unknown":sum("M4" in r["missing_dimensions"] for r in rows),
             "context_facts_retained":sum(r["tail_fact"] for r in rows),
             "scope":"Existing event-anchored candidate pool; not a new natural-flow or expert experiment."}
    (out/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(summary,indent=2,ensure_ascii=False))
    assert not legacy_mismatch and not mismatches


if __name__=="__main__":main()
