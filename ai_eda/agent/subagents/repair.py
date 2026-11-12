"""Bounded failure packets and teacher-based candidate selection."""

import os
import re

from ai_eda.agent.cpu_workflow import module_instance_body
from ai_eda.agent.skills.cpu_diagnostics import build_cpu_repair_packet

from .common import combine_files


def l3_connections(files: dict[str, str]) -> dict[str, list[str]]:
    """Collect named bindings from the current RTL, including expression inputs."""
    top = files.get("rtl/rv32i5.sv", "")
    top = re.sub(r"/\*.*?\*/|//[^\n]*", "", top, flags=re.S)
    bindings = {}
    for module in ("reorder_buffer", "rename_unit", "issue_queue", "load_store_unit", "icache", "dcache", "branch_predictor"):
        body = module_instance_body(top, module)
        pins = re.split(r",\s*(?=\.\w+\s*\()", body)
        if body:
            bindings[module] = [" ".join(pin.split()) for pin in pins]
    return bindings


def relevant_l3_connections(
    bindings: dict[str, list[str]], editable_files: list[str], symbols: list[str],
) -> dict[str, list[str]]:
    editable_modules = {path.removeprefix("rtl/").removesuffix(".sv") for path in editable_files}
    related = set(symbols)
    for module in editable_modules:
        for pin in bindings.get(module, ()):
            related.update(re.findall(r"\(\s*([A-Za-z_]\w*)\s*\)$", pin))
    related.difference_update(("clk", "rst_n"))
    scoped = {}
    for module, pins in bindings.items():
        selected = pins if module in editable_modules else [
            pin for pin in pins if related.intersection(re.findall(r"\b[A-Za-z_]\w*\b", pin))
        ]
        if selected:
            scoped[module] = selected
    return scoped


def make_packet(report: dict, files: dict[str, str], report_ref: str, snapshot: str) -> dict:
    summary = build_cpu_repair_packet(
        report, max_check_groups=8, first_trace_events=3, last_trace_events=3,
        max_suites=8, max_benchmarks=1,
    )
    summary.pop("performance")
    summary.pop("score_components")
    if report.get("synthesis_latch"):
        summary["synthesis_latch"] = report["synthesis_latch"]
    checks = report.get("checks") or []
    failed_tests = {entry["name"] for entry in checks}
    trace = report.get("trace") or []
    trace_window = []
    for row in trace:
        if row.get("test") not in failed_tests:
            continue
        if any(str(row.get(key[9:])) != str(value) for key, value in row.items()
               if key.startswith("expected_") and key[9:] in row):
            trace_window.append({key: str(value)[:240] for key, value in list(row.items())[:24]})
            if len(trace_window) == 4:
                break
    summary["first_mismatches"] = trace_window
    if report.get("preflight"):
        summary["preflight"] = report["preflight"]
    summary["retirement_observation"] = report.get("retirement_observation")
    summary["icache_word_observation"] = report.get("icache_word_observation")
    summary["dcache_lane_observation"] = report.get("dcache_lane_observation")
    summary["execution_observation"] = report.get("execution_observation")
    summary["l3_rename_reuse_observation"] = report.get("l3_rename_reuse_observation")
    summary["l3_rename_allocation_observation"] = report.get("l3_rename_allocation_observation")
    summary["l3_iq_selection_observation"] = report.get("l3_iq_selection_observation")
    summary["l3_redirect_observation"] = report.get("l3_redirect_observation")
    summary["l3_repeated_redirect_observation"] = report.get("l3_repeated_redirect_observation")
    summary["l3_memory_queue_observation"] = report.get("l3_memory_queue_observation")
    summary["l3_link_recovery_observation"] = report.get("l3_link_recovery_observation")
    summary["l3_lsu_completion_observation"] = report.get("l3_lsu_completion_observation")
    summary["l3_store_writeback_observation"] = report.get("l3_store_writeback_observation")
    summary["l3_fetch_backpressure_observation"] = report.get("l3_fetch_backpressure_observation")
    summary["l3_predictor_training_observation"] = report.get("l3_predictor_training_observation")
    summary["l1_control_observation"] = report.get("l1_control_observation")
    summary["l1_fetch_wait_observation"] = report.get("l1_fetch_wait_observation")
    summary["l1_halt_observation"] = report.get("l1_halt_observation")
    summary["l1_halt_wait_observation"] = report.get("l1_halt_wait_observation")
    summary["l2_recovery_observation"] = report.get("l2_recovery_observation")
    summary["l2_predictor_table_observation"] = report.get("l2_predictor_table_observation")
    summary["l2_branch_direction_observation"] = report.get("l2_branch_direction_observation")
    summary["l1_redirect_observation"] = report.get("l1_redirect_observation")
    pipeline = report.get("pipeline_observation")
    if pipeline and pipeline.get("l3_accepted_transfers"):
        rows = pipeline.get("samples", [])
        pipeline = dict(pipeline, samples=rows[:8] + rows[-8:] if len(rows) > 16 else rows)
    summary["pipeline_observation"] = pipeline
    summary["logic_loop_observation"] = report.get("logic_loop_observation")
    summary["first_checks"] = [
        {key: str(item[key])[:500] for key in ("name", "detail")} for item in checks[:4]
    ]
    width_warnings = report.get("width_warnings") or []
    if report.get("stage") == "compile" or width_warnings:
        # Icarus points at the concatenated input. Translate line numbers back
        # to the exact source files that Patch Agent can edit.
        starts = []
        for index, line in enumerate(combine_files(files).splitlines(), start=1):
            if line.startswith("// file: ") and line[9:] in files:
                starts.append((index, line[9:]))
        if report.get("stage") == "compile":
            diagnostics = compile_diagnostics(report)
            summary["error_excerpt"] = diagnostics[:16] or summary["error_excerpt"]
        summary["width_warnings"] = list(width_warnings)
        for lines in (summary["error_excerpt"], summary["width_warnings"]):
            for i, line in enumerate(lines):
                match = re.search(r"generated_attempt\d+\.sv:(\d+)", line)
                if match:
                    number = int(match[1])
                    for start, path in reversed(starts):
                        if number > start:
                            lines[i] = f"{path}:{number - start}: {line}"
                            break
    return {
        "schema_version": 1, "report": report_ref, "rtl_manifest": snapshot,
        "teacher_evidence": summary,
    }


