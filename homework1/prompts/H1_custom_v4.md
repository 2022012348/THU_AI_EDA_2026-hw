实现单模块 AES-128 加密核 `aes128`（FIPS-197，只做加密），用于 45nm 标准单元库下的面积/功耗对比实验。

最终回答**只能包含一个 `module aes128 ... endmodule` 的完整 SystemVerilog 源码**：不要 Markdown 说明、不要推导过程、不要 testbench、不要第二个模块。不得出现 `include`、`$readmemh`、DPI、IP 例化或外部文件依赖。

## 一、接口（逐字符一致，不得改动）

```systemverilog
module aes128 (
    input  logic         clk,
    input  logic         rst_n,
    input  logic         start,
    input  logic [127:0] key,
    input  logic [127:0] plaintext,
    output logic         ready,
    output logic         done,
    output logic [127:0] ciphertext
);
```

`ready`、`done`、`ciphertext` 必须是寄存器输出，端口不得在模块体内重复声明。

## 二、字节序（先固化，后续所有逻辑以此为准）

- 128-bit 向量按 FIPS-197 大端：`[127:120]` 是 byte 0，`[7:0]` 是 byte 15。
- 状态按列优先存成 4 个 32-bit 列寄存器 `s0..s3`，`s_c` 保存第 c 列，且 `s_c[31:24]` 是该列第 0 行：
  - 接收时（**最容易错的一步，务必逐字照抄**）：列号与位段是**同向**的——第 0 列占最高 32 位：
    `s0 <= plaintext[127:96] ^ key[127:96]`；
    `s1 <= plaintext[95:64]  ^ key[95:64]`；
    `s2 <= plaintext[63:32]  ^ key[63:32]`；
    `s3 <= plaintext[31:0]   ^ key[31:0]`。
    注意 `plaintext[31:0]` 是**最后一列**（byte 12~15），把它当成第 0 列会让整个状态转置式错位，密文全错而握手时序却完全正常（这种错误极难从波形上看出来）。
  - 输出时：`ciphertext <= {col0, col1, col2, col3}`，即 **col0 放在最高 32 位**（`ciphertext[127:96] = col0`）；写成 `{col3, col2, col1, col0}` 会让整条结果字节序颠倒。
- 轮密钥字：`w0 = key[127:96]`，`w1 = key[95:64]`，`w2 = key[63:32]`，`w3 = key[31:0]`；每个字的最高字节是该字第 0 字节。

## 三、握手、复位与时序

1. `rst_n` 是**同步低有效**复位，只在 `clk` 上升沿生效（禁止 `negedge rst_n`）。复位后空闲：`ready=1`、`done=0`。
2. 空闲时 `start=1` 的上升沿锁存 `key`/`plaintext` 并开始加密；从下一拍起 `ready=0`，忙碌期间忽略 `start`。
3. `done` 只高**一个周期**，该周期 `ciphertext` 有效、`ready=0`；下一拍 `done=0`、`ready=1`，且当拍能立即接受新 `start`（背靠背，无气泡）。
4. 从接收 `start` 的沿（不含）到 `done=1` 的沿（含）不超过 200 拍。
5. 全部寄存器同步复位；不得有 `initial`、`#delay`、锁存器、多驱动。

## 四、算法

- 初始轮：`s = plaintext ^ {w0,w1,w2,w3}`。
- 第 1–9 轮：`SubBytes → ShiftRows → MixColumns → AddRoundKey`；第 10 轮：`SubBytes → ShiftRows → AddRoundKey`。
- **每次加密对每个字节只做一次 SubBytes**。`sb0..sb3` 存的就是 SubBytes 之后的结果；`sr0..sr3` 是 `sb` 的 ShiftRows，**已经包含 SubBytes**。因此：
  - 第 1–9 轮的 `MixColumns` 输入是 `sr`，**不要再对 `sr` 调 `sbox`**；
  - 第 10 轮的密文是 `ciphertext <= {sr0, sr1, sr2, sr3} ^ {w0, w1, w2, w3}`（col0 在最高位），**同样不要再调 `sbox`**。
  写成 `c0 = {sbox(sr0[31:24]), ...}` 就是对已置换数据再置换一次：结果全错，同时多出 16 个 S-box，面积会从约 35 k 涨到约 52 k（一眼可辨）。
