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
Workers cannot run commands, cannot modify files, cannot read Shared Context, cannot communicate with each other, and cannot submit patches to Lean.
Each Worker should receive one concrete, small, executable task. Workers return analysis, candidate lemmas, proof ideas, local Lean snippets, or a complete candidate proof patch for the Main Agent to consider.
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
You cannot submit a patch or run Lean. The Main Agent will choose, modify, integrate, and submit the final proof patch.
You may provide analysis, candidate lemmas, a local proof idea, failure risks, useful tactics, or a complete candidate proof patch.
If you have a plausible complete proof body, include it as CANDIDATE_PROOF_PATCH. It should be Lean code that can replace the theorem body's `sorry`, and it should usually start with `by`.
If you do not have a complete patch, leave CANDIDATE_PROOF_PATCH empty and provide the most useful partial proof idea instead.
Do not use `sorry`, `admit`, or `axiom` in any Lean code.

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
status: solved | partial | failed
summary: <short summary>

DETAILS
<analysis, proof idea, risks, useful lemmas, or partial tactics>

CANDIDATE_PROOF_PATCH
```lean
by
  ...
```
END_CANDIDATE_PROOF_PATCH
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
Workers may provide complete CANDIDATE_PROOF_PATCH blocks, partial Lean snippets, lemmas, or risk notes.
The final submission is your responsibility as Main Agent: output exactly one final proof patch.

Conservative synthesis policy:
- Read all Worker reports before writing the final patch.
- If any Worker provides a complete CANDIDATE_PROOF_PATCH, first check whether it can be used directly.
- Prefer the shortest, most direct, most Lean-plausible complete candidate patch.
- If a Worker patch looks complete, make only the minimum necessary edits.
- Do not mechanically concatenate multiple Worker patches.
- Do not make a simple proof more complex merely to "synthesize" multiple reports.
- Only generate a new proof from multiple partial ideas when no complete candidate patch looks usable.
- You may combine local lemmas or insights, but the final output must be one coherent proof body.

Previous-round failure policy:
- If previous_round_text contains a failed proof patch or Lean errors, do not repeat that failed proof body.
- Avoid the tactic sequence, rewrite path, or automation pattern that caused the previous errors.
- If the previous error was rewrite failed, do not blindly continue the same rewrite route.
- If the previous error was simp made no progress, do not repeat unsupported `simp`.
- If omega, linarith, norm_num, or similar automation failed to close the goal, do not just restyle the same automation attempt.
- If the previous check timed out, produce a shorter, more direct proof with less search, normalization, and broad simplification.
- Do not stack heavy tactics just to gamble on success.

