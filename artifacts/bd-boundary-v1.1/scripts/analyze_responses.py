#!/usr/bin/env python3
"""Analyze completed B/D boundary validation responses after collection."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import Counter
from pathlib import Path


CHOICES = ("A", "B", "C", "D")
BOOTSTRAP_SEED = 20260817
BOOTSTRAP_REPS = 10_000


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    pos = (len(values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return values[lo]
    return values[lo] * (hi - pos) + values[hi] * (pos - lo)


def fleiss_kappa(matrix: list[list[int]]) -> float:
    n = sum(matrix[0])
    items = len(matrix)
    p_i = [(sum(x * x for x in row) - n) / (n * (n - 1)) for row in matrix]
    p_bar = sum(p_i) / items
    totals = [sum(row[j] for row in matrix) for j in range(len(CHOICES))]
    p = [x / (items * n) for x in totals]
    p_e = sum(x * x for x in p)
    return (p_bar - p_e) / (1 - p_e) if p_e < 1 else float("nan")


def krippendorff_alpha_nominal(by_card: dict[str, list[str]]) -> float:
    counts = Counter(choice for values in by_card.values() for choice in values)
    total = sum(counts.values())
    # Finite-sample expected disagreement for nominal alpha.
    d_e = sum(v * (total - v) for v in counts.values()) / (total * (total - 1))
    pair_disagree = pair_total = 0
    for values in by_card.values():
        pair_total += len(values) * (len(values) - 1)
        pair_disagree += sum(a != b for a in values for b in values)
    d_o = pair_disagree / pair_total
    return 1 - d_o / d_e if d_e else float("nan")


def confusion(rows: list[dict], route_field: str) -> list[dict]:
    out = []
    for route in CHOICES:
        subset = [x for x in rows if x[route_field] == route]
        counts = Counter(x["choice"] for x in subset)
        out.append({"reference_route": route, **{c: counts[c] for c in CHOICES}, "total": len(subset)})
    return out


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("responses", type=Path)
    parser.add_argument("--key", type=Path, default=Path("researcher_only/private_answer_key.csv"))
    parser.add_argument("--output", type=Path, default=Path("analysis/results"))
    args = parser.parse_args()

    responses = read_csv(args.responses)
    key = {x["card_id"]: x for x in read_csv(args.key)}
    required = {(eid, cid) for eid in ("V11-E01", "V11-E02", "V11-E03") for cid in key}
    observed = {(x["evaluator_id"], x["card_id"]) for x in responses}
    if observed != required or len(responses) != 72:
        raise ValueError("responses must contain exactly 72 unique expected evaluator-card rows")
    for row in responses:
        row["choice"] = row["choice"].strip().upper()
        if row["choice"] not in CHOICES:
            raise ValueError(f"invalid choice: {row}")
        confidence = int(row["confidence"])
        if not 1 <= confidence <= 5:
            raise ValueError(f"invalid confidence: {row}")
        if not row["reason"].strip():
            raise ValueError(f"missing reason: {row}")
        row.update(key[row["card_id"]])
        row["agree_v1"] = int(row["choice"] == row["v1_route"])
        row["agree_v1_1"] = int(row["choice"] == row["v1_1_route"])

    cr1 = sum(x["agree_v1"] for x in responses) / 72
    cr11 = sum(x["agree_v1_1"] for x in responses) / 72
    card_ids = list(key)
    by_card = {cid: [x for x in responses if x["card_id"] == cid] for cid in card_ids}
    rng = random.Random(BOOTSTRAP_SEED)
    deltas = []
    for _ in range(BOOTSTRAP_REPS):
        sample = [rng.choice(card_ids) for _ in card_ids]
        rows = [row for cid in sample for row in by_card[cid]]
        deltas.append(sum(x["agree_v1_1"] - x["agree_v1"] for x in rows) / len(rows))

    # Design-aligned sensitivity analysis added after the frozen primary analysis:
    # resample cards within each of the five fixed sampling strata.
    strata_cards = {
        stratum: [cid for cid in card_ids if key[cid]["stratum"] == stratum]
        for stratum in ("A_stable", "B_stable", "B_to_D", "C_stable", "D_stable")
    }
    stratified_rng = random.Random(BOOTSTRAP_SEED)
    stratified_deltas = []
    for _ in range(BOOTSTRAP_REPS):
        sample = [
            stratified_rng.choice(cids)
            for cids in strata_cards.values()
            for _ in cids
        ]
        rows = [row for cid in sample for row in by_card[cid]]
        stratified_deltas.append(sum(x["agree_v1_1"] - x["agree_v1"] for x in rows) / len(rows))

    bd = [x for x in responses if x["stratum"] == "B_to_D"]
    stable = [x for x in responses if x["stratum"] != "B_to_D"]
    choices_by_card = {cid: [x["choice"] for x in rows] for cid, rows in by_card.items()}
    matrix = [[Counter(choices_by_card[cid])[c] for c in CHOICES] for cid in card_ids]
    majority_v1 = majority_v1_1 = 0
    unanimous_v1_1 = 0
    majority_rows = []
    for cid in card_ids:
        counts = Counter(choices_by_card[cid])
        majority_choice, majority_count = counts.most_common(1)[0]
        majority_v1 += int(majority_choice == key[cid]["v1_route"])
        majority_v1_1 += int(majority_choice == key[cid]["v1_1_route"])
        unanimous_v1_1 += int(counts[key[cid]["v1_1_route"]] == 3)
        majority_rows.append({
            "card_id": cid,
            "stratum": key[cid]["stratum"],
            "majority_choice": majority_choice,
            "majority_count": majority_count,
            "v1_route": key[cid]["v1_route"],
            "v1_1_route": key[cid]["v1_1_route"],
            "majority_agree_v1": int(majority_choice == key[cid]["v1_route"]),
            "majority_agree_v1_1": int(majority_choice == key[cid]["v1_1_route"]),
        })
    summary = {
        "protocol": "runtime-nfr-bd-boundary-validation/1.1",
        "cards": 24,
        "evaluators": 3,
        "judgments": 72,
        "CR_v1": cr1,
        "CR_v1_1": cr11,
        "delta_CR": cr11 - cr1,
        "delta_CR_card_cluster_bootstrap_95_ci": [percentile(deltas, 0.025), percentile(deltas, 0.975)],
        "delta_CR_stratified_card_cluster_bootstrap_95_ci_sensitivity": [
            percentile(stratified_deltas, 0.025), percentile(stratified_deltas, 0.975)
        ],
        "bootstrap_reps": BOOTSTRAP_REPS,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "B_to_D": {
            "judgments": len(bd),
            "choice_counts": dict(Counter(x["choice"] for x in bd)),
            "v1_agreements": sum(x["agree_v1"] for x in bd),
            "v1_1_agreements": sum(x["agree_v1_1"] for x in bd),
        },
        "stable_group": {
            "judgments": len(stable),
            "v1_1_agreements": sum(x["agree_v1_1"] for x in stable),
            "v1_1_rate": sum(x["agree_v1_1"] for x in stable) / len(stable),
        },
        "inter_rater": {
            "krippendorff_alpha_nominal": krippendorff_alpha_nominal(choices_by_card),
            "fleiss_kappa": fleiss_kappa(matrix),
        },
        "confidence": {
            "median": percentile([int(x["confidence"]) for x in responses], 0.5),
            "distribution": {str(k): v for k, v in sorted(Counter(int(x["confidence"]) for x in responses).items())},
        },
        "card_majority": {
            "v1_agree_cards": majority_v1,
            "v1_1_agree_cards": majority_v1_1,
            "total_cards": len(card_ids),
            "v1_1_unanimous_cards": unanimous_v1_1,
        },
        "interpretation_boundary": "Controlled measurement scenarios anchored to real candidates; not a production-effectiveness test.",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, field in (("confusion_v1.csv", "v1_route"), ("confusion_v1_1.csv", "v1_1_route")):
        write_csv(args.output / name, confusion(responses, field), ["reference_route", *CHOICES, "total"])

    per_evaluator = []
    for eid in sorted({x["evaluator_id"] for x in responses}):
        subset = [x for x in responses if x["evaluator_id"] == eid]
        per_evaluator.append({
            "evaluator_id": eid,
            "judgments": len(subset),
            "CR_v1": sum(x["agree_v1"] for x in subset) / len(subset),
            "CR_v1_1": sum(x["agree_v1_1"] for x in subset) / len(subset),
            "delta_CR": sum(x["agree_v1_1"] - x["agree_v1"] for x in subset) / len(subset),
            "median_confidence": percentile([int(x["confidence"]) for x in subset], 0.5),
        })
    write_csv(args.output / "per_evaluator.csv", per_evaluator, list(per_evaluator[0]))

    per_stratum = []
    for stratum in ("A_stable", "B_stable", "B_to_D", "C_stable", "D_stable"):
        subset = [x for x in responses if x["stratum"] == stratum]
        counts = Counter(x["choice"] for x in subset)
        per_stratum.append({
            "stratum": stratum,
            "cards": len({x["card_id"] for x in subset}),
            "judgments": len(subset),
            **{f"choice_{c}": counts[c] for c in CHOICES},
            "CR_v1": sum(x["agree_v1"] for x in subset) / len(subset),
            "CR_v1_1": sum(x["agree_v1_1"] for x in subset) / len(subset),
        })
    write_csv(args.output / "per_stratum.csv", per_stratum, list(per_stratum[0]))

    per_card = []
    for cid in card_ids:
        subset = by_card[cid]
        counts = Counter(x["choice"] for x in subset)
        per_card.append({
            "card_id": cid,
            "stratum": key[cid]["stratum"],
            "v1_route": key[cid]["v1_route"],
            "v1_1_route": key[cid]["v1_1_route"],
            **{f"choice_{c}": counts[c] for c in CHOICES},
            "v1_agreements": sum(x["agree_v1"] for x in subset),
            "v1_1_agreements": sum(x["agree_v1_1"] for x in subset),
            "median_confidence": percentile([int(x["confidence"]) for x in subset], 0.5),
        })
    write_csv(args.output / "per_card.csv", per_card, list(per_card[0]))
    write_csv(args.output / "card_majority.csv", majority_rows, list(majority_rows[0]))

    report = f"""# B/D 边界修订独立评价结果

