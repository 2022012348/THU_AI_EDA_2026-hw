"""RTL Planner 子 Agent：规划模块与文件结构。"""

from __future__ import annotations

import json
from pathlib import Path

from ai_eda.agent.cpu_workflow import (
    CPU_PLANNING,
    IN_ORDER_BOUNDARIES,
    L2_PREDICTOR,
    L3_EDGE_CONTRACT,
    L3_OPERAND_PORTS,
    L3_PLANNING,
    dedupe_plan_ports,
    required_modules,
    validate_cpu_plan,
    validate_frozen_components,
    validate_l3_operand_ports,
)
from ai_eda.agent.skills.fifo_area_power import evaluate as evaluate_fifo_skill
from ai_eda.agent.skills.l3_architecture import l3_generation_plan

from .base import ModelOutputTruncated, SubAgent
from .common import PLANNER_SYSTEM_PROMPT, extract_json_payload, validate_plan


class PlannerAgent(SubAgent):
    name = "planner"
    title = "02 · RTL Planner"
    node_type = "planner"

    def run(self, parser_handoff: Path) -> Path:
        incoming = self.runtime.read_handoff(parser_handoff)["payload"]
        artifacts = incoming.get("artifacts") or {}
        spec_ref = artifacts.get("spec")
        if not isinstance(spec_ref, str):
            raise ValueError("Parser → Planner handoff 缺少 spec artifact")
        effective_spec = self.runtime.read_text_artifact(spec_ref)
        skill_result: dict | None = None
        skill_ref: str | None = None
        skill_prompt = ""
        project_id = (self.runtime.options.get("project") or {}).get("id")
        if self.runtime.options.get("planner_skill") and project_id == "stream_filter":
            skill_result = evaluate_fifo_skill(effective_spec)
            skill_ref = self.runtime.write_json_artifact("planner_skill.json", skill_result)
            skill_prompt = (
                "\n\nPlanner Skill 已运行，下面是面积/功耗预评估与架构参数。"
                "必须把 architecture 字段原样写入计划，并要求所有 Coder 严格遵守其中的 coder_directives：\n"
                f"{json.dumps(skill_result, ensure_ascii=False, indent=2)}\n"
            )
        level = self.runtime.options["project"].get("expected_cpu_level")
        cpu_requirements = ""
        if level is not None:
            names = ", ".join(required_modules(level))
            cpu_requirements = CPU_PLANNING + f"Required modules for L{level}: {names}. Use rtl/<module>.sv.\n"
        if level == 2:
            cpu_requirements += L2_PREDICTOR
            cpu_requirements += (
                "IF drives bp_lookup_pc from the current fetch PC. The branch predictor drives "
                "bp_lookup_next_pc and bp_lookup_valid back into IF. Declare those as IF inputs.\n"
            )
        if level == 3:
            cpu_requirements += L3_PLANNING + L3_EDGE_CONTRACT
        if level == 1:
            cpu_requirements += (
                "Plan a single outstanding I-cache request. IF saves PC and epoch on request acceptance, "
                "then holds them until the response. IF directly buffers one IF/ID instruction, retaining it "
                "until ID accepts. Name one IF hold input driven by decode backpressure from hazard, "
                "not by the I-cache CPU request handshake. The same hold must govern both the IF/ID "
                "consumption and the ID/EX acceptance, with load-use injecting a bubble when MEM is free. "
                "If the contract names if_id_ready, ID drives it and IF consumes it. "
                "Hazard's own output is a distinct stall signal, such as if_id_hold. If hazard "
                "needs if_id_ready, declare it as an input there. Never have hazard drive if_id_ready. "
                "A fetch request or response never drives readiness of the decode consumer. "
                "List each internal feedback port explicitly, including registered ID/EX operand-use flags "
                "at each consumer. Use dedicated owners for the active epoch and ECALL acceptance.\n"
            )
        if level in (1, 2):
            for module, (prefix, controls) in IN_ORDER_BOUNDARIES.items():
                clock_inputs = ", ".join(("clk", "rst_n", *controls))
                cpu_requirements += (
                    f"{module} owns the rising-edge {prefix} register. Include input logic {clock_inputs} "
                    "as separate declarations, and outputs "
                    f"logic {prefix}_valid, logic [31:0] {prefix}_pc, logic [31:0] {prefix}_insn. "
                    "Specify when the registered payload holds, transfers or clears.\n"
                )
            cpu_requirements += (
                "MEM owns the registered mem_wb_rd and mem_wb_reg_write. Declare each once as an output and "
                "read those output registers internally for store forwarding. The writeback-selected value may "
                "enter MEM through a separate mem_wb_fwd_data input. Connect WB's ECALL exit value to a dedicated "
                "architectural x10 read from the register file, independently of the currently decoded instruction. "
                "Declare a mem_busy output on mem_stage driven by the incomplete EX/MEM transaction, from before "
                "request acceptance through response capture. Connect it to hazard as the hold source. "
                "Use a distinct name for any hold input to MEM. Cache ready and MEM completion are separate events.\n"
                "If the design uses fwd_rs1_sel or fwd_rs2_sel, forwarding_unit produces them and "
                "ex_stage consumes them as inputs. Never declare the same selector as an output in both modules.\n"
                "Use independent two-bit forwarding selectors per operand: 0 for captured register data, "
                "1 for EX/MEM, 2 for MEM/WB. Do not collapse the two producer matches into a single bit.\n"
            )
        seed_interfaces = self.runtime.options.get("seed_interfaces") or {}
        fixed_l3_plan = l3_generation_plan() if level == 3 and not seed_interfaces else None
        if seed_interfaces:
            cpu_requirements += (
                "The I/D caches and predictor come from a verified L2 snapshot. Preserve these "
                "exact ANSI ports in the plan and instantiate the supplied modules. Freeze the new "
                "OoO front end, execution and queues against these interfaces.\n"
                + json.dumps(seed_interfaces, ensure_ascii=False)
                + "\n"
            )
        if level == 3 and fixed_l3_plan is None:
            cpu_requirements += (
                "Rename owns the 40-entry PRF. IQ drives its selected entry's two physical read indices; "
                "LSU independently drives its oldest eligible entry's indices. Both receive their own values. "
                "Do not reuse decode's lookup value for either execution path. Freeze these exact ports: "
                + json.dumps(L3_OPERAND_PORTS) + "\n"
            )
        if fixed_l3_plan is not None:
            cpu_requirements += (
                "IQ emits physical indices to the rename PRF. The PRF values are wired directly to ALU, "
                "while the LSU receives its own independent values. The frozen port list is authoritative.\n"
            )
        system_prompt = PLANNER_SYSTEM_PROMPT
        if fixed_l3_plan is not None:
            system_prompt = (
                "You are the RTL Planner. The supplied L3 interfaces are frozen. Return only "
                '{"notes":"implementation and timing plan"} without any RTL. '
                "Explain how the existing signals implement each accepted transfer and its reset, "
                "hold and squash priorities. Keep notes under 3500 characters."
            )
        prompt = (
            "根据下面的 Spec 规划 RTL 项目的模块与文件结构。每个模块必须能由一个文件独立实现；"
            "所有模块最终会一起用教师 TB 编译验收。每个 module 的 interfaces 必须写全端口名、方向、位宽；"
            "跨模块信号两端必须同宽，端口方向按生产者输出、消费者输入填写。\n"
            + cpu_requirements + skill_prompt
            + f"\nSpec：\n{effective_spec}\n\n"
            + (f"\nFrozen interfaces and architecture:\n{json.dumps(fixed_l3_plan)}\n" if fixed_l3_plan else "")
            + "请只返回约定的 JSON 对象。"
        )
        feedback = ""
        retry_without_thinking = False
        output_limit = min(self.runtime.options["max_tokens"], 12000)
        attempts = {1: 4, 2: 3, 3: 4}.get(level, 2)
        for attempt in range(1, attempts + 1):
            request = prompt + feedback
            event = self.runtime.begin_event("planner", self.node_type, self.title, request, attempt)
            try:
                result = self.runtime.call_model(
                    event, request, system_prompt,
                    model_options={"max_tokens": output_limit, **({"thinking": False} if retry_without_thinking else {})},
                )
                payload = extract_json_payload(result.get("answer") or "")
                if fixed_l3_plan is None:
                    plan = dedupe_plan_ports(validate_plan(payload))
                else:
                    notes = payload.get("notes")
                    if not isinstance(notes, str) or not notes.strip():
                        raise ValueError("Planner 没有返回 L3 实现说明")
                    plan = dict(fixed_l3_plan, notes=f"{fixed_l3_plan['notes']}\n{notes.strip()[:3500]}")
                if level is not None and level >= 1:
                    validate_cpu_plan(plan, level)
                if seed_interfaces:
                    validate_frozen_components(plan, seed_interfaces)
                if level == 3 and fixed_l3_plan is None:
                    validate_l3_operand_ports(plan)
            except ValueError as error:
                truncated = isinstance(error, ModelOutputTruncated)
                message = (
                    f"RTL Planner 输出在本次 {output_limit} token 上限处截断（finish_reason=length）"
                    if truncated else str(error)
                )
                self.finish(event, "failed", error=message)
                if level is None or attempt == attempts or self.runtime.model_calls + 1 >= self.runtime.max_calls:
                    raise ValueError(f"Planner 规划无效：{message}") from error
                if truncated:
                    retry_without_thinking = True
                    feedback = (
                        "\nThe previous answer was truncated. Return compact JSON only. "
                        "Keep every required port, remove repeated explanation, and keep notes short."
                    )
                    continue
                feedback = f"\nCorrect these validation errors and return the complete plan: {error}"
                if "mem_stage: duplicate formal ports" in str(error):
                    feedback += (
                        "\nIn mem_stage.interfaces remove the input declarations of mem_wb_rd and "
                        "mem_wb_reg_write. Keep their existing output declarations; MEM reads the output "
                        "registers internally. Retain mem_wb_fwd_data as a distinct input."
                    )
            else:
                break
        plan["schema_version"] = 1
        if level == 3:
            plan["l3_allocation_tag_contract"] = True
            plan["l3_group_port_contract"] = True
        if level in (1, 2):
            plan["in_order_boundary_contract"] = True
        if skill_result is not None:
            plan["architecture"] = skill_result
        plan_ref = self.runtime.write_json_artifact("plan.json", plan)
        self.save_context(
            spec_ref=spec_ref,
            plan_ref=plan_ref,
            planner_skill_ref=skill_ref,
        )
        self.finish(
            event,
            "info",
            response=json.dumps(plan, ensure_ascii=False, indent=2),
            reasoning=event.get("reasoning"),
            usage=event.get("usage"),
        )
        return self.runtime.write_handoff(
            "planner",
            "coder",
            {
                "artifacts": {
                    "spec": spec_ref,
                    "plan": plan_ref,
                    **({"skill": skill_ref} if skill_ref else {}),
                }
            },
            label="Planner → Coder: Spec / Plan artifacts",
        )
