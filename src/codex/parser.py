from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


@dataclass
class WorkerParseResult:
    status: str | None
    summary: str | None
    proof_patch: str | None
    raw_text: str
    errors: list[str] = field(default_factory=list)


@dataclass
class MainPlanParseResult:
    tasks: list[dict[str, str]]
    summary: str | None
    raw_text: str
    errors: list[str] = field(default_factory=list)


@dataclass
class InteractiveWorkerAction:
    action: str | None
    summary: str | None
    proof_patch: str | None
    note_type: str | None
    note_content: str | None
    raw_text: str
    errors: list[str] = field(default_factory=list)


def parse_worker_output(text: str) -> WorkerParseResult:
    errors: list[str] = []
    status = _extract_field(text, "status")
    summary = _extract_field(text, "summary")
    proof_patch = _extract_patch(text, errors)

    return WorkerParseResult(
        status=status,
        summary=summary,
        proof_patch=proof_patch,
        raw_text=text,
        errors=errors,
    )


def parse_main_plan_output(text: str) -> MainPlanParseResult:
    errors: list[str] = []
    summary = _extract_field(text, "summary")
    tasks: list[dict[str, str]] = []
    match = re.search(
        r"(?is)\bTASKS_JSON\b(?P<body>.*?)\bEND_TASKS_JSON\b",
        text,
    )
    if match is None:
        errors.append("Could not find TASKS_JSON ... END_TASKS_JSON block.")
        return MainPlanParseResult(tasks=[], summary=summary, raw_text=text, errors=errors)

    body = _strip_fence(match.group("body")).strip()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        errors.append(f"Could not parse TASKS_JSON: {exc}")
        return MainPlanParseResult(tasks=[], summary=summary, raw_text=text, errors=errors)

    if not isinstance(parsed, list):
        errors.append("TASKS_JSON must be a JSON list.")
        return MainPlanParseResult(tasks=[], summary=summary, raw_text=text, errors=errors)

    required = {"worker_id", "role", "task"}
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            errors.append(f"Task {index} is not a JSON object.")
            continue
        missing = sorted(required - set(item))
        if missing:
            errors.append(f"Task {index} missing required fields: {', '.join(missing)}")
            continue
        tasks.append(
            {
                "worker_id": str(item["worker_id"]),
                "role": str(item["role"]),
                "task": str(item["task"]),
            }
        )

    return MainPlanParseResult(tasks=tasks, summary=summary, raw_text=text, errors=errors)


def parse_interactive_worker_action(text: str) -> InteractiveWorkerAction:
    errors: list[str] = []
    action = _extract_field(text, "action")
    summary = _extract_field(text, "summary")
    proof_patch: str | None = None
    note_type: str | None = None
    note_content: str | None = None

    valid_actions = {"REQUEST_CONTEXT", "SUBMIT_PATCH", "SUBMIT_NOTE", "STOP"}
    if action is None:
        errors.append("Could not find action field.")
    else:
        action = action.strip().upper()
        if action not in valid_actions:
            errors.append(f"Invalid worker action: {action}")

    if action == "SUBMIT_PATCH":
        proof_patch = _extract_patch(text, errors)
    elif action == "SUBMIT_NOTE":
        note_type = _extract_field(text, "note_type")
        if note_type is None:
            errors.append("SUBMIT_NOTE missing note_type.")
        else:
            note_type = note_type.strip().upper()
        note_content = _extract_block(text, "NOTE_CONTENT", "END_NOTE_CONTENT")
        if not note_content:
            errors.append("SUBMIT_NOTE missing NOTE_CONTENT block.")

    return InteractiveWorkerAction(
        action=action,
        summary=summary,
        proof_patch=proof_patch,
        note_type=note_type,
        note_content=note_content,
        raw_text=text,
        errors=errors,
    )


def _extract_field(text: str, field_name: str) -> str | None:
    match = re.search(
        rf"(?im)^\s*{re.escape(field_name)}\s*:\s*(?P<value>.+?)\s*$",
        text,
    )
    if match is None:
        return None
    return match.group("value").strip()


def _extract_block(text: str, start_marker: str, end_marker: str) -> str | None:
    match = re.search(
        rf"(?is)\b{re.escape(start_marker)}\b(?P<body>.*?)\b{re.escape(end_marker)}\b",
        text,
    )
    if match is None:
        return None
    body = match.group("body").strip()
    return body or None


def _extract_patch(text: str, errors: list[str]) -> str | None:
    match = re.search(
        r"(?is)\bPROOF_PATCH\b(?P<body>.*?)\bEND_PROOF_PATCH\b",
        text,
    )
    if match is not None:
        return _clean_patch(match.group("body"), errors)

    proof_patch_match = _last_proof_patch_marker(text)
    if proof_patch_match is None:
        errors.append("Could not find PROOF_PATCH marker.")
        return None

    body = text[proof_patch_match.end() :]
    fenced = re.search(r"(?is)```(?:lean)?\s*(?P<body>.*?)\s*```", body)
    if fenced is not None:
        return _clean_patch(fenced.group("body"), errors)

    by_match = re.search(r"(?im)^\s*by\b", body)
    if by_match is not None:
        return _clean_patch(body[by_match.start() :], errors)

    errors.append(
        "Could not find PROOF_PATCH ... END_PROOF_PATCH block, fenced Lean block, "
        "or patch starting with 'by'."
    )
    return None

def _clean_patch(text: str, errors: list[str]) -> str | None:
    patch = _strip_fence(text).strip()
    patch = _remove_trailing_codex_text(patch).strip()
    if not patch:
        errors.append("PROOF_PATCH block is empty.")
        return None
    return patch


def _strip_fence(text: str) -> str:
    match = re.match(r"(?is)^```(?:[A-Za-z0-9_-]+)?\s*(?P<body>.*?)\s*```$", text.strip())
    if match is None:
        return text
    return match.group("body")


def _last_proof_patch_marker(text: str) -> re.Match[str] | None:
    matches = list(re.finditer(r"(?i)\bPROOF_PATCH\b", text))
    if not matches:
        return None
    return matches[-1]


def _remove_trailing_codex_text(text: str) -> str:
    stop_patterns = [
        r"(?im)^\s*tokens used\b.*$",
        r"(?im)^\s*WORKER_RESULT\b.*$",
        r"(?im)^\s*PROOF_PATCH\b.*$",
        r"(?im)^\s*END_PROOF_PATCH\b.*$",
        r"(?im)^\s*status\s*:.*$",
        r"(?im)^\s*summary\s*:.*$",
        r"(?im)^\s*codex\b.*$",
    ]
    stop_positions = [
        match.start()
        for pattern in stop_patterns
        for match in [re.search(pattern, text)]
        if match is not None
    ]
    if not stop_positions:
        return text
    return text[: min(stop_positions)]
