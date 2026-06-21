from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PutnamLeanProblem:
    problem_id: str
    lean_path: Path
    relative_path: str
    theorem_name: str | None
    docstring: str | None
    source: str
    line_count: int
    has_sorry: bool
    has_solution_abbrev: bool


def find_lean_src_dir(putnam_root: Path) -> Path:
    lean_src_dir = putnam_root / "lean4" / "src"
    if not lean_src_dir.is_dir():
        raise FileNotFoundError(
            f"PutnamBench Lean4 source directory not found: {lean_src_dir}. "
            "Expected '<putnam-root>/lean4/src'."
        )
    return lean_src_dir


def iter_lean_files(putnam_root: Path) -> list[Path]:
    lean_src_dir = find_lean_src_dir(putnam_root)
    return sorted(lean_src_dir.rglob("*.lean"))


def problem_id_from_path(path: Path) -> str:
    return path.stem


def extract_docstring(source: str) -> str | None:
    theorem_match = _THEOREM_RE.search(source)
    if theorem_match is None:
        return None

    candidates = [
        match
        for match in _DOCSTRING_RE.finditer(source)
        if match.end() <= theorem_match.start()
    ]
    if not candidates:
        return None

    return candidates[-1].group("body").strip()


def extract_theorem_name(source: str) -> str | None:
    match = _THEOREM_RE.search(source)
    if match is None:
        return None
    return match.group("name")


def extract_theorem_names(source: str) -> list[str]:
    return [match.group("name") for match in _THEOREM_RE.finditer(source)]


def load_problem(path: Path, putnam_root: Path) -> PutnamLeanProblem:
    source = path.read_text(encoding="utf-8")
    return PutnamLeanProblem(
        problem_id=problem_id_from_path(path),
        lean_path=path,
        relative_path=_relative_path(path, putnam_root),
        theorem_name=extract_theorem_name(source),
        docstring=extract_docstring(source),
        source=source,
        line_count=len(source.splitlines()),
        has_sorry=bool(_SORRY_RE.search(source)),
        has_solution_abbrev=bool(_SOLUTION_ABBREV_RE.search(source)),
    )


def load_problems_from_file(path: Path, putnam_root: Path) -> list[PutnamLeanProblem]:
    source = path.read_text(encoding="utf-8")
    if not _is_minif2f_batch_file(path):
        return [load_problem(path, putnam_root)]

    theorem_matches = list(_THEOREM_RE.finditer(source))
    if not theorem_matches:
        return [load_problem(path, putnam_root)]

    header = source[: theorem_matches[0].start()].rstrip() + "\n\n"
    problems: list[PutnamLeanProblem] = []
    for index, match in enumerate(theorem_matches):
        theorem_name = match.group("name")
        next_start = (
            theorem_matches[index + 1].start()
            if index + 1 < len(theorem_matches)
            else len(source)
        )
        theorem_source = header + source[match.start() : next_start].strip() + "\n"
        problems.append(
            PutnamLeanProblem(
                problem_id=theorem_name,
                lean_path=path,
                relative_path=_relative_path(path, putnam_root),
                theorem_name=theorem_name,
                docstring=_extract_docstring_before(source, match.start()),
                source=theorem_source,
                line_count=len(theorem_source.splitlines()),
                has_sorry=bool(_SORRY_RE.search(theorem_source)),
                has_solution_abbrev=bool(_SOLUTION_ABBREV_RE.search(theorem_source)),
            )
        )
    return problems


def load_all_problems(putnam_root: Path) -> list[PutnamLeanProblem]:
    problems: list[PutnamLeanProblem] = []
    seen_problem_ids: set[str] = set()
    for path in iter_lean_files(putnam_root):
        for problem in load_problems_from_file(path, putnam_root):
            if problem.problem_id in seen_problem_ids:
                continue
            problems.append(problem)
            seen_problem_ids.add(problem.problem_id)
    return problems


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()


def _extract_docstring_before(source: str, theorem_start: int) -> str | None:
    candidates = [
        match for match in _DOCSTRING_RE.finditer(source) if match.end() <= theorem_start
    ]
    if not candidates:
        return None
    return candidates[-1].group("body").strip()


def _is_minif2f_batch_file(path: Path) -> bool:
    return path.name.startswith("minif2f_") and path.name.endswith("_batch.lean")


_DOCSTRING_RE = re.compile(r"/--(?P<body>.*?)-/", re.DOTALL)
_THEOREM_RE = re.compile(
    r"(?m)^\s*(?:private\s+)?theorem\s+(?P<name>[A-Za-z_][\w'.]*)\b"
)
_SORRY_RE = re.compile(r"\bsorry\b")
_SOLUTION_ABBREV_RE = re.compile(
    r"(?m)^\s*(?:noncomputable\s+)?abbrev\s+\S*_solution\b"
)
