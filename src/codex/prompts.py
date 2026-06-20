from __future__ import annotations


def build_single_worker_prompt(
    *,
    problem_id: str,
    theorem_name: str | None,
    theorem_statement: str | None,
    docstring: str | None,
    shared_context_text: str,
) -> str:
    theorem_name_text = theorem_name or "(unknown)"
    theorem_statement_text = theorem_statement or "(not available)"
    docstring_text = docstring or "(none)"
    context_text = shared_context_text or "(no shared context notes yet)"

    return f"""You are a PutnamBench Lean4 theorem-proving worker.

Your task is to produce exactly one candidate Lean proof patch for the problem below.
The proof patch must be Lean code that can replace the theorem body's `sorry`.

Rules:
- Output only one candidate proof patch.
- Do not use `sorry`, `admit`, or `axiom`.
- Do not introduce new axioms or unsafe placeholder declarations.
- Do not modify files.
- Do not run commands.
- Use Shared Context as compact feedback.
- Do not repeat known failed strategies.
- Prefer concise, checkable patches.
- If you cannot complete the proof, still output a concrete attempt that does not contain `sorry`, `admit`, or `axiom`.
- The patch should usually start with `by`.
- The system will submit your patch to Lean; do not include explanations inside the code block.

Problem:
problem_id: {problem_id}
theorem_name: {theorem_name_text}

Docstring:
{docstring_text}

Lean theorem/source:
```lean
{theorem_statement_text}
```

Shared Context:
{context_text}

Final output format, exactly:

WORKER_RESULT
status: solved | partial | failed
summary: <short summary>

PROOF_PATCH
```lean
by
  ...
```
END_PROOF_PATCH
"""


def build_main_agent_planning_prompt(
    *,
    problem_id: str,
    theorem_name: str | None,
    theorem_statement: str | None,
    docstring: str | None,
    full_source: str | None,
    num_workers: int,
    previous_round_text: str = "",
) -> str:
    return f"""You are the centralized Main Agent theorem-proving coordinator for a PutnamBench Lean4 problem.

Read the theorem and create a short task plan for {num_workers} independent Workers.
Workers cannot run commands, cannot modify files, cannot read Shared Context, and cannot communicate with each other.
Each Worker should receive one concrete, small, executable task. Workers return analysis, candidate lemmas, proof ideas, or local Lean snippets only.
You will later synthesize all Worker reports into one final proof patch.
If previous round information is provided, use it to avoid repeated failed strategies.

Problem:
problem_id: {problem_id}
theorem_name: {theorem_name or "(unknown)"}

Docstring:
{docstring or "(none)"}

Lean theorem/source:
```lean
{full_source or theorem_statement or "(not available)"}
```

Previous round:
{previous_round_text or "(none)"}

Output exactly:

MAIN_PLAN
summary: <short summary>

TASKS_JSON
```json
[
  {{
    "worker_id": "W1",
    "role": "algebra_decomposition",
    "task": "..."
  }},
  {{
    "worker_id": "W2",
    "role": "integration_lemmas",
    "task": "..."
  }}
]
```
END_TASKS_JSON
"""


def build_main_assigned_worker_prompt(
    *,
    problem_id: str,
    theorem_name: str | None,
    theorem_statement: str | None,
    docstring: str | None,
    full_source: str | None,
    worker_id: str,
    role: str,
    task: str,
) -> str:
    return f"""You are Worker {worker_id} in a centralized Main Agent theorem-proving baseline.

You are not the Main Agent. Complete only the local task assigned to you.
Do not try to solve the whole problem unless the task explicitly asks for that.
Do not read or use Shared Context. Do not communicate with other Workers.
Do not run commands. Do not modify files.
You may provide analysis, a candidate lemma, a local proof idea, failure risks, suggestions, or a small Lean snippet.
If you output Lean code, do not use `sorry`, `admit`, or `axiom`.

Problem:
problem_id: {problem_id}
theorem_name: {theorem_name or "(unknown)"}

Docstring:
{docstring or "(none)"}

Lean theorem/source:
```lean
{full_source or theorem_statement or "(not available)"}
```

Assigned role: {role}
Assigned task: {task}

Output exactly:

WORKER_REPORT
worker_id: {worker_id}
status: useful | uncertain | failed
summary: <short summary>

DETAILS
<worker details>

OPTIONAL_LEAN_SNIPPET
```lean
...
```
END_OPTIONAL_LEAN_SNIPPET
END_WORKER_REPORT
"""


