from pathlib import Path
from types import SimpleNamespace

import scripts.experiments.run_interactive_shared_context_agents as runner
from src.codex.parser import InteractiveWorkerAction
from src.controller.patch_service import PatchSubmissionResult
from src.shared_context.schema import SharedNote
from src.shared_context.storage import append_note, get_notes, init_db


def test_pull_worker_context_uses_worker_renderer(tmp_path: Path) -> None:
    db_path = tmp_path / "context.sqlite"
    init_db(db_path)
    append_note(
        db_path,
        SharedNote(
            seq=None,
            problem_id="p",
            worker_id="A1",
            type="PROOF_PATCH",
            content="by\n  exact hidden_full_patch",
        ),
    )

    rendered = runner.pull_worker_context(db_path, "p")

    assert "previous candidate patch submitted" in rendered
    assert "hidden_full_patch" not in rendered


def test_request_context_pulls_again(monkeypatch) -> None:
    calls: list[str] = []

    def fake_pull_worker_context(db_path: Path, problem_id: str) -> str:
        calls.append(problem_id)
        return f"context version {len(calls)}"

    monkeypatch.setattr(runner, "pull_worker_context", fake_pull_worker_context)
    state = {"history": ""}

    latest = runner.handle_request_context(Path("db.sqlite"), "p", state)

    assert latest == "context version 1"
    assert calls == ["p"]
    assert "context version 1" in state["history"]


def test_build_step_prompt_mentions_request_context() -> None:
    problem = SimpleNamespace(
        problem_id="p",
        theorem_name="thm",
        source="theorem thm : True := sorry",
        docstring=None,
    )

    prompt = runner.build_step_prompt(
        problem=problem,
        worker_id="A1",
        step_index=1,
        current_context_text="compact context",
        worker_history_text="",
    )

    assert "REQUEST_CONTEXT" in prompt
    assert "compact context" in prompt
    assert "not a Main Agent" in prompt


def test_empty_submit_note_is_ignored_without_db_write(tmp_path: Path) -> None:
    db_path = tmp_path / "context.sqlite"
    init_db(db_path)
    states = _states()
    action = InteractiveWorkerAction(
        action="SUBMIT_NOTE",
        summary="empty",
        proof_patch=None,
        note_type="CLAIM",
        note_content="   ",
        raw_text="",
    )

    result = runner.handle_action(
        action=action,
        worker_id="A1",
        step_index=1,
        states=states,
        putnam_root=Path("data/PutnamBench"),
        db_path=db_path,
        run_dir=tmp_path / "run",
        problem=_problem(),
        budget=runner.TimeBudget(0),
        lean_timeout_sec=180,
    )

    assert result["event"] == "SUBMIT_NOTE_EMPTY_IGNORED"
    assert states["A1"]["actions"] == ["SUBMIT_NOTE", "SUBMIT_NOTE_EMPTY_IGNORED"]
    assert states["A1"]["malformed_action_count"] == 1
    assert "empty SUBMIT_NOTE ignored" in states["A1"]["last_error"]
    assert get_notes(db_path, problem_id="p") == []


def test_invalid_submit_note_type_is_ignored(tmp_path: Path) -> None:
    db_path = tmp_path / "context.sqlite"
    init_db(db_path)
    states = _states()
    action = InteractiveWorkerAction(
        action="SUBMIT_NOTE",
        summary="bad type",
        proof_patch=None,
        note_type="VERIFIED_PATCH",
        note_content="claim",
        raw_text="",
    )

    result = runner.handle_action(
        action=action,
        worker_id="A1",
        step_index=1,
        states=states,
        putnam_root=Path("data/PutnamBench"),
        db_path=db_path,
        run_dir=tmp_path / "run",
        problem=_problem(),
        budget=runner.TimeBudget(0),
        lean_timeout_sec=180,
    )

    assert result["event"] == "SUBMIT_NOTE_INVALID_TYPE_IGNORED"
    assert get_notes(db_path, problem_id="p") == []


