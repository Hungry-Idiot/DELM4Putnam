# 实验结果摘要

本目录保存课程项目使用的轻量实验结果摘要。这些结果用于分析本项目中的 Agent 架构差异，不是官方 benchmark 结论，也不代表稳定排行榜分数。

实验结果会受到模型采样、API 状态、timeout 设置和并行调度影响。复现实验时应记录具体命令、模型配置和 run directory。

## Easy Benchmark

12 道题：

| 架构 | 成功数 | 成功率 | 平均耗时 | 成功题平均耗时 | 总 Codex 调用 | 平均 Codex 调用 |
|---|---:|---:|---:|---:|---:|---:|
| Shared Context `event_pull_soft` | 12/12 | 100.00% | 39.32s | 39.32s | 24 | 2.00 |
| Single-main optimized | 12/12 | 100.00% | 96.45s | 96.45s | 56 | 4.67 |

easy 上两种架构都能解，但 Shared Context 的平均耗时和 Codex 调用数更低。

## Medium Benchmark

6 道题：

| 架构 | 成功数 | 成功率 | 平均耗时 | 成功题平均耗时 | 总 Codex 调用 |
|---|---:|---:|---:|---:|---:|
| Shared Context `event_pull` | 6/6 | 100.00% | 69.63s | 69.63s | 20 |
| Single-main optimized | 5/6 | 83.33% | 214.72s | 201.88s | 32 |

medium 上 Shared Context 在成功率和速度上都优于 Single-main。

## Hard-small Benchmark

8 道题：

| 架构 | 成功数 | 成功率 | 平均 wall time | 成功题平均耗时 | 总 Codex 调用 |
|---|---:|---:|---:|---:|---:|
| Shared Context `event_pull_soft` | 6/8 | 75.00% | 543.93s | 147.28s | 40 |
| Single-main optimized | 5/8 | 62.50% | 448.60s | 192.92s | 44 |

hard-small 上 Single-main 的平均 wall time 表面低于 Shared Context，但这是因为 Single-main 的失败题在 `max_rounds_reached` 后提前停止，而 Shared Context 的失败题跑到 `time_budget_exceeded`。更公平的比较应看成功率和成功题平均耗时。

## 现有摘要文件

- `minif2f_medium_shared_context_legacy_summary.txt`
- `minif2f_medium_shared_context_event_pull_summary.txt`
- `minif2f_medium_single_main_optimized_summary.txt`
- `minif2f_hard_small_shared_context_event_pull_soft_summary.txt`
- `minif2f_hard_small_single_main_optimized_summary.txt`
