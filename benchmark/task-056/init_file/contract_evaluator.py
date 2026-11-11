"""task056 public contract READER ONLY, no evaluator score or native grant."""
import json
from pathlib import Path
def read_public_contract(root):
 return json.loads((Path(root)/"task056_v54_contract.json").read_text(encoding="utf-8"))
