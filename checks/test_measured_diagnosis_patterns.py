"""Codex 新加的 5 条定向机械诊断：命中要准，近似输入不能误命中。

这些模式插在 _observed_diagnosis 的最前面，一旦误匹配会抢在其它模式之前
给出 high confidence 的错误根因，所以每条都要有正例 + 近似反例。
"""

import unittest

from ai_eda.agent.subagents.diagnoser import _observed_diagnosis

ID_AUIPC_GOOD = """
case (opcode)
  7'b0010111: begin
    dec_alu_src_pc = 1'b1; dec_alu_src_imm = 1'b1; dec_imm = imm_u;
  end
endcase
"""

EX_IMM = "assign alu_b = id_ex_alu_src_imm ? id_ex_imm : rs2_fwd;\nelse if (id_ex_alu_src_imm) alu_b = id_ex_imm;\n"


class AuipcPatternTests(unittest.TestCase):
    def evidence(self):
        return {"first_checks": [{"name": "t04_upper", "detail": "x6 期望 0x12345000 得到 0x00000003"}]}

    def test_missing_alu_src_imm_matches(self):
        files = {
            "rtl/branch_predictor.sv": "module branch_predictor; endmodule",
            "rtl/id_stage.sv": "case (opcode)\n  7'b0010111: begin\n    dec_alu_src_pc = 1'b1; dec_imm = imm_u;\n  end\nendcase\n",
            "rtl/ex_stage.sv": EX_IMM,
        }
        diagnosis = _observed_diagnosis(self.evidence(), files, {})
        self.assertIsNotNone(diagnosis)
        self.assertIn("AUIPC", diagnosis["hypothesis"])
        self.assertEqual(diagnosis["files"], ["rtl/id_stage.sv"])

    def test_present_alu_src_imm_does_not_match(self):
        files = {
            "rtl/branch_predictor.sv": "module branch_predictor; endmodule",
            "rtl/id_stage.sv": ID_AUIPC_GOOD,
            "rtl/ex_stage.sv": EX_IMM,
        }
        diagnosis = _observed_diagnosis(self.evidence(), files, {})
        self.assertTrue(diagnosis is None or "AUIPC" not in diagnosis["hypothesis"])


class ExitCodeFromDebugMuxTests(unittest.TestCase):
    def evidence(self):
        return {"first_checks": [{"name": "t02_alu_r", "detail": "exit_code 应等于停机时 x10，得到 0"}]}

    def test_debug_mux_source_matches(self):
        files = {
            "rtl/branch_predictor.sv": "module branch_predictor; endmodule",
            "rtl/rv32i5.sv": (
                "assign arch_x10 = (dbg_reg_addr == 5'd10) ? dbg_reg_data : 32'b0;\n"
                "wb_stage u_wb (.arch_x10(arch_x10));\n"
            ),
        }
        diagnosis = _observed_diagnosis(self.evidence(), files, {})
        self.assertIsNotNone(diagnosis)
        self.assertIn("debug", diagnosis["hypothesis"].lower())

    def test_committed_x10_source_does_not_match(self):
        files = {
            "rtl/branch_predictor.sv": "module branch_predictor; endmodule",
            "rtl/rv32i5.sv": "assign arch_x10 = wb_commit_value;\nwb_stage u_wb (.arch_x10(arch_x10));\n",
        }
        diagnosis = _observed_diagnosis(self.evidence(), files, {})
        self.assertTrue(diagnosis is None or "debug" not in diagnosis["hypothesis"].lower())


class RobCommitValidTests(unittest.TestCase):
    ROB = "assign commit_valid = valid[head_r];\nassign commit_fire = valid[head_r] && done[head_r];\n"

    def test_younger_complete_matches(self):
        diagnosis = _observed_diagnosis(
            {"first_checks": []},
            {"rtl/reorder_buffer.sv": self.ROB},
            {"l3_rob_allocation_observation": ("younger_complete",)},
        )
        self.assertIsNotNone(diagnosis)
        self.assertIn("commit_valid", diagnosis["hypothesis"])

    def test_commit_valid_from_fire_does_not_match(self):
        rob = "assign commit_valid = valid[head_r] && done[head_r];\nassign commit_fire = valid[head_r] && done[head_r];\n"
        diagnosis = _observed_diagnosis(
            {"first_checks": []},
            {"rtl/reorder_buffer.sv": rob},
            {"l3_rob_allocation_observation": ("younger_complete",)},
        )
        self.assertTrue(diagnosis is None or "commit_valid" not in diagnosis["hypothesis"])


class MemBusyFirstCycleTests(unittest.TestCase):
    MEM = "assign mem_busy = txn_active && !complete;\nassign issue = need_txn && !txn_active;\n"

    def test_first_cycle_busy_missing_matches(self):
        diagnosis = _observed_diagnosis(
            {"first_checks": [{"name": "t07_load_use", "detail": "x12 期望 0x2a 得到 0"}]},
            {"rtl/mem_stage.sv": self.MEM},
            {},
        )
        self.assertIsNotNone(diagnosis)
        self.assertIn("mem_busy", diagnosis["hypothesis"])

    def test_busy_including_need_txn_does_not_match(self):
        mem = "assign mem_busy = (need_txn || txn_active) && !complete;\nassign issue = need_txn && !txn_active;\n"
        diagnosis = _observed_diagnosis(
            {"first_checks": [{"name": "t07_load_use", "detail": "x12 期望 0x2a 得到 0"}]},
            {"rtl/mem_stage.sv": mem},
            {},
        )
        self.assertTrue(diagnosis is None or "mem_busy" not in diagnosis["hypothesis"])


if __name__ == "__main__":
    unittest.main()
