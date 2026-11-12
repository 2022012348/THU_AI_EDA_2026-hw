"""Deterministic contracts for the advanced RV32I teaching tiers.

The CPU specifications intentionally leave internal implementation choices to the
planner.  Asking the user to resolve those choices makes L2/L3 runs fragile, so
this module records the small, stable subset of facts that every agent in the
pipeline must agree on.  It is deliberately independent of the orchestrator:
callers may use it for prompting, seed selection, or report validation without
changing the current workflow.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


# These are the advanced-only ports observed by the shared teacher harness.
# ``imem_ready`` and ``dmem_ready`` are inputs, but belong here because an
# implementation which omits either handshake cannot satisfy L1 or above.
ADVANCED_INTERFACE_SIGNALS = (
    "imem_valid",
    "imem_ready",
    "dmem_ready",
    "dbg_features",
    "dbg_retire_valid",
    "dbg_retire_pc",
    "dbg_retire_insn",
    "dbg_bp_resolve",
    "dbg_bp_mispredict",
    "dbg_ooo_issue",
    "dbg_ooo_bypass",
)

REQUIRED_CAPABILITY_SIGNALS = (
    "dbg_features",
    "dbg_retire_valid",
    "dbg_retire_pc",
    "dbg_retire_insn",
    "dbg_bp_resolve",
    "dbg_bp_mispredict",
    "dbg_ooo_issue",
    "dbg_ooo_bypass",
)


@dataclass(frozen=True)
class FocusStage:
    """A repair/generation focus and the files most relevant to it.

    Files are allowed to occur in more than one focus.  A focus describes the
    smallest useful review slice; :attr:`Rv32i5TierContract.module_ownership`
    records one primary owner for each required file.
    """

    name: str
    objective: str
    modules: tuple[str, ...]


@dataclass(frozen=True)
class SeedEligibility:
    """The previously verified design required to bootstrap a higher tier."""

    source_project_id: str
    source_expected_level: int
    builtin_reference_id: str | None = None


@dataclass(frozen=True)
class Rv32i5TierContract:
    """Prompt-safe facts for exactly one advanced RV32I project."""

    project_id: str
    expected_level: int
    feature_bits: str
    required_interface_signals: tuple[str, ...]
    required_capability_signals: tuple[str, ...]
    focus_stages: tuple[FocusStage, ...]
    # ``(rtl path, primary stage name)`` pairs preserve deterministic order.
    module_ownership: tuple[tuple[str, str], ...]
    non_blocking_assumptions: tuple[str, ...]
    seed_eligibility: SeedEligibility

    @property
    def required_modules(self) -> tuple[str, ...]:
        """Required RTL files in deterministic planner order."""

        return tuple(path for path, _stage in self.module_ownership)

    def modules_for_focus(self, name: str) -> tuple[str, ...]:
        """Return the review slice for a named focus, or an empty tuple."""

        for focus in self.focus_stages:
            if focus.name == name:
                return focus.modules
        return ()

    def owner_for_module(self, path: str) -> str | None:
        """Return the primary focus that owns a required file."""

        for module_path, owner in self.module_ownership:
            if module_path == path:
                return owner
        return None


_L1_OWNERSHIP = (
    ("rtl/rv32i5.sv", "top_protocol"),
    ("rtl/if_stage.sv", "fetch_cache"),
    ("rtl/id_stage.sv", "decode_execute"),
    ("rtl/ex_stage.sv", "decode_execute"),
    ("rtl/mem_stage.sv", "memory_cache"),
    ("rtl/wb_stage.sv", "commit"),
    ("rtl/hazard_unit.sv", "decode_execute"),
    ("rtl/forwarding_unit.sv", "decode_execute"),
    ("rtl/icache.sv", "fetch_cache"),
    ("rtl/dcache.sv", "memory_cache"),
)

_L2_OWNERSHIP = _L1_OWNERSHIP + (("rtl/branch_predictor.sv", "prediction"),)

_L3_OWNERSHIP = (
    ("rtl/rv32i5.sv", "top_protocol"),
    ("rtl/icache.sv", "fetch_prediction"),
    ("rtl/dcache.sv", "memory_order"),
    ("rtl/branch_predictor.sv", "fetch_prediction"),
    ("rtl/rename_unit.sv", "rename_dispatch"),
    ("rtl/issue_queue.sv", "issue_execute"),
    ("rtl/reorder_buffer.sv", "in_order_commit"),
    ("rtl/load_store_unit.sv", "memory_order"),
)

_COMMON_ADVANCED_ASSUMPTIONS = (
    "Use the advanced L1+ top-level interface; all listed capability outputs are present. "
    "Unsupported higher-tier event outputs are tied low rather than omitted.",
    "A valid/ready handshake is accepted only on a clock edge with both signals high; "
    "an accepted request keeps address, write data, and byte enables stable until ready.",
    "Capability events are single-cycle pulses and are zero during reset and after halted; "
    "dbg_features is the fixed tier value from time zero.",
    "Internal port names, FSM encodings, and helper-file layout are planner-owned choices, "
    "not questions for the user, provided the externally visible contract is preserved.",
)


CPU_TIER_CONTRACTS: dict[str, Rv32i5TierContract] = {
    "rv32i5_cache": Rv32i5TierContract(
        project_id="rv32i5_cache",
        expected_level=1,
        feature_bits="3'b001",
        required_interface_signals=ADVANCED_INTERFACE_SIGNALS,
        required_capability_signals=REQUIRED_CAPABILITY_SIGNALS,
        focus_stages=(
            FocusStage(
                "base_pipeline",
                "Preserve RV32I ISA, valid-bit propagation, forwarding, reset, and ECALL commit.",
                tuple(path for path, _ in _L1_OWNERSHIP[:8]),
            ),
            FocusStage(
                "cache_protocol",
                "Implement real I/D Cache refill, write-through, epoch, and ready/response behavior.",
                (
                    "rtl/rv32i5.sv",
                    "rtl/if_stage.sv",
                    "rtl/mem_stage.sv",
                    "rtl/icache.sv",
                    "rtl/dcache.sv",
                ),
            ),
            FocusStage(
                "commit_observability",
                "Retire exactly once per committed instruction and delay halt until ECALL commits.",
                ("rtl/rv32i5.sv", "rtl/wb_stage.sv", "rtl/mem_stage.sv"),
            ),
        ),
        module_ownership=_L1_OWNERSHIP,
        non_blocking_assumptions=_COMMON_ADVANCED_ASSUMPTIONS
        + (
            "A 16-byte line refills base, base+4, base+8, base+12 into word0..word3; "
            "a partial refill is never a hit.",
            "A redirect or ECALL may suppress an obsolete response, but cannot revoke an "
            "already accepted external request or discard a completed cache line incorrectly.",
            "A D-cache store is write-allocate plus exactly one write-through; it responds only "
            "after write-through acceptance and preserves the original byte lane/address offset.",
        ),
        seed_eligibility=SeedEligibility(
            source_project_id="rv32i5",
            source_expected_level=0,
            builtin_reference_id="rv32i5_l1_reference",
        ),
    ),
    "rv32i5_predict": Rv32i5TierContract(
        project_id="rv32i5_predict",
        expected_level=2,
        feature_bits="3'b011",
        required_interface_signals=ADVANCED_INTERFACE_SIGNALS,
        required_capability_signals=REQUIRED_CAPABILITY_SIGNALS,
        focus_stages=(
            FocusStage(
                "verified_l1_preservation",
                "Keep the verified L1 pipeline and cache behavior intact while adding prediction.",
                tuple(path for path, _ in _L1_OWNERSHIP),
            ),
            FocusStage(
                "prediction",
                "Predict in fetch, carry metadata, train at resolution, and recover precisely on mismatch.",
                (
                    "rtl/branch_predictor.sv",
                    "rtl/if_stage.sv",
                    "rtl/ex_stage.sv",
                    "rtl/rv32i5.sv",
                ),
            ),
            FocusStage(
                "prediction_observability",
                "Emit resolve/mispredict pulses only for non-squashed resolved control transfers.",
                ("rtl/branch_predictor.sv", "rtl/ex_stage.sv", "rtl/rv32i5.sv"),
            ),
        ),
        module_ownership=_L2_OWNERSHIP,
        non_blocking_assumptions=_COMMON_ADVANCED_ASSUMPTIONS
        + (
            "BTB and BHT are independent 16-entry direct-mapped tables indexed by PC[5:2]; "
            "BTB tags use PC[31:6], and BHT resets to weak-not-taken 2'b01.",
            "A same-cycle lookup observes the old predictor contents; a resolution updates the "
            "table for later predictions only.",
            "Conditional BTB allocation occurs on a miss only when actually taken; JAL/JALR "
            "allocate or update from their resolved target.",
            "Predictor-to-pipeline wiring, metadata packing, and internal redirect FSM names are "
            "implementation choices; preserve valid/ready/epoch semantics rather than asking users.",
        ),
        seed_eligibility=SeedEligibility(
            source_project_id="rv32i5_cache",
            source_expected_level=1,
        ),
    ),
    "rv32i5_ooo": Rv32i5TierContract(
        project_id="rv32i5_ooo",
        expected_level=3,
        feature_bits="3'b111",
        required_interface_signals=ADVANCED_INTERFACE_SIGNALS,
        required_capability_signals=REQUIRED_CAPABILITY_SIGNALS,
        focus_stages=(
            FocusStage(
                "verified_l2_preservation",
                "Preserve functional L2 cache, predictor, ISA, and precise wrong-path suppression.",
                (
                    "rtl/rv32i5.sv",
                    "rtl/icache.sv",
                    "rtl/dcache.sv",
                    "rtl/branch_predictor.sv",
                ),
            ),
            FocusStage(
                "rename_dispatch",
                "Rename architectural registers, allocate ROB/IQ entries, and apply backpressure safely.",
                ("rtl/rename_unit.sv", "rtl/reorder_buffer.sv", "rtl/issue_queue.sv"),
            ),
            FocusStage(
                "issue_execute",
                "Allow an independent younger ALU operation to execute while an older load waits.",
                ("rtl/issue_queue.sv", "rtl/load_store_unit.sv", "rtl/dcache.sv"),
            ),
            FocusStage(
                "in_order_commit",
                "Commit one completed ROB head at a time and serialize stores/ECALL correctly.",
                ("rtl/reorder_buffer.sv", "rtl/load_store_unit.sv", "rtl/rv32i5.sv"),
            ),
        ),
        module_ownership=_L3_OWNERSHIP,
        non_blocking_assumptions=_COMMON_ADVANCED_ASSUMPTIONS
        + (
            "Use at least an 8-entry ROB, 8-entry integer issue queue, 40 physical registers, "
            "and a two-entry-or-equivalent load/store queue; helper modules may implement the PRF.",
            "PR0 is permanent zero; speculative and committed maps start at PR0..PR31, with "
            "PR32..PR39 initially free.  Internal signal names and state encodings are planner-owned.",
            "A load/store path may remain ordered, but an independent younger ALU instruction must "
            "be able to execute while an older accepted load waits for external memory.",
            "Only ROB-head stores create external side effects, and only ROB-head ECALL retires and "
            "initiates halt.  Squashed work may complete internally but cannot commit or signal events.",
            "Checkpoint representation, free-list bookkeeping, and recovery sequencing are internal "
            "choices so long as older correct work remains and squashed mappings/results are discarded.",
        ),
        seed_eligibility=SeedEligibility(
            source_project_id="rv32i5_predict",
            source_expected_level=2,
        ),
    ),
}

# ``rv32i5`` is intentionally not a full advanced contract, but it is a valid
# verified source for the L1 bootstrap policy.
_EXPECTED_LEVEL_BY_PROJECT = {
    "rv32i5": 0,
    **{project_id: contract.expected_level for project_id, contract in CPU_TIER_CONTRACTS.items()},
}


def detect_rv32i5_tier(project_id: str | None) -> Rv32i5TierContract | None:
    """Return the advanced-tier contract for ``project_id``, if one exists."""

    return CPU_TIER_CONTRACTS.get(str(project_id or ""))


def cpu_tier_level(project_id: str | None) -> int | None:
    """Return a known RV32I level, including L0, without importing project state."""

    return _EXPECTED_LEVEL_BY_PROJECT.get(str(project_id or ""))


def is_advanced_rv32i5_tier(project_id: str | None) -> bool:
    """Whether the project is one of the L1/L2/L3 deterministic contracts."""

    return detect_rv32i5_tier(project_id) is not None


def reported_verified_level(report: Mapping[str, Any] | None) -> int | None:
    """Extract a *verified* CPU level, never substituting declared capability.

    ``level``/``declared_level`` only describe what RTL claims.  A seed is safe
    only when the teacher harness emitted a verified level.
    """

    if not isinstance(report, Mapping):
        return None
    capability = report.get("cpu_capability")
    if not isinstance(capability, Mapping):
        return None
    value = capability.get("verified_level", capability.get("verified"))
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def report_qualifies_as_seed(
    project_id: str | None, report: Mapping[str, Any] | None
) -> bool:
    """Return whether a report is a promotable seed for its own project tier."""

    expected_level = cpu_tier_level(project_id)
    return bool(
        expected_level is not None
        and isinstance(report, Mapping)
        and report.get("passed") is True
        and reported_verified_level(report) == expected_level
    )


def source_is_eligible_for_tier(
    target_project_id: str | None,
    source_project_id: str | None,
    source_report: Mapping[str, Any] | None,
) -> bool:
    """Validate that a passed source run is the required seed for a target tier."""

    contract = detect_rv32i5_tier(target_project_id)
    if contract is None:
        return False
    policy = contract.seed_eligibility
    if source_project_id != policy.source_project_id:
        return False
    if not report_qualifies_as_seed(source_project_id, source_report):
        return False
    return reported_verified_level(source_report) == policy.source_expected_level


def builtin_reference_is_allowed(project_id: str | None) -> bool:
    """Whether this tier has a curated fallback when no promoted seed exists."""

    contract = detect_rv32i5_tier(project_id)
    return bool(contract and contract.seed_eligibility.builtin_reference_id)


def describe_tier_context(project_id: str | None, *, role: str = "planner") -> str:
    """Build a compact deterministic context block for a Planner or Coder prompt.

    Unknown projects intentionally return an empty string so existing non-CPU
    workflows do not gain irrelevant prompt text.
    """

    contract = detect_rv32i5_tier(project_id)
    if contract is None:
        return ""
    if role not in {"planner", "coder"}:
        raise ValueError("role 必须是 planner 或 coder")

    lines = [
        "RV32I fixed tier contract (implementation defaults, not user questions):",
        f"- Target: L{contract.expected_level} / dbg_features={contract.feature_bits}.",
        "- Required capability outputs: " + ", ".join(contract.required_capability_signals) + ".",
        "- Required RTL files: " + ", ".join(contract.required_modules) + ".",
        "- Focus order: " + " -> ".join(focus.name for focus in contract.focus_stages) + ".",
    ]
    if role == "planner":
        for focus in contract.focus_stages:
            lines.append(
                f"- {focus.name}: {focus.objective} Files: {', '.join(focus.modules)}."
            )
        policy = contract.seed_eligibility
        reference = (
            f"; fallback={policy.builtin_reference_id}"
            if policy.builtin_reference_id
            else ""
        )
        lines.append(
            "- Seed policy: require a passed, verified "
            f"L{policy.source_expected_level} run of {policy.source_project_id}{reference}."
        )
    else:
        lines.append(
            "- Preserve the stated module/file contract and all passed lower-tier behavior; "
            "do not replace the processor with a serial behavioral FSM."
        )
        lines.append(
            "- Repair in the focus order above.  Diagnose the earliest failing focus before "
            "rewriting unrelated modules."
        )
    lines.extend(f"- Default: {item}" for item in contract.non_blocking_assumptions)
    return "\n".join(lines)


def planner_context(project_id: str | None) -> str:
    """Convenience alias for the planner prompt integration point."""

    return describe_tier_context(project_id, role="planner")


def coder_context(project_id: str | None) -> str:
    """Convenience alias for the coder/modifier prompt integration point."""

    return describe_tier_context(project_id, role="coder")