def compile_diagnostics(report: dict) -> list[str]:
    lines = str(report.get("stderr") or "").splitlines()
    return [
        line.strip()[:360] for line in lines
        if re.search(r":(?:\d+:)?\s*(?:(?:vvp\.tgt\s+)?error:|syntax error|Syntax in|Incomprehensible|warning: .*dangling input port)", line)
    ]


def _parse_diagnostics(lines: list[str]) -> list[str]:
    return [line for line in lines if re.search(
        r"syntax error|Syntax in|Incomprehensible|has already been declared in this scope", line, re.I
    )]


def require_l3_synthesis(report: dict) -> None:
    metrics = report.get("metrics") or {}
    if not report.get("passed") or metrics.get("status") != "failed":
        return
    error = str(metrics.get("reason") or "")
    latch = re.search(r"ERROR: Latch inferred for signal `\\(\w+)\.\\(\w+)", error)
    if latch:
        report["synthesis_latch"] = {"module": latch[1], "signal": latch[2]}
    detail = next((line.strip() for line in reversed(error.splitlines()) if "ERROR:" in line), error)
    report["checks"].append({"name": "synthesis", "detail": detail[:450]})
    report["passed"] = False


def repair_verdict(old: dict, new: dict) -> tuple[bool, str]:
    if new.get("passed"):
        return True, "teacher_pass"
    if old.get("synthesis_latch") and new.get("teacher_passed") and new.get("synthesis_latch"):
        if new["synthesis_latch"] != old["synthesis_latch"]:
            return True, "next_synthesis_latch"
    if old.get("stage") == "compile":
        if new.get("stage") != "compile":
            return True, "reached_simulation"
        old_errors, new_errors = compile_diagnostics(old), compile_diagnostics(new)
        old_parse, new_parse = _parse_diagnostics(old_errors), _parse_diagnostics(new_errors)
        if old_parse and not new_parse and new_errors:
            return True, "reached_elaboration"
        if old_errors and new_parse and not old_parse:
            return False, "parse_failure_reintroduced"
        if old_parse and new_parse and len(old_parse) != len(new_parse):
            return len(new_parse) < len(old_parse), "parse_diagnostic_count"
        if new_errors and len(new_errors) < len(old_errors):
            return True, "fewer_compile_diagnostics"
        return False, "compile_failure_not_improved"
    if new.get("stage") == "compile" or (new.get("timed_out") and not old.get("timed_out")):
        return False, "lost_simulation_coverage"
    if old.get("timed_out") and not new.get("timed_out"):
        return True, "completed_teacher_simulation"
    old_passed = {x["name"] for x in old.get("test_suites", []) if x.get("passed") and not x.get("skipped")}
    new_passed = {x["name"] for x in new.get("test_suites", []) if x.get("passed") and not x.get("skipped")}
    if old_passed - new_passed:
        return False, "previously_passing_suite_regressed"
    old_checks = old.get("checks") or []
    new_checks = new.get("checks") or []
    old_timeouts = {x["name"] for x in old_checks if "等待 halted 超时" in x["detail"]}
    new_timeouts = {x["name"] for x in new_checks if "等待 halted 超时" in x["detail"]}
    old_cases = {x["name"] for x in old_checks}
    new_cases = {x["name"] for x in new_checks}
    novel_timeouts = new_timeouts - old_timeouts
    novel_cases = new_cases - old_cases
    if novel_timeouts or novel_cases:
        # 一次大幅收敛的补丁常顺带把一个用例改成长超时、或新增一条断言。
        # 历史上 103 → 34 被 "new_failing_cases" 整份判负（REFACTOR.md:198），
        # 因此允许"断言数大幅下降 + 至多两条新用例"的候选进入复测。
        drop = len(old_checks) - len(new_checks)
        bulk = int(os.environ.get("EDA_BULK_DROP", "40"))
        if (os.environ.get("EDA_VERDICT_STRICT") != "1" and drop >= bulk
                and len(novel_cases) <= 2):
            return True, "substantial_assertion_drop"
        if novel_timeouts:
            return False, "additional_halt_timeouts"
        return False, "new_failing_cases"
    if new_timeouts < old_timeouts:
        return True, "more_tests_reached_halt"
    if len(new_checks) < len(old_checks):
        return True, "fewer_assertion_failures"
    return False, "no_verified_progress"


