from __future__ import annotations
import shutil, uuid
from pathlib import Path

class Sandbox:
    def __init__(self, base: Path):
        self.base = base.resolve(); self.path = (self.base / uuid.uuid4().hex).resolve()
        if self.base == self.path or self.base not in self.path.parents: raise RuntimeError("sandbox path escape")
        self.path.mkdir(parents=True, exist_ok=False)
    def __enter__(self): return self
    def __exit__(self, *_): shutil.rmtree(self.path, ignore_errors=True)
