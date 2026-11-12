"""教师用例源码抽取：Diagnoser 只有 "t11_halt 超时" 时，Patch 需要看到程序本体。"""

import unittest

from ai_eda.agent.skills.teacher_tests import test_source


class TeacherTestSourceTests(unittest.TestCase):
    def test_extracts_program_and_checks(self):
        source = test_source("t11_halt")
        self.assertIn("task automatic build_t11_halt()", source)
        self.assertIn("e_ecall()", source)
        self.assertIn("check_exit_code", source)

    def test_unknown_case_is_empty(self):
        self.assertEqual(test_source("no_such_case"), "")
        self.assertEqual(test_source(None), "")
        self.assertEqual(test_source("../etc/passwd"), "")

    def test_truncates_long_source(self):
        source = test_source("t11_halt", max_lines=5)
        self.assertIn("truncated", source)
        self.assertLessEqual(len(source.splitlines()), 7)


if __name__ == "__main__":
    unittest.main()
