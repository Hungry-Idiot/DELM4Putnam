# DELM4Putnam

## 1. Project Overview

本项目研究 Lean4 形式化证明任务中的 test-time multi-agent 协作架构。

核心问题是：

> 在 Lean proof generation 中，多个 Agent 应该去中心化并行尝试、共享中间状态，还是应该由一个 Main Agent 统一规划、分配 worker、综合最终证明？

我们把 DELM 中的 decentralized agents + shared context 思想迁移到 Lean4 proof generation，并与 centralized single-main agent 架构进行对比。项目重点不是训练新的深度模型，也不是声称达到 SOTA，而是分析不同 Agent 协作机制在 Lean 证明生成中的成本、成功率和失败模式。

本项目主要比较两类架构：

- **Shared Context / decentralized parallel workers**
  - 运行脚本：`scripts/experiments/run_interactive_shared_context_agents.py`
  - 多个 worker 并行生成 proof patch，并可以直接提交给 Lean 检查。
  - controller 维护 Shared Context 数据库，记录 proof patch、Lean error、verified patch、failure note 等信息。
  - 当前推荐 policy 是 `event_pull_soft`。

- **Single-main centralized agent**
  - 运行脚本：`scripts/baselines/run_single_main_agent.py`
  - Main Agent 负责 planning、分配 worker、读取 worker reports、synthesis 最终 proof patch，并提交给 Lean。
  - worker 不直接提交 patch，也不直接调用 Lean。

## 2. Motivation

传统多 Agent 系统常见的做法是中心化协调：Main Agent 分配任务，worker 完成局部工作，Main Agent 收集并综合最终答案。这种结构清晰，但在 Lean proof generation 中可能形成瓶颈：

- 每轮都需要 `main_plan -> worker reports -> main_synthesis -> Lean check`；
- worker 生成的正确候选 patch 可能在 synthesis 阶段被改坏；
- Main Agent 的 planning 和 synthesis 会带来固定 Codex 调用成本；
- Lean error 反馈链路较长，失败信息需要经过 Main Agent 再进入下一轮。

DELM 的思想是让多个 Agent 并行工作，通过 shared context / shared state 进行协作，减少单一 Main Agent 的协调瓶颈。本项目尝试把这一思想迁移到 Lean4 证明生成任务中：

- worker 并行尝试 proof patch；
- Lean checker 提供真实反馈；
- 失败 patch 和 Lean error 被写入 Shared Context；
- worker 可以在需要时读取共享状态；
- 任意 worker 的 patch 一旦通过 Lean，问题即解决。

因此，本项目研究的是 **test-time agent orchestration**，不是模型训练。

## 3. Method

### 3.1 Shared Context / Parallel Worker

Shared Context 架构中没有 Main Agent。每个 worker 都是平等的 decentralized proof-search agent。

核心流程：

1. controller 启动多个 active workers；
2. 每个 worker 基于当前问题和本地 history 生成 action；
3. action 可以是 `SUBMIT_PATCH`、`SUBMIT_NOTE`、`REQUEST_CONTEXT` 或 `STOP`；
4. `SUBMIT_PATCH` 会通过 `patch_service.submit_patch` 交给 Lean 检查；
5. proof patch、Lean error、verified patch、failure note 等写入 Shared Context；
6. 任意 worker 的 patch 通过 Lean，则整个问题成功。

Shared Context policy：

- `legacy`
  - 基本并行 worker 模式。
  - worker 很少主动 `REQUEST_CONTEXT`。
  - 实验中主要体现 parallel direct attempts 的收益。

- `event_pull`
  - hard forced pull。
  - 一个 worker 的 patch 失败后，controller 写入 failure note，并强制其他 worker 下一步先 `REQUEST_CONTEXT`。
  - 该策略可作为 ablation，但实验中容易增加额外 step 和 Codex 调用，可能拖慢。

