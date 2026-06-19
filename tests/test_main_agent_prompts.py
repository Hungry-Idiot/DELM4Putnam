from src.codex.prompts import (
    build_main_agent_planning_prompt,
    build_main_agent_synthesis_prompt,
    build_main_assigned_worker_prompt,
)


def test_planning_prompt_mentions_coordinator() -> None:
    prompt = build_main_agent_planning_prompt(
        problem_id="p",
        theorem_name="thm",
        theorem_statement="theorem thm : True := sorry",
        docstring=None,
        full_source=None,
        num_workers=3,
    )

    assert "centralized Main Agent" in prompt
    assert "coordinator" in prompt
    assert "TASKS_JSON" in prompt


def test_worker_prompt_forbids_shared_context_and_peer_communication() -> None:
    prompt = build_main_assigned_worker_prompt(
        problem_id="p",
        theorem_name="thm",
        theorem_statement="theorem thm : True := sorry",
        docstring=None,
        full_source=None,
        worker_id="W1",
        role="algebra",
        task="Find decomposition.",
    )

    assert "not the Main Agent" in prompt
    assert "Do not read or use Shared Context" in prompt
    assert "Do not communicate with other Workers" in prompt


def test_synthesis_prompt_contains_proof_patch_format() -> None:
    prompt = build_main_agent_synthesis_prompt(
        problem_id="p",
        theorem_name="thm",
        theorem_statement="theorem thm : True := sorry",
        docstring=None,
        full_source=None,
        task_plan_text="plan",
        worker_reports_text="reports",
    )

    assert "PROOF_PATCH" in prompt
    assert "END_PROOF_PATCH" in prompt
    assert "Do not use `sorry`, `admit`, or `axiom`" in prompt
