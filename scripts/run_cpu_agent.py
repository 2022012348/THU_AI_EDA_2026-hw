"""Run or resume a CPU design and print its course acceptance summary."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_eda.agent.orchestrator import run_agent
from ai_eda.config import AGENT_RUNS_ROOT
from ai_eda.model.client import ModelClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--max-calls", type=int, default=8)
    parser.add_argument("--max-repairs", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=60000)
    parser.add_argument("--resume-from", metavar="RUN_ID")
    parser.add_argument("--resume-attempt", type=int, metavar="TESTER_ROUND")
    parser.add_argument("--component-seed-from", metavar="VERIFIED_L2_RUN_ID")
    parser.add_argument("--thinking", action="store_true")
    args = parser.parse_args()
    ModelClient.BASE_URL = os.environ.get("EDA_BASE_URL", ModelClient.BASE_URL)
    ModelClient.MODEL_NAME = os.environ.get("EDA_MODEL", ModelClient.MODEL_NAME)
    ModelClient.REQUEST_TIMEOUT = float(os.environ.get("EDA_API_TIMEOUT", "180"))
    ModelClient.MAX_RETRIES = 0
    run_id = None

    def emit(event):
        nonlocal run_id
        run_id = event["run_id"]
        if event.get("status") not in {"running", "waiting_input"}:
            print(event["node_id"], event["attempt"], event["status"], flush=True)

    try:
        result = run_agent(
            project_id={1: "rv32i5_cache", 2: "rv32i5_predict", 3: "rv32i5_ooo"}[args.level],
            max_calls=args.max_calls, max_repair_attempts=args.max_repairs,
            max_tokens=args.max_tokens, thinking=args.thinking, on_event=emit,
            resume_from=args.resume_from, resume_attempt=args.resume_attempt,
            component_seed_from=args.component_seed_from,
        )
        return 0 if result["status"] == "passed" else 1
    finally:
        if run_id:
            summary = AGENT_RUNS_ROOT / run_id / "run_summary.json"
            print(summary, flush=True)
            if summary.is_file():
                print(json.dumps(json.loads(summary.read_text()), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
