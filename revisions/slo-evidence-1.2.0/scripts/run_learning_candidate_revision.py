"""Frozen simple learned G vs statistical G: interface experiment, not a user study."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import timedelta
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import QuantileRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_pinball_loss
from slo_evidence import summarize, statistical_value, decide, file_hash, canonical_hash
from slo_evidence.experiment_io import make_card, evidence_store, utc, LOCAL_DEFINITION


def time_features(times):
    minute = times.hour.to_numpy()*60+times.minute.to_numpy()
    angle = 2*np.pi*minute/1440
    return np.column_stack([np.sin(angle),np.cos(angle)])


def fit_candidate(values, times, start, end):
    split = start + (end-start)*.7
    train = times < split; test = ~train
    if train.sum() < 60 or test.sum() < 20:
        return None,{"status":"insufficient_training_or_holdout","n_train":int(train.sum()),"n_holdout":int(test.sum()),"split":split.isoformat()}
    assert times.max() < end and times[train].max() < times[test].min()
    x = time_features(times)
    scale = max(float(np.quantile(values[train],.95)),float(np.std(values[train])),1e-6)
    model = make_pipeline(StandardScaler(),QuantileRegressor(quantile=.95,alpha=.01,solver="highs"))
    model.fit(x[train],values[train]/scale)
    raw = model.predict(x[test])*scale
    return (raw, values[test]),{"status":"fitted","n_train":int(train.sum()),"n_holdout":int(test.sum()),
        "split":split.isoformat(),"train_end":times[train].max().isoformat(),"holdout_start":times[test].min().isoformat(),
        "input_end":times.max().isoformat(),"target_scale_train_only":scale,
        "coefficient":model[-1].coef_.tolist(),"intercept":float(model[-1].intercept_),
        "feature_mean":model[0].mean_.tolist(),"feature_scale":model[0].scale_.tolist()}


def main():
    p=argparse.ArgumentParser();p.add_argument("--rebuild",required=True);p.add_argument("--output",required=True)
    a=p.parse_args(); src=Path(a.rebuild);out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    cards=json.loads((src/"learning_selection.json").read_text(encoding="utf-8"))
    windows=pd.read_csv(src/"learning_windows.csv.gz");windows["timestamp"]=pd.to_datetime(windows.timestamp,utc=True)
    sourcehash=file_hash(src/"learning_windows.csv.gz"); store=evidence_store()
    rows=[];objects=[];models=[];started=time.perf_counter()
    for frozen in cards:
        frame=windows[windows.card_id==frozen["card_id"]].sort_values("timestamp")
        times=pd.DatetimeIndex(frame.timestamp);values=frame.value.to_numpy(float)
        start,end=pd.Timestamp(utc(frozen["history_start"])),pd.Timestamp(utc(frozen["history_end"]))
        stats=summarize(values); observationhash=canonical_hash({"t":times.astype(str).tolist(),"x":values.tolist()})
        base=make_card(frozen,stats,source_id="learning_windows.csv.gz",source_hash=sourcehash,
                       query_version="legacy-minute-with-offline-exclusions/1",observation_hash=observationhash)
        statistical_decision=decide(base,at=end.isoformat(),evidence_store=store)
        objects.append({"source":"statistical","card":base,"decision":statistical_decision})
        fitted,info=fit_candidate(values,times,start,end)
        row={"card_id":frozen["card_id"],"subject_id":frozen["subject_id"],"sli":frozen["sli"],
             "old_route":frozen["evidence_status"],"statistical_value":base["candidate_value"],
             "statistical_route":statistical_decision["route"],"learned_route":None,"status":info["status"],
             "valid_samples":len(values),"n_train":info["n_train"],"n_holdout":info["n_holdout"]}
        models.append({"card_id":frozen["card_id"],**info})
        if fitted is not None:
            raw,ytest=fitted; upper=1 if frozen["sli"]=="error_ratio" else np.inf
            predictions=np.clip(raw,0,upper);value=float(np.quantile(predictions,.95))
            generator={"id":"linear-quantile-time","version":"1","kind":"learned",
                       "fit_end":info["train_end"],"input_end":info["input_end"],
                       "model":"StandardScaler+QuantileRegressor","quantile":.95,"alpha":.01,
                       "candidate_aggregation":"Q95 of conditional-Q95 predictions on historical holdout times",
                       "parameters_sha256":canonical_hash(info),"sklearn":sklearn.__version__}
            learned=make_card(frozen,stats,source_id="learning_windows.csv.gz",source_hash=sourcehash,
                              query_version="legacy-minute-with-offline-exclusions/1",generator=generator,value=value,
                              observation_hash=observationhash)
            decision=decide(learned,at=end.isoformat(),evidence_store=store)
            objects.append({"source":"learned","card":learned,"decision":decision})
            row.update({"learned_value":value,"learned_route":decision["route"],
                        "absolute_difference":abs(value-base["candidate_value"]),
                        "different_value":not np.isclose(value,base["candidate_value"],rtol=1e-9,atol=1e-12),
                        "route_changed":decision["route"]!=statistical_decision["route"],
                        "missing_measurement":",".join(decision["missing_measurement_dimensions"]),
                        "context_fact":decision["triggered_facts"]["context"],
                        "train_end":info["train_end"],"input_end":info["input_end"],"audit_time":end.isoformat(),
                        "clipped_predictions":int(np.count_nonzero(raw!=predictions)),
                        "holdout_pinball_loss":mean_pinball_loss(ytest,predictions,alpha=.95)})
        rows.append(row)
    frame=pd.DataFrame(rows);frame.to_csv(out/"paired_candidates.csv",index=False)
    (out/"candidate_records.json").write_text(json.dumps(objects,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    (out/"model_parameters.json").write_text(json.dumps(models,indent=2,ensure_ascii=False),encoding="utf-8")
    (out/"local_measurement_definition.txt").write_bytes(LOCAL_DEFINITION)
    ok=frame[frame.status=="fitted"]
    summary={"protocol":"learned-G-interface/20260929","selected_cards":len(cards),"fitted":len(ok),
             "failures":dict(Counter(frame.loc[frame.status!="fitted","status"])),
             "statistical_route_counts":dict(Counter(frame.statistical_route)),
             "learned_route_counts":dict(Counter(ok.learned_route)),
             "different_values":int(ok.different_value.sum()),"route_changes":int(ok.route_changed.sum()),
             "measurement_unknown_M4":len(ok),"context_facts_retained":int(ok.context_fact.sum()),
             "context_facts_under_B":int((ok.context_fact & (ok.learned_route=="B")).sum()),
             "no_training_or_input_at_or_after_audit":all(pd.Timestamp(r.train_end)<pd.Timestamp(r.audit_time) and pd.Timestamp(r.input_end)<pd.Timestamp(r.audit_time) for r in ok.itertuples()),
             "source_sha256":sourcehash,"selection_sha256":file_hash(src/"learning_selection.json"),
             "sklearn_version":sklearn.__version__,"elapsed_seconds":time.perf_counter()-started,
             "per_sli":{s:{"n":len(g),"median_absolute_difference":float(g.absolute_difference.median()),
                           "median_holdout_pinball_loss":float(g.holdout_pinball_loss.median()),"clipped_predictions":int(g.clipped_predictions.sum())}
                        for s,g in ok.groupby("sli")},
             "limitations":["Purposefully stratified interface cases, not a population estimate or human evaluation.",
                            "M4 remains unknown for real cards. Identical A/B routes are expected from shared evidence, not proof of efficacy.",
                            "Conditional historical quantile candidate differs in construction from empirical robust bound; no superiority claim.",
                            "Fault interval cleaning uses offline known labels; timestamps are interpreted as UTC per source case mapping."]}
    (out/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(summary,indent=2,ensure_ascii=False))


if __name__=="__main__":main()
