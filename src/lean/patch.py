from __future__ import annotations

import re
from pathlib import Path


def contains_forbidden_placeholders(text: str) -> dict[str, bool]:
    return {
        "has_sorry": bool(_SORRY_RE.search(text)),
        "has_admit": bool(_ADMIT_RE.search(text)),
        "has_axiom": bool(_AXIOM_RE.search(text)),
    }


def replace_first_sorry(source: str, proof_patch: str) -> str:
    if not proof_patch.strip():
        raise ValueError("proof_patch must not be empty.")

    match = _SORRY_RE.search(source)
    if match is None:
        raise ValueError("No standalone 'sorry' found in Lean source.")

    return source[: match.start()] + proof_patch + source[match.end() :]


def make_attempt_file(
    original_source: str, proof_patch: str, attempt_path: Path
) -> Path:
    patched_source = replace_first_sorry(original_source, proof_patch)
    attempt_path.parent.mkdir(parents=True, exist_ok=True)
    attempt_path.write_text(patched_source, encoding="utf-8")
    return attempt_path


_SORRY_RE = re.compile(r"\bsorry\b")
_ADMIT_RE = re.compile(r"\badmit\b")
_AXIOM_RE = re.compile(r"(?m)^\s*(?:private\s+)?axiom\b")
