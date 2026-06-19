import json
from pathlib import Path

from scripts.data.select_putnam_candidates import (
    CandidateRules,
    analyze_problem_features,
    score_problem,
    select_candidates,
    write_report,
)
from src.data.putnam_lean import PutnamLeanProblem


def make_problem(problem_id: str, source: str, line_count: int = 5) -> PutnamLeanProblem:
    return PutnamLeanProblem(
        problem_id=problem_id,
        lean_path=Path(f"{problem_id}.lean"),
        relative_path=f"lean4/src/{problem_id}.lean",
        theorem_name=problem_id,
        docstring="short doc",
        source=source,
        line_count=line_count,
        has_sorry="sorry" in source,
        has_solution_abbrev=False,
    )


def test_integral_statement_is_flagged() -> None:
    problem = make_problem("p", "theorem p : (∫ x in (0)..1, x) = 0 := sorry")

    features = analyze_problem_features(problem)

    assert features["contains_integral"] is True


def test_real_pi_statement_is_flagged() -> None:
    problem = make_problem("p", "theorem p : Real.pi > 0 := sorry")

    features = analyze_problem_features(problem)

    assert features["contains_real_pi"] is True


def test_short_nat_int_scores_lower_than_integral_real_pi() -> None:
    easy = analyze_problem_features(
        make_problem("easy", "theorem easy : ∀ n : ℕ, n = n := sorry")
    )
    hard = analyze_problem_features(
        make_problem("hard", "theorem hard : Real.pi = ∫ x in (0)..1, x := sorry")
    )

    assert score_problem(easy) < score_problem(hard)


def test_selection_sort_order() -> None:
    problems = [
        make_problem("p_b", "theorem p_b : ∀ n : ℕ, n = n := sorry", line_count=20),
        make_problem("p_a", "theorem p_a : ∀ n : ℕ, n = n := sorry", line_count=10),
        make_problem("p_c", "theorem p_c : ∀ n : ℕ, n + 0 = n := sorry", line_count=10),
    ]

    selected, _ = select_candidates(problems, rules=CandidateRules(), max_count=3)

    assert [item["problem_id"] for item in selected] == ["p_a", "p_c", "p_b"]


def test_report_json_structure(tmp_path: Path) -> None:
    selected = [
        {
            "problem_id": "p",
            "score": 1,
            "line_count": 5,
            "theorem_statement_len": 20,
            "reject_reasons": [],
        }
    ]
    rejected = [
        {
            "problem_id": "q",
            "score": 30,
            "reject_reasons": ["contains_integral"],
        }
    ]
    path = tmp_path / "report.json"

    write_report(
        path=path,
        putnam_root="data/PutnamBench",
        total_problems=2,
        selected=selected,
        rejected=rejected,
        rejected_count=1,
        rules=CandidateRules(),
    )

    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["putnam_root"] == "data/PutnamBench"
    assert report["total_problems"] == 2
    assert report["selected_count"] == 1
    assert report["rejected_count"] == 1
    assert report["selected"][0]["problem_id"] == "p"
    assert report["rejected"][0]["reject_reasons"] == ["contains_integral"]
