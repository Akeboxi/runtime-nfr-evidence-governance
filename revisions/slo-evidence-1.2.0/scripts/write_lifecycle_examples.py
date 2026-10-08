"""Export explicitly synthetic executable examples; not human confirmations."""
import copy
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tests"))
from test_evidence_revision import example, close, check
from slo_evidence import derive_child


def main():
    output=Path(sys.argv[1]);output.mkdir(parents=True,exist_ok=True)
    parent,store,refs=example();before=copy.deepcopy(parent)
    cases=[]
    def add(name,c):
        cases.append({"case":name,"evidence_type":"SYNTHETIC_SOFTWARE_CONTRACT_NOT_HUMAN_REVIEW",
                      "card":c,"result":check(c,store)})
    add("initial_high_tail_C",parent)
    note=copy.deepcopy(parent);note["closures"]=[{"record_id":"plain-note","task_type":"context","resolution":"normal workload"}]
    add("plain_note_still_C",derive_child(parent,note,mode="carry"))
    completed=copy.deepcopy(parent);completed["closures"]=[close(parent,refs)]
    child=derive_child(parent,completed,mode="carry");add("valid_scoped_confirmation_D_not_approved",child)
    unresolved=copy.deepcopy(child);unresolved["measurement_evidence"]["M4"]={"status":"unknown","refs":[]}
    add("measurement_unresolved_still_B",derive_child(child,unresolved,mode="carry"))
    changed=copy.deepcopy(child);changed["candidate_value"]+=1
    add("regen_invalidates_old_confirmation_C",derive_child(child,changed,mode="regen"))
    assert parent==before
    (output/"lifecycle_cases.json").write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({c["case"]:c["result"]["route"] for c in cases},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
