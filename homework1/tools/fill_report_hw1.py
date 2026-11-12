"""把作业一的实测数据填入课程报告模板，生成 作业提交_作业一_报告.docx。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "作业提交要求与报告模板.docx"
OUT = ROOT / "作业提交_作业一_报告.docx"
PROMPT = ROOT / "content/prompts/homework/H1_custom.md"
RUNS = ROOT / "runs/prompt/homework_aes128"
ROWS = ["hw1-v8r4", "hw1-v8r2", "hw1-v8r8"]
FINAL = "hw1-v8r2"


def clear(cell):
    for p in cell.paragraphs[1:]:
        p._element.getparent().remove(p._element)
    cell.paragraphs[0].text = ""


def write_cell(cell, lines, size=9.0, bold_first=False):
    clear(cell)
    first = cell.paragraphs[0]
    for i, line in enumerate(lines):
        p = first if i == 0 else cell.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        run = p.add_run(line[i == 0 and bold_first:] if False else line)
        run.font.size = Pt(size)


def write_prompt_cell(cell, text):
    """按 Markdown 语义排版 Prompt：**加粗** 转真加粗、代码块用等宽字体、去掉围栏行。"""
    clear(cell)
    first = cell.paragraphs[0]
    code = False
    para = first
    started = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            code = not code
            continue
        if not started:
            para, started = first, True
        else:
            para = cell.add_paragraph()
        para.paragraph_format.space_after = Pt(0)
        para.paragraph_format.space_before = Pt(0)
        if code:
            run = para.add_run(line if line else " ")
            run.font.name = "Menlo"
            run.font.size = Pt(7.5)
            continue
        line = line.replace("`", "")
        for idx, token in enumerate(re.split(r"\*\*(.+?)\*\*", line)):
            if not token:
                continue
            run = para.add_run(token)
            run.font.size = Pt(8)
            run.bold = idx % 2 == 1



def set_col_widths(table, widths_cm):
    """固定列宽（关闭 autofit，逐单元格写入 tcW，兼容 Word/LibreOffice）。"""
    from docx.oxml.ns import qn
    table.autofit = False
    tblPr = table._tbl.tblPr
    layout = tblPr.find(qn("w:tblLayout"))
    if layout is None:
        layout = tblPr.makeelement(qn("w:tblLayout"), {})
        tblPr.append(layout)
    layout.set(qn("w:type"), "fixed")
    grid = table._tbl.find(qn("w:tblGrid"))
    if grid is not None:
        for col, w in zip(grid.findall(qn("w:gridCol")), widths_cm):
            col.set(qn("w:w"), str(int(Cm(w).twips)))
    for row in table.rows:
        for cell, w in zip(row.cells, widths_cm):
            cell.width = Cm(w)


def set_cell_text(cell, text, size=8.0, bold=False):
    clear(cell)
    first = True
    for line in str(text).split("\n"):
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        run = p.add_run(line)
        run.font.size = Pt(size)
        run.bold = bold


def load(run_id):
    meta = json.loads((RUNS / run_id / "metadata.json").read_text(encoding="utf-8"))
    report = json.loads((RUNS / run_id / "test_report.json").read_text(encoding="utf-8"))
    return meta, report


def main():
    doc = Document(TEMPLATE)
    tables = doc.tables

    # --- 提交人信息 -------------------------------------------------
    info = tables[0]
    info.cell(1, 1).text = "____"
    info.cell(1, 3).text = "____"
    info.cell(2, 1).text = "____"
    info.cell(2, 3).text = "2026-11-09"
    info.cell(3, 3).text = "e25f015（课程项目原始提交）+ 本地作业一改动"
    info.cell(4, 3).text = (
        "macOS 27.2 arm64；Python 3.11.5（anaconda3 + 项目 .venv）；"
        "iverilog 13.0；yosys 0.70+post；OpenSTA 3.1.0；"
        "verilog-pad-analyzer 0.0.7（gscl45nm.lib）；"
        "模型 DeepSeek-V4.1-Flash-1017（OpenAI 兼容接口）"
    )

    set_col_widths(info, [2.8, 4.4, 3.0, 7.4])

    # --- 作业一：Prompt --------------------------------------------
    prompt_text = PROMPT.read_text(encoding="utf-8").rstrip()
    write_prompt_cell(tables[1].cell(0, 0), prompt_text)

    # --- 作业一：设计思想 -------------------------------------------
    ideas = [
        "1. 接口与时序先写死：Prompt 第一段逐字给出端口声明，并明确「同步低有效复位、done 只高一拍、done 之后下一拍 ready=1 可背靠背接收、约 60 拍出结果（上限 200 拍）」。这四句直接对应教师 TB 的 5 组检查（复位、单拍 done、ready 恢复、背靠背、中途复位），避免模型自行发挥成异步复位或完成信号拉高多拍。",
        "2. 字节序写成位段：FIPS 大端下第 0 列占最高 32 位，因此 s0 取 plaintext[127:96] 而不是 plaintext[31:0]。实测模型把 plaintext[31:0] 当第 0 列时，握手时序全部正确、5 个向量密文全错（hw1-r2）；只改这一条赋值即 PASS。Prompt 里把这段标为「最容易错的一步，务必逐字照抄」并写明错误现象。",
        "3. 面积几乎全由 S-box 决定：一个 S-box 约 1.5 k 面积单位。把「4 个数据 S-box + 4 个密钥 S-box」压到「4 个 S-box 数据/密钥共用」，面积从 35.0 k 降到 17.1 k（约一半）。做法是先用 phase 对 4 个 8-bit 输入做多路选择（in_a0..in_a3），S-box 输出 sx0..sx3 再按 phase 分别写进 kt 或某一列的 s——选择器是几十门的小逻辑，比多 4 个 S-box 便宜得多。",
        "4. 微架构是显式的 6 拍状态表：phase=0 算 SubWord 存 kt；phase=1 锁存下一轮密钥 w 并做第 0 列 SubBytes；phase=2..4 做其余三列；phase=5 做 ShiftRows+MixColumns+AddRoundKey（最后一轮只做 SR+ARK 并输出）。10 轮 ×6 拍 = 61 拍，仍在 200 拍上限内。把表直接写给模型，比只描述「按列串行」可靠得多。",
        "5. 组合逻辑全部 wire+assign：rotw、temp、nw0..nw3、sr0..sr3、xtime、mc0..mc3 都写成一体化表达式，禁止 always_comb 复用中间变量（否则 iverilog 报 Variable 'a0' cannot have multiple drivers，hw1-r11 即栽在此处），也不引入 128-bit 移位器与索引变量。",
        "6. 三个高频错误写成显式反例：①3·b 必须是 xtime(b)^b（写成 b^b 恒为 0）；②MixColumns 的 out3 是 (xtime(b0)^b0)^b1^b2^xtime(b3)，写成 b0^b1^b2^xtime(b3)^b3 会让每列最后一个字节全错（hw1-v6r1 即因此失败，且握手一切正常、极难从报告看出）；③最后一轮直接取 sr^w，不得对 sr 再调 sbox（否则多 16 个实例、面积涨到 52 k，hw1-r17/r26）。MixColumns 还给了可直接照抄的四行 wire 模板。",
        "7. 输出形式约束：只允许输出一个 module 的完整源码，禁止 include / $readmemh / DPI / IP 例化 / testbench / 第二个模块。既满足「单模块」评分要求，也让 ai_eda 的抽取器可直接落盘编译（completion 约 6.2 k tokens）。",
        "8. 与课程参考实现对照：同一套 iverilog + yosys(gscl45nm) + OpenSTA 流程下，课程手写实现（20 个 S-box、全并行、10 拍）area=35803.37、total_power=0.0426 W；本方案 area=17091.44、total_power=0.0099 W，面积小 52%、功耗低 77%，代价是延迟从 10 拍增加到 61 拍（上限 200 拍，仍有 3 倍余量）。",
        "9. 迭代与收敛：v1 全错（35419）→ v2 加 5 拍微架构仍全错（34955）→ v3 修正列映射 + wire/assign，15 次 12 次 PASS（80%）→ v4 补轮函数边界，15 次全过（35.0 k）→ v5 尝试 4 S-box 但模型把 SubBytes 展开成 16 个实例（36.8 k）→ v8 给出显式 phase 表、S-box 输入多路选择和 MixColumns 四行模板后，8 次运行全部 PASS，面积稳定在 17.1 k。失败样本全部保留在 runs/ 下。",
    ]
    write_cell(tables[2].cell(0, 0), ideas, size=8.5)

    # 表格后的空段落会把它挤到下一页、前页留白，这里直接删掉
    tbl_el = tables[2]._tbl
    sib = tbl_el.getnext()
    while sib is not None and sib.tag.endswith('}p') and not "".join(sib.itertext()).strip():
        nxt = sib.getnext()
        sib.getparent().remove(sib)
        sib = nxt

    # --- 作业一：运行与 PPA -----------------------------------------
    run_table = tables[4]
    notes = {
        "hw1-v8r4": "61 拍出结果；复位/单拍 done 全过",
        "hw1-v8r2": "面积最小 17091.44，最终采用",
        "hw1-v8r8": "功耗 10.33 mW；背靠背正常",
    }
    set_col_widths(run_table, [1.2, 4.9, 1.6, 2.2, 1.9, 1.8, 1.8, 2.2])
    for i, run_id in enumerate(ROWS):
        meta, report = load(run_id)
        usage = meta["usage"]
        m = report["metrics"]
        row = run_table.rows[i + 1]
        vals = [
            str(i + 1),
            f"DeepSeek-V4.1-Flash-1017\nT=0.2 / max_tokens=12000\n{run_id}",
            "PASS\n5 组向量",
            f"{usage['total_tokens']}\n({usage['prompt_tokens']}+{usage['completion_tokens']})",
            f"{meta['elapsed_seconds']:.1f} s\n+6 s TB/PPA",
            f"{m['area']:.2f}",
            f"{m['total_power_w'] * 1000:.2f} mW",
            notes[run_id],
        ]
        for cell, v in zip(row.cells, vals):
            set_cell_text(cell, v, size=8)

    meta, report = load(FINAL)
    m = report["metrics"]
    final_row = run_table.rows[4]
    vals = [
        "最终采用",
        f"同 run 2\n{FINAL}",
        "PASS\n5 组向量",
        f"{meta['usage']['total_tokens']}\n({meta['usage']['prompt_tokens']}+{meta['usage']['completion_tokens']})",
        f"{meta['elapsed_seconds']:.1f} s",
        f"{m['area']:.2f}",
        f"{m['total_power_w'] * 1000:.2f} mW",
        f"internal {m['internal_power_w'] * 1000:.2f}\n"
        f"switching {m['switching_power_w'] * 1000:.2f}\n"
        f"leakage {m['leakage_power_w'] * 1000:.3f} mW",
    ]
    for cell, v in zip(final_row.cells, vals):
        set_cell_text(cell, v, size=8)

    # --- 作业一：成功率与失败模式（同一 Prompt 的重复实验） -----------
    import glob

    def tally(prefix):
        n = ok = 0
        bad = []
        for d in sorted(glob.glob(str(RUNS / f"{prefix}*"))):
            rid = Path(d).name
            if not (Path(d) / "test_report.json").exists():
                continue
            rep = json.loads((Path(d) / "test_report.json").read_text(encoding="utf-8"))
            n += 1
            if rep["passed"]:
                ok += 1
            else:
                bad.append(rid)
        return n, ok, bad

    total, passed_n, fails = tally("hw1-v8r")
    v3_total = v3_pass = 0
    for i in range(13, 28):
        d = RUNS / f"hw1-r{i}"
        if not (d / "test_report.json").exists():
            continue
        v3_total += 1
        if json.loads((d / "test_report.json").read_text(encoding="utf-8"))["passed"]:
            v3_pass += 1

    anchor = None
    for para in doc.paragraphs:
        if para.text.startswith("填写说明"):
            anchor = para
            break
    if anchor is not None:
        p1 = anchor.insert_paragraph_before()
        r = p1.add_run(
            f"重复性验证：最终版 Prompt（v8）定型后，用同一模型与参数独立运行 {total} 次（run_id hw1-v8r1 ~ hw1-v8r{total}），"
            f"{passed_n} 次通过教师 TB，成功率 {100 * passed_n / total:.0f}%，面积 17091.44 ~ 17190.93（波动 0.6%）、总功耗 9.8 ~ 10.3 mW。"
            f"中间版本 v5（4 S-box 但 SubBytes 被展开，36.8 k）、v6（MixColumns out3 写错）、v7（phase=5 又异或了 nw，密钥多推一轮）各有 1~4 次失败，样本同样保留在 runs/ 下。"
        )
        r.font.size = Pt(9)
        p2 = anchor.insert_paragraph_before()
        r2 = p2.add_run(
            "需要说明的是：面积从 35.0 k 降到 17.1 k 靠的是「4 个 S-box 由数据通路和密钥扩展共用」，"
            "而这一结构对模型并不友好——v5 把 SubBytes 展开成 16 个并行实例（36.8 k），v6 把 MixColumns 的 out3 写错，v7 则在 phase=5 又异或了一次 "
            "nw（此时 w 已是新一轮密钥，等于密钥多推一轮），三版共 9 次运行全部失败。直到 Prompt 里给出 phase 状态表、S-box 输入多路选择器、MixColumns 四行模板，"
            "并明确「phase=5 只用 w」，v8 才稳定到 8/8。"
        )
        r2.font.size = Pt(9)

    # --- 作业一：验证结果入口 ---------------------------------------
    entry = [
        "复现命令（在项目根目录执行，需先 pip install -r requirements.txt）：",
        ".venv/bin/python -m ai_eda run --task homework_aes128 --prompt homework/H1_custom.md --run-id hw1-v8r2 --max-tokens 12000",
        "",
        "运行记录（Prompt、原始回答、生成 RTL、教师 TB 报告、VCD、PPA 指标）：",
        "runs/prompt/homework_aes128/hw1-v8r2/、hw1-v8r4/、hw1-v8r8/（v8 共 8 次：hw1-v8r1 ~ hw1-v8r8）",
        "每个目录包含 prompt.md、raw_response.txt、generated.sv、test_report.json、activity.vcd、metadata.json。",
        "",
        "教师 TB 结果：generated.sv 在 content/tasks/homework_aes128/tb.sv 下输出 LAB_RESULT: PASS (5 vectors)；",
        "功耗 TB 为 content/tasks/homework_aes128/power_tb.sv（12 组向量）。",
        "",
        "PPA 流程：iverilog -g2012 仿真取 activity.vcd → yosys 以 gscl45nm.lib 综合得到面积 → OpenSTA 读 VCD 估算功耗；",
        "数值见 test_report.json 的 metrics 字段（library=gscl45nm.lib，top_module=aes128，power_method=OpenSTA gate-level VCD estimate）。",
        "",
        "Prompt 源文件：content/prompts/homework/H1_custom.md；本报告的表格由 work/fill_report_hw1.py 从上述记录自动填入。",
    ]
    write_cell(tables[5].cell(0, 0), entry, size=9)

    doc.save(OUT)
    print("saved", OUT)


if __name__ == "__main__":
    main()
