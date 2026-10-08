"""Rebuild actual nonzero facts, numeric-rule errata and alternate-G inputs.

Run with the original project's scientific environment. The graph-aware legacy
loader is used ONLY to reproduce the original service-specific fault exclusions.
All new analysis/decision code lives in the dependency-light slo_evidence package.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime
import itertools
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from slo_evidence import Policy, summarize, statistical_value, legacy_route, file_hash
from slo_evidence.core import STATUS_NAMES
from src.data.runtime_nfr_dataset import _read_segment_layout, _collect_event_intervals, _exclude_overlaps, _metric_dir, load_app_sli_frame


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True); p.add_argument("--segment-root", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args(); project = Path(args.project); out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    cards_path = project/"experiments/analysis_outputs/governance_cards/academic_governance_cards.json"
    cards = json.loads(cards_path.read_text(encoding="utf-8")); frozen_hash = file_hash(cards_path)
    legacy_dir = project/"experiments/study_materials/protocol-robustness-v1"
    archived = pd.read_csv(legacy_dir/"reconstructed_statistics.csv.gz")
    oldgrid = pd.read_csv(legacy_dir/"robustness_grid.csv")
    selected = []
    groups = sorted({(c["evidence_status"], c["sli"]) for c in cards})
    for status, sli in groups:
        group = [c for c in cards if (c["evidence_status"], c["sli"]) == (status, sli)]
        selected.extend(sorted(group, key=lambda c: __import__("hashlib").sha256(("G-interface-20260929|"+c["card_id"]).encode()).hexdigest())[:12])
    selected_ids = {c["card_id"] for c in selected}
    (out/"learning_selection.json").write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    layouts = _read_segment_layout(Path(args.segment_root)); intervals = _collect_event_intervals(layouts)
    tenants = {str(meta.get("segment_id") or tenant.name.rsplit("_",1)[-1]):tenant for tenant,meta,_,_ in layouts}
    cache, source_rows, rows, learning_rows, changes = {}, [], [], [], []
    for idx, card in enumerate(cards):
        tenant = tenants[card["observation_context"]["segment_id"]]
        source = _metric_dir(tenant, tenant/"records"/card["evidence_provenance"]["record_id"])/"app"/(card["subject_id"]+".csv")
        if source not in cache:
            raw = pd.read_csv(source) if source.exists() else pd.DataFrame()
            frame = load_app_sli_frame(source) if source.exists() else pd.DataFrame(columns=["request","latency","error_ratio"])
            cache[source] = _exclude_overlaps(frame, app_id=card["subject_id"], intervals=intervals)
            source_rows.append({"source":str(source), "sha256":file_hash(source) if source.exists() else None,
                                "rows":len(raw), "error_missing":int(raw["error"].isna().sum()) if "error" in raw else len(raw),
                                "timeout_missing":int(raw["timeout"].isna().sum()) if "timeout" in raw else len(raw)})
        frame = cache[source]; end = pd.Timestamp(card["history_end"]).floor("min")
        for hours, q in itertools.product((12,18,24),(.90,.95,.99)):
            hist = frame.loc[(frame.index >= end-pd.Timedelta(hours=hours)) & (frame.index < end), card["sli"]]
            stats = summarize(hist.to_numpy(), q)
            row = {"card_id":card["card_id"], "event_id":card["evidence_provenance"]["event_id"], "subject_id":card["subject_id"],
                   "sli":card["sli"], "window_hours":hours, "quantile":q, "coverage":stats["valid_samples"]/(hours*60), **stats}
            rows.append(row)
            if hours == 24 and q == .95:
                d = legacy_route(stats, card["sli"])
                oldzero = "zero_error_baseline" in card["governance_flags"]
                changes.append({"card_id":card["card_id"],"sli":card["sli"],"old_status":card["evidence_status"],
                                "corrected_status":STATUS_NAMES[d["route"]],"old_value":card["raw_threshold"],"corrected_value":d["value"],
                                "old_zero_flag":oldzero,"true_all_zero":stats["all_zero"],"nonzero_count":stats["nonzero_count"],
                                "valid_samples":stats["valid_samples"],"floor_flag":"threshold_from_robust_scale_floor" in d["flags"]})
                if card["card_id"] in selected_ids:
                    finite = hist[np.isfinite(hist.to_numpy())]
                    learning_rows.extend({"card_id":card["card_id"],"timestamp":t.isoformat()+"Z","value":float(v)} for t,v in finite.items())
        if (idx+1)%1000 == 0: print(f"Reconstructed {idx+1}/{len(cards)}", flush=True)
    stats = pd.DataFrame(rows)
    stats.to_csv(out/"reconstructed_statistics_corrected.csv.gz", index=False, compression="gzip")
    pd.DataFrame(learning_rows).to_csv(out/"learning_windows.csv.gz", index=False, compression="gzip")
    delta = pd.DataFrame(changes); delta.to_csv(out/"card_errata.csv", index=False)
    pd.DataFrame(source_rows).to_csv(out/"source_manifest.csv", index=False)
    keys = ["card_id","window_hours","quantile"]
    merged = stats.merge(archived, on=keys, suffixes=("_new","_old"), validate="one_to_one")
    comparisons = {}
    for name in ("valid_samples","coverage","quantile_value","median","mad","robust_scale"):
        a,b = merged[name+"_new"].to_numpy(),merged[name+"_old"].to_numpy()
        comparisons[name] = {"matches":int(np.isclose(a,b,rtol=1e-12,atol=1e-15).sum()),"max_abs_difference":float(np.max(np.abs(a-b)))}
    configs = []
    oldstatuses = {c["card_id"]:c["evidence_status"] for c in cards}
    baseline_d = {c["card_id"] for c in cards if c["evidence_status"] == STATUS_NAMES["D"]}
    for _, old in oldgrid.iterrows():
        subset = stats[(stats.window_hours==old.window_hours)&(stats["quantile"]==old["quantile"])]
        policy = Policy(int(old.window_hours)*60,float(old.coverage_min),float(old.tail_ratio),float(old["quantile"]))
        routed = [legacy_route(r, r["sli"], policy, float(old.robust_k)) for r in subset.to_dict("records")]
        names = [STATUS_NAMES[r["route"]] for r in routed]; counts = Counter(names)
        ids = subset.card_id.tolist(); moved = sum(name!=oldstatuses[cid] for cid,name in zip(ids,names))
        current_d = {cid for cid,name in zip(ids,names) if name==STATUS_NAMES["D"]}
        configs.append({**old.to_dict(), "valid_samples_min":policy.minimum_samples,
                        **{name:counts[name] for name in STATUS_NAMES.values()},
                        **{f"proportion_{name}":counts[name]/len(cards) for name in STATUS_NAMES.values()},
                        "migration_count":moved,"migration_rate":moved/len(cards),
                        "stakeholder_review_jaccard":len(current_d&baseline_d)/len(current_d|baseline_d),
                        "non_numeric_disposition_count":len(cards)-counts[STATUS_NAMES["D"]],
                        "non_numeric_disposition_rate":1-counts[STATUS_NAMES["D"]]/len(cards)})
    grid = pd.DataFrame(configs); grid.to_csv(out/"robustness_grid_corrected.csv",index=False)
    stat_cols = list(STATUS_NAMES.values())+["migration_count"]
    differences = grid.merge(oldgrid,on="config_id",suffixes=("_new","_old"))
    route_changed = np.zeros(len(grid),dtype=bool)
    for col in stat_cols: route_changed |= differences[col+"_new"].to_numpy()!=differences[col+"_old"].to_numpy()
    summary = {"protocol":"v1-corrected-numeric-audit/20260929", "cards":len(cards),"reconstructed_rows":len(stats),
               "archived_rows_matched":len(merged),"statistics_comparison":comparisons,
               "candidate_value_changes":int((~np.isclose(delta.old_value,delta.corrected_value,rtol=1e-12,atol=1e-15)).sum()),
               "route_changes":int((delta.old_status!=delta.corrected_status).sum()),
               "false_zero_flags_removed":int((delta.old_zero_flag & ~delta.true_all_zero).sum()),
               "true_zero_flags_added":int((~delta.old_zero_flag & delta.true_all_zero & (delta.sli=="error_ratio")).sum()),
               "corrected_error_all_zero":int((delta.true_all_zero & (delta.sli=="error_ratio")).sum()),
               "corrected_status_counts":dict(Counter(delta.corrected_status)),
               "configuration_count":len(grid),"configuration_routing_changes":int(route_changed.sum()),
               "parameter_metadata_rows_corrected":int((differences.valid_samples_min_new!=differences.valid_samples_min_old).sum()),
               "source_csv_count":len(source_rows),"source_missing_counts":{"error":sum(r["error_missing"] for r in source_rows),"timeout":sum(r["timeout_missing"] for r in source_rows)},
               "learning_selected":len(selected),"learning_selection_strata":dict(Counter(c["evidence_status"]+"/"+c["sli"] for c in selected)),
               "input_cards_unchanged":file_hash(cards_path)==frozen_hash,
               "limitations":["Legacy source conversion and service exclusions retained; no proof of source measurement precision.",
                              "These are corrected v1 numeric predicates, not v1.2 M1-M4 empirical outcomes."]}
    (out/"rebuild_summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(summary,indent=2,ensure_ascii=False))


if __name__ == "__main__": main()
