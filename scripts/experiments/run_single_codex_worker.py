from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args: object, **kwargs: object) -> bool:
        return False


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from src.codex.parser import WorkerParseResult, parse_worker_output
from src.codex.prompts import build_single_worker_prompt
from src.codex.runner import run_codex_exec
from src.controller.patch_service import (
    find_problem_by_id,
    patch_submission_result_to_dict,
    submit_patch,
)
from src.shared_context.renderer import render_full_context, render_worker_context
from src.shared_context.schema import SharedNote
from src.shared_context.storage import append_note, get_notes, init_db


DEFAULT_SUBSET_PATH = Path("outputs/putnam_small_ids.txt")


def main() -> int:
    args = parse_args()
    putnam_root = Path(args.putnam_root)
    db_path = Path(args.db)
    run_dir = Path(args.run_dir)
    if args.fresh:
        reset_run_state(db_path, run_dir, args.worker)

    problem_id = args.problem_id or default_problem_id(DEFAULT_SUBSET_PATH)
    problem = find_problem_by_id(putnam_root, problem_id)

    init_db(db_path)
    ensure_problem_note(db_path, problem)
    notes = get_notes(db_path, problem_id=problem_id)
    shared_context_text = render_worker_context(notes)
    prompt = build_single_worker_prompt(
        problem_id=problem.problem_id,
        theorem_name=problem.theorem_name,
        theorem_statement=problem.source,
        docstring=problem.docstring,
        shared_context_text=shared_context_text,
    )

    if args.dry_run:
        prompt_path = run_dir / args.worker / "codex" / "prompt.txt"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        print(prompt)
        print()
        print("Codex provider configuration")
        print(f"provider: {args.provider}")
        print(f"base_url: {args.base_url or '(not set)'}")
        print(f"model: {args.model or '(not set)'}")
        print(f"API key will be read from env var: {args.api_key_env}")
        print(f"shared_context_notes: {len(notes)}")
        print(f"shared_context_lean_errors: {sum(note.type == 'LEAN_ERROR' for note in notes)}")
        print("Shared Context mode for worker prompt: worker")
        print(f"fresh_run: {args.fresh}")
        print()
        print("Manual codex exec example")
        print(manual_codex_command(args, prompt_path, use_api_key=True))
        print()
        print(
            "If your local Codex CLI rejects a flag, run `codex exec --help` "
            "and disable it with script flags."
        )
        return 0

    api_key = None
    if args.use_api_key:
        api_key = os.environ.get(args.api_key_env)
        if not api_key:
            print(
                f"--use-api-key was set, but environment variable "
                f"{args.api_key_env!r} is not set.",
                file=sys.stderr,
            )
            return 1

    codex_result = run_codex_exec(
        prompt=prompt,
        work_dir=run_dir / args.worker / "codex",
        codex_command=args.codex_command,
        model=args.model,
        api_key=api_key,
        base_url=args.base_url,
        provider=args.provider,
        timeout_sec=args.codex_timeout_sec,
        include_approval_flag=args.include_approval_flag,
        include_sandbox_flag=args.include_sandbox_flag,
    )
    parsed = parse_first_worker_patch(codex_result)
    if parsed.proof_patch is None:
        print("Failed to parse proof patch from Codex output.")
        print(json.dumps({"errors": parsed.errors}, ensure_ascii=False, indent=2))
        print_output_paths(run_dir / args.worker / "codex")
        return 1

    patch_result = submit_patch(
        putnam_root=putnam_root,
        db_path=db_path,
        problem_id=problem_id,
        worker_id=args.worker,
        proof_patch=parsed.proof_patch,
        run_dir=run_dir,
        timeout_sec=args.lean_timeout_sec,
    )

    print(json.dumps(patch_submission_result_to_dict(patch_result), ensure_ascii=False, indent=2))
    print()
    print("Shared Context")
    print(render_full_context(get_notes(db_path, problem_id=problem_id)))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one Codex theorem-proving worker.")
    parser.add_argument("--putnam-root", default="data/PutnamBench")
    parser.add_argument("--problem-id")
    parser.add_argument("--worker", default="A1")
    parser.add_argument("--db", default="runs/single_codex_worker/context.sqlite")
    parser.add_argument("--run-dir", default="runs/single_codex_worker")
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--provider", default=os.environ.get("CODEX_PROVIDER", "relay"))
    parser.add_argument(
        "--model",
        default=os.environ.get("CODEX_MODEL") or os.environ.get("RELAY_MODEL"),
    )
    parser.add_argument("--base-url", default=os.environ.get("RELAY_BASE_URL"))
    parser.add_argument("--use-api-key", action="store_true")
    parser.add_argument("--api-key-env", default="RELAY_API_KEY")
    parser.add_argument("--codex-timeout-sec", type=int, default=600)
    parser.add_argument("--lean-timeout-sec", type=int, default=180)
    parser.add_argument("--include-approval-flag", action="store_true")
    parser.add_argument(
        "--include-sandbox-flag",
        type=_str_to_bool,
        default=True,
        metavar="BOOL",
    )
    parser.add_argument("--no-sandbox-flag", dest="include_sandbox_flag", action="store_false")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def default_problem_id(path: Path) -> str:
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            problem_id = line.strip()
            if problem_id:
                return problem_id
    return "putnam_1968_a1"


