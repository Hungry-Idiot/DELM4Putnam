from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.putnam_lean import PutnamLeanProblem, load_all_problems


@dataclass(frozen=True)
class CandidateRules:
    allow_real: bool = False
    allow_int: bool = True
    allow_nat: bool = True
    allow_finset: bool = False
    allow_set: bool = False
    max_statement_len: int = 1200
    max_line_count: int = 80


def main() -> None:
    args = parse_args()
    rules = CandidateRules(
        allow_real=args.allow_real,
        allow_int=args.allow_int,
        allow_nat=args.allow_nat,
        allow_finset=args.allow_finset,
        allow_set=args.allow_set,
    )
    problems = load_all_problems(Path(args.putnam_root))
    selected, rejected = select_candidates(problems, rules=rules, max_count=args.max_count)

    write_ids(Path(args.output_ids), selected)
    write_report(
        path=Path(args.output_report),
        putnam_root=args.putnam_root,
        total_problems=len(problems),
        selected=selected,
        rejected=rejected if args.include_rejected else rejected[:20],
        rejected_count=len(rejected),
        rules=rules,
    )
    print_summary(selected, len(problems), len(rejected), args.output_ids, args.output_report)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select static PutnamBench candidates.")
    parser.add_argument("--putnam-root", default="data/PutnamBench")
    parser.add_argument("--max-count", type=int, default=20)
    parser.add_argument("--output-ids", default="outputs/putnam_candidate_ids.txt")
    parser.add_argument("--output-report", default="outputs/putnam_candidate_report.json")
    parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument("--allow-real", action="store_true")
    parser.add_argument("--allow-int", type=_str_to_bool, default=True)
    parser.add_argument("--allow-nat", type=_str_to_bool, default=True)
    parser.add_argument("--allow-finset", action="store_true")
    parser.add_argument("--allow-set", action="store_true")
    return parser.parse_args()


def analyze_problem_features(problem: PutnamLeanProblem) -> dict[str, Any]:
    statement = extract_theorem_statement(problem.source)
    imports = extract_imports(problem.source)
    text = f"{statement}\n{problem.source}"
    features: dict[str, Any] = {
        "problem_id": problem.problem_id,
        "relative_path": problem.relative_path,
        "line_count": problem.line_count,
        "docstring_len": len(problem.docstring or ""),
        "theorem_statement_len": len(statement),
        "sorry_count": _count_token(problem.source, "sorry"),
        "has_solution_abbrev": problem.has_solution_abbrev,
        "imports": imports,
        "contains_integral": any(token in text for token in ["∫", "intervalIntegral", "MeasureTheory"]),
        "contains_real_pi": "Real.pi" in text,
        "contains_measure": "MeasureTheory" in text or "Measure" in text,
        "contains_topology": any(token in text for token in ["Topology", "Metric", "Continuous"]),
        "contains_filter": "Filter" in text,
        "contains_complex": "Complex" in text or "ℂ" in text,
        "contains_matrix": "Matrix" in text,
        "contains_polynomial": "Polynomial" in text or "[X]" in text,
        "contains_finset": "Finset" in text or "∑" in text,
        "contains_set": any(token in text for token in ["Set", "⊆", "∈", "∉"]),
        "contains_real": "ℝ" in text or "Real" in text,
        "contains_nat": "ℕ" in text or "Nat" in text,
        "contains_int": "ℤ" in text or "Int" in text,
        "contains_exists": "∃" in text or "Exists" in text,
        "contains_forall": "∀" in text or "forall" in text,
        "contains_sum": "∑" in text or "sum" in text or "Finset.sum" in text,
        "contains_prod": "∏" in text or "prod" in text or "Finset.prod" in text,
    }
    return features


