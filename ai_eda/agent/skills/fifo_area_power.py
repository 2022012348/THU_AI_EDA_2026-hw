"""同步 FIFO Planner Skill：面积/功耗预评估与架构参数选择。

该模块不生成 RTL；它在 Planner 调用模型之前运行，用小规模估算模型比较几种
可综合 FIFO 实现，输出 Coder 必须遵守的架构参数。
"""

from __future__ import annotations

import re
from typing import Any


def _extract_int(pattern: str, text: str, default: int) -> int:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return default
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return default


def _candidate(depth: int, width: int, checksum_bits: int) -> list[dict[str, Any]]:
    storage_bits = depth * width
    pointer_bits = max(1, (depth - 1).bit_length())
    count_bits = max(1, (depth).bit_length())
    # 估算单位：触发器权重、组合逻辑权重和动态活动权重。
    # A: 双指针 + count 寄存器（常见默认实现）
    dual_area = storage_bits * 1.0 + pointer_bits * 10 + count_bits * 8 + 8.0
    dual_power = storage_bits * 1.0 + pointer_bits * 10 + count_bits * 8 + 4.0
    # B: 单写指针 + count 寄存器，读指针由 wr_ptr-count 组合得到。
    # 少一组读指针触发器，计数更新和读地址组合逻辑也明显更小。
    single_area = storage_bits * 1.0 + pointer_bits * 6 + count_bits * 5 + 5.0
    single_power = storage_bits * 1.0 + pointer_bits * 6 + count_bits * 5 + 3.0
    # C: 双指针 + full flag，避免 count 寄存器，但需要额外回绕比较。
    flag_area = storage_bits * 1.0 + pointer_bits * 12 + 10.0
    flag_power = storage_bits * 1.0 + pointer_bits * 12 + 2.0
    # D: 移位链，面积/活动都更大，保留作为被拒绝候选。
    shift_area = storage_bits * 1.0 + width * depth * 1.2 + 20.0
    shift_power = storage_bits * 1.0 + width * depth * 1.6
    return [
        {
            "name": "single_write_pointer_with_count",
            "area_units": round(single_area, 2),
            "relative_power": round(single_power, 2),
            "reason": "单写指针 + 4-bit count；读指针组合计算，少一组读指针触发器",
        },
        {
            "name": "dual_pointer_with_count",
            "area_units": round(dual_area, 2),
            "relative_power": round(dual_power, 2),
            "reason": "双指针 + count 寄存器；常见但多一组读指针触发器",
        },
        {
            "name": "dual_pointer_full_flag",
            "area_units": round(flag_area, 2),
            "relative_power": round(flag_power, 2),
            "reason": "双指针 + full flag；省 count 寄存器但增加回绕比较",
        },
        {
            "name": "shift_register_chain",
            "area_units": round(shift_area, 2),
            "relative_power": round(shift_power, 2),
            "reason": "8 级移位链；每个有效写都会搬移整条链，动态功耗和布线较高",
        },
    ]


def evaluate(spec_text: str) -> dict[str, Any]:
    """返回 FIFO 的架构参数和估算报告，保证 JSON 可序列化。"""
    text = spec_text or ""
    burst = _extract_int(r"支持\s*([0-9]+)\s*字节连续(?:写)?突发", text, 16)
    burst = max(1, min(burst, 32))
    # DSE-1：在 16/32 深度中选择满足连续突发的最小深度。
    depth = 16 if burst <= 16 else 32
    width = _extract_int(r"数据位宽(?:固定)?为\s*([0-9]+)", text, 8)
    width = max(1, min(width, 64))
    checksum_bits = _extract_int(r"([0-9]+)-bit\s*(?:递增)?校验和", text, 16)
    checksum_bits = max(1, min(checksum_bits, 128))
    pointer_bits = max(1, (depth - 1).bit_length())
    count_bits = max(1, depth.bit_length())
    candidates = _candidate(depth, width, checksum_bits)
    best = min(candidates, key=lambda item: (item["area_units"], item["relative_power"]))
    rejected = [item for item in candidates if item is not best]
    return {
        "skill": "fifo_area_power_pre_evaluation",
        "version": 1,
        "project": "stream_filter",
        "input": {
            "required_burst": burst,
            "depth": depth,
            "width": width,
            "checksum_bits": checksum_bits,
        },
        "selected": best["name"],
        "estimated_area_units": best["area_units"],
        "estimated_relative_power": best["relative_power"],
        "rejected": rejected,
        "parameters": {
            "fifo_storage": "circular_register_file",
            "write_pointer_bits": pointer_bits,
            "write_pointer": "increment_on_accepted_write",
            "read_pointer": "combinational_from_write_pointer_minus_count",
            "read_pointer_register": "forbidden",
            "count": "registered_occupancy",
            "count_register": "required",
            "count_bits": count_bits,
            "dout": "combinational_head_mux",
            "output_register": "forbidden",
            "checksum_adder": f"{checksum_bits}-bit_ripple_carry",
            "checksum_register": "single_accumulator",
            "top_level": "direct_wiring_no_pipeline_register",
        },
        "reference_structure": {
            "sync_fifo": [
                f"localparam int ADDR_BITS = {pointer_bits};",
                f"logic [7:0] storage [0:{depth - 1}];",
                f"logic [{pointer_bits - 1}:0] wr_ptr;",
                f"logic [{count_bits - 1}:0] count_q;",
                f"logic [{pointer_bits - 1}:0] rd_ptr;",
                f"assign rd_ptr = wr_ptr - count_q[{pointer_bits - 1}:0];",
                "assign do_wr = wr_en && !full;",
                "assign do_rd = rd_en && !empty;",
                "assign dout = storage[rd_ptr];",
                "always_ff only updates wr_ptr/count_q/storage; no rd_ptr <= ...",
            ],
            "checksum_acc": "一个 16-bit 累加寄存器 + 一个 16-bit 加法器",
            "stream_filter": "只实例化 sync_fifo 与 checksum_acc，组合连接 full/empty/valid",
        },
        "coder_directives": [
            "sync_fifo 使用 storage[0:DEPTH-1] 寄存器阵列，不要用 8 级移位链",
            f"只实例化 wr_ptr（{pointer_bits} bit）和 count_q（{count_bits} bit），不要额外实例化 rd_ptr 寄存器",
            f"rd_ptr 由 (wr_ptr - count_q) 的低 {pointer_bits} bit 组合计算，dout=storage[rd_ptr]",
            "empty=(count_q==0)，full=(count_q==DEPTH)；write gated by !full, read gated by !empty；读写同时有效时 count_q 保持不变",
            "dout 由当前 rd_ptr 组合选择，不添加输出流水寄存器",
            "checksum_acc 只保留一个 16-bit 累加寄存器和一个 16-bit 加法器",
            "stream_filter 顶层只连接已有模块，不增加额外寄存/缓冲级",
            "top: assign in_ready = ~fifo_full；reset 后 count_q=0 使 full=0，in_ready 自然为 1，不要额外加 rst_n 选择 mux",
        ],
        "rationale": [
            "移除独立的读指针寄存器，改为 wr_ptr - count 组合计算，减少 3 个触发器",
            "保留 count 寄存器用于 full/empty 和 count 输出，避免额外的指针回绕比较",
            "只写目标存储项，避免移位链每个周期搬移全部 8 个字节",
            "预估数据为相对比较值，最终以教师 TB 的 Yosys 面积与 OpenSTA 功耗为准",
        ],
    }