Lean proof style preferences:
- Prefer short proofs with explicit intermediate `have` statements.
- Use readable local facts instead of large tactic searches.
- Use targeted `simp`, `norm_num`, `omega`, or arithmetic tactics only when the goal structure supports them.
- Avoid unsupported `rw` / `rewrite` steps.
- Prefer robust transformations that are visibly justified by the theorem context.

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
    force_context_request: bool = False,
    pending_context_note_seq: int | None = None,
    soft_context_available: bool = False,
    pending_soft_context_note_seq: int | None = None,
) -> str:
    forced_context_section = _event_pull_forced_context_section(
        shared_context_policy=shared_context_policy,
        force_context_request=force_context_request,
        pending_context_note_seq=pending_context_note_seq,
    )
    soft_context_section = _event_pull_soft_context_section(
        shared_context_policy=shared_context_policy,
        soft_context_available=soft_context_available,
        pending_soft_context_note_seq=pending_soft_context_note_seq,
    )
    context_policy = _interactive_context_policy(
        max_context_requests_per_worker=max_context_requests_per_worker,
        context_request_count=context_request_count,
        patch_attempt_count=patch_attempt_count,
        previous_actions=previous_actions or [],
        last_check_status=last_check_status,
        last_error=last_error,
        shared_context_policy=shared_context_policy,
        force_context_request=force_context_request,
    )
    context_policy_section = ""
    if context_policy:
        context_policy_section = f"\nShared-context policy:\n{context_policy}\n"
    request_context_format = ""
    context_requests_available = (
        max_context_requests_per_worker < 0
        or context_request_count < max_context_requests_per_worker
    )
    if (
        shared_context_policy != "legacy"
        and max_context_requests_per_worker != 0
        and context_requests_available
    ):
        request_context_format = """WORKER_ACTION
action: REQUEST_CONTEXT
summary: <why you need latest context>
END_WORKER_ACTION

or

"""
    if shared_context_policy in {"event_pull", "event_pull_soft"}:
        context_source_text = (
            "You learn about other Agents only when you explicitly request Shared Context "
            "and the controller writes the returned context into your local history."
        )
        context_intro = (
            "Shared Context is not automatically shown in event_pull modes. "
            "When useful, use REQUEST_CONTEXT; "
            "the returned context will appear in your local history on a later step."
        )
    else:
        context_source_text = (
            "You only learn about other Agents indirectly through compact Shared Context shown in this prompt."
        )
        context_intro = (
            "The context below is the controller's most recent context snapshot for you."
        )
    return f"""You are decentralized peer worker {worker_id} in a pull-based Shared Context multi-agent experiment.

You are not a Main Agent. There is no Main Agent in this experiment.
{context_source_text}
{context_intro}
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
{forced_context_section}
{soft_context_section}
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
    force_context_request: bool = False,
) -> str:
    if shared_context_policy == "legacy":
        return ""
    if shared_context_policy == "event_pull":
        if force_context_request:
            return "\n".join(
                [
                    "- Event-triggered Shared Context pull is required in this step.",
                    "- Output REQUEST_CONTEXT exactly as instructed above.",
                    "- Do not submit a proof patch in this step.",
                ]
            )
        return "\n".join(
            [
                "- Event-pull policy: Shared Context is requested only when a new failure event requires it.",
                "- Do not request context just because it is available.",
                "- If no event-triggered pull is required, choose SUBMIT_PATCH when you have a concrete proof idea, otherwise SUBMIT_NOTE or STOP.",
            ]
        )
    if shared_context_policy == "event_pull_soft":
        return "\n".join(
            [
                "- Soft event-pull policy: Shared Context is not automatically shown.",
                "- If no soft notice is shown, do not request context just because it is available.",
                "- If a soft notice is shown, REQUEST_CONTEXT is optional; SUBMIT_PATCH is still allowed when you have a high-confidence proof.",
            ]
        )

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


def _event_pull_forced_context_section(
    *,
    shared_context_policy: str,
    force_context_request: bool,
    pending_context_note_seq: int | None,
) -> str:
    if shared_context_policy != "event_pull" or not force_context_request:
        return ""
    note_text = (
        f" Pending failure note seq: {pending_context_note_seq}."
        if pending_context_note_seq is not None
        else ""
    )
    return f"""
Event-triggered Shared Context pull is required.

Another worker submitted a proof patch that failed Lean verification.
A compact failure note has been written to Shared Context.{note_text}
Before attempting another patch, you must request the latest Shared Context.

Output exactly:

WORKER_ACTION
action: REQUEST_CONTEXT
summary: Need latest failure note before trying another proof.
END_WORKER_ACTION

Do not submit a proof patch in this step.
"""


def _event_pull_soft_context_section(
    *,
    shared_context_policy: str,
    soft_context_available: bool,
    pending_soft_context_note_seq: int | None,
) -> str:
    if shared_context_policy != "event_pull_soft" or not soft_context_available:
        return ""
    note_text = (
        f" Pending failure note seq: {pending_soft_context_note_seq}."
        if pending_soft_context_note_seq is not None
        else ""
    )
    return f"""
New Shared Context failure information is available from another worker.{note_text}

You have two options:
- Option A: If you are uncertain, or your next proof may repeat the same failure, output REQUEST_CONTEXT to inspect the latest failure note.
- Option B: If you already have a high-confidence proof patch, you may directly SUBMIT_PATCH.

Do not request context just because it is available.
Request context only if it is likely to change your next proof attempt.
"""
