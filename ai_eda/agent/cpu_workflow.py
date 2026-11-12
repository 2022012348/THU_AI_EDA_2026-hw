"""Course CPU module ownership and coordinated source generation."""

import re

from collections import Counter

from ai_eda.agent.subagents.common import safe_project_path

BASE = ("rv32i5", "if_stage", "id_stage", "ex_stage", "mem_stage", "wb_stage",
        "hazard_unit", "forwarding_unit")
CACHE = ("icache", "dcache")
OOO = ("rename_unit", "issue_queue", "reorder_buffer", "load_store_unit")
L3_TAG_PORTS = {
    "reorder_buffer": "output logic [2:0] alloc_rob_idx",
    "issue_queue": "input logic [2:0] disp_rob_idx",
    "load_store_unit": "input logic [2:0] disp_rob_idx",
}
L3_OPERAND_PORTS = {
    "rename_unit": tuple(port for prefix in ("iq", "lsu") for operand in (1, 2) for port in (
        f"input logic [5:0] {prefix}_rs{operand}_idx",
        f"output logic [31:0] {prefix}_rs{operand}_value",
    )),
    **{name: tuple(port for operand in (1, 2) for port in (
        f"output logic [5:0] {prefix}_rs{operand}_idx",
        f"input logic [31:0] {prefix}_rs{operand}_value",
    )) for name, prefix in (("issue_queue", "iq"), ("load_store_unit", "lsu"))},
}

IN_ORDER_BOUNDARIES = {
    "id_stage": ("id_ex", ("id_ex_hold", "id_ex_flush")),
    "ex_stage": ("ex_mem", ("ex_mem_hold",)),
    "mem_stage": ("mem_wb", ()),
}

CPU_PLANNING = """
In the plan notes, list producer -> consumer, payload, acceptance, and hold/clear
for IF/ID, ID/EX, EX/MEM, MEM/WB and cache request/response. Use one producer per
net, including ready, redirect target, epoch, and committed x10. The top wires
an owned output without driving it again. A port direction is relative to its
module. State which stage accepts each payload and which event consumes it.
Choose one fetch-PC advance event. Keep PC, prediction metadata and epoch from
the accepted request until its response is consumed. Identify the registered
current epoch separately from any proposed update. State priority when reset,
older redirect, MEM wait, load-use or accepted ECALL meet.
Declare each formal port exactly once. Put a semicolon between declarations, with one
name per declaration (e.g. input logic [31:0] ex_mem_feedback;). A module may read its
own output internally; if feedback actually arrives from elsewhere, give it a distinct
input port. Do not list an output as a second input under the same name.
Follow the advanced sections when they override baseline timing or module requirements.
"""

L3_PLANNING = """
Describe ROB commit, rename recovery, issue wakeup and store/ECALL serialization.
Freeze ROB's allocated entry index as output logic [2:0] alloc_rob_idx, and
both IQ and LSU dispatch tags as input logic [2:0] disp_rob_idx. Connect the same
ROB allocation tag to the matching IQ or LSU enqueue on the same accepted dispatch.
The ROB is the only allocator of that index. Rename must not expose its own
allocation index output; it may consume the ROB's index if it needs to track it.
Use one atomic dispatch acceptance for ROB, rename/free-list, and the selected
IQ or LSQ. ECALL allocates a completed ROB entry without taking either queue.
Record who owns PRF ready bits, who writes each completion lane, and who
provides operands to both IQ and LSU. Specify pending-response and commit
handshakes. Use modular age relative to ROB head, not raw index comparison.
Separate allocation port, completion tag and retirement head tag. Include
the store authorization path: only the oldest valid matching ROB head may
start a store; a write-through response completes that entry.
"""

CPU_CODING = """
Implement all planned files as a coordinated candidate so both ends of a handshake
use the same clock and ownership rules. Use one line `// file: rtl/<name>.sv` before
each file. Return raw SystemVerilog with these separators and no Markdown fences.
Review the whole signal path before emitting it. Bind each formal port only once in
an instance. Connect both clk and rst_n on every sequential instance, including
hazard/predictor control. A missing named connection can compile with a floating
input. Give every external bus output one driver. If reset or halt logic must gate
a cache output, wire the cache to a private net and assign the external port in
one place. Never connect a cache output directly to a top port and also drive the
same port from the top. Avoid redeclaring ANSI ports in the body. Never use SystemVerilog keywords
such as dist or priority as variable names. Put module-scope
signal and loop-index declarations before the assignments and instances that use
them. Declare block-local temporaries before statements. Ensure every pipeline valid has
one driver and a matching consumer, response data is captured even under backpressure,
and tags (PC, instruction, epoch) travel with the corresponding response.
For a single outstanding fetch, save the accepted PC, predicted next PC and epoch
on request acceptance; use those saved values when the response reaches decode.
Advance the fetch PC on acceptance or on response consumption, once per fetch.
While a downstream consumer is held, retain the complete valid payload until it
advances or is explicitly squashed. Drive the external reset outputs to their
required values even before the first rising clock.
Derive cache busy from occupancy or other independent state. Do not use a
request-derived busy signal to combinationally suppress that same request.
Assert request valid from the producer's available payload independently of ready.
Use valid && ready to capture the request at both ends on the same edge.
In the top-level active external bus path, drive valid, address, write flag,
byte enables and write data from the cache outputs in the same cycle. Do not
insert a second output register while giving the cache raw external ready.
Pass the external read data back on the actual external accepted edge. A
separate address register may capture the last bus address for halted only.
If IF has a response slot, capture instruction data, accepted PC and epoch
together on the response edge and retain all three while decode is held.
In a same-edge refill completion and cancellation, apply
the specification's cancellation priority to both installation and delivery.
Keep the active epoch in resettable state and update it only on the chosen
redirect/squash event. Avoid feeding a combinational next-epoch output back
to the current-epoch input.
At cache request acceptance latch the requested word offset. After a successful
four-word refill, return the word at that saved offset, irrespective of the live
request address. A selected final beat comes from the current accepted response,
since its nonblocking array write is not visible yet. Earlier selected beats come
from the words already captured. Check all four offsets for both I and D loads.
A two-bit refill index covers beats 0 through 3. End the refill on acceptance of
beat 3. A two-bit literal cannot represent 4 and truncates to zero.
The frozen teacher spec governs external ports, cache contracts and retirement.
Do not infer architecture behavior from benchmark addresses or values.
"""

