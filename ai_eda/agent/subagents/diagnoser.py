"""Diagnoser turns a Tester failure packet into one testable repair brief."""

import json
import os
import re
from pathlib import Path

from ai_eda.agent.cpu_workflow import (
    ex_mem_control_bypasses, in_order_forwarding_collision, l3_fetch_response_uses_live_pc,
    l3_iq_dispatch_feedback, l3_load_dispatches_to_iq,
    l3_missing_debug_register_connection, l3_missing_fetch_buffer_guard,
    l3_rename_allocation_feedback,
    l3_rob_head_feedback, l3_rob_tail_advances_without_allocation, module_instance_body,
)
from ai_eda.agent.patches import PatchProtocolError
from ai_eda.agent.skills.teacher_tests import test_source
from ai_eda.agent.snapshots import load_snapshot

PATCH_INERT_WARNING = (
    "最近两轮补丁都编译通过、仿真结果逐字相同（失败 check 数不变）：上一轮假设不在决定性路径上。"
    "本轮必须换一个机制或换一个模块，并允许整份重写该文件；不要在原条件表达式上继续微调。"
)

from .base import SubAgent
from .common import extract_json_payload
from .repair import PROBE_TARGETS, l3_connections, probe_failures, relevant_l3_connections


DIAGNOSE = """You are the RTL Diagnoser. Treat the failure packet and module map as
observations. The current failure packet's stage and RTL manifest are authoritative.
When stage is simulation the current RTL compiled. A prior compile diagnostic
cannot be the current failure. Distinguish the first architectural mismatch from
a downstream timeout. Select one causal failure and the smallest set of existing
files needed to investigate or fix it. For a pipeline that never retires, localize
the earliest transfer with evidence before blaming ECALL. Report uncertainty honestly.
If a stage-boundary trace is available, compare valid+PC at adjacent boundaries
across the cache request/response, stall and release. Identify where an instruction
disappears or repeats, including the controlling connection when the bug spans modules.
Check asserted_signals against the trace before claiming any stage never runs. In
L3, compare issue and completion; a valid issue with no ROB completion can be a
missing completion producer even when frontend dispatch works.
Use l3_accepted_transfers for named handshakes. A missing signal entry means
the probe could not bind that signal. An issue/completion count describes only
the sampled cycles. If the complete execution probe shows a load retired with
the correct value, do not claim its completion is missing.
When a planned internal channel is missing from the source, select the producer
and each caller or consumer needed to connect it. Preserve the teacher-facing ports.
If the first accepted fetch or first valid IF payload is already wrong, explain
that before attributing the failure to a later redirect, data-store or halt.
When a synthesis logic loop coincides with an unknown signal in the failing
trace, follow the listed instance ports and source line across modules before
changing a cache or instruction datapath.
For a selected verification probe, target one of its current failed invariants.
When the current load register value and exit code match but retirement count or
order fails, trace duplicate or missing valid transfers rather than the data value.
Name one observed cycle, port binding, or compiler net that supports the hypothesis.
Use numbered source_evidence, when supplied, to distinguish a proven connection
from a suspected one.
Read each trace's field_semantics: at_edge fields precede the rising-edge update;
other fields follow it. Ignore PC or data where its valid is zero. An I-memory
wait failure concerns the external mem_req bus, not necessarily the CPU-to-cache
request. Check the actual ready, valid and address owner on that interface.
For unexpected retirement, separate (a) the same slot consumed twice, (b) a taken
branch or jump missing from an otherwise correct loop, and (c) one fetched word
retired at two different PCs. Route (a) to the first repeated transfer,
(b) to redirect/EX-MEM capture, and (c) to fetch or refill address/data pairing.
If a previous patch changed the named module but left the same measurement,
check the controlling port at both ends before proposing the same repair again.
Return one JSON object with stage (copied from current_stage), hypothesis,
confidence (low/medium/high), files (existing
paths), symbols (signal names), spec_sections (existing headings), constraints
(strings), teacher_checks (names copied from relevant failure groups), verification
(specific failing case or compiler diagnostic), and verification_probe (one of the listed failing probes if it directly measures your
hypothesis, or null when the teacher test is the appropriate verification)."""


def _source_evidence(packet: dict, files: dict[str, str], probes: dict) -> dict[str, list[str]]:
    """Add small, numbered excerpts when the simulation symptom has multiple causes."""
    if packet.get("stage") != "simulation":
        return {}
    scopes: dict[str, list[str]] = {}
    groups = (packet.get("checks") or {}).get("groups", ())
    if any("I-memory request changed before ready" in row["detail"] for row in groups):
        scopes["rtl/icache.sv"] = [r"assign\s+mem_req_(?:valid|addr)", r"if\s*\(flush\)", r"busy\w*\s*<=\s*1'b0"]
        scopes["rtl/rv32i5.sv"] = [r"assign\s+imem_(?:valid|addr)", r"assign\s+\w*ic\w*req_ready"]
    execution = packet.get("execution_observation") or {}
    if any(re.search(r"\|first_retirements=00000000:([0-9a-f]{8}),00000004:\1,", row)
           for row in execution.get("samples", ())):
        scopes.setdefault("rtl/rv32i5.sv", []).extend([
            r"assign\s+imem_(?:valid|addr)", r"\bimem_addr\w*\s*<=", r"assign\s+\w*ic\w*req_ready",
        ])
        scopes.setdefault("rtl/icache.sv", []).extend([r"assign\s+mem_(?:accept|fire)", r"assign\s+mem_req_addr"])
    if probes.get("l1_redirect_observation") or probes.get("l2_recovery_observation"):
        scopes["rtl/hazard_unit.sv"] = [r"assign\s+(?:ex_mem_flush|redirect_fire|redirect_valid|redirect_pc)"]
        scopes["rtl/ex_stage.sv"] = [r"\bif\s*\(ex_mem_flush\)", r"assign\s+ex_\w*redirect_\w+"]
    excerpts = {}
    for path, patterns in scopes.items():
        lines = files.get(path, "").splitlines()
        matches = [i for i, line in enumerate(lines) if any(re.search(pattern, line) for pattern in patterns)]
        indexes = sorted({j for i in matches[:6] for j in (i, i + 1) if j < len(lines)})
        if indexes:
            excerpts[path] = [f"{i + 1}: {lines[i].strip()[:150]}" for i in indexes]
    return excerpts


def _observed_compile_diagnosis(evidence: dict, files: dict[str, str]) -> dict | None:
    errors = evidence.get("error_excerpt", ())
    if any("cannot have multiple drivers" in line for line in errors):
        shared = sorted(in_order_forwarding_collision(files))
        if shared:
            return {
                "stage": "compile",
                "hypothesis": "EX and forwarding_unit drive the same top-level selector wires.",
                "confidence": "high",
                "files": ["rtl/ex_stage.sv"],
                "symbols": shared,
                "constraints": [
                    "Change the duplicate EX selector outputs to inputs and remove EX's selector "
                    "assignments. Keep its existing data muxes and top-level pin names.",
                ],
                "verification": "Each selector has a single forwarding_unit driver and Icarus elaborates.",
            }
        top_path = "rtl/rv32i5.sv"
        top = files.get(top_path, "")
        dcache = module_instance_body(top, "dcache")
        if (re.search(r"\.cpu_req_ready\s*\(\s*dc_mem_req_ready\s*\)", dcache)
                and re.search(r"\.mem_req_ready\s*\(\s*dc_mem_req_ready\s*\)", dcache)):
            return {
                "stage": "compile",
                "hypothesis": "The D-cache's CPU-side outputs are tied to its external-memory inputs, "
                              "which the top already drives from the teacher bus.",
                "confidence": "high",
                "files": [top_path],
                "symbols": ["dc_mem_req_ready", "dc_mem_resp_valid"],
                "constraints": [
                    "Create distinct CPU-side ready and response-valid wires. Connect those between "
                    "D-cache cpu_req_ready/cpu_resp_valid and MEM's corresponding inputs.",
                    "Leave dc_mem_req_ready/dc_mem_resp_valid for D-cache external inputs and preserve "
                    "their current assignments from dmem_ready and the accepted bus response.",
                ],
                "verification": "D-cache CPU handshake and external handshake no longer share drivers.",
            }
        if_stage = files.get("rtl/if_stage.sv", "")
        if (any("bp_lookup_" in line for line in errors)
                and re.search(r"input\s+logic\s+\[31:0\]\s+bp_lookup_pc", if_stage)
                and re.search(r"output\s+logic\s+\[31:0\]\s+bp_lookup_next_pc", if_stage)):
            return {
                "stage": "compile",
                "hypothesis": "The IF prediction port directions are reversed. Both IF and predictor "
                              "drive the next PC and prediction valid, and neither drives lookup PC.",
                "confidence": "high",
                "files": ["rtl/if_stage.sv"],
                "symbols": ["bp_lookup_pc", "bp_lookup_next_pc", "bp_lookup_valid", "pc_q"],
                "constraints": [
                    "Make bp_lookup_pc an output driven by the current fetch PC. Make bp_lookup_next_pc "
                    "and bp_lookup_valid inputs. Preserve port names and consume predictions on fetch acceptance.",
                ],
                "verification": "The predictor lookup PC has one IF driver and prediction outputs have one predictor driver.",
            }
    duplicates: dict[str, list[str]] = {}
    for line in errors:
        duplicate = re.match(r"(rtl/\w+\.sv):\d+: .*error: '(\w+)' has already been declared in this scope", line)
        if duplicate and duplicate[1] in files:
            duplicates.setdefault(duplicate[1], []).append(duplicate[2])
    if duplicates:
        path, signals = next(iter(duplicates.items()))
        return {
            "stage": "compile",
            "hypothesis": "Icarus found duplicate declarations in the same scope.",
            "confidence": "high",
            "files": [path],
            "symbols": list(dict.fromkeys(signals)),
            "constraints": ["Keep one declaration per named signal. Preserve the connections so elaboration can report the next issue."],
            "verification": "The named duplicate declarations disappear from Icarus output.",
        }
    for diagnostic in errors:
        packed = re.match(r"(rtl/(?:i|d)cache\.sv):\d+: .*Array index expressions must be constant", diagnostic)
        if packed and packed[1] in files:
            return {
                "stage": "compile",
                "hypothesis": "Icarus cannot elaborate the cache's dynamic index into a nested packed array.",
                "confidence": "high",
                "files": [packed[1]],
                "constraints": [
                    "Use unpacked line and word dimensions with a 32-bit packed word, for example "
                    "logic [31:0] words [0:15][0:3]. Select a whole word before merging bytes.",
                    "Apply the representation change to hit, refill, and store paths. Preserve the frozen ports.",
                ],
                "verification": "Icarus elaborates the cache and its standalone refill and byte-lane probes pass.",
            }
        location = re.match(r"(rtl/[A-Za-z0-9_]+\.sv):(\d+): .*syntax error", diagnostic, re.I)
        if not location or location[1] not in files:
            continue
        path, index = location[1], int(location[2]) - 1
        lines = files[path].splitlines()
        if not 0 <= index < len(lines):
            continue
        declaration = re.search(r"\b(?:logic|reg|wire)\s+(?:\[[^\]]+\]\s*)?(dist|priority)\s*;", lines[index])
        if declaration:
            name = declaration[1]
            return {
                "stage": "compile",
                "hypothesis": f"{path}:{index + 1} declares {name!r}, a reserved SystemVerilog keyword.",
                "confidence": "high",
                "files": [path],
                "symbols": [name],
                "constraints": [f"Rename the local {name} variable and all its references in this module to "
                                "a non-keyword such as candidate_distance; preserve all ports and logic."],
                "verification": "The syntax cascade disappears on elaboration.",
            }
    return None

