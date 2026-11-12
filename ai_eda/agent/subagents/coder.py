"""Coder 子 Agent：按计划逐文件生成 RTL。"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from ai_eda.model.client import RTL_SYSTEM_PROMPT
from ai_eda.rtl.runner import extract_rtl
from ai_eda.agent.cpu_workflow import (
    CPU_CODING, IN_ORDER_CODING, L1_FETCH_CODING, L2_PREDICTOR, L3_EDGE_CONTRACT,
    L3_FROZEN_INTERFACE_TIMING, OUT_OF_ORDER_CODING,
    in_order_boundary_wiring, l3_allocation_wiring, l3_fetch_response_uses_live_pc,
    l3_iq_dispatch_feedback, l3_load_dispatches_to_iq,
    l3_missing_debug_register_connection, l3_missing_fetch_buffer_guard,
    l3_rename_allocation_feedback, l3_rob_head_feedback, l3_rob_tail_advances_without_allocation,
    parse_cpu_sources, validate_generated_ports,
)
from ai_eda.agent.snapshots import save_snapshot
from ai_eda.agent.skills.cpu_observation import (
    dcache_lane_observation, icache_word_observation, l3_iq_selection_observation, l3_lsu_completion_observation,
    l3_rename_allocation_observation, l3_rob_allocation_observation, logic_loop_observation,
)

from .base import SubAgent
from .common import combine_files, safe_project_path
from .repair import probe_failures


def _architecture_block(plan: dict) -> str:
    architecture = plan.get("architecture")
    if not isinstance(architecture, dict):
        return ""
    parameters = architecture.get("parameters") or {}
    directives = architecture.get("coder_directives") or []
    reference = architecture.get("reference_structure") or {}
    return (
        "\n\nPlanner Skill 预评估架构参数（直接来自 Python Skill，必须严格遵守）：\n"
        + json.dumps(parameters, ensure_ascii=False, indent=2)
        + "\n\nCoder 指令：\n"
        + "\n".join(f"- {item}" for item in directives)
        + "\n\n参考结构：\n"
        + json.dumps(reference, ensure_ascii=False, indent=2)
        + "\n"
    )


class CoderAgent(SubAgent):
    name = "coder"
    title = "03 · Coder"
    node_type = "coder"
    # RTL 生成同样属于工具型输出。L1/L2 的 Plan+Spec 很长，思考模式可能把
    # 60k 输出预算全部消耗在 reasoning 上，导致没有 RTL 文本。默认关闭思考，
    # 把预算留给完整的 SystemVerilog 文件。
    MODEL_OPTIONS = {"thinking": False}

    def run(
        self,
        planner_handoff: Path,
        *,
        existing_files: dict[str, str] | None = None,
        start_module: str | None = None,
    ) -> Path:
        incoming = self.runtime.read_handoff(planner_handoff)["payload"]
        artifacts = incoming.get("artifacts") or {}
        spec_ref = artifacts.get("spec")
        plan_ref = artifacts.get("plan")
        if not isinstance(spec_ref, str) or not isinstance(plan_ref, str):
            raise ValueError("Planner → Coder handoff 缺少 Spec 或 Plan artifact")
        effective_spec = self.runtime.read_text_artifact(spec_ref)
        plan = self.runtime.read_json_artifact(plan_ref)
        if not isinstance(plan, dict):
            raise ValueError("Plan artifact 格式无效")
        level = self.runtime.options["project"].get("expected_cpu_level")
        if level is not None and level >= 1:
            seed_files = self.runtime.options.get("seed_files") or {}
            generation_plan = dict(plan, modules=[m for m in plan["modules"] if m["file"] not in seed_files])
            seed_instruction = (
                "\nThese verified L2 components are already supplied: " + ", ".join(seed_files)
                + ". Instantiate them exactly as planned but emit no RTL for them. Emit every other plan file. "
                  "On the I-cache connect fetch epoch/flush; for the in-order LSU, authorize memory only "
                  "for nonsquashed entries and do not cancel older accepted D-cache work on a frontend redirect.\n"
                if seed_files else ""
            )
            prompt = (
                f"Implement the complete RV32I L{level} candidate.\n"
                + CPU_CODING
                + (OUT_OF_ORDER_CODING + L3_EDGE_CONTRACT if level == 3 else IN_ORDER_CODING)
                + ({1: L1_FETCH_CODING, 2: L2_PREDICTOR}.get(level, ""))
                + seed_instruction
                + f"\nFrozen plan:\n{json.dumps(plan, ensure_ascii=False)}"
                + f"\nFrozen spec:\n{effective_spec}"
            )
            if level == 3:
                if plan.get("interface_origin"):
                    prompt += L3_FROZEN_INTERFACE_TIMING
                return self._generate_l3_groups(spec_ref, plan_ref, plan, prompt, seed_files)
            feedback = ""
            for attempt in (1, 2):
                request = prompt + feedback
                event = self.runtime.begin_event("coder", self.node_type, self.title, request, attempt)
                try:
                    result = self.runtime.call_model(
                        event, request, RTL_SYSTEM_PROMPT, model_options=self.MODEL_OPTIONS,
                    )
                    files = seed_files | parse_cpu_sources(result["answer"], generation_plan, self.runtime.run_dir)
                    if plan.get("in_order_boundary_contract"):
                        in_order_boundary_wiring(files, check_controls=True)
                except ValueError as error:
                    self.finish(event, "failed", error=str(error))
                    if attempt == 2 or self.runtime.model_calls >= self.runtime.max_calls:
                        raise
                    feedback = f"\nRegenerate the complete candidate and fix this validation error: {error}"
                else:
                    break
            reused = f"; reused {len(seed_files)} verified L2 components" if seed_files else ""
            self.finish(event, "info", response=f"Generated {len(files) - len(seed_files)} coordinated RTL files{reused}.")
            return self.write_tester_handoff(spec_ref, plan_ref, files)
        files: dict[str, str] = {}
        existing = existing_files or {}
        modules = plan["modules"]
        start_index = 0
        if start_module:
            for index, module in enumerate(modules):
                if module.get("name") == start_module:
                    start_index = index
                    break
        architecture_block = _architecture_block(plan)
        for index, module in enumerate(modules):
            if index < start_index and module["file"] in existing:
                files[module["file"]] = existing[module["file"]]
                continue
            prompt = (
                f"请实现项目中模块 `{module['name']}`，写入文件 `{module['file']}`。"
                "只输出该文件的完整 SystemVerilog RTL，不要输出 Markdown 围栏或解释。\n\n"
                "项目计划（必须严格遵守 modules[].interfaces 的端口名/方向/位宽；"
                "跨模块信号在该模块中也必须使用同一套名称和宽度，不能自行改名或增删端口）。\n"
                + architecture_block
                + "语法自检：端口列表已经声明的 output/input 信号不要在模块体内再次用 logic/wire 声明；"
                "每个信号只能有一个 driver；不要出现重复声明或多驱动。\n"
                f"{json.dumps(plan, ensure_ascii=False, indent=2)}\n\n"
                f"Spec：\n{effective_spec}\n\n"
                f"本模块说明：{module['description']}\n本模块接口契约：{module['interfaces']}"
            )
            event = self.runtime.begin_event(
                "coder", self.node_type, f"{self.title} · {module['name']}", prompt
            )
            result = self.runtime.call_model(
                event, prompt, RTL_SYSTEM_PROMPT, model_options=self.MODEL_OPTIONS
            )
            rtl = extract_rtl(result.get("answer") or "")
            if not rtl.strip() or "module" not in rtl:
                raise ValueError(f"Coder 没有为 {module['name']} 生成有效 RTL")
            target = safe_project_path(self.runtime.run_dir, module["file"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rtl, encoding="utf-8")
            files[module["file"]] = rtl
            artifact_dir = self.runtime.run_dir / f"step-{event['step']:02d}-{event['node_id']}"
            artifact_dir.mkdir(exist_ok=True)
            (artifact_dir / "generated.sv").write_text(rtl, encoding="utf-8")
            self.save_context(plan_ref=plan_ref, last_file=self.runtime.artifact_ref(target))
            self.finish(
                event,
                "info",
                response=f"Wrote {module['file']} ({len(rtl)} chars).",
                rtl=rtl,
                reasoning=event.get("reasoning"),
                usage=event.get("usage"),
            )
        return self.write_tester_handoff(spec_ref, plan_ref, files)

    def _generate_l3_groups(
        self, spec_ref: str, plan_ref: str, plan: dict, prompt: str, seed_files: dict[str, str]
    ) -> Path:
        files = dict(seed_files)
        top_path = "rtl/rv32i5.sv"
        blocks = (
            {"icache", "dcache", "branch_predictor"},
            {"rename_unit", "reorder_buffer"},
            {"issue_queue", "load_store_unit", "alu", "decode"},
        )
        submodules = set().union(*blocks)
        planned = [m for m in plan["modules"] if m["file"] not in seed_files]
        groups = (
            *([m for m in planned if m["name"] in block] for block in blocks),
            [m for m in planned if m["name"] not in submodules],
        )
        for group in groups:
            if not group:
                continue
            paths = ", ".join(m["file"] for m in group)
            group_rules = (
                "Other planned files are implemented by separate calls using the same frozen port contract. "
                "In this call honor the exact planned direction, width and spelling of every port. "
                "Use each IQ/LSU read index from the corresponding resident queue entry. "
                "Both loads and stores go to LSU; neither can complete through the integer lane. "
                "Wire each ready output to the consumer's actual ready input and qualify fetch consumption, "
                "rename, ROB allocation and the selected queue with the same accepted edge. "
                "Use actual branch direction/target and derive capability events from accepted operations. "
                "Use short signal names and connect existing compatible signals directly. "
                "Do not add or remove formal ports.\n"
            )
            feedback = ""
            generated = None
            requested = group
            frozen_interfaces = bool(plan.get("interface_origin"))
            attempt_limit = 3 if frozen_interfaces else 2
            for attempt in range(1, attempt_limit + 1):
                requested_paths = ", ".join(m["file"] for m in requested)
                request = prompt + f"\nEmit ONLY these planned files: {requested_paths}. " + group_rules + feedback
                event = self.runtime.begin_event("coder", self.node_type, f"{self.title} · {requested_paths}", request, attempt)
                try:
                    result = self.runtime.call_model(
                        event, request, RTL_SYSTEM_PROMPT,
                        model_options={**self.MODEL_OPTIONS, "max_tokens": min(self.runtime.options["max_tokens"], 16000)},
                    )
                    revisions = parse_cpu_sources(result["answer"], {"modules": requested}, self.runtime.run_dir)
                    if plan.get("l3_group_port_contract"):
                        validate_generated_ports(revisions, requested)
                    candidate = (generated or {}) | revisions
                    if top_path in candidate:
                        l3_allocation_wiring(files | candidate)
                except ValueError as error:
                    self.finish(event, "failed", error=str(error))
                    if attempt == attempt_limit or self.runtime.model_calls >= self.runtime.max_calls:
                        if generated is not None:
                            break
                        raise
                    feedback += f"\nCorrect this validation error: {error}. Return the complete requested files only."
                else:
                    generated = candidate
                    diagnostics = self._l3_group_diagnostics(
                        files | candidate if top_path in candidate else candidate,
                        editable=candidate.keys(), final=top_path in candidate,
                        check_module_behavior=frozen_interfaces,
                    )
                    if diagnostics:
                        self.finish(event, "failed", error=diagnostics)
                        if attempt == attempt_limit or self.runtime.model_calls >= self.runtime.max_calls:
                            break
                        affected = {line.split(":", 1)[0] for line in diagnostics.splitlines()}
                        requested = [module for module in group if module["file"] in affected]
                        feedback = (
                            "\nFix these measured diagnostics while keeping the frozen ports and functioning logic. "
                            "Return the complete requested files only.\n" + diagnostics + "\nCurrent sources:\n"
                            + "\n".join(f"// file: {module['file']}\n{generated[module['file']]}" for module in requested)
                        )
                        continue
                    self.finish(event, "info", response=f"Generated {len(generated)} files for {paths}.")
                    break
            files.update(generated)
        return self.write_tester_handoff(spec_ref, plan_ref, files)

    @staticmethod
    def _l3_group_diagnostics(
        files: dict[str, str], *, editable, final: bool, check_module_behavior: bool = False,
    ) -> str:
        with tempfile.TemporaryDirectory(prefix="eda-l3-coder-") as directory:
            for relative, source in files.items():
                path = Path(directory) / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
            scope = ["-s", "rv32i5"] if final else ["-i"]
            try:
                result = subprocess.run(
                    ["iverilog", "-g2012", "-Wall", "-tnull", *scope, *files],
                    cwd=directory, capture_output=True, text=True, timeout=10,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return ""
            findings = [
                line.strip() for line in result.stderr.splitlines()
                if line.split(":", 1)[0] in editable
                and re.search(r"syntax error|error:|Numeric constant truncated|dangling input port", line, re.I)
            ]
            packed_index_paths = list(dict.fromkeys(
                line.split(":", 1)[0] for line in findings if "Array index expressions must be constant" in line
            ))
            findings = [
                f"{path}: use unpacked line and word dimensions, e.g. logic [31:0] words [0:15][0:3]. "
                "Select a whole word first and merge bytes in a 32-bit temporary."
                for path in packed_index_paths
            ] + findings
            for index, finding in enumerate(findings):
                location = re.match(r"(rtl/\w+\.sv):(\d+):", finding)
                if location:
                    path, line = location[1], int(location[2])
                    source_lines = files[path].splitlines()
                    if 1 <= line <= len(source_lines):
                        source_line = source_lines[line - 1].strip()
                        hint = "Widen the literal." if "Numeric constant truncated" in finding else ""
                        if "syntax error" in finding.lower() and re.search(r"\bdist\b", source_line):
                            hint = "dist is a SystemVerilog keyword. Rename that identifier everywhere in this module."
                        findings[index] = f"{finding} Source: {source_line[:200]} {hint}".rstrip()
            rob = "rtl/reorder_buffer.sv"
            if rob in editable and l3_rob_head_feedback(files[rob]):
                findings.append(
                    f"{rob}: current head reads its own next-state result. "
                    "Use registered state for current head, and index all commit outputs from that head."
                )
            if rob in editable and l3_rob_tail_advances_without_allocation(files[rob]):
                findings.append(f"{rob}: advance tail only on alloc_fire. Preserve reset and recovery.")
            rename = "rtl/rename_unit.sv"
            if rename in editable and l3_rename_allocation_feedback(files[rename]):
                findings.append(
                    f"{rename}: allocation readiness depends on the free head after that same allocation. "
                    "Use registered free head and tail for readiness, allowing the last free entry to be used."
                )
            top = "rtl/rv32i5.sv"
            if top in editable and l3_iq_dispatch_feedback(files[top]):
                findings.append(f"{top}: dispatch must not wait for issue_valid in the empty IQ. Use the free ROB tag.")
            if top in editable and l3_load_dispatches_to_iq(files[top]):
                findings.append(
                    f"{top}: d_load enters IQ and only d_store enters LSU. "
                    "Use d_load || d_store for LSU readiness and enqueue, and exclude both from IQ."
                )
            if top in editable and l3_fetch_response_uses_live_pc(files[top]):
                findings.append(
                    f"{top}: fetch_pc advances when the request is accepted, but the later response copies it. "
                    "Save the accepted PC, prediction and epoch in an outstanding slot and use them on response."
                )
            if top in editable and l3_missing_debug_register_connection(files[top]):
                findings.append(f"{top}: connect Rename dbg_data directly to dbg_reg_data and remove the constant driver.")
            if top in editable and l3_missing_fetch_buffer_guard(files[top]):
                findings.append(
                    f"{top}: fetch can start while its one response buffer is occupied. "
                    "Require an empty buffer as well as no outstanding request."
                )
            if final and result.returncode == 0:
                combined = Path(directory) / "candidate.sv"
                combined.write_text(combine_files(files), encoding="utf-8")
                structural = logic_loop_observation(combined)
                for net in structural.get("undriven", ()):
                    if re.fullmatch(r"rv32i5\.\w+", net):
                        findings.append(f"{top}: {net} has no driver. Connect its producer to every consumer.")
            if check_module_behavior and result.returncode == 0:
                module_probes = {
                    "rtl/icache.sv": icache_word_observation,
                    "rtl/dcache.sv": dcache_lane_observation,
                    rename: l3_rename_allocation_observation,
                    rob: l3_rob_allocation_observation,
                    "rtl/load_store_unit.sv": l3_lsu_completion_observation,
                    "rtl/issue_queue.sv": l3_iq_selection_observation,
                }
                for path in editable:
                    if not final:
                        structural = logic_loop_observation(Path(directory) / path, top_module=Path(path).stem)
                        detail = structural.get("detail", "")
                        if "ERROR: Latch inferred for signal" in detail:
                            findings.append(
                                f"{path}: {detail.strip()} Default the named combinational temporary and "
                                "others in the same block before the first condition or loop."
                            )
                            continue
                        loops = structural.get("loops", ())
                        if loops:
                            source = files[path].splitlines()
                            lines = list(dict.fromkeys(
                                int(match) for match in re.findall(r"candidate\.sv:(\d+)", loops[0]["cells"])
                            ))
                            assignments = " ".join(
                                f"line {line}: {source[line - 1].strip()}"
                                for line in lines if 1 <= line <= len(source)
                            )
                            findings.append(f"{path}: Yosys detected combinational feedback. {assignments}")
                            continue
                    observe = module_probes.get(path)
                    if observe is None:
                        continue
                    observation = observe(Path(directory) / path)
                    failures = probe_failures(observe.__name__, {observe.__name__: observation})
                    if observation["status"] == "timeout":
                        findings.append(f"{path}: module probe timed out. {observation['experiment']}")
                    if failures:
                        failed_samples = [sample for sample in observation["samples"]
                                          if sample.endswith("|match=0") or "|refill_match=0|" in sample]
                        findings.append(f"{path}: {observation['experiment']} Failed: {', '.join(failures)}")
                        findings.extend(failed_samples[:2])
        return "\n".join(findings[:8])

    def write_tester_handoff(
        self, spec_ref: str, plan_ref: str, files: dict[str, str], *, label: str = "Coder → Tester: RTL manifest",
    ) -> Path:
        for path, content in files.items():
            target = safe_project_path(self.runtime.run_dir, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        manifest_ref = save_snapshot(self.runtime, files, 1)
        self.save_context(plan_ref=plan_ref, rtl_manifest_ref=manifest_ref)
        return self.runtime.write_handoff(
            "coder",
            "tester",
            {
                "artifacts": {
                    "spec": spec_ref,
                    "plan": plan_ref,
                    "rtl_manifest": manifest_ref,
                },
            },
            label=label,
        )