- `event_pull_soft`
  - soft optional pull。
  - 一个 worker 的 patch 失败后，controller 写入 failure note，并提示其他 worker 有新 Shared Context 可用。
  - worker 可以选择 `REQUEST_CONTEXT`，也可以在已有高置信 proof patch 时直接 `SUBMIT_PATCH`。
  - 这是当前推荐的 Shared Context policy。

### 3.2 Single-main Centralized Baseline

Single-main 架构保留中心化主 Agent：

```text
Main Agent planning
-> Worker reports / candidate patches
-> Main Agent synthesis
-> Main Agent 提交最终 proof patch
-> Lean verification
```

特点：

- worker 不直接提交 patch；
- worker 只提供分析、报告、候选 lemma 或 candidate proof patch；
- 最终 proof patch 必须由 Main Agent synthesis 后提交；
- Main Agent synthesis prompt 已经被优化为更保守地使用 worker candidate patch，但整体仍存在较高调用成本。

### 3.3 架构对比

| 维度 | Shared Context / Parallel Worker | Single-main Centralized |
|---|---|---|
| 是否有 Main Agent | 否 | 是 |
| worker 是否直接提交 patch | 是 | 否 |
| Lean error 是否进入共享状态 | 是，写入 Shared Context | 是，但主要供下一轮 Main Agent 使用 |
| 是否需要 synthesis | 不需要 | 需要 Main Agent synthesis |
| 成功条件 | 任意 worker patch 通过 Lean | Main Agent 最终 patch 通过 Lean |
| 反馈链路长度 | 短：worker -> Lean -> Shared Context | 长：worker -> Main -> Lean -> Main |
| 典型调用成本 | 较低，常由并行 patch attempt 主导 | 较高，每轮包含 planning 和 synthesis |

## 4. Benchmarks

本项目使用 PutnamBench Lean4 数据目录中的 miniF2F / math competition 风格题目构造课程实验 benchmark。`data/PutnamBench/` 被视为外部数据目录，需要用户在本地准备；本仓库不提交完整 PutnamBench 外部仓库，也不把它作为 submodule 跟踪。

下表中的 `.lean` 文件路径是本地运行时路径，位于用户准备好的 `data/PutnamBench/lean4/` 目录中。本仓库跟踪的是轻量 problem list 和项目代码。

| Benchmark | 本地 Lean batch 文件 | 仓库跟踪的 problem list | 题目数 | 说明 |
|---|---|---|---:|---|
| miniF2F-easy | `data/PutnamBench/lean4/src/minif2f_easy_batch.lean` | `runs/minif2f_easy/problems.txt` | 12 | 较简单的 Lean 证明任务 |
| miniF2F-medium | `data/PutnamBench/lean4/src/minif2f_medium_batch.lean` | `runs/minif2f_medium/problems.txt` | 6 | 中等难度任务 |
| miniF2F-hard-small | `data/PutnamBench/lean4/src/minif2f_hard_small_batch.lean` | `runs/minif2f_hard_small/problems.txt` | 8 | 项目内部构造的小型 hard 子集 |

`miniF2F-hard-small` 不是官方 benchmark 名称，而是本项目为了课程实验构造的小型 hard benchmark。构造脚本：

```text
scripts/experiments/build_minif2f_hard_small.py
```

该脚本会在本地 PutnamBench 目录下生成 `data/PutnamBench/lean4/src/minif2f_hard_small_batch.lean`。生成文件属于外部数据目录内容，默认不由本项目父仓库跟踪。

hard-small 题目包括：

- `minif2f_hard_small_amc12a_2019_p9`
- `minif2f_hard_small_amc12a_2015_p10`
- `minif2f_hard_small_amc12a_2009_p9`
- `minif2f_hard_small_amc12_2001_p9`
- `minif2f_hard_small_mathd_numbertheory_13`
- `minif2f_hard_small_mathd_numbertheory_780`
- `minif2f_hard_small_mathd_algebra_73`
- `minif2f_hard_small_mathd_algebra_140`

