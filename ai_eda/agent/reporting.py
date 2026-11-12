"""Shared human-readable reporting for test reports.

Both the sub-agent feedback layer and the persisted run payload use these
helpers so a report renders identically in the UI, history, and Modifier
feedback.
"""

from __future__ import annotations

from typing import Any

_STAGE_LABELS = {"compile": "Compile", "simulation": "Simulation"}


def stage_label(stage: str | None) -> str:
    return _STAGE_LABELS.get(stage or "", stage or "Unknown stage")


def report_summary(report: dict[str, Any] | None) -> str:
    if report is None:
        return "Verification has not run yet."
    if report.get("passed"):
        summary = f"Teacher TB passed ({stage_label(report.get('stage'))})."
        capability = report.get("cpu_capability") or {}
        level = capability.get("verified_level", capability.get("level"))
        if level is not None:
            summary += f" Verified CPU level: L{level}."
        return summary
    boundaries = report.get("l2_boundary_failures") or {}
    if report.get("teacher_passed") and any(boundaries.values()):
        failed_boundaries = ", ".join(
            f"{name}: {', '.join(failures)}" for name, failures in boundaries.items() if failures
        )
        return f"Teacher TB passed. Supplementary L2 checks failed ({failed_boundaries})."
    checks = report.get("checks") or []
    failed = ", ".join(item.get("name", "check") for item in checks[:4])
    suffix = f"; failed checks: {failed}" if failed else ""
    return f"Teacher TB failed ({stage_label(report.get('stage'))}){suffix}."


def report_preview(report: dict[str, Any], artifact: str) -> dict[str, Any]:
    """Bound the copy embedded in run events; the Tester artifact is authoritative."""
    preview = {**report, "report_artifact": artifact}
    omitted = {}
    for key, limit in (("checks", 200), ("trace", 2000), ("stdout", 16000), ("stderr", 16000)):
        value = report.get(key)
        if not isinstance(value, (list, str)) or len(value) <= limit:
            continue
        omitted[key] = len(value) - limit
        if key == "checks":
            preview[key] = value[:limit]
        else:
            preview[key] = value[:limit // 2] + value[-(limit // 2):]
    if omitted:
        preview["preview_omitted"] = omitted
    return preview