IN_ORDER_CODING = """
Each in-order stage owns a real clocked output boundary: ID owns ID/EX, EX owns
EX/MEM, and MEM owns MEM/WB. Capture valid, PC, instruction and the associated
payload together on the accepted rising edge. Drive downstream ports from that
registered payload. Wire the hold/flush inputs at their named top-level instances.
Forward EX/MEM and MEM/WB registered results from older instructions only.
Bind both EX forwarding data inputs to their actual producers in rv32i5. The
EX/MEM path forwards a ready ALU or link result. The MEM/WB path forwards WB's
selected value, including the loaded data, rather than its saved load address.
Use the registered ID/EX instruction's operand-use flags for EX forwarding.
Keep forwarded rs2 as store data before applying the ALU immediate mux.
For register ALU opcode 0110011, ALU B is forwarded rs2 and the shift
amount is forwarded rs2[4:0]. Immediate shifts use the encoded shamt.
Compare forwarded rs1 and rs2 for conditional branches. Use the branch
immediate only in the target address computation.
Separate hold, bubble and squash. A cold-load request acceptance does not release
the held EX/MEM instruction; the matching response enables its one transfer.
Use these slot transitions after reset and any valid older squash:
- Hold: retain valid and every payload field; still capture a cache response
  into its reserved slot if the consumer is held.
- Accept without replacement: clear valid, even when a new request launches.
- Accept with replacement: latch the new valid and its entire payload together.
- Load-use bubble: keep the dependent IF/ID slot, but clear ID/EX when EX can
  advance. During MEM hold, retain the older ID/EX and EX/MEM contents.
Do not let a later assignment reassert the consumed slot's old valid. When ID accepts ECALL, stop younger fetch
while letting that ECALL and older instructions advance. That edge must also
transfer ECALL into the next stage even if halt-pending begins on that edge.
Qualify ECALL acceptance with an available ID/EX slot and absence of an older
redirect. Use the same accepted decode event for halt-pending and its epoch change.
Check that holding an IF/ID payload cannot cause ID/EX to accept it twice. Gate
the payload's consumption and replacement with the same transfer event.
An EX redirect is accepted only when that instruction can advance past EX.
It squashes younger IF/ID and ID/EX slots. The redirecting instruction still
enters EX/MEM and retires, including an rd=x0 jump or a taken conditional branch.
Hold EX/MEM from the older memory instruction's first request cycle through
its wait for a response. Derive that hold from the MEM transaction state,
independently of the younger EX redirect, so qualifying redirect cannot
feed back into its own enable.
Connect the EX-computed branch/jump target to IF in the top-level design.
The hazard unit may qualify when a redirect fires. Every qualified redirect
must deliver the computed target on that edge, including nonzero targets.
Reserve storage for each new IF request before it is accepted. A redirect
updates PC and epoch immediately even if an old response will be cancelled.
Keep an already asserted external beat stable until ready. Then discard the
stale refill without installing a line or delivering a response to IF.
For writes, preserve the original byte address, use the spec's replicated
SB/SH write data and byte strobes, and respond only after write-through acceptance.
For jumps save PC+4 and for JALR clear target bit zero. Forward the youngest
available older destination with rd != 0; a load in EX/MEM is not yet available.
A cache load returns an aligned 32-bit word. Select byte address bits [1:0]
or halfword bit [1] from the held request. Use an 8-bit selected byte and
a 16-bit selected halfword before sign or zero extension to 32 bits.
For a cache transaction distinguish request accepted, response received, and
the held EX/MEM slot released. While releasing a completed slot, keep request
valid low so that slot cannot issue a second transaction. On cycles with no
new MEM/WB payload, clear MEM/WB valid. Adjacent valid WB payloads may retire
on consecutive edges even at the same PC.
Drive the debug retirement valid, PC and instruction from the same MEM/WB
accept event. Expose the ECALL retirement on its commit edge, including when
that edge sets halted. Drive debug retirement directly from the WB commit
event and current MEM/WB payload, without a second retirement register.
For ECALL use committed architectural x10 independently of dbg_reg_addr.
Hold the external imem_addr from immediately before entering halted.
While halted neither bus is valid. Do not use an internal fetch PC for this hold.
"""

