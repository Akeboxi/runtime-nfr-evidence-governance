import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parent
m=json.loads((root/"MANIFEST.json").read_text(encoding="utf-8"))
bad=[p for p,h in m["files"].items() if not (root/p).is_file() or hashlib.sha256((root/p).read_bytes()).hexdigest()!=h]
print(json.dumps({"files":len(m["files"]),"mismatches":bad,"publication_status":m["publication_status"]},indent=2))
assert not bad
