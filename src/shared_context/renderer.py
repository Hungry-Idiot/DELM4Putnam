from __future__ import annotations

from src.shared_context.schema import SharedNote


def render_notes(notes: list[SharedNote], max_chars: int = 12000) -> str:
    return render_full_context(notes, max_chars=max_chars)


def render_shared_context(
    notes: list[SharedNote], max_chars: int = 12000, mode: str = "full"
) -> str:
    if mode == "full":
        return render_full_context(notes, max_chars=max_chars)
    if mode == "worker":
        return render_worker_context(notes, max_chars=max_chars)
    raise ValueError(f"Unknown shared context render mode: {mode}")


def render_full_context(notes: list[SharedNote], max_chars: int = 12000) -> str:
    sorted_notes = sorted(notes, key=lambda note: note.seq or 0)
    lines = [_render_note(note) for note in sorted_notes]
    return _truncate_lines_from_front(lines, max_chars=max_chars)


def render_worker_context(notes: list[SharedNote], max_chars: int = 6000) -> str:
    sorted_notes = sorted(notes, key=lambda note: note.seq or 0)
    lines = [_render_worker_note(note) for note in sorted_notes]
    lines = [line for line in lines if line]
    return _truncate_lines_from_front(lines, max_chars=max_chars)


def _truncate_lines_from_front(lines: list[str], max_chars: int) -> str:
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text

    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        line_len = len(line) + (1 if kept else 0)
        if kept and total + line_len > max_chars:
            break
        if not kept and line_len > max_chars:
            kept.append(line[-max_chars:])
            total = max_chars
            break
        kept.append(line)
        total += line_len

    kept.reverse()
    prefix = "[... truncated older notes ...]"
    rendered = "\n".join([prefix, *kept])
    if len(rendered) > max_chars:
        return rendered[-max_chars:]
    return rendered


def _render_note(note: SharedNote) -> str:
    seq = note.seq if note.seq is not None else "?"
    content = note.content.strip().replace("\n", "\\n")
    extras: list[str] = []
    if note.target_seq is not None:
        extras.append(f"target=#{note.target_seq}")
    if note.attempt_path:
        extras.append(f"attempt={note.attempt_path}")
    if note.status:
        extras.append(f"status={note.status}")

    suffix = f" ({', '.join(extras)})" if extras else ""
    return f"[#{seq}][{note.worker_id}][{note.type}] {content}{suffix}"


def _render_worker_note(note: SharedNote) -> str:
    seq = note.seq if note.seq is not None else "?"
    prefix = f"[#{seq}][{note.worker_id}][{note.type}]"

    if note.type == "PROOF_PATCH":
        return (
            f"{prefix} previous candidate patch submitted; "
            "see run log for full text."
        )
    if note.type == "LEAN_ERROR":
        return f"{prefix} {_lean_error_summary(note.content)}"
    if note.type in {"PROBLEM", "VERIFIED_PATCH", "FINAL_PROOF", "VERIFIED_LEMMA"}:
        return _render_note(note)
    if note.type in {"CLAIM", "TACTIC_TRIED", "FAIL", "LEMMA_CANDIDATE"}:
        return _render_note(note)
    return ""


def _lean_error_summary(content: str) -> str:
    normalized = content.lower()
    if "timed out" in normalized:
        return (
            "previous Lean check timed out; avoid expensive global "
            "simplification/search."
        )
    if "`simp` made no progress" in normalized or "simp made no progress" in normalized:
        return (
            "previous Lean check failed: `simp` made no progress; avoid "
            "repeating the same simp step."
        )

    first_error = _first_error_line(content)
    if first_error:
        return f"previous Lean check failed: {_truncate(first_error, 300)}"
    return "previous Lean check failed; see run log for full error."


def _first_error_line(content: str) -> str | None:
    lines = [line.strip() for line in content.splitlines()]
    for line in lines:
        if line.startswith("-"):
            return line.lstrip("-").strip()
    for line in lines:
        if line and not line.lower().startswith(("check_status:", "summary:", "suggestion:", "errors:")):
            return line
    return None


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
