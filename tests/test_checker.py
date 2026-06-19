from pathlib import Path

import pytest

from src.lean.checker import check_patch_for_problem
from src.lean.patch import replace_first_sorry


def test_forbidden_patch_is_rejected_before_lean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    problem_path = tmp_path / "putnam_test.lean"
    problem_path.write_text(
        "import Mathlib\n\ntheorem putnam_test : True :=\nsorry\n",
        encoding="utf-8",
    )

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("check_lean_file should not be called")

    monkeypatch.setattr("src.lean.checker.check_lean_file", fail_if_called)

    result = check_patch_for_problem(
        putnam_root=tmp_path / "PutnamBench",
        problem_path=problem_path,
        proof_patch="by\n  sorry",
        run_dir=tmp_path / "runs",
    )

    assert result.success is False
    assert result.returncode == -2
    assert result.has_sorry is True
    assert result.has_admit is False
    assert result.has_axiom is False
    assert result.errors == []
    assert result.stdout == ""
    assert "Rejected before Lean check" in result.stderr
    assert result.attempt_path.read_text(encoding="utf-8").endswith("by\n  sorry\n")


def test_replace_first_sorry_replaces_first_standalone_sorry() -> None:
    source = (
        "def sorry_name := 1\n"
        "theorem first : True :=\n"
        "sorry\n"
        "theorem second : True :=\n"
        "sorry\n"
    )

    patched = replace_first_sorry(source, "by\n  trivial")

    assert "def sorry_name := 1" in patched
    assert patched.count("sorry") == 2
    assert "theorem first : True :=\nby\n  trivial" in patched
    assert "theorem second : True :=\nsorry" in patched
