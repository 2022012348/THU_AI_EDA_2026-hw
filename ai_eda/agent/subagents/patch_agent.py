"""Patch Agent applies a repair brief to a specific immutable RTL snapshot."""

import json
import re
import subprocess
import tempfile
from pathlib import Path

from ai_eda.agent.cpu_workflow import L3_EDGE_CONTRACT, in_order_boundary_wiring, l3_allocation_wiring, module_headers
from ai_eda.agent.patches import PatchProtocolError, RepairApplication, apply_repair_payload
from ai_eda.agent.skills.cpu_observation import dcache_lane_observation, icache_word_observation
from ai_eda.agent.snapshots import load_snapshot, save_snapshot

from .base import ModelOutputTruncated, SubAgent
from .common import combine_files, extract_json_payload
from .repair import make_packet, probe_failures


PATCH = """You are the RTL Patch Agent. Given a validated repair brief and current
sources, fix the stated failure. Preserve all other behavior and the module boundaries.
Only edit the supplied files. Never edit teacher files or specialize logic to tests.
Put new declarations before any use. Review each changed control signal at its
producer and consumer, especially where a cache response releases a held stage.
Keep one driver per net and preserve the existing direction of each formal port.
When fixing a slot, check simultaneous consume/replace and squash/response;
when fixing MEM, check first acceptance, wait, completion, and next-slot release.
Use the brief's current target_failures and the current observation. A load
probe with correct data and exit code but a duplicate retirement calls for
fixing consumption and valid-bit lifetime while preserving the good data path.
If the brief includes a previous_patch_validation_error, that patch was rejected.
Apply the new repair to the supplied source and avoid the reported protocol error.
Do not suppress retirement based on PC equality or wait for a valid-low bubble
between instructions; a new instruction can replace the slot on the same edge.
When breaking a feedback loop, keep every consumer driven. Put the current epoch
in a resettable register and keep the proposed next epoch separate.
Return JSON with schema_version=1, edits=[{path, expected, replacement}], and notes.
Prefer short anchored edits. Each expected string must match verbatim once in that file. For a large local
correction you may instead return files=[{path, content}] for the same allowed paths.
Include only files whose contents change."""


