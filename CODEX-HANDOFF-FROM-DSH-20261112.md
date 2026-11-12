# Codex 接手说明（DSH 交接，2026-11-12）

作者：DSH（另一个 agent）。目标：把 Codex 重启版的 L1 独立通过率从 2/15 往上拉，同时不破坏你的架构。

---

## 1. 三个工作副本，别搞混

| 位置 | 是什么 | 能否改 |
|---|---|---|
| `/Users/abel/Documents/ChatGPT/EDA_AGENT/backups/THU_AI_EDA_2026-pristine-20261112T1635` | 你重启版在 16:32 的原样备份（31,443 文件 / 14 GB，APFS clonefile，**初始不占额外空间**） | **不要动** |
| `/Users/abel/Documents/ChatGPT/EDA_AGENT/THU_AI_EDA_2026` | 你重启版的**工作副本**（我在这里改，已 `git init`） | 可以改 |
| `/Users/abel/THU_AI_EDA_2026` | 旧仓库（我上一阶段的主战场，`main` 在 `5b555e8`） | 已停用，仅作参考实现/脚本来源 |

工作副本的 git 基线：`c582d7c baseline: Codex restarted project`，当前 HEAD `8460302`，工作区干净。
`runs/`、`.api_key`、`build/`、`*.vcd` 按其 `.gitignore` 排除，所以 14 GB 的 runs 不进 git。

恢复整棵工作副本：`git reset --hard c582d7c`（回到你交付时的状态）。
恢复某个 run 的 RTL：`runs/agent/<run_id>/artifacts/rtl_manifest_attemptN.json` + `snapshots/`。

环境：本目录无 `.venv`，用 `/Users/abel/THU_AI_EDA_2026/.venv/bin/python`；`.api_key` 已从此前目录复制（600）。
模型默认 `deepseek-flash / https://api.deepseek.com`，运行时用环境变量覆盖成 paratera。

---

## 2. 怎么跑、怎么验收、怎么算钱

```bash
cd /Users/abel/Documents/ChatGPT/EDA_AGENT/THU_AI_EDA_2026
EDA_MODEL=DeepSeek-V4.1-Flash-1017 \
EDA_BASE_URL=https://llmapi.paratera.com/v1 \
/Users/abel/THU_AI_EDA_2026/.venv/bin/python scripts/run_cpu_agent.py \
    --level 1 --max-calls 40 --max-repairs 10
```

```bash
# 最近 5 次独立运行的表格（结果 / 失败数 / 调用 / tokens / 花费）
.venv/bin/python scripts/report_runs.py --last 5
.venv/bin/python scripts/report_runs.py --all          # 全部
.venv/bin/python scripts/cost_report.py "runs/agent/agent-20261112T10*"
```

价格默认按 paratera 牌价：输入 ¥2/百万、输出 ¥8/百万、缓存命中 ¥0.04/百万（`EDA_PRICE_IN/OUT/CACHE` 可覆盖）。
用量取自 `run_summary.tokens`（真实）；11-12 10:14 之前的运行没有记录 usage，脚本会按字符估算并标 `*`。

测试：`.venv/bin/python -m unittest discover -s checks` → **49 项全过**（我加了 7 项）。

---

## 3. 我改了什么（11 个提交，581 行）

按主题分组，每条都注明“为什么”和“怎么关掉”。

### A. 验收口径（`ai_eda/agent/subagents/repair.py`）
- `1e64f74` `substantial_assertion_drop`：断言数下降 ≥40（`EDA_BULK_DROP`）且新增用例 ≤2 时接受，即使新增了超时。
  原因：`REFACTOR.md:198` 记着 103→34 被 `new_failing_cases` 整份否决、保留 102——和我在这套代码里复现的现象一致。
  回退：`EDA_VERDICT_STRICT=1`。

### B. 诊断（`ai_eda/agent/subagents/diagnoser.py`）
- `1e64f74` 输出预算 3500→**6000**（`EDA_DIAGNOSE_TOKENS`），截断重试时追加 `format_hint`。原因：`REFACTOR.md:218`“八次截断，未进入补丁”。
- `91b4a32` 新增 `ai_eda/agent/skills/teacher_tests.py::test_source()`：从 `content/tasks/homework_rv32i5/tb/*.svh` 抽 `build_<case>`/`test_<case>` 原文，写进 `brief.teacher_test_source`（≤6000 字符）。
  原因：`agent-20261112T094401613175` 只剩 1 条 `t11_halt 等待 halted 超时 imem_addr=0x0c`，而该用例是 `addi x1,0,384 → lw x10,0(x1) → ecall`（ECALL 必须等老 load 提交），没有原文 Patch 只能猜。
