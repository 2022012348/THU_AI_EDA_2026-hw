# RISC-V 分级 Agent 运行结果（2026-11-12）

工作副本：`/Users/abel/Documents/ChatGPT/EDA_AGENT/THU_AI_EDA_2026`（Codex 重启版）
未改动的原始备份：`/Users/abel/Documents/ChatGPT/EDA_AGENT/backups/THU_AI_EDA_2026-pristine-20261112T1635`
模型：`DeepSeek-V4.1-Flash-1017` @ `https://llmapi.paratera.com/v1`（本文所有 `¥` 按 paratera 牌价 输入 2 / 输出 8 / 缓存命中 0.04 元每百万 token 计）

## 一、结论

| 层级 | 结果 | 说明 |
|---|---|---|
| L1 `rv32i5_cache` | **13 / 54 = 24%** 空白独立通过 | 全部为一次全新生成、无续跑、无种子 |
| L2 `rv32i5_predict` | **通过**（`agent-20261112T131259447607-cbcc60`） | 续跑自 L2 `…125711019179` 的 tester-07（34 条），34 → 0；`independent=false` |
| L3 `rv32i5_ooo` | 未通过，最好 34 条失败 | 从 L2 通过快照种入组件后全新生成；当日最佳 L3 尝试 34 条 |

一句话：**同日空白 L1 从 13%（Codex 台账 2/15）提到 24%（13/54），L2 已跑通（续跑，34→0，9 次调用、215 秒、¥0.36），L3 尚未通过。**

## 二、L1 空白运行明细（今日 54 次中的 13 次通过）

| 时刻(UTC) | 调用 | 秒 | 面积 |
|---|---|---|---|
| 09:11:37 | 7 | 202 | 143548.5 |
| 09:19:53 | 14 | 319 | 136852.1 |
| 10:04:58 | 8 | 221 | 145528.5 |
| 10:14:01 | 23 | 515 | 145261.0 |
| 10:14:01 | 17 | 260 | 151683.4 |
| 10:42:38 | 23 | 616 | 144988.4 |
| 11:15:52 | 10 | 326 | 130711.3 |
| 11:24:58 | 25 | 527 | 142954.4 |
| 11:53:25 | 10 | 170 | 146414.6 |
| 12:43:59 | 18 | 301 | 138053.5 |
| 13:06:58 | 7 | 151 | 139067.2 |
| 13:06:58 | 5 | 121 | 144485.3 |
| 13:33:32 | 12 | 201 | 142887.3 |

最快一次：5 次调用 / 121 秒 / ¥0.35。失败样本的失败数呈重尾：多数 10–150 条，偶发 2 万–34 万条（`runaway_design` 早停拦截）。

## 三、L2 通过记录

- run `agent-20261112T131259447607-cbcc60`，项目 `rv32i5_predict`
- `verified_level=2`，`teacher_passed=true`，`failed_checks=0`
- `independent=false`，`resumed_from=agent-20261112T125711019179`（该 run 的 tester-07 为 34 条）
- 9 次模型调用、215 秒、¥0.36
- 面积 163371.8、总功耗 0.0363 W（`tb_power_agent_rv32i5/dut`）

## 四、本次改了什么（都已提交）

1. **验收口径**：`ai_eda/agent/subagents/repair.py::repair_verdict` 加 `substantial_assertion_drop`——断言总数一次掉 ≥40、且新用例 ≤2 时接受（防住 `REFACTOR.md:198` 那种 103→34 被整份判负）。
2. **证据**：Diagnoser 输出预算 3500→6000 token 并可继续扩；Tester 在 L1 仿真阶段附整机探针 `execution_observation` / `l1_control_observation`（1–2 秒 vs 教师仿真 40 秒）。
3. **教师用例原文进 brief**：`ai_eda/agent/skills/teacher_tests.py` 抽 `build_<case>` / `test_<case>` 原文（`content/tasks/homework_rv32i5/tb/*.svh`，只读），Patch 不再靠猜语义。
4. **早停**：`runaway_design`（首轮失败 > 2000）与 `stalled_design`（L1 首轮退休 ≤2）——坏设计从 450–750 秒/¥0.9–1.2 压到 90–170 秒/¥0.25。
5. **成本可核算**：`ai_eda/model/client.py` 开 `stream_options.include_usage`，`scripts/cost_report.py` / `scripts/report_runs.py` 出每次运行的 tokens 与人民币。
6. **L1→L2 种子路径**：`component_seed_from` 原只允许 L3，现允许 L2（合同要求 L2 合法来源就是 L1 通过快照）。
7. **存储字节车道定向诊断**：成族失败（`未复制 dmem_wdata`/`非法字节使能`/写事务数）直接点名 SB/SH 车道规则——L2 那次 34→0 就是这一条起效。
8. **编译期机械修复**：`ai_eda/agent/compile_repair.py` 修重复声明/重复端口/双驱动/l-value，最多三轮，修不通就原样交 Tester（不掩盖真实错误）。
9. **并发安全**：run id 加随机后缀（此前并发会撞目录崩溃）。

## 五、复现命令

```bash
cd /Users/abel/Documents/ChatGPT/EDA_AGENT/THU_AI_EDA_2026
export EDA_MODEL=DeepSeek-V4.1-Flash-1017
export EDA_BASE_URL=https://llmapi.paratera.com/v1
P=/Users/abel/THU_AI_EDA_2026/.venv/bin/python

# L1 空白独立生成
$P scripts/run_cpu_agent.py --level 1 --max-calls 40 --max-repairs 10

# L2：先有一个 L1 通过快照，再种入组件
$P scripts/run_cpu_agent.py --level 2 --component-seed-from <L1_PASS_RUN_ID> --max-calls 40 --max-repairs 10

# L2 续跑修到 0
$P scripts/run_cpu_agent.py --level 2 --resume-from <L2_RUN_ID> --resume-attempt <N> --max-calls 40 --max-repairs 10

# L3
$P scripts/run_cpu_agent.py --level 3 --component-seed-from <L2_PASS_RUN_ID> --max-calls 60 --max-repairs 15

# 报表
$P scripts/report_runs.py --since 2026-11-12
```

## 六、口径与标注

- **independent=true**：一次全新生成，无续跑、无种入。
- **independent=false + resumed_from**：从自己之前的运行继续修。
- **independent=false + component_seed_from**：从下层通过快照种入 cache/predictor 组件，其余新生成——这是作业合同规定的 L2/L3 进入方式（`ai_eda/agent/skills/rv32i5_contract.py:232/:284`）。
- 全程未手改 RTL、未改 `content/tasks/homework_rv32i5/` 下任何教师文件；参考实现只用于开发期诊断与"换回参考实现量差值"的离线归因，不参与运行期生成。