L1_FETCH_CODING = """
Use a single outstanding CPU-to-I-cache fetch. Use three distinct states:
idle, awaiting cache response, and holding one valid IF/ID instruction.
In idle, request the current PC only when decode can eventually accept it.
On valid && ready, save request PC and epoch, set awaiting response, and advance
PC by 4. While awaiting response, issue no requests and leave the saved PC
and epoch unchanged. On cache response, clear awaiting and capture data plus
the saved PC into IF/ID, even if decode is stalled. While IF/ID is full, issue
no requests and hold the slot until decode accepts it. Use the same decode
accept event to clear IF/ID and to load ID/EX. No extra response slot is needed.
On an accepted redirect, discard the younger IF/ID slot, change the fetch epoch
and set PC to EX's target. Let the cache drain in-flight bus beats without
delivering a stale response. Wait for cache ready before accepting the new PC.
The cache miss FSM returns exactly one CPU response from the final valid refill
beat. Refill line installation alone does not produce that response.
Keep decode's stall separate from cache's request backpressure. Hold IF/ID on
load-use or a busy MEM stage. Insert a load-use bubble only if ID/EX can advance.
"""

L2_PREDICTOR = """
Freeze one accepted EX resolution event: valid control transfer, EX/MEM can
accept it, and the instruction survives older control. Use that same event for
BTB/BHT training and dbg_bp_resolve. On that edge compare the instruction's own
saved predicted next PC with its actual next PC, including target mismatch even
when both predict Taken. Drive recovery and dbg_bp_mispredict only on mismatch.
When the prediction is right, keep younger correct-path IF/ID work and still
retire the resolving instruction exactly once.

On fetch acceptance capture PC, predicted next PC, prediction valid, and epoch
from that same accepted lookup. Freeze the metadata until the matching response
and through the clocked IF/ID and ID/EX transfers. Never re-query the predictor
to reconstruct metadata at EX. Use the predicted next PC to choose the next
fetch. Lookup sees old BTB/BHT state on a simultaneous training edge.

Use 16 BTB entries indexed by PC[5:2] with valid, PC[31:6] tag, target and type.
Reset the 16 independent two-bit BHT entries to 01. Read the old counter at the
training PC index for saturation; the concurrent lookup may use another index.
Train only accepted
conditional branches; allocate a missed conditional BTB entry only if actually
taken. Store a branch's taken target independently from its actual next PC.
On an accepted JAL or JALR train its actual target without changing BHT.
Do not treat a BTB index match as a tag hit.

An accepted mismatch restores actual next PC, changes the fetch epoch, and
squashes younger instructions and stale fetch responses. Keep older MEM
transactions progressing; do not give a younger speculative fetch cancellation
priority over an older accepted D-cache request. A younger store, ECALL or
predictor update from a squashed slot cannot have a side effect.
"""

OUT_OF_ORDER_CODING = """
Implement a true single-dispatch, single-retirement OoO core. Trace these edges:
1. IF reserves a slot on request acceptance, saves PC/prediction/epoch and captures
the corresponding response even if dispatch is stalled. Clear the slot only on
atomic dispatch or squash. Never use the current fetch PC for a prior response.
2. Allocate ROB tail, rename and enqueue IQ/LSQ together on the same accept. Source
mappings precede the instruction's own destination mapping update. Initialize PR0
through PR31 as ready, PR32 through PR39 as free; PR0 never changes.
3. IQ selects a ready entry and issues once. Use ready operand values from the PRF
or completion forwarding. Integer completion carries original ROB index, PRD and
result. LSU independently issues in memory order, keeping original ROB index,
PRD, byte address and store lanes through wait and response.
4. Both integer and LSU completions must reach ROB and PRF. Use two write channels
or buffer a simultaneous completion until accepted; never drop either. Wake queued
consumers only on actual completion. Permit ready ALU issue during an older load
wait and derive the overlap event from real queue/LSU state and modular ROB ages.
5. Retire only a valid, completed ROB head and clear it once. Commit its PRD to the
committed map, release its old PRD, report its original PC/insn. Authorize store
at head and complete it only on the D-cache write-through response. ECALL is a
completed ROB entry at dispatch, blocks younger dispatch, and retires at the head.
Latch committed x10 and assert halted one edge after its retirement, after bus drain.
6. On mispredict retain the resolving and all older entries. Squash only younger
ROB/IQ/LSQ entries and restore mappings/free state from the branch checkpoint.
Never cancel an older memory transaction because fetch epoch changes.
Train BTB/BHT once per accepted, nonsquashed control completion. Index BHT
training by the trained PC, not the concurrent fetch PC. Store the taken target
separately from actual next PC. Use the saved prediction for mismatch, and
apply direction/tag/epoch rules from Section C.
On each connection check the formal port direction and same-edge ownership before
emitting RTL, particularly ROB allocate tag, completion valids/tags, PRF values,
and ROB head store permission. Use existing verified bus and predictor semantics.
"""

