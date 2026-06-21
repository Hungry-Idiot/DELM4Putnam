from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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

from src.codex.parser import parse_main_plan_output, parse_worker_output
from src.codex.prompts import (
    build_main_agent_planning_prompt,
    build_main_agent_synthesis_prompt,
    build_main_assigned_worker_prompt,
)
from src.codex.runner import run_codex_exec
from src.controller.patch_service import (
    find_problem_by_id,
    patch_submission_result_to_dict,
    submit_patch,
)
from src.shared_context.storage import init_db


DEFAULT_SUBSET_PATH = Path("outputs/putnam_small_ids.txt")


def main() -> int:
    args = parse_args()
    putnam_root = Path(args.putnam_root)
    db_path = Path(args.db)
    run_dir = Path(args.run_dir)
    if args.fresh:
        reset_run_state(db_path, run_dir)

    problem_id = args.problem_id or default_problem_id(DEFAULT_SUBSET_PATH)
    problem = find_problem_by_id(putnam_root, problem_id)
    init_db(db_path)

    round_dir = run_dir / "rounds" / "round_001"
    planning_prompt = build_main_agent_planning_prompt(
        problem_id=problem.problem_id,
        theorem_name=problem.theorem_name,
        theorem_statement=problem.source,
        docstring=problem.docstring,
        full_source=problem.source,
        num_workers=args.num_workers,
    )

    if args.dry_run:
        print(planning_prompt)
        print()
        print("Expected directory structure")
        print(run_dir / "context.sqlite")
        print(round_dir / "main_plan")
        print(round_dir / "workers" / "W1")
        print(round_dir / "main_synthesis")
        print(round_dir / "result.json")
        print(run_dir / "summary.json")
        print()
        print("Codex provider configuration")
        print(f"provider: {args.provider}")
        print(f"base_url: {args.base_url or '(not set)'}")
        print(f"model: {args.model or '(not set)'}")
        print(f"API key will be read from env var: {args.api_key_env}")
        print(f"time_budget_sec: {args.time_budget_sec:g}")
        print(f"max_concurrency: {effective_max_concurrency(args)}")
        print(f"wall-clock budget mode: {budget_mode(args.time_budget_sec)}")
        if args.time_budget_sec > 0:
            print(f"This run will stop after {args.time_budget_sec:g} seconds unless solved earlier.")
        print("For parallel architecture comparison, use different --run-dir and --db for each architecture to avoid conflicts.")
        return 0

    api_key = read_api_key(args)
    budget = TimeBudget(args.time_budget_sec)
    previous_round_text = ""
    summary = initial_summary(args, problem_id)
    log_event(
        budget,
        "run_start",
        problem_id=problem_id,
        num_workers=args.num_workers,
        max_concurrency=effective_max_concurrency(args),
        max_rounds=args.max_rounds,
        time_budget_sec=args.time_budget_sec,
    )

    for round_index in range(1, args.max_rounds + 1):
        if not budget.can_start_call():
            summary["stopped_reason"] = "time_budget_exceeded"
            log_event(
                budget,
                "time_budget_exceeded",
                phase="round_start",
                problem_id=problem_id,
                next_round=round_index,
            )
            break
        round_dir = run_dir / "rounds" / f"round_{round_index:03d}"
        round_result = run_round(
            args=args,
            api_key=api_key,
            budget=budget,
            putnam_root=putnam_root,
            db_path=db_path,
            run_dir=run_dir,
            round_dir=round_dir,
            round_index=round_index,
            problem=problem,
            previous_round_text=previous_round_text,
        )
        (round_dir / "result.json").parent.mkdir(parents=True, exist_ok=True)
        (round_dir / "result.json").write_text(
            json.dumps(round_result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        summary["rounds_completed"] = round_index
        summary["total_codex_calls"] += round_result["codex_calls"]
        summary["check_status_by_round"].append(round_result.get("check_status"))
        submit_result = round_result.get("submit_result") or {}
        if round_result.get("stopped_reason") == "time_budget_exceeded":
            summary["stopped_reason"] = "time_budget_exceeded"
            break
        if round_result["success"]:
            summary["success"] = True
            summary["final_verified_patch_seq"] = submit_result.get("verified_patch_seq")
            summary["final_proof_seq"] = submit_result.get("final_proof_seq")
            summary["time_to_success_sec"] = budget.elapsed()
            summary["stopped_reason"] = "success"
            break
        previous_round_text = build_previous_round_text(round_result)

    if summary["stopped_reason"] is None:
        summary["stopped_reason"] = main_stopped_reason(
            success=summary["success"],
            budget_exhausted=budget.exhausted(),
            rounds_completed=summary["rounds_completed"],
            max_rounds=args.max_rounds,
        )
    summary["budget_exhausted"] = budget.exhausted() and not summary["success"]
    summary["wall_elapsed_sec"] = budget.elapsed()
    summary["total_elapsed_sec"] = summary["wall_elapsed_sec"]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    log_event(
        budget,
        "run_end",
        success=summary["success"],
        stopped_reason=summary["stopped_reason"],
        rounds_completed=summary["rounds_completed"],
        total_codex_calls=summary["total_codex_calls"],
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def run_round(
    *,
    args: argparse.Namespace,
    api_key: str | None,
    budget: "TimeBudget",
    putnam_root: Path,
    db_path: Path,
    run_dir: Path,
    round_dir: Path,
    round_index: int,
    problem: object,
    previous_round_text: str,
) -> dict[str, Any]:
    codex_calls = 0
    stopped_reason: str | None = None

    def finish_round(result: dict[str, Any]) -> dict[str, Any]:
        log_event(
            budget,
            "round_end",
            **{
                "round": round_index,
                "success": result.get("success", False),
                "stopped_reason": result.get("stopped_reason"),
                "check_status": result.get("check_status"),
                "codex_calls": result.get("codex_calls", codex_calls),
            },
        )
        return result

    log_event(
        budget,
        "round_start",
        **{
            "round": round_index,
            "problem_id": problem.problem_id,
            "num_workers": args.num_workers,
        },
    )
    planning_prompt = build_main_agent_planning_prompt(
        problem_id=problem.problem_id,
        theorem_name=problem.theorem_name,
        theorem_statement=problem.source,
        docstring=problem.docstring,
        full_source=problem.source,
        num_workers=args.num_workers,
        previous_round_text=previous_round_text,
    )
    if not budget.can_start_call():
        log_event(
            budget,
            "time_budget_exceeded",
            phase="main_plan_start",
            **{"round": round_index},
        )
        return finish_round(empty_round_result(round_index, problem.problem_id, args.num_workers, "time_budget_exceeded"))
    plan_timeout = budget.timeout_for(args.codex_timeout_sec)
    log_event(
        budget,
        "main_plan_start",
        **{"round": round_index, "timeout_sec": plan_timeout},
    )
    plan_run = run_codex(
        args=args,
        api_key=api_key,
        prompt=planning_prompt,
        work_dir=round_dir / "main_plan",
        timeout_sec=plan_timeout,
    )
    codex_calls += 1
    log_event(
        budget,
        "main_plan_end",
        **{
            "round": round_index,
            "returncode": plan_run.returncode,
            "timed_out": plan_run.timed_out,
            "codex_elapsed_sec": plan_run.elapsed_sec,
        },
    )
    plan_text = plan_run.last_message or plan_run.stdout or plan_run.combined_output or ""
    plan = parse_main_plan_output(plan_text)
    plan_parse_ok = bool(plan.tasks) and not plan.errors
    log_event(
        budget,
        "main_plan_parse",
        **{
            "round": round_index,
            "main_plan_parse_ok": plan_parse_ok,
            "main_plan_error_count": len(plan.errors),
            "num_tasks": len(plan.tasks),
        },
    )
    if not plan_parse_ok:
        return finish_round({
            "round": round_index,
            "problem_id": problem.problem_id,
            "num_workers": args.num_workers,
            "main_plan_parse_ok": False,
            "main_plan_errors": plan.errors,
            "num_worker_reports": 0,
            "proof_patch_parse_ok": False,
            "proof_patch_parse_errors": [],
            "submit_result": None,
            "success": False,
            "check_status": None,
            "codex_calls": codex_calls,
            "stopped_reason": "main_plan_parse_failed",
        })

    worker_reports_by_index: dict[int, str] = {}
    worker_return_order: list[str] = []
    worker_tasks = plan.tasks[: args.num_workers]
    max_concurrency = effective_max_concurrency(args)
    futures = {}
    executor = ThreadPoolExecutor(max_workers=max_concurrency)
    interrupted = False
    try:
        next_worker_index = 0
        while next_worker_index < len(worker_tasks) and len(futures) < max_concurrency:
            task = worker_tasks[next_worker_index]
            worker_id = task["worker_id"]
            if not budget.can_start_call():
                stopped_reason = "time_budget_exceeded"
                log_event(
                    budget,
                    "time_budget_exceeded",
                    phase="worker_start",
                    **{"round": round_index, "worker": worker_id},
                )
                break
            worker_timeout = budget.timeout_for(args.codex_timeout_sec)
            log_event(
                budget,
                "worker_start",
                **{
                    "round": round_index,
                    "worker": worker_id,
                    "timeout_sec": worker_timeout,
                    "max_concurrency": max_concurrency,
                },
            )
            future = executor.submit(
                run_assigned_worker,
                args=args,
                api_key=api_key,
                problem=problem,
                task=task,
                round_dir=round_dir,
                timeout_sec=worker_timeout,
            )
            futures[future] = (next_worker_index, task)
            next_worker_index += 1

        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                index, task = futures.pop(future)
                worker_id = task["worker_id"]
                try:
                    worker_run = future.result()
                except Exception as exc:
                    log_event(
                        budget,
                        "worker_error",
                        **{
                            "round": round_index,
                            "worker": worker_id,
                            "error": type(exc).__name__,
                            "message": str(exc),
                        },
                    )
                    worker_reports_by_index[index] = f"Worker {worker_id} failed: {type(exc).__name__}: {exc}"
                    worker_return_order.append(worker_id)
                    continue

                codex_calls += 1
                worker_return_order.append(worker_id)
                log_event(
                    budget,
                    "worker_end",
                    **{
                        "round": round_index,
                        "worker": worker_id,
                        "returncode": worker_run.returncode,
                        "timed_out": worker_run.timed_out,
                        "codex_elapsed_sec": worker_run.elapsed_sec,
                    },
                )
                if worker_run.timed_out:
                    log_event(
                        budget,
                        "worker_timeout",
                        **{"round": round_index, "worker": worker_id},
                    )
                worker_reports_by_index[index] = worker_report_text(worker_id, worker_run)

            while (
                stopped_reason is None
                and next_worker_index < len(worker_tasks)
                and len(futures) < max_concurrency
            ):
                task = worker_tasks[next_worker_index]
                worker_id = task["worker_id"]
                if not budget.can_start_call():
                    stopped_reason = "time_budget_exceeded"
                    log_event(
                        budget,
                        "time_budget_exceeded",
                        phase="worker_start",
                        **{"round": round_index, "worker": worker_id},
                    )
                    break
                worker_timeout = budget.timeout_for(args.codex_timeout_sec)
                log_event(
                    budget,
                    "worker_start",
                    **{
                        "round": round_index,
                        "worker": worker_id,
                        "timeout_sec": worker_timeout,
                        "max_concurrency": max_concurrency,
                    },
                )
                future = executor.submit(
                    run_assigned_worker,
                    args=args,
                    api_key=api_key,
                    problem=problem,
                    task=task,
                    round_dir=round_dir,
                    timeout_sec=worker_timeout,
                )
                futures[future] = (next_worker_index, task)
                next_worker_index += 1
    except KeyboardInterrupt:
        interrupted = True
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        if not interrupted:
            executor.shutdown(wait=True)

    worker_reports = [
        worker_reports_by_index[index]
        for index in range(len(worker_tasks))
        if index in worker_reports_by_index
    ]
    worker_reports_count = len(worker_reports)

    if stopped_reason == "time_budget_exceeded":
        return finish_round({
            "round": round_index,
            "problem_id": problem.problem_id,
            "num_workers": args.num_workers,
            "main_plan_parse_ok": bool(plan.tasks) and not plan.errors,
            "main_plan_errors": plan.errors,
            "num_worker_reports": worker_reports_count,
            "proof_patch_parse_ok": False,
            "proof_patch_parse_errors": [],
            "submit_result": None,
            "success": False,
            "check_status": None,
            "codex_calls": codex_calls,
            "stopped_reason": stopped_reason,
            "max_concurrency": max_concurrency,
            "worker_reports_count": worker_reports_count,
            "worker_return_order": worker_return_order,
        })

    if not budget.can_start_call():
        log_event(
            budget,
            "time_budget_exceeded",
            phase="synthesis_start",
            **{"round": round_index},
        )
        return finish_round(empty_round_result(round_index, problem.problem_id, args.num_workers, "time_budget_exceeded", codex_calls))
    synthesis_prompt = build_main_agent_synthesis_prompt(
        problem_id=problem.problem_id,
        theorem_name=problem.theorem_name,
        theorem_statement=problem.source,
        docstring=problem.docstring,
        full_source=problem.source,
        task_plan_text=plan_text,
        worker_reports_text="\n\n".join(worker_reports),
        previous_round_text=previous_round_text,
    )
    synthesis_timeout = budget.timeout_for(args.codex_timeout_sec)
    log_event(
        budget,
        "synthesis_start",
        **{"round": round_index, "timeout_sec": synthesis_timeout},
    )
    synthesis_run = run_codex(
        args=args,
        api_key=api_key,
        prompt=synthesis_prompt,
        work_dir=round_dir / "main_synthesis",
        timeout_sec=synthesis_timeout,
    )
    codex_calls += 1
    log_event(
        budget,
        "synthesis_end",
        **{
            "round": round_index,
            "returncode": synthesis_run.returncode,
            "timed_out": synthesis_run.timed_out,
            "codex_elapsed_sec": synthesis_run.elapsed_sec,
        },
    )
    synthesis_text = (
        synthesis_run.last_message
        or synthesis_run.stdout
        or synthesis_run.combined_output
        or ""
    )
    synthesis = parse_worker_output(synthesis_text)
    synthesis_proof_patch_preview = truncate_text(synthesis.proof_patch or "", 2000)
    log_event(
        budget,
        "synthesis_parse",
        **{
            "round": round_index,
            "proof_patch_parse_ok": synthesis.proof_patch is not None,
            "parse_error_count": len(synthesis.errors),
        },
    )

    submit_result = None
    if synthesis.proof_patch is not None:
        if not budget.can_start_call():
            stopped_reason = "time_budget_exceeded"
            log_event(
                budget,
                "time_budget_exceeded",
                phase="lean_check_start",
                **{"round": round_index},
            )
        else:
            lean_timeout = budget.timeout_for(args.lean_timeout_sec)
            if lean_timeout <= 0:
                stopped_reason = "time_budget_exceeded"
                log_event(
                    budget,
                    "time_budget_exceeded",
                    phase="lean_check_start",
                    **{"round": round_index},
                )
            else:
                log_event(
                    budget,
                    "lean_check_start",
                    **{"round": round_index, "timeout_sec": lean_timeout},
                )
                lean_start = time.perf_counter()
                submitted = submit_patch(
                    putnam_root=putnam_root,
                    db_path=db_path,
                    problem_id=problem.problem_id,
                    worker_id="main_agent",
                    proof_patch=synthesis.proof_patch,
                    run_dir=run_dir,
                    timeout_sec=lean_timeout,
                )
                lean_elapsed = time.perf_counter() - lean_start
                submit_result = patch_submission_result_to_dict(submitted)
                if stopped_reason is None and not submit_result.get("success"):
                    if submit_result.get("check_status") == "timed out":
                        stopped_reason = "lean_check_timed_out"
                    else:
                        stopped_reason = "lean_check_failed"
                log_event(
                    budget,
                    "lean_check_end",
                    **{
                        "round": round_index,
                        "success": submit_result.get("success"),
                        "check_status": submit_result.get("check_status"),
                        "lean_elapsed_sec": lean_elapsed,
                    },
                )
                print(json.dumps(submit_result, ensure_ascii=False, indent=2), flush=True)
    else:
        stopped_reason = "no_proof_patch"

    attempt_path = submit_result.get("attempt_path") if submit_result else None
    error_messages = submit_result.get("error_messages") if submit_result else None
    return finish_round({
        "round": round_index,
        "problem_id": problem.problem_id,
        "num_workers": args.num_workers,
        "main_plan_parse_ok": bool(plan.tasks) and not plan.errors,
        "main_plan_errors": plan.errors,
        "num_worker_reports": worker_reports_count,
        "proof_patch_parse_ok": synthesis.proof_patch is not None,
        "proof_patch_parse_errors": synthesis.errors,
        "synthesis_summary": synthesis.summary,
        "synthesis_parse_errors": synthesis.errors,
        "synthesis_proof_patch_preview": synthesis_proof_patch_preview,
        "submit_result": submit_result,
        "attempt_path": attempt_path,
        "error_messages": error_messages,
        "success": bool(submit_result and submit_result.get("success")),
        "check_status": submit_result.get("check_status") if submit_result else None,
        "codex_calls": codex_calls,
        "stopped_reason": stopped_reason,
        "max_concurrency": max_concurrency,
        "worker_reports_count": worker_reports_count,
        "worker_return_order": worker_return_order,
    })


def run_codex(
    *,
    args: argparse.Namespace,
    api_key: str | None,
    prompt: str,
    work_dir: Path,
    timeout_sec: float | int,
):
    return run_codex_exec(
        prompt=prompt,
        work_dir=work_dir,
        codex_command=args.codex_command,
        model=args.model,
        api_key=api_key,
        base_url=args.base_url,
        provider=args.provider,
        timeout_sec=max(1, int(timeout_sec)),
    )


def run_assigned_worker(
    *,
    args: argparse.Namespace,
    api_key: str | None,
    problem: object,
    task: dict[str, Any],
    round_dir: Path,
    timeout_sec: float | int,
):
    worker_prompt = build_main_assigned_worker_prompt(
        problem_id=problem.problem_id,
        theorem_name=problem.theorem_name,
        theorem_statement=problem.source,
        docstring=problem.docstring,
        full_source=problem.source,
        worker_id=task["worker_id"],
        role=task["role"],
        task=task["task"],
    )
    return run_codex(
        args=args,
        api_key=api_key,
        prompt=worker_prompt,
        work_dir=round_dir / "workers" / task["worker_id"],
        timeout_sec=timeout_sec,
    )


def worker_report_text(worker_id: str, worker_run: object) -> str:
    return (
        worker_run.last_message
        or worker_run.stdout
        or worker_run.combined_output
        or worker_run.stderr
        or f"Worker {worker_id} returned no output."
    )


def effective_max_concurrency(args: argparse.Namespace) -> int:
    num_workers = max(1, int(args.num_workers))
    requested = args.max_concurrency
    if requested is None:
        requested = num_workers
    return max(1, min(int(requested), num_workers))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the single Main Agent baseline.")
    parser.add_argument("--putnam-root", default="data/PutnamBench")
    parser.add_argument("--problem-id")
    parser.add_argument("--num-workers", type=int, default=3)
    parser.add_argument("--max-concurrency", type=int)
    parser.add_argument("--max-rounds", type=int, default=1)
    parser.add_argument("--db", default="runs/single_main_agent/context.sqlite")
    parser.add_argument("--run-dir", default="runs/single_main_agent")
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
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{db_path}{suffix}")
        if path.exists():
            path.unlink()
    if run_dir.exists():
        shutil.rmtree(run_dir)


def build_previous_round_text(round_result: dict[str, Any]) -> str:
    submit_result = round_result.get("submit_result") or {}
    attempt_path = round_result.get("attempt_path") or submit_result.get("attempt_path")
    attempt_text = read_attempt_preview(attempt_path, max_chars=2500)
    lines = [
        f"round: {round_result.get('round')}",
        f"main_plan_parse_ok: {round_result.get('main_plan_parse_ok')}",
        f"main_plan_errors: {truncate_json(round_result.get('main_plan_errors'), 1200)}",
        f"num_worker_reports: {round_result.get('num_worker_reports')}",
        f"proof_patch_parse_ok: {round_result.get('proof_patch_parse_ok')}",
        f"proof_patch_parse_errors: {truncate_json(round_result.get('proof_patch_parse_errors'), 1200)}",
        f"synthesis_summary: {truncate_text(str(round_result.get('synthesis_summary') or ''), 800)}",
        f"check_status: {round_result.get('check_status') or submit_result.get('check_status')}",
        f"stopped_reason: {round_result.get('stopped_reason')}",
        f"error_messages: {truncate_json(round_result.get('error_messages') or submit_result.get('error_messages'), 2500)}",
        f"attempt_path: {attempt_path}",
        "",
        "Previous synthesis proof patch preview:",
        "```lean",
        truncate_text(str(round_result.get("synthesis_proof_patch_preview") or ""), 2500) or "(none)",
        "```",
    ]
    if attempt_text:
        lines.extend(
            [
                "",
                "Previous failed Lean attempt file preview:",
                "```lean",
                attempt_text,
                "```",
            ]
        )
    lines.extend(
        [
            "",
            "Next-round guidance:",
            "- Do not repeat the failed proof patch.",
            "- Avoid tactics and rewrite patterns that caused the previous Lean errors.",
            "- Prefer a shorter and more robust proof if the previous patch timed out.",
            "- If previous Lean check timed out, avoid heavy normalization/search tactics.",
        ]
    )
    return truncate_text("\n".join(lines), 9000)


def truncate_json(value: object, max_chars: int) -> str:
    if value is None:
        return "None"
    try:
        text = json.dumps(value, ensure_ascii=False)
    except TypeError:
        text = str(value)
    return truncate_text(text, max_chars)


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"{text[:max_chars]}\n... [truncated {omitted} chars]"


def read_attempt_preview(attempt_path: object, max_chars: int) -> str:
    if not attempt_path:
        return ""
    path = Path(str(attempt_path))
    if not path.is_file():
        return ""
    try:
        return truncate_text(path.read_text(encoding="utf-8", errors="replace"), max_chars)
    except OSError:
        return ""


def log_event(budget: "TimeBudget", event: str, **fields: object) -> None:
    elapsed_sec = budget.elapsed()
    data: dict[str, object] = {}
    remaining = budget.remaining()
    if remaining is not None and "remaining_sec" not in fields:
        data["remaining_sec"] = remaining
    data.update(fields)
    rendered = " ".join(
        f"{key}={format_log_value(value)}"
        for key, value in data.items()
    )
    prefix = f"[{elapsed_sec:.1f}s] {event}"
    print(f"{prefix} {rendered}".rstrip(), flush=True)


def format_log_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "none"
    if isinstance(value, float):
        return f"{value:.1f}"
    text = str(value)
    if any(ch.isspace() for ch in text):
        return json.dumps(text, ensure_ascii=False)
    return text


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


def initial_summary(args: argparse.Namespace, problem_id: str) -> dict[str, Any]:
    return {
        "problem_id": problem_id,
        "num_workers": args.num_workers,
        "max_concurrency": effective_max_concurrency(args),
        "max_rounds": args.max_rounds,
        "rounds_completed": 0,
        "success": False,
        "final_verified_patch_seq": None,
        "final_proof_seq": None,
        "total_codex_calls": 0,
        "total_elapsed_sec": 0.0,
        "check_status_by_round": [],
        "time_budget_sec": args.time_budget_sec,
        "wall_elapsed_sec": 0.0,
        "time_to_success_sec": None,
        "stopped_reason": None,
        "budget_exhausted": False,
    }


def main_stopped_reason(
    *,
    success: bool,
    budget_exhausted: bool,
    rounds_completed: int,
    max_rounds: int,
) -> str:
    if success:
        return "success"
    if budget_exhausted:
        return "time_budget_exceeded"
    if rounds_completed >= max_rounds:
        return "max_rounds_reached"
    return "error"


def empty_round_result(
    round_index: int,
    problem_id: str,
    num_workers: int,
    stopped_reason: str,
    codex_calls: int = 0,
) -> dict[str, Any]:
    return {
        "round": round_index,
        "problem_id": problem_id,
        "num_workers": num_workers,
        "main_plan_parse_ok": False,
        "main_plan_errors": [],
        "num_worker_reports": 0,
        "proof_patch_parse_ok": False,
        "proof_patch_parse_errors": [],
        "submit_result": None,
        "success": False,
        "check_status": None,
        "codex_calls": codex_calls,
        "stopped_reason": stopped_reason,
    }


if __name__ == "__main__":
    raise SystemExit(main())
