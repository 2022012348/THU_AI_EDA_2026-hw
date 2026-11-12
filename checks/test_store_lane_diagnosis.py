"""存储字节车道：三条同族证据才触发，证据不足不触发。"""

import unittest

from ai_eda.agent.subagents.diagnoser import _observed_diagnosis


def evidence(rows):
    return {"first_checks": [{"name": name, "detail": detail} for name, detail in rows]}


FILES = {"rtl/mem_stage.sv": "module mem_stage; endmodule", "rtl/rv32i5.sv": "module rv32i5; endmodule"}


class StoreLaneDiagnosisTests(unittest.TestCase):
    def test_illegal_byte_enable_is_high_confidence(self):
        diagnosis = _observed_diagnosis(evidence([
            ("t06_load_store", "非法字节使能：dmem_be=1100 dmem_addr=0x00000114"),
            ("t06_load_store", "x22 期望 0xa5a5a500 得到 0xa5a5a5a5"),
            ("t08_store_fwd", "DMEM 写事务数期望 7 得到 6"),
        ]), FILES, {})
        self.assertIsNotNone(diagnosis)
        self.assertEqual(diagnosis["confidence"], "high")
        self.assertEqual(diagnosis["files"], ["rtl/mem_stage.sv"])
        self.assertIn("0001<<addr[1:0]", " ".join(diagnosis["constraints"]))
        self.assertIn("t06_load_store", diagnosis["teacher_checks"])

    def test_three_lane_rows_without_illegal_mask_are_medium(self):
        diagnosis = _observed_diagnosis(evidence([
            ("t06_load_store", "x24 期望 0x0000a500 得到 0xa5a5a5a5"),
            ("t08_store_fwd", "DMEM[0x00000214] 期望 0xa5a507aa 得到 0xa5a5a5a5"),
            ("t08_store_fwd", "DMEM 写事务数期望 7 得到 6"),
        ]), FILES, {})
        self.assertEqual(diagnosis["confidence"], "medium")

    def test_two_rows_are_not_enough(self):
        diagnosis = _observed_diagnosis(evidence([
            ("t06_load_store", "非法字节使能：dmem_be=1100 dmem_addr=0x00000114"),
            ("t08_store_fwd", "DMEM 写事务数期望 7 得到 6"),
        ]), FILES, {})
        self.assertNotEqual(diagnosis and diagnosis["confidence"], "high")


if __name__ == "__main__":
    unittest.main()