def build_main_agent_synthesis_prompt(
    *,
    problem_id: str,
    theorem_name: str | None,
    theorem_statement: str | None,
    docstring: str | None,
    full_source: str | None,
    task_plan_text: str,
    worker_reports_text: str,
    previous_round_text: str = "",
) -> str:
    return f"""You are the centralized Main Agent theorem-proving coordinator.

Read the theorem, your task plan, and all Worker reports. Synthesize the useful parts into one final candidate proof patch.
The proof patch must be Lean code that can replace the theorem body's `sorry`.
Do not use `sorry`, `admit`, or `axiom`.
If you cannot complete the proof, output a concrete attempt without placeholders.
Do not run commands. Do not modify files.

Problem:
problem_id: {problem_id}
theorem_name: {theorem_name or "(unknown)"}

Docstring:
{docstring or "(none)"}

Lean theorem/source:
```lean
{full_source or theorem_statement or "(not available)"}
```

Previous round:
{previous_round_text or "(none)"}

Task plan:
{task_plan_text}

Worker reports:
{worker_reports_text}

Output exactly:

MAIN_SYNTHESIS
status: solved | partial | failed
summary: <short summary>

PROOF_PATCH
```lean
by
  ...
```
END_PROOF_PATCH
"""


def build_interactive_worker_step_prompt(
    *,
    problem_id: str,
    theorem_name: str | None,
    theorem_statement: str | None,
    docstring: str | None,
    full_source: str | None,
    worker_id: str,
    step_index: int,
    current_context_text: str,
    worker_history_text: str = "",
    max_context_requests_per_worker: int = 1,
    context_request_count: int = 0,
    patch_attempt_count: int = 0,
    previous_actions: list[str] | None = None,
    last_check_status: str | None = None,
    last_error: str | None = None,
    shared_context_policy: str = "legacy",
) -> str:
    context_policy = _interactive_context_policy(
        max_context_requests_per_worker=max_context_requests_per_worker,
        context_request_count=context_request_count,
        patch_attempt_count=patch_attempt_count,
        previous_actions=previous_actions or [],
        last_check_status=last_check_status,
        last_error=last_error,
        shared_context_policy=shared_context_policy,
    )
    context_policy_section = ""
    if context_policy:
        context_policy_section = f"\nShared-context policy:\n{context_policy}\n"
    request_context_format = ""
    if shared_context_policy != "legacy" and max_context_requests_per_worker != 0:
        request_context_format = """WORKER_ACTION
action: REQUEST_CONTEXT
summary: <why you need latest context>
END_WORKER_ACTION

or

"""
    return f"""You are decentralized peer worker {worker_id} in a pull-based Shared Context multi-agent experiment.

You are not a Main Agent. There is no Main Agent in this experiment.
You only learn about other Agents indirectly through compact Shared Context shown in this prompt.
The context below is the controller's most recent context snapshot for you.
If you want to submit a candidate proof patch, output SUBMIT_PATCH.
If you want to record a short useful note, output SUBMIT_NOTE.
If you do not need to continue, output STOP.

Rules:
- Your primary goal is to submit a Lean proof patch that can be checked.
- Prefer SUBMIT_PATCH when you have a concrete proof idea.
- SUBMIT_PATCH content must be only the replacement proof body for the target theorem's `sorry`, usually starting with `by`.
- Use SUBMIT_NOTE only for concise, non-empty, actionable information.
- Do not run commands.
- Do not modify files.
- Do not communicate directly with other Workers.
- Do not use full audit log content.
- Proof patches must not contain `sorry`, `admit`, or `axiom`.
{context_policy_section}

Problem:
problem_id: {problem_id}
theorem_name: {theorem_name or "(unknown)"}
step_index: {step_index}

Docstring:
{docstring or "(none)"}

Lean theorem/source:
```lean
{full_source or theorem_statement or "(not available)"}
```

Current compact Shared Context:
{current_context_text or "(empty)"}

Your previous local history:
{worker_history_text or "(none)"}

Choose exactly one output format.

{request_context_format}WORKER_ACTION
action: SUBMIT_PATCH
summary: <short summary>

PROOF_PATCH
```lean
by
  ...
```
END_PROOF_PATCH
END_WORKER_ACTION

or

WORKER_ACTION
action: SUBMIT_NOTE
note_type: CLAIM | TACTIC_TRIED | FAIL | LEMMA_CANDIDATE
summary: <short summary>

NOTE_CONTENT
<short content>
END_NOTE_CONTENT
END_WORKER_ACTION

or

WORKER_ACTION
action: STOP
summary: <why stopping>
END_WORKER_ACTION
"""


