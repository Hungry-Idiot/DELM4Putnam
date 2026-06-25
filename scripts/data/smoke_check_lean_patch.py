from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.putnam_lean import PutnamLeanProblem, load_all_problems
from src.lean.checker import LeanCheckResult, check_patch_for_problem


DEFAULT_SUBSET_PATH = Path("outputs/putnam_small_ids.txt")


def main() -> None:
    args = parse_args()
    putnam_root = Path(args.putnam_root)
    problem_id = args.problem_id or read_first_problem_id(DEFAULT_SUBSET_PATH)
    problem = find_problem(putnam_root, problem_id)

    result = check_patch_for_problem(
        putnam_root=putnam_root,
        problem_path=problem.lean_path,
        proof_patch=args.proof_patch,
        run_dir=Path(args.run_dir),
        timeout_sec=args.timeout_sec,
    )
    print_result(problem, result)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-check one Lean proof patch.")
    parser.add_argument("--putnam-root", default="data/PutnamBench")
    parser.add_argument("--problem-id")
    parser.add_argument("--proof-patch", default="by\n  sorry")
    parser.add_argument("--run-dir", default="runs/smoke_check")
    parser.add_argument("--timeout-sec", type=int, default=180)
    return parser.parse_args()


def read_first_problem_id(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(
            f"No --problem-id provided and default subset file was not found: {path}"
        )
    for line in path.read_text(encoding="utf-8").splitlines():
        problem_id = line.strip()
        if problem_id:
            return problem_id
    raise ValueError(f"No problem ids found in default subset file: {path}")


def find_problem(putnam_root: Path, problem_id: str) -> PutnamLeanProblem:
    for problem in load_all_problems(putnam_root):
        if problem.problem_id == problem_id:
            return problem
    raise ValueError(f"Problem id not found in PutnamBench Lean4 data: {problem_id}")


def print_result(problem: PutnamLeanProblem, result: LeanCheckResult) -> None:
    print("Lean patch smoke check")
    print(f"problem_id: {problem.problem_id}")
    print(f"problem_path: {problem.relative_path}")
    print(f"attempt_path: {result.attempt_path}")
    print(f"success: {result.success}")
    print(f"returncode: {result.returncode}")
    print(f"check_status: {check_status(result)}")
    print(f"elapsed_sec: {result.elapsed_sec:.3f}")
    print(
        "forbidden placeholders: "
        f"sorry={result.has_sorry}, admit={result.has_admit}, axiom={result.has_axiom}"
    )
    print()
    print("errors:")
    if result.errors:
        for error in result.errors:
            location = (
                f"{error.line}:{error.column}"
                if error.line is not None and error.column is not None
                else "unknown"
            )
            print(f"- {location}: {error.message}")
    else:
        print("- none")

    if result.stderr.strip():
        print()
        print("stderr:")
        print(result.stderr.strip())


def check_status(result: LeanCheckResult) -> str:
    if result.returncode == -2:
        return "rejected before Lean check"
    if "Timed out after" in result.stderr:
        return "timed out"
    if result.success:
        return "checked by Lean and passed"
    return "checked by Lean and failed"


if __name__ == "__main__":
    main()
