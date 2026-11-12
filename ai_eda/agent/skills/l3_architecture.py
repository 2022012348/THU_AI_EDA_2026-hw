"""Load the bundled L3 architecture with an explicit source and interface plan."""

from ai_eda.agent.cpu_workflow import frozen_component_interfaces, l3_allocation_wiring
from ai_eda.config import AGENT_CONTENT_ROOT


L3_ARCHITECTURE = "l3_architecture"
_MODULES = {
    "rv32i5": "Fetch and dispatch control and module wiring",
    "alu": "Integer execution and branch resolution",
    "decode": "RV32I source selection and memory immediate decode",
    "reorder_buffer": "Sole owner of allocation tags, completion and in-order commit",
    "rename_unit": "Speculative and committed RATs, checkpoints, circular free list and PRF",
    "issue_queue": "Oldest-ready integer issue, with control instructions gated at the head",
    "load_store_unit": "Two-entry memory queue with head-only access and held responses",
    "icache": "Instruction cache with redirect-safe refill",
    "dcache": "Blocking data cache",
    "branch_predictor": "Separate prediction and training interfaces",
}

_NOTES = (
    "One accepted dispatch allocates the ROB, renames and enqueues the selected queue. "
    "Integer and memory completions reach the ROB and PRF independently. "
    "Control recovery resolves at the ROB head. The LSU starts only at the head; "
    "independent younger integer work can issue while an older load waits. "
    "The IQ is indexed by the ROB tag, so a free ROB allocation slot guarantees a free IQ slot."
)


def _architecture_files() -> dict[str, str]:
    root = AGENT_CONTENT_ROOT / "skills" / "rv32i5_l3_architecture"
    return {f"rtl/{name}.sv": (root / f"{name}.sv").read_text(encoding="utf-8") for name in _MODULES}


def _plan(interfaces: dict[str, str]) -> dict:
    return {
        "top_module": "rv32i5",
        "schema_version": 1,
        "notes": _NOTES,
        "l3_allocation_tag_contract": True,
        "modules": [
            {"name": name, "file": f"rtl/{name}.sv", "interfaces": interfaces[f"rtl/{name}.sv"], "description": description}
            for name, description in _MODULES.items()
        ],
    }


def load_l3_architecture() -> tuple[dict[str, str], dict]:
    files = _architecture_files()
    l3_allocation_wiring(files)
    plan = _plan(frozen_component_interfaces(files))
    plan.update(project_name="Bundled L3 OoO architecture", architecture_profile=L3_ARCHITECTURE)
    return files, plan


def l3_generation_plan() -> dict:
    """Supply the verified interface contract to generation without passing RTL bodies."""
    interfaces = frozen_component_interfaces(_architecture_files())
    for path, ports in interfaces.items():
        expanded = []
        prefix = ""
        for port in ports.split("; "):
            if " " in port:
                prefix, pin = port.rsplit(" ", 1)
            else:
                pin = port
            expanded.append(f"{prefix} {pin}")
        interfaces[path] = "; ".join(expanded)
    plan = _plan(interfaces)
    plan.update(project_name="RV32I L3 OoO", l3_group_port_contract=True, interface_origin=L3_ARCHITECTURE)
    return plan
