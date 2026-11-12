# 作业一交付包 · AES-128 加密核 Prompt 设计

本包是「AI EDA 2026 作业一」的完整交付物：一份最终报告、最终 Prompt 与生成的 RTL、以及支撑报告数字的全部运行记录。

## 目录结构

```
作业一_交付包/
├─ 作业提交_作业一_报告.docx        最终报告（11 页，作业二部分保留模板）
├─ 作业提交_作业一_报告.pdf         同上，PDF 版便于直接查看
├─ 运行记录汇总.csv                 16 次运行的 TB 结果 / 面积 / 功耗 / tokens / 墙钟
├─ README.md                        本文件
├─ rtl/
│  ├─ aes128_final.sv               最终采用 RTL，即 hw1-v8r2 的 generated.sv
│  └─ aes128_4sbox_ref.sv           手写的 4-Sbox 参考实现（用于确认面积余量，非模型产物）
├─ prompts/
│  ├─ H1_custom_final_v8.md         最终 Prompt（与项目内 content/prompts/homework/H1_custom.md 一致）
│  ├─ H1_custom_v8.md               同上，带版本号的快照
│  ├─ H1_custom_v4.md               v4：20 个 S-box 版本（面积 35.0 k，15/15 通过）
│  └─ H1_custom_v6.md               v6/v7：首次改成 4-Sbox 共用骨架的版本
├─ tools/
│  └─ fill_report_hw1.py            读 runs/ 自动填报告的脚本
├─ runs_success/                    v8 的 8 次成功运行（含 hw1-v8r2 的 activity.vcd）
│  └─ hw1-v8r1 … hw1-v8r8/          prompt.md / generated.sv / raw_response.txt / test_report.json / metadata.json
└─ runs_failed/                     中间版本的失败样本（只保留 RTL 与报告 JSON，便于对照）
   ├─ hw1-v5r1 … v5r3/              v5：SubBytes 被展开成 16 个实例
   ├─ hw1-v6r1/                     v6：MixColumns out3 写错
   └─ hw1-v7r1 … v7r4/              v7：phase=5 又异或了一次 nw
```

## 最终结果

| 指标 | 数值 |
| --- | --- |
| 教师 TB | PASS（5 组 FIPS/重复/复位向量） |
| 出结果延迟 | 61 拍（start 后到 done，上限 200 拍） |
| 面积 | 17091.44（gscl45nm 面积单位） |
| 总功耗 | 9.88 mW（internal 6.35 / switching 3.45 / leakage 0.08） |
| Tokens | 11127（prompt 4895 + completion 6232） |
| 生成墙钟 | 21.7 s（另加 TB + PPA 约 6 s） |
| 复现性 | 8 次独立运行 8 次通过，面积 17091.44–17190.93（波动 0.6%） |

与课程参考实现对比：面积 17091 vs 35803（小 52%），总功耗 9.88 mW vs 42.6 mW（低 77%），代价是延迟 10 → 61 拍。

## 复现方式

环境：macOS arm64，Python 3.11；`iverilog 13.0`、`yosys 0.70`、`OpenSTA 3.1.0`；模型 `DeepSeek-V4.1-Flash-1017`（paratera 兼容接口）。

```bash
cd THU_AI_EDA_2026
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
echo "sk-..." > .api_key          # paratera API key

.venv/bin/python -m ai_eda run --task homework_aes128 \
    --prompt homework/H1_custom.md --run-id hw1-v8r2 --max-tokens 12000

.venv/bin/python -m ai_eda retest --run-dir runs/prompt/homework_aes128/hw1-v8r2
```

`run` 一次完成：调模型 → 抽取 RTL → 跑教师 TB → 综合面积 → 功耗估算，产物全部落在 `runs/prompt/homework_aes128/<run-id>/`。

## Prompt 迭代与失败模式（报告第 6 页有展开）

| 版本 | S-box 实例 | 面积 | 通过率 | 主要问题 |
| --- | --- | --- | --- | --- |
| v1–v3 | 20（8 组） | 35.0 k | 80%（12/15） | 字节序写反、MixColumns 跨列、重复 SubBytes |
| v4 | 20（8 组） | 35.0 k | 100%（15/15） | 加了轮函数边界说明后稳定 |
| v5 | 16（并行展开） | 36.8 k | 2/3 | 模型把 SubBytes 展开成 16 个实例，反而更差 |
| v6 | 4 | 17.0 k | 0/1 | MixColumns 的 out3 少 `xtime(b0)`、多异或 `b3` |
| v7 | 4 | 17.1 k | 0/4 | phase=5 又异或 `nw`，密钥多推一轮 |
| **v8** | **4** | **17.1 k** | **8/8** | 给出 phase 状态表 + 输入多路选择器 + MixColumns 四行模板 |

结论：把「共用 4 个 S-box」这种非默认结构写成可照抄的骨架（phase 表、`in_a0..in_a3` 选择器、四行 MC 模板）比只描述意图可靠得多；反之，任何一处轮函数边界含糊，模型都会给出「握手全对、密文全错」的样本，只能靠波形逐拍对照定位。
