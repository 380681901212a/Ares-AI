import json
import os
from datetime import datetime, timezone
from pathlib import Path

class RunLogger:
    def __init__(self, run_id: str, logs_dir: str = "logs"):
        Path(logs_dir).mkdir(exist_ok=True)
        self.path = Path(logs_dir) / f"{run_id}.jsonl"
    
    def log(self, node: str, event: str, data: dict = None):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "node": node,
            "event": event,
            "data": data or {}
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
