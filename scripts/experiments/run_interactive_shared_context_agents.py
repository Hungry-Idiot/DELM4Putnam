from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

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
from src.codex.runner import run_codex_exec
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


def main() -> int:
    args = parse_args()
    if args.max_concurrency is None:
        args.max_concurrency = args.num_workers

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
        print(f"wall-clock budget mode: {budget_mode(args.time_budget_sec)}")
        if args.time_budget_sec > 0:
            print(f"This run will stop after {args.time_budget_sec:g} seconds unless solved earlier.")
        print("For parallel architecture comparison, use different --run-dir and --db for each architecture to avoid conflicts.")
        print("Shared Context mode: worker")
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
                current_context_text=first_context,
                worker_history_text="",
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
        step_codex_timeout = budget.timeout_for(args.codex_timeout_sec)
        if step_codex_timeout <= 0:
            stopped_reason = "time_budget_exceeded"
            break

        jobs = []
        with ThreadPoolExecutor(max_workers=args.max_concurrency) as pool:
            for worker_id in active_workers:
                context_text = pull_worker_context(db_path, problem.problem_id)
                prompt = build_step_prompt(
                    problem=problem,
                    worker_id=worker_id,
                    step_index=step_index,
                    current_context_text=context_text,
                    worker_history_text=str(states[worker_id]["history"]),
                )
                work_dir = (
                    run_dir
                    / "workers"
                    / worker_id
                    / f"step_{step_index:03d}"
                    / "codex"
                )
                jobs.append(
                    pool.submit(
                        run_worker_step,
                        args=args,
                        api_key=api_key,
                        prompt=prompt,
                        work_dir=work_dir,
                        timeout_sec=step_codex_timeout,
                    )
                )
                total_codex_calls += 1

            for future in as_completed(jobs):
                worker_id, action = future.result()
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

    if stopped_reason is None:
        stopped_reason = interactive_stopped_reason(
            success=success,
            budget_exhausted=budget.exhausted(),
            steps_completed=steps_completed,
            max_steps=args.max_steps,
            any_active=any(states[worker_id]["active"] for worker_id in worker_ids),
        )
    return {
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
            }
            for worker_id in worker_ids
        },
        "malformed_action_count": sum(
            int(states[worker_id]["malformed_action_count"])
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
        "budget_exhausted": budget.exhausted() and not success,
    }


def run_worker_step(
    *,
    args: argparse.Namespace,
    api_key: str | None,
    prompt: str,
    work_dir: Path,
    timeout_sec: float | int,
) -> tuple[str, InteractiveWorkerAction]:
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
    return worker_id, parse_interactive_worker_action(text)


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
) -> dict[str, Any]:
    action_name = action.action or "PARSE_ERROR"
    states[worker_id]["actions"].append(action_name)
    append_history(states, worker_id, step_index, action)

    if action_name == "REQUEST_CONTEXT":
        latest_context = handle_request_context(db_path, problem.problem_id, states[worker_id])
        return {"action": action_name, "context_chars": len(latest_context)}

    if action_name == "SUBMIT_NOTE":
        if action.note_type not in AGENT_NOTE_TYPES:
            return record_malformed_action(
                states,
                worker_id,
                action_name,
                "SUBMIT_NOTE_INVALID_TYPE_IGNORED",
                f"invalid SUBMIT_NOTE note_type: {action.note_type!r}",
            )
        note_content = (action.note_content or "").strip()
        if not note_content:
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
            return record_malformed_action(
                states,
                worker_id,
                action_name,
                "SUBMIT_NOTE_WRITE_ERROR_IGNORED",
                f"SUBMIT_NOTE write error ignored: {exc}",
            )
        return {"action": action_name, "note_seq": saved.seq}

    if action_name == "SUBMIT_PATCH":
        proof_patch = (action.proof_patch or "").strip()
        if not proof_patch:
            return record_malformed_action(
                states,
                worker_id,
                action_name,
                "SUBMIT_PATCH_EMPTY_IGNORED",
                "empty SUBMIT_PATCH ignored",
            )
        if not budget.can_start_call():
            return {"action": action_name, "stopped_reason": "time_budget_exceeded"}
        effective_lean_timeout = budget.timeout_for(lean_timeout_sec)
        if effective_lean_timeout <= 0:
            return {"action": action_name, "stopped_reason": "time_budget_exceeded"}
        submit_result = submit_patch(
            putnam_root=putnam_root,
            db_path=db_path,
            problem_id=problem.problem_id,
            worker_id=worker_id,
            proof_patch=proof_patch,
            run_dir=run_dir,
            timeout_sec=max(1, int(effective_lean_timeout)),
        )
        result_dict = patch_submission_result_to_dict(submit_result)
        states[worker_id]["last_check_status"] = submit_result.check_status
        return {
            "action": action_name,
            "success": submit_result.success,
            "lean_elapsed_sec": submit_result.elapsed_sec,
            "verified_patch_seq": submit_result.verified_patch_seq,
            "final_proof_seq": submit_result.final_proof_seq,
            "submit_result": result_dict,
        }

    if action_name == "STOP":
        states[worker_id]["active"] = False
        return {"action": action_name}

    return {"action": action_name, "errors": action.errors}


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
) -> str:
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
    )


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
