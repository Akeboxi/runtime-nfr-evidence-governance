"""Re-run RCAEval adaptation when the public source archive is available."""
from pathlib import Path
import argparse, json, sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "adapter_snapshot"))
from data.runtime_nfr_external import adapt_rcaeval_re1_ob

p=argparse.ArgumentParser()
p.add_argument("--source-root", type=Path, required=True)
p.add_argument("--output", type=Path, default=HERE / "rebuilt")
p.add_argument("--archive-sha256", default="4a709297e0a829f0f2ee8a7792a6d74da32d663c600565b7fffc860963b840c4")
a=p.parse_args()
result=adapt_rcaeval_re1_ob(a.source_root, a.output, frozen_archive_sha256=a.archive_sha256)
print(json.dumps(result, ensure_ascii=False, indent=2))
