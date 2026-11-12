"""Parser / Planner / Coder / Tester / Modifier 共用的协议与工具函数。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ai_eda.agent.reporting import report_summary, stage_label

PARSER_SYSTEM_PROMPT = """You are the Spec Parser of a digital IC design agent.
Decide whether the given design spec (plus user supplements and previous parser addendum) is sufficient to implement, plan, and verify the design.
Your job is to SUPPLEMENT and CORRECT the original spec, never to summarize or replace it.

Blocking gaps (ask the user):
- missing/contradictory top-level ports, clock/reset, ISA behavior, external bus handshake, ECALL/halt behavior, file/task structure, or externally observable test expectations;
- requirements that make a correct implementation impossible or make PASS depend on an undefined architectural choice.

Non-blocking details (do NOT ask; record short assumptions in spec_addendum instead):
- internal module port names, internal handshake/FSM/state encoding details;
- Cache refill arbitration, epoch bit-width/implementation, internal buffers/queues, predictor table encoding;
- ROB/rename/issue implementation details not externally observable;
- optional auxiliary file names or internal RAM implementation choices.
These belong to Planner/Coder/Modifier and may be frozen later; do not block the flow on them.

When only non-blocking gaps remain, set sufficient=true, missing=[], questions=[], and put concise assumptions in spec_addendum.
Keep every original requirement intact. Return ONLY a JSON object:
{"sufficient": true|false, "missing": ["..."], "questions": ["..."], "spec_addendum": "only additional/corrected requirements or assumptions in Markdown; empty if none"}
Do not put the full original spec in spec_addendum. Do not return Markdown fences or extra text."""

PLANNER_SYSTEM_PROMPT = """You are the RTL Planner of a digital IC design agent.
Given the frozen spec, plan the project module and file structure. The agent will write each file separately.
Return ONLY a JSON object:
{"project_name": "...", "top_module": "...", "modules": [{"name": "module_name", "file": "rtl/module_name.sv", "description": "short responsibility", "interfaces": "complete port contract: every port name, direction and width; for each inter-module signal use the same name/width in all modules"}], "notes": "..."}
Use only relative paths ending in .sv. Every module must be implementable in one file. The interfaces field is the interface contract: list every port name, direction and width, and use identical names/widths for a signal in every module. Do not return Markdown fences or extra text."""

RV32I_L1_CODE_PATTERNS = """RV32I L1 tactical patterns (use these structures; keep interfaces from the plan):
1) IF response / stall:
  always_ff @(posedge clk) begin
    if (!rst_n) ...
    else if (redirect_valid) ...
    else if (ecall_in_id && !halt_pending && !mem_stall) begin
      halt_pending <= 1'b1; req_pending <= 1'b0; end
    else if (cpu_resp_valid) begin
      if2id_valid<=1; if2id_pc<=pc; if2id_pc4<=pc+4; if2id_insn<=cpu_resp_data;
      pc<=pc+4; req_pending<=1; end
    else if (stall) begin if2id_valid<=if2id_valid; end
    else begin if (req_pending && cpu_req_ready) begin req_pending<=0; if2id_valid<=0; end end
  end
  Never put a stall branch before the one-cycle cpu_resp_valid branch, and never clear if2id_valid on stall.
2) MEM response:
  if (mem_active) begin
    if (cpu_resp_valid) begin mem2wb_valid<=1; ...; mem_active<=0; mem_wait_resp<=0; end
    else if (!mem_wait_resp && cpu_req_ready) mem_wait_resp<=1;
  end