L3_EDGE_CONTRACT = """
Use a conservative recovery design: a branch/JAL/JALR resolves only when its
ready IQ entry is the current ROB head. Independently ready younger integer
entries can still bypass an older pending load. The LSU may start its oldest
ready entry only when its ROB tag equals the head. Hence no younger accepted
memory transaction needs to be undone at a redirect.
On reset the eight-entry ROB is empty and allocation is ready. A count through
eight takes four bits, while head, tail, completion and allocation tags take three.
Use block-local int loop counters and the full queue depth. Slicing a depth of
eight to three bits yields zero, and a three-bit loop variable cannot reach eight.
Default every combinational temporary before a conditional selection, including
IQ age distances and LSU byte enables. Keep loop indices local to their process.
Allocation and commit can coincide; update count once from both accepted events.
Compute current ROB head from registered state. Index commit validity and
payload from the current head. The next head must not feed the current head
or the commit event that advances it.
Advance the ROB tail only on an accepted allocation or recovery. On every
idle cycle keep the same tail and tag.
On recovery preserve the resolving head and mark it complete even if squash
occurs on that edge. Set ROB tail to head+1 and count to one so clearing younger
valid bits cannot leave holes. Suppress dispatch on the recovery edge.
Suppress CPU fetch acceptance during squash; clear any younger ECALL's pending
halt and make squash dominate buffer updates from the stale fetch.
Use an eight-entry circular free FIFO with four-bit head and tail counters and
three-bit array indices. Reset head=0, tail=8 and slots 0..7 to PR32..PR39.
Each free-list slot stores a six-bit PR number. Only the array index is three bits.
Allocation advances only head. Commit enqueues the old PR at tail and advances
tail. A branch checkpoint captures the post-dispatch RAT and head. On recovery
restore them while keeping the current free tail, which includes older commits.
Drive allocation readiness from current registered free-list occupancy. If the
current dispatch takes the last free PR, next-cycle occupancy becomes zero.
Never use the head or tail computed from this dispatch to decide its readiness.
Derive recovered occupancy from the four-bit difference between the current
tail and restored head, so every squashed destination becomes free again.
Retain readiness for live mappings and never assign a destination to PR0.
Use separate procedural branches for signed and logical right shifts. In a
conditional expression with an unsigned arm, a signed shift can lose its sign.
Keep the LSU head until its response. Request valid asserts for the launch and
stays high until acceptance. While awaiting a response, the saved request is
outstanding and valid stays low. The acceptance saves its address, tag and
destination. Only the response pops it. A simultaneous enqueue writes slot zero
if the response popped slot zero while slot one was empty.
For SB, replicate the low byte to all four lanes. For SH, replicate the low
halfword to both halves. Use the original byte address and a separate mask.
On a store miss merge the saved store lanes into the newly refilled word
before installing the cache line, and acknowledge only after write-through.
Use unpacked arrays for independently indexed cache lines and words, for
example logic [31:0] line_words [0:15][0:3]. Dynamically select a whole word
from the unpacked array, then merge byte lanes in a 32-bit temporary.
Declare byte-lane loop counters inside their procedural loops. A module-scope
counter assigned from multiple always blocks can infer a latch or multiple drivers.
For a store, conditional branch, or instruction with rd=x0, emit PRD=0 and
allow dispatch even if no free PR exists. Independently qualify each PRF data
and readiness write with that lane's PRD!=0. A delayed store completion must
leave intact a PR allocated to a younger instruction while the store waited.
An LSQ request handshakes with the cache only
when the cache can save that request; CPU ready must be low during a pending
request lookup as well as refill, write-through, and response.
Select IQ and LSU physical read indices from resident entries, with a
dedicated index and data port per operand. The selected physical index is six
bits and the value is 32 bits. Drive the read indices directly from the
resident tags. Calculate the effective address and store data in a separate
block that consumes the returned PRF values. Keep the accepted LSU request and its tag stable
until response. A younger ready ALU may overlap a waiting load, and both real
completions may arrive on the same cycle. The ROB and PRF observe both lanes.
Qualify the issue event by either accepted integer issue or accepted LSU
request. Derive the bypass event from an accepted younger integer issue and
an outstanding older load. Allocate a completed ECALL ROB entry without a
queue slot and stop younger dispatch until commit or a valid older redirect.
"""

L3_FROZEN_INTERFACE_TIMING = """
The frozen top routes memory ready straight to the caches. For a read,
mem_resp_valid and mem_resp_data refer to the same cycle as external
mem_req_valid && mem_req_ready. Never advance or defer the refill beat
independently of data capture. Keep the D-cache flush inactive in the top
because LSU only issues at ROB head. Its accepted store response comes
after external write-through. I-cache flush drains a previously asserted
beat until ready and suppresses stale installation and response.
For each accepted cold read, save its word offset. The fourth accepted beat
installs the line and emits one CPU response using that saved offset, even
when the CPU deasserted valid or changed its address. For offset 3, use the
current fourth-beat data rather than the previous refill register value.
Keep a registered refill-active bit distinct from the 2-bit beat index.
On a miss set active and start at beat 0. Only accepted external reads advance
the index, so the four bus addresses are base+0, base+4, base+8, and base+12.
In the top, keep one fetch outstanding or one buffered instruction. On
fetch acceptance, save PC, predicted next PC and epoch. On a matching
response, fill the buffer. On dispatch or recovery, clear it. A fresh
request alone does not consume or replace a buffered instruction.
Use the ROB tag as the IQ array index; the ROB's free slot guarantees
that index is free in the IQ. Dispatch to the IQ uses ROB space, independent
of whether the execution port is ready. Choose the oldest eligible IQ entry
after filtering for source readiness and any ROB-head restriction, so an
older unready instruction cannot block a younger ready one.
IQ's physical indices address the PRF.
The PRF's integer operands feed ALU directly. LSU receives its own values.
The top routes LSU acceptance and integer acceptance to separate issue
paths, serializing their launch if both are ready on one edge. They still
complete independently. Write the integer completion and PRF on actual
issue, and write the memory completion and PRF on actual LSU response.
Complete both loads and stores in the ROB. A store has no PRF destination and
returns PRD zero, but still needs a completion when its write is acknowledged.
Use one integer accepted event (iq_issue_valid && iq_issue_ready) for both IQ
consumption and completion. Put any LSU arbitration in iq_issue_ready. Never
gate the resolving instruction's own issue with the squash it generates.
Read ALU operands from registered PRF state. Feeding the current ALU result
straight back to its current inputs can form a combinational loop.
For redirect, the resolving branch is already head and still completes.
Every other occupied ROB/IQ/LSQ slot is younger. Checkpoint the RAT and
free-list head after the control's own allocation. Rewind to that state,
preserving the control's link destination and the free tail from older commits.
Train the predictor on the resolving instruction's PC, actual direction and
actual target. Decode the training branch/JAL/JALR kind from the issued instruction
in the IQ, since the frontend buffer may hold an unrelated instruction.
While reset is asserted, mask both external bus valid signals
combinationally so they are quiet even before the first clock edge.
"""


