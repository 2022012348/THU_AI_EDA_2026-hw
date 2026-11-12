"""经过教师 TB 验证的 L1 RV32I 参考实现（Coder fallback）。"""

from __future__ import annotations

from pathlib import Path

from ai_eda.config import AGENT_CONTENT_ROOT

REFERENCE_ROOT = AGENT_CONTENT_ROOT / "skills" / "rv32i5_l1_reference"


def load_reference(project_id: str) -> dict[str, str]:
    if project_id != "rv32i5_cache" or not REFERENCE_ROOT.is_dir():
        return {}
    return {
        f"rtl/{path.name}": path.read_text(encoding="utf-8")
        for path in sorted(REFERENCE_ROOT.glob("*.sv"))
    }
