from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from src.lean.errors import LeanError, parse_lean_errors
from src.lean.patch import contains_forbidden_placeholders, make_attempt_file


@dataclass(frozen=True)
class LeanCheckResult:
    success: bool
    returncode: int
    attempt_path: Path
    elapsed_sec: float
    has_sorry: bool
    has_admit: bool
    has_axiom: bool
    errors: list[LeanError]
    stdout: str
    stderr: str


def check_lean_file(
    lean4_root: Path, attempt_path: Path, timeout_sec: int = 180
) -> LeanCheckResult:
    start = time.perf_counter()
    absolute_attempt_path = attempt_path.resolve()
    stdout = ""
    stderr = ""
    returncode = -1

    try:
        completed = subprocess.run(
            ["lake", "env", "lean", str(absolute_attempt_path)],
            cwd=lean4_root,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        returncode = completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = _to_text(exc.stdout)
        stderr = _to_text(exc.stderr)
        timeout_message = f"Timed out after {timeout_sec} seconds."
        stderr = f"{stderr}\n{timeout_message}".strip()
    except FileNotFoundError as exc:
        stderr = str(exc)

    elapsed_sec = time.perf_counter() - start
    placeholder_flags = _placeholder_flags_for_file(absolute_attempt_path)
    combined_output = f"{stdout}\n{stderr}"
    has_sorry_warning = _has_sorry_warning(combined_output)
    success = (
        returncode == 0
        and not placeholder_flags["has_sorry"]
        and not placeholder_flags["has_admit"]
        and not placeholder_flags["has_axiom"]
        and not has_sorry_warning
    )

    return LeanCheckResult(
        success=success,
        returncode=returncode,
        attempt_path=absolute_attempt_path,
        elapsed_sec=elapsed_sec,
        has_sorry=placeholder_flags["has_sorry"],
        has_admit=placeholder_flags["has_admit"],
        has_axiom=placeholder_flags["has_axiom"],
        errors=parse_lean_errors(combined_output),
        stdout=stdout,
        stderr=stderr,
    )


def check_patch_for_problem(
    putnam_root: Path,
    problem_path: Path,
    proof_patch: str,
    run_dir: Path,
    attempt_name: str = "attempt_001.lean",
    timeout_sec: int = 180,
) -> LeanCheckResult:
    start = time.perf_counter()
    original_source = problem_path.read_text(encoding="utf-8")
    attempt_path = run_dir / attempt_name
    make_attempt_file(original_source, proof_patch, attempt_path)
    absolute_attempt_path = attempt_path.resolve()

    patch_flags = contains_forbidden_placeholders(proof_patch)
    if (
        patch_flags["has_sorry"]
        or patch_flags["has_admit"]
        or patch_flags["has_axiom"]
    ):
        return LeanCheckResult(
            success=False,
            returncode=-2,
            attempt_path=absolute_attempt_path,
            elapsed_sec=time.perf_counter() - start,
            has_sorry=patch_flags["has_sorry"],
            has_admit=patch_flags["has_admit"],
            has_axiom=patch_flags["has_axiom"],
            errors=[],
            stdout="",
            stderr=(
                "Rejected before Lean check because proof patch contains "
                "forbidden placeholders."
            ),
        )

    return check_lean_file(putnam_root / "lean4", attempt_path, timeout_sec=timeout_sec)


def _placeholder_flags_for_file(path: Path) -> dict[str, bool]:
    try:
        return contains_forbidden_placeholders(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"has_sorry": False, "has_admit": False, "has_axiom": False}


def _has_sorry_warning(output: str) -> bool:
    normalized = output.lower()
    return "declaration uses 'sorry'" in normalized or "uses sorry" in normalized


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
