"""重复声明/双驱动由编译期机械修复，删的是后出现的那条。"""

import unittest

from ai_eda.agent.compile_repair import repair


FILES = {
    "rtl/ex_stage.sv": "module ex_stage;\n  logic ex_mem_fwd_data;\n  assign ex_mem_fwd_data = a;\nendmodule",
    "rtl/rv32i5.sv": "module rv32i5;\n  wire ex_mem_fwd_data;\nendmodule",
}
COMBINED_LINES = ["// file: rtl/ex_stage.sv"] + FILES["rtl/ex_stage.sv"].splitlines() + [""] + \
                 ["// file: rtl/rv32i5.sv"] + FILES["rtl/rv32i5.sv"].splitlines()


def stderr_for(line: int) -> str:
    return (f"generated_attempt0.sv:{line}: error: 'ex_mem_fwd_data' has already been declared in this scope.\n"
            f"generated_attempt0.sv:4:      : It was declared here as a net.\n")


class CompileRepairTests(unittest.TestCase):
    def test_duplicate_declaration_in_second_file_is_removed(self):
        line = len(COMBINED_LINES)          # 第二条（rv32i5.sv 里的 wire）
        repaired, notes = repair(FILES, stderr_for(line))
        self.assertNotIn("wire ex_mem_fwd_data", repaired["rtl/rv32i5.sv"])
        self.assertIn("logic ex_mem_fwd_data", repaired["rtl/ex_stage.sv"])
        self.assertEqual(len(notes), 1)

    def test_unrelated_error_changes_nothing(self):
        repaired, notes = repair(FILES, "generated_attempt0.sv:9: error: syntax error\n")
        self.assertEqual(repaired, FILES)
        self.assertEqual(notes, [])


if __name__ == "__main__":
    unittest.main()
