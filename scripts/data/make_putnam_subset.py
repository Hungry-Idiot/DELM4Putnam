from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common.io import write_text
from src.data.putnam_lean import load_all_problems


def main() -> None:
    args = parse_args()
    putnam_root = Path(args.putnam_root)
    out_path = Path(args.out)

    problems = load_all_problems(putnam_root)
    selected = [
        problem
        for problem in problems
        if problem.line_count <= args.max_lines and problem.has_sorry
    ]
    selected.sort(key=lambda problem: (problem.line_count, problem.problem_id))
    selected = selected[: args.max_count]

    write_text(out_path, "\n".join(problem.problem_id for problem in selected) + "\n")

    print("Selected PutnamBench Lean4 subset")
    print(f"max_count: {args.max_count}")
    print(f"max_lines: {args.max_lines}")
    print(f"selected: {len(selected)}")
    print()
    for problem in selected:
        print(f"{problem.problem_id}\t{problem.line_count}\t{problem.relative_path}")
    print()
    print(f"wrote ids: {out_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a small Putnam problem id list.")
    parser.add_argument("--putnam-root", default="data/PutnamBench")
    parser.add_argument("--out", default="outputs/putnam_small_ids.txt")
    parser.add_argument("--max-count", type=int, default=10)
    parser.add_argument("--max-lines", type=int, default=80)
    return parser.parse_args()


if __name__ == "__main__":
    main()