def required_modules(level: int) -> tuple[str, ...]:
    if level == 3:
        return ("rv32i5", *CACHE, "branch_predictor", *OOO)
    return BASE + (CACHE if level >= 1 else ()) + (("branch_predictor",) if level == 2 else ())


def l3_rob_head_feedback(source: str) -> bool:
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
    return bool(
        re.search(r"assign\s+head\s*=\s*head_next\s*;", source)
        and re.search(r"head_next\s*=\s*head\s*;", source)
        and re.search(r"assign\s+commit_valid\s*=\s*rob_valid\[head_next\]", source)
    )


def l3_rob_tail_advances_without_allocation(source: str) -> bool:
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
    return bool(
        re.search(r"assign\s+next_tail\s*=\s*tail\s*\+\s*3'd1\s*;", source)
        and re.search(r"end\s+tail\s*<=\s*next_tail\s*;\s*count\s*<=", source)
    )


def l3_rename_allocation_feedback(source: str) -> bool:
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
    return bool(
        re.search(r"assign\s+alloc_ok\s*=[^;]*\bfree_head_next\b", source)
        and re.search(r"assign\s+do_alloc\s*=[^;]*\balloc_ok\b", source)
        and re.search(r"if\s*\(do_alloc\)\s*begin\s*free_head_next\s*=", source)
    )


def l3_missing_fetch_buffer_guard(source: str) -> bool:
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
    request = re.search(r"assign\s+ic_cpu_req_valid\s*=([^;]*);", source)
    return bool(
        request and re.search(r"\bfetch_out_valid\b", request[1])
        and not re.search(r"\bif_buf_valid\b", request[1])
        and re.search(r"ic_cpu_resp_valid\s*&&\s*fetch_out_valid\s*&&"
                      r"\s*\(fetch_out_epoch\s*==\s*epoch\)\s*&&\s*!if_buf_valid", source)
    )


def l3_missing_debug_register_connection(source: str) -> bool:
    rename = module_instance_body(source, "rename_unit")
    return bool(
        re.search(r"\.dbg_data\s*\(\s*\)", rename)
        and re.search(r"assign\s+dbg_reg_data\s*=[^;]*\?\s*32'b0\s*:\s*32'b0\s*;", source)
    )


def l3_iq_dispatch_feedback(source: str) -> bool:
    dispatch = re.search(r"assign\s+dispatch_accept\s*=([^;]*);", source)
    ready = re.search(r"assign\s+iq_issue_ready\s*=([^;]*);", source)
    return bool(dispatch and ready and re.search(r"\blsu_enq_ready\s*:\s*iq_issue_ready\b", dispatch[1])
                and re.search(r"\biq_issue_valid\b", ready[1]))


def l3_load_dispatches_to_iq(source: str) -> bool:
    return bool(
        re.search(r"\.d_load\s*\(\s*d_load\s*\)", module_instance_body(source, "decode"))
        and re.search(r"\bassign\s+lsu_enqueue\s*=\s*dispatch_accept\s*&\s*d_store\s*;", source)
        and re.search(r"\bassign\s+iq_enqueue\s*=\s*dispatch_accept\s*&\s*~d_store\b", source)
    )


def l3_fetch_response_uses_live_pc(source: str) -> bool:
    return bool(
        re.search(r"\bif\s*\(fetch_req_valid\s*&&\s*fetch_req_ready\)\s*begin\s*fetch_pc\s*<=", source)
        and re.search(r"\bif\s*\(fetch_resp_valid\b[^)]*\)\s*begin\s*"
                      r"buf_valid\s*<=\s*1'b1\s*;\s*buf_pc\s*<=\s*fetch_pc\s*;", source)
    )


def forwarding_select_outputs(interfaces: str, module: str) -> set[str]:
    signatures = _port_signatures(interfaces, module)
    return {pin for pin in ("fwd_rs1_sel", "fwd_rs2_sel") if signatures.get(pin, "").startswith("output")}


def if_id_ready_message(name: str, actual: str, expected: str) -> str:
    """报错必须写出实际方向：旧文案把期望值当实际值，模型只会重复同一份计划。"""

    return (
        f"{name}.if_id_ready is {actual}, expected {expected} "
        "(ID owns decode readiness; if_id_ready is an output of id_stage "
        "and an input of everyone else)"
    )