def test_empty_submit_patch_is_ignored_without_submit_call(monkeypatch, tmp_path: Path) -> None:
    def fail_submit_patch(**kwargs):
        raise AssertionError("submit_patch should not be called for an empty patch")

    monkeypatch.setattr(runner, "submit_patch", fail_submit_patch)
    states = _states()
    action = InteractiveWorkerAction(
        action="SUBMIT_PATCH",
        summary="empty patch",
        proof_patch=" \n ",
        note_type=None,
        note_content=None,
        raw_text="",
    )

    result = runner.handle_action(
        action=action,
        worker_id="A1",
        step_index=1,
        states=states,
        putnam_root=Path("data/PutnamBench"),
        db_path=tmp_path / "context.sqlite",
        run_dir=tmp_path / "run",
        problem=_problem(),
        budget=runner.TimeBudget(0),
        lean_timeout_sec=180,
    )

    assert result["event"] == "SUBMIT_PATCH_EMPTY_IGNORED"
    assert states["A1"]["actions"] == ["SUBMIT_PATCH", "SUBMIT_PATCH_EMPTY_IGNORED"]
    assert states["A1"]["malformed_action_count"] == 1


def test_normal_submit_note_still_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "context.sqlite"
    init_db(db_path)
    states = _states()
    action = InteractiveWorkerAction(
        action="SUBMIT_NOTE",
        summary="claim",
        proof_patch=None,
        note_type="CLAIM",
        note_content="  A short useful claim.  ",
        raw_text="",
    )

    result = runner.handle_action(
        action=action,
        worker_id="A1",
        step_index=1,
        states=states,
        putnam_root=Path("data/PutnamBench"),
        db_path=db_path,
        run_dir=tmp_path / "run",
        problem=_problem(),
        budget=runner.TimeBudget(0),
        lean_timeout_sec=180,
    )

    notes = get_notes(db_path, problem_id="p")
    assert result["note_seq"] == notes[0].seq
    assert notes[0].type == "CLAIM"
    assert notes[0].content == "A short useful claim."


def test_normal_submit_patch_keeps_old_behavior(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_submit_patch(**kwargs):
        calls.append(kwargs)
        return PatchSubmissionResult(
            problem_id=kwargs["problem_id"],
            worker_id=kwargs["worker_id"],
            proof_patch_seq=1,
            lean_error_seq=2,
            verified_patch_seq=None,
            final_proof_seq=None,
            attempt_path=str(tmp_path / "attempt.lean"),
            success=False,
            check_status="checked by Lean and failed",
            returncode=1,
            elapsed_sec=0.25,
            error_messages=["failed"],
        )

    monkeypatch.setattr(runner, "submit_patch", fake_submit_patch)
    states = _states()
    action = InteractiveWorkerAction(
        action="SUBMIT_PATCH",
        summary="candidate",
        proof_patch="  by\n  exact True.intro  ",
        note_type=None,
        note_content=None,
        raw_text="",
    )

    result = runner.handle_action(
        action=action,
        worker_id="A1",
        step_index=1,
        states=states,
        putnam_root=Path("data/PutnamBench"),
        db_path=tmp_path / "context.sqlite",
        run_dir=tmp_path / "run",
        problem=_problem(),
        budget=runner.TimeBudget(0),
        lean_timeout_sec=180,
    )

    assert calls
    assert calls[0]["proof_patch"] == "by\n  exact True.intro"
    assert result["success"] is False
    assert result["submit_result"]["check_status"] == "checked by Lean and failed"


def _states() -> dict:
    return {
        "A1": {
            "active": True,
            "history": "",
            "actions": [],
            "last_check_status": None,
            "last_error": None,
            "malformed_action_count": 0,
        }
    }


def _problem() -> SimpleNamespace:
    return SimpleNamespace(problem_id="p")
