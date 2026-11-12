"""按 run_summary 的 token 用量估算每次运行花了多少钱。

价格默认按 paratera 的 DeepSeek-V4.1-Flash 牌价（元 / 百万 tokens）：
输入（缓存未命中）2、输出 8、缓存命中 0.04。可用环境变量覆盖，
也可以按官方空闲时段价（1 / 4 / 0.02）算一版做区间。

用量来源优先级：run_summary["tokens"]（真实）→ 逐事件 usage 求和（真实）
→ 对话/应答字符数估算（标注 est，仅当两者都没有时使用）。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path

PRICE_IN = float(os.environ.get("EDA_PRICE_IN", "2"))
PRICE_OUT = float(os.environ.get("EDA_PRICE_OUT", "8"))
PRICE_CACHE = float(os.environ.get("EDA_PRICE_CACHE", "0.04"))

# 混合中文 + Verilog 的实测比例（一次 23 字符提问 → 9 prompt tokens）。
CHARS_PER_PROMPT_TOKEN = float(os.environ.get("EDA_CHARS_PER_PROMPT_TOKEN", "2.6"))
CHARS_PER_OUTPUT_TOKEN = float(os.environ.get("EDA_CHARS_PER_OUTPUT_TOKEN", "3.0"))


def _event_usage(run_dir: Path) -> tuple[int, int, int] | None:
    events = run_dir / "events.json"
    if not events.exists():
        return None
    payload = json.loads(events.read_text(encoding="utf-8"))
    prompt = completion = cached = 0
    seen = 0
    for event in payload.get("events", []):
        usage = event.get("usage") or {}
        if not isinstance(usage, dict):
            continue
        if usage.get("prompt_tokens") is None:
            continue
        seen += 1
        prompt += int(usage.get("prompt_tokens") or 0)
        completion += int(usage.get("completion_tokens") or 0)
        cached += int(usage.get("cached_tokens") or 0)
    return (prompt, completion, cached) if seen else None


def _chars(run_dir: Path) -> tuple[int, int]:
    prompt = 0
    for path in glob.glob(str(run_dir / "step-*" / "conversation.json")):
        for message in json.loads(Path(path).read_text(encoding="utf-8")):
            if isinstance(message, dict) and message.get("role") != "assistant":
                content = message.get("content")
                prompt += len(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
    events = run_dir / "events.json"
    completion = 0
    if events.exists():
        payload = json.loads(events.read_text(encoding="utf-8"))
        completion = sum(len(event.get("response") or "") for event in payload.get("events", []))
    return prompt, completion


def usage_for(run_dir: Path) -> tuple[int, int, int, str]:
    summary_path = run_dir / "run_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    tokens = summary.get("tokens") or {}
    if tokens.get("prompt_tokens") is not None:
        return (
            int(tokens["prompt_tokens"]), int(tokens.get("completion_tokens") or 0),
            int(tokens.get("cached_tokens") or 0), "real",
        )
    event_usage = _event_usage(run_dir)
    if event_usage:
        return (*event_usage, "real")
    prompt_chars, completion_chars = _chars(run_dir)
    return (
        round(prompt_chars / CHARS_PER_PROMPT_TOKEN),
        round(completion_chars / CHARS_PER_OUTPUT_TOKEN),
        0, "est",
    )


def cost(prompt: int, completion: int, cached: int) -> float:
    fresh = max(prompt - cached, 0)
    return (fresh * PRICE_IN + cached * PRICE_CACHE + completion * PRICE_OUT) / 1_000_000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patterns", nargs="*", help="run 目录或通配符（默认今天全部 run）")
    args = parser.parse_args()
    patterns = args.patterns or [str(Path("runs/agent/agent-*"))]
    dirs: list[Path] = []
    for pattern in patterns:
        dirs.extend(Path(p) for p in sorted(glob.glob(pattern)) if (Path(p) / "run_summary.json").exists())
    total = 0.0
    rows = []
    for run_dir in dirs:
        summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
        if not summary.get("independent"):
            continue
        prompt, completion, cached, source = usage_for(run_dir)
        yuan = cost(prompt, completion, cached)
        total += yuan
        rows.append((run_dir.name[-14:], summary.get("status"), summary.get("failed_checks"),
                     summary.get("model_calls"), prompt, completion, source, yuan))
    print(f"{'run':14s} {'status':7s} {'checks':>8s} {'calls':>5s} {'prompt':>9s} {'compl':>8s} {'src':4s} {'¥':>7s}")
    for row in rows:
        name, status, checks, calls, prompt, completion, source, yuan = row
        print(f"{name:14s} {str(status):7s} {str(checks):>8s} {calls:>5d} {prompt:>9d} {completion:>8d} {source:4s} {yuan:7.3f}")
    print(f"\n{len(rows)} runs, total ¥{total:.2f}, avg ¥{total / max(len(rows), 1):.3f}")
    print(f"price: in {PRICE_IN}/M (cache miss), cache {PRICE_CACHE}/M, out {PRICE_OUT}/M")


if __name__ == "__main__":
    main()
