from __future__ import annotations
import hashlib, json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Problem:
    problem_id: str
    split: str
    main_path: Path
    statement_path: Path
    main_sha256: str
    statement_sha256: str


class Benchmark:
    """A directory with ``manifest.json`` and ``problems/<id>/{Main.lean,problem.md}``.

    ``manifest.json`` lists ``{"id", "split", "main_sha256", "statement_sha256"}`` per problem.
    Pass ``expected_manifest_sha256`` to refuse a manifest that differs from a pinned hash.
    """

    def __init__(self, root: Path, *, expected_manifest_sha256: str | None = None):
        self.root = Path(root).resolve()
        self.manifest_path = self.root / "manifest.json"
        actual = hashlib.sha256(self.manifest_path.read_bytes()).hexdigest().upper()
        if expected_manifest_sha256 is not None and actual != expected_manifest_sha256.upper():
            raise ValueError(f"manifest hash mismatch: {actual}")
        self.manifest_sha256 = actual
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.problems = tuple(
            Problem(x["id"], x["split"], self.root / "problems" / x["id"] / "Main.lean",
                    self.root / "problems" / x["id"] / "problem.md", x["main_sha256"], x["statement_sha256"])
            for x in data["problems"])

    def split(self, name: str):
        return tuple(p for p in self.problems if p.split == name)

    def select(self, problem_id: str, *, allow_split: str | None = None) -> Problem:
        p = next((x for x in self.problems if x.problem_id == problem_id), None)
        if p is None:
            raise KeyError(problem_id)
        if allow_split is not None and p.split != allow_split:
            raise PermissionError(f"only {allow_split} problems are enabled")
        return p
