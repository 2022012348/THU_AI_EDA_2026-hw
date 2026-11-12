#!/usr/bin/env python3
"""提交前扫描：拦下形如 API Key 的字面量，避免把凭据写进仓库。

用法：
    python scripts/check_secrets.py            # 扫描全部已跟踪文件
    python scripts/check_secrets.py --staged   # 只扫描暂存区（pre-commit 用）

判定规则（命中任一即失败）：
    * sk- 后接 20 个以上 [A-Za-z0-9_-]
    * 形如 xxx_api_key = "..." 且字符串长度 >= 20（排除占位符 sk-... / 你的Key）
"""

from __future__ import annotations

import re
import subprocess
import sys

KEY = re.compile(r"sk-[A-Za-z0-9_-]{20,}")
ASSIGN = re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"]([A-Za-z0-9_\-]{20,})['\"]")


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p]


def staged() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        capture_output=True, check=True,
    ).stdout
    return [p for p in out.decode().split("\0") if p]


def scan(paths: list[str]) -> list[str]:
    problems: list[str] = []
    for path in paths:
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if path.endswith(".zip"):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if KEY.search(line) and "sk-..." not in line and "sk-在这" not in line:
                problems.append(f"{path}:{lineno}: 疑似 API Key")
            elif ASSIGN.search(line):
                value = ASSIGN.search(line).group(2)
                if not value.startswith("sk-") and len(value) >= 20 and not set(value) <= set("0123456789abcdefABCDEF"):
                    problems.append(f"{path}:{lineno}: 疑似密钥字面量")
    return problems


def main() -> int:
    paths = staged() if "--staged" in sys.argv else tracked()
    problems = scan(paths)
    if problems:
        print("发现疑似凭据，已拒绝：")
        for item in problems:
            print("  " + item)
        return 1
    print(f"SECRETS|ok|files={len(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
