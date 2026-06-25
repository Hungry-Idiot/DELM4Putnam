from __future__ import annotations

import argparse
import sys
from pathlib import Path
from statistics import mean
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common.io import write_json
from src.data.putnam_lean import PutnamLeanProblem, load_all_problems


def main() -> None:
    args = parse_args()
    putnam_root = Path(args.putnam_root)
    out_path = Path(args.out)

    problems = load_all_problems(putnam_root)
    if args.limit > 0:
        problems = problems[: args.limit]

    stats = build_stats(problems)
    write_json(out_path, stats)
    print_report(stats, out_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect PutnamBench Lean4 files.")
    parser.add_argument("--putnam-root", default="data/PutnamBench")
    parser.add_argument("--out", default="outputs/putnam_lean_inspection.json")
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def build_stats(problems: list[PutnamLeanProblem]) -> dict[str, Any]:
    line_counts = [problem.line_count for problem in problems]
    return {
        "total_files": len(problems),
        "files_with_sorry": sum(problem.has_sorry for problem in problems),
        "files_with_solution_abbrev": sum(
            problem.has_solution_abbrev for problem in problems
        ),
        "line_count": {
            "min": min(line_counts) if line_counts else 0,
            "avg": mean(line_counts) if line_counts else 0,
            "max": max(line_counts) if line_counts else 0,
        },
        "first_10": [problem_summary(problem) for problem in problems[:10]],
    }


def problem_summary(problem: PutnamLeanProblem) -> dict[str, Any]:
    return {
        "problem_id": problem.problem_id,
        "relative_path": problem.relative_path,
        "theorem_name": problem.theorem_name,
        "line_count": problem.line_count,
        "has_sorry": problem.has_sorry,
        "has_solution_abbrev": problem.has_solution_abbrev,
    }


def print_report(stats: dict[str, Any], out_path: Path) -> None:
    line_count = stats["line_count"]
    print("PutnamBench Lean4 inspection")
    print(f"total files: {stats['total_files']}")
    print(f"files with sorry: {stats['files_with_sorry']}")
    print(f"files with solution abbrev: {stats['files_with_solution_abbrev']}")
    print(
        "line_count min/avg/max: "
        f"{line_count['min']}/{line_count['avg']:.2f}/{line_count['max']}"
    )
    print()
    print("first 10 problems:")
    for problem in stats["first_10"]:
        print(
            f"- {problem['problem_id']} "
            f"lines={problem['line_count']} "
            f"sorry={problem['has_sorry']} "
            f"solution_abbrev={problem['has_solution_abbrev']} "
            f"path={problem['relative_path']}"
        )
    print()
    print(f"wrote JSON: {out_path}")


if __name__ == "__main__":
    main()