PROBE_TARGETS = (
    "icache_word_observation", "dcache_lane_observation", "retirement_observation", "execution_observation",
    "logic_loop_observation", "pipeline_observation", "l1_control_observation", "l1_redirect_observation",
    "l1_halt_observation", "l1_fetch_wait_observation", "l1_halt_wait_observation", "l2_recovery_observation",
    "l2_predictor_table_observation", "l2_branch_direction_observation",
    "l3_rename_reuse_observation", "l3_redirect_observation", "l3_memory_queue_observation",
    "l3_repeated_redirect_observation", "l3_link_recovery_observation", "l3_predictor_training_observation",
    "l3_store_writeback_observation", "l3_fetch_backpressure_observation",
    "l3_lsu_completion_observation", "l3_rename_allocation_observation", "l3_iq_selection_observation",
)


def probe_failures(target: str, evidence: dict) -> list[str] | None:
    observation = evidence.get(target) or {}
    if observation.get("status") != "observed":
        return None
    if target == "pipeline_observation":
        transfer = observation.get("fetch_transfer")
        if transfer is None:
            return None
        return [key for key in ("accepted", "response_seen") if not transfer.get(key)]
    if target == "logic_loop_observation":
        return [
            *(", ".join(loop["locations"]) or loop["cells"] for loop in observation.get("loops", [])),
            *(f"undriven {name}" for name in observation.get("undriven", [])),
        ]
    samples = [
        dict(field.split("=", 1) for field in line.split("|")[1:] if "=" in field)
        for line in observation.get("samples", [])
    ]
    if target == "icache_word_observation" and len(samples) == 4:
        return [row.get("address", "?") for row in samples
                if row.get("match") != "1" or row.get("refill_match", "1") != "1"]
    if target == "dcache_lane_observation" and len(samples) in (5, 7, 9):
        return [row.get("case", "?") for row in samples if row.get("match") != "1"]
    if (target, len(samples)) in (("l3_rename_allocation_observation", 10), ("l3_rob_allocation_observation", 5),
                                  ("l3_rob_allocation_observation", 6),
                                  ("l3_lsu_completion_observation", 4), ("l3_iq_selection_observation", 2)):
        return [row.get("case", "?") for row in samples if row.get("match") != "1"]
    if target == "execution_observation" and len(samples) == 1:
        return execution_failures(samples[0])
    if target == "l3_rename_reuse_observation" and len(samples) == 1:
        expected = {"retired": "14", "ordered": "1", "halted": "1", "register": "0000000c",
                    "exit": "0000000c", "reads": "0", "writes": "0"}
        return [key for key, value in expected.items() if samples[0].get(key) != value]
    if target == "l3_memory_queue_observation" and len(samples) == 1:
        expected = {"retired": "5", "ordered": "1", "halted": "1", "register": "0000292a",
                    "exit": "00000029", "reads": "4", "writes": "0"}
        return [key for key, value in expected.items() if samples[0].get(key) != value]
    if target == "l3_store_writeback_observation" and len(samples) == 1:
        expected = {"retired": "6", "ordered": "1", "halted": "1", "register": "00000013",
                    "exit": "00000013", "writes": "1"}
        return [key for key, value in expected.items() if samples[0].get(key) != value]
    if target == "l3_fetch_backpressure_observation" and len(samples) == 1:
        expected = {"retired": "16", "ordered": "1", "halted": "1", "last_pc": "0000003c",
                    "exit": "00000035", "register": "00000029", "reads": "4", "writes": "0"}
        return [key for key, value in expected.items() if samples[0].get(key) != value]
    if target == "l1_control_observation" and len(samples) == 1:
        expected = {"retired": "35", "last_pc": "00000010", "register": "00000000", "halted": "1", "exit": "00000007"}
        return [key for key, value in expected.items() if samples[0].get(key) != value]
    if target == "l3_predictor_training_observation" and len(samples) == 1:
        return execution_failures(samples[0])
    if target in ("l1_redirect_observation", "l3_redirect_observation", "l3_link_recovery_observation") and len(samples) == 1:
        exit_code = "0000000b" if target == "l3_link_recovery_observation" else "00000007"
        expected = {"retired": "4", "ordered": "1", "last_pc": "00000024", "halted": "1", "exit": exit_code}
        return [key for key, value in expected.items() if samples[0].get(key) != value]
    if target in ("l1_halt_observation", "l1_halt_wait_observation") and len(samples) == 1:
        expected = {"retired": "3", "ordered": "1", "reads": "4", "writes": "0", "halted": "1", "exit": "00000029"}
        if target == "l1_halt_wait_observation":
            expected.update(if_changes="0", dmem_changes="0", halt_changes="0")
        return [key for key, value in expected.items() if samples[0].get(key) != value]
    if target == "l2_branch_direction_observation" and len(samples) == 1:
        expected = {"retired": "27", "ordered": "1", "writes": "0", "halted": "1", "exit": "00000009",
                    "resolved": "10", "mispredicts": "5"}
        return [key for key, value in expected.items() if samples[0].get(key) != value]
    if target == "l2_predictor_table_observation" and len(samples) == 3:
        return [row.get("case", "?") for row in samples if row.get("match") != "1"]
    if target in ("l2_recovery_observation", "l3_repeated_redirect_observation") and len(samples) == 1:
        expected = {"retired": "10", "ordered": "1", "writes": "0", "halted": "1", "exit": "00000008",
                    "resolved": "3", "mispredicts": "3", "if_changes": "0"}
        return [key for key, value in expected.items() if samples[0].get(key) != value]
    if target == "l1_fetch_wait_observation" and len(samples) == 1:
        row = samples[0]
        failures = [key for key, value in {"if_changes": "0", "halted": "1", "exit": "00000007"}.items()
                    if row.get(key) != value]
        reads = row.get("if_reads", "")
        if not reads.isdigit() or not 8 <= int(reads) <= 12:
            failures.append("refill_reuse")
        return failures
    if target == "retirement_observation" and len(samples) == 2:
        reset, retirement = samples
        requirements = {
            "reset_bus_quiet": all(reset.get(key) == "0" for key in ("imem_valid", "dmem_valid")),
            "first_pc_instruction": (
                retirement.get("first_pc") == "00000000" and retirement.get("first_insn") == "00700513"
            ),
            "ecall_committed": retirement.get("ecalls") == "1" and retirement.get("halted") == "1",
            "exit_code": retirement.get("exit") == "00000007" and retirement.get("x10") == "00000007",
        }
        return [name for name, passed in requirements.items() if not passed]
    return None