def _interactive_context_policy(
    *,
    max_context_requests_per_worker: int,
    context_request_count: int,
    patch_attempt_count: int,
    previous_actions: list[str],
    last_check_status: str | None,
    last_error: str | None,
    shared_context_policy: str = "legacy",
) -> str:
    if shared_context_policy == "legacy":
        return ""

    previous_actions_text = ", ".join(previous_actions[-8:]) or "(none)"
    last_check_text = last_check_status or "(none)"
    last_error_text = last_error or "(none)"
    if max_context_requests_per_worker == 0:
        return "\n".join(
            [
                "- Shared-context requests are disabled for this run; do not choose REQUEST_CONTEXT.",
                "- Work from the context already shown here and choose SUBMIT_PATCH, SUBMIT_NOTE, or STOP.",
                f"- Current worker state: patch_attempt_count={patch_attempt_count}; "
                f"context_request_count={context_request_count}; previous_actions={previous_actions_text}; "
                f"last_check_status={last_check_text}; last_error={last_error_text}.",
            ]
        )

    remaining_context_requests = (
        "unlimited"
        if max_context_requests_per_worker < 0
        else str(max(0, max_context_requests_per_worker - context_request_count))
    )
    max_context_text = (
        "unlimited"
        if max_context_requests_per_worker < 0
        else str(max_context_requests_per_worker)
    )
    state_lines = [
        f"- Context request budget for you: used {context_request_count} of {max_context_text}; remaining {remaining_context_requests}.",
        f"- Current worker state: worker_id is implicit in this prompt; step-local patch_attempt_count={patch_attempt_count}; "
        f"has_requested_context={context_request_count > 0}; previous_actions={previous_actions_text}; "
        f"last_check_status={last_check_text}; last_error={last_error_text}.",
    ]
    if shared_context_policy == "aggressive":
        return "\n".join(
            [
                "- You are part of a multi-worker Lean proof search; do not work as if fully isolated on medium/hard Lean problems.",
                "- Available shared-context action: REQUEST_CONTEXT.",
                "- REQUEST_CONTEXT reads other workers' attempts, failed strategies, Lean errors, candidate proof ideas, and any verified patch information.",
                *state_lines,
                "- If you are not confident the next patch will pass Lean, request shared context before another risky patch attempt.",
                "- If you have already submitted a patch and it failed, and you have not requested context yet, your next action should usually be REQUEST_CONTEXT unless you are highly confident you know the exact fix.",
                "- Before a second or later risky patch attempt, prefer REQUEST_CONTEXT to inspect other workers' attempts, Lean errors, candidate lemmas, and partial proof ideas.",
                "- Do not request context repeatedly if the budget is used up or if you have a concrete patch that is likely to pass Lean.",
                "- Submit SUBMIT_PATCH only when you can provide a concrete Lean replacement proof body.",
            ]
        )

    return "\n".join(
        [
            "- Balanced policy: REQUEST_CONTEXT is a repair aid, not a mandatory action.",
            "- Available shared-context action: REQUEST_CONTEXT.",
            "- REQUEST_CONTEXT reads other workers' attempts, failed strategies, Lean errors, candidate proof ideas, and any verified patch information.",
            *state_lines,
            "- Primary objective: submit a concrete Lean replacement proof body; SUBMIT_PATCH is preferred whenever you have a concrete proof idea.",
            "- Good reasons to REQUEST_CONTEXT: a previous patch failed and you do not understand the Lean error; another worker may have useful failed attempts, candidate lemmas, or partial proof ideas; or you are about to make a second or later risky patch attempt and lack a concrete fix.",
            "- Bad reasons to REQUEST_CONTEXT: only because context is available; before any real proof attempt on an easy-looking problem; repeatedly after already receiving context; or when you already have a concrete patch likely to pass Lean.",
            "- After you REQUEST_CONTEXT once, your next action should usually be SUBMIT_PATCH using what you learned.",
            "- Do not alternate REQUEST_CONTEXT and SUBMIT_PATCH mechanically.",
            "- Do not request context twice unless the first context was insufficient and there is genuinely new information to obtain.",
            "- If another worker already requested context recently and you have a concrete patch idea, prefer SUBMIT_PATCH.",
            "- Avoid having all workers request context in the same round unless no worker has a viable patch idea.",
            "- If a patch failed, consider REQUEST_CONTEXT only if it is likely to help; if you already know the concrete fix, submit the revised patch directly.",
            "- Context should improve proof search, not replace proof search.",
        ]
    )