- `ShiftRows`：`out[r][c] = in[r][(c+r) mod 4]`。用列寄存器表示时：`sr0 = {sb0[31:24], sb1[23:16], sb2[15:8], sb3[7:0]}`，`sr1 = {sb1[31:24], sb2[23:16], sb3[15:8], sb0[7:0]}`，`sr2 = {sb2[31:24], sb3[23:16], sb0[15:8], sb1[7:0]}`，`sr3 = {sb3[31:24], sb0[23:16], sb1[15:8], sb2[7:0]}`。
- `MixColumns`：`out[0] = 2·b0 ^ 3·b1 ^ b2 ^ b3`，`out[1] = b0 ^ 2·b1 ^ 3·b2 ^ b3`，`out[2] = b0 ^ b1 ^ 2·b2 ^ 3·b3`，`out[3] = 3·b0 ^ b1 ^ b2 ^ 2·b3`，其中 `2·b = xtime(b) = {b[6:0],1'b0} ^ (b[7] ? 8'h1b : 8'h00)`，`3·b = xtime(b) ^ b`。
  **易错点**：`3·b1` 必须写成 `xtime(b1) ^ b1`，绝不能写成 `b1 ^ b1`（那是 0）。每个 `xtime` 结果只算一次并在 4 个输出字节间共享。
  **边界（务必遵守）**：`s_c` 本身就是**一列**的 4 个字节。做第 c 列的 MixColumns 时，把这 4 个字节记作 `b0=s_c[31:24]`、`b1=s_c[23:16]`、`b2=s_c[15:8]`、`b3=s_c[7:0]`，四个输出只允许引用它们：`m0 = 2·b0 ^ 3·b1 ^ b2 ^ b3`、`m1 = b0 ^ 2·b1 ^ 3·b2 ^ b3`、`m2 = b0 ^ b1 ^ 2·b2 ^ 3·b3`、`m3 = 3·b0 ^ b1 ^ b2 ^ 2·b3`，其中 `3·bx = xtime(bx) ^ bx`。
  每一列的四个输出只能出现该列自己的字节；**不允许**出现相邻列 `s_{c+1}`、`s_{c-1}` 的字节（把 4 个列字当成同一列的 4 个字节、在 `m0` 里混进别的列的字节，是隐蔽错误：握手正常、密文全错、面积看不出异常）。
- 密钥扩展：`w[i] = w[i-4] ^ temp`；`i%4==0` 时 `temp = SubWord(RotWord(w[i-1])) ^ Rcon[i/4]`，`Rcon[1..10]=01,02,04,08,10,20,40,80,1b,36`。`RotWord` 是字节左旋一位：`{w3[23:16], w3[15:8], w3[7:0], w3[31:24]}`；**Rcon 异或到最高字节**：`sub_word(rot) ^ {rcon, 24'b0}`（写成 `{24'b0, rcon}` 会全错）。链式：`nw0 = w0 ^ temp; nw1 = w1 ^ nw0; nw2 = w2 ^ nw1; nw3 = w3 ^ nw2;`
- S-box 必须逐字节照抄下表，用 `function automatic logic [7:0] sbox(input logic [7:0] a)` + `case` 实现；**函数体内禁止使用 `return`**，用 `sbox = 8'hxx;` 给函数名赋值。`xtime`/`rcon` 同样用 function 实现（`rcon` 可在 function 里用 case 返回 8 位常量）。

```text
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00  63 7c 77 7b f2 6b 6f c5 30 01 67 2b fe d7 ab 76
10  ca 82 c9 7d fa 59 47 f0 ad d4 a2 af 9c a4 72 c0
20  b7 fd 93 26 36 3f f7 cc 34 a5 e5 f1 71 d8 31 15
30  04 c7 23 c3 18 96 05 9a 07 12 80 e2 eb 27 b2 75
40  09 83 2c 1a 1b 6e 5a a0 52 3b d6 b3 29 e3 2f 84
50  53 d1 00 ed 20 fc b1 5b 6a cb be 39 4a 4c 58 cf
60  d0 ef aa fb 43 4d 33 85 45 f9 02 7f 50 3c 9f a8
70  51 a3 40 8f 92 9d 38 f5 bc b6 da 21 10 ff f3 d2
80  cd 0c 13 ec 5f 97 44 17 c4 a7 7e 3d 64 5d 19 73
90  60 81 4f dc 22 2a 90 88 46 ee b8 14 de 5e 0b db
a0  e0 32 3a 0a 49 06 24 5c c2 d3 ac 62 91 95 e4 79
b0  e7 c8 37 6d 8d d5 4e a9 6c 56 f4 ea 65 7a ae 08
c0  ba 78 25 2e 1c a6 b4 c6 e8 dd 74 1f 4b bd 8b 8a
d0  70 3e b5 66 48 03 f6 0e 61 35 57 b9 86 c1 1d 9e
e0  e1 f8 98 11 69 d9 8e 94 9b 1e 87 e9 ce 55 28 df
f0  8c a1 89 0d bf e6 42 68 41 99 2d 0f b0 54 bb 16
```

## 五、目标微架构（面积优先，按此实现）

