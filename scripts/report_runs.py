"""列出最近的独立运行：结果、失败数、调用、token、花费、时长。

用法：
    python scripts/report_runs.py            # 最近 5 次独立运行
    python scripts/report_runs.py --last 10  # 最近 10 次
    python scripts/report_runs.py --all      # 今天全部
    python scripts/report_runs.py --since 2026-11-12T10:00
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

from cost_report import cost, usage_for


def _runs(pattern: str) -> list[Path]:
    dirs = [Path(p) for p in sorted(glob.glob(pattern))]
    return [d for d in dirs if (d / "run_summary.json").exists()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--last", type=int, default=5)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--since", default=None)
    parser.add_argument("--pattern", default="runs/agent/agent-*")
    args = parser.parse_args()

    rows = []
    for run_dir in _runs(args.pattern):
        summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
        if not summary.get("independent"):
            continue
        started = summary.get("started_at", "")
        if args.since and started < args.since:
            continue
        rows.append((started, summary, run_dir))
    rows.sort(key=lambda row: row[0])
    if not args.all:
        rows = rows[-args.last :]

    print(f"{'started':20s} {'status':7s} {'checks':>8s} {'calls':>5s} {'tester':>6s} {'time':>6s} {'tokens(in/out)':>18s} {'¥':>6s}")
    total = 0.0
    passed = 0
    for started, summary, run_dir in rows:
        prompt, completion, cached, source = usage_for(run_dir)
        yuan = cost(prompt, completion, cached)
        total += yuan
        passed += 1 if summary.get("status") == "passed" else 0
        mark = "" if source == "real" else "*"
        print(
            f"{started[:19]:20s} {str(summary.get('status')):7s} {str(summary.get('failed_checks')):>8s} "
            f"{summary.get('model_calls'):>5d} {summary.get('tester_attempts'):>6d} "
            f"{round(summary.get('duration_s') or 0):>5d}s {prompt:>8d}/{completion:<8d}{mark} {yuan:6.3f}"
        )
    count = max(len(rows), 1)
    print(f"\n{len(rows)} runs, passed {passed} ({passed / count:.0%}), total ¥{total:.2f}, avg ¥{total / count:.3f}")
    if any(usage_for(r)[3] != "real" for _, _, r in rows):
        print("* 该行 token 为按字符估算（旧运行未记录 usage）")


if __name__ == "__main__":
    main()