def verification_receipt(brief: dict, packet: dict, report: dict, manifest_ref: str) -> dict:
    target = brief.get("verification_probe")
    evidence = packet["teacher_evidence"]
    before = probe_failures(target, evidence) if target else None
    after = probe_failures(target, report) if target else None
    comparisons = {}
    for probe in PROBE_TARGETS:
        old = probe_failures(probe, evidence)
        new = probe_failures(probe, report)
        if old is not None and new is not None:
            comparisons[probe] = {"before": len(old), "after": len(new)}
    baseline_stage = evidence.get("stage")
    candidate_stage = report.get("stage")
    resolved = not after if after is not None else None
    if baseline_stage == "compile" and target is None:
        resolved = candidate_stage == "simulation"
    return {
        "schema_version": 1,
        "rtl_manifest": manifest_ref,
        "baseline_manifest": brief["rtl_manifest"],
        "baseline_stage": baseline_stage,
        "candidate_stage": candidate_stage,
        "verification": brief["verification"],
        "probe": target,
        "before_failures": before,
        "after_failures": after,
        "probe_failure_counts": comparisons,
        "resolved": resolved,
        "teacher_passed": report.get("teacher_passed", report.get("passed")),
    }


def execution_failures(row: dict[str, str]) -> list[str] | None:
    scenario = row.get("scenario")
    common = {"halted": "1", "writes": "0"}
    expectations = {
        "1": {"retired": "4", "ordered": "1", "reads": "4", "register": "00000029", "exit": "0000002a"},
        "2": {"retired": "35", "resolved": "16", "register": "00000000", "exit": "00000007"},
        "3": {"retired": "5", "ordered": "1", "reads": "4", "register": "00000029", "exit": "0000000a"},
    }
    if scenario not in expectations:
        return None
    failures = [key for key, expected in (common | expectations[scenario]).items() if row.get(key) != expected]
    if scenario == "2" and row.get("mispredicts") not in {"0", "1", "2", "3", "4"}:
        failures.append("mispredict_budget")
    if scenario == "3":
        expected_retirements = (
            "00000000:10000093,00000004:0000a103,00000008:00900193,"
            "0000000c:00118513,00000010:00000073,"
        )
        if row.get("first_retirements") != expected_retirements:
            failures.append("instruction_words")
        if not row.get("first_reads", "").startswith("00000100:"):
            failures.append("load_address")
        if not (row.get("overlaps", "").isdigit() and int(row["overlaps"]) >= 1):
            failures.append("data_wait_bypass")
    return failures