PORT_DECLARATION = re.compile(
    r"(input|output|inout)\s+(?:(?:logic|wire|reg|signed|unsigned)\s+)*"
    r"(?:\[\s*\d+\s*:\s*\d+\s*\]\s*)?([A-Za-z_]\w*)"
)


def dedupe_plan_ports(plan: dict) -> dict:
    """Remove repeated declarations while retaining conflicting directions and widths for validation.

    实测 Planner 生成的计划里 mem_stage 把 mem_wb_fwd_data 声明两遍，
    校验连拒 4 轮、模型每次都重复同样的错误，整次运行 105 秒作废。
    重名是机械可判定的，直接修掉比回喂给模型更省。
    """

    modules = []
    for module in plan.get("modules", []):
        interfaces = module.get("interfaces") or ""
        if not isinstance(interfaces, str):
            modules.append(module)
            continue
        parts = [part.strip() for part in re.split(r"[;,\n]", interfaces) if part.strip()]
        seen: set[str] = set()
        kept: list[str] = []
        for part in parts:
            declaration = re.sub(r"\s*([\[\]:])\s*", r"\1", " ".join(part.split()))
            if declaration in seen:
                continue
            seen.add(declaration)
            kept.append(part)
        modules.append(dict(module, interfaces="; ".join(kept)))
    return dict(plan, modules=modules)


def validate_cpu_plan(plan: dict, level: int) -> None:
    modules = {item["name"]: item for item in plan["modules"]}
    missing = set(required_modules(level)) - modules.keys()
    if missing or plan["top_module"] != "rv32i5":
        raise ValueError(f"CPU Plan 缺少模块或顶层不符：{sorted(missing)}")
    errors = []
    declaration = PORT_DECLARATION
    for name, item in modules.items():
        if item["file"] != f"rtl/{name}.sv":
            errors.append(f"{name}: expected rtl/{name}.sv")
        declarations = [part.strip() for part in re.split(r"[;,\n]", item["interfaces"]) if part.strip()]
        ports = [declaration.fullmatch(part) for part in declarations]
        if not ports or any(port is None for port in ports):
            errors.append(f"{name}: supply one ANSI port declaration per semicolon")
        duplicates = [port for port, count in Counter(p[2] for p in ports if p).items() if count > 1]
        if duplicates:
            errors.append(f"{name}: duplicate formal ports {', '.join(duplicates)}")
        if level in (1, 2):
            for port in ports:
                if port and port[2] == "if_id_ready":
                    expected_direction = "output" if name == "id_stage" else "input"
                    if port[1] != expected_direction:
                        errors.append(if_id_ready_message(name, port[1], expected_direction))
        if level == 2 and name == "if_stage":
            directions = {"bp_lookup_pc": "output", "bp_lookup_next_pc": "input", "bp_lookup_valid": "input"}
            for port in ports:
                if port and port[2] in directions and port[1] != directions[port[2]]:
                    errors.append(f"if_stage.{port[2]} is {directions[port[2]]}; IF supplies PC and consumes prediction")
        if level in (1, 2) and name in IN_ORDER_BOUNDARIES:
            prefix, controls = IN_ORDER_BOUNDARIES[name]
            by_direction = {port.groups() for port in ports if port}
            expected_ports = [("input", "clk"), ("input", "rst_n"), *(("input", pin) for pin in controls),
                              *(("output", f"{prefix}_{field}") for field in ("valid", "pc", "insn"))]
            missing_ports = [f"{direction} {pin}" for direction, pin in expected_ports
                             if (direction, pin) not in by_direction]
            if missing_ports:
                errors.append(f"{name}: missing {', '.join(missing_ports)} for registered {prefix}")
        if level == 3 and name in L3_TAG_PORTS:
            expected = L3_TAG_PORTS[name]
            if re.sub(r"\s+", "", expected) not in {re.sub(r"\s+", "", part) for part in declarations}:
                errors.append(f"{name}: declare the allocation tag port as {expected}")
        if level == 3 and name != "reorder_buffer" and any(
            port and port.groups() == ("output", "alloc_rob_idx") for port in ports
        ):
            errors.append(f"{name}: alloc_rob_idx is owned by reorder_buffer; do not declare an allocator output")
    if not errors and level in (1, 2):
        ex_outputs = forwarding_select_outputs(modules["ex_stage"]["interfaces"], "ex_stage")
        fwd_outputs = forwarding_select_outputs(modules["forwarding_unit"]["interfaces"], "forwarding_unit")
        if shared := ex_outputs & fwd_outputs:
            errors.append("forwarding_unit owns " + ", ".join(sorted(shared))
                          + "; declare those selectors as ex_stage inputs only")
        ex_ports = _port_signatures(modules["ex_stage"]["interfaces"], "ex_stage")
        fwd_ports = _port_signatures(modules["forwarding_unit"]["interfaces"], "forwarding_unit")
        for pin in ("fwd_a_sel", "fwd_b_sel"):
            if pin in ex_ports and pin in fwd_ports and (ex_ports[pin], fwd_ports[pin]) != (
                "input[1:0]", "output[1:0]",
            ):
                errors.append(f"{pin}: use a 2-bit forwarding_unit output and EX input for RF, EX/MEM and MEM/WB")
    if errors:
        raise ValueError(" | ".join(errors))


