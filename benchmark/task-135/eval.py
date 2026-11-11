#!/usr/bin/env python3
import json
from pathlib import Path
from contract_evaluator import evaluate
print(json.dumps(evaluate(Path('/home/user/Desktop'), 'urbanwind-task-135'), ensure_ascii=False, sort_keys=True))
