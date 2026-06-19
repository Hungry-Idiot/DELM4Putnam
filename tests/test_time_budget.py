from types import SimpleNamespace

import scripts.baselines.run_single_main_agent as main_agent
import scripts.experiments.run_interactive_shared_context_agents as interactive


def test_time_budget_zero_keeps_old_timeout_behavior() -> None:
    budget = main_agent.TimeBudget(0)

    assert budget.enabled is False
    assert budget.timeout_for(600) == 600
    assert budget.can_start_call() is True
    assert budget.exhausted() is False


def test_initial_summary_contains_budget_fields() -> None:
    args = SimpleNamespace(num_workers=3, max_rounds=1, time_budget_sec=1800)

    summary = main_agent.initial_summary(args, "putnam_test")

    assert summary["time_budget_sec"] == 1800
    assert "wall_elapsed_sec" in summary
    assert summary["stopped_reason"] is None
    assert summary["budget_exhausted"] is False


def test_exhausted_budget_prevents_new_codex_call() -> None:
    budget = main_agent.TimeBudget(0.001)
    budget.start -= 1

    assert budget.can_start_call() is False
    assert budget.timeout_for(600) == 0


def test_interactive_max_steps_stopped_reason() -> None:
    reason = interactive.interactive_stopped_reason(
        success=False,
        budget_exhausted=False,
        steps_completed=3,
        max_steps=3,
        any_active=True,
    )

    assert reason == "max_steps_reached"


def test_main_agent_max_rounds_stopped_reason() -> None:
    reason = main_agent.main_stopped_reason(
        success=False,
        budget_exhausted=False,
        rounds_completed=1,
        max_rounds=1,
    )

    assert reason == "max_rounds_reached"
