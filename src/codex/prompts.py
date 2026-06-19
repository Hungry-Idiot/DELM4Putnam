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
) -> str:
    return f"""You are decentralized peer worker {worker_id} in a pull-based Shared Context multi-agent experiment.

You are not a Main Agent. There is no Main Agent in this experiment.
You only learn about other Agents indirectly through compact Shared Context shown in this prompt.
The context below is the controller's most recent context snapshot for you.
If you want the latest committed Shared Context, output REQUEST_CONTEXT.
If you do not request context, the controller will not push newer changes to you.
If you want to submit a candidate proof patch, output SUBMIT_PATCH.
If you want to record a short useful note, output SUBMIT_NOTE.
If you do not need to continue, output STOP.

Rules:
- Do not run commands.
- Do not modify files.
- Do not communicate directly with other Workers.
- Do not use full audit log content.
- Proof patches must not contain `sorry`, `admit`, or `axiom`.

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

WORKER_ACTION
action: REQUEST_CONTEXT
summary: <why you need latest context>
END_WORKER_ACTION

or

WORKER_ACTION
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
