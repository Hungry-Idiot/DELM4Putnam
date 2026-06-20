import json
import time
from pathlib import Path
from types import SimpleNamespace

import scripts.experiments.run_interactive_shared_context_agents as runner
from src.codex.parser import InteractiveWorkerAction
from src.controller.patch_service import PatchSubmissionResult
from src.shared_context.storage import get_notes, init_db


def test_event_logger_writes_valid_jsonl(tmp_path: Path) -> None:
    budget = runner.TimeBudget(0)
    logger = runner.EventLogger(
        run_dir=tmp_path,
        budget=budget,
        progress=False,
        problem_id="p",
    )

    logger.emit("run_start", step=1)
    logger.emit("run_end", success=False, stopped_reason="max_steps_reached")
    logger.close()

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event"] for line in lines] == ["run_start", "run_end"]
    assert all(isinstance(json.loads(line)["elapsed_sec"], float) for line in lines)


def test_progress_prints_key_events(tmp_path: Path, capsys) -> None:
    logger = runner.EventLogger(
        run_dir=tmp_path,
        budget=runner.TimeBudget(0),
        progress=True,
        problem_id="p",
    )

    logger.emit(
        "worker_return",
        step=1,
        worker_id="A1",
        action="SUBMIT_NOTE",
        remaining_sec=12.3,
    )
    logger.close()

    output = capsys.readouterr().out
    assert "worker_return" in output
    assert "step=1" in output
    assert "worker=A1" in output
    assert "action=SUBMIT_NOTE" in output


