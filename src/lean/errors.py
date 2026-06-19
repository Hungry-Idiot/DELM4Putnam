from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LeanError:
    line: int | None
    column: int | None
    message: str


def parse_lean_errors(output: str) -> list[LeanError]:
    errors: list[LeanError] = []
    for line in output.splitlines():
        match = _LEAN_ERROR_RE.match(line)
        if match is None:
            continue

        errors.append(
            LeanError(
                line=int(match.group("line")),
                column=int(match.group("column")),
                message=match.group("message").strip(),
            )
        )

    if not errors and "error:" in output:
        errors.append(LeanError(line=None, column=None, message=output.strip()))

    return errors


_LEAN_ERROR_RE = re.compile(
    r"^.*?:(?P<line>\d+):(?P<column>\d+):\s+error:\s+(?P<message>.*)$"
)
