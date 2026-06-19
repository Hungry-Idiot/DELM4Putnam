from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.controller.patch_service import (
    patch_submission_result_to_dict,
    submit_patch,
)
from src.shared_context.renderer import render_notes
from src.shared_context.storage import get_notes


DEFAULT_SUBSET_PATH = Path("outputs/putnam_small_ids.txt")


def main() -> None:
    args = parse_args()
    problem_id = args.problem_id or default_problem_id(DEFAULT_SUBSET_PATH)
    proof_patch = read_proof_patch(args)

    result = submit_patch(
        putnam_root=Path(args.putnam_root),
        db_path=Path(args.db),
        problem_id=problem_id,
        worker_id=args.worker,
        proof_patch=proof_patch,
        run_dir=Path(args.run_dir),
        timeout_sec=args.timeout_sec,
    )

    print(json.dumps(patch_submission_result_to_dict(result), ensure_ascii=False, indent=2))
    print()
    print("Shared Context")
    print(render_notes(get_notes(Path(args.db), problem_id=problem_id)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit one proof patch.")
    parser.add_argument("--putnam-root", default="data/PutnamBench")
    parser.add_argument("--problem-id")
    parser.add_argument("--worker", default="A_manual")
    parser.add_argument("--db", default="runs/submit_patch_once/context.sqlite")
    parser.add_argument("--run-dir", default="runs/submit_patch_once")
    parser.add_argument("--timeout-sec", type=int, default=180)
    parser.add_argument("--proof-patch")
    parser.add_argument("--proof-patch-file")
    return parser.parse_args()


def default_problem_id(path: Path) -> str:
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            problem_id = line.strip()
            if problem_id:
                return problem_id
    return "putnam_1968_a1"


def read_proof_patch(args: argparse.Namespace) -> str:
    if args.proof_patch_file:
        return Path(args.proof_patch_file).read_text(encoding="utf-8")
    if args.proof_patch is not None:
        return args.proof_patch
    return "by\n  sorry"


if __name__ == "__main__":
    main()