**不要**为 16 个字节例化 16 个 S-box，也**不要**展开 10 轮。要求：

- 状态 `s0..s3`（4×32-bit 列寄存器，就地累加 SubBytes 结果到独立的 `sb0..sb3`），轮密钥 `w0..w3`（4×32-bit，在线展开），计数器 `round[3:0]`、`phase[2:0]`、`busy`，`done`/`ready` 各 1 位。
- **每个 phase 只实例化 4 个 S-box**（对一列 4 字节），共 4 个 phase 覆盖 16 字节；密钥扩展另用 4 个 S-box（`SubWord`），总计 **8 个 S-box**。写成 `sb0 <= {sbox(s0[31:24]), sbox(s0[23:16]), sbox(s0[15:8]), sbox(s0[7:0])}` 这种按列形式，由 `phase` 选择更新哪一列。
- 每轮 5 拍：
  - `phase=0..3`：更新 `sb{phase}`（对该列做 SubBytes）；`phase=0` 时同时把 `w <= nw`（`nw` 为组合逻辑按 Rcon 展开的下一组轮密钥），使后续 `AddRoundKey` 用**新一轮**密钥。
  - `phase=4`：若 `round<9`：`s <= MixColumns(ShiftRows(sb)) ^ {w0,w1,w2,w3}`，`round<=round+1`，`phase<=0`；若 `round==9`：`ciphertext <= {sr0,sr1,sr2,sr3} ^ {w0,w1,w2,w3}`（`sr0` 在最高位，**不做 MixColumns，也不再调 `sbox`**），`done<=1`，`busy<=0`，`ready<=1`。
  - 总延迟约 50 拍，远低于 200 拍上限。
- **AddRoundKey 的密钥必须与本次轮函数输出对应**：`phase=4` 使用的是 `phase=0` 时刚锁存的下一轮密钥 `w`（第 r 轮结束加第 r+1 轮密钥；第 9 轮结束加第 10 轮密钥）。不要用旧的 `w`，否则每轮都错一轮密钥。
- `ShiftRows` 用硬连线字节拼接（按第四节公式），不引入 128-bit 移位器、索引变量或循环。
- **所有组合逻辑一律用 `wire` + `assign` 实现**（`temp`/`nw0..nw3`、`sr0..sr3`、`xtime` 结果等），不要用 `always_comb`，更不要在多处对同一个变量赋值；每个中间量只声明一次。否则 iverilog 会报 `Variable 'a0' cannot have multiple drivers` 而直接编译失败。
- 组合中间量也**不要**复用名字（例如先算第 0 列的 `x0..x3` 再覆盖成第 1 列的），要么每列用独立名字（`x0a/x0b/...`），要么写成 `wire [31:0] mc0 = {...}` 的一体化表达式。
- **S-box 的预算**：源码里 `sbox(...)` 文本调用共 20 处——数据通路 4 列 × 4 字节 = 16 处（`sb0..sb3` 各一处），密钥扩展 `SubWord` 4 处；综合后实例数为 8（4 个数据 S-box 由 4 个 phase 分时复用，加 4 个密钥 S-box）。**不要出现第 21 处**：最常见的错误是在最后一轮对 `sr` 再调 `sbox`（多 16 个实例，面积从约 35 k 涨到约 52 k）。
- 空闲态不更新状态寄存器，避免无意义翻转；不使用门控单元、存储阵列、11×128-bit 轮密钥阵列。

## 六、生成前自查

1. 只有一个模块，端口与第一节逐字一致，无重复声明。
2. `function` 内无 `return`；S-box 256 项逐项正确（抽查 `00→63`、`01→7c`、`53→ed`、`ff→16`）。
3. `3·b` 写成 `xtime(b) ^ b`；ShiftRows 四个拼接式与第四节完全一致。
4. 第 10 轮无 MixColumns；Rcon 在最高字节；`round` 与 `phase` 的进位/清零正确。
5. `done` 恰好一拍；`ready` 在 `done` 后下一拍为 1；复位同步低有效。
6. MixColumns 的 4 个输出只引用本列字节 `b0..b3`，没有混入相邻列的字节。
7. `sbox(...)` 文本调用共 20 处（数据 16 + 密钥 4），没有第 21 处；最后一轮密文直接取 `sr ^ w`，第 1–9 轮 MixColumns 输入也直接取 `sr`，都没有再调 `sbox`。
8. 口算校验：`key=000102...0f`、`pt=00112233...eeff` → `ciphertext=69c4e0d86a7b0430d8cdb78070b4c55a`；第 1 轮 AddRoundKey 后应为 `89d810e8855ace682d1843d8cb128fe4`。

只输出最终 RTL 源码。