def may_continue_probe_repair(report: dict, selected: dict | None = None) -> bool:
    """Permit another diagnostic attempt without promoting the candidate."""
    receipt = report.get("repair_verification") or {}
    before = receipt.get("before_failures")
    after = receipt.get("after_failures")
    comparisons = receipt.get("probe_failure_counts") or {}
    if report.get("stage") != "simulation":
        return False
    if report.get("timed_out"):
        old_samples = ((selected or {}).get("retirement_observation") or {}).get("samples", ())
        fetch = (report.get("pipeline_observation") or {}).get("fetch_transfer") or {}
        return (
            receipt.get("probe") == "icache_word_observation"
            and isinstance(before, list) and len(before) == 4 and after == []
            and any("|retired=0|" in sample for sample in old_samples)
            and fetch.get("accepted") is True and fetch.get("response_seen") is True
            and not any(row.get("passed") for row in (selected or {}).get("test_suites", ()))
        )
    if (selected is not None and receipt.get("probe") == "icache_word_observation"
            and isinstance(before, list) and len(before) == 4 and after == []
            and len(report.get("checks", ())) < len(selected.get("checks", ()))):
        old_passed = {row["name"] for row in selected.get("test_suites", ()) if row.get("passed")}
        new_passed = {row["name"] for row in report.get("test_suites", ()) if row.get("passed")}
        structural = comparisons.get("logic_loop_observation") or {}
        if old_passed <= new_passed and structural.get("after") == 0:
            return True
    if selected is not None and receipt.get("resolved") is False:
        selected_cases = {row["name"] for row in selected.get("checks", ())}
        if any(row["name"] not in selected_cases for row in report.get("checks", ())):
            # A correctly paired L3 fetch or load can unblock diagnosis without
            # promoting the candidate over its teacher-verified baseline.
            strong_l3_progress = (
                receipt.get("probe") == "execution_observation"
                and isinstance(before, list) and isinstance(after, list)
                and set(after) < set(before)
                and bool({"instruction_words", "load_address"} & (set(before) - set(after)))
            )
            if not strong_l3_progress:
                return False
    if comparisons:
        return sum(x["after"] for x in comparisons.values()) < sum(x["before"] for x in comparisons.values())
    return isinstance(before, list) and isinstance(after, list) and len(after) < len(before)


def diagnostic_repair_progress(report: dict, selected: dict, diagnostic: dict | None) -> bool:
    if diagnostic is not None:
        probe = (diagnostic.get("repair_verification") or {}).get("probe")
        if probe:
            before = probe_failures(probe, diagnostic)
            after = probe_failures(probe, report)
            if before is not None and (after is None or not set(after) <= set(before)):
                return False
        if repair_verdict(diagnostic, report)[0]:
            return True
    return may_continue_probe_repair(report, selected)
