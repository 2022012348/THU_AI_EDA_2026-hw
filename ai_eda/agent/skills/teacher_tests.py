"""只读抽取教师 TB 里某个用例的建程序/检查任务源码。

Diagnoser 只看到 "t11_halt 等待 halted 超时，imem_addr=0x0c" 时，Patch Agent 必须猜
这个用例装的是什么程序；而决定性的信息（load 之后紧跟 ecall、ECALL 必须等老 load
提交）就写在 build_t11_halt/test_t11_halt 里。本模块把这段原文取出来塞进 brief。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from ai_eda.config import CONTENT_ROOT

TB_ROOT = CONTENT_ROOT / "tasks" / "homework_rv32i5" / "tb"
MAX_LINES = 90


def _task_body(text: str, name: str) -> str | None:
    pattern = re.compile(rf"\btask\s+automatic\s+{re.escape(name)}\s*\((.*?)\)\s*;(.*?)\n\s*endtask", re.S)
    match = pattern.search(text)
    if not match:
        return None
    return f"task automatic {name}({match.group(1)});{match.group(2)}\nendtask"


def test_source(testcase: str | None, *, root: Path | None = None, max_lines: int = MAX_LINES) -> str:
    """Return the teacher TB source for one test case, or "" when unknown."""

    if not testcase:
        return ""
    name = str(testcase).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
        return ""
    directory = root or TB_ROOT
    if not directory.is_dir():
        return ""
    parts: list[str] = []
    for path in _tb_files(directory):
        text = path.read_text(encoding="utf-8", errors="replace")
        if f"build_{name}" not in text and f"task automatic {name}" not in text:
            continue
        for task in (f"build_{name}", f"test_{name}"):
            body = _task_body(text, task)
            if body:
                parts.append(body)
        if parts:
            header = f"// teacher TB source: {path.name}"
            lines = "\n\n".join(parts).splitlines()
            if len(lines) > max_lines:
                lines = lines[:max_lines] + ["// ... truncated"]
            return "\n".join([header, *lines])
    return ""


def _tb_files(directory: Path) -> Iterable[Path]:
    for path in sorted(directory.glob("*.svh")):
        yield path