def reset_run_state(db_path: Path, run_dir: Path, worker_id: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{db_path}{suffix}")
        if path.exists():
            path.unlink()

    worker_dir = run_dir / worker_id
    if worker_dir.exists():
        shutil.rmtree(worker_dir)


def ensure_problem_note(db_path: Path, problem: object) -> None:
    problem_id = getattr(problem, "problem_id")
    existing = [
        note
        for note in get_notes(db_path, problem_id=problem_id)
        if note.type == "PROBLEM"
    ]
    if existing:
        return

    parts = [
        f"problem_id: {problem_id}",
        f"theorem_name: {getattr(problem, 'theorem_name')}",
        f"relative_path: {getattr(problem, 'relative_path')}",
    ]
    docstring = getattr(problem, "docstring")
    if docstring:
        parts.append(f"docstring: {docstring}")
    append_note(
        db_path,
        SharedNote(
            seq=None,
            problem_id=problem_id,
            worker_id="single_codex_worker",
            type="PROBLEM",
            content="\n".join(parts),
            metadata={"source": "run_single_codex_worker"},
        ),
        writer_role="system",
    )


def manual_codex_command(
    args: argparse.Namespace, prompt_path: Path, use_api_key: bool
) -> str:
    model_ref = "${CODEX_MODEL}"
    base_url_ref = "${RELAY_BASE_URL}"
    lines = [
        f"{args.codex_command} exec \\",
        "  --skip-git-repo-check \\",
    ]
    if args.include_approval_flag:
        lines.append("  --ask-for-approval never \\")
    if args.include_sandbox_flag:
        lines.append("  --sandbox read-only \\")
    lines.append(f"  --model \"{model_ref}\" \\")
    provider = args.provider
    lines.extend(
        [
            f"  -c model_provider={provider} \\",
            f"  -c model_providers.{provider}.name={provider} \\",
            f"  -c model_providers.{provider}.base_url=\"{base_url_ref}\" \\",
            f"  -c model_providers.{provider}.env_key=CODEX_API_KEY \\",
            f"  --output-last-message {prompt_path.parent / 'last_message.txt'} \\",
        ]
    )
    lines.append(f"  - < {prompt_path}")
    command_text = "\n".join(lines)
    if use_api_key:
        return f"CODEX_API_KEY=${{{args.api_key_env}}} {command_text}"
    return command_text


def _str_to_bool(value: str) -> bool:
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected a boolean value, got: {value}")


def parse_first_worker_patch(codex_result: object) -> WorkerParseResult:
    errors: list[str] = []
    candidate_texts = [
        ("last_message", getattr(codex_result, "last_message", None)),
        ("stdout", getattr(codex_result, "stdout", None)),
        ("stderr", getattr(codex_result, "stderr", None)),
        ("combined_output", getattr(codex_result, "combined_output", None)),
    ]
    fallback: WorkerParseResult | None = None
    for label, text in candidate_texts:
        if not text:
            errors.append(f"{label}: empty")
            continue
        parsed = parse_worker_output(text)
        if parsed.proof_patch is not None:
            return parsed
        errors.extend(f"{label}: {error}" for error in parsed.errors)
        if fallback is None:
            fallback = parsed

    if fallback is not None:
        fallback.errors = errors
        return fallback
    return WorkerParseResult(
        status=None,
        summary=None,
        proof_patch=None,
        raw_text="",
        errors=errors,
    )


def print_output_paths(work_dir: Path) -> None:
    print("Codex output files:")
    print(f"- stdout: {work_dir / 'stdout.txt'}")
    print(f"- stderr: {work_dir / 'stderr.txt'}")
    last_message_path = work_dir / "last_message.txt"
    if last_message_path.exists():
        print(f"- last_message: {last_message_path}")
    print(f"- combined_output: {work_dir / 'combined_output.txt'}")


if __name__ == "__main__":
    raise SystemExit(main())
