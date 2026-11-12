"""固定 Agent 流程的受限 EDA 自动设计执行器。

固定流水线：Parser → RTL Planner → Coder → Tester → Diagnoser → Patch Agent（失败回环）。
Parser 需要补充 Spec 时通过 ``request_input`` 暂停并等待用户输入。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai_eda.agent.projects import get_project
from ai_eda.agent.skills.rv32i5_contract import source_is_eligible_for_tier
from ai_eda.agent.reporting import report_preview
from ai_eda.agent.runs import (
    AgentCancelled,
    _now,
    _report_summary,
    _run_payload,
    _write_json,
    _write_metadata,
    agent_task_spec,
    load_agent_graph,
)
from ai_eda.agent.subagents.base import RunContext
from ai_eda.agent.subagents.coder import CoderAgent
from ai_eda.agent.subagents.common import safe_project_path
from ai_eda.agent.patches import PatchProtocolError
from ai_eda.agent.snapshots import fingerprint, load_snapshot, load_snapshot_at, resolve_artifact, save_snapshot
from ai_eda.agent.subagents.diagnoser import DiagnoserAgent
from ai_eda.agent.subagents.patch_agent import PatchAgent
from ai_eda.agent.subagents.repair import PROBE_TARGETS, diagnostic_repair_progress, probe_failures, repair_verdict
from ai_eda.agent.subagents.parser import ParserAgent
from ai_eda.agent.subagents.planner import PlannerAgent
from ai_eda.agent.subagents.tester import TesterAgent
from ai_eda.agent.cpu_workflow import frozen_component_interfaces
from ai_eda.agent.skills.l3_architecture import L3_ARCHITECTURE, load_l3_architecture
from ai_eda.config import AGENT_RUNS_ROOT

EventSink = Callable[[dict[str, Any]], None]


def _check_cancel(cancel_event: Any) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise AgentCancelled("Agent 运行已取消。")


def _load_resume_source(run_id: str) -> dict[str, Any]:
    """读取上一次运行的 Spec/Plan，用于从 Coder 继续执行。"""
    name = str(run_id or "").strip()
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError("resume_from 必须是单个运行目录名")
    root = AGENT_RUNS_ROOT.resolve()
    source = (root / name).resolve()
    if root not in source.parents or not source.is_dir():
        raise FileNotFoundError(f"找不到要续跑的运行：{name}")
    artifacts = source / "artifacts"
    spec_path = artifacts / "effective_spec.md"
    if not spec_path.is_file():
        spec_path = artifacts / "spec.md"
    plan_path = artifacts / "plan.json"
    if not spec_path.is_file():
        raise ValueError("上一次运行没有 Spec artifact，无法续跑")
    spec = spec_path.read_text(encoding="utf-8")
    plan: dict[str, Any] | None = None
    if plan_path.is_file():
        loaded_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if isinstance(loaded_plan, dict) and isinstance(loaded_plan.get("modules"), list):
            plan = loaded_plan
    parser_result: dict[str, Any] = {}
    parser_path = artifacts / "parser_result.json"
    if parser_path.is_file():
        try:
            parsed = json.loads(parser_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                parser_result = parsed
        except json.JSONDecodeError:
            pass
    events_payload: dict[str, Any] = {}
    events_path = source / "events.json"
    if events_path.is_file():
        try:
            loaded = json.loads(events_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                events_payload = loaded
        except json.JSONDecodeError:
            pass
    source_events = events_payload.get("events") if isinstance(events_payload.get("events"), list) else []
    failed_event: dict[str, Any] | None = None
    for event in reversed(source_events):
        if not isinstance(event, dict):
            continue
        if event.get("status") in {"failed", "cancelled", "running"}:
            failed_event = event
            break
    if failed_event is None and source_events:
        failed_event = source_events[-1] if isinstance(source_events[-1], dict) else None
    stage = str((failed_event or {}).get("node_id") or "coder")
    if plan is None and stage not in {"parser", "planner"}:
        raise ValueError("上一次运行没有 Plan artifact，无法从 Coder/Tester 续跑")
    title = str((failed_event or {}).get("title") or "")
    module_name = title.rsplit(" · ", 1)[-1] if stage == "coder" and " · " in title else None
    project_files: dict[str, str] = {}
    project_rtl = source / "project" / "rtl"
    if project_rtl.is_dir():
        for path in sorted(project_rtl.glob("*.sv")):
            project_files[f"rtl/{path.name}"] = path.read_text(encoding="utf-8")
    return {
        "run_id": name,
        "spec": spec,
        "plan": plan,
        "parser_result": parser_result,
        "source": source,
        "stage": stage,
        "module": module_name,
        "event": failed_event,
        "project_files": project_files,
        "project_id": events_payload.get("project_id"),
        "source_status": events_payload.get("status"),
    }


def _copy_resume_files(context: RunContext, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = safe_project_path(context.run_dir, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _seed_resume_project(
    context: RunContext,
    source: dict[str, Any],
    spec_ref: str,
    plan_ref: str,
    *,
    attempt: int = 1,
    extra_artifacts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """把上次项目的 RTL 复制到新运行，并建立进入 Tester 的 manifest/handoff。"""
    files = source.get("project_files") or {}
    if not isinstance(files, dict) or not files:
        raise ValueError("上一次运行没有可续跑的 RTL 项目文件")
    if attempt == 1:
        _copy_resume_files(context, files)
    manifest_ref = save_snapshot(context, files, attempt)
    tester_input = context.write_handoff(
        "coder",
        "tester",
        {
            "artifacts": {
                "spec": spec_ref,
                "plan": plan_ref,
                "rtl_manifest": manifest_ref,
                **(extra_artifacts or {}),
            }
        },
        attempt=attempt if attempt != 1 else None,
        label=f"Resume {source['run_id']}: reuse current RTL",
    )
    return {"handler": tester_input, "files": files, "manifest": manifest_ref}


def _tested_resume_checkpoints(
    source: Path, project_id: str, preferred_attempt: int | None = None
) -> tuple[dict | None, dict | None]:
    """Recover the accepted baseline and a measured diagnostic improvement."""
    handoffs = [
        path for target in ("diagnoser", "summary")
        for path in (source / "handoffs").glob(f"tester_to_{target}_attempt*.json")
        if path.stem.rsplit("attempt", 1)[-1].isdigit()
    ]
    handoffs.sort(key=lambda path: int(path.stem.rsplit("attempt", 1)[-1]))
    baseline = diagnostic = None
    for path in handoffs:
        handoff_attempt = int(path.stem.rsplit("attempt", 1)[-1])
        if preferred_attempt is not None and handoff_attempt != preferred_attempt:
            continue
        try:
            record = RunContext.read_handoff_at(source, path)
        except ValueError as error:
            if preferred_attempt is not None or not str(error).startswith("handoff artifact 哈希不匹配："):
                raise
            continue
        if record.get("project_id") not in (None, project_id):
            raise ValueError("续跑的已测试候选与目标项目不一致")
        artifacts = record["payload"]["artifacts"]
        if not {"rtl_manifest", "report"} <= record.get("artifact_integrity", {}).keys():
            continue
        report = json.loads(resolve_artifact(source, artifacts["report"]).read_text(encoding="utf-8"))
        checkpoint = {"manifest": artifacts["rtl_manifest"], "report": report, "attempt": record["attempt"]}
        if preferred_attempt is not None:
            return checkpoint, None
        if baseline is None or repair_verdict(baseline["report"], report)[0]:
            baseline, diagnostic = checkpoint, None
        elif diagnostic_repair_progress(report, baseline["report"], diagnostic["report"] if diagnostic else None):
            diagnostic = checkpoint
    if preferred_attempt is not None:
        raise ValueError(f"找不到已核验的 Tester 第 {preferred_attempt} 轮快照")
    return baseline, diagnostic


def _resume_diagnostic_input(
    context: RunContext, source: dict, baseline_artifacts: dict, baseline_report: dict, checkpoint: dict,
) -> Path | None:
    report = checkpoint["report"]
    original_probe = report["repair_verification"].get("probe")
    for probe in dict.fromkeys((original_probe, *PROBE_TARGETS)):
        if not isinstance(probe, str):
            continue
        before = probe_failures(probe, baseline_report)
        after = probe_failures(probe, report)
        if before and after is not None and len(after) < len(before):
            break
    else:
        return None
    brief = context.write_json_artifact("resume_diagnostic_brief.json", {
        "rtl_manifest": baseline_artifacts["rtl_manifest"],
        "verification_probe": probe,
        "verification": f"Re-test diagnostic candidate from {source['run_id']} attempt {checkpoint['attempt']}",
    })
    candidate = dict(source, project_files=load_snapshot_at(source["source"], checkpoint["manifest"]))
    return _seed_resume_project(
        context, candidate, baseline_artifacts["spec"], baseline_artifacts["plan"],
        attempt=2, extra_artifacts={"failure_packet": baseline_artifacts["failure_packet"], "repair_brief": brief},
    )["handler"]


def run_agent(
    *,
    thinking: bool = True,
    reasoning_effort: str = "low",
    temperature: float = 0.2,
    max_tokens: int = 60000,
    max_calls: int = 100,
    max_repair_attempts: int = 8,
    planner_skill: bool = False,
    resume_from: str | None = None,
    resume_attempt: int | None = None,
    component_seed_from: str | None = None,
    architecture_profile: str | None = None,
    on_event: EventSink | None = None,
    cancel_event: Any = None,
    design_id: str | None = None,
    design_name: str | None = None,
    project_id: str | None = None,
    request_input: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    """运行固定流水线：Parser → Planner → Coder → Tester → Diagnoser → Patch Agent。

    ``graph.json`` 只是该固定流程的只读可视化，不参与调度。每个 Agent 都从上游
    handoff 中取得 artifact 引用，再按引用读取 Spec、RTL 或报告内容。
    """
    graph = load_agent_graph()
    if reasoning_effort not in {"low", "high", "max"}:
        raise ValueError("reasoning_effort 无效")
    if not 1 <= int(max_calls) <= 1000:
        raise ValueError("max_calls 必须在 1 到 1000 之间")
    if not 1 <= int(max_repair_attempts) <= 20:
        raise ValueError("max_repair_attempts 必须在 1 到 20 之间")
    max_tester_attempts = int(max_repair_attempts) + 1
    project = get_project(project_id)
    if architecture_profile is not None and (
        architecture_profile != L3_ARCHITECTURE or project.get("expected_cpu_level") != 3
        or resume_from or component_seed_from
    ):
        raise ValueError("L3 架构起点仅用于独立选择的新 L3 运行")
    if resume_attempt is not None and (not resume_from or not project.get("expected_cpu_level") or resume_attempt < 1):
        raise ValueError("Tester 轮次要求 CPU 项目、续跑来源和正整数轮次")
    seed_files: dict[str, str] = {}
    seed_interfaces: dict[str, str] = {}
    seed_manifest = None
    if component_seed_from:
        if resume_from or project.get("expected_cpu_level") != 3:
            raise ValueError("组件来源仅用于新 L3 运行，不能同时续跑")
        source = _load_resume_source(component_seed_from)
        summary = json.loads((source["source"] / "run_summary.json").read_text(encoding="utf-8"))
        report_ref = summary["selected_report"]
        report = json.loads(resolve_artifact(source["source"], report_ref).read_text(encoding="utf-8"))
        if not source_is_eligible_for_tier(project["id"], source["project_id"], report):
            raise ValueError("L3 组件来源必须是教师测试验证通过的 L2 运行")
        seed_manifest = summary["selected_manifest"]
        available = load_snapshot_at(source["source"], seed_manifest)
        seed_files = {f"rtl/{name}.sv": available[f"rtl/{name}.sv"]
                      for name in ("icache", "dcache", "branch_predictor")}
        seed_interfaces = frozen_component_interfaces(seed_files)
    spec_base = agent_task_spec(project["id"])
    run_id = f"agent-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"
    run_dir = AGENT_RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    _write_json(run_dir / "graph.json", graph)
    (run_dir / "project").mkdir(exist_ok=True)

    events: list[dict[str, Any]] = []
    started_at = _now()
    started_clock = time.perf_counter()
    current_status = "running"
    final_report: dict[str, Any] | None = None
    final_report_preview: dict[str, Any] | None = None
    final_result: dict[str, Any] | None = None
    effective_spec = spec_base
    parser_result: dict[str, Any] = {}
    plan: dict[str, Any] | None = None
    files: list[dict[str, Any]] = []
    tester_attempts = 0
    resume_diagnostic = None

    def persist(status: str = "running", error: str | None = None) -> None:
        _write_json(
            run_dir / "events.json",
            _run_payload(
                run_id=run_id,
                run_dir=run_dir,
                graph=graph,
                events=events,
                status=status,
                started_at=started_at,
                report=final_report_preview,
                result=final_result,
                spec=effective_spec,
                error=error,
                design_id=design_id,
                design_name=design_name,
                project_id=project["id"],
                resumed_from=resume_from,
            ),
        )

    def emit(event: dict[str, Any]) -> None:
        if on_event is not None:
            on_event(json.loads(json.dumps(event, ensure_ascii=False)))

    def begin_event(
        node_id: str, node_type: str, title: str, prompt: str, attempt: int = 1
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "run_id": run_id,
            "step": len(events) + 1,
            "node_id": node_id,
            "node_type": node_type,
            "title": title,
            "prompt": prompt,
            "status": "running",
            "attempt": attempt,
            "started_at": _now(),
            "_started_clock": time.perf_counter(),
        }
        events.append(event)
        persist()
        emit(event)
        return event

    def finish_event(event: dict[str, Any], status: str, **fields: Any) -> None:
        event.update(fields)
        event["status"] = status
        event["duration_ms"] = round(
            (time.perf_counter() - event.get("_started_clock", time.perf_counter())) * 1000
        )
        event.pop("_started_clock", None)
        event["finished_at"] = _now()
        persist()
        emit(event)

    def check_cancel() -> None:
        _check_cancel(cancel_event)

    def save_summary(status: str, error: str | None = None) -> None:
        calls = [event for event in events if "call_index" in event]
        tokens = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            values = [(event.get("usage") or {}).get(key) for event in calls]
            tokens[key] = sum(values) if all(type(value) is int for value in values) else None
        summary = {
            "schema_version": 1, "run_id": run_id, "project_id": project["id"],
            "started_at": started_at, "finished_at": _now(), "status": status,
            "duration_s": round(time.perf_counter() - started_clock, 3),
            "independent": resume_from is None and component_seed_from is None and architecture_profile is None,
            "resumed_from": resume_from,
            **({"architecture_profile": architecture_profile} if architecture_profile else {}),
            **({"resumed_from_tester_attempt": resume_attempt} if resume_attempt is not None else {}),
            **({"component_seed_from": component_seed_from, "component_seed_manifest": seed_manifest,
                "component_seed_files": sorted(seed_files)} if component_seed_from else {}),
            "model_calls": len(calls), "max_calls": max_calls,
            "max_repair_attempts": max_repair_attempts, "tester_attempts": tester_attempts,
            "call_settings": [dict(event.get("model_settings") or {}, node=event["node_id"],
                                   finish_reason=event.get("finish_reason"), usage=event.get("usage"))
                              for event in calls],
            "tokens": tokens, "error": error,
            "teacher_spec_sha256": fingerprint(Path(project["spec_path"]))["sha256"],
            "teacher_tb_sha256": fingerprint(Path(project["testbench_path"]))["sha256"],
        }
        if final_report:
            summary.update(
                teacher_passed=final_report.get("teacher_passed", final_report["passed"]),
                verified_level=(final_report.get("cpu_capability") or {}).get("verified_level"),
                failed_checks=len(final_report.get("checks") or []),
                test_suites=final_report.get("test_suites"),
                performance=final_report.get("performance") if final_report["passed"] else None,
                metrics=final_report.get("metrics") if final_report["passed"] else None,
            )
        if final_result:
            for key in ("passed", "selected_report", "selected_manifest", "repair_stop"):
                summary[key] = final_result.get(key)
        _write_json(run_dir / "run_summary.json", summary)

    persist()
    try:
        context = RunContext(
            run_dir=run_dir,
            options={
                "temperature": temperature,
                "max_tokens": max_tokens,
                "thinking": thinking,
                "reasoning_effort": reasoning_effort,
                "planner_skill": bool(planner_skill),
                "project": project,
                "seed_files": seed_files,
                "seed_interfaces": seed_interfaces,
            },
            begin_event=begin_event,
            finish_event=finish_event,
            persist=persist,
            emit=emit,
            check_cancel=check_cancel,
            request_input=request_input,
            max_calls=max_calls,
        )
        if architecture_profile:
            event = begin_event("coder", "coder", "Load L3 architecture", "")
            architecture_files, plan = load_l3_architecture()
            spec_ref = context.write_text_artifact("spec.md", effective_spec)
            plan_ref = context.write_json_artifact("plan.json", plan)
            next_tester_input = CoderAgent(context).write_tester_handoff(
                spec_ref, plan_ref, architecture_files, label="L3 architecture → Tester: RTL manifest",
            )
            finish_event(event, "info", response="Loaded the bundled L3 starter RTL.")
        elif resume_from:
            resume_source = _load_resume_source(resume_from)
            if resume_source["project_id"] not in (None, project["id"]):
                raise ValueError("续跑的原项目与目标项目不一致")
            effective_spec = resume_source["spec"]
            plan = resume_source["plan"]
            parser_result = resume_source.get("parser_result") or {}
            resume_stage = str(resume_source.get("stage") or "coder")
            spec_ref = context.write_text_artifact("effective_spec.md", effective_spec)
            plan_ref = context.write_json_artifact("plan.json", plan)
            parser_result_ref = context.write_json_artifact(
                "parser_result_resume.json", parser_result
            )
            if resume_attempt is not None and resume_stage not in {"tester", "modifier", "diagnoser", "patch", "summary"}:
                raise ValueError("续跑来源没有到达 Tester")
            if resume_stage == "parser":
                initial_handoff = context.write_handoff(
                    "spec",
                    "parser",
                    {"task": project["id"], "artifacts": {"spec": context.write_text_artifact("spec.md", effective_spec)}},
                    label=f"Resume {resume_from}: continue from Parser",
                )
                parser = ParserAgent(context)
                parser_handoff = parser.run(initial_handoff)
                planner = PlannerAgent(context)
                planner_handoff = planner.run(parser_handoff)
                coder = CoderAgent(context)
                next_tester_input = coder.run(planner_handoff)
            elif resume_stage == "planner":
                parser_result_ref = context.write_json_artifact(
                    "parser_result_resume.json", parser_result
                )
                parser_handoff = context.write_handoff(
                    "parser",
                    "planner",
                    {
                        "artifacts": {
                            "spec": spec_ref,
                            "parser_result": parser_result_ref,
                        }
                    },
                    label=f"Resume {resume_from}: continue from Planner",
                )
                planner = PlannerAgent(context)
                planner_handoff = planner.run(parser_handoff)
                coder = CoderAgent(context)
                next_tester_input = coder.run(planner_handoff)
            elif resume_stage == "coder":
                _copy_resume_files(context, resume_source.get("project_files") or {})
                planner_handoff = context.write_handoff(
                    "planner",
                    "coder",
                    {
                        "artifacts": {
                            "spec": spec_ref,
                            "plan": plan_ref,
                            "parser_result": parser_result_ref,
                        }
                    },
                    label=f"Resume {resume_from}: continue from Coder",
                )
                coder = CoderAgent(context)
                next_tester_input = coder.run(
                    planner_handoff,
                    existing_files=resume_source.get("project_files") or {},
                    start_module=resume_source.get("module"),
                )
            elif resume_stage in {"tester", "modifier", "diagnoser", "patch", "summary"}:
                if project.get("expected_cpu_level"):
                    baseline, resume_diagnostic = _tested_resume_checkpoints(
                        resume_source["source"], project["id"], resume_attempt
                    )
                    if baseline is not None:
                        resume_source["project_files"] = load_snapshot_at(resume_source["source"], baseline["manifest"])
                seeded = _seed_resume_project(context, resume_source, spec_ref, plan_ref)
                next_tester_input = seeded["handler"]
            else:
                raise ValueError(
                    f"上一次运行停在 {resume_stage}，没有可直接继续的 Coder/Tester 产物"
                )
        else:
            resume_stage = None
            spec_ref = context.write_text_artifact("spec.md", spec_base)
            initial_handoff = context.write_handoff(
                "spec",
                "parser",
                {"task": project["id"], "artifacts": {"spec": spec_ref}},
                label="Input Spec → Parser: Spec artifact",
            )

            parser = ParserAgent(context)
            parser_handoff = parser.run(initial_handoff)

            planner = PlannerAgent(context)
            planner_handoff = planner.run(parser_handoff)
            coder = CoderAgent(context)
            next_tester_input = coder.run(planner_handoff)

        tester = TesterAgent(context)
        diagnoser = DiagnoserAgent(context)
        patch = PatchAgent(context)
        selected = None
        diagnostic_seed = None
        diagnostic_report = None
        decisions = []
        repair_stop = None
        repair_attempts = 0
        while True:
            tester_attempts += 1
            tester_handoff = tester.run(next_tester_input, tester_attempts)
            tester_artifacts = context.read_handoff(tester_handoff)["payload"]["artifacts"]
            report = context.read_json_artifact(tester_artifacts["report"])
            accepted, reason = (True, "initial_candidate") if final_report is None else repair_verdict(final_report, report)
            failing = len(report.get("checks") or [])
            if tester_attempts == 1:
                initial_failed_checks = failing
            runaway_limit = int(os.environ.get("EDA_RUNAWAY_CHECKS", "50000"))
            if (
                tester_attempts == 1 and not report.get("passed")
                and report.get("stage") == "simulation" and failing > runaway_limit
            ):
                # 首轮就是断言风暴（实测 23 万条）：这类设计靠 9 轮小补丁救不回来，
                # 每轮却要一次 40 s 教师仿真。直接停，省下整段修复预算。
                repair_stop = f"runaway_design: {failing} failing checks"
                final_report = report
                selected = tester_handoff
                final_report_preview = report_preview(report, tester_artifacts["report"])
                break
            if accepted:
                selected = tester_handoff
                final_report = report
                final_report_preview = report_preview(report, tester_artifacts["report"])
                diagnostic_seed = None
                diagnostic_report = None
            if tester_attempts == 1 and resume_diagnostic is not None and not report["passed"]:
                resumed_input = _resume_diagnostic_input(
                    context, resume_source, tester_artifacts, report, resume_diagnostic,
                )
                if resumed_input is not None:
                    next_tester_input = resumed_input
                    max_tester_attempts += 1
                    continue
            use_partial_repair = (
                not accepted
                and diagnostic_repair_progress(report, final_report, diagnostic_report)
            )
            if use_partial_repair:
                diagnostic_seed = tester_handoff
                diagnostic_report = report
            if tester_attempts > 1:
                decisions.append({
                    "attempt": tester_attempts, "accepted": accepted, "reason": reason,
                    "continued_for_diagnosis": (
                        use_partial_repair and tester_attempts < max_tester_attempts
                        and context.model_calls + 2 <= context.max_calls
                    ),
                    "report": tester_artifacts["report"], "rtl_manifest": tester_artifacts["rtl_manifest"],
                    "observations": {
                        "halt_timeouts": list(dict.fromkeys(
                            row["name"] for row in report.get("checks", [])
                            if "等待 halted 超时" in row["detail"]
                        )),
                        "icache_mismatches": [
                            row for row in (report.get("icache_word_observation") or {}).get("samples", [])
                            if row.endswith("match=0")
                        ],
                        "failed_checks": len(report.get("checks", [])),
                        "verification": report.get("repair_verification"),
                    },
                })
                context.write_json_artifact(f"decision_attempt{tester_attempts}.json", decisions[-1])
            if final_report["passed"] or tester_attempts >= max_tester_attempts:
                break
            if context.model_calls + 2 > context.max_calls:
                repair_stop = "model_call_budget"
                break
            repair_seed = diagnostic_seed or selected
            protocol_error = None
            while repair_attempts < max_repair_attempts and context.model_calls + 2 <= context.max_calls:
                check_cancel()
                repair_attempts += 1
                try:
                    brief_handoff = diagnoser.run(
                        repair_seed, repair_attempts, decisions, protocol_error=protocol_error,
                    )
                    next_tester_input = patch.run(brief_handoff, repair_attempts)
                except (PatchProtocolError, ValueError) as error:
                    if events[-1].get("status") == "running":
                        finish_event(events[-1], "failed", error=str(error))
                    protocol_error = str(error)[:1000]
                    repair_stop = f"repair_protocol: {error}"
                    if project.get("expected_cpu_level") not in (1, 2, 3):
                        break
                    continue
                repair_stop = None
                break
            else:
                repair_stop = repair_stop or (
                    "repair_budget" if repair_attempts >= max_repair_attempts else "model_call_budget"
                )
            if repair_stop:
                break

        final_artifacts = context.read_handoff(selected)["payload"]["artifacts"]
        _copy_resume_files(context, load_snapshot(context, final_artifacts["rtl_manifest"]))
        spec_ref = final_artifacts.get("spec")
        plan_ref = final_artifacts.get("plan")
        manifest_ref = final_artifacts.get("rtl_manifest")
        if not all(isinstance(ref, str) for ref in (spec_ref, plan_ref, manifest_ref)):
            raise ValueError("最终 handoff 缺少 Spec、Plan 或 RTL manifest artifact")
        effective_spec = context.read_text_artifact(spec_ref)
        plan = context.read_json_artifact(plan_ref)
        manifest = context.read_json_artifact(manifest_ref)
        if not isinstance(plan, dict) or not isinstance(manifest, dict):
            raise ValueError("最终 artifact 格式无效")
        files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
        if not resume_from and not architecture_profile:
            parser_payload = context.read_handoff(parser_handoff)["payload"]
            parser_result_ref = (parser_payload.get("artifacts") or {}).get("parser_result")
            if isinstance(parser_result_ref, str):
                parsed = context.read_json_artifact(parser_result_ref)
                parser_result = parsed if isinstance(parsed, dict) else {}
        passed = bool(final_report.get("passed"))
        final_result = {
            "passed": passed,
            "stage": final_report.get("stage"),
            "summary": _report_summary(final_report),
            "metrics": final_report.get("metrics") or {},
            "spec": effective_spec,
            "plan": plan,
            "files": files,
            "parser_rounds": parser_result.get("rounds", 0 if architecture_profile else 1),
            "supplements": parser_result.get("supplements", []),
            "tester_attempts": tester_attempts,
            "selected_report": final_artifacts["report"],
            "selected_manifest": manifest_ref,
            "repair_stop": repair_stop,
            "initial_failed_checks": locals().get("initial_failed_checks"),
            "resumed_from": resume_from,
            **({"architecture_profile": architecture_profile} if architecture_profile else {}),
            **({"component_seed_from": component_seed_from, "component_seed_files": sorted(seed_files)}
               if component_seed_from else {}),
            "cpu_capability": final_report.get("cpu_capability"),
            "test_suites": final_report.get("test_suites"),
            "performance": final_report.get("performance"),
        }
        _write_json(run_dir / "result.json", final_result)
        current_status = "passed" if passed else "failed"
    except AgentCancelled as error:
        current_status = "cancelled"
        for pending in events:
            if pending.get("status") in {"running", "waiting_input"}:
                pending.update(
                    {"status": "cancelled", "finished_at": _now(), "error": str(error)}
                )
        payload = _run_payload(
            run_id=run_id,
            run_dir=run_dir,
            graph=graph,
            events=events,
            status=current_status,
            started_at=started_at,
            finished_at=_now(),
            report=final_report_preview,
            result=final_result,
            spec=effective_spec,
            resumed_from=resume_from,
            error=str(error),
            design_id=design_id,
            design_name=design_name,
            project_id=project["id"],
        )
        payload["duration_ms"] = round((time.perf_counter() - started_clock) * 1000)
        _write_json(run_dir / "events.json", payload)
        save_summary(current_status, str(error))
        try:
            _write_metadata(
                run_dir,
                graph=graph,
                run_id=run_id,
                status=current_status,
                started_at=started_at,
                finished_at=payload["finished_at"],
                attempts=tester_attempts or 1,
                max_repair_attempts=max_repair_attempts,
                resumed_from=resume_from,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
                design_id=design_id,
                design_name=design_name,
                project_id=project["id"],
            )
        except Exception:
            pass
        raise
    except Exception as error:
        current_status = "failed"
        for pending in events:
            if pending.get("status") in {"running", "waiting_input"}:
                pending.update(
                    {"status": "failed", "finished_at": _now(), "error": str(error)}
                )
        payload = _run_payload(
            run_id=run_id,
            run_dir=run_dir,
            graph=graph,
            events=events,
            status=current_status,
            started_at=started_at,
            finished_at=_now(),
            report=final_report_preview,
            result=final_result,
            spec=effective_spec,
            resumed_from=resume_from,
            error=str(error),
            design_id=design_id,
            design_name=design_name,
            project_id=project["id"],
        )
        payload["duration_ms"] = round((time.perf_counter() - started_clock) * 1000)
        _write_json(run_dir / "events.json", payload)
        save_summary(current_status, str(error))
        try:
            _write_metadata(
                run_dir,
                graph=graph,
                run_id=run_id,
                status=current_status,
                started_at=started_at,
                finished_at=payload["finished_at"],
                attempts=tester_attempts or 1,
                max_repair_attempts=max_repair_attempts,
                resumed_from=resume_from,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
                design_id=design_id,
                design_name=design_name,
                project_id=project["id"],
            )
        except Exception:
            pass
        raise

    payload = _run_payload(
        run_id=run_id,
        run_dir=run_dir,
        graph=graph,
        events=events,
        status=current_status,
        started_at=started_at,
        finished_at=_now(),
        report=final_report_preview,
        result=final_result,
        spec=effective_spec,
        resumed_from=resume_from,
        design_id=design_id,
        design_name=design_name,
        project_id=project["id"],
    )
    payload["duration_ms"] = round((time.perf_counter() - started_clock) * 1000)
    _write_json(run_dir / "events.json", payload)
    _write_metadata(
        run_dir,
        graph=graph,
        run_id=run_id,
        status=current_status,
        started_at=started_at,
        finished_at=payload["finished_at"],
        attempts=tester_attempts or 1,
        max_repair_attempts=max_repair_attempts,
        resumed_from=resume_from,
        temperature=temperature,
        max_tokens=max_tokens,
        thinking=thinking,
        reasoning_effort=reasoning_effort,
        design_id=design_id,
        design_name=design_name,
        project_id=project["id"],
    )
    save_summary(current_status)
    return payload



