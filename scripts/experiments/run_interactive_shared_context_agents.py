from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TextIO

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args: object, **kwargs: object) -> bool:
        return False


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from src.codex.parser import InteractiveWorkerAction, parse_interactive_worker_action
from src.codex.prompts import build_interactive_worker_step_prompt
from src.codex.runner import CodexRunResult, run_codex_exec, terminate_active_codex_processes
from src.controller.patch_service import (
    find_problem_by_id,
    patch_submission_result_to_dict,
    submit_patch,
)
from src.shared_context.renderer import render_worker_context
from src.shared_context.schema import SharedNote
from src.shared_context.storage import append_note, get_notes, init_db


DEFAULT_SUBSET_PATH = Path("outputs/putnam_small_ids.txt")
AGENT_NOTE_TYPES = {"CLAIM", "TACTIC_TRIED", "FAIL", "LEMMA_CANDIDATE"}


class EventLogger:
    def __init__(
        self,
        *,
        run_dir: Path,
        budget: "TimeBudget",
        progress: bool = False,
        heartbeat_sec: float = 30,
        problem_id: str | None = None,
    ) -> None:
        self.budget = budget
        self.progress = progress
        self.heartbeat_sec = float(heartbeat_sec)
        self.problem_id = problem_id
        self.events_path = run_dir / "events.jsonl"
        self.event_counts: dict[str, int] = {}
        self.last_event: str | None = None
        self._last_event_elapsed = budget.elapsed()
        self._handle: TextIO | None = None
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            self._handle = self.events_path.open("w", encoding="utf-8")
        except OSError as exc:
            print(f"warning: could not open events.jsonl: {exc}", file=sys.stderr, flush=True)

    def emit(self, event: str, **fields: object) -> None:
        elapsed = self.budget.elapsed()
        payload: dict[str, object] = {
            "elapsed_sec": round(elapsed, 3),
            "event": event,
        }
        if self.problem_id is not None and "problem_id" not in fields:
            payload["problem_id"] = self.problem_id
        payload.update(fields)
        self.last_event = event
        self.event_counts[event] = self.event_counts.get(event, 0) + 1
        if self._handle is not None:
            try:
                self._handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
                self._handle.flush()
            except (OSError, TypeError) as exc:
                print(f"warning: could not write event {event!r}: {exc}", file=sys.stderr, flush=True)
        if self.progress:
            print(self._format_progress(payload), flush=True)
        self._last_event_elapsed = elapsed

    def seconds_since_last_event(self) -> float:
        return max(0.0, self.budget.elapsed() - self._last_event_elapsed)

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.close()
        except OSError as exc:
            print(f"warning: could not close events.jsonl: {exc}", file=sys.stderr, flush=True)

    def _format_progress(self, payload: dict[str, object]) -> str:
        elapsed = _format_float(payload.get("elapsed_sec"))
        event = str(payload["event"])
        parts = [f"[{elapsed}s]", event]
        for key in (
            "step",
            "worker_id",
            "action",
            "note_type",
            "note_seq",
            "success",
            "status",
            "stopped_reason",
            "reason",
            "terminated_codex_processes",
        ):
            if key in payload and payload[key] is not None:
                label = "worker" if key == "worker_id" else key
                parts.append(f"{label}={_format_progress_value(payload[key])}")
        if "active_workers" in payload:
            parts.append(f"active_workers={_format_progress_value(payload['active_workers'])}")
        if "waiting_workers" in payload:
            parts.append(f"waiting={_format_progress_value(payload['waiting_workers'])}")
        if "done_workers" in payload:
            parts.append(f"done={_format_progress_value(payload['done_workers'])}")
        if "remaining_sec" in payload and payload["remaining_sec"] is not None:
            parts.append(f"remaining={_format_float(payload['remaining_sec'])}s")
        if "elapsed_since_last_event" in payload:
            parts.append(
                f"elapsed_since_last_event={_format_float(payload['elapsed_since_last_event'])}s"
            )
        return " ".join(parts)


def _format_progress_value(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item) for item in value) or "-"
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _format_float(value: object) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return str(value)


def emit_optional(
    event_logger: EventLogger | None,
    event: str,
    **fields: object,
) -> None:
    if event_logger is not None:
        event_logger.emit(event, **fields)


def normalized_min_remaining_sec(value: float | int) -> float:
    return max(0.0, float(value or 0))


def has_enough_time_to_start_call(
    budget: "TimeBudget",
    min_remaining_sec: float | int,
) -> bool:
    remaining = budget.remaining()
    if remaining is None:
        return True
    return remaining >= normalized_min_remaining_sec(min_remaining_sec)


def context_request_limit_exceeded(count: int, limit: int) -> bool:
    return limit >= 0 and count > limit


def remaining_context_request_budget(count: int, limit: int) -> int | None:
    if limit < 0:
        return None
    return max(0, limit - count)


