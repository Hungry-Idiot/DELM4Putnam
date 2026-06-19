from __future__ import annotations

from src.shared_context.schema import AGENT_WRITABLE_TYPES, NOTE_TYPES, SharedNote


MAX_CONTENT_CHARS = 4000
VERIFIED_TYPES = {"VERIFIED_PATCH", "FINAL_PROOF"}


def validate_note(note: SharedNote, writer_role: str = "agent") -> tuple[bool, str]:
    if note.type not in NOTE_TYPES:
        return False, f"Invalid note type: {note.type}"

    if not note.content or not note.content.strip():
        return False, "Note content must not be empty."

    if len(note.content) > MAX_CONTENT_CHARS:
        return False, f"Note content exceeds {MAX_CONTENT_CHARS} characters."

    if writer_role == "agent":
        if note.type not in AGENT_WRITABLE_TYPES:
            return False, f"Agent role cannot write note type: {note.type}"
    elif writer_role == "system":
        pass
    else:
        return False, f"Invalid writer_role: {writer_role}"

    if note.type in VERIFIED_TYPES and not _has_verification_reference(note):
        return (
            False,
            f"{note.type} requires attempt_path or proof/check metadata.",
        )

    return True, ""


def _has_verification_reference(note: SharedNote) -> bool:
    if note.attempt_path:
        return True
    for key in note.metadata:
        normalized = str(key).lower()
        if "proof" in normalized or "check" in normalized:
            return True
    return False
