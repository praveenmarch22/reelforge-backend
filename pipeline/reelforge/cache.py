"""Remembers which inputs produced each generated file, so unchanged steps are reused."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Optional


def input_hash(**inputs) -> str:
    blob = json.dumps(inputs, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class Cache:
    def __init__(self, project_dir: Path):
        self.path = project_dir / "cache.json"
        self._lock = threading.Lock()
        self.entries: dict = json.loads(self.path.read_text("utf-8-sig")) if self.path.exists() else {}

    def get(self, key: str, digest: str) -> Optional[dict]:
        entry = self.entries.get(key)
        if not entry or entry.get("hash") != digest:
            return None
        result = entry["result"]
        if not Path(result["path"]).exists():
            return None
        return result

    def put(self, key: str, digest: str, result: dict) -> None:
        with self._lock:
            self.entries[key] = {"hash": digest, "result": result}
            self._save()

    def forget(self, match: str) -> list[str]:
        """Remove entries whose key is `match` or ends with ':<match>' (for example a scene id)."""
        with self._lock:
            removed = [k for k in self.entries if match in ("all", k) or k.endswith(f":{match}")]
            for k in removed:
                del self.entries[k]
            self._save()
        return removed

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.entries, indent=2, ensure_ascii=False), "utf-8")
        tmp.replace(self.path)