def _observed_diagnosis(evidence: dict, files: dict[str, str], probes: dict) -> dict | None:
    latch = evidence.get("synthesis_latch") or {}
    if latch:
        module, signal = latch["module"], latch["signal"]
        path = f"rtl/{module}.sv"
        if path in files:
            return {
                "stage": "simulation",
                "hypothesis": f"Teacher simulation passed, but Yosys inferred a latch for {module}.{signal}.",
                "confidence": "high",
                "files": [path],
                "symbols": [signal],
                "constraints": [
                    "Give combinational values defaults before any conditional branches or loops. "
                    "Check other temporaries in the same always_comb for the same omission.",
                    "Keep valid and ready logic, cycle timing, ports, and sequential behavior intact.",
                ],
                "verification": "Teacher simulation remains passing and Yosys completes mapping.",
            }
    structural = evidence.get("logic_loop_observation") or {}
    undriven = structural.get("undriven", ())
    top_path = "rtl/rv32i5.sv"
    top = files.get(top_path, "")
    if ("rtl/branch_predictor.sv" in files
            and any(row["name"] == "t04_upper" for row in evidence.get("first_checks", ()))):
        decode = files.get("rtl/id_stage.sv", "")
        auipc = re.search(r"7'b0010111\s*:\s*begin(.*?)\bend\b", decode, re.S)
        if (auipc and "dec_alu_src_pc" in auipc[1] and "imm_u" in auipc[1]
                and "dec_alu_src_imm" not in auipc[1]
                and re.search(r"else\s+if\s*\(id_ex_alu_src_imm\)\s*alu_b\s*=\s*id_ex_imm",
                              files.get("rtl/ex_stage.sv", ""))):
            return {
                "stage": "simulation",
                "hypothesis": "AUIPC selects the PC and decodes the upper immediate, but ID leaves the "
                              "ALU immediate select clear. EX therefore adds rs2 instead of the immediate.",
                "confidence": "high",
                "files": ["rtl/id_stage.sv"],
                "symbols": ["dec_alu_src_pc", "dec_alu_src_imm", "imm_u"],
                "constraints": [
                    "Assert dec_alu_src_imm in the AUIPC decode case. Preserve the PC select, upper "
                    "immediate, ALU add and all other opcode cases.",
                ],
                "teacher_checks": ["t04_upper"],
                "verification": "AUIPC writes the instruction's PC plus its upper immediate.",
            }
    if ("rtl/branch_predictor.sv" in files
            and any("应等于停机时 x10" in row["detail"] for row in evidence.get("first_checks", ()))
            and re.search(r"\bassign\s+arch_x10\s*=[^;]*\bdbg_reg_addr\b[^;]*\bdbg_reg_data\b", top)
            and re.search(r"\.arch_x10\s*\(\s*arch_x10\s*\)", module_instance_body(top, "wb_stage"))):
        return {
            "stage": "simulation",
            "hypothesis": "The top derives the ECALL exit value from the address-selected debug read. "
                          "Architectural x10 is correct, but selecting another debug register supplies zero.",
            "confidence": "high",
            "files": [top_path],
            "symbols": ["arch_x10", "dbg_reg_addr", "dbg_reg_data"],
            "constraints": [
                "Track architectural x10 from the existing WB write enable, destination and data that drive ID. "
                "Reset it to zero, update it only on a committed write to register 10 and bind it to the "
                "existing WB port. Preserve the debug mux and ECALL commit timing.",
            ],
            "verification": "ECALL exit equals committed x10 regardless of the debug address.",
        }
    if ({"rv32i5.squash", "rv32i5.iq_issue_ready"} <= set(undriven)
            and re.search(r"\bassign\s+recover_evt\s*=\s*mispredict_evt\s*;", top)
            and re.search(r"\bassign\s+fetch_req_valid\s*=[^;]*!squash", top)):
        return {
            "stage": "simulation",
            "hypothesis": "Yosys found two missing top-level drivers. Unknown squash disables fetch "
                          "before the first request, and undriven iq_issue_ready prevents issue and completion.",
            "confidence": "high",
            "files": [top_path],
            "symbols": ["squash", "recover_evt", "iq_issue_ready", "fetch_req_valid", "disp_accept"],
            "constraints": [
                "Drive squash from the existing accepted recover_evt. Drive IQ issue_ready from the "
                "actual integer execution-lane availability; do not gate it with the resolving squash. "
                "Integer dispatch uses the free ROB tag, independently of issue_ready. Memory dispatch "
                "still requires LSU enqueue ready. Keep recovery and retirement interfaces intact.",
            ],
            "verification": "The empty core accepts a fetch at PC zero and can issue the first integer entry.",
        }
    retirement = (evidence.get("retirement_observation") or {}).get("samples", ())
    if (l3_fetch_response_uses_live_pc(top)
            and any("|first_pc=00000004|first_insn=00700513|" in row for row in retirement)):
        return {
            "stage": "simulation",
            "hypothesis": "The first retired word belongs to PC zero but carries PC four. Fetch advances "
                          "fetch_pc at request acceptance and then captures that live PC on response.",
            "confidence": "high",
            "files": [top_path],
            "symbols": ["fetch_req_valid", "fetch_req_ready", "fetch_resp_valid", "fetch_pc", "buf_pc", "buf_pred"],
            "constraints": [
                "On an accepted request save the request PC, that request's predicted next PC, and its epoch "
                "in a single outstanding slot. Advance fetch_pc to that prediction, or PC+4 on a miss.",
                "Capture matching response data with the saved metadata. Do not accept another fetch while "
                "the outstanding or dispatch buffer slot is occupied. Redirect overrides request and response, "
                "updates the fetch PC to redirect_pc and discards the stale slots.",
            ],
            "verification_probe": "retirement_observation",
            "verification": "The word 00700513 retires at PC zero, with subsequent responses paired to their own PCs.",
        }
    rob_failures = probes.get("l3_rob_allocation_observation") or ()
    rob_source = files.get("rtl/reorder_buffer.sv", "")
    if ("younger_complete" in rob_failures
            and re.search(r"\bassign\s+commit_valid\s*=\s*valid\[head_r\]\s*;", rob_source)
            and re.search(r"\bassign\s+commit_fire\s*=\s*valid\[head_r\]\s*&&\s*done\[head_r\]\s*;", rob_source)):
        return {
            "stage": "simulation",
            "hypothesis": "ROB exposes an incomplete head as commit_valid while its internal commit_fire "
                          "requires done. External retirement can then repeat a head that ROB has not consumed.",
            "confidence": "high",
            "files": ["rtl/reorder_buffer.sv"],
            "symbols": ["commit_valid", "commit_fire", "head_r"],
            "constraints": [
                "Drive commit_valid from the same existing commit_fire condition that consumes the head. "
                "Keep the head-indexed payload, completion tags and sequential recovery intact.",
            ],
            "verification_probe": "l3_rob_allocation_observation",
            "verification": "Completing only a younger entry never makes the incomplete head commit.",
        }
    if "ecall_complete" in rob_failures:
        dispatch_blocks_ecall = re.search(r"\bassign\s+disp_accept\s*=[^;]*!disp_is_ecall\b", top)
        paths = ["rtl/reorder_buffer.sv"]
        constraints = [
            "When alloc_valid && alloc_ready, initialize the allocated done bit to alloc_ecall. "
            "A valid ECALL commits only when it reaches the ROB head. Preserve other completion and recovery.",
        ]
        if dispatch_blocks_ecall:
            paths.append(top_path)
            constraints.append(
                "Allow ECALL to take the same ROB allocation edge as other instructions. Exclude ECALL "
                "from IQ and LSU enqueue, and raise halt_pending only on the accepted dispatch."
            )
        return {
            "stage": "simulation",
            "hypothesis": "The standalone ROB allocates ECALL but leaves it incomplete without an execution lane."
                          + (" The top also excludes ECALL from allocation." if dispatch_blocks_ecall else ""),
            "confidence": "high",
            "files": paths,
            "symbols": ["alloc_ecall", "commit_ecall", "disp_accept", "halt_pending"],
            "constraints": constraints,
            "verification_probe": "l3_rob_allocation_observation",
            "verification": "An allocated ECALL commits in order with its PC and completion flag.",
        }
    control = module_instance_body(top, "hazard_unit")
    icache_path = "rtl/icache.sv"
    icache = files.get(icache_path, "")
    words = (evidence.get("icache_word_observation") or {}).get("samples", ())
    if (len(words) == 4 and all("|first_refill=00000104|read_beats=3|" in word for word in words)):
        return {
            "stage": "simulation",
            "hypothesis": "All four cold requests start at line base plus four and issue only three "
                          "read beats. Beat zero is skipped, so early offsets return the next word.",
            "confidence": "high",
            "files": [icache_path],
            "constraints": [
                "Use a registered refill-active bit separate from the two-bit word index. On an accepted "
                "miss save the requested word and start external reads at beat zero. End after beat three.",
                "Use the current fourth-beat data for offset three. Hold valid and address until external "
                "ready and suppress stale installation or response after flush or epoch change.",
            ],
            "verification_probe": "icache_word_observation",
            "verification": "Each cold request issues four reads from the aligned line base and returns its word.",
        }
    if ("rtl/reorder_buffer.sv" in files and len(words) == 4
            and all("|accepted=1|response=0|" in word for word in words)):
        return {
            "stage": "simulation",
            "hypothesis": "All four accepted cold requests time out in the standalone I-cache probe, "
                          "before decode, ROB, IQ or LSU can affect the result. Follow the saved request "
                          "through each accepted refill beat to its single CPU response.",
            "confidence": "high",
            "files": [icache_path],
            "constraints": [
                "Complete each nonsquashed miss without a second CPU request. Preserve the accepted word "
                "offset, and use the current response data when it is the selected final beat.",
                "Allocate independent valid, tag, and four data words for every cache line. "
                "Keep external valid and address stable under backpressure and discard stale refill data.",
            ],
            "verification_probe": "icache_word_observation",
            "verification": "All four standalone cold requests return the word at their accepted address.",
        }
    short_refill = r"\bmem_req_addr\s*=\s*\{\s*(\w+)\s*\[31\s*:\s*4\]\s*,\s*2'b00\s*\}"
    short_base = re.search(short_refill, icache)
    if short_base and len(words) == 4 and all("|accepted=1|response=1|" in word and "|match=0" in word for word in words):
        base = short_base[1]
        return {
            "stage": "simulation",
            "hypothesis": (
                f"The I-cache rebuilds a line base from {base}[31:4] followed by only two zero bits. "
                "This shifts the byte address right by two before adding the beat offset. "
                "All four standalone requests complete but return words from the wrong line."
            ),
            "confidence": "high",
            "files": [icache_path],
            "symbols": ["mem_req_addr", base],
            "constraints": [
                "Rebuild the 16-byte aligned base with four low zero bits or use the already aligned saved base. "
                "Add the beat index times four exactly once."
            ],
            "verification_probe": "icache_word_observation",
            "verification": "All four refill offsets match their accepted byte addresses.",
        }
    rob_path = "rtl/reorder_buffer.sv"
    rob = files.get(rob_path, "")
    rename_path = "rtl/rename_unit.sv"
    rename = files.get(rename_path, "")
    if (re.search(r"free_slot\[i\]\s*<=\s*i\[2:0\]\s*\+\s*3'd32", rename)
            and any("Numeric constant truncated" in line for line in evidence.get("width_warnings", ()))):
        return {
            "stage": "simulation",
            "hypothesis": "The free-list initializer uses 3'd32, which truncates to zero. "
                          "Reset gives the allocator PR0..PR7 instead of the eight free PR32..PR39.",
            "confidence": "high",
            "files": [rename_path],
            "symbols": ["free_slot"],
            "constraints": [
                "Declare free_slot entries as six bits and initialize each using 6'd32 + i[5:0]. "
                "Preserve the eight-entry FIFO, registered pointers, and all interfaces.",
            ],
            "verification_probe": "l3_rename_reuse_observation",
            "verification": "The dependent-write program completes fourteen ordered retirements and exits with 12.",
        }
    if l3_rob_tail_advances_without_allocation(rob):
        return {
            "stage": "simulation",
            "hypothesis": "ROB advances the tail on every clock. The first real dispatch follows "
                          "several idle cycles, so its tag skips head zero and retirement cannot start.",
            "confidence": "high",
            "files": [rob_path],
            "symbols": ["tail", "next_tail", "alloc_fire", "head_r"],
            "constraints": [
                "Update tail to next_tail inside the alloc_fire branch only. Keep reset and recovery "
                "tail assignments and allow simultaneous allocation and commit.",
            ],
            "verification_probe": "retirement_observation",
            "verification": "The first dispatched instruction reaches ROB head zero and retires at PC zero.",
        }
    if l3_rename_allocation_feedback(rename):
        return {
            "stage": "simulation",
            "hypothesis": "Rename allocation checks the next free head, but the next head advances "
                          "only when allocation is accepted. When one PR remains, accepting dispatch "
                          "makes allocation unavailable and withdrawing dispatch makes it available again.",
            "confidence": "high",
            "files": [rename_path],
            "symbols": ["alloc_ok", "do_alloc", "free_head", "free_tail", "free_head_next"],
            "constraints": [
                "Compute alloc_ok from the registered free_head and free_tail alone. Permit the "
                "current instruction to take the last free entry, then report empty on the next cycle.",
                "Keep next-pointer, checkpoint, and writeback logic intact.",
            ],
            "verification": "The teacher simulation advances past a full rename queue and completes.",
        }
    if ("allocate_7" in probes.get("l3_rename_allocation_observation", ())
            and re.search(r"assign\s+do_alloc\s*=\s*dispatch\s*&&\s*can_allocate\s*&&\s*!recover\s*;", rename)):
        return {
            "stage": "simulation",
            "hypothesis": "Rename advances the free head for an accepted nonwriter despite its zero "
                          "PRD. After that nonwriter, the eighth real writer has no free entry.",
            "confidence": "high",
            "files": [rename_path],
            "symbols": ["do_alloc", "writes_rd", "rd", "free_head"],
            "constraints": [
                "Require writes_rd and rd != 0 for do_alloc. Preserve dispatch of nonwriters and "
                "checkpoint the same unchanged head for a nonwriting control instruction.",
            ],
            "verification_probe": "l3_rename_allocation_observation",
            "verification": "A nonwriter consumes no slot and the next eight writers obtain distinct free PRs.",
        }
    execution = (evidence.get("execution_observation") or {}).get("samples", ())
    if (any("|retired=0|" in row and re.search(r"\|issues=[1-9]", row) for row in execution)
            and l3_rob_head_feedback(rob)):
        return {
            "stage": "simulation",
            "hypothesis": "Fetch and integer issue now occur, but nothing retires. ROB head is "
                          "assigned from head_next, whose default reads head. Commit also indexes "
                          "head_next, feeding back through commit_fire and preventing a defined head.",
            "confidence": "high",
            "files": [rob_path],
            "symbols": ["head", "head_next", "commit_valid", "tail", "count"],
            "constraints": [
                "Derive the current head from tail minus count[2:0] in three-bit arithmetic, or use "
                "a resettable current-head register. Drive commit_valid and every commit payload "
                "from the current head, never the combinational next head.",
                "Keep simultaneous alloc and commit, preserve the resolving head on recover, "
                "and leave all ROB interfaces and completion tags unchanged.",
            ],
            "verification_probe": "execution_observation",
            "verification": "Five ordered retirements finish the cold load and younger ALU probe, exiting with 10.",
        }
    if l3_missing_debug_register_connection(top):
        return {
            "stage": "simulation",
            "hypothesis": "The top returns zero for every debug register because Rename's dbg_data "
                          "output is disconnected. Teacher register reads disagree with the committed exit value.",
            "confidence": "high",
            "files": [top_path],
            "symbols": ["dbg_reg_addr", "dbg_reg_data", "dbg_data"],
            "constraints": [
                "Connect Rename's existing dbg_data port directly to dbg_reg_data and remove the "
                "constant driver. Keep dbg_reg_addr connected and preserve Rename's own x0 logic.",
            ],
            "verification": "The teacher can read nonzero architectural register values after commit.",
        }
    if l3_missing_fetch_buffer_guard(top):
        return {
            "stage": "simulation",
            "hypothesis": "Fetch accepts a request even when its only instruction buffer is occupied. "
                          "If dispatch stalls, the next response arrives with no destination slot, "
                          "but the outstanding bit is cleared and the PC has already advanced.",
            "confidence": "high",
            "files": [top_path],
            "symbols": ["ic_cpu_req_valid", "if_buf_valid", "fetch_out_valid", "fetch_pc", "squash"],
            "constraints": [
                "In this one-buffer design qualify ic_cpu_req_valid with !if_buf_valid as well as "
                "the existing conditions. Never start a second transfer while a buffered response waits.",
                "Give squash priority over halt_pending when updating fetch_pc, since the pending ECALL "
                "can be on the wrong path. Keep the PC/epoch captured with each accepted request.",
            ],
            "verification_probe": "l3_fetch_backpressure_observation",
            "verification": "The delayed-load program retires sixteen ordered instructions and exits with 53.",
        }
    if l3_iq_dispatch_feedback(top):
        paths = [top_path]
        constraints = [
            "In dispatch_accept replace the integer branch of (is_mem ? lsu_enq_ready : iq_issue_ready) "
            "with 1'b1. A free ROB allocation tag is already a free IQ slot. Keep LSU enqueue ready "
            "for memory dispatch.",
        ]
        if re.search(r"assign\s+int_issue_fire\s*=\s*iq_issue_ready\s*&&\s*!lsu_launch_fire\s*;", top):
            constraints.append(
                "Drive iq_issue_ready from !lsu_launch_fire, !halted_r and the existing head restriction. "
                "Then int_issue_fire is iq_issue_valid && iq_issue_ready, the same event that removes the IQ entry."
            )
        if re.search(r"if\s*\(lsu_rs[12]_idx\s*==\s*alu_prd\)[^;]*=\s*alu_value", rename):
            paths.append(rename_path)
            constraints.append(
                "Remove the combinational current-ALU bypass into LSU read values. Keep registered PRF "
                "writeback and use registered PRF reads, so LSU launch cannot feed back through ALU issue arbitration."
            )
        return {
            "stage": "simulation",
            "hypothesis": "The first integer dispatch requires IQ issue ready, which requires IQ issue "
                          "valid. The empty IQ cannot become valid until that dispatch enqueues an entry.",
            "confidence": "high",
            "files": paths,
            "symbols": ["dispatch_accept", "iq_issue_ready", "iq_issue_valid", "rob_alloc_ready"],
            "constraints": constraints,
            "verification_probe": "retirement_observation",
            "verification": "The first integer instruction enters the empty IQ and retires.",
        }
    issue_ready = re.search(r"assign\s+iq_issue_ready\s*=([^;]*);", top)
    if (issue_ready and "lsu_launch_fire" not in issue_ready[1]
            and re.search(r"assign\s+int_issue_fire\s*=\s*iq_issue_ready\s*&&\s*!lsu_launch_fire\s*;", top)):
        return {
            "stage": "simulation",
            "hypothesis": "The IQ consumes its entry on issue_valid && issue_ready, but the top "
                          "suppresses ALU completion when LSU launches. The consumed ROB tag never completes.",
            "confidence": "high",
            "files": [top_path],
            "symbols": ["iq_issue_ready", "int_issue_fire", "lsu_launch_fire", "dispatch_accept"],
            "constraints": [
                "Put LSU arbitration in iq_issue_ready itself. Drive int_issue_fire from "
                "iq_issue_valid && iq_issue_ready, so the IQ and ROB observe the same event.",
                "Do not gate issue ready with the squash caused by the resolving instruction. "
                "Use the free ROB tag for IQ enqueue; issue ready is not an enqueue condition.",
            ],
            "verification": "No integer entry is removed without the corresponding ROB completion.",
        }
    if ("ready_bypass" in probes.get("l3_iq_selection_observation", ())
            and re.search(r"\bif\s*\(ent_valid\[i\]\)\s*begin", files.get("rtl/issue_queue.sv", ""))):
        iq_path = "rtl/issue_queue.sv"
        paths = [iq_path]
        constraints = [
            "Filter each resident candidate for both PR readiness bits and the ROB-head restriction for "
            "control operations before comparing its age. Choose the oldest eligible tag modulo eight.",
        ]
        if re.search(r"assign\s+dispatch_accept\s*=[^;]*lsu_enq_ready\s*:\s*iq_issue_ready", top):
            paths.append(top_path)
            constraints.append(
                "Integer enqueue uses the free ROB tag, whose IQ slot is free. Remove iq_issue_ready "
                "from the dispatch condition; retain LSU enq_ready for memory dispatch."
            )
        return {
            "stage": "simulation",
            "hypothesis": "The IQ picks the oldest valid entry before checking readiness, blocking a "
                          "younger ready entry when the oldest is waiting.",
            "confidence": "high",
            "files": paths,
            "symbols": ["best_idx", "ent_valid", "ready_bits", "dispatch_accept", "iq_issue_ready"],
            "constraints": constraints,
            "verification_probe": "l3_iq_selection_observation",
            "verification": "The ready entry issues past a waiting older entry and age order holds across wrap.",
        }
    dcache_path = "rtl/dcache.sv"
    dcache = files.get(dcache_path, "")
    lanes = (evidence.get("dcache_lane_observation") or {}).get("samples", ())
    wrong_load_address = "load_address" in (probes.get("execution_observation") or ())
    refill_concat = re.search(
        r"\bmem_req_addr\s*=[^;]*?\{\s*\w+\s*,\s*\w+\s*,\s*(\w+)\s*,\s*2'b00\s*\}",
        dcache,
    )
    beat_range = refill_concat and re.search(
        rf"\b(?:logic|reg)\s*\[\s*(\d+)\s*:\s*0\s*\]\s*{re.escape(refill_concat[1])}\b",
        dcache,
    )
    wide_beat = beat_range and int(beat_range[1]) > 1
    if (re.search(short_refill, dcache) or wide_beat) and (wrong_load_address or any(
        "|refill_addresses_match=0|" in sample and "|read_beats=4|" in sample for sample in lanes
    )):
        cause = (
            "includes a beat index wider than two bits in the external address concatenation"
            if wide_beat else "rebuilds the aligned refill address with two low zero bits instead of four"
        )
        return {
            "stage": "simulation",
            "hypothesis": f"The D-cache {cause}, so accepted loads fetch the wrong cache line "
                          "even when four beats complete.",
            "confidence": "high",
            "files": [dcache_path],
            "symbols": ["mem_req_addr", refill_concat[1] if wide_beat else "refill_base"],
            "constraints": [
                "Use the saved 16-byte aligned base plus the two-bit beat index times four. In a tag/index "
                "concatenation, only the low two bits of the beat index belong before 2'b00. Preserve the separate "
                "write-through address, mask, data and completion path."
            ],
            "verification_probe": "execution_observation" if wrong_load_address else "dcache_lane_observation",
            "verification": "The cold load reads the four expected external byte addresses and returns its expected value.",
        }
    cold_store = next((row for row in lanes if "|case=byte_miss|" in row), "")
    cold_readback = next((row for row in lanes if "|case=load_after_miss_store|" in row), "")
    cold_load = next((row for row in lanes if "|case=cold_load|" in row), "")
    hit_readback = next((row for row in lanes if "|case=load_after_sh|" in row), "")
    if ("|response=0|" in cold_load and "|read_beats=4|" in cold_load
            and "|refill_addresses_match=1|" in cold_load and "|match=0" in hit_readback
            and re.search(r"store_mask\s*=\s*\{\{8\{store_be\[3\]\}", dcache)):
        return {
            "stage": "simulation",
            "hypothesis": "D-cache reads all four correct external addresses, yet a cold load has no "
                          "CPU response. The hit-store readback also misses the accepted halfword. "
                          "The hit path uses a saved store mask instead of the current request's mask.",
            "confidence": "high",
            "files": [dcache_path],
            "symbols": ["refill_install", "resp_valid_r", "req_word", "store_mask", "cpu_req_be"],
            "constraints": [
                "On a load miss save the accepted word offset and respond once on completed refill. "
                "Select that saved offset, using the current mem_resp_data for the fourth word.",
                "Use a byte-merge helper taking the old word, full 32-bit write data and 4-bit enables. "
                "Expand each enable to eight bits and compute (old_word & ~mask) | (write_data & mask), "
                "with the full write word already aligned to bus lanes. On a hit, the old word comes "
                "from the hit line at cpu_req_addr[3:2]. On installation, for each of the four words "
                "compare its index to saved store_addr[3:2]. Merge that word from the refill buffer "
                "(current response for the fourth beat) and copy all other words unchanged. Send "
                "exactly one write-through with the original byte address.",
                "After an external write response clear the request and all write controls. Before "
                "any read refill explicitly drive the external write enable low.",
            ],
            "verification_probe": "dcache_lane_observation",
            "verification": "Cold loads respond and the standalone cache lane checks pass.",
        }
    if ("|match=1" in cold_store and "|write_beats=1|" in cold_store
            and "|read_beats=0|" in cold_readback and "|match=0" in cold_readback):
        return {
            "stage": "simulation",
            "hypothesis": "A cold byte store writes through successfully, but the following cache hit returns "
                          "the old byte. The allocated cache line did not merge the accepted store lanes.",
            "confidence": "high",
            "files": [dcache_path],
            "symbols": ["refill_active", "req_we", "req_word", "req_be", "req_wdata"],
            "constraints": [
                "Merge the saved byte lanes into the selected word of the completed refill before installing "
                "the line. Use the assembled final-beat word when the store targets the final beat.",
                "Preserve the other three words and unselected lanes. Still write through once and respond "
                "only after external write acceptance. Leave hit-store and load behavior intact.",
            ],
            "verification_probe": "dcache_lane_observation",
            "verification": "Cold byte-store readback hits and returns the merged word without another refill.",
        }
    lsu_path = "rtl/load_store_unit.sv"
    lsu = files.get(lsu_path, "")
    lsu_probe = evidence.get("l3_lsu_completion_observation") or {}
    lsu_feedback = any(location.startswith(lsu_path + ":")
                       for loop in structural.get("loops", ()) for location in loop["locations"])
    if ((lsu_feedback or lsu_probe.get("status") == "timeout")
            and re.search(r"\bassign\s+cpu_req_valid\s*=\s*launch\s*;", lsu)
            and re.search(r"\bassign\s+launch\s*=[^;]*!cpu_req_valid\b", lsu)):
        return {
            "stage": "simulation",
            "hypothesis": "LSU drives cpu_req_valid from launch, and launch immediately negates "
                          "cpu_req_valid. The two continuous assignments oscillate.",
            "confidence": "high",
            "files": [lsu_path],
            "symbols": ["launch", "cpu_req_valid", "req_fire", "resp_fire"],
            "constraints": [
                "Use a registered outstanding bit to inhibit launch after the request handshake. "
                "Keep the head payload and request valid stable until ready. Set outstanding on "
                "req_fire, clear it on completion, reset or squash. Qualify resp_fire with the "
                "outstanding request or a same-edge request acceptance for a zero-wait response.",
                "Keep store completion, signed loads and queue port directions intact.",
            ],
            "verification_probe": "l3_lsu_completion_observation",
            "verification": "Yosys reports no feedback and the standalone store/load probe completes.",
        }
    if structural.get("loops"):
        loop = structural["loops"][0]
        paths = list(dict.fromkeys(location.rsplit(":", 1)[0] for location in loop["locations"]))
        if paths and all(path in files for path in paths):
            if ({top_path, rename_path} <= set(paths)
                    and re.search(r"assign\s+iq_issue_ready\s*=[^;]*!squash", top)
                    and re.search(r"assign\s+control_resolve\s*=\s*int_issue_fire", top)
                    and re.search(r"if\s*\(iq_rs[12]_idx\s*==\s*alu_prd\)[^;]*=\s*alu_value", rename)):
                return {
                    "stage": "simulation",
                    "hypothesis": "The resolving instruction's squash feeds back into its own issue "
                                  "ready, and Rename also forwards the current ALU output into that "
                                  "same ALU's operand reads. Yosys measures the resulting feedback.",
                    "confidence": "high",
                    "files": [top_path, rename_path],
                    "symbols": ["iq_issue_ready", "int_issue_fire", "squash", "alu_wb", "iq_rs1_value"],
                    "constraints": [
                        "Compute iq_issue_ready from halt, ROB eligibility and LSU arbitration, without "
                        "squash. Use iq_issue_valid && iq_issue_ready as the only IQ consume and ALU complete event.",
                        "Drive integer operand reads from the registered PRF. Remove the current ALU "
                        "value bypass to its own operand ports, while preserving the registered writeback lanes.",
                        "The resolving ROB head still completes. Keep squash on sequential younger-state recovery.",
                    ],
                    "verification_probe": "logic_loop_observation",
                    "verification": "The measured combinational loop disappears and IQ consumption still matches completion.",
                }
            sources = "\n".join(loop.get("source", {}).values())
            fetch_feedback = (
                {top_path, "rtl/if_stage.sv", "rtl/hazard_unit.sv"} <= set(paths)
                and "icache_stall" in sources and "if_stall" in sources and "req_valid" in sources
            )
            if fetch_feedback:
                return {
                    "stage": "simulation",
                    "hypothesis": "The I-cache request feeds back through cache stall into the IF request's own "
                                  "enable. Separate downstream pipeline hold from the cache request handshake. "
                                  "Keep IF's request valid driven by the available request, redirect/halt and its "
                                  "registered pending state. A cache wait may affect acceptance through ready.",
                    "confidence": "high",
                    "files": paths,
                    "constraints": [
                        "Remove the request-derived icache_stall term from the hold that qualifies this same IF valid. "
                        "Preserve load-use and halt holds and the accepted request PC.",
                        "If IF uses a pending response slot, capture instruction data on the same response edge as "
                        "the stored request PC and epoch, and read the captured data when the slot advances.",
                        "For any producer net already declared in the top, prefer a direct named instance binding. "
                        "Place any necessary continuous assignment after the declarations of all signals it uses.",
                    ],
                    "verification_probe": "logic_loop_observation",
                    "verification": "The IF request/stall cycle is gone and normal request acceptance remains possible.",
                }
            return {
                "stage": "simulation",
                "hypothesis": "Yosys elaboration proved combinational feedback at " + ", ".join(loop["locations"])
                    + ". Trace the inputs and outputs at those statements. Break the feedback at its owning state "
                      "and preserve a defined reset value and a driver on each consumer.",
                "confidence": "high",
                "files": list(dict.fromkeys([*paths, "rtl/rv32i5.sv"])) if "rtl/rv32i5.sv" in files else paths,
                "verification_probe": "logic_loop_observation",
                "verification": "The reported SCC disappears without adding an undriven control net.",
            }
    if l3_load_dispatches_to_iq(top):
        return {
            "stage": "simulation",
            "hypothesis": "Top decodes d_load but routes only stores to LSU. Loads instead enter IQ "
                          "and cannot receive data from the D-cache.",
            "confidence": "high",
            "files": [top_path],
            "symbols": ["d_load", "d_store", "iq_enqueue", "lsu_enqueue", "queue_enq_ready"],
            "constraints": [
                "Use the same d_load || d_store condition for LSU readiness and enqueue, and exclude "
                "both from IQ enqueue. Preserve the ROB tag, rename acceptance and LSU store flag.",
            ],
            "verification": "The cold load issues through LSU and gets its writeback from D-cache.",
        }
    alu_path = "rtl/alu.sv"
    alu = files.get(alu_path, "")
    failure_checks = (*evidence.get("first_checks", ()), *(evidence.get("checks") or {}).get("groups", ()))
    wb_path = "rtl/wb_stage.sv"
    wb_source = files.get(wb_path, "")
    if (any("应等于停机时 x10" in row["detail"] for row in failure_checks)
            and re.search(r"\bassign\s+wb_x10_value\s*=\s*32'b0\s*;", wb_source)
            and re.search(r"if\s*\(ecall_commit\)\s*begin\s*halted\s*<=\s*1'b1\s*;"
                          r"\s*exit_code\s*<=\s*32'b0", wb_source)):
        return {
            "stage": "simulation",
            "hypothesis": "WB hardcodes both wb_x10_value and the ECALL exit code to zero. "
                          "Teacher debug reads already observe the nonzero committed x10 value.",
            "confidence": "high",
            "files": [wb_path],
            "symbols": ["wb_x10_value", "wb_reg_write", "wb_rd", "wb_wdata", "exit_code"],
            "constraints": [
                "Track architectural x10 in a WB register reset to zero and updated only on "
                "wb_reg_write && wb_rd == 5'd10, using wb_wdata. Drive wb_x10_value from that "
                "register and capture it at ECALL commit. Leave debug address and retirement unchanged.",
            ],
            "verification": "ECALL exit code matches the committed x10, regardless of debug address.",
        }
    reset_checks = [row for row in failure_checks if row["name"] in ("power_on_reset", "t01_mid_reset")]
    if (any("dmem_valid must be 0" in row["detail"] or "复位期间出现 DMEM 写" in row["detail"]
            for row in reset_checks)
            and re.search(r"assign\s+dmem_valid\s*=\s*dc_mem_req_valid\s*&&\s*!halted_r\s*;", top)):
        return {
            "stage": "simulation",
            "hypothesis": "The external memory valid signal is qualified by halt but remains active "
                          "when reset is asserted before a rising edge. The teacher sees a write during reset.",
            "confidence": "high",
            "files": [top_path],
            "symbols": ["rst_n", "imem_valid", "dmem_valid", "halted_r"],
            "constraints": [
                "Qualify both external bus valid outputs with rst_n as well as the existing halt and "
                "cache request conditions. Preserve ready and response wiring outside reset.",
            ],
            "teacher_checks": list(dict.fromkeys(row["name"] for row in reset_checks)),
            "verification": "Both external valids are zero before the first reset edge and mid-reset writes stop.",
        }
    if (any(row["name"] in ("t02_alu_r", "t03_alu_imm") and "0xffffffff" in row["detail"]
            for row in failure_checks)
            and re.search(r"\?\s*\(\$signed\([^)]*\)\s*>>>[^:;]+:\s*\([^;]+\s>>\s", alu)):
        return {
            "stage": "simulation",
            "hypothesis": "The arithmetic and logical shifts share a conditional expression. The unsigned "
                          "logical arm forces unsigned context, so an arithmetic shift of a negative word "
                          "fills with zeros. The original ALU test observes a zero-filled result.",
            "confidence": "high",
            "files": [alu_path],
            "symbols": ["alu_res"],
            "constraints": [
                "Use separate procedural if/else assignments for SRA/SRAI versus SRL/SRLI. In the arithmetic "
                "assignment keep the signed left operand and the same shift amount. Preserve other operations."
            ],
            "teacher_checks": ["t02_alu_r", "t03_alu_imm"],
            "verification": "The arithmetic register and immediate shifts preserve the sign bit.",
        }
    lsu_path = "rtl/load_store_unit.sv"
    lsu = files.get(lsu_path, "")
    if ("store_completion" in probes.get("l3_lsu_completion_observation", ())
            and re.search(r"assign\s+complete_valid\s*=\s*resp_fire\s*&&\s*!out_store", lsu)):
        return {
            "stage": "simulation",
            "hypothesis": "The store reaches external memory but its response is excluded from "
                          "complete_valid. The ROB can never retire the store at its head.",
            "confidence": "high",
            "files": [lsu_path],
            "symbols": ["complete_valid", "complete_prd", "resp_fire", "out_store", "load_data"],
            "constraints": [
                "Every accepted load and store response must complete the saved ROB tag. For a "
                "store drive PRD zero and value zero, preserving its external write and byte lanes.",
                "For LB and LH replicate only the sign bit of the selected byte or halfword; "
                "LBU and LHU zero-extend.",
            ],
            "verification_probe": "l3_lsu_completion_observation",
            "verification": "The store completes with PRD zero and a concurrent enqueue leaves a usable signed load.",
        }
    store_checks = [row for row in failure_checks
                    if re.search(r"(?:SB|SH) 未复制 dmem_wdata", row["detail"])]
    if (store_checks and re.search(r"\bassign\s+store_data\s*=\s*lsu_rs2_value\s*;", lsu)
            and re.search(r"\bassign\s+cpu_req_wdata\s*=\s*store_data\s*;", lsu)):
        return {
            "stage": "simulation",
            "hypothesis": "The teacher bus sees the raw source register on SB/SH. The LSU sets the lane "
                          "enable but passes the word unchanged, so the selected lane can contain another byte.",
            "confidence": "high",
            "files": [lsu_path],
            "symbols": ["store_data", "cpu_req_wdata", "funct3_q"],
            "constraints": [
                "For SB replicate source[7:0] four times, and for SH replicate source[15:0] twice. "
                "Keep SW unchanged, the existing original byte address and the existing byte enables."
            ],
            "teacher_checks": list(dict.fromkeys(row["name"] for row in store_checks)),
            "verification": "The original byte and halfword stores pass both bus-lane and memory assertions.",
        }
    training_checks = [row for row in failure_checks if row["name"] == "predictor_training"]
    if (training_checks and re.search(r"\bassign\s+train_taken\s*=\s*1'b0\s*;", top)
            and re.search(r"\bassign\s+train_target\s*=\s*commit_pc\s*\+\s*32'd4\s*;", top)
            and re.search(r"\btaken_target\s*\(\s*alu_taken_target\s*\)", top)):
        return {
            "stage": "simulation",
            "hypothesis": "The top trains every control instruction with taken=0 and the sequential address. "
                          "The teacher observes repeated mispredictions on the taken loop.",
            "confidence": "high",
            "files": [top_path],
            "symbols": ["train_valid", "train_pc", "train_target", "train_taken", "control_resolve"],
            "constraints": [
                "Train on control_resolve using iq_issue_pc, alu_taken, alu_taken_target, and the instruction "
                "type decoded from iq_issue_insn. Do not train a second time at commit.",
                "The BTB target is the taken target, not the actual next PC on an untaken branch. "
                "Preserve the current mispredict and fetch metadata wiring.",
            ],
            "teacher_checks": ["predictor_training"],
            "verification_probe": "l3_predictor_training_observation",
            "verification": "The trained loop stays within the teacher mispredict limit and retires every instruction.",
        }
    memory_queue = (evidence.get("l3_memory_queue_observation") or {}).get("samples", ())
    if (any("|retired=2|" in row and "|halted=0|" in row and "|reads=4|" in row for row in memory_queue)
            and re.search(r"if\s*\(launch_fire\)\s*begin\s*valid_q\[0\]\s*<=\s*valid_q\[1\]", lsu)
            and re.search(r"else\s+if\s*\(resp_fire\)\s*begin\s*valid_q\[0\]\s*<=\s*valid_q\[1\]", lsu)):
        return {
            "stage": "simulation",
            "hypothesis": "The cold load retires but the following byte load never retires. The queue shifts "
                          "once at request acceptance and again at response, discarding a waiting entry.",
            "confidence": "high",
            "files": [lsu_path],
            "symbols": ["launch_fire", "resp_fire", "valid_q", "req_addr_q"],
            "constraints": [
                "Retain slot zero through its outstanding request; pop it once on response. A simultaneous "
                "enqueue must target the correct slot after that pop, including when the second slot was empty.",
                "Use saved req_addr_q bits for load byte/halfword extraction. Replicate byte and halfword store "
                "data per the external bus contract, and preserve the accepted request's ROB tag and PRD.",
            ],
            "verification_probe": "l3_memory_queue_observation",
            "verification": "Both loads retire, the byte load produces 41, and exactly four external reads occur.",
        }
    memory_timeouts = [row for row in failure_checks if row["name"] in
                       ("t06_load_store", "t06b_neg_imm", "t08_store_fwd") and "等待 halted 超时" in row["detail"]]
    if (memory_timeouts and re.search(r"if\s*\(enqueue\)\s*begin\s*if\s*\(!slot0_free\)", lsu)
            and re.search(r"if\s*\(resp_fire\)\s*begin[^;]*;\s*lsq_valid\[0\]\s*<=\s*1'b0", lsu)):
        return {
            "stage": "simulation",
            "hypothesis": "An enqueue concurrent with the LSU response uses the pre-pop occupancy. "
                          "When slot 1 was empty, the new entry is put there after slot 0 is popped, "
                          "leaving a queue hole that prevents launch.",
            "confidence": "high",
            "files": [lsu_path],
            "symbols": ["resp_fire", "slot0_free", "lsq_valid", "cpu_req_valid", "req_pending"],
            "constraints": [
                "Select slot 0 when it was empty or when the response pops it and slot 1 was empty. "
                "Preserve the existing shift and all payload fields.",
                "Use launch to drive request valid until acceptance. req_pending denotes a request "
                "that already handshook, so it must not reassert CPU request valid before response.",
            ],
            "teacher_checks": list(dict.fromkeys(row["name"] for row in memory_timeouts)),
            "verification": "Concurrent enqueue and response leave a usable LSU head and memory tests halt.",
        }
    if (memory_timeouts and re.search(r"assign\s+enq_ready\s*=\s*!valid_q\[1\]", lsu)
            and re.search(r"if\s*\(resp_fire\)\s*begin\s*valid_q\[0\]\s*<=\s*valid_q\[1\]", lsu)
            and re.search(r"if\s*\(enqueue\s*&&\s*enq_ready\)\s*begin\s*"
                          r"if\s*\(resp_fire\)\s*begin\s*valid_q\[1\]\s*<=\s*1'b1", lsu)):
        return {
            "stage": "simulation",
            "hypothesis": "When a response and enqueue coincide, slot 1 must have been empty for enq_ready. "
                          "The pop clears slot 0, but the new entry is written to slot 1. This leaves a hole "
                          "and the LSU launch logic only reads slot 0.",
            "confidence": "high",
            "files": [lsu_path],
            "symbols": ["resp_fire", "enq_ready", "valid_q"],
            "constraints": [
                "Route an accepted enqueue to slot zero when resp_fire and slot one was empty, updating "
                "every field together. Keep one pop on response and preserve the completion and lane logic.",
            ],
            "teacher_checks": list(dict.fromkeys(row["name"] for row in memory_timeouts)),
            "verification": "The affected memory programs complete without an LSU queue hole.",
        }
    store_samples = (evidence.get("l3_store_writeback_observation") or {}).get("samples", ())
    if (any("|retired=6|" in row and "|exit=00000013|register=00000000|" in row
            and "|writes=1|" in row for row in store_samples)
            and re.search(r"assign\s+new_prd\s*=\s*alloc_prd\s*;", rename)):
        return {
            "stage": "simulation",
            "hypothesis": "An older store completes after the younger x3 write. x10 already copied 19 "
                          "from x3, but committed x3 reads zero. Rename exports the next free PR for "
                          "the store, so store completion overwrites the same PR given to the x3 writer.",
            "confidence": "high",
            "files": [rename_path],
            "symbols": ["new_prd", "can_allocate", "alu_wb", "mem_wb"],
            "constraints": [
                "Export PR0 whenever writes_rd is false or rd is zero. A nonwriter must remain "
                "dispatchable even when no destination PR is available.",
                "Ignore PR0 on both writeback ports for PRF data and readiness. Preserve legitimate "
                "nonzero integer and memory writebacks and all checkpoint state.",
            ],
            "verification_probe": "l3_store_writeback_observation",
            "verification": "The delayed store performs one bus write and x3 stays 19 through six ordered retirements.",
        }
    link_samples = (evidence.get("l3_link_recovery_observation") or {}).get("samples", ())
    if (any("|retired=4|" in row and "|ordered=1|" in row and "|exit=00000003|" in row for row in link_samples)
            and re.search(r"cp_map\[dispatch_tag\]\[i\]\s*<=\s*spec_map\[i\]", rename)):
        return {
            "stage": "simulation",
            "hypothesis": "The JAL retires, but its target reads the pre-JAL value of the link register. "
                          "The checkpoint saves the old RAT while saving the post-allocation free head.",
            "confidence": "high",
            "files": [rename_path],
            "symbols": ["cp_map", "cp_head", "spec_map", "alloc_prd", "do_alloc"],
            "constraints": [
                "On a checkpointed dispatch that allocates a destination, save alloc_prd for the rd entry "
                "and spec_map for all other entries. Save the matching post-allocation head.",
                "Recover the checkpointed head and RAT together. Preserve the current free tail and derive "
                "free occupancy from the modular tail minus restored head, so squashed allocations return.",
                "Export PR0 for a non-writing instruction, including store and branch, and ignore both "
                "writeback lanes when their destination is PR0.",
                "Keep ordinary simultaneous alloc/commit updates, live readiness and the external interfaces.",
            ],
            "verification_probe": "l3_link_recovery_observation",
            "verification": "The JAL target reads link 8 and exits with 11 after four ordered retirements.",
        }
    rename_samples = (evidence.get("l3_rename_reuse_observation") or {}).get("samples", ())
    if (any("|retired=9|" in sample and "|halted=0|" in sample for sample in rename_samples)
            and re.search(r"\bfl_head\s*<=\s*6'd32\s*;", rename)
            and re.search(r"\bfl_tail\s*<=\s*6'd32\s*;", rename)
            and re.search(r"\bfree_list\[fl_tail\]\s*<=\s*alloc_prd", rename)):
        return {
            "stage": "simulation",
            "hypothesis": "Nine straight-line dependent writes retire, then issue stops. Rename initializes "
                          "only slots 32..39 but both pointers start at 32, and allocation writes into the "
                          "free-list tail. At wrap, the allocator can hand out an invalid or already-live PR.",
            "confidence": "high",
            "files": [rename_path],
            "symbols": ["fl_head", "fl_tail", "free_list", "ready", "checkpoint"],
            "constraints": [
                "Use an eight-entry free FIFO with four-bit monotonic head and tail pointers, indexed by their "
                "low three bits. Reset head=0, tail=8, and slots 0..7 to PR32..39. Allocate by reading head "
                "and advancing head. Only commit of a nonzero old PR writes tail and advances it.",
                "Checkpoint accepted controls after their own allocation, saving the resulting RAT and free "
                "head. Recovery at ROB head rewinds allocation but preserves the current free tail and older "
                "commits. Preserve live PR readiness and ignore writes to physical zero.",
                "Retain the port contract, both independent operand read paths, and committed debug values.",
            ],
            "verification_probe": "l3_rename_reuse_observation",
            "verification": "Twelve dependent writes complete and ECALL reports 12 after fourteen ordered retirements.",
        }
    repeated_redirects = (evidence.get("l3_repeated_redirect_observation") or {}).get("samples", ())
    recovery_block = re.search(r"\bif\s*\(recover\)\s*begin\s*for\b", rob)
    rob_leaves_gaps = (recovery_block and re.search(r"assign\s+head\s*=\s*tail\s*-\s*count\[2:0\]", rob)
                       and not re.search(r"\btail\s*<=\s*head\b", rob))
    rename_clears_live = re.search(
        r"\bif\s*\(i\s*>=\s*32\)\s*(?:begin\s*)?(?:ready|rdy|ready_r|ready_q)\[i\]\s*<=\s*1'b0",
        rename,
    )
    if ((rob_leaves_gaps or rename_clears_live)
            and any("|halted=0|" in row and re.search(r"\|mispredicts=[1-9]", row) for row in repeated_redirects)):
        symptoms, constraints, paths = [], [], []
        if rob_leaves_gaps:
            paths.append(rob_path)
            symptoms.append("The ROB clears younger valid bits but leaves tail and occupancy unchanged.")
            constraints.append("On recovery retain and complete the control at ROB head, set tail=head+1 "
                               "and count=1, and prioritize this over ordinary allocation and commit.")
        if rename_clears_live:
            paths.append(rename_path)
            symptoms.append("Rename clears every ready bit for PR32 and above, including committed live sources.")
            constraints.extend([
                "Preserve readiness of live physical mappings on recovery, including committed mappings. "
                "Reallocation clears the ready bit of each newly allocated destination.",
                "Checkpoint the post-dispatch RAT including a JAL/JALR destination and its resulting free head. "
                "Restore free head only, keep current free tail and derive occupancy from their four-bit difference.",
            ])
        return {
            "stage": "simulation",
            "hypothesis": "Control redirects resolve, then retirement stops. " + " ".join(symptoms),
            "confidence": "high",
            "files": paths,
            "symbols": ["recover", "tail", "count", "head", "cp_map", "ready"],
            "constraints": constraints,
            "verification_probe": "l3_repeated_redirect_observation",
            "verification": "The changing-target JALR program retires ten ordered instructions and exits with 8.",
        }
    redirects = (evidence.get("l3_redirect_observation") or {}).get("samples", ())
    if (any("|halted=0|" in row and "|mispredicts=1|" in row and "|if_reads=4|" in row for row in redirects)
            and re.search(r"\bassign\s+ic_cpu_req_valid\s*=\s*~fetch_blocked\s*&\s*~halted_r\s*&\s*~halt_pending\s*;", top)
            and re.search(r"if\s*\(fetch_slot_clear\).*?fetch_slot_valid\s*<=\s*1'b0;.*?"
                          r"if\s*\(fetch_req_fire\).*?fetch_slot_valid\s*<=\s*1'b1;", top, re.S)):
        return {
            "stage": "simulation",
            "hypothesis": "The redirect is observed, but the I bus never fetches the target line. The top can "
                          "accept a fetch on the squash edge and reassert fetch_slot_valid after clearing it. "
                          "The old-epoch response is then discarded while the slot blocks all new requests.",
            "confidence": "high",
            "files": [top_path],
            "symbols": ["fetch_req_fire", "fetch_slot_valid", "squash_younger", "redirect", "halt_pending"],
            "constraints": [
                "Suppress fetch request acceptance and dispatch when redirect/squash is active. On redirect "
                "discard both fetch and decode slots, clear halt_pending for squashed ECALL, update PC and epoch "
                "and make squash dominate same-edge stale response capture.",
                "Do not suppress an already asserted external memory beat before ready.",
            ],
            "verification_probe": "l3_redirect_observation",
            "verification": "The jump target is fetched, four instructions retire in order, and exit is 7.",
        }
    alloc_trace = (evidence.get("pipeline_observation") or {}).get("l3_accepted_transfers") or {}
    iq_path = "rtl/issue_queue.sv"
    iq = files.get(iq_path, "")
    iq_ready = re.search(r"\.issue_ready\s*\(\s*(\w+)\s*\)", module_instance_body(top, "issue_queue"))
    if (iq_ready and f"rv32i5.{iq_ready[1]}" in undriven
            and re.search(r"\binput\s+(?:(?:logic|wire|reg)\s+)?issue_ready\b", iq)
            and alloc_trace.get("allocate_at_edge", {}).get("count", 0) > 0
            and alloc_trace.get("issue_at_edge", {}).get("count") == 0):
        net = iq_ready[1]
        return {
            "stage": "simulation",
            "hypothesis": f"IQ.issue_ready is an input. Its top-level binding {net} has no driver. "
                          "The core allocates ROB entries but cannot accept an integer issue.",
            "confidence": "high",
            "files": [top_path],
            "symbols": [net, "issue_ready"],
            "constraints": [
                "Drive the existing net from integer execute acceptance and genuine LSU launch arbitration. "
                "Keep it independent of IQ issue_valid so valid and ready cannot form a combinational loop.",
            ],
            "verification": "Yosys finds no undriven issue-ready net and the execution probe sees integer issue.",
        }
    if (alloc_trace.get("allocate_at_edge", {}).get("count", 0) > 0
            and alloc_trace.get("issue_at_edge", {}).get("count") == 0
            and re.search(r"\b(?:localparam|parameter)(?:\s+(?:int|integer))?\s+DEPTH\s*=\s*8\b", iq)
            and re.search(r"\bfor\s*\([^;]+;[^;]+<\s*DEPTH\s*\[\s*2\s*:\s*0\s*\]", iq)):
        return {
            "stage": "simulation",
            "hypothesis": (
                "ROB allocations are observed but the IQ never issues. Its for-loop bound DEPTH[2:0] "
                "truncates the declared depth 8 to zero, so neither selection nor affected sequential loops run."
            ),
            "confidence": "high",
            "files": [iq_path],
            "constraints": [
                "Use full DEPTH for each queue loop, with a distinct integer loop counter for each process. "
                "Keep the actual slot index and ROB tags three bits. Preserve the existing issue/dispatch ports."
            ],
            "verification_probe": "execution_observation",
            "verification": "The IQ selects an enqueued ready entry and the core retires an integer instruction.",
        }
    lsu_request = alloc_trace.get("lsu_issue_at_edge") or {}
    lsu_bindings = l3_connections(files).get("load_store_unit", [])
    req, ready = (
        next((match[1] for pin in lsu_bindings if (match := re.fullmatch(rf"\.{port}\(\s*(\w+)\s*\)", pin))), None)
        for port in ("cpu_req_valid", "cpu_req_ready")
    )
    issue_checks = (evidence.get("checks") or {}).get("groups", ())
    if (lsu_request.get("count", 0) > 0 and req and ready
            and any("missing issue evidence" in row["detail"] for row in issue_checks)):
        # Ignore the reset assignment when examining the running event.
        events = re.findall(r"\bdbg_ooo_issue\s*(?:<=|=)\s*([^;]+);", top)
        runtime_events = [expr for expr in events if expr.strip() not in ("0", "1'b0", "'0")]
        if runtime_events and not any(re.search(rf"\b{re.escape(req)}\b", expr) for expr in runtime_events):
            return {
                "stage": "simulation",
                "hypothesis": (
                    f"The LSU accepted a memory operation at {lsu_request['first'][0]}, but the top's "
                    f"dbg_ooo_issue event only observes {runtime_events[0].strip()}. The memory launch "
                    "is independent of the integer queue and must contribute its own accepted issue event."
                ),
                "confidence": "high",
                "files": [top_path],
                "symbols": ["dbg_ooo_issue", req, ready],
                "constraints": [
                    f"Preserve the existing integer event and OR in the actual LSU acceptance ({req} && {ready}) "
                    "under the existing reset/halt policy. Preserve the separate integer and memory pipelines "
                    "and their completion paths. Keep bypass qualified by true overlap."
                ],
                "verification": "The unchanged teacher test detects the integer and memory issue events.",
            }
    rob_instance = module_instance_body(top, "reorder_buffer")
    rename_instance = module_instance_body(top, "rename_unit")
    if (alloc_trace.get("allocate_at_edge", {}).get("count") == 0
            and re.search(r"\.alloc_ready\s*\(\s*rob_alloc_ready\s*\)", rob_instance)
            and re.search(r"\.alloc_rob_ready\s*\(\s*alloc_rob_ready\s*\)", rename_instance)
            and not re.search(r"\bassign\s+alloc_rob_ready\s*=", top)):
        return {
            "stage": "simulation",
            "hypothesis": "ROB produces rob_alloc_ready but Rename reads a separate, undriven alloc_rob_ready. "
                          "The pre-edge trace confirms zero accepted allocations.",
            "confidence": "high",
            "files": [top_path],
            "constraints": [
                "Bind Rename's existing .alloc_rob_ready input directly to rob_alloc_ready. "
                "Preserve the same ROB allocation tag and same-edge queue acceptance.",
            ],
            "verification_probe": "execution_observation",
            "verification": "The empty ROB accepts the first decoded instruction and dispatch reaches issue.",
        }
    if (re.search(r"\bassign\s+alloc_ready\s*=\s*\(count\s*!=\s*3'd8\)", rob)
            and re.search(r"\blogic\s*\[2:0\]\s*count\s*;", rob)):
        rename_path = "rtl/rename_unit.sv"
        rename = files.get(rename_path, "")
        false_ready = (
            re.search(r"\bassign\s+disp_ready\s*=[^;]*;", rename)
            and re.search(r"\bassign\s+accept\s*=\s*disp_valid\s*&&\s*disp_ready\s*&&\s*rob_alloc_ready", rename)
            and "rob_alloc_ready" not in re.search(r"\bassign\s+disp_ready\s*=[^;]*;", rename)[0]
        )
        constraints = [
            "Use four internal occupancy bits so count=8 is representable. Keep the frozen external port widths. "
            "Compare against 4'd8, and handle simultaneous allocation and retirement without losing either count update.",
        ]
        if false_ready:
            constraints.append(
                "Make the visible disp_ready include rob_alloc_ready, so IF/ID clears only when the ROB and "
                "selected queue accepted the same instruction. Preserve ready on each queue path."
            )
        return {
            "stage": "simulation",
            "hypothesis": "3'd8 truncates to zero. The empty ROB therefore reports not ready, so decode can "
                          "show valid repeatedly without allocating a single ROB entry.",
            "confidence": "high",
            "files": [rob_path, rename_path] if false_ready else [rob_path],
            "constraints": constraints,
            "verification_probe": "execution_observation" if probes.get("execution_observation") else None,
            "verification": "The reset-empty ROB accepts dispatch and the first integer instruction retires.",
        }
    if (any(name.endswith(".alloc_idx") for name in undriven)
            and re.search(r"\bassign\s+alloc_rob_idx\s*=\s*tail\s*;", rob)
            and re.search(r"\bent_valid\[alloc_idx\]\s*<=\s*1'b1", rob)):
        return {
            "stage": "simulation",
            "hypothesis": "ROB exposes tail as alloc_rob_idx, but writes entry arrays via an undriven alloc_idx. "
                          "Dispatch and the issue tag therefore advance without allocating the corresponding ROB entry.",
            "confidence": "high",
            "files": [rob_path],
            "constraints": [
                "Drive alloc_idx from the same tail tag as alloc_rob_idx, keeping the existing alloc_valid/ready "
                "condition and port contract. Leave IQ and LSU tag paths unchanged.",
            ],
            "verification_probe": "logic_loop_observation",
            "verification": "ROB allocation uses the same driven tag as dispatch, and the undriven net disappears.",
        }
    l3_samples = (evidence.get("execution_observation") or {}).get("samples", ())
    if (any("|scenario=3|" in row and re.search(r"first_retirements=00000000:[0-9a-f]{8},00000000:", row)
            for row in l3_samples)
            and re.search(r"\bfetch_pc\s*<=\s*fetch_pred_next\s*;", top)
            and re.findall(r"\bfetch_pred_next\s*<=\s*([^;]+);", top) == ["32'd0"]
            and not re.search(r"\bassign\s+fetch_pred_next\s*=", top)):
        return {
            "stage": "simulation",
            "hypothesis": "The dispatch edge updates fetch_pc from fetch_pred_next, but fetch_pred_next is assigned "
                          "only in reset. The probe observes repeated dispatch and retirement of PC zero.",
            "confidence": "high",
            "files": [top_path],
            "constraints": [
                "Capture the predictor next PC (target on taken hit, otherwise fetch_pc + 4) on the I-cache "
                "request acceptance edge. Retain it until that accepted instruction dispatches. Feed the saved "
                "prediction to decode and use it once to advance PC. Keep redirect priority and cache response pairing.",
            ],
            "verification_probe": "execution_observation",
            "verification": "After PC 0 retires, the next PC advances to the cold load at PC 4.",
        }
    if (any("|scenario=3|" in row and "|retired=4|last_pc=0000000c|ordered=1|halted=0|" in row
            for row in l3_samples)
            and re.search(r"\bent_ecall\[alloc_idx\]\s*<=\s*alloc_is_ecall", rob)
            and re.search(r"\bent_done\[alloc_idx\]\s*<=\s*1'b0", rob)):
        return {
            "stage": "simulation",
            "hypothesis": "The four instructions before ECALL retire in order, but ECALL does not use a "
                          "functional unit and ROB allocation initializes its done bit to zero.",
            "confidence": "high",
            "files": [rob_path],
            "constraints": [
                "At atomic allocation, initialize ent_done to alloc_is_ecall. Retire ECALL only "
                "as the valid ROB head after older stores complete, preserving the existing commit path.",
            ],
            "verification_probe": "execution_observation",
            "verification": "The fifth instruction, ECALL, retires once and the core halts.",
        }
    predictor_path = "rtl/branch_predictor.sv"
    predictor = files.get(predictor_path, "")
    if ("training_index" in (probes.get("l2_predictor_table_observation") or ())
            and re.search(r"\bassign\s+bht_val\s*=\s*bht\[lookup_idx\]", predictor)
            and re.search(r"\bbht_next\s*=\s*bht_val\s*;", predictor)
            and re.search(r"\bbht\[train_idx\]\s*<=\s*bht_next\s*;", predictor)):
        return {
            "stage": "simulation",
            "hypothesis": "BHT training writes train_idx using the old counter from lookup_idx. "
                          "When EX trains a branch while IF looks up a different PC, it updates "
                          "the branch with an unrelated counter's history.",
            "confidence": "high",
            "files": [predictor_path],
            "constraints": [
                "Keep bht_val for lookup. Read bht[train_idx] as the old counter for all saturation "
                "comparisons and arithmetic in bht_next. Preserve BTB tag checking and JAL/JALR training.",
            ],
            "verification_probe": "l2_predictor_table_observation",
            "verification": "Train and lookup at distinct indices, then check both direction changes and the tag alias.",
        }
    ex_source = files.get("rtl/ex_stage.sv", "")
    if (set(probes.get("l2_branch_direction_observation") or ()) == {"mispredicts"}
            and re.search(r"\bassign\s+ex_bp_train_target\s*=\s*actual_next_pc\s*;", ex_source)
            and re.search(r"\bassign\s+branch_target\s*=", ex_source)
            and "btb_target[train_idx] != train_target" in predictor):
        return {
            "stage": "simulation",
            "hypothesis": "EX sends the fall-through PC as the BTB target on a not-taken outcome, "
                          "and the predictor overwrites the existing taken target.",
            "confidence": "high",
            "files": ["rtl/ex_stage.sv"],
            "symbols": ["ex_bp_train_target", "branch_target", "actual_next_pc"],
            "constraints": [
                "Send branch_target as the training target for conditional branches regardless of direction. "
                "Preserve actual_next_pc for redirect and comparison. Preserve JAL and JALR targets.",
            ],
            "verification_probe": "l2_branch_direction_observation",
            "verification": "The alternating-direction branch reaches the expected misprediction count.",
        }
    if (any(row["name"] == "t02_alu_r" for row in failure_checks)
            and re.search(r"\bassign\s+alu_b\s*=\s*[^;]*\?\s*id_ex_imm\s*:\s*id_ex_imm\s*;", ex_source)
            and re.search(r"\bassign\s+rs2_fwd\s*=", ex_source)):
        return {
            "stage": "simulation",
            "hypothesis": "Both arms of EX's ALU B mux select the immediate. The register operand is "
                          "available after forwarding but never reaches register arithmetic or shifts.",
            "confidence": "high",
            "files": ["rtl/ex_stage.sv"],
            "symbols": ["alu_b", "rs2_fwd", "id_ex_insn"],
            "constraints": [
                "When id_ex_insn[6:0] is 7'b0110011, use rs2_fwd as ALU B and rs2_fwd[4:0] "
                "as the shift amount. Keep id_ex_imm for immediate operations and memory addresses. "
                "Immediate shifts use the encoded shamt. Preserve branch comparison and jump targets.",
            ],
            "teacher_checks": ["t02_alu_r"],
            "verification": "The original register ALU suite passes arithmetic, logic, comparisons and shifts.",
        }
    if ("rtl/branch_predictor.sv" in files
            and re.search(r"assign\s+ex_redirect_valid\s*=\s*ctrl_taken\s*&\s*~ex_mem_hold", ex_source)
            and re.search(r"assign\s+ex_bp_mispredict\s*=\s*ex_bp_resolve", ex_source)):
        return {
            "stage": "simulation",
            "hypothesis": "EX redirects on taken outcomes only. A branch that was predicted taken "
                          "but resolves not taken must also flush the wrong path.",
            "confidence": "high",
            "files": ["rtl/ex_stage.sv"],
            "symbols": ["ex_redirect_valid", "ex_bp_mispredict", "ex_bp_target"],
            "constraints": [
                "Use ex_bp_mispredict as ex_redirect_valid. Compare the actual next PC against the "
                "captured predicted next PC, using PC+4 when no prediction is valid.",
                "Train the BTB with branch_target for conditional branches regardless of direction. "
                "JAL and JALR train their actual targets. Preserve the accepted resolve event.",
            ],
            "verification_probe": "l2_branch_direction_observation",
            "verification": "Both directions recover correctly and the branch training probe completes.",
        }
    if (re.search(r"assign\s+rs1_fwd\s*=\s*fwd_a_sel\s*\?[^;]*fwd_b_sel\s*\?", ex_source)
            and re.search(r"assign\s+rs2_fwd\s*=\s*fwd_b_sel\s*\?[^;]*fwd_a_sel\s*\?", ex_source)):
        return {
            "stage": "simulation",
            "hypothesis": "EX uses the other operand's one-bit forwarding flag to choose MEM/WB. "
                          "The flag only says that its own operand matches either producer. A match on "
                          "rs1 can therefore overwrite rs2, and register ALU operations also use the immediate.",
            "confidence": "high",
            "files": [top_path, "rtl/ex_stage.sv"],
            "symbols": ["fwd_a_sel", "fwd_b_sel", "rs1_fwd", "rs2_fwd", "alu_b"],
            "constraints": [
                "Preserve the current one-bit ports. In top compute a dedicated resolved value for "
                "each EX operand using the corresponding ID/EX rs index. Prefer a valid matching "
                "non-load EX/MEM producer, then a valid matching MEM/WB producer, then captured register data.",
                "Bind the two resolved values only to EX's existing ex_mem_fwd_data and mem_wb_fwd_data ports, "
                "respectively. Keep the true MEM/WB forwarding signal unchanged for all other consumers.",
                "Use dedicated internal wires for the EX-only bindings. Leave the existing producer net assignments "
                "and the MEM stage instance unchanged. For R-type operations select "
                "rs2_fwd for alu_b, and preserve immediate and PC operands for other opcodes.",
            ],
            "verification": "ALU register arithmetic and dependent branch comparisons use the intended operands.",
        }
    mem_source = files.get("rtl/mem_stage.sv", "")
    if (any(row["name"] == "t07_load_use" for row in failure_checks)
            and re.search(r"\bassign\s+mem_busy\s*=\s*txn_active\s*&&\s*!complete\s*;", mem_source)
            and re.search(r"\bassign\s+issue\s*=\s*need_txn\s*&&\s*!txn_active", mem_source)):
        return {
            "stage": "simulation",
            "hypothesis": "The first cycle of a memory operation can overwrite EX/MEM, because mem_busy "
                          "only becomes active after request acceptance.",
            "confidence": "high",
            "files": ["rtl/mem_stage.sv"],
            "symbols": ["mem_busy", "need_txn", "txn_active", "complete", "issue"],
            "constraints": [
                "Assert mem_busy when need_txn or txn_active is true, until complete. Allow the request "
                "to be accepted while EX/MEM is held. Release on the response capture edge and retain "
                "the existing load extension, saved metadata and MEM/WB valid logic.",
            ],
            "teacher_checks": ["t07_load_use", "t08_store_fwd"],
            "verification": "Back-to-back memory and dependent instructions each transfer once with correct data.",
        }
    if (probe_failures("dcache_lane_observation", evidence) == []
            and any("DMEM 写事务数期望" in row["detail"] for row in failure_checks)
            and re.search(r"\bassign\s+mem_busy\s*=\s*req_issued\s*&\s*req_is_mem\s*;", mem_source)
            and re.search(r"\bassign\s+resp_accept\s*=\s*dcache_cpu_resp_valid\s*&\s*req_issued\s*;", mem_source)):
        return {
            "stage": "simulation",
            "hypothesis": "The standalone D-cache now passes store and readback tests, but the CPU "
                          "issues too many writes. MEM only holds EX/MEM after accepting the request, "
                          "then holds it even on resp_accept, so a stale EX/MEM memory slot can reissue.",
            "confidence": "high",
            "files": ["rtl/mem_stage.sv"],
            "symbols": ["mem_busy", "mem_op", "req_issued", "resp_accept", "can_issue"],
            "constraints": [
                "Assert mem_busy from the first cycle mem_op is present until its matching response; "
                "deassert it on resp_accept so EX/MEM releases that exact payload as MEM/WB captures it. "
                "Keep the request valid independent of the EX/MEM hold and allow only one handshake "
                "per accepted payload. Retain the existing saved request metadata and lane extraction.",
            ],
            "teacher_checks": ["t06_load_store", "t06b_neg_imm", "t08_store_fwd"],
            "verification": "Teacher store transaction counts match and the standalone cache remains passing.",
        }
    if ("ordered" in (probes.get("execution_observation") or ())
            and re.search(r"\bassign\s+mem_busy\s*=\s*\(state_q\s*!=\s*M_IDLE\)\s*;", mem_source)
            and re.search(r"load_data_q\s*<=\s*dcache_cpu_resp_rdata", mem_source)
            and re.search(r"mem_wb_load_data\s*<=\s*load_ext", mem_source)
            and re.search(r"assign\s+mem_hold\s*=\s*1'b0", files.get("rtl/hazard_unit.sv", ""))):
        return {
            "stage": "simulation",
            "hypothesis": "MEM holds EX/MEM only after the cache accepts a request. On the acceptance "
                          "edge EX can overwrite the load's PC and destination. On the response edge "
                          "MEM also reads load_data_q before the response is registered.",
            "confidence": "high",
            "files": ["rtl/mem_stage.sv"],
            "symbols": ["mem_busy", "dcache_cpu_req_valid", "dcache_cpu_resp_valid", "load_ext"],
            "constraints": [
                "Hold a valid EX/MEM memory payload starting in M_IDLE and while M_WAIT has no response. "
                "Release on the matching response edge, when MEM/WB captures the held payload and the "
                "current dcache_cpu_resp_rdata. M_IDLE may assert the request while EX/MEM is held. "
                "M_WAIT must never reissue the request.",
                "Extend the selected response byte or halfword using its sign bit, selecting the lane "
                "from the held EX/MEM address. Keep unrelated ALU and store handling intact.",
            ],
            "verification_probe": "execution_observation",
            "verification": "The load at PC 4 retires with value 41, then the dependent add exits with 42.",
        }
    if (re.search(r"\blogic\s*\[31:0\]\s*load_byte\s*;", mem_source)
            and re.search(r"\{\{24\{load_byte\[7\]\}\},\s*load_byte\}", mem_source)
            and re.search(r"\blogic\s*\[31:0\]\s*load_half\s*;", mem_source)
            and re.search(r"\{\{16\{load_half\[15\]\}\},\s*load_half\}", mem_source)):
        return {
            "stage": "simulation",
            "hypothesis": "MEM concatenates sign bits with 32-bit load_byte and load_half values. "
                          "The result is wider than 32 bits, so assignment discards the sign bits.",
            "confidence": "high",
            "files": ["rtl/mem_stage.sv"],
            "symbols": ["load_byte", "load_half", "load_data_c"],
            "constraints": [
                "Change only load_byte and load_half declarations to 8 and 16 bits. Their assignments "
                "already select the correct lane. Leave the transaction and store paths intact.",
            ],
            "teacher_checks": ["t06_load_store"],
            "verification": "Negative LB and LH results are sign extended in the original teacher test.",
        }
    if re.search(r"\{\{24\{dcache_cpu_resp_rdata\[7:0\]\}\}", mem_source):
        return {
            "stage": "simulation",
            "hypothesis": "MEM repeats the entire low byte or halfword instead of extending its sign, "
                          "and byte reads ignore the low bits of the accepted address.",
            "confidence": "high",
            "files": ["rtl/mem_stage.sv"],
            "symbols": ["load_data", "txn_addr", "txn_funct3"],
            "constraints": [
                "Select the addressed byte or halfword from dcache_cpu_resp_rdata using saved txn_addr. "
                "LB and LH replicate only bit 7 or bit 15 of the selected data. LBU and LHU zero-extend. "
                "LW returns the full word. Preserve transaction timing and write masks.",
            ],
            "teacher_checks": ["t06_load_store"],
            "verification": "The original test reads all signed and unsigned byte and halfword values.",
        }
    if (set(probes.get("l2_branch_direction_observation") or ()) == {"mispredicts"}
            and re.search(r"\bassign\s+train_target\s*=\s*ex_actual_next_pc\s*;", top)
            and re.search(r"\bassign\s+ex_branch_target\s*=\s*actual_next_pc\s*;", ex_source)):
        return {
            "stage": "simulation",
            "hypothesis": "BTB training writes the architectural next PC as the taken target. "
                          "A not-taken branch trains PC+4 into its BTB entry, so a later taken prediction "
                          "can use the wrong target even while the BHT still predicts taken.",
            "confidence": "high",
            "files": ["rtl/ex_stage.sv", top_path],
            "constraints": [
                "In EX drive ex_branch_target with the computed branch/JAL target, or the computed "
                "jalr_target for JALR. Keep ex_actual_next_pc unchanged for redirect and comparison.",
                "Use ex_branch_target for train_target in rv32i5. Keep the existing accepted resolve event.",
            ],
            "verification_probe": "l2_branch_direction_observation",
            "verification": "The T/T/N/T sequence retains the taken target and eliminates the extra miss.",
        }
    execution_rows = (evidence.get("execution_observation") or {}).get("samples", ())
    fetch_cache = module_instance_body(top, "icache")
    port_nets = {
        match[1]: match[2] for match in re.finditer(
            r"\.(mem_req_addr|mem_req_ready)\s*\(\s*(\w+)\s*\)", fetch_cache
        )
    }
    top_assignments = dict(re.findall(r"\bassign\s+(\w+)\s*=\s*(\w+)\s*;", top))
    address_reg = top_assignments.get("imem_addr", "")
    cache_ready = port_nets.get("mem_req_ready", "")
    cache_address = port_nets.get("mem_req_addr", "")
    first_words = re.search(r"\|first_retirements=00000000:([0-9a-f]{8}),00000004:\1,", " ".join(execution_rows))
    ready_binding = (
        cache_ready == "imem_ready" or top_assignments.get(cache_ready) == "imem_ready"
        or (cache_ready and re.search(
            rf"\bassign\s+{re.escape(cache_ready)}\s*=\s*imem_valid\s*&&\s*imem_ready\s*;", top
        ))
    )
    if (first_words and address_reg and cache_address and address_reg != cache_address
            and ready_binding
            and re.search(rf"\b{re.escape(address_reg)}\s*<=\s*{re.escape(cache_address)}\s*;", top)):
        return {
            "stage": "simulation",
            "hypothesis": "The top registers the cache's active bus address, then feeds external acceptance "
                          "back to the cache. Its extra register can replay the previous address on the next "
                          "beat or let the cache advance before the matching address appears externally. "
                          "The first instruction word is observed at both PC 0 and PC 4.",
            "confidence": "high",
            "files": [top_path],
            "constraints": [
                "Use the cache's registered request state to drive external valid and address in the same cycle. "
                "Do not add a second address/valid register in the top-level active bus path. Provide cache "
                "request-ready and same-cycle response only for an actual external valid && ready handshake.",
                "Audit the D-memory path for the same delayed-output/raw-ready split; keep write payload stable "
                "until acceptance. Retain reset quiet and use a separate saved imem_addr only after halt.",
            ],
            "verification_probe": "execution_observation",
            "verification": "Zero-wait fetch returns the original instruction for each accepted address.",
        }
    ex = module_instance_body(top, "ex_stage")
    wb = module_instance_body(top, "wb_stage")
    if (any(name.endswith(".ex_mem_fwd_data") for name in undriven)
            and any(name.endswith(".mem_wb_fwd_data") for name in undriven)
            and re.search(r"\.ex_mem_alu_result\s*\(\s*ex_mem_alu_result\s*\)", ex)
            and re.search(r"\.wb_data\s*\(\s*wb_data\s*\)", wb)):
        return {
            "stage": "simulation",
            "hypothesis": "The two data inputs to EX's forwarding mux have no drivers. In the top, use the "
                          "registered EX/MEM result for ex_mem_fwd_data and the WB-selected value for "
                          "mem_wb_fwd_data, including its MEM consumer if present. Preserve existing selectors.",
            "confidence": "high",
            "files": [top_path],
            "constraints": [
                "Bind .ex_mem_fwd_data(ex_mem_alu_result) and .mem_wb_fwd_data(wb_data) directly at the consumers. "
                "Both producer nets already exist at the instance block. This wiring needs no new declarations or assigns."
            ],
            "verification_probe": "logic_loop_observation",
            "verification": "Both forwarding data channels are driven and no combinational feedback is introduced.",
        }
    pipeline = evidence.get("pipeline_observation") or {}
    if (len(probes.get("icache_word_observation") or ()) == 4 and len(words) == 4
            and all("|accepted=1|response=1|" in word for word in words)
            and re.search(r"\bassign\s+wsel\s*=\s*cpu_req_addr\s*\[\s*3\s*:\s*2\s*\]", icache)
            and re.search(r"\bresp_data_r\s*<=\s*\(\s*wsel\s*==[^;]*refill_words", icache)):
        return {
            "stage": "simulation",
            "hypothesis": "Every cold request returns a word after acceptance, but the refill response uses wsel "
                          "from the current CPU address. Capture wsel in a refill register on miss acceptance "
                          "and use that saved selector in the final refill response. Leave hit selection on the "
                          "current request and use mem_resp_data for the final beat.",
            "confidence": "high",
            "files": [icache_path],
            "verification_probe": "icache_word_observation",
            "verification": "All four cold requests return their originally accepted word despite a later address change.",
        }
    execution = set(probes.get("execution_observation") or ())
    if ({"retired", "register"} <= execution and "reads" not in execution
            and re.search(r"\bassign\s+dcache_stall\s*=\s*dc_cpu_req_valid\s*&\s*~dc_cpu_req_ready\s*;", top)
            and re.search(r"\.dcache_stall\s*\(\s*dcache_stall\s*\)", control)
            and "rtl/mem_stage.sv" in files):
        return {
            "stage": "simulation",
            "hypothesis": "MEM stall is derived from request backpressure and falls while the accepted load is still "
                          "awaiting its response. The load disappears from the retirement stream despite four memory "
                          "beats. Hold the complete EX/MEM slot until response capture has made the result available.",
            "confidence": "high",
            "files": [top_path, "rtl/hazard_unit.sv", "rtl/mem_stage.sv"],
            "constraints": [
                "With the current frozen ports, track a response-captured bit in rv32i5. Set it on dc_cpu_resp_valid "
                "while holding the memory instruction, clear it on reset and slot release. Drive dcache_stall from "
                "valid EX/MEM load/store and an incomplete response. Qualify redirect and ECALL acceptance with "
                "the same downstream hold, and clear MEM/WB valid on cycles without a new result.",
                "On a load-use hazard with EX/MEM free, keep IF/ID and inject an ID/EX bubble. Hold ID/EX only "
                "when EX/MEM is held. Retain the IF/ID payload during the downstream memory hold.",
            ],
            "verification_probe": "execution_observation",
            "verification": "The cold load and dependent arithmetic each retire once in order, with x2=41.",
        }
    exit_probe = next((target for target in ("execution_observation", "l1_halt_observation", "l1_control_observation")
                       if set(probes.get(target) or ()) == {"exit"}), None)
    unconnected_x10 = any(name.endswith(".arch_x10") for name in undriven)
    debug_mux_x10 = re.search(r"\.arch_x10\s*\(\s*dbg_reg_data\s*\)", wb)
    if exit_probe and (unconnected_x10 or debug_mux_x10):
        return {
            "stage": "simulation",
            "hypothesis": "WB's ECALL x10 input is undriven or depends on the debug read selection. "
                          "Use committed x10 independently of dbg_reg_addr. With these frozen ports, "
                          "track x10 at the top using reset and the existing committed wb_reg_write, wb_rd and "
                          "wb_data, and bind that value to wb_stage.arch_x10.",
            "confidence": "high",
            "files": [top_path],
            "verification_probe": exit_probe,
            "verification": "ECALL exits with the committed x10 while the debug mux observes another register.",
        }
    ex_path = "rtl/ex_stage.sv"
    bypasses = ex_mem_control_bypasses(ex_source)
    memory_checks = [
        row["name"] for row in (*evidence.get("first_checks", ()), *(evidence.get("checks") or {}).get("groups", ()))
        if row["name"] in {"t06_load_store", "t06b_neg_imm", "t07_load_use", "t08_store_fwd"}
    ]
    if bypasses and memory_checks:
        mem_path = "rtl/mem_stage.sv"
        mem_source = files.get(mem_path, "")
        late_busy = re.search(r"\bassign\s+mem_busy\s*=\s*mem_active_q\s*&&\s*!mem_done\s*;", mem_source)
        relevant_files = [ex_path, mem_path] if late_busy else [ex_path]
        constraints = [
            "Reset the listed outputs and capture them under the same existing !ex_mem_hold and id_ex_valid "
            "guard as ex_mem_pc. Remove their direct ID/EX assignments. If ex_mem_is_load is a redundant "
            "indicator, derive it from the registered ex_mem_mem_read.",
        ]
        if late_busy:
            constraints.append(
                "MEM busy must cover the existing need_mem slot before the cache accepts it. Include need_mem "
                "in mem_busy and release only on mem_done. Preserve the saved transaction and response handling."
            )
        return {
            "stage": "simulation",
            "hypothesis": "These EX/MEM controls are wired to the younger live ID/EX instruction while the "
                          "PC and valid are registered: " + ", ".join(f"{k} <- {v}" for k, v in bypasses.items()),
            "confidence": "high",
            "files": relevant_files,
            "constraints": constraints,
            "teacher_checks": list(dict.fromkeys(memory_checks)),
            "verification": "Memory instructions carry their own registered operation and destination through MEM.",
        }
    lane_rows = [
        row for row in (*evidence.get("first_checks", ()), *(evidence.get("checks") or {}).get("groups", ()))
        if any(marker in str(row.get("detail", "")) for marker in (
            "非法字节使能", "写事务数", "未复制 dmem_wdata", "得到 0xa5a5a5a5",
        ))
    ]
    if len(lane_rows) >= 3:
        mem_path = "rtl/mem_stage.sv"
        path = mem_path if mem_path in files else "rtl/rv32i5.sv"
        illegal = any("非法字节使能" in str(row.get("detail", "")) for row in lane_rows)
        return {
            "stage": "simulation",
            "hypothesis": "Store byte lanes are wrong: dmem_be, the data on dmem_wdata and the address are not "
                          "derived from the same funct3 + addr[1:0], so byte/half stores land in the wrong "
                          "lanes, get dropped, or issue more than one transaction.",
            "confidence": "high" if illegal else "medium",
            "files": [path],
            "symbols": ["dmem_be", "dmem_wdata", "dmem_addr"],
            "constraints": [
                "Build dmem_be as one contiguous block from funct3 and addr[1:0]: SW=1111, SH=0011<<addr[1], "
                "SB=0001<<addr[1:0]. Never emit a mask with holes.",
                "dmem_wdata must carry the store data replicated across the word so the selected lanes already "
                "hold the low bytes (0x80 as SB becomes 80808080). Do not shift the data by the byte address "
                "unless every lane also carries the correct byte.",
                "One store instruction produces exactly one bus transaction: hold valid, addr, we, wdata and be "
                "stable until the cache accepts it, then release.",
                "Keep the load path, the EX forwarding mux and all cycle timing unchanged.",
            ],
            "teacher_checks": list(dict.fromkeys(row.get("name") for row in lane_rows)),
            "verification": "t06_load_store and t08_store_fwd pass; DMEM write transaction counts match and no "
                            "illegal byte enables appear.",
        }
    store_checks = [row.get("name") for row in evidence.get("first_checks", ())
                    if row.get("name") in ("t06_load_store", "t08_store_fwd", "cache_line_conflict")]
    if not store_checks:
        store_checks = [row.get("name") for row in (evidence.get("checks") or {}).get("groups", ())
                        if row.get("name") in ("t06_load_store", "t08_store_fwd", "cache_line_conflict")]
    store_mux = bool(store_checks and re.search(r"\bex_mem_store_data\s*<=\s*op_b\s*;", ex_source))
    branch_mux = bool(probes.get("l1_control_observation") and re.search(
        r"\bbranch_taken\s*=\s*\(\s*op_a\s*(?:==|!=)\s*op_b\s*\)", ex_source,
    ) and re.search(r"7'b1100011\s*:\s*begin(?:(?!\bend\b).)*alu_src_b_imm\s*=\s*1'b1",
                    files.get("rtl/id_stage.sv", ""), re.S))
    if ((store_mux or branch_mux)
            and re.search(r"\bif\s*\(id_ex_alu_src_b_imm\)\s*op_b\s*=\s*id_ex_imm\s*;", ex_source)):
        uses = []
        if store_mux:
            uses.append("the data captured in ex_mem_store_data")
        if branch_mux:
            uses.append("conditional branch comparisons")
        return {
            "stage": "simulation",
            "hypothesis": "The EX immediate mux replaces forwarded rs2 with the displacement. Retain forwarded rs2 "
                          "separately for " + " and ".join(uses) + ". Preserve the ALU's immediate selection and the "
                          "separate PC-plus-immediate branch target calculation.",
            "confidence": "high",
            "files": [ex_path],
            "teacher_checks": list(dict.fromkeys(store_checks)),
            "verification_probe": "l1_control_observation" if branch_mux else None,
            "verification": "The loop exits when x1 reaches zero. Store data comes from the forwarded rs2 operand.",
        }
    redirect_target = ("l1_redirect_observation" if probes.get("l1_redirect_observation")
                       else "l2_recovery_observation")
    redirect_probe = probes.get(redirect_target) or ()
    fetch_binding = module_instance_body(top, "if_stage")
    hazard_source = files.get("rtl/hazard_unit.sv", "")
    redirect_net = re.search(r"\.redirect_pc\s*\(\s*(\w+)\s*\)", fetch_binding)
    ex_target = re.search(r"\.ex_redirect_pc\s*\(\s*(\w+)\s*\)", ex)
    if ("halted" in redirect_probe and "ordered" in redirect_probe and redirect_net and ex_target
            and redirect_net[1] != ex_target[1]
            and re.search(rf"\.redirect_pc\s*\(\s*{re.escape(redirect_net[1])}\s*\)", control)
            and re.search(r"\bassign\s+redirect_pc\s*=\s*32'[hHdD]0+\s*;", hazard_source)):
        return {
            "stage": "simulation",
            "hypothesis": (
                f"IF consumes {redirect_net[1]}, driven by hazard's constant-zero redirect_pc. "
                f"EX produces the actual target on {ex_target[1]}. The cold jump never retires its target."
            ),
            "confidence": "high",
            "files": [top_path],
            "constraints": [
                f"Bind IF's existing redirect_pc input to {ex_target[1]}. Keep redirect_valid qualified "
                "by the hazard unit. Preserve module interfaces and other logic.",
            ],
            "verification_probe": redirect_target,
            "verification": "The directed jump retires the target instructions and reaches ECALL.",
        }
    if ("retired" in redirect_probe and "ordered" in redirect_probe
            and re.search(r"\bassign\s+ex_mem_flush\s*=\s*(?:ex_redirect_valid|redirect_fire)\s*;", hazard_source)
            and re.search(r"\bif\s*\(ex_mem_flush\)\s*begin\s*ex_mem_valid\s*<=\s*1'b0", ex_source)
            and re.search(r"\.ex_mem_flush\s*\(\s*ex_mem_flush\s*\)", ex)
            and re.search(r"\.ex_mem_flush\s*\(\s*ex_mem_flush\s*\)", control)):
        return {
            "stage": "simulation",
            "hypothesis": "EX's redirect flushes the EX/MEM result from that same instruction. Taken branches "
                          "and jumps redirect IF but do not retire. The cold jump probe reaches its target "
                          "but lacks the jump retirement.",
            "confidence": "high",
            "files": ["rtl/hazard_unit.sv"],
            "constraints": [
                "Do not drive ex_mem_flush with the same EX redirect event. Preserve younger IF/ID flush and MEM hold.",
            ],
            "verification_probe": redirect_target,
            "verification": "The jump retires once between the instruction at PC zero and the target.",
        }
    dcache_source = dcache
    cold_samples = (evidence.get("dcache_lane_observation") or {}).get("samples", ())
    if ("cold_load" in (probes.get("dcache_lane_observation") or ())
            and any("|case=cold_load|" in row and "|accepted=1|response=0|" in row
                    and "|read_beats=4|" in row for row in cold_samples)
            and len(re.findall(
                r"if\s*\(!refill_active\)\s*begin\s*req_pending\s*<=\s*1'b0\s*;"
                r"\s*refill_active\s*<=\s*1'b1", dcache_source,
            )) == 2
            and re.search(r"if\s*\(req_pending\)\s*begin\s*if\s*\(req_we\)", dcache_source)):
        return {
            "stage": "simulation",
            "hypothesis": "Both D-cache miss branches clear req_pending as refill starts. The four beats "
                          "install a line, but no request remains to produce the CPU load response or perform "
                          "the store's write-through.",
            "confidence": "high",
            "files": [dcache_path],
            "constraints": [
                "Retain the accepted CPU address, operation, data, byte mask and pending bit throughout refill. "
                "Skip pending-request processing while refill is active. Resume the retained request once on "
                "the installed line. Discard the pending request if that refill is cancelled by its epoch.",
                "Keep the existing write-through completion event and hit lane merges.",
            ],
            "verification_probe": "dcache_lane_observation",
            "verification": "The cold load returns the selected word after four beats, and lane tests still pass.",
        }
    fetch_path = "rtl/if_stage.sv"
    fetch_source = files.get(fetch_path, "")
    missed_instructions = [
        row["name"] for row in (evidence.get("checks") or {}).get("groups", ())
        if (counts := re.search(r"reference instruction count (\d+) != retirement count (\d+)", row["detail"]))
        and int(counts[1]) > int(counts[2])
    ]
    retirement_signal = re.search(r"\bassign\s+dbg_retire_valid\s*=\s*(\w+)\s*;", wb_source)
    halt_signal = re.search(r"\bassign\s+halted\s*=\s*(\w+)\s*;", wb_source)
    if (len(set(missed_instructions)) >= 2 and retirement_signal and halt_signal
            and re.search(rf"\b{retirement_signal[1]}\s*<=\s*1'b1", wb_source)
            and re.search(rf"\bif\s*\(mem_wb_(?:is_)?ecall\)\s*begin\s*{halt_signal[1]}\s*<=\s*1'b1",
                          wb_source)):
        return {
            "stage": "simulation",
            "hypothesis": "The WB commits ECALL and asserts halted on the edge that first registers "
                          "dbg_retire_valid. The retirement observer samples before that update and "
                          "the program finishes before the next sampling edge.",
            "confidence": "high",
            "files": [wb_path],
            "symbols": ["dbg_retire_valid", "dbg_retire_pc", "dbg_retire_insn", retirement_signal[1], halt_signal[1]],
            "constraints": [
                "Drive dbg_retire_valid from the existing WB commit event, preserving all its qualifiers "
                "including store completion and halt. Drive PC and instruction directly from that same MEM/WB entry. "
                "Remove the unused retire registers. Preserve halted and exit code timing and register writes.",
            ],
            "teacher_checks": list(dict.fromkeys(missed_instructions)),
            "verification": "All benchmarks count the ECALL retirement on its commit edge, with no duplicate retirement.",
        }
    fetch_request = re.search(r"\bassign\s+icache_cpu_req_valid\s*=\s*([^;]+);", fetch_source)
    if (missed_instructions and fetch_request and "if_id_valid" not in fetch_request[1]
            and re.search(r"if\s*\(resp_accept\)\s*begin\s*req_pending_q\s*<=\s*1'b0\s*;"
                          r"\s*if\s*\(if_id_advance\)\s*begin\s*if_id_valid\s*<=\s*1'b1", fetch_source)):
        return {
            "stage": "simulation",
            "hypothesis": "IF acknowledges and clears a matching cache response even when if_id_advance is "
                          "false. The valid bit and payload are only captured when decode can advance, "
                          "so a returned instruction can disappear during a downstream stall.",
            "confidence": "high",
            "files": [fetch_path],
            "constraints": [
                "Reserve the IF/ID slot before accepting an I-cache request: allow a new request only when "
                "IF/ID is empty and no request is pending. Match predictor lookup to this request.",
                "Capture the matching response with the saved PC, prediction and epoch even when decode is "
                "held. Only the accepted ID transfer may consume a valid slot; redirect may squash it.",
            ],
            "teacher_checks": list(dict.fromkeys(missed_instructions)),
            "verification": "The teacher workload retires every instruction across D-cache stalls.",
        }
    bus_wait_checks = [
        row["name"] for row in (evidence.get("checks") or {}).get("groups", ())
        if "I-memory request changed before ready" in row["detail"]
    ]
    busy_driver = re.search(r"\bassign\s+mem_req_valid\s*=\s*(\w+)\s*;", icache)
    if (bus_wait_checks and busy_driver
            and re.search(rf"if\s*\(flush\)\s*begin\s*{busy_driver[1]}\s*<=\s*1'b0", icache)):
        target = next((name for name in ("l2_recovery_observation", "l1_fetch_wait_observation")
                       if "if_changes" in (probes.get(name) or ())), None)
        deferred_redirect = re.search(
            r"else if\s*\(redirect_valid\s*&&\s*req_pending_q\)\s*begin"
            r"(?:(?!\bend\b).)*if\s*\(resp_accept\)",
            re.sub(r"//[^\n]*", "", files.get(fetch_path, "")), re.S,
        )
        constraints = [
            "Remember cancellation while a read beat is awaiting ready. Keep the current valid and address "
            "stable until the accepted edge, then abandon the stale refill before issuing another beat. "
            "On flush or epoch mismatch, do not install any stale line or deliver a CPU response.",
            "Give reset highest priority. Do not accept a new cache request on the same edge as flush.",
        ]
        if deferred_redirect:
            constraints.append(
                "IF currently waits for resp_accept before applying a redirect with a request pending. "
                "The cancelled response will never arrive. Apply the accepted redirect immediately, "
                "clear the IF outstanding slot and update PC/epoch. Cache ready will remain low while "
                "the old externally asserted beat is draining."
            )
        return {
            "stage": "simulation",
            "hypothesis": (
                f"I-cache flush clears {busy_driver[1]}, which directly drives external mem_req_valid. "
                "The teacher observed that the external request changes before ready. "
                "A redirect must squash delivery while keeping an already asserted waiting beat stable."
            ),
            "confidence": "high",
            "files": [icache_path, fetch_path] if deferred_redirect else [icache_path],
            "constraints": constraints,
            "teacher_checks": list(dict.fromkeys(bus_wait_checks)),
            "verification_probe": target,
            "verification": "The external I-memory valid and address stay stable through wait and redirect.",
        }
    fetch_wait = probes.get("l1_fetch_wait_observation") or ()
    if ("if_changes" in fetch_wait
            and re.search(r"\bif\s*\(flush\)\s*begin\s*refilling\s*<=\s*1'b0", icache)):
        return {
            "stage": "simulation",
            "hypothesis": "Redirect cancels refilling while the external bus is still waiting. Drain the "
                          "already-started refill through all remaining ready handshakes. At its final beat, "
                          "install the cache line and emit a CPU response only if the accepted refill epoch "
                          "still matches and there is no same-cycle flush. Invalidate the stale refill's line.",
            "confidence": "high",
            "files": [icache_path],
            "verification_probe": "l1_fetch_wait_observation",
            "verification": "The external instruction address and valid stay stable until each waiting beat is accepted.",
        }
    request_enable = re.search(r"\bassign\s+icache_cpu_req_valid\s*=\s*([^;]+);", files.get(fetch_path, ""))
    teacher_checks = evidence.get("checks") or {}
    only_reuse = teacher_checks.get("total") == 1 and any(
        row.get("name") == "cache_instruction_reuse" for row in teacher_checks.get("groups", ())
    )
    if ((set(fetch_wait) == {"refill_reuse"} or (only_reuse and "refill_reuse" in fetch_wait))
            and request_enable and "if_id_valid" not in request_enable[1]
            and "resp_pending" in request_enable[1] and "if_flush" in request_enable[1]):
        return {
            "stage": "simulation",
            "hypothesis": "The loop reaches the correct exit, but IF repeatedly starts wrong-path refills "
                          "while a branch still occupies IF/ID. Include !if_id_valid in the existing request "
                          "enable so the branch advances and its redirect can qualify the next request. "
                          "Preserve response capture, flush and downstream hold behavior.",
            "confidence": "high",
            "files": [fetch_path],
            "verification_probe": "l1_fetch_wait_observation",
            "verification": "The warm loop keeps the bus stable and refills at most three cache lines.",
        }
    if probes.get("icache_word_observation") and not (pipeline.get("fetch_transfer") or {}).get("response_seen"):
        for warning in evidence.get("width_warnings", ()):
            location = re.match(r"(rtl/icache\.sv):(\d+):", warning)
            if not location or location[1] not in files:
                continue
            source = files[location[1]].splitlines()
            index = int(location[2]) - 1
            line = source[index].strip() if 0 <= index < len(source) else ""
            limit = re.fullmatch(
                r"assign\s+mem_req_valid\s*=\s*\w+\s*&\s*\(\s*\w+\s*<\s*(\d+)'[dD](\d+)\s*\)\s*;",
                line,
            )
            if limit and 0 < int(limit[1]) <= 32 and int(limit[2]) == 1 << int(limit[1]):
                return {
                    "stage": "simulation",
                    "hypothesis": (
                        f"Icarus reports numeric truncation at {location[1]}:{location[2]} ({line}). "
                        "The unsigned refill counter is compared against a limit that truncates to zero, "
                        "so the cache cannot request memory on a miss. Keep the request valid through the "
                        "four legal refill beats. Return the word selected by the address captured at acceptance."
                    ),
                    "confidence": "high",
                    "files": [location[1]],
                    "symbols": ["mem_req_valid"],
                    "verification_probe": "icache_word_observation",
                    "verification": "All four standalone cold requests return their accepted address's word.",
                }
    connections = pipeline.get("instance_connections", {})
    fetch_valid = "icache_cpu_req_valid"
    fetch_ready = "icache_cpu_req_ready"
    if (pipeline.get("status") == "observed" and not pipeline.get("first_fetch_accept")
            and "fetch_accept_at_edge" in pipeline.get("fields", ())
            and "first_pc_instruction" in (probes.get("retirement_observation") or ())
            and all(connections.get(f"if_stage.{port}") for port in (fetch_valid, fetch_ready))):
        source = re.sub(r"/\*.*?\*/|//[^\n]*", "", files.get(fetch_path, ""), flags=re.S)
        request = re.search(rf"\bassign\s+{fetch_valid}\s*=\s*([^;]+);", source)
        if request and re.fullmatch(r"[~!&\s\w]+", request[1]) and re.search(
            rf"(?:^|&)\s*[~!]\s*{fetch_ready}\s*(?:&|$)", request[1]
        ):
            return {
                "stage": "simulation",
                "hypothesis": (
                    f"The fetch producer assigns {fetch_valid} = {' '.join(request[1].split())}. "
                    f"The negated {fetch_ready} term makes valid && ready impossible. The trace observes no fetch "
                    "acceptance. Make valid express an available request independently of ready and preserve "
                    "the accepted request's PC and prediction until response. Confirm the request does not "
                    "depend on a stall term that itself derives from this handshake."
                ),
                "confidence": "high",
                "files": [fetch_path],
                "symbols": [fetch_valid, fetch_ready],
                "verification_probe": "pipeline_observation",
                "verification": "The first fetch accepts PC 0, then the I-cache returns a CPU response.",
            }
    transfer = pipeline.get("fetch_transfer") or {}
    if (transfer.get("accepted") and transfer.get("response_seen")
            and "first_pc_instruction" in (probes.get("retirement_observation") or ())
            and re.search(r"if\s*\(req_accept\)\s*begin\s*req_pending\s*<=\s*1'b0", fetch_source)
            and re.search(r"if\s*\(\s*!req_pending\s*&&\s*!resp_slot_valid", fetch_source)
            and re.search(r"resp_slot_pc\s*<=\s*saved_pc", fetch_source)
            and top_path in files and "rtl/hazard_unit.sv" in files):
        return {
            "stage": "simulation",
            "hypothesis": (
                "IF clears req_pending at request acceptance. The idle issue condition can then overwrite "
                "saved_pc before the cache response, even though resp_slot_pc is assigned from saved_pc. "
                "The trace observes accepted requests and responses but incorrect retirement PC/instruction pairs."
            ),
            "confidence": "high",
            "files": [fetch_path, top_path, "rtl/hazard_unit.sv"],
            "constraints": [
                "Separate request availability from outstanding occupancy. At valid && ready save the "
                "accepted PC and epoch, set outstanding, and advance PC once. Clear outstanding on the "
                "matching response or redirect. Issue again only when no request is outstanding and IF/ID is empty.",
                "Capture the response and the saved PC together even while decode is held. Use an existing "
                "IF hold input for decode backpressure. If it currently carries request backpressure, rewire "
                "its producer to the hazard hold, without making that hold depend on IF request valid.",
                "Consume IF/ID only when ID accepts the same instruction. Preserve a younger IF/ID slot "
                "on MEM hold or load-use; a redirect squashes it.",
            ],
            "verification_probe": "retirement_observation",
            "verification": "The first instruction retires at its own PC with the original word.",
        }
    if (pipeline.get("duplicate_retirement") and {"retired", "ordered"} <= execution
            and re.search(r"if\s*\(decode_accept\)\s*begin\s*if_id_valid\s*<=\s*1'b0", fetch_source)
            and re.search(r"if\s*\(state\s*==\s*ST_HOLD\s*&&\s*!redirect_valid\)\s*begin\s*"
                          r"if_id_valid\s*<=\s*1'b1", fetch_source)):
        return {
            "stage": "simulation",
            "hypothesis": "The IF/ID consume edge clears valid, then the later ST_HOLD block writes valid "
                          "back to one from the same saved slot. The last assignment wins, so decode "
                          "sees the already-consumed instruction on the next edge.",
            "confidence": "high",
            "files": [fetch_path],
            "constraints": [
                "On decode_accept clear IF/ID valid and leave ST_HOLD. Prevent the later ST_HOLD refill "
                "assignment on that same edge. On redirect clear IF/ID valid regardless of state. "
                "Preserve the existing accepted PC and response data pairing.",
            ],
            "verification_probe": "execution_observation",
            "verification": "The first PC and cold load each retire once, followed by the dependent instruction.",
        }
    if ("retired" in (probes.get("l2_recovery_observation") or ())
            and re.search(r"\bassign\s+if_id_advance\s*=\s*!if_hold\s*;", fetch_source)
            and re.search(r"if\s*\(if_id_advance\s*&&\s*!req_accept\)\s*begin\s*if_id_valid\s*<=\s*1'b0", fetch_source)):
        return {
            "stage": "simulation",
            "hypothesis": "IF/ID valid remains set when decode advances and a new cache request is accepted "
                          "on the same edge. That request is not a replacement response, so decode can "
                          "accept the old instruction again.",
            "confidence": "high",
            "files": [fetch_path],
            "constraints": [
                "Clear a consumed IF/ID slot even on req_accept when there is no replacement response. "
                "Keep saved prediction metadata paired with its accepted request until the response.",
            ],
            "verification_probe": "l2_recovery_observation",
            "verification": "The recovery program retires each instruction at its original PC, once.",
        }
    duplicate = pipeline.get("duplicate_retirement") or {}
    witness = duplicate.get("if_id_consume_mismatch")
    failures = probes.get("execution_observation")
    front_end = ["rtl/rv32i5.sv", "rtl/if_stage.sv", "rtl/id_stage.sv", "rtl/hazard_unit.sv"]
    if witness and failures and set(failures) <= {"retired", "ordered"} and all(path in files for path in front_end):
        constraints = [
            "Use one IF/ID slot. Include !if_id_valid in IF request generation along with !req_pending and !hold_pc. "
            "Accept a cache response into the slot only when req_pending is set, even during hold_pc. Clear a "
            "consumed slot whenever !hold_pc and no replacement arrives. Redirect discards req_pending and IF/ID.",
            "Advance flush_epoch on redirect or accepted ECALL, as required by L1 §B.3. "
            "Gate ECALL acceptance with !pc_hold and !redirect_valid. Feed EX's computed ex_target directly to IF; "
            "a hazard redirect_pc placeholder must not drive the IF input.",
        ]
        mem_path = "rtl/mem_stage.sv"
        mem = files.get(mem_path, "")
        if ("resp_consumed <= 1'b1" in mem and "end else if (!resp_pending) begin" in mem
                and re.search(r"assign cpu_req_valid\s*=\s*[^;]*!req_issued\b", mem)):
            front_end.append(mem_path)
            constraints.append(
                "Keep mem_busy high during response capture and release on the next edge when resp_consumed is set, "
                "so the load result is already in MEM/WB for forwarding. Ensure !resp_consumed gates cpu_req_valid, "
                "with the declaration above its first use. In the MEM/WB update, replace the final else if "
                "(!resp_pending) with unconditional else to clear valid on any cycle that produces no result. "
                "Preserve existing load extraction and WB logic."
            )
        if witness["id_hold"] == "load_use_stall" and witness["if_hold"] == "pc_hold":
            front_end.remove("rtl/id_stage.sv")
            constraints.append(
                "In rtl/rv32i5.sv, bind ID's existing id_ex_hold port to ex_mem_hold. In rtl/hazard_unit.sv, "
                "include (load_use_stall && !mem_busy) in id_ex_flush, and include !mem_busy in redirect_valid. "
                "These are distinct edits. A load-use bubble must not keep the same load in ID/EX."
            )
        return {
            "stage": "simulation",
            "hypothesis": (
                f"PC {duplicate['pc_hex']} repeats before WB. Across cycles {witness['cycles']} the IF/ID producer "
                f"holds that valid payload ({witness['if_hold']}=1), but ID/EX is allowed to reload it "
                f"({witness['id_hold']}=0). Use the same downstream memory hold for ID/EX and EX/MEM, "
                "retaining the existing ID/EX payload until EX/MEM accepts it. Handle load-use separately: "
                "hold IF/ID and insert a bubble into ID/EX when the older load advances. Only fire a branch "
                "redirect when EX can advance and latch halt_pending on actual ECALL decode acceptance."
            ),
            "confidence": "high",
            "files": front_end,
            "constraints": constraints,
            "verification_probe": "execution_observation",
            "verification": "The dependent instruction retires once in order, with register 41 and exit 42 preserved.",
        }
    halt_condition = r"id_ecall_detected\s*&&\s*if_id_valid\s*&&\s*!redirect_valid\s*&&\s*!halt_pending"
    if (set(probes.get("l1_halt_observation") or ()) == {"retired", "halted", "exit"}
            and re.search(rf"\bif\s*\({halt_condition}\)\s*begin\s*halt_pending\s*<=\s*1'b1", top)
            and re.search(r"\.hold_pc\s*\(\s*pc_hold\s*\)", module_instance_body(top, "if_stage"))):
        return {
            "stage": "simulation",
            "hypothesis": "The load completes and retires, but the following ECALL never retires. In rv32i5.sv "
                          "halt_pending is set as soon as IF/ID presents ECALL even while the decode consumer is held. "
                          "Add !pc_hold to the existing ECALL condition that sets halt_pending and to the ECALL "
                          "condition that advances flush_epoch, so both use the same accepted decode event. "
                          "Keep redirect priority and the existing WB retirement and exit logic.",
            "confidence": "high",
            "files": [top_path],
            "verification_probe": "l1_halt_observation",
            "verification": "The load and ECALL both retire, exit is 41, and the younger store never issues.",
        }
    hazard_path = "rtl/hazard_unit.sv"
    hazard = files.get(hazard_path, "")
    if ("halted" in (probes.get("l1_halt_observation") or ())
            and re.search(r"if\s*\(id_ecall_detected\s*&&\s*!halted\)", hazard)
            and re.search(r"\bassign\s+id_ecall_detected\s*=\s*if_id_valid\s*&&", files.get("rtl/id_stage.sv", ""))
            and re.search(r"else\s+if\s*\(halt_pending\)\s*begin\s*if_id_valid\s*<=\s*1'b0", fetch_source)):
        return {
            "stage": "simulation",
            "hypothesis": "Decode advertises ECALL before it is accepted. The hazard unit immediately "
                          "latches halt_pending, so IF clears the held ECALL while an older load stalls decode.",
            "confidence": "high",
            "files": [hazard_path],
            "symbols": ["id_ecall_detected", "if_id_ready", "halt_pending_q", "epoch_q"],
            "constraints": [
                "In the existing sequential condition require if_id_ready as well as id_ecall_detected "
                "and !halted before setting halt_pending_q and advancing epoch_q. Retain redirect priority. "
                "Leave WB and IF behavior intact.",
            ],
            "verification_probe": "l1_halt_observation",
            "verification": "The older load completes, ECALL retires once, and the younger store never issues.",
        }
    if ("ordered" in (probes.get("l1_redirect_observation") or ())
            and re.search(r"\bassign\s+ex_mem_flush\s*=\s*redirect_valid\s*;", hazard)
            and re.search(r"if\s*\(ex_mem_flush\)\s*begin\s*ex_mem_valid\s*<=\s*1'b0",
                          files.get("rtl/ex_stage.sv", ""))):
        return {
            "stage": "simulation",
            "hypothesis": "The redirect also flushes EX/MEM on the edge the redirecting JAL must enter "
                          "that stage. JAL disappears from retirement and its link value cannot be written.",
            "confidence": "high",
            "files": [hazard_path],
            "symbols": ["ex_mem_flush", "id_ex_flush", "redirect_valid"],
            "constraints": [
                "Keep redirect flush for IF and ID/EX. Do not assert ex_mem_flush for redirect_valid. "
                "The valid redirecting instruction must advance through EX/MEM when EX can accept it.",
            ],
            "verification_probe": "l1_redirect_observation",
            "verification": "JAL at PC 4 retires and execution reaches its target without wrong-path retirement.",
        }
    if probes.get("l1_control_observation"):
        if (re.search(r"\bassign\s+redirect_pc\s*=\s*(?:32'[hH]0[0_]*|'0|0)\s*;", hazard)
                and all(re.search(r"\.redirect_pc\s*\(\s*redirect_pc\s*\)", module_instance_body(top, module))
                        for module in ("hazard_unit", "if_stage"))
                and re.search(r"\.ex_target\s*\(\s*ex_target\s*\)", module_instance_body(top, "ex_stage"))):
            return {
                "stage": "simulation",
                "hypothesis": "IF receives redirect_pc from a hazard output tied to zero, while EX drives ex_target on "
                              "a separate net. In rv32i5.sv change only the if_stage instance binding from "
                              ".redirect_pc(redirect_pc) to .redirect_pc(ex_target). Preserve the hazard instance, "
                              "every module header, redirect-valid qualification and cache cancellation timing.",
                "confidence": "high",
                "files": [top_path],
                "verification_probe": "l1_control_observation",
                "verification": "The backward loop terminates with x1=0 and retires ECALL at PC 0x10 with exit 7.",
            }
    if "reset_bus_quiet" in (probes.get("retirement_observation") or ()):
        direct = dict(re.findall(r"\bassign\s+([id]mem_valid)\s*=\s*(\w+)\s*;", top))
        reset = next(iter(evidence.get("retirement_observation", {}).get("samples", ())), "")
        exposed = [f"{port}={net}" for port, net in direct.items() if re.search(rf"\|{port}=[xz](?:\||$)", reset)]
        if exposed:
            return {
                "stage": "simulation",
                "hypothesis": "Before the first reset edge the bus probe observes an unknown valid. The top directly "
                              "exposes the cache outputs (" + ", ".join(exposed) + "). Gate those external valids "
                              "with rst_n at their existing top-level drivers. Keep address/data wiring and cache timing.",
                "confidence": "high",
                "files": [top_path],
                "verification_probe": "retirement_observation",
                "verification": "Both external valid outputs are 0 before the first reset edge.",
            }
    return None


