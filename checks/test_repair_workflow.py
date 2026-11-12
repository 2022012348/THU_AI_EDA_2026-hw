import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from httpx import Request
from openai import APIConnectionError

from ai_eda.agent import orchestrator
from ai_eda.agent.cpu_workflow import (
    IN_ORDER_BOUNDARIES, L3_TAG_PORTS, in_order_boundary_wiring, l3_allocation_wiring, parse_cpu_sources,
    required_modules, validate_cpu_plan, validate_generated_ports,
)
from ai_eda.agent.reporting import report_preview
from ai_eda.agent.projects import get_project
from ai_eda.agent.patches import PatchProtocolError, apply_repair_payload
from ai_eda.agent.skills.cpu_observation import cpu_elaboration_observation, logic_loop_observation
from ai_eda.agent.subagents.base import ModelOutputTruncated, RunContext
from ai_eda.agent.subagents.common import combine_files
from ai_eda.agent.subagents.coder import CoderAgent
from ai_eda.agent.subagents.diagnoser import _observed_diagnosis
from ai_eda.agent.subagents.parser import ParserAgent
from ai_eda.agent.subagents.patch_agent import PatchAgent
from ai_eda.agent.subagents.planner import PlannerAgent
from ai_eda.agent.subagents.repair import (
    diagnostic_repair_progress,
    make_packet,
    may_continue_probe_repair,
    probe_failures,
    repair_verdict,
    require_l3_synthesis,
    verification_receipt,
)
from ai_eda.agent.snapshots import load_snapshot, save_snapshot
from ai_eda.model.client import ModelClient


class RepairWorkflowTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("yosys"), "requires Yosys")
    def test_latch_does_not_hide_undriven_controls_from_diagnosis(self):
        source = """module rv32i5(input logic allow, output logic fetch, output logic held);
logic squash;
assign fetch = allow && !squash;
always_comb begin
    if (allow) held = 1'b1;
end
endmodule
"""
        with tempfile.TemporaryDirectory() as directory:
            rtl = Path(directory) / "candidate.sv"
            rtl.write_text(source, encoding="utf-8")
            result = logic_loop_observation(rtl)
        self.assertEqual(result["status"], "partial")
        self.assertIn("Latch inferred", result["detail"])
        self.assertIn("rv32i5.squash", result["undriven"])
        self.assertIsNone(probe_failures("logic_loop_observation", {"logic_loop_observation": result}))

    def test_l2_parser_retries_a_truncated_response_with_the_same_spec(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = RunContext(
                run_dir=Path(directory), options={"project": get_project("rv32i5_predict"), "max_tokens": 60000},
                begin_event=lambda *args: {},
                finish_event=lambda event, status, **values: None,
                persist=lambda: None, emit=lambda event: None, check_cancel=lambda: None, request_input=None,
            )
            spec = runtime.write_text_artifact("spec.md", "Complete L2 teaching specification.")
            incoming = runtime.write_handoff("start", "parser", {"artifacts": {"spec": spec}})
            answers = [
                ModelOutputTruncated("length"),
                {"answer": '{"sufficient": true, "missing": [], "questions": [], "spec_addendum": ""}'},
            ]
            with patch.object(runtime, "call_model", side_effect=answers) as model:
                outgoing = ParserAgent(runtime).run(incoming)
            handoff = runtime.read_handoff(outgoing)
            self.assertEqual(handoff["target"], "planner")
            self.assertEqual(model.call_count, 2)
            self.assertEqual(runtime.read_text_artifact(handoff["payload"]["artifacts"]["spec"]),
                             "Complete L2 teaching specification.")

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("yosys"), "requires Icarus and Yosys")
    def test_l3_group_reports_lsu_feedback_before_running_a_probe(self):
        path = "rtl/load_store_unit.sv"
        rtl = """module load_store_unit(input logic pending, output logic cpu_req_valid);
logic launch;
assign launch = pending && !cpu_req_valid;
assign cpu_req_valid = launch;
endmodule
"""
        with patch("ai_eda.agent.subagents.coder.l3_lsu_completion_observation") as probe:
            finding = CoderAgent._l3_group_diagnostics(
                {path: rtl}, editable=[path], final=False, check_module_behavior=True,
            )
        probe.assert_not_called()
        self.assertIn(f"{path}: Yosys detected combinational feedback", finding)
        self.assertIn("assign launch = pending && !cpu_req_valid;", finding)

    def test_compile_progress_keeps_elaboration_after_duplicate_declarations_are_removed(self):
        parse = {"stage": "compile", "stderr": "top.sv:1: error: 'ready' has already been declared in this scope."}
        elaborate = {"stage": "compile", "stderr": "\n".join(
            f"top.sv:{line}: error: Variable 'ready' cannot have multiple drivers." for line in (2, 3)
        )}
        self.assertEqual(repair_verdict(parse, elaborate), (True, "reached_elaboration"))
        self.assertEqual(repair_verdict(elaborate, parse), (False, "parse_failure_reintroduced"))

    def test_coder_stops_a_repeating_identifier_and_closes_the_stream(self):
        closed = []

        def repeats(*args, **kwargs):
            try:
                yield {"type": "answer", "text": "logic fetch" + "_issue_resp" * 20}
                self.fail("repeated output should stop before reading another chunk")
            finally:
                closed.append(True)

        client = SimpleNamespace(stream_chat_detail=repeats, MODEL_NAME="test", max_tokens=100,
                                 thinking=False, reasoning_effort="low", temperature=0)
        with tempfile.TemporaryDirectory() as directory:
            runtime = RunContext(
                run_dir=Path(directory), options={}, begin_event=None, finish_event=None,
                persist=lambda: None, emit=lambda event: None, check_cancel=lambda: None,
                request_input=None, max_calls=2,
            )
            event = {"step": 1, "node_id": "coder"}
            with patch.object(runtime, "make_client", return_value=client):
                with self.assertRaisesRegex(ModelOutputTruncated, "反复拼接"):
                    runtime.call_model(event, "request", "system")
            self.assertEqual(event["finish_reason"], "repetition")
            self.assertEqual(closed, [True])

    def test_l3_synthesis_failure_keeps_the_teacher_result_and_scopes_repair(self):
        report = {
            "passed": True, "teacher_passed": True, "stage": "simulation", "checks": [],
            "metrics": {"status": "failed", "reason":
                        r"ERROR: Latch inferred for signal `\issue_queue.\head_dist' from always_comb"},
        }
        require_l3_synthesis(report)
        self.assertFalse(report["passed"])
        self.assertTrue(report["teacher_passed"])
        packet = make_packet(report, {"rtl/issue_queue.sv": ""}, "report", "manifest")
        diagnosis = _observed_diagnosis(packet["teacher_evidence"], {"rtl/issue_queue.sv": ""}, {})
        self.assertEqual(diagnosis["files"], ["rtl/issue_queue.sv"])
        self.assertEqual(diagnosis["symbols"], ["head_dist"])

    def test_interrupted_stream_retries_without_reusing_partial_answer(self):
        def interrupted(*args, **kwargs):
            yield {"type": "answer", "text": "partial JSON"}
            raise APIConnectionError(request=Request("POST", "http://localhost"))

        def recovered(*args, **kwargs):
            yield {"type": "done", "answer": "complete JSON", "finish_reason": "stop"}

        settings = {"MODEL_NAME": "test", "max_tokens": 10, "thinking": False,
                    "reasoning_effort": "low", "temperature": 0}
        with tempfile.TemporaryDirectory() as directory:
            runtime = RunContext(
                run_dir=Path(directory), options={}, begin_event=None, finish_event=None,
                persist=lambda: None, emit=lambda event: None, check_cancel=lambda: None,
                request_input=None, max_calls=2,
            )
            clients = [SimpleNamespace(stream_chat_detail=stream, **settings)
                       for stream in (interrupted, recovered)]
            event = {"step": 1, "node_id": "patch"}
            with patch.object(runtime, "make_client", side_effect=clients):
                result = runtime.call_model(event, "request", "system")
            self.assertEqual(result["answer"], "complete JSON")
            self.assertEqual(event["connection_retries"], 1)
            self.assertEqual(runtime.model_calls, 2)
            self.assertEqual((Path(directory) / "step-01-patch/raw_response.txt").read_text(), "complete JSON")

    def test_l2_planner_retries_when_thinking_exhausts_the_output_budget(self):
        modules = []
        for name in required_modules(2):
            ports = ["input logic clk"]
            if name in IN_ORDER_BOUNDARIES:
                prefix, controls = IN_ORDER_BOUNDARIES[name]
                ports.extend(f"input logic {port}" for port in ("rst_n", *controls))
                ports.extend(f"output logic {prefix}_{field}" for field in ("valid", "pc", "insn"))
            modules.append({"name": name, "file": f"rtl/{name}.sv", "interfaces": "; ".join(ports)})
        plan = json.dumps({"top_module": "rv32i5", "modules": modules})
        duplicate_plan = json.loads(plan)
        mem = next(module for module in duplicate_plan["modules"] if module["name"] == "mem_stage")
        mem["interfaces"] += "; output logic [4:0] mem_wb_rd; input logic [4:0] mem_wb_rd"
        requests = []

        def completion(**request):
            requests.append(request)
            first = len(requests) == 1
            answer = None if first else json.dumps(duplicate_plan) if len(requests) == 2 else plan
            delta = SimpleNamespace(content=answer, reasoning_content="reasoning" if first else None)
            return [SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason="length" if first else "stop")])]

        events = []
        with tempfile.TemporaryDirectory() as directory:
            def begin(node, node_type, title, prompt, attempt):
                event = {"step": len(events) + 1, "node_id": node}
                events.append(event)
                return event

            def make_client(overrides):
                client = object.__new__(ModelClient)
                client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=completion)))
                client.thinking = overrides.get("thinking", True)
                client.reasoning_effort = "low"
                client.temperature = 0.2
                client.max_tokens = overrides["max_tokens"]
                return client

            runtime = RunContext(
                run_dir=Path(directory), options={"project": get_project("rv32i5_predict"), "max_tokens": 60000},
                begin_event=begin, finish_event=lambda event, status, **values: event.update(status=status, **values),
                persist=lambda: None, emit=lambda event: None, check_cancel=lambda: None, request_input=None,
            )
            spec = runtime.write_text_artifact("spec.md", "L2 teaching specification")
            incoming = runtime.write_handoff("parser", "planner", {"artifacts": {"spec": spec}})
            with patch.object(runtime, "make_client", side_effect=make_client):
                handoff = PlannerAgent(runtime).run(incoming)
            self.assertEqual(runtime.read_handoff(handoff)["target"], "coder")

        self.assertEqual([r["extra_body"]["thinking"]["type"] for r in requests], ["enabled", "disabled", "disabled"])
        self.assertEqual([r["max_tokens"] for r in requests], [12000, 12000, 12000])
        self.assertEqual(events[0]["finish_reason"], "length")
        self.assertIn("12000", events[0]["error"])
        self.assertIn("duplicate formal ports", events[1]["error"])

    def test_l3_group_preserves_ports_and_reports_a_real_width_change(self):
        path = "rtl/rename_unit.sv"
        group = [{"name": "rename_unit", "file": path, "interfaces":
                  "input logic [5:0] index_a; input logic [5:0] index_b; output logic [31:0] value"}]
        rtl = "module rename_unit(input wire [5:0] index_a, index_b, output logic [31:0] value); endmodule"
        validate_generated_ports({path: rtl}, group)
        with self.assertRaisesRegex(ValueError, "ports differ.*value"):
            validate_generated_ports({path: rtl.replace("[31:0]", "[5:0]")}, group)

    def test_l3_issue_event_diagnosis_uses_the_accepted_lsu_handshake(self):
        top = "rtl/rv32i5.sv"
        files = {top: """
            load_store_unit u_lsu(.cpu_req_valid(memory_valid), .cpu_req_ready(memory_ready));
            always @(posedge clk) begin
                if (!rst_n) dbg_ooo_issue <= 0;
                else dbg_ooo_issue <= issue_fire;
            end
        """}
        trace = {"count": 1, "first": [{"cycle": "10"}]}
        evidence = {
            "checks": {"groups": [{"name": "ooo_load_overlap_rename", "detail": "missing issue evidence"}]},
            "pipeline_observation": {"l3_accepted_transfers": {"lsu_issue_at_edge": trace}},
        }
        diagnosis = _observed_diagnosis(evidence, files, {})
        self.assertEqual(diagnosis["files"], [top])
        self.assertIn("memory_valid && memory_ready", diagnosis["constraints"][0])
        trace["count"] = 0
        self.assertIsNone(_observed_diagnosis(evidence, files, {}))

    def test_coordinated_cpu_generation_requires_each_planned_file(self):
        modules = [
            {"name": name, "file": f"rtl/{name}.sv"}
            for name in required_modules(3)
        ]
        plan = {"modules": modules}
        sources = "".join(
            f"// file: {item['file']}\nmodule {item['name']};\nendmodule\n"
            for item in reversed(modules)
        )
        with tempfile.TemporaryDirectory() as directory:
            parsed = parse_cpu_sources(sources, plan, Path(directory))
            self.assertEqual(list(parsed), [item["file"] for item in modules])
            with self.assertRaisesRegex(ValueError, "未返回这些文件"):
                parse_cpu_sources(sources[sources.index("// file:", 1):], plan, Path(directory))

    def test_plan_rejects_same_formal_as_input_and_output(self):
        modules = [
            {"name": name, "file": f"rtl/{name}.sv", "interfaces": "input logic clk"}
            for name in required_modules(1)
        ]
        plan = {"top_module": "rv32i5", "modules": modules}
        with self.assertRaisesRegex(ValueError, "registered id_ex"):
            validate_cpu_plan(plan, 1)
        for module in modules:
            if module["name"] in IN_ORDER_BOUNDARIES:
                prefix, controls = IN_ORDER_BOUNDARIES[module["name"]]
                module["interfaces"] += "; input logic rst_n"
                for control in controls:
                    module["interfaces"] += f"; input logic {control}"
                for signal in ("valid", "pc", "insn"):
                    module["interfaces"] += f"; output logic {prefix}_{signal}"
        validate_cpu_plan(plan, 1)
        modules[3]["interfaces"] += "; input logic [31:0] result; output logic [31:0] result"
        with self.assertRaisesRegex(ValueError, "ex_stage: duplicate formal ports result"):
            validate_cpu_plan(plan, 1)

    def test_in_order_pipeline_outputs_are_clocked_and_controls_connected(self):
        files = {}
        instances = []
        for name, (prefix, controls) in IN_ORDER_BOUNDARIES.items():
            updates = "\n".join(f"{prefix}_{field} <= '0;" for field in ("valid", "pc", "insn"))
            files[f"rtl/{name}.sv"] = f"module {name}; always_ff @(posedge clk) begin\n{updates}\nend endmodule"
            ports = ", ".join(f".{pin}({pin})" for pin in controls)
            instances.append(f"{name} u_{name}({ports});")
        top = "rtl/rv32i5.sv"
        files[top] = "module rv32i5; " + " ".join(instances) + " endmodule"
        in_order_boundary_wiring(files)
        files["rtl/id_stage.sv"] = files["rtl/id_stage.sv"].replace("id_ex_valid <= '0;", "state_valid <= '0;")
        files["rtl/id_stage.sv"] = files["rtl/id_stage.sv"].replace("always_ff", "assign id_ex_valid = state_valid; always_ff")
        in_order_boundary_wiring(files)
        files["rtl/id_stage.sv"] = files["rtl/id_stage.sv"].replace("id_ex_valid = state_valid", "id_ex_valid = if_id_valid")
        files[top] = files[top].replace(".ex_mem_hold(ex_mem_hold)", ".ex_mem_hold()")
        with self.assertRaisesRegex(ValueError, r"id_stage\.id_ex_valid.*ex_stage\.ex_mem_hold"):
            in_order_boundary_wiring(files)

    def test_run_report_preview_keeps_reference_and_leaves_raw_report_intact(self):
        full = {"passed": False, "checks": [{"name": "bad"}] * 203, "stdout": "X" * 16500}
        preview = report_preview(full, "tester-03/test_report.json")
        self.assertEqual(preview["report_artifact"], "tester-03/test_report.json")
        self.assertEqual(preview["preview_omitted"], {"checks": 3, "stdout": 500})
        self.assertEqual(len(preview["checks"]), 200)
        self.assertEqual(len(full["checks"]), 203)

    def test_l3_plan_and_coder_require_the_allocated_tag(self):
        modules = [
            {"name": name, "file": f"rtl/{name}.sv", "interfaces": "input logic clk"}
            for name in required_modules(3)
        ]
        plan = {"top_module": "rv32i5", "modules": modules}
        with self.assertRaisesRegex(ValueError, "reorder_buffer: declare the allocation tag port"):
            validate_cpu_plan(plan, 3)
        for module in modules:
            if module["name"] in L3_TAG_PORTS:
                module["interfaces"] += "; " + L3_TAG_PORTS[module["name"]]
        validate_cpu_plan(plan, 3)
        rename = next(module for module in modules if module["name"] == "rename_unit")
        rename["interfaces"] += "; output logic [2:0] alloc_rob_idx"
        with self.assertRaisesRegex(ValueError, "alloc_rob_idx is owned by reorder_buffer"):
            validate_cpu_plan(plan, 3)
        files = {
            f"rtl/{name}.sv": f"module {name}({declaration}); endmodule"
            for name, declaration in L3_TAG_PORTS.items()
        }
        top_path = "rtl/rv32i5.sv"
        files[top_path] = (
            "module rv32i5; wire [2:0] tag; "
            "reorder_buffer rob(.alloc_rob_idx(tag)); "
            "issue_queue iq(.disp_rob_idx(tag)); "
            "load_store_unit lsu(.disp_rob_idx(tag)); endmodule"
        )
        l3_allocation_wiring(files)
        files[top_path] = files[top_path].replace(
            "issue_queue iq(.disp_rob_idx(tag));",
            "wire [2:0] iq_tag; assign iq_tag = tag; issue_queue iq(.disp_rob_idx(iq_tag));",
        )
        l3_allocation_wiring(files)
        files[top_path] = files[top_path].replace("assign iq_tag = tag;", "assign iq_tag = other;")
        with self.assertRaisesRegex(ValueError, "iq_tag <- other"):
            l3_allocation_wiring(files)
        files[top_path] = files[top_path].replace("assign iq_tag = other;", "assign iq_tag = tag;")
        files[top_path] = files[top_path].replace("lsu(.disp_rob_idx(tag))", "lsu(.disp_rob_idx(3'd0))")
        with self.assertRaisesRegex(ValueError, "load_store_unit.disp_rob_idx must be bound"):
            l3_allocation_wiring(files)
        files[top_path] = files[top_path].replace(
            "load_store_unit lsu(.disp_rob_idx(3'd0));",
            "wire [2:0] rename_tag; "
            "rename_unit rename(.alloc_rob_idx(tag), .disp_rob_idx(rename_tag)); "
            "load_store_unit lsu(.disp_rob_idx(rename_tag));",
        )
        files["rtl/rename_unit.sv"] = (
            "module rename_unit(input logic [2:0] alloc_rob_idx, output logic [2:0] disp_rob_idx); "
            "assign disp_rob_idx = alloc_rob_idx; endmodule"
        )
        l3_allocation_wiring(files)

    def test_cpu_patch_can_restore_a_planned_internal_port(self):
        module = {"name": "reorder_buffer", "interfaces": "output logic [2:0] alloc_rob_idx"}
        before = {"reorder_buffer": "()"}
        restored = {"reorder_buffer": "(outputlogic[2:0]alloc_rob_idx)"}
        self.assertEqual(
            PatchAgent._simulation_interface_changes(
                "rtl/reorder_buffer.sv", before, restored, module, cpu_simulation=True,
            ),
            {"reorder_buffer"},
        )
        with self.assertRaisesRegex(PatchProtocolError, "Planner"):
            PatchAgent._simulation_interface_changes(
                "rtl/reorder_buffer.sv", before, {"reorder_buffer": "(outputlogicunplanned)"},
                module, cpu_simulation=True,
            )
        with self.assertRaisesRegex(PatchProtocolError, "受保护"):
            PatchAgent._simulation_interface_changes(
                "rtl/rv32i5.sv", {"rv32i5": "()"}, {"rv32i5": "(inputlogicclk)"},
                {"name": "rv32i5", "interfaces": "input logic clk"}, cpu_simulation=True,
            )

    def test_snapshot_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = RunContext(
                run_dir=Path(directory), options={}, begin_event=None, finish_event=None,
                persist=None, emit=None, check_cancel=None, request_input=None,
            )
            ref = save_snapshot(runtime, {"rtl/top.sv": "module top; endmodule\n"}, 1)
            handoff = runtime.write_handoff("coder", "tester", {"artifacts": {"rtl_manifest": ref}})
            runtime.read_handoff(handoff)
            entry = runtime.read_json_artifact(ref)["files"][0]
            (runtime.run_dir / entry["artifact"]).write_text("module top; wire q; endmodule\n")
            with self.assertRaisesRegex(ValueError, "哈希"):
                load_snapshot(runtime, ref)

    def test_interrupted_resume_restores_measured_candidate_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            old_dir = Path(directory) / "interrupted"
            new_dir = Path(directory) / "resumed"
            callbacks = dict(begin_event=None, finish_event=None, persist=None, emit=None,
                             check_cancel=None, request_input=None)
            project = {"id": "rv32i5_ooo"}
            old = RunContext(run_dir=old_dir, options={"project": project}, **callbacks)
            old_report = {
                "stage": "simulation", "passed": False, "checks": [{"name": "load", "detail": "wrong value"}],
                "logic_loop_observation": {"status": "observed", "loops": [{"locations": ["rtl/top.sv:1"]}]},
            }
            repaired_report = {
                "stage": "simulation", "passed": False, "checks": [
                    {"name": "load", "detail": "等待 halted 超时"}, {"name": "load", "detail": "wrong value"},
                ],
                "logic_loop_observation": {"status": "observed", "loops": []},
                "repair_verification": {"probe": "execution_observation",
                                        "probe_failure_counts": {"logic_loop_observation": {"before": 1, "after": 0}}},
            }
            later_report = dict(repaired_report, checks=repaired_report["checks"][:1], repair_verification={
                "probe": "logic_loop_observation", "probe_failure_counts": {"logic_loop_observation": {"before": 0, "after": 0}},
            })
            refs = []
            for attempt, report in enumerate((old_report, repaired_report, later_report), 1):
                ref = save_snapshot(old, {"rtl/top.sv": f"snapshot {attempt}"}, attempt)
                refs.append(ref)
                report_ref = old.write_json_artifact(f"report{attempt}.json", report)
                old.write_handoff("tester", "diagnoser", {
                    "artifacts": {"rtl_manifest": ref, "report": report_ref},
                }, attempt=attempt)
            baseline, diagnostic = orchestrator._tested_resume_checkpoints(old_dir, project["id"])
            self.assertEqual((baseline["manifest"], diagnostic["manifest"]), (refs[0], refs[2]))
            with self.assertRaisesRegex(ValueError, "目标项目不一致"):
                orchestrator._tested_resume_checkpoints(old_dir, "rv32i5_cache")

            new = RunContext(run_dir=new_dir, options={"project": project}, **callbacks)
            source = {"source": old_dir, "run_id": old_dir.name, "project_files": load_snapshot(old, refs[0])}
            spec = new.write_text_artifact("spec.md", "spec")
            plan = new.write_json_artifact("plan.json", {})
            seed = orchestrator._seed_resume_project(new, source, spec, plan)
            packet = new.write_json_artifact("packet.json", {"teacher_evidence": old_report})
            baseline_artifacts = {"spec": spec, "plan": plan, "rtl_manifest": seed["manifest"], "failure_packet": packet}
            handoff = orchestrator._resume_diagnostic_input(new, source, baseline_artifacts, old_report, diagnostic)
            refs = new.read_handoff(handoff)["payload"]["artifacts"]
            self.assertEqual(load_snapshot(new, refs["rtl_manifest"]), {"rtl/top.sv": "snapshot 3"})
            self.assertEqual(new.read_json_artifact(refs["repair_brief"])["verification_probe"], "logic_loop_observation")
            self.assertEqual((new_dir / "project/rtl/top.sv").read_text(), "snapshot 1")
            patched = save_snapshot(new, {"rtl/top.sv": "next patch"}, 2, refs["rtl_manifest"])
            self.assertNotEqual(patched, refs["rtl_manifest"])
            new.read_handoff(handoff)
            self.assertEqual(load_snapshot(new, refs["rtl_manifest"]), {"rtl/top.sv": "snapshot 3"})

    def test_partial_probe_improvement_can_be_investigated_without_promotion(self):
        before = {"stage": "simulation", "checks": [{"name": "loads", "detail": "wrong value"}]}
        after = {
            "stage": "simulation", "checks": [{"name": "branches", "detail": "等待 halted 超时"}],
            "repair_verification": {
                "before_failures": ["ordered", "register", "exit"],
                "after_failures": ["retired", "ordered"],
            },
        }
        self.assertTrue(may_continue_probe_repair(after))
        self.assertEqual(repair_verdict(before, after), (False, "additional_halt_timeouts"))
        after["repair_verification"]["probe_failure_counts"] = {
            "execution_observation": {"before": 3, "after": 2},
            "retirement_observation": {"before": 0, "after": 1},
        }
        self.assertFalse(may_continue_probe_repair(after))

    def test_no_fetch_diagnosis_requires_an_impossible_handshake_in_the_source(self):
        observation = {
            "status": "observed", "fields": ["fetch_accept_at_edge"],
            "fetch_transfer": {"accepted": False, "response_seen": False},
            "instance_connections": {
                "if_stage.icache_cpu_req_valid": "request_valid",
                "if_stage.icache_cpu_req_ready": "request_ready",
            },
        }
        evidence = {"stage": "simulation", "pipeline_observation": observation}
        probes = {"retirement_observation": ["first_pc_instruction"]}
        path = "rtl/if_stage.sv"
        broken = "assign icache_cpu_req_valid = rst_n & ~outstanding & ~icache_cpu_req_ready;"
        valid = "assign icache_cpu_req_valid = rst_n & ~outstanding;"
        diagnosis = _observed_diagnosis(evidence, {path: broken}, probes)
        self.assertEqual(diagnosis["files"], [path])
        self.assertEqual(diagnosis["verification_probe"], "pipeline_observation")
        self.assertIsNone(_observed_diagnosis(evidence, {path: valid}, probes))
        self.assertIsNone(_observed_diagnosis(evidence, {path: "// " + broken}, probes))
        after = dict(observation, fetch_transfer={"accepted": True, "response_seen": False})
        receipt = verification_receipt(
            dict(diagnosis, rtl_manifest="baseline"), {"teacher_evidence": evidence},
            {"stage": "simulation", "pipeline_observation": after}, "candidate",
        )
        self.assertEqual(receipt["before_failures"], ["accepted", "response_seen"])
        self.assertEqual(receipt["after_failures"], ["response_seen"])
        self.assertFalse(receipt["resolved"])
        self.assertTrue(may_continue_probe_repair({"stage": "simulation", "repair_verification": receipt}))

    def test_cold_fetch_repair_can_expose_a_second_blocker(self):
        before = {
            "stage": "simulation",
            "retirement_observation": {"samples": ["RETIREMENT|retired=0|ecalls=0"]},
        }
        after = {
            "stage": "simulation",
            "timed_out": True,
            "pipeline_observation": {"fetch_transfer": {"accepted": True, "response_seen": True}},
            "repair_verification": {
                "probe": "icache_word_observation", "before_failures": ["0", "4", "8", "c"],
                "after_failures": [],
            },
        }
        self.assertEqual(repair_verdict(before, after), (False, "lost_simulation_coverage"))
        self.assertTrue(may_continue_probe_repair(after, before))
        after["icache_word_observation"] = {
            "status": "observed", "samples": [f"WORD|address={i}|match=1" for i in range(4)],
        }
        rolled_back = dict(before, icache_word_observation={
            "status": "observed", "samples": [f"WORD|address={i}|match=0" for i in range(4)],
        })
        self.assertEqual(repair_verdict(after, rolled_back), (True, "completed_teacher_simulation"))
        self.assertFalse(diagnostic_repair_progress(rolled_back, before, after))
        before["test_suites"] = [{"name": "base", "passed": True}]
        self.assertFalse(may_continue_probe_repair(after, before))

    def test_verified_refill_can_be_kept_for_diagnosis_without_promoting_it(self):
        before = {
            "stage": "simulation", "checks": [{"name": "old", "detail": "wrong value"}] * 3,
            "test_suites": [{"name": "base", "passed": False}],
        }
        after = {
            "stage": "simulation", "checks": [{"name": "new", "detail": "等待 halted 超时"}],
            "test_suites": [{"name": "base", "passed": False}],
            "repair_verification": {
                "probe": "icache_word_observation", "before_failures": ["0", "4", "8", "c"],
                "after_failures": [], "resolved": True,
                "probe_failure_counts": {
                    "icache_word_observation": {"before": 4, "after": 0},
                    "execution_observation": {"before": 3, "after": 7},
                    "logic_loop_observation": {"before": 0, "after": 0},
                },
            },
        }
        self.assertEqual(repair_verdict(before, after), (False, "additional_halt_timeouts"))
        self.assertTrue(may_continue_probe_repair(after, before))
        before["test_suites"][0]["passed"] = True
        self.assertFalse(may_continue_probe_repair(after, before))

    def test_branch_loop_diagnosis_checks_the_actual_target_driver(self):
        top = "rtl/rv32i5.sv"
        files = {
            top: ("module rv32i5; hazard_unit hazard(.redirect_pc(redirect_pc)); "
                  "ex_stage execute(.ex_target(ex_target)); if_stage fetch(.redirect_pc(redirect_pc)); endmodule"),
            "rtl/hazard_unit.sv": "assign redirect_pc = 32'h0000_0000;",
        }
        probes = {"l1_control_observation": ["halted"]}
        diagnosis = _observed_diagnosis({}, files, probes)
        self.assertEqual(diagnosis["verification_probe"], "l1_control_observation")
        self.assertEqual(diagnosis["files"], [top])
        files[top] = files[top].replace("fetch(.redirect_pc(redirect_pc))", "fetch(.redirect_pc(ex_target))")
        self.assertIsNone(_observed_diagnosis({}, files, probes))

    def test_complete_file_patch_ignores_unchanged_allowed_file(self):
        first = {"path": "rtl/hazard.sv", "content": "original hazard"}
        second = {"path": "rtl/top.sv", "content": "changed top"}
        files = {first["path"]: first["content"], second["path"]: "original top"}
        applied = apply_repair_payload({"schema_version": 1, "files": [first, second]}, files)
        self.assertEqual(applied.changed_files, {second["path"]: second["content"]})
        self.assertEqual(applied.manifest["changed_paths"], [second["path"]])
        with self.assertRaisesRegex(PatchProtocolError, "makes no changes"):
            apply_repair_payload({"schema_version": 1, "files": [first]}, files)

    @unittest.skipUnless(shutil.which("iverilog"), "requires iverilog")
    def test_successful_elaboration_passes_refill_width_warning_to_diagnoser(self):
        files = {
            "rtl/rv32i5.sv": "module rv32i5; wire unused; icache ic(unused); endmodule",
            "rtl/icache.sv": (
                "module icache(output wire mem_req_valid);\n"
                "wire busy = 1'b1;\nwire [1:0] count = 2'd0;\n"
                "assign mem_req_valid = busy & (count < 2'd4);\nendmodule"
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            combined = Path(directory) / "generated_attempt1.sv"
            combined.write_text(combine_files(files))
            floating, warnings = cpu_elaboration_observation(combined)
        self.assertEqual(floating, [])
        report = {"stage": "simulation", "width_warnings": warnings}
        evidence = make_packet(report, files, "report", "snapshot")["teacher_evidence"]
        self.assertTrue(evidence["width_warnings"][0].startswith("rtl/icache.sv:4:"))
        diagnosis = _observed_diagnosis(evidence, files, {"icache_word_observation": ["00000100"]})
        self.assertEqual(diagnosis["files"], ["rtl/icache.sv"])
        self.assertEqual(diagnosis["verification_probe"], "icache_word_observation")

    def test_repair_rolls_back_new_failures(self):
        old = {"stage": "simulation", "checks": [{"name": "alu", "detail": "mismatch"}]}
        new = {"stage": "compile", "checks": []}
        self.assertEqual(repair_verdict(old, new), (False, "lost_simulation_coverage"))

    def test_repair_values_recovered_coverage_over_raw_counts(self):
        old = {"stage": "simulation", "checks": [{"name": "alu", "detail": "等待 halted 超时"}]}
        new = {"stage": "simulation", "checks": [{"name": "alu", "detail": "mismatch"}] * 2}
        self.assertEqual(repair_verdict(old, new), (True, "more_tests_reached_halt"))

    def test_verification_receipt_names_the_remaining_counterexample(self):
        def observed(first_three_match, last_matches):
            matches = [first_three_match] * 3 + [last_matches]
            return {"status": "observed", "samples": [
                f"WORD|address={index * 4:08x}|match={int(match)}"
                for index, match in enumerate(matches)
            ]}
        target = "icache_word_observation"
        brief = {"verification_probe": target, "rtl_manifest": "baseline",
                 "verification": "return the requested word"}
        packet = {"teacher_evidence": {target: observed(False, True)}}
        report = {target: observed(True, False), "passed": False}
        receipt = verification_receipt(brief, packet, report, "candidate")
        self.assertEqual(receipt["before_failures"], ["00000000", "00000004", "00000008"])
        self.assertEqual(receipt["after_failures"], ["0000000c"])
        self.assertFalse(receipt["resolved"])
        self.assertEqual(receipt["rtl_manifest"], "candidate")

    def test_compile_receipt_reports_transition_to_simulation(self):
        receipt = verification_receipt(
            {"rtl_manifest": "old", "verification": "elaborate"},
            {"teacher_evidence": {"stage": "compile"}},
            {"stage": "simulation", "passed": False},
            "new",
        )
        self.assertTrue(receipt["resolved"])
        self.assertEqual((receipt["baseline_stage"], receipt["candidate_stage"]), ("compile", "simulation"))
        self.assertFalse(receipt["teacher_passed"])

    def test_compile_errors_point_at_original_files(self):
        report = {"stage": "compile", "stderr": "/tmp/generated_attempt1.sv:2: error: syntax"}
        packet = make_packet(report, {"rtl/top.sv": "module top;"}, "report", "snapshot")
        self.assertTrue(packet["teacher_evidence"]["error_excerpt"][0].startswith("rtl/top.sv:1:"))

    @unittest.skipUnless(shutil.which("iverilog"), "Icarus Verilog is unavailable")
    def test_cpu_patch_elaboration_reports_the_changed_source_line(self):
        files = {"rtl/rv32i5.sv": "module rv32i5(output logic value);\nassign value = missing;\nendmodule\n"}
        with self.assertRaisesRegex(PatchProtocolError, r"rtl/rv32i5\.sv:2: .*Unable to bind"):
            PatchAgent._check_cpu_compile(files)

    @unittest.skipUnless(shutil.which("iverilog"), "Icarus Verilog is unavailable")
    def test_restored_internal_input_requires_an_instance_connection(self):
        files = {
            "rtl/rv32i5.sv": "module rv32i5; wire value; stage u_stage(.value(value)); endmodule",
            "rtl/stage.sv": "module stage(input logic ready, output logic value); assign value = ready; endmodule",
        }
        with self.assertRaisesRegex(PatchProtocolError, r"dangling input port .*\(ready\) floating"):
            PatchAgent._check_cpu_compile(files, {"stage"})
        files["rtl/rv32i5.sv"] = files["rtl/rv32i5.sv"].replace(".value(value)", ".ready(1'b1), .value(value)")
        PatchAgent._check_cpu_compile(files, {"stage"})

    @unittest.skipUnless(shutil.which("yosys"), "Yosys is unavailable")
    def test_combinational_loop_evidence_maps_to_rtl_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.sv"
            path.write_text(
                "// file: rtl/rv32i5.sv\n"
                "module rv32i5(output wire [1:0] epoch);\n"
                "assign epoch = epoch + 2'd1;\nendmodule\n"
            )
            result = logic_loop_observation(path)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["loops"][0]["locations"], ["rtl/rv32i5.sv:2"])

    def test_normal_entrypoint_reaches_teacher_with_patched_manifest(self):
        project = get_project("stream_filter")
        module = {"name": "stream_filter", "file": "rtl/stream_filter.sv",
                  "description": "top", "interfaces": "input clk"}
        wrong = "module stream_filter(input clk);\n  wire valid = 1'b0;\nendmodule"
        changed = wrong.replace("1'b0", "1'b1")
        answers = iter([
            {"sufficient": True},
            {"top_module": "stream_filter", "modules": [module]},
            wrong,
            {"stage": "simulation", "hypothesis": "valid held low", "confidence": "high",
             "files": [module["file"]], "verification": "valid asserts"},
            ModelOutputTruncated("truncated patch"),
            {"schema_version": 1, "edits": [
                {"path": module["file"], "expected": "1'b0", "replacement": "1'b1"},
                {"path": module["file"], "expected": "input clk", "replacement": "input clk"},
            ]},
        ])
        seen = []

        def fake_model(runtime, event, prompt, system, **kwargs):
            runtime.model_calls += 1
            event["call_index"] = runtime.model_calls
            event["usage"] = {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
            answer = next(answers)
            if isinstance(answer, ModelOutputTruncated):
                raise answer
            if event["node_id"] == "patch":
                self.assertIn("Return only compact anchored edits", prompt)
                self.assertEqual(json.loads(prompt.split("\nThe response exceeded", 1)[0])["rtl"],
                                 {module["file"]: wrong + "\n"})
            return {"answer": answer if isinstance(answer, str) else json.dumps(answer)}

        def fake_tester(*, rtl_path, **kwargs):
            content = rtl_path.read_text()
            seen.append(content)
            passed = "1'b1" in content
            return {"stage": "simulation", "passed": passed,
                    "checks": [] if passed else [{"name": "valid", "detail": "observed 0"}]}

        with tempfile.TemporaryDirectory() as directory, (
            patch.object(orchestrator, "AGENT_RUNS_ROOT", Path(directory))
        ), patch.object(RunContext, "call_model", fake_model), (
            patch("ai_eda.agent.subagents.tester.test_rtl", fake_tester)
        ):
            payload = orchestrator.run_agent(project_id=project["id"], thinking=False)
            run = Path(directory) / payload["run_id"]
            result = payload["result"]
            summary = json.loads((run / "run_summary.json").read_text())
            self.assertTrue(result["passed"])
            self.assertIn(wrong, seen[0])
            self.assertIn(changed, seen[1])
            self.assertEqual(summary["tokens"]["total_tokens"], 180)
            handoff = json.loads((run / "handoffs/diagnoser_to_patch_attempt1.json").read_text())
            self.assertEqual(
                set(handoff["payload"]["artifacts"]),
                {"spec", "plan", "rtl_manifest", "failure_packet", "repair_brief"},
            )
            self.assertTrue((run / "artifacts/repair_verification_attempt2.json").is_file())
            self.assertTrue((run / result["selected_manifest"]).is_file())


if __name__ == "__main__":
    unittest.main()
