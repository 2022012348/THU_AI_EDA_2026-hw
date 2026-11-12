"""Tester 子 Agent：编译项目、运行教师 TB、评估面积 / 功耗。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from ai_eda.agent.projects import get_project
from ai_eda.agent.reporting import report_preview
from ai_eda.agent.snapshots import load_snapshot
from ai_eda.agent.skills.cpu_observation import (
    cpu_elaboration_observation,
    dcache_lane_observation,
    execution_observation,
    l3_fetch_backpressure_observation,
    l3_link_recovery_observation,
    l3_iq_selection_observation,
    l3_lsu_completion_observation,
    l3_memory_queue_observation,
    l3_rename_reuse_observation,
    l3_rename_allocation_observation,
    l3_store_writeback_observation,
    icache_word_observation,
    l1_control_observation,
    l1_fetch_wait_observation,
    l1_halt_observation,
    l1_halt_wait_observation,
    l2_branch_direction_observation,
    l2_predictor_table_observation,
    l2_recovery_observation,
    l1_redirect_observation,
    logic_loop_observation,
    pipeline_observation,
    retirement_observation,
)
from ai_eda.rtl.runner import save_report, test_rtl

from .base import SubAgent
from .common import combine_files, report_feedback, report_summary
from .repair import make_packet, probe_failures, require_l3_synthesis, verification_receipt



class TesterAgent(SubAgent):
    name = "tester"
    title = "04 · Tester"
    node_type = "tester"

    def run(self, input_handoff: Path, attempt: int) -> Path:
        incoming = self.runtime.read_handoff(input_handoff)["payload"]
        artifacts = incoming.get("artifacts") or {}
        spec_ref = artifacts.get("spec")
        plan_ref = artifacts.get("plan")
        manifest_ref = artifacts.get("rtl_manifest")
        if not all(isinstance(ref, str) for ref in (spec_ref, plan_ref, manifest_ref)):
            raise ValueError("Tester 输入缺少 Spec、Plan 或 RTL manifest artifact")
        plan = self.runtime.read_json_artifact(plan_ref)
        if not isinstance(plan, dict):
            raise ValueError("Plan artifact 格式无效")
        files = load_snapshot(self.runtime, manifest_ref)
        combined_ref = self.runtime.write_text_artifact(
            f"generated_attempt{attempt}.sv", combine_files(files)
        )
        combined_path = self.runtime.run_dir / combined_ref
        tester_dir = self.runtime.run_dir / f"tester-{attempt:02d}"
        tester_dir.mkdir(exist_ok=True)
        prompt = (
            f"Round {attempt}: compile {len(files)} project files and run the fixed teacher TB; "
            "generate power activity and synthesized area as well."
        )
        event = self.runtime.begin_event(
            "tester", self.node_type, f"{self.title} · Round {attempt}", prompt, attempt
        )
        project = self.runtime.options.get("project") or get_project()
        expected_level = project.get("expected_cpu_level")
        floating, width_warnings = (
            cpu_elaboration_observation(combined_path) if expected_level and expected_level >= 1 else ([], [])
        )
        if floating:
            report = {
                "passed": False, "teacher_passed": None, "stage": "compile",
                "preflight": "floating_clock_or_reset",
                "checks": [], "trace": [], "stderr": "\n".join(floating),
            }
        else:
            report = test_rtl(
                task_name=project["task_name"],
                testbench_path=project["testbench_path"],
                testbench_top=project["testbench_top"],
                rtl_path=combined_path,
                power_testbench_path=project["power_testbench_path"],
                power_testbench_top=project["power_testbench_top"],
                expected_cpu_level=project.get("expected_cpu_level"),
                rtl_top_module=plan.get("top_module"),
                timeout_seconds=int(project.get("timeout_seconds", 20)),
                run_dir=tester_dir,
                metrics_on_failure=False,
            )
            self.check_cancel()
            if expected_level == 1 and report.get("stage") == "simulation":
                # 教师 TB 一次 40 s 以上，而整机探针 1–2 s 就能指出"cold load 拿回 0"这类
                # 决定性反例。先跑探针再交给 Diagnoser，避免 86 条断言只换来"load 全 0"。
                report["execution_observation"] = execution_observation(combined_path, 1)
                report["l1_control_observation"] = l1_control_observation(combined_path)
        if width_warnings:
            report["width_warnings"] = width_warnings
        brief = self.runtime.read_json_artifact(artifacts["repair_brief"]) if "repair_brief" in artifacts else None
        if expected_level is not None:
            report.setdefault("teacher_passed", bool(report["passed"]))
            capability = report.get("cpu_capability") or {}
            report["passed"] = report["passed"] and capability.get("verified_level") == expected_level
            if expected_level == 3:
                require_l3_synthesis(report)
            if report["passed"] and expected_level == 1:
                report["l1_halt_wait_observation"] = l1_halt_wait_observation(combined_path)
            if expected_level == 2 and report["stage"] == "simulation":
                report["l2_recovery_observation"] = l2_recovery_observation(combined_path)
                report["l2_predictor_table_observation"] = l2_predictor_table_observation(combined_path)
                report["l2_branch_direction_observation"] = l2_branch_direction_observation(combined_path)
                l2_boundaries = {
                    name: probe_failures(name, report)
                    for name in ("l2_recovery_observation", "l2_predictor_table_observation",
                                 "l2_branch_direction_observation")
                }
                report["l2_boundary_failures"] = l2_boundaries
                report["passed"] = report["passed"] and not any(l2_boundaries.values())
            if expected_level >= 1 and report["stage"] == "simulation" and (
                not report["passed"] or (brief and brief.get("verification_probe"))
            ):
                observations = {
                    "retirement_observation": retirement_observation,
                    "icache_word_observation": icache_word_observation,
                    "dcache_lane_observation": dcache_lane_observation,
                    "execution_observation": partial(execution_observation, level=expected_level),
                }
                if expected_level == 3:
                    observations.update({
                        "l3_fetch_backpressure_observation": l3_fetch_backpressure_observation,
                        "l3_rename_reuse_observation": l3_rename_reuse_observation,
                        "l3_rename_allocation_observation": l3_rename_allocation_observation,
                        "l3_iq_selection_observation": l3_iq_selection_observation,
                        "l3_redirect_observation": l1_redirect_observation,
                        "l3_repeated_redirect_observation": l2_recovery_observation,
                        "l3_memory_queue_observation": l3_memory_queue_observation,
                        "l3_link_recovery_observation": l3_link_recovery_observation,
                        "l3_lsu_completion_observation": l3_lsu_completion_observation,
                        "l3_store_writeback_observation": l3_store_writeback_observation,
                        "l3_predictor_training_observation": l1_control_observation,
                    })
                if expected_level == 1:
                    observations.update({
                        "l1_control_observation": l1_control_observation,
                        "l1_fetch_wait_observation": l1_fetch_wait_observation,
                        "l1_halt_observation": l1_halt_observation,
                        "l1_halt_wait_observation": l1_halt_wait_observation,
                        "l1_redirect_observation": l1_redirect_observation,
                    })
                if not report["passed"] or (brief and brief.get("verification_probe") == "pipeline_observation"):
                    observations["pipeline_observation"] = partial(pipeline_observation, level=expected_level)
                if not report["passed"] or (brief and brief.get("verification_probe") == "logic_loop_observation"):
                    observations["logic_loop_observation"] = logic_loop_observation
                if expected_level == 3:
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        results = {name: pool.submit(observe, combined_path) for name, observe in observations.items()}
                        report.update({name: result.result() for name, result in results.items()})
                else:
                    report.update({name: observe(combined_path) for name, observe in observations.items()})
                self.check_cancel()
        receipt_ref = None
        if brief is not None:
            packet = self.runtime.read_json_artifact(artifacts["failure_packet"])
            receipt = verification_receipt(brief, packet, report, manifest_ref)
            receipt_ref = self.runtime.write_json_artifact(f"repair_verification_attempt{attempt}.json", receipt)
            report["repair_verification"] = receipt
        report_path = save_report(tester_dir, report)
        self.save_context(
            attempts=int(self.context.get("attempts", 0)) + 1,
            plan_ref=plan_ref,
            rtl_manifest_ref=manifest_ref,
        )
        report_ref = self.runtime.artifact_ref(report_path)
        self.finish(
            event,
            "passed" if report.get("passed") else "failed",
            response=report_summary(report),
            report=report_preview(report, report_ref),
            feedback=report_feedback(report),
        )
        packet_ref = None
        if not report.get("passed"):
            packet_ref = self.runtime.write_json_artifact(
                f"failure_packet_attempt{attempt}.json", make_packet(report, files, report_ref, manifest_ref)
            )
        target = "summary" if report.get("passed") else "diagnoser"
        label = "Tester → Summary: final report artifact" if report.get("passed") else "Tester → Diagnoser: failure report artifact"
        return self.runtime.write_handoff(
            "tester",
            target,
            {
                "attempt": attempt,
                "summary": report_summary(report),
                "artifacts": {
                    "spec": spec_ref,
                    "plan": plan_ref,
                    "rtl_manifest": manifest_ref,
                    "report": report_ref,
                    "combined_rtl": combined_ref,
                    **({"failure_packet": packet_ref} if packet_ref else {}),
                    **({"repair_verification": receipt_ref} if receipt_ref else {}),
                },
            },
            attempt=attempt,
            label=label,
        )