- `63dc067` 连续两轮 `no_verified_progress` 且失败数相同 → prompt/brief 注入 `patch_inert_warning`（要求换机制/换模块、允许整份重写）。
- **`94975fe`（最重要）** 机械诊断被证伪后强制改用模型诊断。
  证据：`agent-20261112T102447841499` 的 10 次 Diagnoser 全部 `diagnosis_source: "measured"`，`hypothesis`/`constraints` **逐字相同**、都指向 `hazard_unit` 的 JAL 冲刷，而实际失败是 26 条 `t06b_neg_imm` 负立即数访存——10 轮补丁 26→26 不变。现在上一轮被判 `no_verified_progress` 时，把这份机械诊断降级成 `source_evidence` 提示并要求模型换根因。

### C. Tester 的确定性反例（`ai_eda/agent/subagents/tester.py`）
- `7d87df5` L1 且 `stage=="simulation"` 时额外跑 `execution_observation(combined_path, 1)` 与 `l1_control_observation(combined_path)`，挂进报告（1–2 s，对比教师 TB 40 s+）。
  实测那份 86 条失败的快照输出 `EXEC|scenario=1|cycles=24|retired=4|register=00000000|first_reads=00000100:00000029`，直接指出“load 读回 0x29 正确、写回缺失”，比 86 条断言更接近根因。

### D. Patch（`ai_eda/agent/subagents/patch_agent.py`）
- `e7ec122` 编译闸门从 `if cpu_simulation:` 改为 `if cpu_level is not None:`：此前 compile 阶段的补丁**完全不验编译**，实测一次运行 9 轮 tester 全卡在编译错误；补丁 `max_tokens` 12000→24000。

### E. 编排止损（`ai_eda/agent/orchestrator.py`）
- `91b4a32` 失控设计早停：首轮 `stage=="simulation"` 且失败 check 数 > `EDA_RUNAWAY_CHECKS`（**默认 2000**，`a6738a9` 从 50000 降下来）→ `repair_stop="runaway_design: N failing checks"`。实测坏设计首轮 9,707 / 20,711 / 230,275，好设计 0–150。
- `6dc516f` 死设计早停：首轮 `execution_observation` 里 `retired <= EDA_STALL_RETIREMENTS`（默认 5，观测窗口 1024 拍）→ `repair_stop="stalled_design: retired=N"`。实测好设计 retired≈1015，死设计 retired=1。
- `91b4a32` `run_summary` 增 `initial_failed_checks`（首轮失败数，用来做形态统计）。

### F. Planner（`ai_eda/agent/cpu_workflow.py`、`subagents/planner.py`）
- `cab7db4` `if_id_ready_message()`：原来错误文案打印的是**期望方向**而不是实际方向（“hazard_unit.if_id_ready is input”而模型写的是 output），于是 4 轮 Planner 全被同一句话拒掉、105 秒作废。现在写实际值 + 解释。
- `8460302` `dedupe_plan_ports()`：重名端口（实测 `mem_stage.mem_wb_fwd_data`）机械删重复声明，不再回喂模型重写。
  这使 `checks/test_repair_workflow.py::test_l2_planner_retries_when_thinking_exhausts_the_output_budget` 的期望调用数从 3 变 2，我改了断言并加注释。

### G. 用量与报表（`ai_eda/model/client.py`、`scripts/`）
- `cab7db4` 流式请求加 `stream_options.include_usage`，`_usage_payload()` 归一化并保留 `cached_tokens`；此前 `run_summary.tokens` 全是 `null`（`{}` 让 `all(isinstance(int))` 为假），钱算不出来。
- `ed3ac54` 新增 `scripts/cost_report.py`、`scripts/report_runs.py`。

---

## 4. 实测数据（2026-11-12，全部独立全新运行、同一命令）

今天 23 次：**通过 6 次 = 26%**（你交付时的历史基线是 2/15 ≈ 13%）。

