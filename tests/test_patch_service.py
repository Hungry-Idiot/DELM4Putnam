from pathlib import Path

import pytest

from src.controller.patch_service import submit_patch
from src.data.putnam_lean import PutnamLeanProblem
from src.lean.checker import LeanCheckResult
from src.shared_context.storage import get_notes


def test_submit_patch_early_reject_writes_error_notes(tmp_path: Path) -> None:
    putnam_root = Path("data/PutnamBench")
    if not (putnam_root / "lean4" / "src").is_dir():
        pytest.skip("local PutnamBench Lean4 data is not available")

    db_path = tmp_path / "context.sqlite"
    result = submit_patch(
        putnam_root=putnam_root,
        db_path=db_path,
        problem_id="putnam_1968_a1",
        worker_id="A_test",
        proof_patch="by\n  sorry",
        run_dir=tmp_path / "runs",
    )

    assert result.success is False
    assert result.returncode == -2
    assert result.proof_patch_seq is not None
    assert result.lean_error_seq is not None
    assert result.verified_patch_seq is None
    assert result.final_proof_seq is None

    notes = get_notes(db_path, problem_id="putnam_1968_a1")
    note_types = [note.type for note in notes]
    assert "PROOF_PATCH" in note_types
    assert "LEAN_ERROR" in note_types
    assert "VERIFIED_PATCH" not in note_types
    assert "FINAL_PROOF" not in note_types


def test_timeout_lean_error_has_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    problem = PutnamLeanProblem(
        problem_id="putnam_test",
        lean_path=tmp_path / "putnam_test.lean",
        relative_path="lean4/src/putnam_test.lean",
        theorem_name="putnam_test",
        docstring="Test theorem.",
        source="theorem putnam_test : True := sorry",
        line_count=1,
        has_sorry=True,
        has_solution_abbrev=False,
    )

    def fake_find_problem_by_id(putnam_root: Path, problem_id: str) -> PutnamLeanProblem:
        return problem

    def fake_check_patch_for_problem(**kwargs: object) -> LeanCheckResult:
        return LeanCheckResult(
            success=False,
            returncode=-1,
            attempt_path=tmp_path / "runs" / "A_test" / "attempts" / "attempt_0002.lean",
            elapsed_sec=180.244,
            has_sorry=False,
            has_admit=False,
            has_axiom=False,
            errors=[],
            stdout="",
            stderr="Timed out after 180 seconds.",
        )

    monkeypatch.setattr(
        "src.controller.patch_service.find_problem_by_id", fake_find_problem_by_id
    )
    monkeypatch.setattr(
        "src.controller.patch_service.check_patch_for_problem",
        fake_check_patch_for_problem,
    )

    db_path = tmp_path / "context.sqlite"
    result = submit_patch(
        putnam_root=tmp_path / "PutnamBench",
        db_path=db_path,
        problem_id="putnam_test",
        worker_id="A_test",
        proof_patch="by\n  trivial",
        run_dir=tmp_path / "runs",
        timeout_sec=180,
    )

    assert result.check_status == "timed out"
    notes = get_notes(db_path, problem_id="putnam_test")
    lean_error = next(note for note in notes if note.type == "LEAN_ERROR")
    assert "Lean checker timed out" in lean_error.content
    assert "Avoid large" in lean_error.content
    assert "norm_num" in lean_error.content or "simp" in lean_error.content
    assert lean_error.metadata["timeout_sec"] == 180
    assert lean_error.metadata["elapsed_sec"] == 180.244
    assert lean_error.metadata["proof_patch_seq"] == result.proof_patch_seq
    assert lean_error.metadata["attempt_path"] == result.attempt_path
    assert lean_error.metadata["check_status"] == "timed out"
    assert lean_error.metadata["returncode"] == -1
