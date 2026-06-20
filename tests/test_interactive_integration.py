import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import scripts.experiments.run_interactive_shared_context_agents as runner
from src.codex.parser import InteractiveWorkerAction
from src.controller.patch_service import PatchSubmissionResult
from src.shared_context.storage import get_notes, init_db


def test_summary_matches_events_for_fake_note_run(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "context.sqlite"
    run_dir = tmp_path / "run"
    init_db(db_path)

    def fake_run_worker_step(**kwargs):
        worker_id = kwargs["work_dir"].parents[1].name
        return worker_id, InteractiveWorkerAction(
            action="SUBMIT_NOTE",
            summary="record claim",
            proof_patch=None,
            note_type="CLAIM",
            note_content="Useful claim.",
            raw_text="",
        )

    monkeypatch.setattr(runner, "run_worker_step", fake_run_worker_step)

    summary = runner.run_scheduler(
        args=_args(max_steps=1),
        api_key=None,
        budget=runner.TimeBudget(0),
        putnam_root=Path("data/PutnamBench"),
        db_path=db_path,
        run_dir=run_dir,
        problem=_problem(),
        worker_ids=["A1"],
    )

    events = _events(run_dir)
    event_counts = Counter(event["event"] for event in events)
    notes = get_notes(db_path, problem_id="p")

    assert Path(summary["events_path"]) == run_dir / "events.jsonl"
    assert summary["last_event"] == "run_end"
    assert summary["event_counts"] == dict(event_counts)
    assert summary["time_budget_low_count"] == event_counts.get("time_budget_low", 0)
    assert summary["context_request_limit_exceeded_count"] == 0
    assert events[0]["event"] == "run_start"
    assert events[-1]["event"] == "run_end"
    assert {"worker_launch", "worker_return", "note_admitted", "action_handled"} <= set(event_counts)
    assert notes[0].type == "CLAIM"
    assert notes[0].content == "Useful claim."
    assert summary["workers"]["A1"]["actions"] == ["SUBMIT_NOTE"]
    assert summary["workers"]["A1"]["context_request_count"] == 0
    assert summary["patch_attempt_count"] == 0
    assert summary["workers_with_patch_attempt"] == 0
    assert summary["workers_stopped_without_patch"] == 0
    assert summary["worker_action_counts"]["A1"]["SUBMIT_NOTE"] == 1
    assert _worker_summary_has_required_fields(summary["workers"]["A1"])


def test_context_request_limit_summary_and_events(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "context.sqlite"
    run_dir = tmp_path / "run"
    init_db(db_path)
    calls = {"A1": 0}

    def fake_pull_worker_context(db_path: Path, problem_id: str) -> str:
        return "compact context version"

    def fake_run_worker_step(**kwargs):
        worker_id = kwargs["work_dir"].parents[1].name
        calls[worker_id] += 1
        return worker_id, InteractiveWorkerAction(
            action="REQUEST_CONTEXT",
            summary="latest please",
            proof_patch=None,
            note_type=None,
            note_content=None,
            raw_text="",
        )

    monkeypatch.setattr(runner, "pull_worker_context", fake_pull_worker_context)
    monkeypatch.setattr(runner, "run_worker_step", fake_run_worker_step)

    summary = runner.run_scheduler(
        args=_args(max_steps=2, max_context_requests_per_worker=1),
        api_key=None,
        budget=runner.TimeBudget(0),
        putnam_root=Path("data/PutnamBench"),
        db_path=db_path,
        run_dir=run_dir,
        problem=_problem(),
        worker_ids=["A1"],
    )

    events = _events(run_dir)
    event_names = [event["event"] for event in events]

    assert event_names.count("request_context_handled") == 1
    assert event_names.count("request_context_ignored") == 1
    assert events[-1]["event"] == "run_end"
    assert summary["workers"]["A1"]["context_request_count"] == 2
    assert summary["workers"]["A1"]["context_request_limit_exceeded_count"] == 1
    assert summary["context_request_limit_exceeded_count"] == 1
    assert summary["event_counts"]["request_context_ignored"] == 1


def test_fake_patch_run_has_paired_patch_events(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "context.sqlite"
    run_dir = tmp_path / "run"
    init_db(db_path)
    submit_calls: list[dict] = []
    worker_calls = {"A1": 0}

    def fake_run_worker_step(**kwargs):
        worker_id = kwargs["work_dir"].parents[1].name
        worker_calls[worker_id] += 1
        if worker_calls[worker_id] > 1:
            return worker_id, InteractiveWorkerAction(
                action="STOP",
                summary="done after patch",
                proof_patch=None,
                note_type=None,
                note_content=None,
                raw_text="",
            )
        return worker_id, InteractiveWorkerAction(
            action="SUBMIT_PATCH",
            summary="candidate",
            proof_patch="by\n  trivial",
            note_type=None,
            note_content=None,
            raw_text="",
        )

    def fake_submit_patch(**kwargs):
        submit_calls.append(kwargs)
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

    monkeypatch.setattr(runner, "run_worker_step", fake_run_worker_step)
    monkeypatch.setattr(runner, "submit_patch", fake_submit_patch)

    summary = runner.run_scheduler(
        args=_args(max_steps=2),
        api_key=None,
        budget=runner.TimeBudget(0),
        putnam_root=Path("data/PutnamBench"),
        db_path=db_path,
        run_dir=run_dir,
        problem=_problem(),
        worker_ids=["A1"],
    )

    event_counts = Counter(event["event"] for event in _events(run_dir))

    assert len(submit_calls) == 1
    assert event_counts["patch_submit_start"] == 1
    assert event_counts["patch_submit_end"] == 1
    assert summary["event_counts"]["patch_submit_start"] == 1
    assert summary["workers"]["A1"]["last_check_status"] == "checked by Lean and failed"
    assert summary["patch_attempt_count"] == 1
    assert summary["workers_with_patch_attempt"] == 1
    assert summary["workers_stopped_without_patch"] == 0
    assert summary["worker_action_counts"]["A1"]["SUBMIT_PATCH"] == 1
    assert summary["worker_action_counts"]["A1"]["STOP"] == 1
    assert summary["workers"]["A1"]["patch_attempt_count"] == 1


def test_summary_diagnostics_count_workers_stopped_without_patch(
    monkeypatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "context.sqlite"
    run_dir = tmp_path / "run"
    init_db(db_path)

    def fake_run_worker_step(**kwargs):
        worker_id = kwargs["work_dir"].parents[1].name
        return worker_id, InteractiveWorkerAction(
            action="STOP",
            summary="no plausible attempt",
            proof_patch=None,
            note_type=None,
            note_content=None,
            raw_text="",
        )

    monkeypatch.setattr(runner, "run_worker_step", fake_run_worker_step)

    summary = runner.run_scheduler(
        args=_args(max_steps=1, max_concurrency=2),
        api_key=None,
        budget=runner.TimeBudget(0),
        putnam_root=Path("data/PutnamBench"),
        db_path=db_path,
        run_dir=run_dir,
        problem=_problem(),
        worker_ids=["A1", "A2"],
    )

    assert summary["patch_attempt_count"] == 0
    assert summary["workers_with_patch_attempt"] == 0
    assert summary["workers_stopped_without_patch"] == 2
    assert summary["worker_action_counts"]["A1"]["STOP"] == 1
    assert summary["worker_action_counts"]["A2"]["STOP"] == 1


def test_low_budget_summary_and_events_consistent(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "context.sqlite"
    run_dir = tmp_path / "run"
    init_db(db_path)

    def fail_run_worker_step(**kwargs):
        raise AssertionError("worker should not launch")

    monkeypatch.setattr(runner, "run_worker_step", fail_run_worker_step)
    budget = runner.TimeBudget(1)
    budget.start -= 0.9

    summary = runner.run_scheduler(
        args=_args(max_steps=1, time_budget_sec=1, min_remaining_sec_to_start_call=30),
        api_key=None,
        budget=budget,
        putnam_root=Path("data/PutnamBench"),
        db_path=db_path,
        run_dir=run_dir,
        problem=_problem(),
        worker_ids=["A1"],
    )

    events = _events(run_dir)
    event_counts = Counter(event["event"] for event in events)

    assert summary["stopped_reason"] == "time_budget_exceeded"
    assert summary["total_codex_calls"] == 0
    assert event_counts["time_budget_low"] == 1
    assert summary["time_budget_low_count"] == 1
    assert summary["event_counts"] == dict(event_counts)
    assert events[-1]["event"] == "run_end"


def test_event_logger_write_failure_warns_without_raising(tmp_path: Path, capsys) -> None:
    class FailingHandle:
        def write(self, text: str) -> None:
            raise OSError("disk full")

        def flush(self) -> None:
            pass

        def close(self) -> None:
            pass

    logger = runner.EventLogger(
        run_dir=tmp_path,
        budget=runner.TimeBudget(0),
        progress=False,
        problem_id="p",
    )
    logger._handle.close()
    logger._handle = FailingHandle()

    logger.emit("run_start")
    logger.close()

    assert "warning: could not write event" in capsys.readouterr().err


def _events(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def _args(**overrides: object) -> SimpleNamespace:
    values = {
        "max_steps": 1,
        "max_concurrency": 1,
        "codex_timeout_sec": 1,
        "lean_timeout_sec": 1,
        "time_budget_sec": 0,
        "progress": False,
        "heartbeat_sec": 30,
        "max_context_requests_per_worker": 1,
        "min_remaining_sec_to_start_call": 30,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _problem() -> SimpleNamespace:
    return SimpleNamespace(
        problem_id="p",
        theorem_name="thm",
        source="theorem thm : True := by trivial",
        docstring=None,
    )


def _worker_summary_has_required_fields(worker: dict) -> bool:
    return {
        "actions",
        "active",
        "last_check_status",
        "last_error",
        "malformed_action_count",
        "context_request_count",
        "context_request_limit_exceeded_count",
        "patch_attempt_count",
    } <= set(worker)
