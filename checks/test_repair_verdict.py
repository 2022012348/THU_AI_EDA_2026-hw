import os
import unittest
from unittest import mock

from ai_eda.agent.subagents.repair import repair_verdict


def report(names, hangs=(), suites=(("base", False),), timed_out=None):
    checks = [
        {"name": name, "detail": "等待 halted 超时" if name in hangs else "mismatch"}
        for name in names
    ]
    data = {
        "stage": "simulation",
        "passed": False,
        "checks": checks,
        "test_suites": [{"name": name, "passed": passed} for name, passed in suites],
    }
    if timed_out is not None:
        data["timed_out"] = timed_out
    return data


class BulkDropVerdictTests(unittest.TestCase):
    def test_bulk_drop_with_one_new_case_is_accepted(self):
        # REFACTOR.md:198 的历史事故：103 → 34 因新增用例被整份判负。
        old = report([f"c{i}" for i in range(103)])
        new = report([f"c{i}" for i in range(34)] + ["new_case"])
        self.assertEqual(repair_verdict(old, new), (True, "substantial_assertion_drop"))

    def test_bulk_drop_with_one_new_hang_is_accepted(self):
        old = report([f"c{i}" for i in range(103)])
        new = report([f"c{i}" for i in range(34)] + ["new_case"], hangs=("new_case",))
        self.assertEqual(repair_verdict(old, new), (True, "substantial_assertion_drop"))

    def test_small_drop_with_new_case_is_still_rejected(self):
        old = report([f"c{i}" for i in range(40)])
        new = report([f"c{i}" for i in range(38)] + ["new_case"])
        self.assertEqual(repair_verdict(old, new), (False, "new_failing_cases"))

    def test_three_new_cases_rejected_even_after_bulk_drop(self):
        old = report([f"c{i}" for i in range(103)])
        new = report([f"c{i}" for i in range(34)] + ["a", "b", "c"])
        self.assertEqual(repair_verdict(old, new), (False, "new_failing_cases"))

    def test_new_hang_without_bulk_drop_is_rejected(self):
        old = report([f"c{i}" for i in range(60)])
        new = report([f"c{i}" for i in range(45)] + ["new_case"], hangs=("new_case",))
        self.assertEqual(repair_verdict(old, new), (False, "additional_halt_timeouts"))

    def test_previously_passing_suite_still_blocks(self):
        old = report([f"c{i}" for i in range(103)], suites=(("base", False), ("cache", True)))
        new = report([f"c{i}" for i in range(34)] + ["new_case"], suites=(("base", False), ("cache", False)))
        self.assertEqual(repair_verdict(old, new), (False, "previously_passing_suite_regressed"))

    def test_lost_simulation_coverage_still_blocks(self):
        old = report([f"c{i}" for i in range(103)], timed_out=False)
        new = report([f"c{i}" for i in range(34)], timed_out=True)
        self.assertEqual(repair_verdict(old, new), (False, "lost_simulation_coverage"))

    def test_strict_switch_restores_history(self):
        old = report([f"c{i}" for i in range(103)])
        new = report([f"c{i}" for i in range(34)] + ["new_case"])
        with mock.patch.dict(os.environ, {"EDA_VERDICT_STRICT": "1"}):
            self.assertEqual(repair_verdict(old, new), (False, "new_failing_cases"))


if __name__ == "__main__":
    unittest.main()