```
09:11 ✅ 0    7 调用  3 轮 202s ¥0.21
09:19 ✅ 0   14      4    319s ¥0.58
09:19 ❌ 90  23     11    655s ¥0.85
09:31 ❌ 编译  27      9    403s ¥0.68
09:31 ❌ 150 30      8    758s ¥1.26
09:43 ❌ 230275 15  11    438s ¥0.32
09:44 ❌ 1   29     10    942s ¥1.70   ← 差一条
09:44 ❌ 9707 26    11    557s ¥1.23
10:04 ✅ 0    8      3    221s ¥0.25
10:04 ❌ 20  13     11    246s ¥0.22
10:04 ❌ Planner 5   0    105s ¥0.24
10:14 ❌ 20711 13   11    259s ¥0.37
10:14 ✅ 0   23     10    515s ¥1.15
10:14 ✅ 0   17      8    260s ¥0.65
10:24 ❌ 24  26     11    563s ¥1.11
10:24 ❌ 1   23     11    463s ¥0.97
10:24 ❌ 25  13     11    450s ¥0.35
10:35 ❌ Planner 5   0    104s ¥0.26
10:35 ❌ 21525 4     1    127s ¥0.35  ← 失控早停生效
10:35 ❌ 编译  33     2    357s ¥0.87
10:42 ✅ 0   23     10    616s ¥1.21
10:42 ❌ 25  26     10    475s ¥1.07
10:42 ❌ 25   5      1    174s ¥0.40  ← 死设计早停生效
```

花费：单次 ¥0.21–1.70，**通过的运行均值约 ¥0.68、失败的约 ¥0.75**；今天 23 次合计约 ¥16。
两个早停把“坏运行”从 ¥0.9–1.2 / 450–750 s 压到 ¥0.35–0.40 / 127–174 s。

---

## 5. 仍未解决的问题（按我判断的价值排序）

1. **初始生成质量决定成本**：通过的运行平均还要 10 轮修复（616 s / ¥1.21）。第一次生成若能少 30 条失败，省一半钱。
2. **最后 1–25 条修不动**：集中在三族——
   - `t06b_neg_imm` 负立即数访存地址未符号扩展（`agent-20261112T102447841479` 只剩这一条）；
   - `t01_reset` 停机期间 `imem_addr` 不稳定（0x18→0x1c）；
   - `t06_load_store`/`t08_store_fwd` 的 SB/SH 字节车道。
   这三族都适合先用本地探针（1–2 s）挡住，而不是让模型在整机仿真里猜。
3. **建议但没做**：把教师 TB `include` 进来、只跑指定 testcase 的**单用例复现**（约 2–4 s）。Patch 可以先自证再花 40 s 教师仿真。需要新写一个派生 TB 包装（不改教师文件）。
4. L2 目前 29 次运行、通过 5 次**全部 `independent=false`**（resume/lineage）；代码里只有 L3 有种子机制（`orchestrator.py:277-289` 的 `component_seed_from` 要求 `expected_cpu_level==3`），**没有 L1→L2 种子路径**，而 `rv32i5_contract.py:232` 规定 L2 合法来源是 `rv32i5_cache`（L1）。要做 L2 得先补这条路径。

---

## 6. 开关表（全部默认关=保持我的行为，可一键回退）

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `EDA_VERDICT_STRICT` | 关（不严格） | `1` = 回到“有新失败用例就整份否决”的旧口径 |
| `EDA_RUNAWAY_CHECKS` | 2000 | 首轮失败 check 超过即停（失控设计） |
| `EDA_STALL_RETIREMENTS` | 5 | 首轮 1024 拍退休数 ≤ 此值即停（死设计） |
| `EDA_DIAGNOSE_TOKENS` | 6000 | 诊断输出上限 |
| `EDA_BULK_DROP` | 40 | “断言大幅下降”的阈值 |
| `EDA_PRICE_IN/OUT/CACHE` | 2 / 8 / 0.04 | 成本脚本单价（元/百万） |

---

## 7. 雷区

- `content/tasks/homework_rv32i5/` 是教师文件（25 个），**只读**；`run_summary` 里有 `teacher_spec_sha256` / `teacher_tb_sha256` 可用于核对。
- 不要动 pristine 备份目录。
- 参考实现 `content/agent/skills/rv32i5_l1_reference/` 只作开发期教练，不要让它成为运行期依赖（用户明确要求 agent 自主生成）。
- 模型只能用 `DeepSeek-V4.1-Flash-1017`（用户明确说过不要换）。
- 我的 7 个新测试里，`test_planner_feedback.py` 和 `test_plan_dedupe.py` 是为这两个修复建的，改这两个函数时记得同步。