def main() -> int:
    args = parse_args()
    if args.max_concurrency is None:
        args.max_concurrency = args.num_workers
    if (
        getattr(args, "shared_context_policy", "legacy") == "event_pull"
        and int(getattr(args, "max_context_requests_per_worker", 1)) < 1
    ):
        raise SystemExit(
            "--shared-context-policy event_pull requires "
            "--max-context-requests-per-worker >= 1."
        )

    putnam_root = Path(args.putnam_root)
    db_path = Path(args.db)
    run_dir = Path(args.run_dir)
    if args.fresh:
        reset_run_state(db_path, run_dir)

    problem_id = args.problem_id or default_problem_id(DEFAULT_SUBSET_PATH)
    problem = find_problem_by_id(putnam_root, problem_id)
    init_db(db_path)
    ensure_problem_note(db_path, problem)
    worker_ids = [f"A{i}" for i in range(1, args.num_workers + 1)]
    first_context = pull_worker_context(db_path, problem_id)

    if args.dry_run:
        print("Interactive Shared Context agents dry-run")
        print(f"problem_id: {problem_id}")
        print(f"num_workers: {args.num_workers}")
        print(f"max_steps: {args.max_steps}")
        print(f"max_concurrency: {args.max_concurrency}")
        print(f"provider: {args.provider}")
        print(f"base_url: {args.base_url or '(not set)'}")
        print(f"model: {args.model or '(not set)'}")
        print(f"API key will be read from env var: {args.api_key_env}")
        print(f"time_budget_sec: {args.time_budget_sec:g}")
        print(f"shared_context_policy: {args.shared_context_policy}")
        print(
            f"max_context_requests_per_worker: "
            f"{args.max_context_requests_per_worker}"
        )
        print(f"wall-clock budget mode: {budget_mode(args.time_budget_sec)}")
        if args.time_budget_sec > 0:
            print(f"This run will stop after {args.time_budget_sec:g} seconds unless solved earlier.")
        print("For parallel architecture comparison, use different --run-dir and --db for each architecture to avoid conflicts.")
        print("Shared Context mode: worker")
        if args.shared_context_policy in {"event_pull", "event_pull_soft"}:
            print(
                "Interactive semantics: event_pull policies do not automatically "
                "inject full Shared Context into each prompt. Workers must use "
                "REQUEST_CONTEXT to read failure details."
            )
        else:
            print(
                "Interactive semantics: each worker step pulls latest committed worker "
                "context before prompting; REQUEST_CONTEXT triggers another latest pull "
                "for the next step."
            )
        print()
        print("A1 step 1 prompt")
        print(
            build_step_prompt(
                problem=problem,
                worker_id="A1",
                step_index=1,
                current_context_text=prompt_context_for_policy(
                    first_context, getattr(args, "shared_context_policy", "legacy")
                ),
                worker_history_text="",
                worker_state={
                    "actions": [],
                    "context_request_count": 0,
                    "patch_attempt_count": 0,
                    "last_check_status": None,
                    "last_error": None,
                    "force_context_request": False,
                    "force_context_request_after_step": None,
                    "pending_context_note_seq": None,
                    "last_seen_context_seq": None,
                    "event_context_request_count": 0,
                    "soft_context_available": False,
                    "pending_soft_context_note_seq": None,
                    "soft_context_notice_after_step": None,
                    "soft_context_notice_count": 0,
                },
                max_context_requests_per_worker=getattr(
                    args, "max_context_requests_per_worker", 1
                ),
                shared_context_policy=getattr(args, "shared_context_policy", "legacy"),
            )
        )
        return 0

    api_key = read_api_key(args)
    budget = TimeBudget(args.time_budget_sec)
    summary = run_scheduler(
        args=args,
        api_key=api_key,
        budget=budget,
        putnam_root=putnam_root,
        db_path=db_path,
        run_dir=run_dir,
        problem=problem,
        worker_ids=worker_ids,
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


@dataclass(frozen=True)
class WorkerStepReturn:
    worker_id: str
    action: InteractiveWorkerAction
    codex_result: CodexRunResult | None = None


def run_scheduler(
    *,
    args: argparse.Namespace,
    api_key: str | None,
    budget: "TimeBudget",
    putnam_root: Path,
    db_path: Path,
    run_dir: Path,
    problem: object,
    worker_ids: list[str],
) -> dict[str, Any]:
    states = {
        worker_id: {
            "active": True,
            "history": "",
            "actions": [],
            "last_check_status": None,
            "last_error": None,
            "malformed_action_count": 0,
            "context_request_count": 0,
            "context_request_limit_exceeded_count": 0,
            "patch_attempt_count": 0,
            "force_context_request": False,
            "force_context_request_after_step": None,
            "pending_context_note_seq": None,
            "last_seen_context_seq": None,
            "event_context_request_count": 0,
            "soft_context_available": False,
            "pending_soft_context_note_seq": None,
            "soft_context_notice_after_step": None,
            "soft_context_notice_count": 0,
        }
        for worker_id in worker_ids
    }
    total_codex_calls = 0
    total_lean_elapsed_sec = 0.0
    success = False
    final_verified_patch_seq = None
    final_proof_seq = None
    steps_completed = 0
    stopped_reason: str | None = None
    interrupted = False
    terminated_codex_processes = 0
    min_remaining_sec_to_start_call = normalized_min_remaining_sec(
        getattr(args, "min_remaining_sec_to_start_call", 30)
    )
    max_context_requests_per_worker = int(
        getattr(args, "max_context_requests_per_worker", 1)
    )
    shared_context_policy = getattr(args, "shared_context_policy", "legacy")
    event_logger = EventLogger(
        run_dir=run_dir,
        budget=budget,
        progress=bool(args.progress),
        heartbeat_sec=float(args.heartbeat_sec),
        problem_id=problem.problem_id,
    )
    event_logger.emit(
        "run_start",
        problem_id=problem.problem_id,
        num_workers=len(worker_ids),
        max_steps=args.max_steps,
        max_concurrency=args.max_concurrency,
        time_budget_sec=args.time_budget_sec,
        max_context_requests_per_worker=max_context_requests_per_worker,
        min_remaining_sec_to_start_call=min_remaining_sec_to_start_call,
        shared_context_policy=shared_context_policy,
    )

    try:
        for step_index in range(1, args.max_steps + 1):
            if not budget.can_start_call():
                stopped_reason = "time_budget_exceeded"
                break
            active_workers = [
                worker_id for worker_id in worker_ids if states[worker_id]["active"]
            ]
            if not active_workers or success:
                break
            steps_completed = step_index
            if not has_enough_time_to_start_call(
                budget, min_remaining_sec_to_start_call
            ):
                stopped_reason = "time_budget_exceeded"
                event_logger.emit(
                    "time_budget_low",
                    phase="codex_launch",
                    step=step_index,
                    remaining_sec=budget.remaining(),
                    min_remaining_sec_to_start_call=min_remaining_sec_to_start_call,
                )
                break
            step_codex_timeout = budget.timeout_for(args.codex_timeout_sec)
            if step_codex_timeout <= 0:
                stopped_reason = "time_budget_exceeded"
                break

            event_logger.emit(
                "step_start",
                step=step_index,
                active_workers=active_workers,
                remaining_sec=budget.remaining(),
            )
            step_result = run_scheduler_step(
                args=args,
                api_key=api_key,
                budget=budget,
                putnam_root=putnam_root,
                db_path=db_path,
                run_dir=run_dir,
                problem=problem,
                states=states,
                active_workers=active_workers,
                step_index=step_index,
                step_codex_timeout=step_codex_timeout,
                max_context_requests_per_worker=max_context_requests_per_worker,
                min_remaining_sec_to_start_call=min_remaining_sec_to_start_call,
                shared_context_policy=shared_context_policy,
                event_logger=event_logger,
            )
            total_codex_calls += step_result["codex_calls"]
            total_lean_elapsed_sec += step_result["lean_elapsed_sec"]
            if step_result["success"]:
                success = True
                final_verified_patch_seq = step_result["final_verified_patch_seq"]
                final_proof_seq = step_result["final_proof_seq"]
            stopped_reason = step_result["stopped_reason"] or stopped_reason
            if success or stopped_reason == "time_budget_exceeded":
                break
    except KeyboardInterrupt:
        interrupted = True
        stopped_reason = "keyboard_interrupt"
        event_logger.emit("codex_cleanup_start", reason="keyboard_interrupt")
        terminated_codex_processes = terminate_active_codex_processes()
        event_logger.emit(
            "codex_cleanup_end",
            reason="keyboard_interrupt",
            terminated_codex_processes=terminated_codex_processes,
        )
        event_logger.emit(
            "keyboard_interrupt",
            terminated_codex_processes=terminated_codex_processes,
        )

    if stopped_reason is None:
        stopped_reason = interactive_stopped_reason(
            success=success,
            budget_exhausted=budget.exhausted(),
            steps_completed=steps_completed,
            max_steps=args.max_steps,
            any_active=any(states[worker_id]["active"] for worker_id in worker_ids),
        )
    event_logger.emit(
        "run_end",
        success=success,
        stopped_reason=stopped_reason,
        steps_completed=steps_completed,
        wall_elapsed_sec=budget.elapsed(),
    )
    summary = {
        "problem_id": problem.problem_id,
        "num_workers": len(worker_ids),
        "max_steps": args.max_steps,
        "max_concurrency": args.max_concurrency,
        "steps_completed": steps_completed,
        "success": success,
        "workers": {
            worker_id: {
                "actions": states[worker_id]["actions"],
                "active": states[worker_id]["active"],
                "last_check_status": states[worker_id]["last_check_status"],
                "last_error": states[worker_id]["last_error"],
                "malformed_action_count": states[worker_id]["malformed_action_count"],
                "context_request_count": states[worker_id]["context_request_count"],
                "context_request_limit_exceeded_count": states[worker_id][
                    "context_request_limit_exceeded_count"
                ],
                "patch_attempt_count": states[worker_id]["patch_attempt_count"],
                "force_context_request": states[worker_id]["force_context_request"],
                "force_context_request_after_step": states[worker_id][
                    "force_context_request_after_step"
                ],
                "pending_context_note_seq": states[worker_id][
                    "pending_context_note_seq"
                ],
                "last_seen_context_seq": states[worker_id]["last_seen_context_seq"],
                "event_context_request_count": states[worker_id][
                    "event_context_request_count"
                ],
                "soft_context_available": states[worker_id]["soft_context_available"],
                "pending_soft_context_note_seq": states[worker_id][
                    "pending_soft_context_note_seq"
                ],
                "soft_context_notice_after_step": states[worker_id][
                    "soft_context_notice_after_step"
                ],
                "soft_context_notice_count": states[worker_id][
                    "soft_context_notice_count"
                ],
            }
            for worker_id in worker_ids
        },
        "worker_action_counts": {
            worker_id: dict(Counter(str(action) for action in states[worker_id]["actions"]))
            for worker_id in worker_ids
        },
        "patch_attempt_count": sum(
            int(states[worker_id]["patch_attempt_count"])
            for worker_id in worker_ids
        ),
        "workers_with_patch_attempt": sum(
            1
            for worker_id in worker_ids
            if int(states[worker_id]["patch_attempt_count"]) > 0
        ),
        "workers_stopped_without_patch": sum(
            1
            for worker_id in worker_ids
            if not states[worker_id]["active"]
            and int(states[worker_id]["patch_attempt_count"]) == 0
        ),
        "malformed_action_count": sum(
            int(states[worker_id]["malformed_action_count"])
            for worker_id in worker_ids
        ),
        "context_request_limit_exceeded_count": sum(
            int(states[worker_id]["context_request_limit_exceeded_count"])
            for worker_id in worker_ids
        ),
        "time_budget_low_count": event_logger.event_counts.get("time_budget_low", 0),
        "failure_note_count": event_logger.event_counts.get("failure_note_admitted", 0),
        "event_context_pull_required_count": event_logger.event_counts.get(
            "event_context_pull_required", 0
        ),
        "event_context_pull_handled_count": event_logger.event_counts.get(
            "event_context_pull_handled", 0
        ),
        "forced_context_request_missed_count": event_logger.event_counts.get(
            "forced_context_request_missed", 0
        ),
        "soft_context_pull_available_count": event_logger.event_counts.get(
            "soft_context_pull_available", 0
        ),
        "soft_context_pull_handled_count": event_logger.event_counts.get(
            "soft_context_pull_handled", 0
        ),
        "soft_context_pull_bypassed_count": event_logger.event_counts.get(
            "soft_context_pull_declined_or_bypassed", 0
        ),
        "soft_context_pull_skipped_due_to_limit_count": event_logger.event_counts.get(
            "soft_context_pull_skipped_due_to_limit", 0
        ),
        "soft_context_pull_skipped_due_to_budget_count": event_logger.event_counts.get(
            "soft_context_pull_skipped_due_to_budget", 0
        ),
        "total_event_context_requests": sum(
            int(states[worker_id]["event_context_request_count"])
            for worker_id in worker_ids
        ),
        "total_codex_calls": total_codex_calls,
        "total_lean_elapsed_sec": total_lean_elapsed_sec,
        "final_verified_patch_seq": final_verified_patch_seq,
        "final_proof_seq": final_proof_seq,
        "time_budget_sec": args.time_budget_sec,
        "wall_elapsed_sec": budget.elapsed(),
        "time_to_success_sec": budget.elapsed() if success else None,
        "stopped_reason": stopped_reason,
        "interrupted": interrupted,
        "terminated_codex_processes": terminated_codex_processes,
        "budget_exhausted": budget.exhausted() and not success,
        "events_path": str(event_logger.events_path),
        "progress_enabled": bool(args.progress),
        "heartbeat_sec": args.heartbeat_sec,
        "max_context_requests_per_worker": max_context_requests_per_worker,
        "min_remaining_sec_to_start_call": min_remaining_sec_to_start_call,
        "shared_context_policy": shared_context_policy,
        "last_event": event_logger.last_event,
        "event_counts": dict(event_logger.event_counts),
    }
    event_logger.close()
    return summary


def run_scheduler_step(
    *,
    args: argparse.Namespace,
    api_key: str | None,
    budget: "TimeBudget",
    putnam_root: Path,
    db_path: Path,
    run_dir: Path,
    problem: object,
    states: dict[str, dict[str, Any]],
    active_workers: list[str],
    step_index: int,
    step_codex_timeout: float,
    max_context_requests_per_worker: int,
    min_remaining_sec_to_start_call: float,
    shared_context_policy: str,
    event_logger: EventLogger,
) -> dict[str, Any]:
    total_codex_calls = 0
    total_lean_elapsed_sec = 0.0
    success = False
    final_verified_patch_seq = None
    final_proof_seq = None
    stopped_reason: str | None = None
    future_to_worker: dict[Future[WorkerStepReturn], str] = {}
    done_workers: set[str] = set()
    pending: set[Future[WorkerStepReturn]] = set()
    pool = ThreadPoolExecutor(max_workers=args.max_concurrency)
    interrupted = False
    try:
        for worker_id in active_workers:
            if not has_enough_time_to_start_call(
                budget, min_remaining_sec_to_start_call
            ):
                stopped_reason = "time_budget_exceeded"
                event_logger.emit(
                    "time_budget_low",
                    phase="codex_launch",
                    step=step_index,
                    worker_id=worker_id,
                    remaining_sec=budget.remaining(),
                    min_remaining_sec_to_start_call=min_remaining_sec_to_start_call,
                )
                break
            context_text = prompt_context_for_policy(
                pull_worker_context(db_path, problem.problem_id),
                shared_context_policy,
            )
            prompt = build_step_prompt(
                problem=problem,
                worker_id=worker_id,
                step_index=step_index,
                current_context_text=context_text,
                worker_history_text=str(states[worker_id]["history"]),
                worker_state=states[worker_id],
                max_context_requests_per_worker=max_context_requests_per_worker,
                shared_context_policy=shared_context_policy,
            )
            work_dir = (
                run_dir
                / "workers"
                / worker_id
                / f"step_{step_index:03d}"
                / "codex"
            )
            future = pool.submit(
                run_worker_step,
                args=args,
                api_key=api_key,
                prompt=prompt,
                work_dir=work_dir,
                timeout_sec=step_codex_timeout,
            )
            future_to_worker[future] = worker_id
            total_codex_calls += 1
            event_logger.emit(
                "worker_launch",
                step=step_index,
                worker_id=worker_id,
                timeout_sec=max(1, int(step_codex_timeout)),
                remaining_sec=budget.remaining(),
            )

        pending = set(future_to_worker)
        while pending:
            done, pending = wait_for_worker_futures(
                pending,
                event_logger=event_logger,
                step_index=step_index,
                future_to_worker=future_to_worker,
                done_workers=done_workers,
                budget=budget,
            )
            for future in done:
                expected_worker_id = future_to_worker[future]
                try:
                    worker_step = normalize_worker_step_return(future.result())
                except KeyboardInterrupt:
                    interrupted = True
                    cancel_pending_futures(pending)
                    raise
                except Exception as exc:
                    event_logger.emit(
                        "worker_error",
                        step=step_index,
                        worker_id=expected_worker_id,
                        status="worker_exception",
                        message=str(exc),
                    )
                    raise
                worker_id = worker_step.worker_id
                action = worker_step.action
                result = worker_step.codex_result
                done_workers.add(worker_id)
                action_name = action.action or "PARSE_ERROR"
                if result is not None and result.timed_out:
                    event_logger.emit(
                        "codex_timeout",
                        step=step_index,
                        worker_id=worker_id,
                        timeout_sec=max(1, int(step_codex_timeout)),
                        returncode=result.returncode,
                    )
                    event_logger.emit(
                        "codex_cleanup_start",
                        step=step_index,
                        worker_id=worker_id,
                        reason="timeout",
                    )
                    event_logger.emit(
                        "codex_cleanup_end",
                        step=step_index,
                        worker_id=worker_id,
                        reason="timeout",
                        returncode=result.returncode,
                    )
                event_logger.emit(
                    "worker_return",
                    step=step_index,
                    worker_id=worker_id,
                    action=action_name,
                    errors_count=len(action.errors),
                )
                try:
                    action_result = handle_action(
                        action=action,
                        worker_id=worker_id,
                        step_index=step_index,
                        states=states,
                        putnam_root=putnam_root,
                        db_path=db_path,
                        run_dir=run_dir,
                        problem=problem,
                        budget=budget,
                        lean_timeout_sec=args.lean_timeout_sec,
                        max_context_requests_per_worker=max_context_requests_per_worker,
                        min_remaining_sec_to_start_call=min_remaining_sec_to_start_call,
                        shared_context_policy=shared_context_policy,
                        event_logger=event_logger,
                    )
                except Exception as exc:
                    event_logger.emit(
                        "worker_error",
                        step=step_index,
                        worker_id=worker_id,
                        action=action_name,
                        status="action_exception",
                        message=str(exc),
                    )
                    raise
                event_logger.emit(
                    "action_handled",
                    step=step_index,
                    worker_id=worker_id,
                    action=action_result.get("action", action_name),
                    result_event=action_result.get("event"),
                    success=action_result.get("success"),
                    stopped_reason=action_result.get("stopped_reason"),
                )
                write_action_json(
                    run_dir / "workers" / worker_id / f"step_{step_index:03d}",
                    action,
                    action_result,
                )
                if action_result.get("lean_elapsed_sec"):
                    total_lean_elapsed_sec += float(action_result["lean_elapsed_sec"])
                if action_result.get("success"):
                    success = True
                    final_verified_patch_seq = action_result.get("verified_patch_seq")
                    final_proof_seq = action_result.get("final_proof_seq")
                if action_result.get("stopped_reason") == "time_budget_exceeded":
                    stopped_reason = "time_budget_exceeded"
                    break
            if stopped_reason == "time_budget_exceeded" or success:
                break
    except KeyboardInterrupt:
        interrupted = True
        cancel_pending_futures(pending)
        raise
    finally:
        pool.shutdown(wait=not interrupted, cancel_futures=interrupted)

    return {
        "codex_calls": total_codex_calls,
        "lean_elapsed_sec": total_lean_elapsed_sec,
        "success": success,
        "final_verified_patch_seq": final_verified_patch_seq,
        "final_proof_seq": final_proof_seq,
        "stopped_reason": stopped_reason,
    }


def cancel_pending_futures(pending: set[Future[WorkerStepReturn]]) -> None:
    for future in pending:
        future.cancel()


def normalize_worker_step_return(value: object) -> WorkerStepReturn:
    if isinstance(value, WorkerStepReturn):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        worker_id, action = value
        return WorkerStepReturn(
            worker_id=str(worker_id),
            action=action,
            codex_result=None,
        )
    raise TypeError(f"unexpected worker step return: {value!r}")


def wait_for_worker_futures(
    pending: set[Future[WorkerStepReturn]],
    *,
    event_logger: "EventLogger",
    step_index: int,
    future_to_worker: dict[Future[WorkerStepReturn], str],
    done_workers: set[str],
    budget: "TimeBudget",
) -> tuple[set[Future[WorkerStepReturn]], set[Future[WorkerStepReturn]]]:
    heartbeat_sec = event_logger.heartbeat_sec
    timeout = heartbeat_sec if heartbeat_sec > 0 else None
    done, still_pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
    if not done and still_pending and heartbeat_sec > 0:
        waiting_workers = sorted(future_to_worker[future] for future in still_pending)
        event_logger.emit(
            "heartbeat",
            step=step_index,
            waiting_workers=waiting_workers,
            done_workers=sorted(done_workers),
            elapsed_since_last_event=event_logger.seconds_since_last_event(),
            remaining_sec=budget.remaining(),
        )
    return done, still_pending


def run_worker_step(
    *,
    args: argparse.Namespace,
    api_key: str | None,
    prompt: str,
    work_dir: Path,
    timeout_sec: float | int,
) -> WorkerStepReturn:
    worker_id = work_dir.parents[1].name
    result = run_codex_exec(
        prompt=prompt,
        work_dir=work_dir,
        codex_command=args.codex_command,
        model=args.model,
        api_key=api_key,
        base_url=args.base_url,
        provider=args.provider,
        timeout_sec=max(1, int(timeout_sec)),
    )
    text = result.last_message or result.stdout or result.stderr or result.combined_output or ""
    return WorkerStepReturn(
        worker_id=worker_id,
        action=parse_interactive_worker_action(text),
        codex_result=result,
    )


def handle_action(
    *,
    action: InteractiveWorkerAction,
    worker_id: str,
    step_index: int,
    states: dict[str, dict[str, Any]],
    putnam_root: Path,
    db_path: Path,
    run_dir: Path,
    problem: object,
    budget: "TimeBudget",
    lean_timeout_sec: int,
    max_context_requests_per_worker: int = 1,
    min_remaining_sec_to_start_call: float = 30,
    shared_context_policy: str = "legacy",
    event_logger: EventLogger | None = None,
) -> dict[str, Any]:
    action_name = action.action or "PARSE_ERROR"
    states[worker_id]["actions"].append(action_name)
    append_history(states, worker_id, step_index, action)
    worker_state = states[worker_id]

    if (
        shared_context_policy == "event_pull"
        and worker_state.get("force_context_request")
        and (
            worker_state.get("force_context_request_after_step") is None
            or step_index >= int(worker_state["force_context_request_after_step"])
        )
        and action_name != "REQUEST_CONTEXT"
    ):
        message = (
            "You must request Shared Context before further actions because "
            "another worker's patch failed."
        )
        emit_optional(
            event_logger,
            "forced_context_request_missed",
            step=step_index,
            worker_id=worker_id,
            action=action_name,
            pending_context_note_seq=worker_state.get("pending_context_note_seq"),
        )
        return record_malformed_action(
            states,
            worker_id,
            action_name,
            "FORCED_CONTEXT_REQUEST_MISSED",
            message,
        )

    if (
        shared_context_policy == "event_pull_soft"
        and worker_state.get("soft_context_available")
        and (
            worker_state.get("soft_context_notice_after_step") is None
            or step_index >= int(worker_state["soft_context_notice_after_step"])
        )
        and action_name in {"SUBMIT_PATCH", "SUBMIT_NOTE", "STOP"}
    ):
        emit_optional(
            event_logger,
            "soft_context_pull_declined_or_bypassed",
            step=step_index,
            worker_id=worker_id,
            action=action_name,
            pending_soft_context_note_seq=worker_state.get(
                "pending_soft_context_note_seq"
            ),
        )
        worker_state["soft_context_available"] = False
        worker_state["pending_soft_context_note_seq"] = None
        worker_state["soft_context_notice_after_step"] = None

    if action_name == "REQUEST_CONTEXT":
        worker_state["context_request_count"] = int(
            worker_state.get("context_request_count", 0)
        ) + 1
        context_request_count = int(worker_state["context_request_count"])
        remaining_context_requests = remaining_context_request_budget(
            context_request_count, max_context_requests_per_worker
        )
        if context_request_limit_exceeded(
            context_request_count, max_context_requests_per_worker
        ):
            if shared_context_policy == "event_pull_soft" and worker_state.get(
                "soft_context_available"
            ):
                emit_optional(
                    event_logger,
                    "soft_context_pull_skipped_due_to_limit",
                    step=step_index,
                    worker_id=worker_id,
                    pending_soft_context_note_seq=worker_state.get(
                        "pending_soft_context_note_seq"
                    ),
                    context_request_count=context_request_count,
                    max_context_requests_per_worker=max_context_requests_per_worker,
                )
                worker_state["soft_context_available"] = False
                worker_state["pending_soft_context_note_seq"] = None
                worker_state["soft_context_notice_after_step"] = None
            worker_state["context_request_limit_exceeded_count"] = int(
                worker_state.get("context_request_limit_exceeded_count", 0)
            ) + 1
            message = (
                "REQUEST_CONTEXT limit exceeded. Do not request context again; "
                "next step must choose SUBMIT_NOTE, SUBMIT_PATCH, or STOP."
            )
            worker_state["history"] += f"\nController: {message}"
            emit_optional(
                event_logger,
                "request_context_ignored",
                step=step_index,
                worker_id=worker_id,
                action=action_name,
                context_request_count=context_request_count,
                max_context_requests_per_worker=max_context_requests_per_worker,
                remaining_context_requests=remaining_context_requests,
                reason="context_request_limit_exceeded",
            )
            return {
                "action": action_name,
                "ignored": True,
                "reason": "context_request_limit_exceeded",
                "context_request_count": context_request_count,
                "max_context_requests_per_worker": max_context_requests_per_worker,
                "remaining_context_requests": remaining_context_requests,
            }
        latest_context = handle_request_context(db_path, problem.problem_id, worker_state)
        handled_pending_seq = worker_state.get("pending_context_note_seq")
        if shared_context_policy == "event_pull" and worker_state.get(
            "force_context_request"
        ):
            worker_state["force_context_request"] = False
            worker_state["force_context_request_after_step"] = None
            worker_state["last_seen_context_seq"] = handled_pending_seq
            worker_state["pending_context_note_seq"] = None
            worker_state["event_context_request_count"] = int(
                worker_state.get("event_context_request_count", 0)
            ) + 1
            emit_optional(
                event_logger,
                "event_context_pull_handled",
                step=step_index,
                worker_id=worker_id,
                pending_context_note_seq=handled_pending_seq,
                context_chars=len(latest_context),
                event_context_request_count=worker_state[
                    "event_context_request_count"
                ],
            )
        if shared_context_policy == "event_pull_soft" and worker_state.get(
            "soft_context_available"
        ):
            handled_soft_seq = worker_state.get("pending_soft_context_note_seq")
            worker_state["soft_context_available"] = False
            worker_state["pending_soft_context_note_seq"] = None
            worker_state["soft_context_notice_after_step"] = None
            worker_state["last_seen_context_seq"] = handled_soft_seq
            worker_state["event_context_request_count"] = int(
                worker_state.get("event_context_request_count", 0)
            ) + 1
            emit_optional(
                event_logger,
                "soft_context_pull_handled",
                step=step_index,
                worker_id=worker_id,
                pending_soft_context_note_seq=handled_soft_seq,
                context_chars=len(latest_context),
                event_context_request_count=worker_state[
                    "event_context_request_count"
                ],
            )
        emit_optional(
            event_logger,
            "request_context_handled",
            step=step_index,
            worker_id=worker_id,
            action=action_name,
            context_chars=len(latest_context),
            context_request_count=context_request_count,
            max_context_requests_per_worker=max_context_requests_per_worker,
            remaining_context_requests=remaining_context_requests,
        )
        return {
            "action": action_name,
            "context_chars": len(latest_context),
            "context_request_count": context_request_count,
            "max_context_requests_per_worker": max_context_requests_per_worker,
            "remaining_context_requests": remaining_context_requests,
        }

    if action_name == "SUBMIT_NOTE":
        if action.note_type not in AGENT_NOTE_TYPES:
            emit_optional(
                event_logger,
                "note_ignored",
                step=step_index,
                worker_id=worker_id,
                action=action_name,
                note_type=action.note_type,
                reason="invalid_type",
            )
            return record_malformed_action(
                states,
                worker_id,
                action_name,
                "SUBMIT_NOTE_INVALID_TYPE_IGNORED",
                f"invalid SUBMIT_NOTE note_type: {action.note_type!r}",
            )
        note_content = (action.note_content or "").strip()
        if not note_content:
            emit_optional(
                event_logger,
                "note_ignored",
                step=step_index,
                worker_id=worker_id,
                action=action_name,
                note_type=action.note_type,
                reason="empty_content",
            )
            return record_malformed_action(
                states,
                worker_id,
                action_name,
                "SUBMIT_NOTE_EMPTY_IGNORED",
                "empty SUBMIT_NOTE ignored",
            )
        try:
            saved = append_note(
                db_path,
                SharedNote(
                    seq=None,
                    problem_id=problem.problem_id,
                    worker_id=worker_id,
                    type=action.note_type,
                    content=note_content[:1000],
                    metadata={"source": "interactive_shared_context_agents"},
                ),
                writer_role="agent",
            )
        except ValueError as exc:
            emit_optional(
                event_logger,
                "note_ignored",
                step=step_index,
                worker_id=worker_id,
                action=action_name,
                note_type=action.note_type,
                reason="write_error",
                message=str(exc),
            )
            return record_malformed_action(
                states,
                worker_id,
                action_name,
                "SUBMIT_NOTE_WRITE_ERROR_IGNORED",
                f"SUBMIT_NOTE write error ignored: {exc}",
            )
        emit_optional(
            event_logger,
            "note_admitted",
            step=step_index,
            worker_id=worker_id,
            action=action_name,
            note_type=action.note_type,
            note_seq=saved.seq,
        )
        return {"action": action_name, "note_seq": saved.seq}

    if action_name == "SUBMIT_PATCH":
        proof_patch = (action.proof_patch or "").strip()
        if not proof_patch:
            emit_optional(
                event_logger,
                "worker_error",
                step=step_index,
                worker_id=worker_id,
                action=action_name,
                status="empty_patch",
                message="empty SUBMIT_PATCH ignored",
            )
            return record_malformed_action(
                states,
                worker_id,
                action_name,
                "SUBMIT_PATCH_EMPTY_IGNORED",
                "empty SUBMIT_PATCH ignored",
            )
        min_remaining_sec_to_start_call = normalized_min_remaining_sec(
            min_remaining_sec_to_start_call
        )
        if not budget.can_start_call() or not has_enough_time_to_start_call(
            budget, min_remaining_sec_to_start_call
        ):
            emit_optional(
                event_logger,
                "time_budget_low",
                phase="lean_submit",
                step=step_index,
                worker_id=worker_id,
                remaining_sec=budget.remaining(),
                min_remaining_sec_to_start_call=min_remaining_sec_to_start_call,
            )
            return {
                "action": action_name,
                "stopped_reason": "time_budget_exceeded",
                "skipped": True,
                "reason": "insufficient_remaining_time_for_lean",
            }
        effective_lean_timeout = budget.timeout_for(lean_timeout_sec)
        if effective_lean_timeout <= 0:
            return {"action": action_name, "stopped_reason": "time_budget_exceeded"}
        timeout_sec = max(1, int(effective_lean_timeout))
        states[worker_id]["patch_attempt_count"] = int(
            states[worker_id].get("patch_attempt_count", 0)
        ) + 1
        emit_optional(
            event_logger,
            "patch_submit_start",
            step=step_index,
            worker_id=worker_id,
            action=action_name,
            timeout_sec=timeout_sec,
        )
        try:
            submit_result = submit_patch(
                putnam_root=putnam_root,
                db_path=db_path,
                problem_id=problem.problem_id,
                worker_id=worker_id,
                proof_patch=proof_patch,
                run_dir=run_dir,
                timeout_sec=timeout_sec,
            )
        except Exception as exc:
            emit_optional(
                event_logger,
                "patch_submit_end",
                step=step_index,
                worker_id=worker_id,
                action=action_name,
                success=False,
                status="exception",
                message=str(exc),
            )
            raise
        result_dict = patch_submission_result_to_dict(submit_result)
        states[worker_id]["last_check_status"] = submit_result.check_status
        if not submit_result.success:
            states[worker_id]["last_error"] = "; ".join(
                submit_result.error_messages[:3]
            )
        emit_optional(
            event_logger,
            "patch_submit_end",
            step=step_index,
            worker_id=worker_id,
            action=action_name,
            success=submit_result.success,
            status=submit_result.check_status,
            returncode=submit_result.returncode,
            lean_elapsed_sec=submit_result.elapsed_sec,
            verified_patch_seq=submit_result.verified_patch_seq,
            final_proof_seq=submit_result.final_proof_seq,
        )
        failure_note_seq = None
        if shared_context_policy in {"event_pull", "event_pull_soft"} and not submit_result.success:
            failure_note = admit_failure_note(
                db_path=db_path,
                problem_id=problem.problem_id,
                failed_worker_id=worker_id,
                step_index=step_index,
                proof_patch=proof_patch,
                submit_result=submit_result,
                event_logger=event_logger,
            )
            failure_note_seq = failure_note.seq
            if shared_context_policy == "event_pull":
                mark_event_context_pull_required(
                    states=states,
                    failed_worker_id=worker_id,
                    step_index=step_index,
                    note_seq=failure_note.seq,
                    max_context_requests_per_worker=max_context_requests_per_worker,
                    budget=budget,
                    min_remaining_sec_to_start_call=min_remaining_sec_to_start_call,
                    event_logger=event_logger,
                )
            elif shared_context_policy == "event_pull_soft":
                mark_soft_context_pull_available(
                    states=states,
                    failed_worker_id=worker_id,
                    step_index=step_index,
                    note_seq=failure_note.seq,
                    max_context_requests_per_worker=max_context_requests_per_worker,
                    budget=budget,
                    min_remaining_sec_to_start_call=min_remaining_sec_to_start_call,
                    event_logger=event_logger,
                )
        return {
            "action": action_name,
            "success": submit_result.success,
            "lean_elapsed_sec": submit_result.elapsed_sec,
            "verified_patch_seq": submit_result.verified_patch_seq,
            "final_proof_seq": submit_result.final_proof_seq,
            "submit_result": result_dict,
            "failure_note_seq": failure_note_seq,
        }

    if action_name == "STOP":
        states[worker_id]["active"] = False
        states[worker_id]["force_context_request"] = False
        states[worker_id]["force_context_request_after_step"] = None
        states[worker_id]["pending_context_note_seq"] = None
        states[worker_id]["soft_context_available"] = False
        states[worker_id]["pending_soft_context_note_seq"] = None
        states[worker_id]["soft_context_notice_after_step"] = None
        emit_optional(
            event_logger,
            "worker_stop",
            step=step_index,
            worker_id=worker_id,
            action=action_name,
        )
        return {"action": action_name}

    emit_optional(
        event_logger,
        "worker_error",
        step=step_index,
        worker_id=worker_id,
        action=action_name,
        status="parse_error",
        message="; ".join(action.errors),
    )
    return {"action": action_name, "errors": action.errors}


def admit_failure_note(
    *,
    db_path: Path,
    problem_id: str,
    failed_worker_id: str,
    step_index: int,
    proof_patch: str,
    submit_result: object,
    event_logger: EventLogger | None,
) -> SharedNote:
    error_messages = getattr(submit_result, "error_messages", []) or []
    content = "\n".join(
        [
            f"failed_worker_id: {failed_worker_id}",
            f"step_index: {step_index}",
            f"check_status: {getattr(submit_result, 'check_status', None)}",
            f"returncode: {getattr(submit_result, 'returncode', None)}",
            f"tactic_keywords: {', '.join(extract_tactic_keywords(proof_patch)) or '(none detected)'}",
            "",
            "error_messages:",
            truncate_text("\n".join(str(message) for message in error_messages), 1500)
            or "(none)",
            "",
            "failed_proof_patch_preview:",
            "```lean",
            truncate_text(proof_patch, 1800),
            "```",
            "",
            "guidance:",
            "- Avoid repeating the same proof patch.",
            "- Inspect the Lean errors before trying a similar tactic or rewrite pattern.",
            "- Prefer a shorter, more robust patch if this attempt timed out.",
        ]
    )
    saved = append_note(
        db_path,
        SharedNote(
            seq=None,
            problem_id=problem_id,
            worker_id="interactive_controller",
            type="FAIL",
            content=truncate_text(content, 3800),
            target_seq=getattr(submit_result, "proof_patch_seq", None),
            attempt_path=getattr(submit_result, "attempt_path", None),
            status="failed",
            metadata={
                "source": "event_pull",
                "failed_worker_id": failed_worker_id,
                "step_index": step_index,
                "check_status": getattr(submit_result, "check_status", None),
            },
        ),
        writer_role="system",
    )
    emit_optional(
        event_logger,
        "failure_note_admitted",
        step=step_index,
        worker_id=failed_worker_id,
        note_seq=saved.seq,
        check_status=getattr(submit_result, "check_status", None),
        content_chars=len(saved.content),
    )
    return saved


def mark_event_context_pull_required(
    *,
    states: dict[str, dict[str, Any]],
    failed_worker_id: str,
    step_index: int,
    note_seq: int | None,
    max_context_requests_per_worker: int,
    budget: "TimeBudget",
    min_remaining_sec_to_start_call: float,
    event_logger: EventLogger | None,
) -> None:
    for worker_id, worker_state in states.items():
        if worker_id == failed_worker_id or not worker_state.get("active"):
            continue
        if remaining_context_request_budget(
            int(worker_state.get("context_request_count", 0)),
            max_context_requests_per_worker,
        ) == 0:
            emit_optional(
                event_logger,
                "event_context_pull_skipped_due_to_limit",
                step=step_index,
                worker_id=worker_id,
                failed_worker_id=failed_worker_id,
                pending_context_note_seq=note_seq,
            )
            continue
        if not has_enough_time_to_start_call(budget, min_remaining_sec_to_start_call):
            emit_optional(
                event_logger,
                "event_context_pull_skipped_due_to_budget",
                step=step_index,
                worker_id=worker_id,
                failed_worker_id=failed_worker_id,
                pending_context_note_seq=note_seq,
                remaining_sec=budget.remaining(),
                min_remaining_sec_to_start_call=min_remaining_sec_to_start_call,
            )
            continue
        if worker_state.get("last_seen_context_seq") == note_seq:
            continue
        worker_state["force_context_request"] = True
        worker_state["force_context_request_after_step"] = step_index + 1
        worker_state["pending_context_note_seq"] = note_seq
        emit_optional(
            event_logger,
            "event_context_pull_required",
            step=step_index,
            worker_id=worker_id,
            failed_worker_id=failed_worker_id,
            pending_context_note_seq=note_seq,
        )


def mark_soft_context_pull_available(
    *,
    states: dict[str, dict[str, Any]],
    failed_worker_id: str,
    step_index: int,
    note_seq: int | None,
    max_context_requests_per_worker: int,
    budget: "TimeBudget",
    min_remaining_sec_to_start_call: float,
    event_logger: EventLogger | None,
) -> None:
    for worker_id, worker_state in states.items():
        if worker_id == failed_worker_id or not worker_state.get("active"):
            continue
        if remaining_context_request_budget(
            int(worker_state.get("context_request_count", 0)),
            max_context_requests_per_worker,
        ) == 0:
            emit_optional(
                event_logger,
                "soft_context_pull_skipped_due_to_limit",
                step=step_index,
                worker_id=worker_id,
                failed_worker_id=failed_worker_id,
                pending_soft_context_note_seq=note_seq,
            )
            continue
        if not has_enough_time_to_start_call(budget, min_remaining_sec_to_start_call):
            emit_optional(
                event_logger,
                "soft_context_pull_skipped_due_to_budget",
                step=step_index,
                worker_id=worker_id,
                failed_worker_id=failed_worker_id,
                pending_soft_context_note_seq=note_seq,
                remaining_sec=budget.remaining(),
                min_remaining_sec_to_start_call=min_remaining_sec_to_start_call,
            )
            continue
        if worker_state.get("last_seen_context_seq") == note_seq:
            continue
        pending_seq = worker_state.get("pending_soft_context_note_seq")
        notice_after_step = worker_state.get("soft_context_notice_after_step")
        if (
            worker_state.get("soft_context_available")
            and pending_seq is not None
            and (note_seq is None or int(pending_seq) >= int(note_seq))
        ):
            continue
        if (
            worker_state.get("soft_context_available")
            and notice_after_step is not None
            and step_index < int(notice_after_step)
        ):
            continue
        worker_state["soft_context_available"] = True
        worker_state["pending_soft_context_note_seq"] = note_seq
        worker_state["soft_context_notice_after_step"] = step_index + 1
        worker_state["soft_context_notice_count"] = int(
            worker_state.get("soft_context_notice_count", 0)
        ) + 1
        emit_optional(
            event_logger,
            "soft_context_pull_available",
            step=step_index,
            worker_id=worker_id,
            failed_worker_id=failed_worker_id,
            pending_soft_context_note_seq=note_seq,
            soft_context_notice_count=worker_state["soft_context_notice_count"],
        )


def extract_tactic_keywords(proof_patch: str) -> list[str]:
    keywords = [
        "simp",
        "rw",
        "rewrite",
        "omega",
        "ring_nf",
        "norm_num",
        "aesop",
        "linarith",
        "nlinarith",
        "field_simp",
        "positivity",
        "exact",
        "apply",
        "calc",
    ]
    found = []
    for keyword in keywords:
        if keyword in proof_patch:
            found.append(keyword)
    return found


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"{text[:max_chars]}\n... [truncated {omitted} chars]"


def record_malformed_action(
    states: dict[str, dict[str, Any]],
    worker_id: str,
    action_name: str,
    event_name: str,
    message: str,
) -> dict[str, Any]:
    states[worker_id]["actions"].append(event_name)
    states[worker_id]["last_error"] = message
    states[worker_id]["malformed_action_count"] += 1
    states[worker_id]["history"] += f"\nController: {message}."
    return {
        "action": action_name,
        "malformed": True,
        "event": event_name,
        "error": message,
    }


def handle_request_context(
    db_path: Path, problem_id: str, worker_state: dict[str, Any]
) -> str:
    latest_context = pull_worker_context(db_path, problem_id)
    worker_state["history"] += (
        "\nController returned latest committed Shared Context:\n"
        f"{latest_context}\n"
    )
    return latest_context


def pull_worker_context(db_path: Path, problem_id: str) -> str:
    notes = get_notes(db_path, problem_id=problem_id)
    return render_worker_context(notes)


def build_step_prompt(
    *,
    problem: object,
    worker_id: str,
    step_index: int,
    current_context_text: str,
    worker_history_text: str,
    worker_state: dict[str, Any] | None = None,
    max_context_requests_per_worker: int = 1,
    shared_context_policy: str = "legacy",
) -> str:
    worker_state = worker_state or {}
    return build_interactive_worker_step_prompt(
        problem_id=problem.problem_id,
        theorem_name=problem.theorem_name,
        theorem_statement=problem.source,
        docstring=problem.docstring,
        full_source=problem.source,
        worker_id=worker_id,
        step_index=step_index,
        current_context_text=current_context_text,
        worker_history_text=worker_history_text,
        max_context_requests_per_worker=max_context_requests_per_worker,
        context_request_count=int(worker_state.get("context_request_count", 0)),
        patch_attempt_count=int(worker_state.get("patch_attempt_count", 0)),
        previous_actions=[str(action) for action in worker_state.get("actions", [])],
        last_check_status=worker_state.get("last_check_status"),
        last_error=worker_state.get("last_error"),
        shared_context_policy=shared_context_policy,
        force_context_request=bool(worker_state.get("force_context_request", False)),
        pending_context_note_seq=worker_state.get("pending_context_note_seq"),
        soft_context_available=bool(worker_state.get("soft_context_available", False)),
        pending_soft_context_note_seq=worker_state.get("pending_soft_context_note_seq"),
    )


def prompt_context_for_policy(context_text: str, shared_context_policy: str) -> str:
    if shared_context_policy in {"event_pull", "event_pull_soft"}:
        return (
            "(Shared Context is not automatically shown in event_pull modes. "
            "Use REQUEST_CONTEXT if you need the latest failure details.)"
        )
    return context_text


def append_history(
    states: dict[str, dict[str, Any]],
    worker_id: str,
    step_index: int,
    action: InteractiveWorkerAction,
) -> None:
    states[worker_id]["history"] += (
        f"\nstep {step_index}: action={action.action}; "
        f"summary={action.summary}; errors={action.errors}"
    )


def write_action_json(
    step_dir: Path, action: InteractiveWorkerAction, result: dict[str, Any]
) -> None:
    step_dir.mkdir(parents=True, exist_ok=True)
    payload = {"action": asdict(action), "result": result}
    (step_dir / "action.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def ensure_problem_note(db_path: Path, problem: object) -> None:
    existing = [
        note for note in get_notes(db_path, problem_id=problem.problem_id)
        if note.type == "PROBLEM"
    ]
    if existing:
        return
    content = "\n".join(
        [
            f"problem_id: {problem.problem_id}",
            f"theorem_name: {problem.theorem_name}",
            f"relative_path: {problem.relative_path}",
            f"docstring: {problem.docstring or '(none)'}",
        ]
    )
    append_note(
        db_path,
        SharedNote(
            seq=None,
            problem_id=problem.problem_id,
            worker_id="interactive_controller",
            type="PROBLEM",
            content=content,
            metadata={"source": "interactive_shared_context_agents"},
        ),
        writer_role="system",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pull-based Shared Context agents.")
    parser.add_argument("--putnam-root", default="data/PutnamBench")
    parser.add_argument("--problem-id")
    parser.add_argument("--num-workers", type=int, default=3)
    parser.add_argument("--max-steps", type=int, default=3)
    parser.add_argument("--max-concurrency", type=int)
    parser.add_argument("--db", default="runs/interactive_shared_context_agents/context.sqlite")
    parser.add_argument("--run-dir", default="runs/interactive_shared_context_agents")
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--provider", default=os.environ.get("CODEX_PROVIDER", "relay"))
    parser.add_argument(
        "--model",
        default=os.environ.get("CODEX_MODEL") or os.environ.get("RELAY_MODEL"),
    )
    parser.add_argument("--base-url", default=os.environ.get("RELAY_BASE_URL"))
    parser.add_argument("--api-key-env", default="RELAY_API_KEY")
    parser.add_argument("--use-api-key", action="store_true")
    parser.add_argument("--codex-timeout-sec", type=int, default=600)
    parser.add_argument("--lean-timeout-sec", type=int, default=180)
    parser.add_argument("--time-budget-sec", type=float, default=0)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--heartbeat-sec", type=float, default=30)
    parser.add_argument("--max-context-requests-per-worker", type=int, default=1)
    parser.add_argument("--min-remaining-sec-to-start-call", type=float, default=30)
    parser.add_argument(
        "--shared-context-policy",
        choices=("legacy", "balanced", "aggressive", "event_pull", "event_pull_soft"),
        default="legacy",
    )
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_api_key(args: argparse.Namespace) -> str | None:
    if not args.use_api_key:
        return None
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        raise SystemExit(
            f"--use-api-key was set, but environment variable {args.api_key_env!r} is not set."
        )
    return api_key


def default_problem_id(path: Path) -> str:
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            problem_id = line.strip()
            if problem_id:
                return problem_id
    return "putnam_1968_a1"


def reset_run_state(db_path: Path, run_dir: Path) -> None:
    if run_dir.exists():
        shutil.rmtree(run_dir)
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{db_path}{suffix}")
        if path.exists():
            path.unlink()


class TimeBudget:
    def __init__(self, time_budget_sec: float = 0) -> None:
        self.time_budget_sec = max(0.0, float(time_budget_sec or 0))
        self.start = time.perf_counter()

    @property
    def enabled(self) -> bool:
        return self.time_budget_sec > 0

    def elapsed(self) -> float:
        return time.perf_counter() - self.start

    def remaining(self) -> float | None:
        if not self.enabled:
            return None
        return max(0.0, self.time_budget_sec - self.elapsed())

    def exhausted(self) -> bool:
        remaining = self.remaining()
        return remaining is not None and remaining <= 0

    def can_start_call(self) -> bool:
        remaining = self.remaining()
        return remaining is None or remaining > 0

    def timeout_for(self, requested_timeout_sec: float | int) -> float:
        remaining = self.remaining()
        if remaining is None:
            return float(requested_timeout_sec)
        return min(float(requested_timeout_sec), remaining)


def budget_mode(time_budget_sec: float) -> str:
    return "enabled" if time_budget_sec > 0 else "disabled"


def interactive_stopped_reason(
    *,
    success: bool,
    budget_exhausted: bool,
    steps_completed: int,
    max_steps: int,
    any_active: bool,
) -> str:
    if success:
        return "success"
    if budget_exhausted:
        return "time_budget_exceeded"
    if not any_active:
        return "all_workers_stopped"
    if steps_completed >= max_steps:
        return "max_steps_reached"
    return "error"


if __name__ == "__main__":
    raise SystemExit(main())