def test_dry_run_does_not_write_events(monkeypatch, tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    db_path = tmp_path / "context.sqlite"
    args = SimpleNamespace(
        putnam_root="data/PutnamBench",
        problem_id="p",
        num_workers=1,
        max_steps=1,
        max_concurrency=None,
        db=str(db_path),
        run_dir=str(run_dir),
        codex_command="codex",
        provider="relay",
        model=None,
        base_url=None,
        api_key_env="RELAY_API_KEY",
        use_api_key=False,
        codex_timeout_sec=1,
        lean_timeout_sec=1,
        time_budget_sec=0,
        progress=True,
        heartbeat_sec=30,
        fresh=False,
        dry_run=True,
    )
    problem = SimpleNamespace(
        problem_id="p",
        theorem_name="thm",
        source="theorem thm : True := by trivial",
        docstring=None,
        relative_path="p.lean",
    )
    monkeypatch.setattr(runner, "parse_args", lambda: args)
    monkeypatch.setattr(runner, "find_problem_by_id", lambda putnam_root, problem_id: problem)

    assert runner.main() == 0
    assert not (run_dir / "events.jsonl").exists()


def test_submit_note_events_record_admitted_and_ignored(tmp_path: Path) -> None:
    db_path = tmp_path / "context.sqlite"
    init_db(db_path)
    logger = runner.EventLogger(
        run_dir=tmp_path / "run",
        budget=runner.TimeBudget(0),
        progress=False,
        problem_id="p",
    )
    states = _states()

    admitted = InteractiveWorkerAction(
        action="SUBMIT_NOTE",
        summary="claim",
        proof_patch=None,
        note_type="CLAIM",
        note_content="nonempty claim",
        raw_text="",
    )
    ignored = InteractiveWorkerAction(
        action="SUBMIT_NOTE",
        summary="empty",
        proof_patch=None,
        note_type="CLAIM",
        note_content=" ",
        raw_text="",
    )

    runner.handle_action(
        action=admitted,
        worker_id="A1",
        step_index=1,
        states=states,
        putnam_root=Path("data/PutnamBench"),
        db_path=db_path,
        run_dir=tmp_path / "run",
        problem=_problem(),
        budget=runner.TimeBudget(0),
        lean_timeout_sec=180,
        event_logger=logger,
    )
    runner.handle_action(
        action=ignored,
        worker_id="A1",
        step_index=2,
        states=states,
        putnam_root=Path("data/PutnamBench"),
        db_path=db_path,
        run_dir=tmp_path / "run",
        problem=_problem(),
        budget=runner.TimeBudget(0),
        lean_timeout_sec=180,
        event_logger=logger,
    )
    logger.close()

    events = _events(tmp_path / "run")
    assert "note_admitted" in [event["event"] for event in events]
    assert "note_ignored" in [event["event"] for event in events]
    assert len(get_notes(db_path, problem_id="p")) == 1


def test_submit_patch_events_wrap_fake_submit(monkeypatch, tmp_path: Path) -> None:
    logger = runner.EventLogger(
        run_dir=tmp_path / "run",
        budget=runner.TimeBudget(0),
        progress=False,
        problem_id="p",
    )

    def fake_submit_patch(**kwargs):
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

    result = runner.handle_action(
        action=InteractiveWorkerAction(
            action="SUBMIT_PATCH",
            summary="candidate",
            proof_patch="by\n  trivial",
            note_type=None,
            note_content=None,
            raw_text="",
        ),
        worker_id="A1",
        step_index=1,
        states=_states(),
        putnam_root=Path("data/PutnamBench"),
        db_path=tmp_path / "context.sqlite",
        run_dir=tmp_path / "run",
        problem=_problem(),
        budget=runner.TimeBudget(0),
        lean_timeout_sec=180,
        event_logger=logger,
    )
    logger.close()

    assert result["submit_result"]["check_status"] == "checked by Lean and failed"
    event_names = [event["event"] for event in _events(tmp_path / "run")]
    assert "patch_submit_start" in event_names
    assert "patch_submit_end" in event_names


def test_scheduler_emits_heartbeat_with_fake_workers(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "context.sqlite"
    init_db(db_path)

    def fake_run_worker_step(**kwargs):
        time.sleep(0.05)
        worker_id = kwargs["work_dir"].parents[1].name
        return worker_id, InteractiveWorkerAction(
            action="REQUEST_CONTEXT",
            summary="need latest",
            proof_patch=None,
            note_type=None,
            note_content=None,
            raw_text="",
        )

    monkeypatch.setattr(runner, "run_worker_step", fake_run_worker_step)
    summary = runner.run_scheduler(
        args=SimpleNamespace(
            max_steps=1,
            max_concurrency=2,
            codex_timeout_sec=1,
            lean_timeout_sec=1,
            time_budget_sec=0,
            progress=False,
            heartbeat_sec=0.01,
        ),
        api_key=None,
        budget=runner.TimeBudget(0),
        putnam_root=Path("data/PutnamBench"),
        db_path=db_path,
        run_dir=tmp_path / "run",
        problem=SimpleNamespace(
            problem_id="p",
            theorem_name="thm",
            source="theorem thm : True := by trivial",
            docstring=None,
        ),
        worker_ids=["A1", "A2"],
    )

    events = _events(tmp_path / "run")
    assert summary["event_counts"]["heartbeat"] >= 1
    assert "heartbeat" in [event["event"] for event in events]
    assert summary["last_event"] == "run_end"


def test_scheduler_records_keyboard_interrupt(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "context.sqlite"
    init_db(db_path)
    cleanup_calls: list[bool] = []

    def fake_run_worker_step(**kwargs):
        time.sleep(0.01)
        worker_id = kwargs["work_dir"].parents[1].name
        return worker_id, InteractiveWorkerAction(
            action="REQUEST_CONTEXT",
            summary="need latest",
            proof_patch=None,
            note_type=None,
            note_content=None,
            raw_text="",
        )

    def fake_wait_for_worker_futures(*args, **kwargs):
        raise KeyboardInterrupt

    def fake_terminate_active_codex_processes() -> int:
        cleanup_calls.append(True)
        return 2

    monkeypatch.setattr(runner, "run_worker_step", fake_run_worker_step)
    monkeypatch.setattr(runner, "wait_for_worker_futures", fake_wait_for_worker_futures)
    monkeypatch.setattr(
        runner,
        "terminate_active_codex_processes",
        fake_terminate_active_codex_processes,
    )

    summary = runner.run_scheduler(
        args=SimpleNamespace(
            max_steps=1,
            max_concurrency=1,
            codex_timeout_sec=1,
            lean_timeout_sec=1,
            time_budget_sec=0,
            progress=False,
            heartbeat_sec=30,
        ),
        api_key=None,
        budget=runner.TimeBudget(0),
        putnam_root=Path("data/PutnamBench"),
        db_path=db_path,
        run_dir=tmp_path / "run",
        problem=SimpleNamespace(
            problem_id="p",
            theorem_name="thm",
            source="theorem thm : True := by trivial",
            docstring=None,
        ),
        worker_ids=["A1"],
    )

    events = _events(tmp_path / "run")
    assert cleanup_calls == [True]
    assert summary["stopped_reason"] == "keyboard_interrupt"
    assert summary["interrupted"] is True
    assert summary["terminated_codex_processes"] == 2
    assert "keyboard_interrupt" in [event["event"] for event in events]
    assert events[-1]["event"] == "run_end"


def test_scheduler_does_not_launch_codex_when_remaining_time_too_low(
    monkeypatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "context.sqlite"
    init_db(db_path)

    def fail_run_worker_step(**kwargs):
        raise AssertionError("Codex worker should not launch with too little time")

    monkeypatch.setattr(runner, "run_worker_step", fail_run_worker_step)
    budget = runner.TimeBudget(1)
    budget.start -= 0.9

    summary = runner.run_scheduler(
        args=SimpleNamespace(
            max_steps=1,
            max_concurrency=1,
            codex_timeout_sec=1,
            lean_timeout_sec=1,
            time_budget_sec=1,
            progress=False,
            heartbeat_sec=30,
            max_context_requests_per_worker=1,
            min_remaining_sec_to_start_call=30,
        ),
        api_key=None,
        budget=budget,
        putnam_root=Path("data/PutnamBench"),
        db_path=db_path,
        run_dir=tmp_path / "run",
        problem=SimpleNamespace(
            problem_id="p",
            theorem_name="thm",
            source="theorem thm : True := by trivial",
            docstring=None,
        ),
        worker_ids=["A1"],
    )

    events = _events(tmp_path / "run")
    assert summary["total_codex_calls"] == 0
    assert summary["stopped_reason"] == "time_budget_exceeded"
    assert summary["min_remaining_sec_to_start_call"] == 30
    assert summary["time_budget_low_count"] == 1
    assert "time_budget_low" in [event["event"] for event in events]


def test_scheduler_min_remaining_zero_allows_codex_launch(
    monkeypatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "context.sqlite"
    init_db(db_path)
    calls: list[str] = []

    def fake_run_worker_step(**kwargs):
        worker_id = kwargs["work_dir"].parents[1].name
        calls.append(worker_id)
        return worker_id, InteractiveWorkerAction(
            action="STOP",
            summary="done",
            proof_patch=None,
            note_type=None,
            note_content=None,
            raw_text="",
        )

    monkeypatch.setattr(runner, "run_worker_step", fake_run_worker_step)
    budget = runner.TimeBudget(1)
    budget.start -= 0.9

    summary = runner.run_scheduler(
        args=SimpleNamespace(
            max_steps=1,
            max_concurrency=1,
            codex_timeout_sec=1,
            lean_timeout_sec=1,
            time_budget_sec=1,
            progress=False,
            heartbeat_sec=30,
            max_context_requests_per_worker=1,
            min_remaining_sec_to_start_call=0,
        ),
        api_key=None,
        budget=budget,
        putnam_root=Path("data/PutnamBench"),
        db_path=db_path,
        run_dir=tmp_path / "run",
        problem=SimpleNamespace(
            problem_id="p",
            theorem_name="thm",
            source="theorem thm : True := by trivial",
            docstring=None,
        ),
        worker_ids=["A1"],
    )

    assert calls == ["A1"]
    assert summary["total_codex_calls"] == 1
    assert summary["time_budget_low_count"] == 0


def _events(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]


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
        }
    }


def _problem() -> SimpleNamespace:
    return SimpleNamespace(problem_id="p")