def validate_l3_operand_ports(plan: dict) -> None:
    modules = {m["name"]: m["interfaces"] for m in plan["modules"]}
    missing = []
    for name, ports in L3_OPERAND_PORTS.items():
        declared = {re.sub(r"\s+", "", part) for part in re.split(r"[;,\n]", modules.get(name, ""))}
        missing.extend(f"{name}: {port}" for port in ports if re.sub(r"\s+", "", port) not in declared)
    if missing:
        raise ValueError("L3 queue/PRF ports missing: " + "; ".join(missing))


def _port_signatures(ports: str, module: str) -> dict[str, str]:
    result = {}
    declaration = ""
    for part in re.split(r"[;,\n]", ports):
        part = part.strip()
        if not part:
            continue
        match = re.search(r"\b([A-Za-z_]\w*)$", part)
        if not match:
            raise ValueError(f"{module}: invalid port declaration {part}")
        pin = match[1]
        prefix = part[:match.start()]
        if prefix:
            declaration = re.sub(r"\b(?:logic|wire|reg)\b|\s+", "", prefix)
        if pin in result:
            raise ValueError(f"{module}: duplicate port {pin}")
        result[pin] = declaration
    return result


def validate_generated_ports(files: dict[str, str], modules: list[dict]) -> None:
    for module in modules:
        path = module["file"]
        name = module["name"]
        actual = frozen_component_interfaces({path: files[path]})[path]
        planned = _port_signatures(module["interfaces"], name)
        declared = _port_signatures(actual, name)
        if planned != declared:
            differences = sorted(pin for pin in planned.keys() | declared.keys() if planned.get(pin) != declared.get(pin))
            raise ValueError(f"{path}: ports differ from the frozen plan: {', '.join(differences)}")


def frozen_component_interfaces(files: dict[str, str]) -> dict[str, str]:
    interfaces = {}
    for path, source in files.items():
        name = path.removeprefix("rtl/").removesuffix(".sv")
        source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
        header = re.search(rf"\bmodule\s+{re.escape(name)}\s*\((.*?)\)\s*;", source, re.S)
        if not header:
            raise ValueError(f"无法读取已验证组件接口：{path}")
        interfaces[path] = "; ".join(" ".join(port.split()) for port in header[1].split(","))
    return interfaces


def validate_frozen_components(plan: dict, interfaces: dict[str, str]) -> None:
    modules = {m["file"]: m["interfaces"] for m in plan["modules"]}
    for path, expected in interfaces.items():
        planned_ports = {re.sub(r"\s+", "", port) for port in re.split(r"[;,\n]", modules.get(path, "")) if port.strip()}
        seed_ports = {re.sub(r"\s+", "", port) for port in expected.split(";")}
        if planned_ports != seed_ports:
            raise ValueError(f"{path} 必须保留已验证组件的端口：{expected}")

def module_headers(source: str) -> dict[str, str]:
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
    headers = {}
    for match in re.finditer(r"\bmodule\s+([A-Za-z_][A-Za-z_0-9]*)\b([^;]*);", source):
        name = match[1]
        if name in headers:
            raise ValueError(f"重复 module：{name}")
        headers[name] = re.sub(r"\s+", "", match[2])
    return headers


def module_instance_body(source: str, module: str) -> str:
    """Return the named port bindings from an ordinary module instance."""
    instance = re.search(
        rf"\b{re.escape(module)}\s+(?:#\s*\([^;]*?\)\s*)?\w+\s*\((.*?)\)\s*;", source, re.S,
    )
    return instance[1] if instance else ""


def ex_mem_control_bypasses(source: str) -> dict[str, str]:
    """Find EX/MEM outputs wired directly to their unregistered ID/EX inputs."""
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
    return {
        output: incoming
        for output, incoming in re.findall(r"\bassign\s+(ex_mem_\w+)\s*=\s*(id_ex_\w+)\s*;", source)
        if output.removeprefix("ex_mem_") in {
            "rd", "reg_write", "mem_read", "mem_write", "is_load", "alu_result",
            "store_data", "rs2", "funct3", "is_ecall", "wb_sel", "epoch", "pred_next_pc", "pred_valid",
        }
    }


def in_order_boundary_wiring(files: dict[str, str], *, check_controls: bool = False) -> None:
    """Check that each planned pipeline boundary actually retains its payload."""
    top = re.sub(r"/\*.*?\*/|//[^\n]*", "", files["rtl/rv32i5.sv"], flags=re.S)
    errors = []
    for module, (prefix, controls) in IN_ORDER_BOUNDARIES.items():
        source = re.sub(r"/\*.*?\*/|//[^\n]*", "", files[f"rtl/{module}.sv"], flags=re.S)
        state = set(re.findall(r"(?m)(?:^|;|\bbegin\b|\belse\b|\))\s*([A-Za-z_]\w*)\s*<=", source))
        aliases = dict(re.findall(r"\bassign\s+(\w+)\s*=\s*(\w+)\s*;", source))
        if not re.search(r"\balways(?:_ff)?\s*@\s*\(\s*posedge\s+clk\b", source):
            state.clear()
        for field in ("valid", "pc", "insn"):
            port = f"{prefix}_{field}"
            net = port
            visited = set()
            while net in aliases and net not in visited:
                visited.add(net)
                net = aliases[net]
            if net not in state:
                errors.append(f"{module}.{port} needs a rising-edge register or a direct alias of its state")
        instance = module_instance_body(top, module)
        for pin in controls:
            if not re.search(rf"\.{pin}\s*\(\s*[A-Za-z_]\w*\s*\)", instance):
                errors.append(f"rv32i5 must connect {module}.{pin} to a control net")
    if check_controls:
        if shared := in_order_forwarding_collision(files):
            errors.append("forwarding_unit owns " + ", ".join(sorted(shared))
                          + "; EX must consume them as inputs and remove its duplicate drivers")
        for output, incoming in ex_mem_control_bypasses(files["rtl/ex_stage.sv"]).items():
            errors.append(f"ex_stage.{output} bypasses EX/MEM state from {incoming}; capture with valid/PC")
    if errors:
        raise ValueError(" | ".join(errors))


