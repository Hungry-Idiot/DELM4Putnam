from src.codex.parser import parse_main_plan_output


def test_parse_main_plan_standard_json() -> None:
    text = """MAIN_PLAN
summary: split the proof

TASKS_JSON
[
  {"worker_id": "W1", "role": "algebra", "task": "Find decomposition."}
]
END_TASKS_JSON
"""

    result = parse_main_plan_output(text)

    assert result.summary == "split the proof"
    assert result.tasks == [
        {"worker_id": "W1", "role": "algebra", "task": "Find decomposition."}
    ]
    assert result.errors == []


def test_parse_main_plan_fenced_json() -> None:
    text = """MAIN_PLAN
summary: split the proof

TASKS_JSON
```json
[
  {"worker_id": "W2", "role": "integration", "task": "List lemmas."}
]
```
END_TASKS_JSON
"""

    result = parse_main_plan_output(text)

    assert result.tasks[0]["worker_id"] == "W2"
    assert result.errors == []


def test_parse_main_plan_bad_json_records_error() -> None:
    result = parse_main_plan_output("TASKS_JSON\n[{bad]\nEND_TASKS_JSON")

    assert result.tasks == []
    assert result.errors


def test_parse_main_plan_missing_fields_records_error() -> None:
    result = parse_main_plan_output(
        "TASKS_JSON\n[{\"worker_id\": \"W1\", \"task\": \"Do it.\"}]\nEND_TASKS_JSON"
    )

    assert result.tasks == []
    assert "missing required fields" in result.errors[0]
