"""Rebuild the frozen reviewer-artifact summary using only package contents."""
import argparse, csv, gzip, hashlib, json
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parent
def load_json(rel): return json.loads((ROOT/rel).read_text(encoding="utf-8"))
def rows(rel):
    with (ROOT/rel).open(encoding="utf-8", newline="") as f: return list(csv.DictReader(f))
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser(); p.add_argument("--check", action="store_true"); a=p.parse_args()
    cards=load_json("governance/governance_cards_pseudonymized.json")
    statuses=Counter(c["evidence_status"] for c in cards)
    ratings=rows("expert/expert_ratings.csv"); card_index=rows("expert/expert_card_index.csv")
    positive=sum(int(r["governance_appropriateness"])>int(r["threshold_plausibility"]) for r in ratings)
    controls=rows("manual/manual_control_judgments.csv"); groups=rows("manual/manual_group_judgments.csv")
    directions=Counter(r["direction_after_unblinding"] for r in groups)
    rca_cards=load_json("rcaeval/governance_cards.json"); rca_summary=load_json("rcaeval/governance_summary.json")
    with gzip.open(ROOT/"rcaeval/canonical_observations.csv.gz", "rt", encoding="utf-8", newline="") as f: observation_rows=sum(1 for _ in f)-1
    result={
      "governance":{"cards":len(cards),"status_counts":dict(statuses)},
      "expert":{"cards":len(card_index),"pairs":len(ratings),"positive_pairs":positive},
      "manual":{"control_judgments":len(controls),"group_judgments":len(groups),"direction_counts":dict(directions)},
      "rcaeval":{"observations":observation_rows,"cards":len(rca_cards),"refused":rca_summary["status_counts"]["insufficient_evidence"],"history_window_shortened":rca_summary["history_window_shortened"]},
    }
    expected=load_json("EXPECTED_SUMMARY.json")
    manifest=load_json("MANIFEST.json")
    hash_ok=all(digest(ROOT/path)==value for path,value in manifest["files"].items())
    if a.check:
      assert result==expected, (result, expected)
      assert hash_ok, "manifest hash mismatch"
      print(json.dumps({"status":"PASS","summary":result,"manifest_hashes":True},ensure_ascii=False,indent=2,sort_keys=True))
    else: print(json.dumps({"summary":result,"manifest_hashes":hash_ok},ensure_ascii=False,indent=2,sort_keys=True))
if __name__=="__main__": main()
