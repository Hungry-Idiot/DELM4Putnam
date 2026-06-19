from src.codex.parser import parse_worker_output
from src.codex.runner import _build_codex_command


def test_parse_standard_worker_output() -> None:
    text = """WORKER_RESULT
status: partial
summary: tried a direct proof

PROOF_PATCH
by
  trivial
END_PROOF_PATCH
"""

    result = parse_worker_output(text)

    assert result.status == "partial"
    assert result.summary == "tried a direct proof"
    assert result.proof_patch == "by\n  trivial"
    assert result.errors == []


def test_parse_fenced_lean_patch() -> None:
    text = """WORKER_RESULT
status: failed
summary: fallback attempt

PROOF_PATCH
```lean
by
  exact True.intro
```
END_PROOF_PATCH
"""

    result = parse_worker_output(text)

    assert result.proof_patch == "by\n  exact True.intro"


def test_parse_fenced_patch_without_end_marker() -> None:
    text = """WORKER_RESULT
status: partial
summary: no end marker

PROOF_PATCH
```lean
by
  exact trivial
```
"""

    result = parse_worker_output(text)

    assert result.proof_patch == "by\n  exact trivial"
    assert result.errors == []


def test_parse_stderr_style_transcript_without_tokens_used() -> None:
    text = """codex
WORKER_RESULT
status: partial
summary: transcript output

PROOF_PATCH
```lean
by
  exact trivial
```

tokens used
123
"""

    result = parse_worker_output(text)

    assert result.status == "partial"
    assert result.summary == "transcript output"
    assert result.proof_patch == "by\n  exact trivial"
    assert "tokens used" not in result.proof_patch


def test_missing_patch_records_error() -> None:
    result = parse_worker_output("WORKER_RESULT\nstatus: failed\nsummary: no patch")

    assert result.proof_patch is None
    assert result.errors


def test_default_codex_command_omits_approval_flag() -> None:
    command = _build_codex_command(
        codex_command="codex",
        model="gpt-test",
        base_url="https://relay.example.com/v1",
        provider="relay",
        approval="never",
        sandbox="read-only",
        skip_git_repo_check=True,
        include_approval_flag=False,
        include_sandbox_flag=True,
        output_last_message_path=None,
    )

    assert "--ask-for-approval" not in command
    assert "--sandbox" in command
    assert "--model" in command
    assert "--skip-git-repo-check" in command
    assert "-c" in command
    assert command[-1] == "-"
