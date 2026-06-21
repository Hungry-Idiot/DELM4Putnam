# DELM4Putnam

## Project Overview

DELM4Putnam studies multi-agent collaboration for Lean4 formal proof generation. The
project compares several agent architectures on PutnamBench / miniF2F-style Lean
problems:

- Shared Context / decentralized parallel workers
- Event-triggered Shared Context Pull
- Single-main centralized planning and synthesis baseline

The goal is not only to maximize solved counts. The experiments are designed to
compare success rate, wall-clock cost, Codex call cost, Lean feedback use, and
failure modes across different collaboration mechanisms.

## Repository Structure

- `src/codex/`: prompt builders, parser helpers, and Codex runner integration.
- `src/controller/`: patch submission service and Lean feedback plumbing.
- `src/shared_context/`: SQLite-backed Shared Context notes and rendering.
- `src/data/`: PutnamBench / miniF2F Lean problem discovery.
- `scripts/experiments/`: decentralized Shared Context runners and utilities.
- `scripts/baselines/`: centralized single-main baseline runners.
- `data/PutnamBench/lean4/src/`: Lean theorem sources and internal benchmark batches.
- `runs/minif2f_medium/`: medium benchmark problem list.
- `runs/minif2f_hard_small/`: hard-small benchmark problem list.
- `docs/results/`: lightweight copied result summaries used by this README.

## Architectures

### A. Shared Context Legacy

The legacy Shared Context runner uses multiple decentralized workers in parallel.
Each worker can submit a proof patch directly through `patch_service.submit_patch`;
the first worker whose patch passes Lean solves the problem. In legacy mode there
is no active context-pull pressure, so the strong results mostly come from
parallel direct proof attempts rather than explicit memory sharing.

### B. Shared Context `event_pull` / `event_pull_soft`

The event-triggered Shared Context variants make Lean failure information visible
through Shared Context.

In hard `event_pull`, when a worker's patch fails Lean, the controller writes a
compact failure note to Shared Context and requires other active workers to pull
context before continuing. This is kept as an ablation because it can introduce
extra REQUEST_CONTEXT calls and delay useful patch attempts.

In `event_pull_soft`, the controller still writes failure notes, but other
workers receive only a soft notice that new failure information is available.
They may output `REQUEST_CONTEXT` if the failure note is likely to change their
next proof attempt, or directly output `SUBMIT_PATCH` when they already have a
high-confidence patch. This preserves real Shared Context behavior while avoiding
hard forced REQUEST_CONTEXT interference.

### C. Single-main Optimized

The single-main baseline is centralized:

1. Main Agent produces a plan.
2. Assigned workers return reports and candidate proof patches.
3. Main Agent synthesis chooses, edits, or combines worker reports into one final
   proof patch.
4. Only the Main Agent's synthesized patch is submitted to Lean.

Workers never call Lean and never submit patches directly. This preserves the
centralized planning-and-synthesis architecture while allowing workers to provide
complete candidate proof bodies for the Main Agent to inspect.

## Benchmarks

- `miniF2F-medium`: 6 medium-difficulty miniF2F/math competition style problems.
- `miniF2F-hard-small`: 8 harder internal miniF2F/math competition style problems.
- PutnamBench: retained as a harder challenge set and failure-analysis source,
  not the current main experiment line.

`miniF2F-hard-small` is an internal project subset, not an official benchmark
name. It is defined in:

- `data/PutnamBench/lean4/src/minif2f_hard_small_batch.lean`
- `runs/minif2f_hard_small/problems.txt`

The hard-small problem IDs are:

- `minif2f_hard_small_amc12a_2019_p9`
- `minif2f_hard_small_amc12a_2015_p10`
- `minif2f_hard_small_amc12a_2009_p9`
- `minif2f_hard_small_amc12_2001_p9`
- `minif2f_hard_small_mathd_numbertheory_13`
- `minif2f_hard_small_mathd_numbertheory_780`
- `minif2f_hard_small_mathd_algebra_73`
- `minif2f_hard_small_mathd_algebra_140`

## Main Experimental Results

Lightweight result summaries are copied under `docs/results/`.

### miniF2F-medium

| Method | Success | Success Rate | Avg Wall Time | Avg Time to Success | Total Codex Calls | Notes |
|---|---:|---:|---:|---:|---:|---|
| Shared Context legacy | 6/6 | 100.00% | 59.38s | 59.38s | N/A | context requests = 0 |
| Shared Context event_pull | 6/6 | 100.00% | 69.63s | 69.63s | 20 | context requests = 4 |
| Single-main optimized | 5/6 | 83.33% | 214.72s | 201.88s | 32 | failed on `amc12a_2003_p1` |

