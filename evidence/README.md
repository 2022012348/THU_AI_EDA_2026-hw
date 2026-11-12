# 运行证据（run summary + 教师 TB 报告 + 通过的 RTL）

每个目录是一次真实 Agent 运行的最小可核验子集（完整 `runs/` 共 14 GB，未入库）：

- `run_summary.json`：运行元数据（`run_id`、`independent`、`resumed_from`、`model_calls`、
  `tester_attempts`、`failed_checks`、`tokens`、`metrics` 面积/功耗、`performance` benchmark）。
- `selected_test_report.json` / `final_test_report.json`：教师 TB 的原始报告（含每条 check）。
- `rtl/`：该次运行最终采用的 RTL 快照（按 `// file: rtl/xxx.sv` 分节）。

| 目录 | 等级 | 结论 | 说明 |
|---|---|---|---|
| `agent-20261112T130658939277` | L1 | 通过 | 空白独立生成，5 次调用 / 121 s / ¥0.35 |
| `agent-20261112T124359484807` | L1 | 通过 | 空白独立生成，18 次调用 / 301 s |
| `agent-20261112T131259447607-cbcc60` | L2 | 通过 | `independent=false`，续跑自 `…125711019179`，34→0 |
| `agent-20261112T050852828836` | L3 | 未通过（49 条） | L3 通过链的起点（空白独立生成，24 次调用） |
| `agent-20261112T055150850447` | L3 | 未通过（33→3） | 该链第三跳 |
| `agent-20261112T060026252331` | L3 | 通过 | `independent=false`，续跑自 `…055150850447`，33→0，7 次调用 / 145 s |
