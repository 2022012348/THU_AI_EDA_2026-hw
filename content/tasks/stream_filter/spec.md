# Agent 复杂任务：带校验和的同步字节流缓冲器

实现一个多模块 SystemVerilog 项目：可参数化深度的同步 FIFO + 16-bit 递增校验和 + 顶层 `stream_filter`。FIFO 深度由 DSE 选择，但必须支持 16 字节连续突发。项目中必须存在下面三个模块，并放在指定文件中。

## 顶层接口（必须完全一致）

```systemverilog
module stream_filter (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       in_valid,
    output logic       in_ready,
    input  logic [7:0] in_data,
    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_data,
    output logic [15:0] checksum,
    output logic [4:0]  count
);
```

## 功能与握手

1. 数据按字节进入 FIFO，按接收顺序从 `out_data` 输出。
2. `out_valid=1` 表示 FIFO 非空；`out_data` 是第一字直通（first-word fall-through）的队头字节。`out_valid && out_ready` 在上升沿完成一次读取，下一拍输出下一字节；FIFO 为空时 `out_valid=0`，`out_data` 不要求有效。
3. 允许同一上升沿同时读写（FIFO 未满且非空时），读写各自独立计数，不得丢失、重复或重排数据。
4. `checksum` 是已接收字节的 16-bit 累加和（模 65536）：每接收一个 `in_data`，在接收的上升沿更新 `checksum <= checksum + in_data`；复位后 `checksum=0`；在上升沿后采样。
5. `count` 是 FIFO 当前占用量，范围 0..DEPTH；本任务要求至少能容纳 16 字节连续突发，DSE 选定深度后 `full` 对应 `count==DEPTH`，空时为 0。
6. `rst_n` 是同步低有效复位：在 `clk` 上升沿采样到 `rst_n=0` 时清空 FIFO、`checksum` 和 `count`，并令 `out_valid=0`、`in_ready=1`。`rst_n` 在两个上升沿之间变化时，寄存器输出保持不变；禁止异步复位。
7. 时钟按 10 ns 周期驱动；FIFO 必须支持 16 字节连续突发。DSE 在 16/32 深度中选择满足突发且面积/功耗更小的方案；数据位宽固定为 8，`count` 为 5 bit。

## 必须实现的模块与文件

除顶层 `stream_filter` 外，项目中必须包含以下内部模块，并分别放在指定文件：

### `rtl/sync_fifo.sv`

```systemverilog
module sync_fifo #(
    parameter DEPTH = 16,
    parameter WIDTH = 8
) (
    input  logic             clk,
    input  logic             rst_n,
    input  logic             wr_en,
    input  logic             rd_en,
    input  logic [WIDTH-1:0] din,
    output logic [WIDTH-1:0] dout,
    output logic             full,
    output logic             empty,
    output logic [4:0]       count
);
```

- `dout` 必须是 first-word fall-through：`empty=0` 时直接给出当前队头数据。
- `full = (count == DEPTH)`，`empty = (count == 0)`，`count` 为占用字节数；DEPTH 由 DSE 在 16/32 中选出，必须至少支持 16 字节突发。
- 同步低有效复位；`wr_en`/`rd_en` 在同一拍同时有效时要正确处理。
- 该模块接口必须保持固定，不能改名或省略。

### `rtl/checksum_acc.sv`

```systemverilog
module checksum_acc (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        valid,
    input  logic [7:0]  data,
    output logic [15:0] checksum
);
```

- `valid=1` 的上升沿执行 `checksum <= checksum + data`；同步低有效复位后为 0。

### `rtl/stream_filter.sv`

- 顶层 `stream_filter`，例化 `sync_fifo` 与 `checksum_acc`，连接上面定义的端口与功能。
- 不得修改文件名、模块名或端口；不得使用外部文件、`include`、`$readmemh` 或 DPI。

## DSE 要求

本任务不是固定深度实现；Planner 必须先完成一个可解释的小型 DSE，并在计划的 `architecture` 中冻结结果：

- **DSE-1 深度**：必须支持 16 字节连续写突发。候选深度为 16 或 32；选择满足突发的最小深度，避免无收益的额外存储。
- **DSE-2 FIFO 存储**：候选为 8 级/16 级移位寄存器链与寄存器阵列；选择只写目标项、动态活动更低的寄存器阵列。
- **DSE-3 指针/计数**：候选为双指针 + count、单写指针 + count、双指针 + full flag；在保证同拍读写和 full/empty 正确的前提下选择触发器更少、计数逻辑更简单的方案。
- **DSE-4 校验和**：只保留一个 16-bit 累加寄存器和一个 16-bit 加法器，不增加流水级。
- **DSE-5 顶层**：直接连线，不使用额外输出寄存或缓冲。

### 16 字节突发语义

- 在 `out_ready=0` 时允许连续写入 16 个字节并被 FIFO 完整保存；
- 教师 TB 以 16 字节突发验收，因此 DSE 应选择最小满足需求的深度 16；第 16 个字节写入后 `full` 必须有效，`count` 必须等于 16；
- 之后必须能按接收顺序读完这 16 个字节，`checksum` 与实际接收字节一致。

## 实现约束

- 只使用可综合的 SystemVerilog 子集（`logic`、`always_ff`、`always_comb`、`localparam`、function 等）；函数内不要使用 `return`（Yosys 前端不支持）。
- 面积/功耗评估会综合所有项目 RTL 并用固定功耗脚本生成活动；不要使用存储阵列以外的外部 IP。