PutnamBench 原始题目难度更高，目前主要用于 failure analysis，不作为本阶段主要定量实验主线。

## 5. Experimental Results

实验结果会受到模型采样、API 状态、timeout 设置和并行调度影响。以下结果用于课程项目分析，不代表官方 benchmark 结论。

### 5.1 Easy benchmark

12 道题：

| 架构 | 成功数 | 成功率 | 平均耗时 | 成功题平均耗时 | 总 Codex 调用 | 平均 Codex 调用 |
|---|---:|---:|---:|---:|---:|---:|
| Shared Context `event_pull_soft` | 12/12 | 100.00% | 39.32s | 39.32s | 24 | 2.00 |
| Single-main optimized | 12/12 | 100.00% | 96.45s | 96.45s | 56 | 4.67 |

easy 上两种架构都能解，但 Shared Context 明显更快，Codex 调用更少。Single-main 的平均耗时约为 Shared Context 的 2.45 倍。

### 5.2 Medium benchmark

6 道题：

| 架构 | 成功数 | 成功率 | 平均耗时 | 成功题平均耗时 | 总 Codex 调用 |
|---|---:|---:|---:|---:|---:|
| Shared Context `event_pull` | 6/6 | 100.00% | 69.63s | 69.63s | 20 |
| Single-main optimized | 5/6 | 83.33% | 214.72s | 201.88s | 32 |

medium 上 Shared Context 在成功率和速度上都优于 Single-main。

### 5.3 Hard-small benchmark

8 道题：

| 架构 | 成功数 | 成功率 | 平均 wall time | 成功题平均耗时 | 总 Codex 调用 |
|---|---:|---:|---:|---:|---:|
| Shared Context `event_pull_soft` | 6/8 | 75.00% | 543.93s | 147.28s | 40 |
| Single-main optimized | 5/8 | 62.50% | 448.60s | 192.92s | 44 |

hard-small 上 Single-main 的平均 wall time 表面低于 Shared Context，但这是因为 Single-main 的失败题在 `max_rounds_reached` 后提前停止，而 Shared Context 的失败题跑到 `time_budget_exceeded`。更公平的比较应看成功率和成功题平均耗时：

- Shared Context `event_pull_soft`: 6/8，成功题平均耗时 147.28s；
- Single-main optimized: 5/8，成功题平均耗时 192.92s。

### 5.4 整体汇总

| 难度 | Shared Context | Single-main | 结论 |
|---|---:|---:|---|
| easy | 12/12 | 12/12 | 都能解，但 Shared Context 更快 |
| medium | 6/6 | 5/6 | Shared Context 成功率和效率都更好 |
| hard-small | 6/8 | 5/8 | Shared Context 仍领先，但暴露边界 |

## 6. Analysis

### 6.1 为什么 Shared Context 更强

本项目实验中，Shared Context 架构的优势主要来自以下几点：

- **并行直接尝试 proof patch**：多个 worker 同时探索不同证明路径。
- **worker 可以直接提交 Lean**：不需要等待 Main Agent 综合。
- **反馈链路短**：`SUBMIT_PATCH -> Lean check -> Shared Context`。
- **first-success-wins**：任意 worker 成功即可结束该题。
- **固定开销低**：没有每轮 `main_plan` 和 `main_synthesis` 的固定成本。

需要注意的是，legacy 和 event_pull_soft 的结果表明，目前收益很大一部分来自 parallel direct attempts，而不是 Shared Context 本身已经稳定解决了最难题。

### 6.2 为什么 Single-main 更弱

Single-main 架构的优势是结构清晰、可解释性强，但在当前短 Lean 证明任务中存在额外成本：

- 每轮至少需要 `main_plan + worker1 + worker2 + main_synthesis`；
- 即使 worker 已经给出较好的 candidate patch，Main Agent synthesis 仍可能改坏；
- synthesis 生成的最终 proof patch 可能出现 Lean 不接受的问题；
- hard-small 上常见错误包括 parse issue、type mismatch、simp issue、no goals 等；
- 失败后需要进入下一轮，进一步增加调用成本。

