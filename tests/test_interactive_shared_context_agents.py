import json
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


def test_request_context_limit_ignores_repeated_request(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_pull_worker_context(db_path: Path, problem_id: str) -> str:
        calls.append(problem_id)
        return "full context should appear once"

    monkeypatch.setattr(runner, "pull_worker_context", fake_pull_worker_context)
    logger = runner.EventLogger(
        run_dir=tmp_path / "run",
        budget=runner.TimeBudget(0),
        progress=False,
        problem_id="p",
    )
    states = _states()
    action = InteractiveWorkerAction(
        action="REQUEST_CONTEXT",
        summary="again",
        proof_patch=None,
        note_type=None,
        note_content=None,
        raw_text="",
    )

    first = runner.handle_action(
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
        max_context_requests_per_worker=1,
        event_logger=logger,
    )
    second = runner.handle_action(
        action=action,
        worker_id="A1",
        step_index=2,
        states=states,
        putnam_root=Path("data/PutnamBench"),
        db_path=tmp_path / "context.sqlite",
        run_dir=tmp_path / "run",
        problem=_problem(),
        budget=runner.TimeBudget(0),
        lean_timeout_sec=180,
        max_context_requests_per_worker=1,
        event_logger=logger,
    )
    logger.close()

    event_names = [
        json.loads(line)["event"]
        for line in (tmp_path / "run" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert first["context_request_count"] == 1
    assert second["ignored"] is True
    assert second["reason"] == "context_request_limit_exceeded"
    assert calls == ["p"]
    assert states["A1"]["context_request_count"] == 2
    assert states["A1"]["context_request_limit_exceeded_count"] == 1
    assert states["A1"]["history"].count("full context should appear once") == 1
    assert "must choose SUBMIT_NOTE, SUBMIT_PATCH, or STOP" in states["A1"]["history"]
    assert "request_context_ignored" in event_names


def test_request_context_limit_zero_blocks_first_request(monkeypatch, tmp_path: Path) -> None:
    def fail_pull_worker_context(db_path: Path, problem_id: str) -> str:
        raise AssertionError("context should not be pulled when limit is zero")

    monkeypatch.setattr(runner, "pull_worker_context", fail_pull_worker_context)
    states = _states()
    action = InteractiveWorkerAction(
        action="REQUEST_CONTEXT",
        summary="first",
        proof_patch=None,
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
        max_context_requests_per_worker=0,
    )

    assert result["ignored"] is True
    assert states["A1"]["context_request_count"] == 1
    assert states["A1"]["context_request_limit_exceeded_count"] == 1


def test_negative_request_context_limit_allows_old_behavior(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_pull_worker_context(db_path: Path, problem_id: str) -> str:
        calls.append(problem_id)
        return f"context {len(calls)}"

    monkeypatch.setattr(runner, "pull_worker_context", fake_pull_worker_context)
    states = _states()
    action = InteractiveWorkerAction(
        action="REQUEST_CONTEXT",
        summary="again",
        proof_patch=None,
        note_type=None,
        note_content=None,
        raw_text="",
    )

    for step in (1, 2):
        runner.handle_action(
            action=action,
            worker_id="A1",
            step_index=step,
            states=states,
            putnam_root=Path("data/PutnamBench"),
            db_path=tmp_path / "context.sqlite",
            run_dir=tmp_path / "run",
            problem=_problem(),
            budget=runner.TimeBudget(0),
            lean_timeout_sec=180,
            max_context_requests_per_worker=-1,
        )

    assert calls == ["p", "p"]
    assert states["A1"]["context_request_count"] == 2
    assert states["A1"]["context_request_limit_exceeded_count"] == 0


def test_build_step_prompt_legacy_keeps_context_out_of_default_prompt() -> None:
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
        worker_state={
            "actions": ["SUBMIT_PATCH"],
            "context_request_count": 0,
            "patch_attempt_count": 1,
            "last_check_status": "checked by Lean and failed",
            "last_error": "failed tactic",
        },
        max_context_requests_per_worker=2,
    )

    assert "compact context" in prompt
    assert "not a Main Agent" in prompt
    assert "primary goal is to submit a Lean proof patch" in prompt
    assert "Prefer SUBMIT_PATCH when you have a concrete proof idea" in prompt
    assert "Use SUBMIT_NOTE only for concise, non-empty" in prompt
    assert "action: REQUEST_CONTEXT" not in prompt
    assert "Shared-context policy:" not in prompt
    assert "Good reasons to REQUEST_CONTEXT" not in prompt
    assert "patch_attempt_count=1" not in prompt
    assert "Proof patches must not contain `sorry`, `admit`, or `axiom`" in prompt


def test_build_step_prompt_balanced_policy_is_explicit_opt_in() -> None:
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
        worker_state={
            "actions": ["SUBMIT_PATCH"],
            "context_request_count": 0,
            "patch_attempt_count": 1,
            "last_check_status": "checked by Lean and failed",
            "last_error": "failed tactic",
        },
        max_context_requests_per_worker=2,
        shared_context_policy="balanced",
    )

    assert "action: REQUEST_CONTEXT" in prompt
    assert "REQUEST_CONTEXT is a repair aid, not a mandatory action" in prompt
    assert "Good reasons to REQUEST_CONTEXT" in prompt
    assert "Bad reasons to REQUEST_CONTEXT" in prompt
    assert "patch_attempt_count=1" in prompt


def test_build_step_prompt_disables_request_context_when_limit_zero() -> None:
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
        max_context_requests_per_worker=0,
    )

    assert "action: REQUEST_CONTEXT" not in prompt
    assert "REQUEST_CONTEXT" not in prompt
    assert "action: SUBMIT_PATCH" in prompt


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


def test_submit_patch_skips_lean_when_remaining_time_too_low(monkeypatch, tmp_path: Path) -> None:
    def fail_submit_patch(**kwargs):
        raise AssertionError("submit_patch should not be called with too little time")

    monkeypatch.setattr(runner, "submit_patch", fail_submit_patch)
    budget = runner.TimeBudget(1)
    budget.start -= 0.9
    states = _states()
    logger = runner.EventLogger(
        run_dir=tmp_path / "run",
        budget=budget,
        progress=False,
        problem_id="p",
    )
    action = InteractiveWorkerAction(
        action="SUBMIT_PATCH",
        summary="candidate",
        proof_patch="by\n  trivial",
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
        budget=budget,
        lean_timeout_sec=180,
        min_remaining_sec_to_start_call=30,
        event_logger=logger,
    )
    logger.close()
    event_names = [
        json.loads(line)["event"]
        for line in (tmp_path / "run" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert result["stopped_reason"] == "time_budget_exceeded"
    assert result["skipped"] is True
    assert result["reason"] == "insufficient_remaining_time_for_lean"
    assert "time_budget_low" in event_names


def test_submit_patch_min_remaining_zero_keeps_old_behavior(monkeypatch, tmp_path: Path) -> None:
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
            elapsed_sec=0.1,
            error_messages=["failed"],
        )

    monkeypatch.setattr(runner, "submit_patch", fake_submit_patch)
    budget = runner.TimeBudget(1)
    budget.start -= 0.9
    action = InteractiveWorkerAction(
        action="SUBMIT_PATCH",
        summary="candidate",
        proof_patch="by\n  trivial",
        note_type=None,
        note_content=None,
        raw_text="",
    )

    result = runner.handle_action(
        action=action,
        worker_id="A1",
        step_index=1,
        states=_states(),
        putnam_root=Path("data/PutnamBench"),
        db_path=tmp_path / "context.sqlite",
        run_dir=tmp_path / "run",
        problem=_problem(),
        budget=budget,
        lean_timeout_sec=180,
        min_remaining_sec_to_start_call=0,
    )

    assert calls
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
            "context_request_count": 0,
            "context_request_limit_exceeded_count": 0,
            "patch_attempt_count": 0,
        }
    }


def _problem() -> SimpleNamespace:
    return SimpleNamespace(problem_id="p")