class DiagnoserAgent(SubAgent):
    name = "diagnoser"
    title = "05 · Diagnoser"
    node_type = name

    def run(
        self, input_handoff: Path, attempt: int, previous: list[dict], *, protocol_error: str | None = None,
    ) -> Path:
        artifacts = self.runtime.read_handoff(input_handoff)["payload"]["artifacts"]
        plan = self.runtime.read_json_artifact(artifacts["plan"])
        files = load_snapshot(self.runtime, artifacts["rtl_manifest"])
        packet_ref = artifacts["failure_packet"]
        packet = self.runtime.read_json_artifact(packet_ref)
        # Report only discarded experiments, and only their measured effects.
        # A previously resolved hypothesis must never override the current evidence.
        rejected = []
        for decision in previous[-2:]:
            if decision["accepted"]:
                continue
            measurements = dict(decision["observations"])
            verification = measurements.pop("verification", None)
            if verification:
                measurements["probe_verification"] = {
                    key: verification.get(key)
                    for key in ("probe", "before_failures", "after_failures", "resolved", "probe_failure_counts")
                }
            rejected.append({
                "reason": decision["reason"],
                "is_current_diagnostic_candidate": decision["rtl_manifest"] == artifacts["rtl_manifest"],
                "observations": measurements,
            })
        packet["rejected_attempts"] = rejected
        trailing = [d for d in previous[-2:] if not d["accepted"]]
        inert = (
            len(trailing) == 2
            and all(d["reason"] == "no_verified_progress" for d in trailing)
            and trailing[-1]["observations"].get("failed_checks")
            == trailing[-2]["observations"].get("failed_checks")
        )
        spec = self.runtime.read_text_artifact(artifacts["spec"])
        headings = re.findall(r"(?m)^#{2,6} +(.+)$", spec)
        module_map = [
            {key: module[key] for key in ("name", "file", "description")}
            for module in plan["modules"]
        ]
        failing_probes = {
            target: failures for target in PROBE_TARGETS
            if (failures := probe_failures(target, packet["teacher_evidence"]))
        }
        evidence = packet["teacher_evidence"]
        stage = evidence["stage"]
        connections = l3_connections(files) if plan.get("l3_allocation_tag_contract") else {}
        raw = (_observed_diagnosis(evidence, files, failing_probes) if stage == "simulation"
               else _observed_compile_diagnosis(evidence, files) if stage == "compile" else None)
        stale_measurement = bool(
            raw is not None and previous and not previous[-1].get("accepted")
            and previous[-1].get("reason") == "no_verified_progress"
        )
        if stale_measurement:
            # 机械诊断上一轮已经被补丁验证为无效：继续沿用它只会重复同一份补丁。
            # 把测量结果降级成提示交还给模型，强制换一个根因假设。
            source_evidence = {
                "previously_failed_measured_diagnosis": raw,
                "note": "上一轮按这份机械诊断打的补丁零效果（失败数逐字不变），"
                        "不要再重复它；请给出不同的根因，并说明为什么这份测量判断错了。",
            }
            raw = None
        if not stale_measurement:
            source_evidence = _source_evidence(evidence, files, failing_probes) if raw is None else {}
        prompt = json.dumps({
            "current_stage": stage,
            "failure_packet": packet, "modules": module_map,
            "spec_headings": headings, "current_probe_failures": failing_probes,
            **({"current_top_bindings": connections} if connections else {}),
            **({"source_evidence": source_evidence} if source_evidence else {}),
            **({"previous_patch_validation_error": protocol_error} if protocol_error else {}),
            **({"format_hint": "上次输出被截断：只返回最短 JSON。hypothesis 一行，constraints 至多 4 条，files 只列必要项。"}
               if protocol_error and "截断" in protocol_error else {}),
            **({"patch_inert_warning": PATCH_INERT_WARNING} if inert else {}),
        }, ensure_ascii=False)
        event = self.runtime.begin_event(self.name, self.node_type, self.title, prompt, attempt)
        if raw is None:
            result = self.runtime.call_model(
                event, prompt, DIAGNOSE,
                model_options={"thinking": False, "max_tokens": min(
                    self.runtime.options["max_tokens"],
                    int(os.environ.get("EDA_DIAGNOSE_TOKENS", "6000")),
                )},
            )
            raw = extract_json_payload(result["answer"])
        else:
            event["diagnosis_source"] = "measured"
        if raw.get("stage") != stage:
            raise PatchProtocolError(f"Diagnoser 必须依据当前 {stage} 阶段证据")
        for key in ("symbols", "constraints", "spec_sections", "teacher_checks"):
            value = raw.get(key, [])
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise PatchProtocolError(f"Diagnoser 字段 {key} 必须是字符串数组")
        allowed = raw.get("files")
        if not isinstance(allowed, list) or not allowed or any(
            not isinstance(path, str) or path not in files for path in allowed
        ):
            raise PatchProtocolError("Diagnoser 必须选择现有 RTL 文件")
        requested_probe = raw.get("verification_probe")
        target = requested_probe if isinstance(requested_probe, str) and requested_probe in failing_probes else None
        evidence = packet["teacher_evidence"]
        check_names = set(raw.get("teacher_checks", []))
        checks = evidence["checks"]["groups"]
        selected_checks = [check for check in checks if check["name"] in check_names]
        brief = {
            "stage": stage,
            "verification_probe": target,
            "target_failures": failing_probes[target] if target else [],
            "evidence": {
                "checks": (selected_checks or checks)[:4],
                "first_mismatches": evidence["first_mismatches"][:2],
                "compiler_errors": evidence["error_excerpt"] if evidence["stage"] == "compile" else [],
                "width_warnings": [line for line in evidence.get("width_warnings", ())
                                   if any(line.startswith(path + ":") for path in allowed)],
            },
            "schema_version": 1, "failure_packet": packet_ref,
            "rtl_manifest": artifacts["rtl_manifest"],
            "hypothesis": str(raw.get("hypothesis") or "")[:1500],
            "confidence": raw.get("confidence") if raw.get("confidence") in ("low", "medium", "high") else "low",
            "files": list(dict.fromkeys(allowed)),
            "symbols": [str(x)[:128] for x in (raw.get("symbols") or [])[:20]],
            "constraints": [str(x)[:500] for x in (raw.get("constraints") or [])[:10]],
            "spec_sections": [x for x in (raw.get("spec_sections") or []) if x in headings][:6],
            "verification": str(raw.get("verification") or "")[:750],
        }
        failing_names = [group["name"] for group in (selected_checks or checks)[:3]]
        test_sources = [
            source for source in (test_source(name) for name in failing_names) if source
        ]
        if test_sources:
            # 教师用例原文（建程序 + 检查项）是决定性的：只给 "等待 halted 超时 0x0c"
            # 时，Patch 必须猜这段程序装了什么。
            brief["teacher_test_source"] = "\n\n".join(test_sources)[:6000]
        if inert:
            brief["patch_inert_warning"] = PATCH_INERT_WARNING
        scoped_connections = relevant_l3_connections(connections, brief["files"], brief["symbols"])
        if scoped_connections:
            brief["current_top_bindings"] = scoped_connections
        if protocol_error:
            brief["previous_patch_validation_error"] = protocol_error
        if not brief["hypothesis"] or not brief["verification"]:
            raise PatchProtocolError("Diagnoser 必须给出可验证的根因假设")
        brief_ref = self.runtime.write_json_artifact(f"repair_brief_attempt{attempt}.json", brief)
        self.finish(event, "info", response=json.dumps(brief, ensure_ascii=False))
        return self.runtime.write_handoff(
            self.name, "patch",
            {"artifacts": {
                "spec": artifacts["spec"], "plan": artifacts["plan"],
                "rtl_manifest": artifacts["rtl_manifest"],
                "failure_packet": packet_ref, "repair_brief": brief_ref,
            }},
            attempt=attempt,
        )
