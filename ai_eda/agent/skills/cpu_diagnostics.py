"""Deterministic, bounded diagnostics for the shared RV32I teacher TB.

The raw simulator report is intentionally retained by ``test_rtl`` for auditability,
but it is too large and repetitive to hand to a repair agent verbatim.  This module
turns that report into a small, JSON-serialisable repair packet without changing the
acceptance decision.  It is deliberately model-free so that the same failed run
always receives the same diagnostic identity.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


FINGERPRINT_VERSION = 1


def _text(value: Any, *, limit: int, default: str = "") -> str:
    """Return a compact, whitespace-normalised representation of ``value``."""
    if value is None:
        text = default
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def _bool(value: Any) -> bool:
    """Accept TB's numeric/string booleans as well as Python booleans."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "pass", "passed", "yes"}
    return False


def _compact_value(value: Any, *, text_limit: int, depth: int = 0) -> Any:
    """Keep trace values JSON-safe and bounded even for malformed reports."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _text(value, limit=text_limit)
    if depth >= 1:
        return _text(value, limit=text_limit)
    if isinstance(value, dict):
        return {
            _text(key, limit=64, default="field"): _compact_value(
                child, text_limit=text_limit, depth=depth + 1
            )
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))[:16]
        }
    if isinstance(value, (list, tuple)):
        return [
            _compact_value(child, text_limit=text_limit, depth=depth + 1)
            for child in value[:12]
        ]
    return _text(value, limit=text_limit)


def _compact_trace_event(event: Any, *, text_limit: int) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {"event": "invalid_trace_event", "value": _text(event, limit=text_limit)}
    # Sorting makes the packet deterministic when a report was built from an
    # unordered source, while preserving all commonly useful trace fields.
    return {
        _text(key, limit=64, default="field"): _compact_value(value, text_limit=text_limit)
        for key, value in sorted(event.items(), key=lambda item: str(item[0]))[:20]
    }


def _check_fields(check: Any) -> tuple[str, str, str]:
    """Normalise multiple checker schemas to testcase/name/detail."""
    if not isinstance(check, dict):
        detail = _text(check, limit=480, default="invalid check record")
        return "unknown", "invalid_check", detail
    testcase = _text(
        check.get("testcase", check.get("test", check.get("case", check.get("name")))),
        limit=120,
        default="unknown",
    )
    name = _text(
        check.get("check_name", check.get("check", check.get("assertion", check.get("name")))),
        limit=160,
        default=testcase,
    )
    detail = _text(check.get("detail", check.get("message", "")), limit=480)
    return testcase or "unknown", name or testcase or "unknown", detail


def _group_checks(checks: Any) -> tuple[list[dict[str, Any]], int]:
    records = checks if isinstance(checks, list) else []
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for index, check in enumerate(records):
        testcase, name, detail = _check_fields(check)
        key = (testcase, name, detail)
        group = grouped.get(key)
        if group is None:
            grouped[key] = {
                "testcase": testcase,
                "name": name,
                "detail": detail,
                "count": 1,
                "first_index": index,
                "last_index": index,
            }
        else:
            group["count"] += 1
            group["last_index"] = index
    groups = sorted(
        grouped.values(),
        key=lambda item: (-item["count"], item["testcase"], item["name"], item["detail"]),
    )
    return groups, len(records)


def _compact_mapping(value: Any, *, max_fields: int = 20) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        _text(key, limit=80, default="field"): _compact_value(child, text_limit=240)
        for key, child in sorted(value.items(), key=lambda item: str(item[0]))[:max_fields]
    }


def _compact_suites(value: Any, *, max_suites: int) -> tuple[list[dict[str, Any]], list[str], int]:
    suites = value if isinstance(value, list) else []
    compact: list[dict[str, Any]] = []
    failed_names: list[str] = []
    for suite in suites:
        if not isinstance(suite, dict):
            continue
        name = _text(suite.get("name"), limit=120, default="unnamed")
        passed = _bool(suite.get("passed"))
        skipped = _bool(suite.get("skipped"))
        if not passed and not skipped:
            failed_names.append(name)
        compact.append(
            {
                "name": name,
                "passed": passed,
                "skipped": skipped,
                "checks": suite.get("checks") if isinstance(suite.get("checks"), (int, float)) else None,
            }
        )
    compact.sort(key=lambda item: item["name"])
    return compact[:max_suites], sorted(set(failed_names)), max(0, len(compact) - max_suites)


def _compact_performance(value: Any, *, max_benchmarks: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = _compact_mapping({key: item for key, item in value.items() if key != "benchmarks"})
    benchmarks = value.get("benchmarks")
    if isinstance(benchmarks, list):
        compact = [_compact_mapping(item, max_fields=18) for item in benchmarks if isinstance(item, dict)]
        compact.sort(key=lambda item: str(item.get("benchmark", "")))
        result["benchmarks"] = compact[:max_benchmarks]
        if len(compact) > max_benchmarks:
            result["omitted_benchmarks"] = len(compact) - max_benchmarks
    return result


def _error_excerpt(report: dict[str, Any], *, max_lines: int = 12) -> list[str]:
    """Surface compiler/runtime messages without copying a large trace log."""
    source = report.get("stderr")
    if not isinstance(source, str) or not source.strip():
        # If there are no parsed checks, a simulator may only have written a
        # useful fatal line to stdout.  Ignore structured trace lines here.
        stdout = report.get("stdout")
        if isinstance(stdout, str) and not report.get("checks"):
            source = "\n".join(
                line for line in stdout.splitlines() if not line.startswith("AI_EDA_TRACE|")
            )
    if not isinstance(source, str):
        return []
    lines = [_text(line, limit=360) for line in source.splitlines() if line.strip()]
    return lines[-max_lines:]


def _fingerprint(
    *,
    passed: bool,
    stage: str,
    timed_out: bool,
    return_code: Any,
    all_groups: list[dict[str, Any]],
    failing_suites: list[str],
    capability: dict[str, Any],
    error_excerpt: list[str],
) -> str:
    """Hash only semantic failure signals, not volatile trace cycles/metrics."""
    capability_keys = ("level", "declared", "declared_level", "verified", "verified_level", "expected", "expected_level", "features")
    canonical = {
        "version": FINGERPRINT_VERSION,
        "passed": passed,
        "stage": stage,
        "timed_out": timed_out,
        "return_code": return_code,
        "checks": [
            {
                "testcase": item["testcase"],
                "name": item["name"],
                "detail": item["detail"],
                "count": item["count"],
            }
            for item in sorted(
                all_groups,
                key=lambda item: (item["testcase"], item["name"], item["detail"]),
            )
        ],
        "failing_suites": sorted(failing_suites),
        "capability": {key: capability[key] for key in capability_keys if key in capability},
        "error_excerpt": error_excerpt if not all_groups else [],
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _failure_score(
    *,
    passed: bool,
    stage: str,
    timed_out: bool,
    check_count: int,
    unique_check_count: int,
    failing_suite_count: int,
    has_error_excerpt: bool,
) -> tuple[int, dict[str, int]]:
    """Return a monotonic, explainable severity score for regression gating."""
    if passed:
        return 0, {
            "base": 0,
            "stage": 0,
            "timeout": 0,
            "checks": 0,
            "unique_checks": 0,
            "failing_suites": 0,
            "unclassified_error": 0,
        }
    stage_weight = {"compile": 5_000, "simulation": 1_000}.get(stage, 2_000)
    components = {
        "base": 100,
        "stage": stage_weight,
        "timeout": 3_000 if timed_out else 0,
        "checks": min(check_count, 1_000) * 2,
        "unique_checks": min(unique_check_count, 200) * 25,
        "failing_suites": min(failing_suite_count, 20) * 200,
        "unclassified_error": 250 if not check_count and has_error_excerpt else 0,
    }
    return sum(components.values()), components


def summarize_cpu_report(
    report: dict[str, Any] | None,
    *,
    max_check_groups: int = 24,
    first_trace_events: int = 6,
    last_trace_events: int = 6,
    max_suites: int = 16,
    max_benchmarks: int = 12,
) -> dict[str, Any]:
    """Build a deterministic, bounded repair packet from a CPU Tester report.

    ``failure_fingerprint`` intentionally ignores trace timing and performance, so
    repeated runs of the same logical failure can be recognized even when cycle
    counts move.  ``failure_score`` is a coarse monotonic severity metric: lower
    is better and zero means the report passed.
    """
    if max_check_groups < 1 or first_trace_events < 0 or last_trace_events < 0:
        raise ValueError("check-group limit must be positive and trace limits cannot be negative")
    if max_suites < 1 or max_benchmarks < 1:
        raise ValueError("suite and benchmark limits must be positive")

    source = report if isinstance(report, dict) else {}
    passed = _bool(source.get("passed"))
    stage = _text(source.get("stage"), limit=80, default="unknown")
    timed_out = _bool(source.get("timed_out"))
    all_groups, check_count = _group_checks(source.get("checks"))
    visible_groups = all_groups[:max_check_groups]
    omitted_groups = all_groups[max_check_groups:]
    capability = _compact_mapping(source.get("cpu_capability"))
    suites, failing_suites, omitted_suites = _compact_suites(
        source.get("test_suites"), max_suites=max_suites
    )
    performance = _compact_performance(source.get("performance"), max_benchmarks=max_benchmarks)
    error_excerpt = _error_excerpt(source)

    traces = source.get("trace") if isinstance(source.get("trace"), list) else []
    first = [
        _compact_trace_event(event, text_limit=360)
        for event in traces[:first_trace_events]
    ]
    last_start = max(first_trace_events, len(traces) - last_trace_events)
    last = [
        _compact_trace_event(event, text_limit=360)
        for event in traces[last_start:]
    ]

    score, score_components = _failure_score(
        passed=passed,
        stage=stage,
        timed_out=timed_out,
        check_count=check_count,
        unique_check_count=len(all_groups),
        failing_suite_count=len(failing_suites),
        has_error_excerpt=bool(error_excerpt),
    )
    fingerprint = _fingerprint(
        passed=passed,
        stage=stage,
        timed_out=timed_out,
        return_code=source.get("return_code"),
        all_groups=all_groups,
        failing_suites=failing_suites,
        capability=capability,
        error_excerpt=error_excerpt,
    )

    return {
        "schema": "ai_eda.cpu_repair_packet.v1",
        "passed": passed,
        "stage": stage,
        "return_code": source.get("return_code"),
        "timed_out": timed_out,
        "timeout_seconds": source.get("timeout_seconds"),
        "failure_fingerprint": fingerprint,
        "failure_score": score,
        "score_components": score_components,
        "checks": {
            "total": check_count,
            "unique": len(all_groups),
            "groups": visible_groups,
            "omitted_groups": len(omitted_groups),
            "omitted_occurrences": sum(item["count"] for item in omitted_groups),
        },
        "trace_windows": {
            "total": len(traces),
            "first": first,
            "last": last,
            "omitted": max(0, len(traces) - len(first) - len(last)),
        },
        "cpu_capability": capability,
        "test_suites": suites,
        "omitted_suites": omitted_suites,
        "performance": performance,
        "error_excerpt": error_excerpt,
    }


# A descriptive alias makes the intended use clear at integration sites.
build_cpu_repair_packet = summarize_cpu_report


__all__ = [
    "FINGERPRINT_VERSION",
    "build_cpu_repair_packet",
    "summarize_cpu_report",
]
