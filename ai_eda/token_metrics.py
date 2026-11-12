"""统一的模型输出 token 统计。

模型流式返回时通常不会逐块给出 usage；这里用确定性的字符估算提供实时计数，
收到最终 usage 后优先使用服务端 completion_tokens。所有 Agent 步骤、运行汇总
和网页显示都只统计“输出 token”，不把 prompt token 混入。
"""

from __future__ import annotations

import math
from typing import Any


def estimate_output_tokens(text: str) -> int:
    """估算输出 token：CJK 字符约 1 token，其他字符约 4 字符/token。"""
    value = str(text or "")
    if not value:
        return 0
    cjk = 0
    for char in value:
        code = ord(char)
        if (
            0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF
        ):
            cjk += 1
    other = max(0, len(value) - cjk)
    return max(1, cjk + math.ceil(other / 4))


def output_tokens(usage: dict[str, Any] | None, text: str) -> int:
    """最终 token：优先服务端 completion_tokens，否则使用统一估算。"""
    if isinstance(usage, dict):
        value = usage.get("completion_tokens")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return int(value)
    return estimate_output_tokens(text)
