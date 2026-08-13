#!/usr/bin/env python3
"""Analyze completed blinded-routing responses without independent-pair inflation."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path


CATEGORIES = ["A", "B", "C", "D"]


def percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    if not values:
        return float("nan")
    index = (len(values) - 1) * p
    lo, hi = math.floor(index), math.ceil(index)
    if lo == hi:
        return values[lo]
    return values[lo] * (hi - index) + values[hi] * (index - lo)


def median_iqr(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "median": None, "q1": None, "q3": None}
    return {
        "n": len(values),
        "median": statistics.median(values),
        "q1": percentile(values, 0.25),
        "q3": percentile(values, 0.75),
    }


def fleiss_kappa(item_choices: list[list[str]]) -> float | None:
    complete = [x for x in item_choices if len(x) >= 2]
    if not complete or len({len(x) for x in complete}) != 1:
        return None
    n_raters = len(complete[0])
    counts = [Counter(x) for x in complete]
    p_i = [
        (sum(v * v for v in row.values()) - n_raters) / (n_raters * (n_raters - 1))
        for row in counts
    ]
    p_bar = statistics.mean(p_i)
    total = len(complete) * n_raters
    p_cat = {cat: sum(row[cat] for row in counts) / total for cat in CATEGORIES}
    p_e = sum(v * v for v in p_cat.values())
    return None if math.isclose(1 - p_e, 0) else (p_bar - p_e) / (1 - p_e)


def krippendorff_alpha_nominal(item_choices: list[list[str]]) -> float | None:
    usable = [x for x in item_choices if len(x) >= 2]
    total_pairs = sum(len(x) * (len(x) - 1) for x in usable)
    if total_pairs == 0:
        return None
    disagree_pairs = sum(
        sum(1 for a in x for b in x if a != b) for x in usable
    )
    d_o = disagree_pairs / total_pairs
    margins = Counter(choice for x in usable for choice in x)
    n = sum(margins.values())
    if n < 2:
        return None
    d_e = sum(v * (n - v) for v in margins.values()) / (n * (n - 1))
    return None if math.isclose(d_e, 0) else 1 - d_o / d_e


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--responses", required=True, type=Path)
    parser.add_argument("--answer-key", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--bootstrap-reps", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260814)
    args = parser.parse_args()

    responses = read_csv(args.responses)
    key_rows = read_csv(args.answer_key)
    key = {x["blind_card_id"]: x for x in key_rows}
    valid = [
        x
        for x in responses
        if x.get("blind_card_id") in key and x.get("blinded_choice", "").strip().upper() in CATEGORIES
    ]
    for row in valid:
        row["blinded_choice"] = row["blinded_choice"].strip().upper()
        row["correct"] = row["blinded_choice"] == key[row["blind_card_id"]]["protocol_choice"]

    reviewer_counts = Counter(x["evaluator_id"] for x in valid)
    complete_reviewers = sorted(x for x, n in reviewer_counts.items() if n == 32)
    primary = [x for x in valid if x["evaluator_id"] in complete_reviewers]
    if len(complete_reviewers) < 3:
        raise SystemExit(
            f"Primary analysis requires 3 complete evaluators; found {complete_reviewers} with counts {dict(reviewer_counts)}"
        )

    by_card: dict[str, list[dict]] = defaultdict(list)
    for row in primary:
        by_card[row["blind_card_id"]].append(row)
    if set(by_card) != set(key):
        raise SystemExit("Primary complete-case data do not cover all 32 frozen cards")

    overall = sum(x["correct"] for x in primary) / len(primary)
    card_ids = sorted(by_card)
    rng = random.Random(args.seed)
    boot: list[float] = []
    for _ in range(args.bootstrap_reps):
        sampled = [rng.choice(card_ids) for _ in card_ids]
        rows = [row for card_id in sampled for row in by_card[card_id]]
        boot.append(sum(x["correct"] for x in rows) / len(rows))
    ci = [percentile(boot, 0.025), percentile(boot, 0.975)]

    confusion_rows = []
    for truth in CATEGORIES:
        row = {"protocol_choice": truth}
        subset = [x for x in primary if key[x["blind_card_id"]]["protocol_choice"] == truth]
        for observed in CATEGORIES:
            row[f"expert_{observed}"] = sum(x["blinded_choice"] == observed for x in subset)
        confusion_rows.append(row)

    per_state = []
    for choice in CATEGORIES:
        subset = [x for x in primary if key[x["blind_card_id"]]["protocol_choice"] == choice]
        per_state.append(
            {
                "protocol_choice": choice,
                "protocol_route": next(x["protocol_route"] for x in key_rows if x["protocol_choice"] == choice),
                "expert_card_judgments": len(subset),
                "agreements": sum(x["correct"] for x in subset),
                "agreement_rate": sum(x["correct"] for x in subset) / len(subset),
            }
        )

    per_card = []
    majority_correct = 0
    for card_id in card_ids:
        rows = by_card[card_id]
        choices = Counter(x["blinded_choice"] for x in rows)
        majority = choices.most_common(1)[0][0]
        truth = key[card_id]["protocol_choice"]
        majority_correct += majority == truth
        per_card.append(
            {
                "blind_card_id": card_id,
                "protocol_choice": truth,
                "difficulty": key[card_id]["difficulty"],
                "agreement_count": sum(x["correct"] for x in rows),
                "agreement_rate": sum(x["correct"] for x in rows) / len(rows),
                "majority_choice": majority,
                "majority_matches_protocol": majority == truth,
            }
        )

    per_reviewer = []
    for evaluator_id in complete_reviewers:
        rows = [x for x in primary if x["evaluator_id"] == evaluator_id]
        choice_counts = Counter(x["blinded_choice"] for x in rows)
        per_reviewer.append(
            {
                "evaluator_id": evaluator_id,
                "agreements": sum(x["correct"] for x in rows),
                "judgments": len(rows),
                "agreement_rate": sum(x["correct"] for x in rows) / len(rows),
                "choice_A": choice_counts["A"],
                "choice_B": choice_counts["B"],
                "choice_C": choice_counts["C"],
                "choice_D": choice_counts["D"],
            }
        )

    per_difficulty = []
    for difficulty in ("obvious", "boundary"):
        difficulty_cards = [card_id for card_id in card_ids if key[card_id]["difficulty"] == difficulty]
        rows = [row for card_id in difficulty_cards for row in by_card[card_id]]
        majority_matches = sum(
            Counter(x["blinded_choice"] for x in by_card[card_id]).most_common(1)[0][0]
            == key[card_id]["protocol_choice"]
            for card_id in difficulty_cards
        )
        per_difficulty.append(
            {
                "difficulty": difficulty,
                "cards": len(difficulty_cards),
                "expert_card_judgments": len(rows),
                "agreements": sum(x["correct"] for x in rows),
                "agreement_rate": sum(x["correct"] for x in rows) / len(rows),
                "majority_matching_cards": majority_matches,
            }
        )

    item_choices = [[x["blinded_choice"] for x in by_card[c]] for c in card_ids]
    confidence_correct = [
        float(x["confidence_1_to_5"])
        for x in primary
        if x["correct"] and x.get("confidence_1_to_5", "").strip()
    ]
    confidence_incorrect = [
        float(x["confidence_1_to_5"])
        for x in primary
        if not x["correct"] and x.get("confidence_1_to_5", "").strip()
    ]
    durations = [
        float(x["stage1_duration_seconds"])
        for x in primary
        if x.get("stage1_duration_seconds", "").strip()
    ]
    post_reveal = Counter(
        x.get("post_reveal_decision", "").strip()
        for x in primary
        if x.get("post_reveal_decision", "").strip()
    )
    post_reveal_rows = [x for x in primary if x.get("final_choice", "").strip().upper() in CATEGORIES]
    post_reveal_final_agreements = sum(
        x["final_choice"].strip().upper() == key[x["blind_card_id"]]["protocol_choice"]
        for x in post_reveal_rows
    )
    initial_disagreements = [x for x in primary if not x["correct"]]
    initial_disagreements_changed_to_protocol = sum(
        x.get("post_reveal_decision", "").strip() == "changed_to_protocol"
        for x in initial_disagreements
    )

    summary = {
        "analysis": "blinded-human-routing/v1",
        "complete_evaluators": complete_reviewers,
        "cards": len(card_ids),
        "expert_card_judgments": len(primary),
        "primary": {
            "name": "blinded routing agreement",
            "agreements": sum(x["correct"] for x in primary),
            "rate": overall,
            "card_cluster_bootstrap_95_ci": ci,
            "bootstrap_reps": args.bootstrap_reps,
            "bootstrap_seed": args.seed,
            "note": "The point estimate uses 96 expert-card judgments; uncertainty resamples the 32 cards and retains all evaluator judgments within each sampled card.",
        },
        "card_majority": {
            "cards_matching_protocol": majority_correct,
            "rate": majority_correct / len(card_ids),
        },
        "inter_rater": {
            "krippendorff_alpha_nominal": krippendorff_alpha_nominal(item_choices),
            "fleiss_kappa": fleiss_kappa(item_choices),
            "note": "The four disposition routes are treated as nominal, not ordinal categories.",
        },
        "confidence_when_agreeing": median_iqr(confidence_correct),
        "confidence_when_disagreeing": median_iqr(confidence_incorrect),
        "stage1_duration_seconds": median_iqr(durations),
        "post_reveal_decisions": dict(post_reveal),
        "post_reveal_final_agreement": {
            "agreements": post_reveal_final_agreements,
            "judgments": len(post_reveal_rows),
            "rate": post_reveal_final_agreements / len(post_reveal_rows) if post_reveal_rows else None,
            "initial_disagreements_changed_to_protocol": initial_disagreements_changed_to_protocol,
            "initial_disagreements": len(initial_disagreements),
            "interpretation_boundary": "Post-reveal choices were made after the frozen protocol disposition was explicitly shown; they describe stated adoption under disclosure, not independent blinded reproducibility or causal persuasion.",
        },
        "supplementary_availability": {
            "per_card_timing_available": bool(durations),
            "post_reveal_stage_available": bool(post_reveal),
        },
        "per_reviewer": per_reviewer,
        "per_difficulty": per_difficulty,
        "claim_boundary": "Descriptive agreement with the frozen protocol on a purposefully balanced card sample; no population approval rate, threshold correctness, workflow utility, or causal effect is inferred.",
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csv(
        args.output_dir / "confusion_matrix.csv",
        confusion_rows,
        ["protocol_choice", "expert_A", "expert_B", "expert_C", "expert_D"],
    )
    write_csv(
        args.output_dir / "per_state.csv",
        per_state,
        ["protocol_choice", "protocol_route", "expert_card_judgments", "agreements", "agreement_rate"],
    )
    write_csv(
        args.output_dir / "per_card.csv",
        per_card,
        [
            "blind_card_id",
            "protocol_choice",
            "difficulty",
            "agreement_count",
            "agreement_rate",
            "majority_choice",
            "majority_matches_protocol",
        ],
    )
    write_csv(
        args.output_dir / "per_reviewer.csv",
        per_reviewer,
        [
            "evaluator_id",
            "agreements",
            "judgments",
            "agreement_rate",
            "choice_A",
            "choice_B",
            "choice_C",
            "choice_D",
        ],
    )
    write_csv(
        args.output_dir / "per_difficulty.csv",
        per_difficulty,
        [
            "difficulty",
            "cards",
            "expert_card_judgments",
            "agreements",
            "agreement_rate",
            "majority_matching_cards",
        ],
    )

    report = f"""# Blinded routing analysis\n\n- Complete evaluators: {', '.join(complete_reviewers)}\n- Cards: {len(card_ids)}\n- Expert-card judgments: {len(primary)}\n- Blinded routing agreement: {sum(x['correct'] for x in primary)}/{len(primary)} ({overall:.1%})\n- Card-cluster bootstrap 95% CI: [{ci[0]:.1%}, {ci[1]:.1%}]\n- Card-majority agreement: {majority_correct}/{len(card_ids)} ({majority_correct / len(card_ids):.1%})\n- Krippendorff's nominal alpha: {summary['inter_rater']['krippendorff_alpha_nominal']:.3f}\n- Fleiss' kappa: {summary['inter_rater']['fleiss_kappa']:.3f}\n- Post-reveal transition counts: {dict(post_reveal)}\n- Post-reveal final agreement: {post_reveal_final_agreements}/{len(post_reveal_rows)}\n\nThese results describe agreement with the frozen protocol on the purposefully balanced 32-card sample. Post-reveal choices describe stated adoption after disclosure and are not independent blinded evidence. The experiment does not establish threshold correctness, workflow utility, governance effects, or a population approval rate.\n"""
    (args.output_dir / "REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
