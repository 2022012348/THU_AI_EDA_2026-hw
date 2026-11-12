"""Planner 反馈文案：报错必须写出"实际方向"。

实测 `agent-20261112T100458454094` 连续 4 轮 Planner 都因为
"hazard_unit.if_id_ready is input; ID owns decode readiness" 被拒，而它实际写的是
output——旧文案把期望方向当成实际方向打印，模型无从纠正。
"""

import unittest

from ai_eda.agent.cpu_workflow import if_id_ready_message


class PlannerFeedbackTests(unittest.TestCase):
    def test_message_names_actual_direction(self):
        message = if_id_ready_message("hazard_unit", "output", "input")
        self.assertIn("hazard_unit.if_id_ready is output, expected input", message)

    def test_message_explains_ownership(self):
        self.assertIn("output of id_stage", if_id_ready_message("x", "a", "b"))


if __name__ == "__main__":
    unittest.main()