因此，在当前实验设置下，Single-main 更像是一个可解释的 centralized baseline，而不是效率最优架构。

### 6.3 Shared Context 的边界

Shared Context 并没有解决所有问题：

- `event_pull_soft` 确实产生了共享上下文行为；
- 但 shared context 本身尚未证明能显著提高最难题成功率；
- hard forced `event_pull` 会带来额外 REQUEST_CONTEXT 开销，因此当前推荐 soft optional 策略；
- hard-small 上失败的两题是：
  - `minif2f_hard_small_amc12a_2019_p9`
  - `minif2f_hard_small_mathd_numbertheory_780`

这些失败说明方法仍有边界，尤其是递推有理数、模逆约束等题型仍然困难。

## 7. How to Run

以下命令是单题运行模板。批量运行时可遍历对应 `problems.txt`。不要把 `.env`、API key、SQLite 数据库、run log 或 attempts 文件提交到仓库。

### 7.1 Shared Context easy 示例

```bash
python scripts/experiments/run_interactive_shared_context_agents.py \
  --problem-id "$PID" \
  --num-workers 2 \
  --max-steps 6 \
  --max-concurrency 2 \
  --time-budget-sec 600 \
  --fresh \
  --use-api-key \
  --run-dir "$RUN_DIR" \
  --db "$RUN_DIR/context.sqlite" \
  --codex-timeout-sec 180 \
  --lean-timeout-sec 90 \
  --progress \
  --heartbeat-sec 30 \
  --max-context-requests-per-worker 2 \
  --min-remaining-sec-to-start-call 120 \
  --shared-context-policy event_pull_soft
```

### 7.2 Shared Context hard-small 示例

```bash
python scripts/experiments/run_interactive_shared_context_agents.py \
  --problem-id "$PID" \
  --num-workers 2 \
  --max-steps 8 \
  --max-concurrency 2 \
  --time-budget-sec 1800 \
  --fresh \
  --use-api-key \
  --run-dir "$RUN_DIR" \
  --db "$RUN_DIR/context.sqlite" \
  --codex-timeout-sec 300 \
  --lean-timeout-sec 120 \
  --progress \
  --heartbeat-sec 30 \
  --max-context-requests-per-worker 2 \
  --min-remaining-sec-to-start-call 180 \
  --shared-context-policy event_pull_soft
```

### 7.3 Single-main easy 示例

```bash
python scripts/baselines/run_single_main_agent.py \
  --problem-id "$PID" \
  --num-workers 2 \
  --max-concurrency 2 \
  --max-rounds 2 \
  --time-budget-sec 600 \
  --fresh \
  --use-api-key \
  --run-dir "$RUN_DIR" \
  --db "$RUN_DIR/context.sqlite" \
  --codex-timeout-sec 180 \
  --lean-timeout-sec 90
```

### 7.4 Single-main hard-small 示例

```bash
python scripts/baselines/run_single_main_agent.py \
  --problem-id "$PID" \
  --num-workers 2 \
  --max-concurrency 2 \
  --max-rounds 2 \
  --time-budget-sec 1800 \
  --fresh \
  --use-api-key \
  --run-dir "$RUN_DIR" \
  --db "$RUN_DIR/context.sqlite" \
  --codex-timeout-sec 300 \
  --lean-timeout-sec 120
```

### 7.5 Lean 检查 hard-small batch

```bash
cd data/PutnamBench/lean4
timeout 300s lake env lean src/minif2f_hard_small_batch.lean
```

## 8. Repository Structure

简化目录结构：

