"""重名端口由机械去重修掉，而不是回喂给 Planner 重写一遍。"""

import unittest

from ai_eda.agent.cpu_workflow import dedupe_plan_ports


class PlanDedupeTests(unittest.TestCase):
    def test_duplicate_port_dropped(self):
        plan = {"modules": [{"name": "mem_stage", "file": "rtl/mem_stage.sv",
                             "interfaces": "input clk; input mem_wb_fwd_data; input mem_wb_fwd_data"}]}
        interfaces = dedupe_plan_ports(plan)["modules"][0]["interfaces"]
        self.assertEqual(interfaces.count("mem_wb_fwd_data"), 1)
        self.assertIn("input mem_wb_fwd_data", interfaces)

    def test_conflicting_port_declarations_are_preserved_for_validation(self):
        interfaces = "input [31:0] a; output [31:0] a; input [7:0] a"
        plan = {"modules": [{"name": "m", "interfaces": interfaces}]}
        self.assertEqual(dedupe_plan_ports(plan)["modules"][0]["interfaces"], interfaces)

    def test_width_declaration_preserved(self):
        plan = {"modules": [{"name": "m", "file": "rtl/m.sv",
                             "interfaces": "input [31:0] a; output [31:0] b"}]}
        self.assertEqual(dedupe_plan_ports(plan)["modules"][0]["interfaces"], "input [31:0] a; output [31:0] b")


if __name__ == "__main__":
    unittest.main()
