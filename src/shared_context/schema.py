from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


NOTE_TYPES = {
    "PROBLEM",
    "CLAIM",
    "TACTIC_TRIED",
    "LEAN_ERROR",
    "FAIL",
    "LEMMA_CANDIDATE",
    "VERIFIED_LEMMA",
    "PROOF_PATCH",
    "VERIFIED_PATCH",
    "FINAL_PROOF",
}

AGENT_WRITABLE_TYPES = {
    "CLAIM",
    "TACTIC_TRIED",
    "FAIL",
    "LEMMA_CANDIDATE",
    "PROOF_PATCH",
}

SYSTEM_WRITABLE_TYPES = NOTE_TYPES


@dataclass(frozen=True)
class SharedNote:
    seq: int | None
    problem_id: str
    worker_id: str
    type: str
    content: str
    target_seq: int | None = None
    attempt_path: str | None = None
    status: str = "admitted"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
