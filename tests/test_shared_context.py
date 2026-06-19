from pathlib import Path

import pytest

from src.shared_context.renderer import render_full_context, render_worker_context
from src.shared_context.schema import SharedNote
from src.shared_context.storage import append_note, get_notes, init_db


def test_init_append_and_pull(tmp_path: Path) -> None:
    db_path = tmp_path / "context.sqlite"
    init_db(db_path)

    saved = append_note(
        db_path,
        SharedNote(
            seq=None,
            problem_id="putnam_1968_a1",
            worker_id="A1",
            type="CLAIM",
            content="Try an integral identity.",
        ),
    )

    notes = get_notes(db_path)
    assert saved.seq == 1
    assert len(notes) == 1
    assert notes[0].content == "Try an integral identity."
    assert notes[0].created_at is not None


def test_agent_cannot_write_verified_patch(tmp_path: Path) -> None:
    db_path = tmp_path / "context.sqlite"

    with pytest.raises(ValueError, match="Agent role cannot write"):
        append_note(
            db_path,
            SharedNote(
                seq=None,
                problem_id="putnam_1968_a1",
                worker_id="A1",
                type="VERIFIED_PATCH",
                content="verified",
                attempt_path="runs/x/attempt.lean",
            ),
            writer_role="agent",
        )


def test_system_can_write_verified_patch(tmp_path: Path) -> None:
    db_path = tmp_path / "context.sqlite"

    saved = append_note(
        db_path,
        SharedNote(
            seq=None,
            problem_id="putnam_1968_a1",
            worker_id="checker",
            type="VERIFIED_PATCH",
            content="patch checked by Lean",
            attempt_path="runs/x/attempt.lean",
        ),
        writer_role="system",
    )

    assert saved.seq == 1
    assert get_notes(db_path)[0].type == "VERIFIED_PATCH"


def test_pull_since_returns_only_new_notes(tmp_path: Path) -> None:
    db_path = tmp_path / "context.sqlite"
    first = append_note(
        db_path,
        SharedNote(
            seq=None,
            problem_id="putnam_1968_a1",
            worker_id="A1",
            type="CLAIM",
            content="first",
        ),
    )
    second = append_note(
        db_path,
        SharedNote(
            seq=None,
            problem_id="putnam_1968_a1",
            worker_id="A2",
            type="TACTIC_TRIED",
            content="second",
        ),
    )

    notes = get_notes(db_path, since_seq=first.seq or 0)
    assert [note.seq for note in notes] == [second.seq]
    assert notes[0].content == "second"


def test_full_context_shows_complete_proof_patch() -> None:
    notes = [
        SharedNote(
            seq=1,
            problem_id="p",
            worker_id="A1",
            type="PROOF_PATCH",
            content="by\n  exact very_specific_patch_term",
        )
    ]

    rendered = render_full_context(notes)

    assert "very_specific_patch_term" in rendered


def test_worker_context_hides_complete_proof_patch() -> None:
    notes = [
        SharedNote(
            seq=1,
            problem_id="p",
            worker_id="A1",
            type="PROOF_PATCH",
            content="by\n  exact very_specific_patch_term",
        )
    ]

    rendered = render_worker_context(notes)

    assert "previous candidate patch submitted" in rendered
    assert "very_specific_patch_term" not in rendered


def test_worker_context_summarizes_timeout() -> None:
    notes = [
        SharedNote(
            seq=2,
            problem_id="p",
            worker_id="checker",
            type="LEAN_ERROR",
            content="check_status: timed out\nerrors:\n- Timed out after 180 seconds.",
        )
    ]

    rendered = render_worker_context(notes)

    assert "previous Lean check timed out" in rendered
    assert "avoid expensive global simplification/search" in rendered
    assert "Timed out after 180 seconds" not in rendered


def test_worker_context_summarizes_simp_no_progress() -> None:
    notes = [
        SharedNote(
            seq=3,
            problem_id="p",
            worker_id="checker",
            type="LEAN_ERROR",
            content="errors:\n- `simp` made no progress",
        )
    ]

    rendered = render_worker_context(notes)

    assert "`simp` made no progress" in rendered
    assert "avoid repeating the same simp step" in rendered


def test_single_worker_script_uses_worker_context_for_prompt() -> None:
    script = Path("scripts/experiments/run_single_codex_worker.py").read_text(
        encoding="utf-8"
    )

    assert "render_worker_context(notes)" in script
    assert "Shared Context mode for worker prompt: worker" in script
