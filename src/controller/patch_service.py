from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.data.putnam_lean import PutnamLeanProblem, load_all_problems
from src.lean.checker import LeanCheckResult, check_patch_for_problem
from src.shared_context.schema import SharedNote
from src.shared_context.storage import append_note, get_notes, init_db


@dataclass(frozen=True)
class PatchSubmissionResult:
    problem_id: str
    worker_id: str
    proof_patch_seq: int | None
    lean_error_seq: int | None
    verified_patch_seq: int | None
    final_proof_seq: int | None
    attempt_path: str
    success: bool
    check_status: str
    returncode: int
    elapsed_sec: float
    error_messages: list[str]


def find_problem_by_id(putnam_root: Path, problem_id: str) -> PutnamLeanProblem:
    for problem in load_all_problems(putnam_root):
        if problem.problem_id == problem_id:
            return problem
    raise ValueError(f"PutnamBench Lean4 problem not found: {problem_id}")


def submit_patch(
    *,
    putnam_root: Path,
    db_path: Path,
    problem_id: str,
    worker_id: str,
    proof_patch: str,
    run_dir: Path,
    timeout_sec: int = 180,
) -> PatchSubmissionResult:
    init_db(db_path)
    problem = find_problem_by_id(putnam_root, problem_id)
    _ensure_problem_note(db_path, problem)

    proof_patch_note = append_note(
        db_path,
        SharedNote(
            seq=None,
            problem_id=problem_id,
            worker_id=worker_id,
            type="PROOF_PATCH",
            content=_bounded_content(proof_patch, "proof patch"),
            metadata={"source": "patch_service"},
        ),
        writer_role="agent",
    )
    proof_patch_seq = proof_patch_note.seq
    attempt_name = f"attempt_{proof_patch_seq or 0:04d}.lean"

    check_result = check_patch_for_problem(
        putnam_root=putnam_root,
        problem_path=problem.lean_path,
        proof_patch=proof_patch,
        run_dir=run_dir / worker_id / "attempts",
        attempt_name=attempt_name,
        timeout_sec=timeout_sec,
    )
    check_status = classify_check_result(check_result)
    error_messages = _error_messages(check_result)

    verified_patch_seq: int | None = None
    final_proof_seq: int | None = None
    lean_error_seq: int | None = None

    if check_result.success:
        verified_patch_note = append_note(
            db_path,
            SharedNote(
                seq=None,
                problem_id=problem_id,
                worker_id="patch_service",
                type="VERIFIED_PATCH",
                content=_bounded_content(proof_patch, "verified proof patch"),
                attempt_path=str(check_result.attempt_path),
                target_seq=proof_patch_seq,
                status="verified",
                metadata=_checker_metadata(
                    check_result, proof_patch_seq, check_status, timeout_sec
                ),
            ),
            writer_role="system",
        )
        final_proof_note = append_note(
            db_path,
            SharedNote(
                seq=None,
                problem_id=problem_id,
                worker_id="patch_service",
                type="FINAL_PROOF",
                content=_bounded_content(proof_patch, "final proof"),
                attempt_path=str(check_result.attempt_path),
                target_seq=verified_patch_note.seq,
                status="verified",
                metadata=_checker_metadata(
                    check_result, proof_patch_seq, check_status, timeout_sec
                ),
            ),
            writer_role="system",
        )
        verified_patch_seq = verified_patch_note.seq
        final_proof_seq = final_proof_note.seq
    else:
        lean_error_note = append_note(
            db_path,
            SharedNote(
                seq=None,
                problem_id=problem_id,
                worker_id="patch_service",
                type="LEAN_ERROR",
                content=_lean_error_content(check_status, error_messages, check_result),
                attempt_path=str(check_result.attempt_path),
                target_seq=proof_patch_seq,
                status="failed",
                metadata=_checker_metadata(
                    check_result, proof_patch_seq, check_status, timeout_sec
                ),
            ),
            writer_role="system",
        )
        lean_error_seq = lean_error_note.seq

    return PatchSubmissionResult(
        problem_id=problem_id,
        worker_id=worker_id,
        proof_patch_seq=proof_patch_seq,
        lean_error_seq=lean_error_seq,
        verified_patch_seq=verified_patch_seq,
        final_proof_seq=final_proof_seq,
        attempt_path=str(check_result.attempt_path),
        success=check_result.success,
        check_status=check_status,
        returncode=check_result.returncode,
        elapsed_sec=check_result.elapsed_sec,
        error_messages=error_messages,
    )


def patch_submission_result_to_dict(
    result: PatchSubmissionResult,
) -> dict[str, Any]:
    return asdict(result)


def classify_check_result(result: LeanCheckResult) -> str:
    if result.returncode == -2:
        return "rejected before Lean check"
    if "Timed out after" in result.stderr:
        return "timed out"
    if result.success:
        return "checked by Lean and passed"
    return "checked by Lean and failed"


def _ensure_problem_note(db_path: Path, problem: PutnamLeanProblem) -> None:
    existing = [
        note
        for note in get_notes(db_path, problem_id=problem.problem_id)
        if note.type == "PROBLEM"
    ]
    if existing:
        return

    append_note(
        db_path,
        SharedNote(
            seq=None,
            problem_id=problem.problem_id,
            worker_id="patch_service",
            type="PROBLEM",
            content=_problem_content(problem),
            metadata={"source": "patch_service"},
        ),
        writer_role="system",
    )


def _problem_content(problem: PutnamLeanProblem) -> str:
    parts = [
        f"problem_id: {problem.problem_id}",
        f"theorem_name: {problem.theorem_name}",
        f"relative_path: {problem.relative_path}",
    ]
    if problem.docstring:
        parts.append(f"docstring: {problem.docstring}")
    return _bounded_content("\n".join(parts), "problem")


def _checker_metadata(
    result: LeanCheckResult,
    proof_patch_seq: int | None,
    check_status: str,
    timeout_sec: int,
) -> dict[str, Any]:
    return {
        "checker": "lean",
        "returncode": result.returncode,
        "elapsed_sec": result.elapsed_sec,
        "timeout_sec": timeout_sec,
        "proof_patch_seq": proof_patch_seq,
        "attempt_path": str(result.attempt_path),
        "check_status": check_status,
    }


def _error_messages(result: LeanCheckResult) -> list[str]:
    messages = [error.message for error in result.errors]
    if messages:
        return messages
    stderr = result.stderr.strip()
    if stderr:
        return [_truncate(stderr.replace("\n", " "), 500)]
    return ["Lean check failed without parsed errors."]


def _lean_error_content(
    check_status: str, error_messages: list[str], result: LeanCheckResult
) -> str:
    lines = [f"check_status: {check_status}"]
    if _is_timeout(check_status, result):
        lines.extend(
            [
                "summary: Lean checker timed out. The proof patch may have triggered expensive tactic search or simplification.",
                "suggestion: Avoid large `simp`/`norm_num` calls with many intervalIntegral lemmas. Try proving smaller lemmas, use more explicit rewrites, or simplify the algebraic decomposition first.",
            ]
        )
    lines.append("errors:")
    lines.extend(f"- {message}" for message in error_messages[:5])
    return _bounded_content("\n".join(lines), "Lean error")


def _is_timeout(check_status: str, result: LeanCheckResult) -> bool:
    return check_status == "timed out" or (
        result.returncode == -1 and "Timed out after" in result.stderr
    )


def _bounded_content(text: str, label: str, max_chars: int = 3900) -> str:
    if len(text) <= max_chars:
        return text
    return f"{label} truncated to {max_chars} chars:\n{_truncate(text, max_chars)}"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
