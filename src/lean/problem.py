from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LeanAttempt:
    problem_id: str
    original_path: Path
    attempt_path: Path
    proof_patch: str