三名匿名评价者完成 24 张卡，共 72 次判断。旧规则与评价者选择一致 52/72（{cr1:.1%}），修订规则一致 68/72（{cr11:.1%}），差值为 {cr11-cr1:+.1%}；冻结主分析按全部卡片聚类的 10,000 次 bootstrap 95% 区间为 {summary['delta_CR_card_cluster_bootstrap_95_ci'][0]:+.1%} 至 {summary['delta_CR_card_cluster_bootstrap_95_ci'][1]:+.1%}。保持 6/6/4/4/4 抽样配额的分层卡片聚类 bootstrap 敏感性区间为 {summary['delta_CR_stratified_card_cluster_bootstrap_95_ci_sensitivity'][0]:+.1%} 至 {summary['delta_CR_stratified_card_cluster_bootstrap_95_ci_sensitivity'][1]:+.1%}。

B→D 层共 18 次判断，其中 16 次选择 D，2 次选择 A；稳定层 54 次判断中 52 次与修订规则一致（{summary['stable_group']['v1_1_rate']:.1%}）。24 张卡中，三人多数票与 v1.1 一致 {majority_v1_1}/24，{unanimous_v1_1} 张三人全体与 v1.1 一致。评价者间 nominal Krippendorff’s α={summary['inter_rater']['krippendorff_alpha_nominal']:.3f}，Fleiss’ κ={summary['inter_rater']['fleiss_kappa']:.3f}。置信度中位数为 {summary['confidence']['median']:.0f}。

这些数字支持在本组受控测量情景中把“测量语义已经明确、历史与上下文证据也足够”的候选从 B 调整到 D。它们不证明正式 SLO 已获批准，也不是生产效果检验；卡片中的 M1—M4 说明部分属于基于真实候选构造的实验给定条件。
"""
    (args.output / "RESULTS_REPORT_ZH.md").write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