Sources:

- `docs/results/minif2f_medium_shared_context_legacy_summary.txt`
- `docs/results/minif2f_medium_shared_context_event_pull_summary.txt`
- `docs/results/minif2f_medium_single_main_optimized_summary.txt`

### miniF2F-hard-small

| Method | Success | Success Rate | Avg Wall Time | Avg Time to Success | Total Codex Calls | Notes |
|---|---:|---:|---:|---:|---:|---|
| Shared Context event_pull_soft | 6/8 | 75.00% | 543.93s | 147.28s | 40 | failed on `amc12a_2019_p9` and `mathd_numbertheory_780` |
| Single-main optimized | 5/8 | 62.50% | 448.60s | 192.92s | 44 | failed on `amc12a_2019_p9`, `mathd_numbertheory_13`, `mathd_numbertheory_780` |

Sources:

- `docs/results/minif2f_hard_small_shared_context_event_pull_soft_summary.txt`
- `docs/results/minif2f_hard_small_single_main_optimized_summary.txt`

The hard-small Single-main average wall time is lower on paper because its failed
problems stopped after `max_rounds_reached`, while Shared Context failures ran to
`time_budget_exceeded`. The more meaningful efficiency comparison is solved-time
and success rate:

- Shared Context `event_pull_soft`: 6/8 solved, average solved time 147.28s.
- Single-main optimized: 5/8 solved, average solved time 192.92s.

## Key Findings

- Decentralized Shared Context workers outperform Single-main on both medium and
  hard-small in these runs.
- The legacy Shared Context result is driven mainly by parallel workers directly
  submitting patches, not by active context sharing.
- `event_pull_soft` introduces observable Shared Context behavior without forcing
  every worker to spend a step on REQUEST_CONTEXT, but it has not yet shown a
  clear speed advantage on these short proof tasks.
- Single-main planning and synthesis adds Codex-call overhead compared with
  decentralized first-success-wins proof attempts.
- Single-main failures often come from Main synthesis producing Lean-rejected
  patches, including type mismatch, `simp` issues, and parse issues.
- The hard-small benchmark raises difficulty and exposes two particularly hard
  problems: `amc12a_2019_p9` and `mathd_numbertheory_780`.
- Shared Context still does not solve every hard-small problem, so the method has
  clear boundaries under the current prompts and budgets.

## Reproduction Commands

Do not commit API keys, `.env`, SQLite databases, raw run logs, or attempt files.
The commands below are examples; set API configuration through the expected local
environment variables.

### Build the hard-small benchmark

```bash
python scripts/experiments/build_minif2f_hard_small.py

cd data/PutnamBench/lean4
timeout 300s lake env lean src/minif2f_hard_small_batch.lean
```

The problem list is:

```text
runs/minif2f_hard_small/problems.txt
```

### Shared Context hard-small batch

Use `scripts/experiments/run_interactive_shared_context_agents.py` with the
problem IDs in `runs/minif2f_hard_small/problems.txt`. Key parameters:

```bash
python scripts/experiments/run_interactive_shared_context_agents.py \
  --problem-id <problem_id> \
  --num-workers 2 \
  --max-concurrency 2 \
  --max-steps 8 \
  --time-budget-sec 1800 \
  --codex-timeout-sec 300 \
  --lean-timeout-sec 120 \
  --shared-context-policy event_pull_soft \
  --progress
```

### Single-main hard-small batch

Use `scripts/baselines/run_single_main_agent.py` with the same problem list. Key
parameters:

```bash
python scripts/baselines/run_single_main_agent.py \
  --problem-id <problem_id> \
  --num-workers 2 \
  --max-concurrency 2 \
  --max-rounds 2 \
  --time-budget-sec 1800 \
  --codex-timeout-sec 300 \
  --lean-timeout-sec 120
```

## Current Limitations

- `miniF2F-hard-small` is small: only 8 problems.
- Results are stochastic because proof generation depends on model behavior.
- `event_pull_soft` has limited context reads and has not yet demonstrated a
  statistically robust hard-problem success improvement.
- PutnamBench remains substantially harder and is best treated as failure
  analysis for the current project stage.
- The project does not train a theorem prover model; it studies agent
  architecture, orchestration, and proof-feedback loops around Lean checking.