```text
DELM4Putnam/
├── scripts/
│   ├── experiments/
│   │   ├── run_interactive_shared_context_agents.py
│   │   └── build_minif2f_hard_small.py
│   └── baselines/
│       └── run_single_main_agent.py
├── src/
│   ├── codex/
│   ├── controller/
│   ├── data/
│   ├── lean/
│   └── shared_context/
├── data/
│   └── PutnamBench/
│       └── lean4/
│           └── src/
├── runs/
│   ├── minif2f_easy/
│   ├── minif2f_medium/
│   └── minif2f_hard_small/
├── docs/
│   └── results/
├── outputs/
└── tests/
```

其中：

- `src/codex/`：prompt、parser、Codex runner；
- `src/controller/`：patch submission 和 Lean feedback 记录；
- `src/shared_context/`：Shared Context SQLite storage 和 note schema；
- `src/data/`：Lean problem loader；
- `scripts/experiments/`：Shared Context runner；
- `scripts/baselines/`：Single-main baseline runner；
- `docs/results/`：轻量实验结果摘要。

注意：`data/PutnamBench/` 是本地外部 Lean4 数据目录。为避免误提交完整外部仓库，本项目父仓库只跟踪 `runs/minif2f_easy/problems.txt`、`runs/minif2f_medium/problems.txt` 和 `runs/minif2f_hard_small/problems.txt` 等轻量 problem list，不跟踪 `data/PutnamBench/` 内部文件。

## 9. Reproducibility Notes

复现实验需要：

- Python / conda 环境，例如 `putnamdelm`；
- Lean4 / Lake / Mathlib 环境；
- Codex 或 OpenAI API key；
- 本地 PutnamBench Lean4 数据目录；
- 合理的 timeout 和 run directory 设置。

clone 本仓库后，如果要复现实验，需要先在 `data/PutnamBench/lean4/` 准备 Lean4 / Mathlib 环境和对应 batch 文件。`miniF2F-hard-small` 可通过 `scripts/experiments/build_minif2f_hard_small.py` 在本地 PutnamBench 目录中生成；easy / medium batch 文件也需要在本地 PutnamBench 目录中存在。

实验结果可能因以下因素变化：

- 模型采样随机性；
- API 延迟或失败；
- Codex timeout 设置；
- Lean timeout 设置；
- 并行 worker 调度；
- 是否使用 `legacy`、`event_pull` 或 `event_pull_soft` policy。

因此，README 中的数值应理解为本项目当前实验记录，而不是稳定的官方评测分数。

## 10. Limitations

- benchmark 规模较小；
- `miniF2F-hard-small` 是项目内部构造，不是官方 benchmark；
- 当前没有训练新的深度模型；
- Shared Context 的收益和 parallel direct attempts 的收益还需要进一步 ablation；
- event_pull_soft 的上下文读取机制尚未证明能显著解决最难题；
- PutnamBench 原始题仍然较难，更适合作为 failure analysis；
- 实验结果主要用于机器学习课程项目分析，而不是大规模正式评测。

## 11. Main Conclusion

我们比较了去中心化 Shared Context 架构与中心化 Single-main Agent 架构在 Lean4 proof generation 任务上的表现。实验覆盖 easy、medium、hard-small 三个难度层级。结果显示，在 easy benchmark 上，两种架构都达到 100% 成功率，但 Shared Context 的平均耗时和 Codex 调用数显著更低；在 medium benchmark 上，Shared Context 达到 100% 成功率，而 Single-main 为 83.33%；在更难的 hard-small benchmark 上，Shared Context 仍达到 75.00% 成功率，高于 Single-main 的 62.50%。

这些结果表明，对于当前短 Lean 证明任务，去中心化并行 worker 直接尝试 proof patch 的方式比中心化 planning/synthesis 更高效。Single-main 架构虽然结构清晰，但 planning 和 synthesis 带来额外调用成本，并且最终 proof patch 容易在综合阶段产生 Lean 形式错误。

同时，实验也说明 Shared Context 机制本身仍有待进一步验证：当前最明显的收益来自并行直接尝试，event_pull_soft 主要提供了更可观察、更温和的共享失败信息机制，但还不能保证解决 hard-small 中最困难的题目。
