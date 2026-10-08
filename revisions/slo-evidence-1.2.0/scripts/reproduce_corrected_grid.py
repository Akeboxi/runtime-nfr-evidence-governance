"""Portable check from archived sufficient statistics; no raw telemetry or DGL."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from slo_evidence import Policy, legacy_route
from slo_evidence.core import STATUS_NAMES


def main():
    p=argparse.ArgumentParser();p.add_argument("--rebuild",required=True);p.add_argument("--output",required=True)
    a=p.parse_args();src=Path(a.rebuild);out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    stats=pd.read_csv(src/"reconstructed_statistics_corrected.csv.gz")
    grid=pd.read_csv(src/"robustness_grid_corrected.csv")
    cards=pd.read_csv(src/"card_errata.csv").set_index("card_id")
    failures=[]
    for r in grid.itertuples():
        policy=Policy(int(r.window_hours)*60,r.coverage_min,r.tail_ratio,r.quantile)
        subset=stats[(stats.window_hours==r.window_hours)&(stats["quantile"]==r.quantile)]
        results=[legacy_route(x,x["sli"],policy,r.robust_k) for x in subset.to_dict("records")]
        names=[STATUS_NAMES[x["route"]] for x in results];counts=Counter(names)
        migrated=sum(name!=cards.loc[cid,"old_status"] for cid,name in zip(subset.card_id,names))
        if (r.valid_samples_min!=policy.minimum_samples or migrated!=r.migration_count or
                any(counts[name]!=getattr(r,name) for name in STATUS_NAMES.values())):
            failures.append(r.config_id)
    summary={"configurations_checked":len(grid),"failures":failures,
             "scope":"Recomputed from published reconstructed statistics, not from upstream raw telemetry."}
    (out/"grid_check.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2));assert not failures


if __name__=="__main__":main()
