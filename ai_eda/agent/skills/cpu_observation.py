"""Short, read-only CPU probe for distinguishing fetch, retirement and halt failures."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ai_eda.agent.cpu_workflow import module_instance_body
from ai_eda.config import AGENT_CONTENT_ROOT


def _observe(
    rtl: Path, top: str, markers: tuple[str, ...], sample_count: int | None, *plusargs: str,
    probe: Path | None = None,
) -> dict:
    probe = probe or AGENT_CONTENT_ROOT / "verification" / f"{top}.sv"
    with tempfile.TemporaryDirectory(prefix="eda-retirement-") as directory:
        executable = Path(directory) / "probe.vvp"
        try:
            compiled = subprocess.run(
                ["iverilog", "-g2012", "-s", top, "-o", str(executable), str(probe), str(rtl)],
                capture_output=True, text=True, timeout=10,
            )
            if compiled.returncode:
                return {"status": "compile_error", "detail": compiled.stderr[:1200]}
            simulated = subprocess.run(["vvp", str(executable), *plusargs], capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            return {"status": "timeout"}
    samples = [line for line in simulated.stdout.splitlines() if line.startswith(markers)]
    return {
        "status": (
            "observed" if simulated.returncode == 0 and
            (len(samples) == sample_count if sample_count is not None else bool(samples))
            else "simulation_error"
        ),
        "samples": samples,
    }


def retirement_observation(rtl: Path) -> dict:
    result = _observe(rtl, "cpu_retirement_probe", ("RESET|", "RETIREMENT|"), 2)
    result["program"] = "addi x10,x0,7; ecall; nop; zero-wait external memory"
    result["expected"] = "reset bus quiet, first retirement PC 0, ECALL retires once, halted with exit=7"
    result["field_semantics"] = "When retired=0, first_pc and first_insn are sentinels, not observed CPU values."
    return result


def icache_word_observation(rtl: Path) -> dict:
    result = _observe(rtl, "icache_word_probe", ("WORD|",), 4)
    result["experiment"] = (
        "Four independent cold requests, one per word offset, with address-dependent memory data. "
        "After acceptance the idle request address changes to the next offset; the response must "
        "still match the accepted address."
    )
    return result


def dcache_lane_observation(rtl: Path) -> dict:
    result = _observe(rtl, "dcache_lane_probe", ("DCACHE|",), 9, "+final_beat_store")
    result["experiment"] = (
        "Cold load at word offset one, then halfword and byte stores with an immediate "
        "readback after each store. Repeat on cold stores to the second and final words of newly allocated lines. Check external "
        "refill addresses, store byte address, mask and data, plus cached data."
    )
    return result


def l3_rename_allocation_observation(rtl: Path) -> dict:
    result = _observe(rtl, "l3_rename_probe", ("RENAME|",), 10,
                      probe=AGENT_CONTENT_ROOT / "verification" / "l3_dispatch_probe.sv")
    result["experiment"] = "Allocate all eight free registers without commits, then try a nonwriter."
    return result


def l3_rob_allocation_observation(rtl: Path) -> dict:
    result = _observe(rtl, "l3_rob_probe", ("ROB|",), 6,
                      probe=AGENT_CONTENT_ROOT / "verification" / "l3_dispatch_probe.sv")
    result["experiment"] = (
        "Leave ROB idle, allocate eight entries, then complete the second entry before the head. "
        "After reset, an accepted ECALL must be complete at allocation without IQ or LSU completion."
    )
    return result


def l3_lsu_completion_observation(rtl: Path) -> dict:
    result = _observe(rtl, "l3_lsu_probe", ("LSU|",), 4,
                      probe=AGENT_CONTENT_ROOT / "verification" / "l3_dispatch_probe.sv")
    result["experiment"] = "Hold a byte store until accepted, enqueue a load on its response, then check signed byte writeback."
    return result


def l3_iq_selection_observation(rtl: Path) -> dict:
    result = _observe(rtl, "l3_iq_probe", ("IQ|",), 2,
                      probe=AGENT_CONTENT_ROOT / "verification" / "l3_dispatch_probe.sv")
    result["experiment"] = "Select a younger ready instruction past an unready older entry, then compare age across tag wrap."
    return result


def execution_observation(rtl: Path, level: int) -> dict:
    experiment, expected = {
        1: ("A cold load followed immediately by a dependent add.",
            "four read beats, x2=41, exit=42, four ordered retirements"),
        2: ("A loop containing 16 conditional branches, followed by ECALL.",
            "16 resolves, at most four mispredicts, x1=0, exit=7, 35 retirements"),
        3: ("A delayed cold load with two younger independent integer operations.",
            "four read beats, x2=41, exit=10, five ordered retirements and a D-wait bypass event"),
    }[level]
    result = _observe(rtl, "cpu_execution_probe", ("EXEC|",), 1, f"+scenario={level}")
    result["experiment"] = experiment
    result["expected"] = expected
    return result


def l1_control_observation(rtl: Path) -> dict:
    result = _observe(rtl, "cpu_execution_probe", ("EXEC|",), 1, "+scenario=2")
    result["experiment"] = "A 16-iteration backward branch loop using an already warm instruction line."
    result["expected"] = "35 retirements, x1=0, ECALL at PC 0x10, halted with exit=7"
    return result


def l3_rename_reuse_observation(rtl: Path) -> dict:
    result = _observe(rtl, "cpu_execution_probe", ("EXEC|",), 1, "+scenario=7")
    result["experiment"] = "Twelve dependent increments of x2, copy x2 to x10, then ECALL."
    result["expected"] = "Fourteen ordered retirements, x2 and exit equal 12, no data memory operations."
    return result


def l3_memory_queue_observation(rtl: Path) -> dict:
    result = _observe(rtl, "cpu_execution_probe", ("EXEC|",), 1, "+scenario=8")
    result["experiment"] = "A delayed cold word load followed by a byte load from the same cache line."
    result["expected"] = "Five ordered retirements, x2=0x292a, exit=41, four external reads and no writes."
    return result


def l3_link_recovery_observation(rtl: Path) -> dict:
    result = _observe(rtl, "cpu_execution_probe", ("EXEC|",), 1, "+scenario=4", "+link_recovery")
    result["experiment"] = "A cold JAL writes x5; its target reads the link immediately after recovery."
    result["expected"] = "Four ordered retirements and exit=11 from link 8 plus 3."
    return result


def l3_store_writeback_observation(rtl: Path) -> dict:
    result = _observe(rtl, "cpu_execution_probe", ("EXEC|",), 1, "+scenario=9")
    result["experiment"] = "A delayed store completes after younger independent writes to x3 and x10."
    result["expected"] = "Six ordered retirements, one bus write, and x3 and exit both equal 19."
    return result


def l3_fetch_backpressure_observation(rtl: Path) -> dict:
    result = _observe(rtl, "cpu_execution_probe", ("EXEC|",), 1, "+scenario=10")
    result["experiment"] = "A delayed load fills the ROB while twelve independent writes arrive from fetch."
    result["expected"] = "All sixteen instructions retire in order, with one cold refill and exit 53."
    return result


def l1_redirect_observation(rtl: Path) -> dict:
    result = _observe(rtl, "cpu_execution_probe", ("EXEC|",), 1, "+scenario=4")
    result["experiment"] = "JAL from PC 4 to a cold instruction line at 0x20, over a wrong-path write and ECALL."
    result["expected"] = "Only PCs 0, 4, 0x20 and 0x24 retire, with their original instructions and exit=7."
    return result


def l1_halt_observation(rtl: Path) -> dict:
    result = _observe(rtl, "cpu_execution_probe", ("EXEC|",), 1, "+scenario=5")
    result["experiment"] = "ECALL follows a cold load into x10, ahead of a younger register write and store."
    result["expected"] = "Three ordered retirements, four read beats, no write beats, and halt with exit=41."
    return result


def l1_fetch_wait_observation(rtl: Path) -> dict:
    result = _observe(rtl, "cpu_execution_probe", ("EXEC|",), 1, "+scenario=2", "+slow_if")
    result["experiment"] = "A backward branch at the end of a cache line, with three wait cycles per external beat."
    result["expected"] = "Stable bus request until ready, no repeated refills of warm lines, normal exit=7."
    return result


def l1_halt_wait_observation(rtl: Path) -> dict:
    result = _observe(rtl, "cpu_execution_probe", ("EXEC|",), 1, "+scenario=5", "+slow_if", "+slow_dmem")
    result["experiment"] = "ECALL after a cold load while both external buses can wait."
    result["expected"] = "Three ordered retirements, exit=41, zero younger writes and stable bus signals."
    return result


def l2_recovery_observation(rtl: Path) -> dict:
    result = _observe(rtl, "cpu_execution_probe", ("EXEC|",), 1, "+scenario=6", "+slow_if")
    result["experiment"] = (
        "The same JALR resolves first to 0x20 and then to 0x40, with a BTB index alias at 0x48 "
        "and a wrong-path store and ECALL on the fall-through path."
    )
    result["expected"] = "Ten ordered retirements, three resolves/mispredicts, no writes, exit=8."
    return result


def l2_predictor_table_observation(rtl: Path) -> dict:
    result = _observe(rtl, "l2_predictor_table_probe", ("PREDICTOR|",), 3)
    result["experiment"] = "Train PC 0x08 while looking up PC 0x18; then check a tag alias at 0x48."
    result["expected"] = "Update the trained counter using its own old state and reject a different PC tag."
    return result


def l2_branch_direction_observation(rtl: Path) -> dict:
    result = _observe(rtl, "l2_branch_direction_probe", ("DIRECTION|",), 1)
    result["experiment"] = "The same conditional branch resolves Taken, Taken, Not Taken, Taken."
    result["expected"] = "Preserve its taken target through Not Taken; 27 ordered retirements and five misses."
    return result


def pipeline_observation(rtl: Path, level: int) -> dict:
    """Observe available stage boundaries without changing the DUT."""
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", rtl.read_text(), flags=re.S)
    top = re.search(r"\bmodule\s+rv32i5\b(.*?)\bendmodule\b", source, flags=re.S)
    if not top:
        return {"status": "unavailable"}
    declarations = set(re.findall(
        r"\b(?:logic|wire|reg)\s+(?:\[[^\]]+\]\s*)?([A-Za-z_]\w*)\s*(?=[;,=])",
        top[1],
    ))
    if level == 3:
        for group in re.findall(r"(?m)^\s*(?:logic|wire|reg)\s+(?:\[[^\]]+\]\s*)?([^;\n]+);", top[1]):
            declarations.update(
                part.strip() for part in group.split(",") if re.fullmatch(r"\s*[A-Za-z_]\w*\s*", part)
            )
    signals = {
        1: (
            "if_id_valid", "if_id_pc", "if_id_insn", "id_ex_valid", "id_ex_pc",
            "ex_mem_valid", "ex_mem_pc", "mem_wb_valid", "mem_wb_pc",
            "pc_hold", "load_use_stall", "id_ex_flush", "ex_mem_hold", "mem_wb_hold",
            "mem_busy", "mem_cpu_req_valid", "mem_cpu_req_ready", "mem_cpu_resp_valid",
        ),
        2: (
            "if_valid", "if_pc", "if_insn", "id_valid", "id_pc", "ex_valid", "ex_pc", "mem_valid", "mem_pc",
            "icache_cpu_req_valid", "icache_cpu_req_ready", "icache_cpu_resp_valid",
            "icache_cpu_resp_data",
            "stall_if", "stall_id", "stall_ex", "stall_mem", "halt_pending",
            "icache_busy", "icache_stall", "if_stall", "if_hold",
            "front_stall", "load_use_stall", "mem_stall", "wb_commit_stall",
            "redirect_valid", "redirect_pc", "bp_update_valid", "bp_update_mispredict",
        ),
        3: (
            "icache_req_valid", "icache_req_ready", "icache_resp_valid",
            "fetch_done", "fetch_pc", "redirect_epoch", "squash_epoch", "cur_epoch", "ic_flush_epoch",
            "dec_valid", "dec_ready", "dec_pc", "disp_valid", "disp_pc",
            "issue_valid", "issue_ready", "issue_rob_idx", "complete_valid", "complete_rob_idx",
            "alu_complete_valid", "alu_complete_rob_idx", "lsu_complete_rob_idx",
            "commit_valid", "commit_ready", "commit_pc", "rob_full", "iq_full", "lsq_full",
            "alloc_valid", "alloc_ready", "alloc_pc", "alloc_rob_idx",
            "store_done_valid", "store_done_rob_idx", "halt_pending",
            "dcache_req_valid", "dcache_resp_valid", "lsu_complete_valid", "squash_valid",
        ),
    }[level]
    fields = [name for name in signals if name in declarations]
    cache_body = module_instance_body(top[1], "icache")
    cache_signals = {}
    for port in ("cpu_req_valid", "cpu_req_ready", "cpu_req_addr", "cpu_resp_valid", "cpu_resp_data", "flush_epoch"):
        bound = re.search(rf"\.{port}\s*\(\s*([A-Za-z_]\w*)\s*\)", cache_body)
        if bound and bound[1] in declarations:
            cache_signals[port] = bound[1]
            if bound[1] not in fields:
                fields.append(bound[1])
    allocated_tag_fields = []
    if level == 3:
        rob = module_instance_body(top[1], "reorder_buffer")
        tag = re.search(r"\.alloc_rob_idx\s*\(\s*([A-Za-z_]\w*)\s*\)", rob)
        if tag and tag[1] in declarations:
            allocated_tag_fields.append(tag[1])
            if tag[1] not in fields:
                fields.append(tag[1])
    if len(fields) < 4:
        return {"status": "unavailable"}
    harness = AGENT_CONTENT_ROOT / "verification" / "cpu_execution_probe.sv"
    columns = ["cycle", "retire_valid", "retire_pc"]
    values = columns.copy()
    accepts_fetch = all(f"cpu_req_{suffix}" in cache_signals for suffix in ("valid", "ready", "addr"))
    response_signal = cache_signals.get("cpu_resp_valid")
    capture = ""
    trace_declarations = ""
    if accepts_fetch:
        columns += ["fetch_accept_at_edge", "fetch_addr_at_edge"]
        values += ["trace_fetch_accept", "trace_fetch_addr"]
        trace_declarations += "    logic trace_fetch_accept;\n    logic [31:0] trace_fetch_addr;\n"
        capture += (
            f"            trace_fetch_accept = dut.{cache_signals['cpu_req_valid']} && dut.{cache_signals['cpu_req_ready']};\n"
            f"            trace_fetch_addr = dut.{cache_signals['cpu_req_addr']};\n"
        )
    if response_signal:
        columns.append("fetch_response_at_edge")
        values.append("trace_fetch_response")
        trace_declarations += "    logic trace_fetch_response;\n"
        capture += f"            trace_fetch_response = dut.{response_signal};\n"
    l3_edges = {}
    if level == 3:
        for label, module, valids, data_ports in (
            ("allocate", "reorder_buffer", ("alloc_valid", "alloc_ready"), ("alloc_rob_idx", "alloc_pc")),
            ("issue", "issue_queue", ("issue_valid", "issue_ready"), ("issue_rob_idx|issue_tag", "issue_pc")),
            ("lsu_issue", "load_store_unit", ("cpu_req_valid", "cpu_req_ready"), ("cpu_req_addr",)),
            ("load_complete", "load_store_unit", ("complete_valid|lsu_complete_valid",),
             ("complete_tag|complete_rob_idx|lsu_complete_rob_idx",)),
            ("retire", "reorder_buffer", ("commit_valid",), ("commit_pc", "commit_rob_idx")),
        ):
            body = module_instance_body(top[1], module)
            bindings = {
                port: match[1] for port in (*valids, *data_ports)
                if (match := re.search(rf"\.(?:{port})\s*\(\s*(\w+|1'b[01])\s*\)", body))
                and (match[1] in declarations or match[1] in ("1'b0", "1'b1"))
            }
            if not all(port in bindings for port in valids):
                continue
            signal = f"{label}_at_edge"
            trace_declarations += f"    logic {signal};\n"
            terms = [f"dut.{bindings[port]}" if bindings[port] in declarations else bindings[port] for port in valids]
            capture += f"            {signal} = {' && '.join(terms)};\n"
            columns.append(signal)
            values.append(signal)
            samples = []
            for port in data_ports:
                if port not in bindings or bindings[port] not in declarations:
                    continue
                field = f"{label}_{port.split('|')[0]}_at_edge"
                trace_declarations += f"    logic [31:0] {field};\n"
                capture += f"            {field} = dut.{bindings[port]};\n"
                columns.append(field)
                values.append(field)
                samples.append(field)
            l3_edges[signal] = samples
    fmt = "|".join(["%0d", *["%0h"] * (len(values) - 1 + len(fields))])
    args = ", ".join(values + [f"dut.{name}" for name in fields])
    trace_cycles = 96 if level == 3 else 36
    monitor = f'if (cycle < {trace_cycles}) $display("PIPE|{fmt}", {args});'
    with tempfile.TemporaryDirectory(prefix="eda-pipeline-") as directory:
        instrumented = Path(directory) / harness.name
        testbench = harness.read_text()
        testbench = testbench.replace("    initial begin\n", trace_declarations + "    initial begin\n", 1)
        instrumented.write_text(testbench.replace(
            "            #1;\n", f"{capture}            #1;\n            {monitor}\n", 1,
        ))
        result = _observe(rtl, "cpu_execution_probe", ("PIPE|",), None, f"+scenario={level}", probe=instrumented)
    result["fields"] = columns + fields
    result["trace_cycles"] = trace_cycles
    result["field_semantics"] = (
        "The at_edge fields sample the fetch request and response immediately before the rising edge update. "
        "Other signals are sampled after the update. Cycle is decimal; all other values are hex. "
        "Ignore a PC when its valid is zero."
    )
    result["top_assignments"] = [
        f"{name} = {' '.join(expression.split())}"
        for name, expression in re.findall(r"\bassign\s+([A-Za-z_]\w*)\s*=\s*([^;]{1,240});", top[1])
        if name in fields
    ][:16]
    capability_signals = {
        1: ("dbg_retire_valid",),
        2: ("dbg_retire_valid", "dbg_bp_resolve", "dbg_bp_mispredict"),
        3: ("dbg_retire_valid", "dbg_bp_resolve", "dbg_bp_mispredict", "dbg_ooo_issue", "dbg_ooo_bypass"),
    }[level]
    result["constant_capability_outputs"] = {
        name: match[1]
        for name in capability_signals
        if (match := re.search(rf"\bassign\s+{name}\s*=\s*([01]|1'b[01]|'0|'1)\s*;", top[1]))
    }
    monitored_ports = {
        1: {
            "if_stage": ("hold_pc", "if_id_valid"),
            "id_stage": ("if_id_valid", "id_ex_hold", "id_ex_flush", "id_ex_valid"),
            "ex_stage": ("id_ex_valid", "ex_mem_hold", "ex_mem_valid"),
            "mem_stage": ("ex_mem_valid", "mem_busy", "mem_wb_valid", "cpu_resp_valid"),
            "wb_stage": ("mem_wb_valid", "wb_hold", "dbg_retire_valid"),
        },
        2: {
            "if_stage": ("stall", "redirect_valid", "icache_cpu_req_valid", "icache_cpu_req_ready",
                         "icache_cpu_req_addr", "icache_cpu_resp_valid", "if_valid"),
            "id_stage": ("stall", "if_valid", "id_valid"),
            "hazard_unit": ("icache_stall", "icache_busy", "stall_if", "if_stall", "front_stall",
                            "load_use_stall", "mem_stall", "wb_commit_stall"),
            "branch_predictor": ("pred_req_pc", "resolve_valid", "resolve_pc", "pred_next_pc"),
        },
        3: {
            "reorder_buffer": ("alloc_valid", "alloc_ready", "alloc_rob_idx", "complete_valid", "complete_rob_idx",
                               "complete_result", "commit_valid", "commit_result", "squash_epoch", "redirect_epoch"),
            "issue_queue": ("disp_rob_idx", "issue_rob_idx", "issue_ready", "prf_data1", "prf_data2", "wakeup_valid"),
            "load_store_unit": ("disp_rob_idx", "disp_src1", "disp_src2", "complete_valid", "complete_rob_idx"),
            "rename_unit": ("commit_valid", "commit_prd", "commit_result"),
        },
    }[level]
    connections = {}
    for module, ports in monitored_ports.items():
        body = module_instance_body(top[1], module)
        for port in ports:
            match = re.search(rf"\.{port}\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)", body)
            if match:
                connections[f"{module}.{port}"] = " ".join(match[1].split())[:160]
    result["instance_connections"] = connections
    event_fields = {
        1: (
            ("mem_cpu_resp_valid", ("ex_mem_valid", "ex_mem_pc", "ex_mem_hold")),
            ("mem_wb_valid", ("mem_wb_pc",)),
        ),
        2: (
            ("icache_cpu_resp_valid", ("icache_cpu_resp_data",)),
            ("if_valid", ("if_pc", "if_insn")),
            ("redirect_valid", ("redirect_pc",)),
        ),
        3: (
            ("dec_valid", ("dec_ready", "dec_pc")),
            ("disp_valid", ("disp_pc", *allocated_tag_fields)),
            ("issue_valid", ("issue_ready", "issue_rob_idx")),
            ("alloc_valid", ("alloc_ready", "alloc_pc", "alloc_rob_idx")),
            ("alu_complete_valid", ("alu_complete_rob_idx",)),
            ("lsu_complete_valid", ("lsu_complete_rob_idx",)),
            ("complete_valid", ("complete_rob_idx",)),
            ("store_done_valid", ("store_done_rob_idx",)),
            ("commit_valid", ("commit_pc", "commit_ready")),
        ),
    }[level]
    field_order = result["fields"]
    rows = [
        dict(zip(field_order, values))
        for sample in result.get("samples", [])
        if len(values := sample.split("|")[1:]) == len(field_order) and values[0].isdigit()
    ]
    if l3_edges:
        result["l3_accepted_transfers"] = {
            signal: {
                "count": len(active := [row for row in rows if row[signal] == "1"]),
                "first": [{key: row[key] for key in ("cycle", *fields)} for row in active[:4]],
            }
            for signal, fields in l3_edges.items()
        }
    result["asserted_signals"] = {}
    for signal, related in (*event_fields, ("retire_valid", ("retire_pc",))):
        if signal not in field_order:
            continue
        active = [row for row in rows if row[signal] == "1"]
        result["asserted_signals"][signal] = {
            "sample_count": len(active),
            "first_samples": [
                {key: row[key] for key in ("cycle", *related) if key in row}
                for row in active[:4]
            ],
        }
    first_fetch = next((row for row in rows if row.get("fetch_accept_at_edge") == "1"), None)
    if first_fetch:
        result["first_fetch_accept"] = {key: first_fetch[key] for key in ("cycle", "fetch_addr_at_edge")}
    if accepts_fetch and response_signal and rows:
        result["fetch_transfer"] = {
            "accepted": first_fetch is not None,
            "response_seen": any(row["fetch_response_at_edge"] == "1" for row in rows),
        }
    if level in (1, 3):
        retired_at = {}
        for row in rows:
            if row["retire_valid"] != "1":
                continue
            pc = row["retire_pc"]
            if pc in retired_at:
                result["duplicate_retirement"] = {
                    "pc_hex": pc, "first_cycle": retired_at[pc], "repeat_cycle": row["cycle"],
                    "sample": row,
                }
                repeated_stages = []
                if level == 1:
                    if_hold = connections.get("if_stage.hold_pc")
                    id_hold = connections.get("id_stage.id_ex_hold")
                    for prior, current in zip(rows, rows[1:]):
                        if int(current["cycle"]) > int(row["cycle"]):
                            break
                        if (prior.get(if_hold) == "1" and prior.get(id_hold) == "0"
                                and prior.get("id_ex_flush") == "0"
                                and prior.get("if_id_valid") == current.get("if_id_valid") == "1"
                                and prior.get("if_id_pc") == current.get("if_id_pc") == pc
                                and prior.get("id_ex_valid") == current.get("id_ex_valid") == "1"
                                and prior.get("id_ex_pc") == current.get("id_ex_pc") == pc):
                            result["duplicate_retirement"]["if_id_consume_mismatch"] = {
                                "cycles": [prior["cycle"], current["cycle"]],
                                "if_hold": if_hold, "id_hold": id_hold,
                            }
                            break
                    for stage, hold in (
                        ("id_ex", connections.get("id_stage.id_ex_hold")),
                        ("ex_mem", "ex_mem_hold"),
                        ("mem_wb", "mem_wb_hold"),
                    ):
                        for prior, current in zip(rows, rows[1:]):
                            if int(current["cycle"]) > int(row["cycle"]):
                                break
                            if (prior.get(hold) == "0"
                                    and prior.get(f"{stage}_valid") == current.get(f"{stage}_valid") == "1"
                                    and prior.get(f"{stage}_pc") == current.get(f"{stage}_pc") == pc):
                                repeated_stages.append({
                                    "stage": stage, "cycles": [prior["cycle"], current["cycle"]], "hold": hold,
                                })
                                break
                result["duplicate_retirement"]["repeated_unheld_stages"] = repeated_stages
                break
            retired_at[pc] = row["cycle"]
    return result


def logic_loop_observation(rtl: Path, *, top_module: str = "rv32i5") -> dict:
    """Find combinational cycles in the elaborated CPU without changing acceptance."""
    yosys = shutil.which("yosys")
    if not yosys:
        return {"status": "unavailable"}
    with tempfile.TemporaryDirectory(prefix="eda-scc-") as directory:
        candidate = Path(directory) / "candidate.sv"
        shutil.copyfile(rtl, candidate)
        script = f"candidate.sv; hierarchy -top {top_module}; proc; flatten; opt_clean; check; scc"
        detail = ""
        try:
            result = subprocess.run(
                [yosys, "-Q", "-T", "-p", "read_verilog -sv " + script],
                cwd=directory, capture_output=True, text=True, timeout=12,
            )
            if result.returncode and "ERROR: Latch inferred for signal" in result.stderr:
                detail = result.stderr[-500:]
                result = subprocess.run(
                    [yosys, "-Q", "-T", "-p", "read_verilog -sv -nolatches " + script],
                    cwd=directory, capture_output=True, text=True, timeout=12,
                )
        except subprocess.TimeoutExpired:
            return {"status": "timeout", **({"detail": detail} if detail else {})}
    if result.returncode:
        return {"status": "unavailable", "detail": detail or result.stderr[-500:]}
    source = rtl.read_text().splitlines()
    file_starts = [
        (index, line.removeprefix("// file: "))
        for index, line in enumerate(source, 1)
        if line.startswith("// file: ")
    ]
    loops = []
    for line in result.stdout.splitlines() if not detail else ():
        if not line.startswith("Found an SCC:"):
            continue
        locations = {}
        for match in re.finditer(r"candidate\.sv:(\d+)", line):
            number = int(match[1])
            for start, path in reversed(file_starts):
                if number > start:
                    locations[f"{path}:{number - start}"] = source[number - 1].strip()[:220]
                    break
        loops.append({
            "cells": line.removeprefix("Found an SCC: ")[:400],
            "locations": list(locations),
            "source": locations,
        })
        if len(loops) == 6:
            break
    undriven = list(dict.fromkeys(
        match[1].replace("\\", "")
        for match in re.finditer(
            r"(?m)^Warning: Wire (\S+)(?: \[\d+\])? is used but has no driver\.$", result.stdout,
        )
    ))
    return {
        "status": "partial" if detail else "observed",
        "loops": loops,
        "undriven": undriven[:24],
        "undriven_count": len(undriven),
        **({"detail": detail} if detail else {}),
    }


def cpu_elaboration_observation(rtl: Path) -> tuple[list[str], list[str]]:
    """Keep consequential compiler warnings even when elaboration succeeds."""
    try:
        result = subprocess.run(
            ["iverilog", "-g2012", "-Wall", "-s", "rv32i5", "-tnull", str(rtl)],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return [], []
    if result.returncode:
        return [], []
    pattern = re.compile(r"warning: .*dangling input port .*\((?:clk|rst_n)\) floating")
    lines = result.stderr.splitlines()
    return (
        [line for line in lines if pattern.search(line)],
        [line for line in lines if "warning: Numeric constant truncated to" in line][:12],
    )
