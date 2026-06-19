from src.codex.parser import parse_interactive_worker_action


def test_parse_request_context_action() -> None:
    result = parse_interactive_worker_action(
        "WORKER_ACTION\naction: REQUEST_CONTEXT\nsummary: need updates\nEND_WORKER_ACTION"
    )

    assert result.action == "REQUEST_CONTEXT"
    assert result.summary == "need updates"
    assert result.errors == []


def test_parse_submit_patch_action() -> None:
    result = parse_interactive_worker_action(
        """WORKER_ACTION
action: SUBMIT_PATCH
summary: try exact

PROOF_PATCH
```lean
by
  exact True.intro
```
END_PROOF_PATCH
END_WORKER_ACTION
"""
    )

    assert result.action == "SUBMIT_PATCH"
    assert result.proof_patch == "by\n  exact True.intro"
    assert result.errors == []


def test_parse_submit_note_action() -> None:
    result = parse_interactive_worker_action(
        """WORKER_ACTION
action: SUBMIT_NOTE
note_type: CLAIM
summary: useful fact

NOTE_CONTENT
Try decomposing the rational function first.
END_NOTE_CONTENT
END_WORKER_ACTION
"""
    )

    assert result.action == "SUBMIT_NOTE"
    assert result.note_type == "CLAIM"
    assert result.note_content == "Try decomposing the rational function first."
    assert result.errors == []


def test_missing_action_records_error() -> None:
    result = parse_interactive_worker_action("WORKER_ACTION\nsummary: missing")

    assert result.action is None
    assert result.errors