def in_order_forwarding_collision(files: dict[str, str]) -> set[str]:
    ex_path, fwd_path = "rtl/ex_stage.sv", "rtl/forwarding_unit.sv"
    top = files.get("rtl/rv32i5.sv", "")
    if ex_path not in files or fwd_path not in files:
        return set()
    try:
        headers = frozen_component_interfaces({path: files[path] for path in (ex_path, fwd_path)})
        shared = (forwarding_select_outputs(headers[ex_path], "ex_stage")
                  & forwarding_select_outputs(headers[fwd_path], "forwarding_unit"))
    except ValueError:
        return set()
    ex, fwd = (module_instance_body(top, name) for name in ("ex_stage", "forwarding_unit"))
    collisions = set()
    for pin in shared:
        bind = rf"\.{pin}\s*\(\s*(\w+)\s*\)"
        ex_net, fwd_net = re.search(bind, ex), re.search(bind, fwd)
        if ex_net and fwd_net and ex_net[1] == fwd_net[1]:
            collisions.add(pin)
    return collisions


class AllocationBindingError(ValueError):
    """The planned allocation port exists but its top-level binding is wrong."""


def l3_allocation_wiring(files: dict[str, str]) -> None:
    """Check that the allocated ROB entry reaches the two dispatch queues."""
    top = re.sub(r"/\*.*?\*/|//[^\n]*", "", files["rtl/rv32i5.sv"], flags=re.S)
    tags = {}
    for module, declaration in L3_TAG_PORTS.items():
        formal = declaration.rsplit(" ", 1)[1]
        header = module_headers(files[f"rtl/{module}.sv"])[module]
        port = re.escape(re.sub(r"\s+", "", declaration))
        if not re.search(rf"[(,]{port}[,)]", header):
            raise ValueError(f"{module}.{formal} does not match the frozen allocation port")
        connection = re.search(rf"\.{formal}\s*\(\s*([A-Za-z_]\w*)\s*\)", module_instance_body(top, module))
        if not connection:
            raise AllocationBindingError(f"{module}.{formal} must be bound to a named top-level net")
        tags[module] = connection[1]
    aliases = dict(re.findall(r"\bassign\s+(\w+)\s*=\s*(\w+)\s*;", top))
    rename = re.sub(r"/\*.*?\*/|//[^\n]*", "", files.get("rtl/rename_unit.sv", ""), flags=re.S)
    if re.search(r"\bassign\s+disp_rob_idx\s*=\s*alloc_rob_idx\s*;", rename):
        rename_header = module_headers(rename).get("rename_unit", "")
        if ("inputlogic[2:0]alloc_rob_idx" in rename_header
                and "outputlogic[2:0]disp_rob_idx" in rename_header):
            instance = module_instance_body(top, "rename_unit")
            incoming = re.search(r"\.alloc_rob_idx\s*\(\s*(\w+)\s*\)", instance)
            outgoing = re.search(r"\.disp_rob_idx\s*\(\s*(\w+)\s*\)", instance)
            if incoming and outgoing:
                aliases[outgoing[1]] = incoming[1]
    rob_net = tags["reorder_buffer"]
    for sink in ("issue_queue", "load_store_unit"):
        path = [tags[sink]]
        while path[-1] != rob_net and path[-1] in aliases and aliases[path[-1]] not in path:
            path.append(aliases[path[-1]])
        if path[-1] != rob_net:
            raise AllocationBindingError(
                f"{sink}.disp_rob_idx follows {' <- '.join(path)}; connect it to ROB alloc_rob_idx net {rob_net}"
            )


def parse_cpu_sources(answer: str, plan: dict, run_dir) -> dict[str, str]:
    sections = re.split(r"(?m)^// file: (rtl/[A-Za-z0-9_]+\.sv)[ \t]*\n", answer)
    if sections[0].strip() or len(sections) == 1:
        raise ValueError("Coder 未使用约定的文件分隔符")
    expected = {module["file"]: module["name"] for module in plan["modules"]}
    files = {}
    for path, source in zip(sections[1::2], sections[2::2]):
        if path in files or path not in expected:
            raise ValueError(f"Coder 返回了计划之外或重复的文件：{path}")
        safe_project_path(run_dir, path)
        if set(module_headers(source)) != {expected[path]} or len(re.findall(r"(?m)^\s*endmodule\b", source)) != 1:
            raise ValueError(f"Coder 文件 {path} 必须包含计划中的完整模块 {expected[path]}")
        files[path] = source.strip() + "\n"
    if files.keys() != expected.keys():
        raise ValueError(f"Coder 未返回这些文件：{sorted(expected.keys() - files.keys())}")
    return {path: files[path] for path in expected}
