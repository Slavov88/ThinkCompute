"""Create a fresh one-problem broker package from problem metadata only."""
from __future__ import annotations

import shutil
from pathlib import Path

from .benchmark import Problem


def package_one_problem(problem: Problem, destination: Path, *, expected_id: str | None = None,
                        allowed_split: str | None = None) -> Path:
    """Copy exactly one explicitly authorized problem into a fresh package.

    The caller supplies an already-selected ``Problem``. The function never
    discovers other IDs and refuses a split or ID mismatch.
    """
    if allowed_split is not None and problem.split != allowed_split:
        raise PermissionError(f"broker packages may contain {allowed_split} problems only")
    if expected_id is not None and problem.problem_id != expected_id:
        raise PermissionError("problem is not the explicitly selected problem ID")
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    output = destination / "problem"
    output.mkdir(parents=True)
    shutil.copy2(problem.main_path, output / "Main.lean")
    shutil.copy2(problem.statement_path, output / "problem.md")
    return output