class PatchAgent(SubAgent):
    name = "patch"
    title = "06 · Patch Agent"
    node_type = name

    def run(self, handoff: Path, attempt: int) -> Path:
        artifacts = self.runtime.read_handoff(handoff)["payload"]["artifacts"]
        brief = self.runtime.read_json_artifact(artifacts["repair_brief"])
        packet = self.runtime.read_json_artifact(artifacts["failure_packet"])
        manifest_ref = artifacts["rtl_manifest"]
        if (brief["rtl_manifest"] != manifest_ref or packet["rtl_manifest"] != manifest_ref
                or brief["failure_packet"] != artifacts["failure_packet"]):
            raise PatchProtocolError("repair brief 的 RTL 版本与当前 manifest 不一致")
        files = load_snapshot(self.runtime, manifest_ref)
        plan = self.runtime.read_json_artifact(artifacts["plan"])
        spec = self.runtime.read_text_artifact(artifacts["spec"])
        stage = packet["teacher_evidence"]["stage"]
        if stage != brief["stage"]:
            raise PatchProtocolError("repair brief 的失败阶段与 Tester 证据不一致")
        cpu_level = (self.runtime.options.get("project") or {}).get("expected_cpu_level")
        cpu_simulation = bool(cpu_level and stage == "simulation")
        selected = {path: files[path] for path in brief["files"]}
        contract = {m["file"]: m["interfaces"] for m in plan["modules"] if m["file"] in selected}
        sections = re.split(r"(?m)(?=^#{2,6} +)", spec)
        spec_excerpt = [section[:3500] for section in sections
                        if section.splitlines() and section.splitlines()[0].lstrip("# ") in brief["spec_sections"]]
        evidence = packet["teacher_evidence"]
        target = brief.get("verification_probe")
        observations = (
            {target: evidence[target]} if target
            else {key: evidence[key] for key in ("retirement_observation", "execution_observation") if evidence[key]}
        )
        if (target in (None, "retirement_observation", "execution_observation", "logic_loop_observation")
                and evidence.get("pipeline_observation")):
            observations["pipeline_observation"] = evidence["pipeline_observation"]
        loops = evidence.get("logic_loop_observation") or {}
        if loops.get("loops") or loops.get("undriven"):
            observations["logic_loop_observation"] = loops
        prompt = json.dumps({
            "brief": brief, "observations": observations,
            "interfaces": contract, "spec_excerpts": spec_excerpt, "rtl": selected,
            "interface_rule": (
                "Correct generated declarations to match the plan." if stage == "compile"
                else "The compiled source is authoritative. Keep the teacher-facing rv32i5 ports. "
                     "Restore an omitted internal port only if it is already in the frozen plan, "
                     "and connect it at all affected instances." if cpu_simulation
                else "Preserve the compiled ANSI module headers."
            ),
        }, ensure_ascii=False)
        system_prompt = PATCH
        if cpu_level == 3:
            system_prompt += (
                "\nThe brief includes actual top-level connections from this snapshot. "
                "Use them to distinguish the driven net from similar names; only edit authorized files. "
                "Use the following edge contract when the frozen plan uses head-only recovery.\n" + L3_EDGE_CONTRACT
            )
        feedback = ""
        for retry in (False, True):
            request = prompt + feedback
            event = self.runtime.begin_event(self.name, self.node_type, self.title, request, attempt)
            try:
                result = self.runtime.call_model(
                    event, request, system_prompt,
                    model_options={"thinking": False, "max_tokens": min(self.runtime.options["max_tokens"], 24000)},
                )
                application = self._apply(result["answer"], selected)
                changed_interfaces = set()
                for path, source in application.changed_files.items():
                    before, after = module_headers(files[path]), module_headers(source)
                    module = next(m for m in plan["modules"] if m["file"] == path)
                    expected = module["name"]
                    if expected not in after or (stage == "compile" and set(after) - (before.keys() | {expected})):
                        raise PatchProtocolError(f"补丁修改了模块归属：{path}")
                    if stage != "compile":
                        changed_interfaces.update(self._simulation_interface_changes(
                            path, before, after, module, cpu_simulation=cpu_simulation,
                        ))
                candidate = files | application.changed_files
                if plan.get("l3_allocation_tag_contract"):
                    l3_allocation_wiring(candidate)
                if plan.get("in_order_boundary_contract"):
                    in_order_boundary_wiring(candidate)
                # Let Tester compare incremental progress when the baseline already fails compilation.
                if cpu_simulation:
                    self._check_cpu_compile(candidate, changed_interfaces)
                if cpu_level == 3:
                    self._check_cache_repair(target, evidence, application.changed_files)
            except ValueError as error:
                self.finish(event, "failed", error=str(error))
                if retry or self.runtime.model_calls >= self.runtime.max_calls:
                    raise
                instruction = (
                    "The response exceeded the output limit. Return only compact anchored edits, with the smallest "
                    "unique exact snippets and no full files; edit one file at a time."
                    if isinstance(error, ModelOutputTruncated)
                    else f"The previous response failed patch validation: {error}. Fix the reported problem. "
                         "Use compact anchored edits or a complete replacement for a small changed file."
                )
                feedback = f"\n{instruction} Use the original source snapshot above."
            else:
                break
        files.update(application.changed_files)
        patch_ref = self.runtime.write_json_artifact(f"patch_attempt{attempt}.json", application.manifest)
        new_ref = save_snapshot(self.runtime, files, attempt + 1, manifest_ref)
        self.finish(event, "info", response=json.dumps(application.manifest, ensure_ascii=False))
        return self.runtime.write_handoff(
            self.name, "tester", {"artifacts": {
                "spec": artifacts["spec"], "plan": artifacts["plan"], "rtl_manifest": new_ref,
                "patch": patch_ref, "repair_brief": artifacts["repair_brief"],
                "failure_packet": artifacts["failure_packet"],
            }}, attempt=attempt,
        )

    @staticmethod
    def _check_cache_repair(target: str | None, evidence: dict, changed_files: dict[str, str]) -> None:
        cache_probe = {
            "icache_word_observation": ("rtl/icache.sv", icache_word_observation),
            "dcache_lane_observation": ("rtl/dcache.sv", dcache_lane_observation),
        }.get(target)
        before = probe_failures(target, evidence) if target else None
        if not cache_probe or not before:
            return
        path, observe = cache_probe
        if path not in changed_files:
            return
        with tempfile.TemporaryDirectory(prefix="eda-patch-cache-") as directory:
            rtl = Path(directory) / Path(path).name
            rtl.write_text(changed_files[path], encoding="utf-8")
            observation = observe(rtl)
        failures = probe_failures(target, {target: observation})
        if failures is not None and set(failures) < set(before):
            return
        samples = [sample for sample in observation.get("samples", [])
                   if sample.endswith("|match=0") or "|refill_match=0|" in sample]
        detail = "\n".join(samples[:3]) if failures else observation["status"]
        raise PatchProtocolError(f"{path} still fails the standalone cache probe.\n{detail}")


    @staticmethod
    def _simulation_interface_changes(
        path: str, before: dict[str, str], after: dict[str, str], module: dict, *, cpu_simulation: bool,
    ) -> set[str]:
        if before == after:
            return set()
        if not cpu_simulation or before.keys() != after.keys() or before.get("rv32i5") != after.get("rv32i5"):
            raise PatchProtocolError(f"补丁修改了受保护的端口合同或模块归属：{path}")
        declarations = [item for item in re.split(r"[;,\n]", module["interfaces"]) if item.strip()]
        planned_header = re.sub(r"\s+", "", "(" + ",".join(declarations) + ")")
        changed = {name for name in before if before[name] != after[name]}
        if changed != {module["name"]} or after[module["name"]] != planned_header:
            raise PatchProtocolError(f"补丁端口必须与 Planner 冻结的接口一致：{path}")
        return changed

    @staticmethod
    def _check_cpu_compile(files: dict[str, str], changed_interfaces: set[str] | None = None) -> None:
        changed_interfaces = changed_interfaces or set()
        with tempfile.TemporaryDirectory(prefix="eda-patch-") as directory:
            path = Path(directory) / "generated_attempt0.sv"
            path.write_text(combine_files(files))
            try:
                result = subprocess.run(
                    ["iverilog", "-g2012", "-Wall", "-s", "rv32i5", "-tnull", str(path)],
                    capture_output=True, text=True, timeout=10,
                )
            except subprocess.TimeoutExpired:
                return
            if result.returncode == 0:
                diagnostics = "\n".join(
                    line for line in result.stderr.splitlines()
                    if (match := re.search(r"Instantiating module (\w+) with dangling input port", line))
                    and match[1] in changed_interfaces
                )
                if not diagnostics:
                    return
            else:
                diagnostics = result.stderr
            packet = make_packet({"stage": "compile", "stderr": diagnostics}, files, "", "")
            errors = packet["teacher_evidence"]["error_excerpt"][:6]
            raise PatchProtocolError("Patched RTL did not elaborate:\n" + "\n".join(errors))

    @staticmethod
    def _apply(answer: str, selected: dict[str, str]) -> RepairApplication:
        payload = extract_json_payload(answer)
        edits = payload.get("edits")
        if isinstance(edits, list):
            payload["edits"] = [
                edit for edit in edits if not (
                    isinstance(edit, dict) and set(edit) == {"path", "expected", "replacement"}
                    and isinstance(edit["path"], str) and edit["path"] in selected
                    and isinstance(edit["expected"], str) and edit["expected"] == edit["replacement"]
                )
            ]
        return apply_repair_payload(payload, selected)