3) DCache word/store:
  assign hit_word = line_data[{wsel,5'b00000} +: 32];
  // store hit: merge only enabled bytes into the selected word, then write back
  // store miss: do not claim a valid line with stale data; either merge the refilled word
  // or invalidate the line and refill on the next load.
4) ICache refill:
  // do not return state to IDLE on flush. Keep mem_req_valid/addr stable until mem_req_ready.
  // On the last word always install valid/tag/data; gate only resp_valid_r/resp_data_r
  // with flush_epoch==refill_epoch. For refill_word==3 use the current mem_resp_data.
5) EX forwarding:
  rs1_fwd = id2ex_rs1_val;
  if (fwd_a==2'b10) rs1_fwd = ex2mem_result;       // EX/MEM ALU result
  else if (fwd_a==2'b01) rs1_fwd = mem2wb_wdata;   // MEM/WB result
  // same for rs2; ex2mem_result for JAL/JALR must be pc4, not the address ALU output.
6) Halt: only WB sees mem2wb_valid && mem2wb_is_ecall and sets halted/exit_code; preserve is_ecall through stall.
"""

RV32I_PIPELINE_CHECKLIST = """RV32I L1/L2 implementation hard checks:
- One-cycle response pulses: if a cache/bus producer asserts resp_valid for one cycle, the consumer must capture resp_valid unconditionally before checking a wait/ready flag; never rely on a wait flag that is set only on the following cycle.
- Stall/freeze: while load_use_stall, mem_stall, or structural stall is active, hold IF/ID valid and ID/EX valid; never clear a valid instruction just because the downstream stage is stalled.
- ECALL/halt: ECALL must stay valid through ID -> EX -> MEM -> WB while stalls are active; only WB may set halted/exit_code. Do not drop an ECALL that is waiting in IF/ID while ID is frozen.
- Cache word lanes: for a 128-bit line, word selection is wsel*32 bits, e.g. line_data[{wsel,5'b00000} +: 32], not wsel*4 bit.
- Store merge: write-through store hit must merge only byte-enable lanes into the line; store miss should either merge the requested word explicitly or invalidate the line (no-write-allocate) so a later load refills correct data.
- Cache line refill: never abort an already-issued external refill before ready; hold valid/address stable until ready, finish all words, then install valid/tag/data (a redirect may suppress only the response, not destroy the line).
- I-Cache refill word3: use the current last memory response for word3, not an old/empty refill register.
- Forwarding: implement BOTH EX/MEM (fwd=2'b10) and MEM/WB (fwd=2'b01) paths; JAL/JALR EX/MEM result must select pc4/pc according to wb_sel, never blindly forward ALU result.
- Halt/reset: synchronous active-low reset, dmem_valid=0 during reset; halted/exit_code are registered and stable after ECALL commits.
"""

MODIFIER_SYSTEM_PROMPT = """You are the RTL Modifier of a digital IC design agent.
You work in a persistent multi-turn repair conversation. The first user message contains the frozen spec, project plan, current RTL files, and the first failure report; later user messages contain the next Tester failure and the current file snapshot.
Your job is to diagnose the root cause and modify only the files that need changes.

Rules:
- Match the Planner interfaces exactly. Never rename ports, modules, files, or parameters.
- For compile failures, fix every error in stderr, including hierarchy/port/width errors.
- For simulation failures, use the failing checks and trace to find the root cause; do not guess or weaken behavior.
- Preserve already-correct logic. A small targeted patch is better than rewriting the project.
- Do not add external IPs, simulation-only constructs, or modify the teacher TB.
- Keep diagnosis focused: identify the minimal root cause, avoid restating the spec or exploring unneeded alternatives.
- Build a short failure matrix before editing: failing check -> expected/actual -> affected module(s). Cover every failed check category, not just the first one.
- If the same check failed in the previous repair round, do not repeat the same patch. Re-read the failing trace and change the data path or state transition that produces the mismatch.
- RV32I load/store failures: explicitly verify little-endian byte lanes, sign/zero extension, SB/SH byte enables and replicated wdata, cache refill merge, write-through ordering, and load-use forwarding.
- RV32I reset/control failures: verify synchronous reset gating of dmem_valid, pipeline valid/flush, PC redirection, and halted/exit_code timing.
- If many tests time out waiting for halted while imem_addr advances, prioritize the ECALL→halt path: ID decode, is_ecall propagation through EX/MEM/WB, WB halted/exit_code commit, IF freeze, and synchronous reset. Do not assume a cache or load/store bug from the timeout alone.
- Before returning JSON, self-check each failed check from the latest report and state the repair in notes. Do not return files that only address a subset.
- Compare every returned content with the current file snapshot. At least one returned file MUST differ from the snapshot; returning an unchanged file is an invalid no-op patch.
- If a previous patch did not change behavior, do not repeat it; change the root-cause hypothesis and the implementation strategy.
- Return ONLY a JSON object; no Markdown fences, no prose outside JSON.

Return schema:
{"files": [{"path": "rtl/foo.sv", "content": "full SystemVerilog file content"}], "notes": "short diagnosis and repair summary"}
You may return only files that need changes, but each returned file must contain its complete content.

"""

MODIFIER_SYSTEM_PROMPT += "\n\n" + RV32I_PIPELINE_CHECKLIST + "\n\n" + RV32I_L1_CODE_PATTERNS

MAX_PARSER_ROUNDS = 3
MAX_TEST_ATTEMPTS = 9
SV_PATH_PATTERN = re.compile(r"^[A-Za-z0-9_./-]+\.sv$")
MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def extract_json_payload(text: str) -> dict[str, Any]:
    """从模型回答中提取第一个 JSON 对象（兼容 ```json 围栏）。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(raw[start : end + 1])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    raise ValueError("模型没有返回合法的 JSON 对象")


def safe_project_path(run_dir: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("RTL 文件路径不能为空")
    relative = relative.strip().replace("\\", "/")
    if not SV_PATH_PATTERN.fullmatch(relative):
        raise ValueError(f"RTL 文件路径必须是相对 .sv 路径：{relative}")
    project_root = (run_dir / "project").resolve()
    candidate = (project_root / relative).resolve()
    if project_root not in candidate.parents:
        raise ValueError(f"RTL 文件路径越出项目目录：{relative}")
    return candidate


def validate_plan(payload: dict[str, Any]) -> dict[str, Any]:
    modules = payload.get("modules")
    if not isinstance(modules, list) or not modules:
        raise ValueError("Planner 没有返回 modules 列表")
    clean_modules: list[dict[str, str]] = []
    seen_names: set[str] = set()
    seen_files: set[str] = set()
    for item in modules:
        if not isinstance(item, dict):
            raise ValueError("Planner 的 module 条目格式无效")
        name = item.get("name")
        file = item.get("file")
        if not isinstance(name, str) or not MODULE_NAME_PATTERN.fullmatch(name):
            raise ValueError("Planner 返回了非法模块名")
        if name in seen_names:
            raise ValueError(f"Planner 重复声明模块：{name}")
        if not isinstance(file, str):
            raise ValueError("Planner 的模块缺少 file")
        file = file.strip().replace("\\", "/")
        if not SV_PATH_PATTERN.fullmatch(file):
            raise ValueError(f"Planner 返回了非法文件路径：{file}")
        if file in seen_files:
            raise ValueError(f"Planner 重复声明文件：{file}")
        seen_names.add(name)
        seen_files.add(file)
        clean_modules.append(
            {
                "name": name,
                "file": file,
                "description": str(item.get("description") or "")[:500],
                "interfaces": str(item.get("interfaces") or "")[:6000],
            }
        )
    top_module = payload.get("top_module") or clean_modules[0]["name"]
    if not isinstance(top_module, str) or top_module not in seen_names:
        raise ValueError("Planner 的 top_module 不在 modules 中")
    return {
        "project_name": str(payload.get("project_name") or "agent_project")[:100],
        "top_module": top_module,
        "modules": clean_modules,
        "notes": str(payload.get("notes") or "")[:6000],
    }


def validate_modifier_files(payload: dict[str, Any], plan: dict[str, Any]) -> dict[str, str]:
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("Modifier 没有返回 files 列表")
    allowed = {module["file"] for module in plan["modules"]}
    clean: dict[str, str] = {}
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Modifier 的 file 条目格式无效")
        path = item.get("path")
        content = item.get("content")
        normalized = path.strip().replace("\\", "/") if isinstance(path, str) else ""
        if normalized not in allowed:
            raise ValueError(f"Modifier 返回了计划之外的文件：{path}")
        if not isinstance(content, str) or "module" not in content:
            raise ValueError(f"Modifier 为 {path} 返回的内容不是 RTL")
        clean[normalized] = content
    return clean


def combine_files(files: dict[str, str]) -> str:
    return "\n\n".join(f"// file: {path}\n{content}" for path, content in files.items()) + "\n"


def trace_mismatches(report: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    for row in report.get("trace") or []:
        differences = []
        for key, value in row.items():
            if not key.startswith("expected_"):
                continue
            actual_key = key.removeprefix("expected_")
            actual = row.get(actual_key)
            if actual is None:
                actual = row.get(f"out_{actual_key}")
            if actual is not None and str(actual) != str(value):
                differences.append(f"{actual_key}={actual} (expected {value})")
        if differences:
            label = row.get("event") or row.get("case") or "sample"
            messages.append(f"cycle {row.get('cycle', '?')} · {label}: " + "，".join(differences))
            if len(messages) == 12:
                break
    return messages


def report_feedback(report: dict[str, Any] | None) -> str:
    if report is None:
        return "Verification has not run for the previous round."
    if report.get("passed"):
        return "The previous teacher TB passed; preserve current behavior and finish the remaining steps."
    lines = [report_summary(report)]
    mismatches = trace_mismatches(report)
    if mismatches:
        lines.append("Failing cycles:")
        lines.extend(f"- {item}" for item in mismatches[:12])
    suites = [
        item.get("name") for item in (report.get("test_suites") or [])
        if item.get("name") and not item.get("passed") and not item.get("skipped")
    ]
    if suites:
        lines.append("Failing suites: " + ", ".join(suites))
    checks = report.get("checks") or []
    timeout_count = 0
    if checks:
        lines.append("Failed checks:")
        lines.extend(f"- {item.get('name')}: {item.get('detail', '')}".rstrip(": ") for item in checks[:20])
        if len(checks) > 20:
            lines.append(f"- ... {len(checks) - 20} additional failed checks omitted")
        timeout_count = sum("等待 halted 超时" in str(item.get("detail") or "") for item in checks)
        if timeout_count >= 2:
            lines.append(
                "HALT-TIMEOUT 诊断提示：多个测试都等到 halted 超时，且 imem_addr 仍在变化；"
                "请优先检查 ECALL 在 ID 级的识别、ID→EX→MEM→WB 的 is_ecall 传递、"
                "WB 的 halted/exit_code 更新、IF/流水线 halt 冻结，以及同步复位；"
                "不要只修 Cache 或单个数据通路。"
            )
    if report.get("stage") == "simulation" and checks:
        if timeout_count:
            lines.append(
                "HALT-TIMEOUT: 先检查单拍 resp_valid 是否被 wait 标志漏采、stall 时 IF/ID/ID-EX valid 是否被清、"
                "以及 ECALL 是否在 ID 冻结时被丢弃；不要先只改 Cache 数据通路。"
            )
        if any(item.get("name") == "cache_instruction_reuse" or any(
            term in str(item.get("detail") or "") for term in ("imem 请求", "I-memory request changed")
        ) for item in checks):
            lines.append(
                "ICACHE/REFILL: 已经拉高的 imem_valid/addr 保持到 ready 接受。"
                "若重定向使其过期，接受该字后丢弃剩余 refill，禁止安装过期 Cache 行或向 IF 返回旧响应。"
            )
    if report.get("stage") == "compile":
        detail = (report.get("stderr") or report.get("stdout") or "").strip()
        if detail:
            lines.append("Compiler output (tail):")
            lines.append(detail[-3000:])
    return "\n".join(lines)


def project_feedback(files: dict[str, str], plan: dict[str, Any], report: dict[str, Any]) -> str:
    lines = [report_summary(report), "当前项目文件："]
    lines.extend(f"- {path} ({len(content)} chars)" for path, content in files.items())
    lines.append("计划模块：")
    lines.extend(f"- {module['name']} -> {module['file']}" for module in plan["modules"])
    lines.append("")
    lines.append(report_feedback(report))
    return "\n".join(lines)