def score_problem(features: dict[str, Any]) -> int:
    score = 0
    score += features["line_count"] // 10
    score += features["theorem_statement_len"] // 200
    score += features["docstring_len"] // 200
    score += 2 if features["contains_exists"] else 0
    score += 2 if features["contains_forall"] else 0
    score += 3 if features["contains_sum"] else 0
    score += 3 if features["contains_prod"] else 0
    score += 4 if features["contains_finset"] else 0
    score += 4 if features["contains_set"] else 0
    score += 4 if features["contains_real"] else 0
    score += 1 if features["contains_int"] else 0
    score += 0 if features["contains_nat"] else 0
    score += 20 if features["contains_integral"] else 0
    score += 20 if features["contains_real_pi"] else 0
    score += 20 if features["contains_measure"] else 0
    score += 15 if features["contains_topology"] else 0
    score += 15 if features["contains_filter"] else 0
    score += 15 if features["contains_complex"] else 0
    score += 15 if features["contains_matrix"] else 0
    score += 12 if features["contains_polynomial"] else 0
    return score


def reject_reasons(features: dict[str, Any], rules: CandidateRules) -> list[str]:
    reasons: list[str] = []
    for key in [
        "contains_integral",
        "contains_real_pi",
        "contains_measure",
        "contains_topology",
        "contains_filter",
        "contains_complex",
        "contains_matrix",
        "contains_polynomial",
    ]:
        if features[key]:
            reasons.append(key)
    if features["theorem_statement_len"] > rules.max_statement_len:
        reasons.append("theorem_statement_too_long")
    if features["line_count"] > rules.max_line_count:
        reasons.append("file_too_long")
    if features["contains_finset"] and not rules.allow_finset:
        reasons.append("contains_finset")
    if features["contains_set"] and not rules.allow_set:
        reasons.append("contains_set")
    if features["contains_real"] and not rules.allow_real:
        reasons.append("contains_real")
    if features["contains_int"] and not rules.allow_int:
        reasons.append("contains_int")
    if features["contains_nat"] and not rules.allow_nat:
        reasons.append("contains_nat")
    return sorted(set(reasons))


def select_candidates(
    problems: list[PutnamLeanProblem], *, rules: CandidateRules, max_count: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    analyzed = []
    for problem in problems:
        features = analyze_problem_features(problem)
        features["score"] = score_problem(features)
        features["reject_reasons"] = reject_reasons(features, rules)
        analyzed.append(features)

    analyzed.sort(
        key=lambda item: (
            item["score"],
            item["line_count"],
            item["theorem_statement_len"],
            item["problem_id"],
        )
    )
    selected = [item for item in analyzed if not item["reject_reasons"]][:max_count]
    rejected = [item for item in analyzed if item["reject_reasons"]]
    return selected, rejected


def extract_theorem_statement(source: str) -> str:
    theorem_index = source.find("theorem ")
    if theorem_index < 0:
        return source
    sorry_index = source.find("sorry", theorem_index)
    if sorry_index < 0:
        return source[theorem_index:]
    return source[theorem_index:sorry_index]


def extract_imports(source: str) -> list[str]:
    return [
        line.removeprefix("import").strip()
        for line in source.splitlines()
        if line.strip().startswith("import ")
    ]


def write_ids(path: Path, selected: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(item["problem_id"] for item in selected) + ("\n" if selected else ""),
        encoding="utf-8",
    )


def write_report(
    *,
    path: Path,
    putnam_root: str,
    total_problems: int,
    selected: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    rejected_count: int,
    rules: CandidateRules,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "putnam_root": putnam_root,
        "total_problems": total_problems,
        "selected_count": len(selected),
        "rejected_count": rejected_count,
        "rules": {
            "description": "Static heuristic filter only; not a guarantee of solvability.",
            **asdict(rules),
        },
        "selected": selected,
        "rejected": rejected,
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def print_summary(
    selected: list[dict[str, Any]],
    total: int,
    rejected_count: int,
    output_ids: str,
    output_report: str,
) -> None:
    print("Putnam candidate selection")
    print(f"total problems: {total}")
    print(f"selected: {len(selected)}")
    print(f"rejected: {rejected_count}")
    print(f"wrote ids: {output_ids}")
    print(f"wrote report: {output_report}")
    print()
    print("Top candidates:")
    for item in selected[:10]:
        print(
            f"- {item['problem_id']} score={item['score']} "
            f"lines={item['line_count']} statement_len={item['theorem_statement_len']}"
        )


def _count_token(text: str, token: str) -> int:
    return text.count(token)


def _str_to_bool(value: str) -> bool:
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected boolean value, got: {value}")


if __name__ == "__main__":
    main()
