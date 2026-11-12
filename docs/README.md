# 提交物索引

| 文件 | 说明 |
|---|---|
| `作业提交_报告_郑珂_2022012348.pdf` / `.docx` | 作业一（AES-128 Prompt）+ 作业二（Agent 工作流重构）合并报告 |
| `agent-workflow.png` | 报告中插入的 Agent 流程图（Parser→Planner→Coder→Tester→Diagnoser→Patch Agent） |

代码与运行证据在仓库其它目录：

- `ai_eda/`：Agent 工作流实现（Parser/Planner/Coder/Tester/Diagnoser/Patch Agent、快照、判决器、探针）
- `scripts/run_cpu_agent.py`：复现入口；`scripts/report_runs.py`：运行汇总（tokens/花费）
- `evidence/`：关键运行的 `run_summary.json`、教师 TB 报告与最终 RTL 快照
- `homework1/`：作业一交付包（AES RTL、Prompt、成功/失败运行记录）

复现：

```bash
EDA_MODEL=DeepSeek-V4.1-Flash-1017 \
EDA_BASE_URL=https://llmapi.paratera.com/v1 \
python scripts/run_cpu_agent.py --level 3 --max-calls 25 --max-repairs 8
```

独立空白运行去掉 `--resume-from`；教师 Spec/TB 未作任何修改（`content/tasks/` 只读）。

## 凭据安全

仓库内不包含任何 API Key。`scripts/check_secrets.py` 会在提交前扫描 `sk-` 字面量，
通过 `.githooks/pre-commit` 生效（`git config core.hooksPath .githooks` 已设置）；
本地 key 只存在于 `.api_key`（`.gitignore` 第 30 行，权限 600）。
