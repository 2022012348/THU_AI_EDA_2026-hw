  // ------------------------- t13: 16 点 DFT 真实程序 -------------------------
  // 数据布局：
  //   0x300..0x33F  x[0..15]（方波）
  //   0x400..0x43F  X[k].re（Q8）
  //   0x440..0x47F  X[k].im（Q8）
  //   0x380..0x3BF  cos(2πm/16) Q8
  //   0x3C0..0x3FF  sin(2πm/16) Q8
  task automatic build_t13_dft();
    integer p;
    begin
      for (p = 1; p <= 2; p = p + 1) begin
        pass_no = p;
        prog_len = 0;
        e_addi(5'd1, 5'd0, 32'h300);     // x base
        e_addi(5'd11, 5'd0, 32'h380);    // cos base
        e_addi(5'd12, 5'd0, 32'h3C0);    // sin base
        e_addi(5'd30, 5'd0, 32'h400);    // real output base
        e_addi(5'd31, 5'd0, 32'h440);    // imag output base
        e_addi(5'd2, 5'd0, 0);           // k
        mark(L20);                       // OUTER
        e_addi(5'd4, 5'd0, 0);           // sum_re
        e_addi(5'd5, 5'd0, 0);           // sum_im
        e_addi(5'd3, 5'd0, 0);           // n
        e_addi(5'd13, 5'd0, 0);          // m = (k*n)&15
        mark(L21);                       // INNER
        e_slli(5'd6, 5'd3, 2);
        e_add (5'd6, 5'd1, 5'd6);
        e_lw  (5'd8, 5'd6, 0);           // x[n]
        e_slli(5'd14, 5'd13, 2);
        e_add (5'd14, 5'd11, 5'd14);
        e_lw  (5'd7, 5'd14, 0);          // cos[m]
        e_jal (5'd15, L22);              // mul(x, cos)
        e_add (5'd4, 5'd4, 5'd9);        // sum_re += product
        e_slli(5'd14, 5'd13, 2);
        e_add (5'd14, 5'd12, 5'd14);
        e_lw  (5'd7, 5'd14, 0);          // sin[m]
        e_slli(5'd6, 5'd3, 2);
        e_add (5'd6, 5'd1, 5'd6);
        e_lw  (5'd8, 5'd6, 0);           // reload x[n]
        e_jal (5'd15, L22);              // mul(x, sin)
        e_sub (5'd5, 5'd5, 5'd9);        // sum_im -= product
        e_addi(5'd3, 5'd3, 1);
        e_add (5'd13, 5'd13, 5'd2);
        e_andi(5'd13, 5'd13, 15);
        e_addi(5'd17, 5'd0, 16);
        e_blt (5'd3, 5'd17, L21);
        e_slli(5'd6, 5'd2, 2);
        e_add (5'd6, 5'd30, 5'd6);
        e_sw  (5'd4, 5'd6, 0);           // X[k].re
        e_slli(5'd6, 5'd2, 2);
        e_add (5'd6, 5'd31, 5'd6);
        e_sw  (5'd5, 5'd6, 0);           // X[k].im
        e_addi(5'd2, 5'd2, 1);
        e_addi(5'd17, 5'd0, 16);
        e_blt (5'd2, 5'd17, L20);
        e_addi(5'd10, 5'd0, 32'hDF);     // exit code 0xDF
        e_ecall();
        // 无符号/有符号 shift-add 乘法子程序：x9 = x7 * x8
        mark(L22);                       // MUL
        e_addi(5'd9, 5'd0, 0);
        e_addi(5'd16, 5'd0, 0);
        e_bge (5'd7, 5'd0, L23);
        e_sub (5'd7, 5'd0, 5'd7);
        e_addi(5'd16, 5'd0, 1);
        mark(L23);                       // MUL_POS
        e_beq (5'd7, 5'd0, L26);
        mark(L24);                       // MUL_LOOP
        e_andi(5'd17, 5'd7, 1);
        e_beq (5'd17, 5'd0, L25);
        e_add (5'd9, 5'd9, 5'd8);
        mark(L25);                       // MUL_SKIP
        e_slli(5'd8, 5'd8, 1);
        e_srli(5'd7, 5'd7, 1);
        e_bne (5'd7, 5'd0, L24);
        mark(L26);                       // MUL_DONE
        e_beq (5'd16, 5'd0, L27);
        e_sub (5'd9, 5'd0, 5'd9);
        mark(L27);                       // MUL_RET
        e_jalr(5'd0, 5'd15, 0);
      end
    end
  endtask

  task automatic test_t13_dft();
    integer k;
    integer n;
    integer m;
    integer re;
    integer im;
    integer mag;
    integer exp_mag;
    integer xin [0:15];
    integer cosq [0:15];
    integer sinq [0:15];
    integer exp_re [0:15];
    integer exp_im [0:15];
    begin
      // 16 点方波：前 8 个采样 +1，后 8 个采样 -1（零直流）。
      xin[0] = 1; xin[1] = 1; xin[2] = 1; xin[3] = 1;
      xin[4] = 1; xin[5] = 1; xin[6] = 1; xin[7] = 1;
      xin[8] = -1; xin[9] = -1; xin[10] = -1; xin[11] = -1;
      xin[12] = -1; xin[13] = -1; xin[14] = -1; xin[15] = -1;
      cosq[0]=256; cosq[1]=237; cosq[2]=181; cosq[3]=98;
      cosq[4]=0;   cosq[5]=-98; cosq[6]=-181; cosq[7]=-237;
      cosq[8]=-256; cosq[9]=-237; cosq[10]=-181; cosq[11]=-98;
      cosq[12]=0;  cosq[13]=98;  cosq[14]=181; cosq[15]=237;
      sinq[0]=0;   sinq[1]=98;  sinq[2]=181;  sinq[3]=237;
      sinq[4]=256; sinq[5]=237; sinq[6]=181;  sinq[7]=98;
      sinq[8]=0;   sinq[9]=-98; sinq[10]=-181; sinq[11]=-237;
      sinq[12]=-256; sinq[13]=-237; sinq[14]=-181; sinq[15]=-98;
      for (k = 0; k < 16; k = k + 1) begin
        exp_re[k] = 0;
        exp_im[k] = 0;
        for (n = 0; n < 16; n = n + 1) begin
          m = (k * n) & 15;
          exp_re[k] = exp_re[k] + xin[n] * cosq[m];
          exp_im[k] = exp_im[k] - xin[n] * sinq[m];
        end
      end
      build_t13_dft();
      begin_test("t13_dft");
      for (k = 0; k < 16; k = k + 1) begin
        set_dmem_word(32'h300 + 4*k, xin[k]);
        set_dmem_word(32'h380 + 4*k, cosq[k]);
        set_dmem_word(32'h3C0 + 4*k, sinq[k]);
        $display("AI_EDA_TRACE|cycle=%0d|test=t13_dft|event=source|n=%0d|x=%0d|expected_x=%0d",
                 cycle_count, k, xin[k], xin[k]);
      end
      release_reset();
      run_program(60000, 50000);
      if (!run_timed_out) begin
        check_halted();
        check_x0();
        check_reg(10, 32'h0000_00DF);
        check_exit_code(32'h0000_00DF);
        for (k = 0; k < 16; k = k + 1) begin
          check_mem_word(32'h400 + 4*k, exp_re[k]);
          check_mem_word(32'h440 + 4*k, exp_im[k]);
          re = {dmem[32'h400 + 4*k + 3], dmem[32'h400 + 4*k + 2],
                dmem[32'h400 + 4*k + 1], dmem[32'h400 + 4*k]};
          im = {dmem[32'h440 + 4*k + 3], dmem[32'h440 + 4*k + 2],
                dmem[32'h440 + 4*k + 1], dmem[32'h440 + 4*k]};
          mag = (re < 0 ? -re : re) + (im < 0 ? -im : im);
          exp_mag = (exp_re[k] < 0 ? -exp_re[k] : exp_re[k])
                  + (exp_im[k] < 0 ? -exp_im[k] : exp_im[k]);
          checks_run = checks_run + 1;
          $display("AI_EDA_TRACE|cycle=%0d|test=t13_dft|event=bin|k=%0d|re=%0d|im=%0d|mag=%0d|expected_mag=%0d",
                   cycle_count, k, re, im, mag, exp_mag);
          if (mag !== exp_mag) begin
            fail(cur_test, $sformatf("DFT 频点 %0d 幅值期望 %0d 得到 %0d", k, exp_mag, mag));
          end
        end
        check_halt_stability();
      end
      end_test();
    end
  endtask

