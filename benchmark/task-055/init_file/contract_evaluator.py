"""urbanwind-task-055 public schema only; not a scorer or native executor."""
import json
from pathlib import Path
def read_public_contract(root):
    return json.loads((Path(root)/"repair_contract.json").read_text(encoding="utf-8"))
