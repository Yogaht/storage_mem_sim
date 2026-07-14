# MQSim CWDP 地址映射分析

---

## 一、什么是 CWDP

`Plane_Allocation_Scheme` 设为 `CWDP` 时，MQSim 使用 **Channel → Chip(Way) → Die → Plane** 的层级顺序在 NAND 阵列中分配物理页。

### 1.1 在 MQSim 内部的作用

CWDP 是 FTL（Flash Translation Layer）地址映射的核心策略之一，决定 LBA 如何映射到物理 NAND 位置。参考 MQSim C++ 源码 `Flash_Block_Manager.cpp` 中的 `CWDP` 分配逻辑：

- 每次分配一个新的物理页时，按 **Channel → Chip → Die → Plane** 顺序轮询
- 同一层级内递增到上限后进位到下一层级
- 所有 Plane 被均匀使用，最大化并行度

### 1.2 为什么是 CWDP 顺序

| 层级 | 并行特性 |
|---|---|
| **Channel** 优先 | 相邻 LBA 首先分布到不同通道 → 利用多通道并行带宽 |
| **Chip** 次之 | 同一通道内不同芯片可独立操作（chip-level parallelism） |
| **Die** 再次 | 同一芯片内不同 Die 可交错操作（die interleaving） |
| **Plane** 最后 | 同一 Die 内多 Plane 可同时执行相同命令（multi-plane operation） |

这个顺序确保了：连续读写时，相邻的 I/O 请求落在不同的物理单元上，可以并行执行。

---

## 二、默认配置下的地址映射公式

基于 `default_ssdconfig.xml`：

```
CHANNELS      = 8    (Flash_Channel_Count)
CHIPS_PER_CH  = 4    (Chip_No_Per_Channel)
DIES_PER_CHIP = 2    (Die_No_Per_Chip)
PLANES_PER_DIE = 2   (Plane_No_Per_Die)
PAGE_SIZE     = 8192 (Page_Capacity)
SECTORS_PER_PAGE = 16
```

**总 Plane 数** = 8 × 4 × 2 × 2 = **128**

### 映射公式

```
给定 LBA → page = LBA / SECTORS_PER_PAGE

channel = page % CHANNELS
chip    = (page / CHANNELS) % CHIPS_PER_CH
die     = (page / (CHANNELS × CHIPS_PER_CH)) % DIES_PER_CHIP
plane   = (page / (CHANNELS × CHIPS_PER_CH × DIES_PER_CHIP)) % PLANES_PER_DIE
```

### 映射规律

```
Page 0      → Ch0, Chip0, Die0, Plane0
Page 1      → Ch1, Chip0, Die0, Plane0    ← 先换 Channel
...
Page 7      → Ch7, Chip0, Die0, Plane0
Page 8      → Ch0, Chip1, Die0, Plane0    ← Channel 轮完一轮，换 Chip
...
Page 31     → Ch7, Chip3, Die0, Plane0    ← Chip 也轮完
Page 32     → Ch0, Chip0, Die1, Plane0    ← 换 Die
...
Page 63     → Ch7, Chip3, Die1, Plane0
Page 64     → Ch0, Chip0, Die0, Plane1    ← 换 Plane
...
Page 127    → Ch7, Chip3, Die1, Plane1    ← 全部 128 个 Plane 各占 1 页
Page 128    → Ch0, Chip0, Die0, Plane0    ← 新一轮循环
```

---

## 三、实例：addr=0, size=1024KB 的写入

### 3.1 基本参数

```
起始地址: addr = 0 → LBA = 0 → page = 0
数据量:   1024 KB = 128 pages × 8KB
页数:     128 页（恰好等于总 Plane 数）
```

### 3.2 逐页分布

128 页正好覆盖所有 128 个 Plane 各 1 次，每页 8KB。

| Page | LBA 范围 | Channel | Chip | Die | Plane |
|---|---|---|---|---|---|
| 0 | 0~15 | Ch0 | Chip0 | Die0 | Plane0 |
| 1 | 16~31 | Ch1 | Chip0 | Die0 | Plane0 |
| 2 | 32~47 | Ch2 | Chip0 | Die0 | Plane0 |
| 3 | 48~63 | Ch3 | Chip0 | Die0 | Plane0 |
| 4 | 64~79 | Ch4 | Chip0 | Die0 | Plane0 |
| 5 | 80~95 | Ch5 | Chip0 | Die0 | Plane0 |
| 6 | 96~111 | Ch6 | Chip0 | Die0 | Plane0 |
| 7 | 112~127 | Ch7 | Chip0 | Die0 | Plane0 |
| 8 | 128~143 | Ch0 | **Chip1** | Die0 | Plane0 |
| ... | ... | ... | ... | ... | ... |
| 31 | 496~511 | Ch7 | **Chip3** | Die0 | Plane0 |
| 32 | 512~527 | Ch0 | Chip0 | **Die1** | Plane0 |
| ... | ... | ... | ... | ... | ... |
| 63 | 1008~1023 | Ch7 | Chip3 | **Die1** | Plane0 |
| 64 | 1024~1039 | Ch0 | Chip0 | Die0 | **Plane1** |
| ... | ... | ... | ... | ... | ... |
| 127 | 2032~2047 | Ch7 | Chip3 | Die1 | **Plane1** |

### 3.3 可视化排布

```
Channel 0 ─┬─ Chip 0 ─┬─ Die 0 ─┬─ Plane 0: Page 0
           │           │         └─ Plane 1: Page 64
           │           └─ Die 1 ─┬─ Plane 0: Page 32
           │                     └─ Plane 1: Page 96
           ├─ Chip 1 ─┬─ Die 0 ─┬─ Plane 0: Page 8
           │           │         └─ Plane 1: Page 72
           │           └─ Die 1 ─┬─ Plane 0: Page 40
           │                     └─ Plane 1: Page 104
           ├─ Chip 2 ─┬─ ...
           └─ Chip 3 ─┬─ ...

Channel 1 ─┬─ Chip 0 ─┬─ Die 0 ─┬─ Plane 0: Page 1
           │           │         └─ Plane 1: Page 65
           │           └─ Die 1 ─┬─ Plane 0: Page 33
           │                     └─ Plane 1: Page 97
           ├─ ...
           
... (Ch2~Ch7 同理)
```

### 3.4 按通道汇总

| Channel | 页数 | 数据量 | 涉及的 Page 编号 |
|---|---|---|---|
| Ch0 | 16 | 128 KB | 0,8,16,24,32,40,48,56,64,72,80,88,96,104,112,120 |
| Ch1 | 16 | 128 KB | 1,9,17,25,33,41,49,57,65,73,81,89,97,105,113,121 |
| Ch2 | 16 | 128 KB | 2,10,18,26,34,42,50,58,66,74,82,90,98,106,114,122 |
| Ch3 | 16 | 128 KB | 3,11,19,27,35,43,51,59,67,75,83,91,99,107,115,123 |
| Ch4 | 16 | 128 KB | 4,12,20,28,36,44,52,60,68,76,84,92,100,108,116,124 |
| Ch5 | 16 | 128 KB | 5,13,21,29,37,45,53,61,69,77,85,93,101,109,117,125 |
| Ch6 | 16 | 128 KB | 6,14,22,30,38,46,54,62,70,78,86,94,102,110,118,126 |
| Ch7 | 16 | 128 KB | 7,15,23,31,39,47,55,63,71,79,87,95,103,111,119,127 |

8 个通道均匀分配，每个通道 128KB，总计 1024KB。

---

## 四、CWDP 对性能的影响

### 4.1 连续读写 — 最优情况

请求大小恰好使每通道分配的页数相同时，所有通道并行工作，达到峰值带宽。

```
size = N × CHANNELS × PAGE_SIZE   (N 为任意正整数)
     = N × 8 × 8KB = N × 64KB

例如: 64KB, 128KB, 256KB, 512KB, 1024KB 都完美对齐
```

1024KB = 128 pages = 128/8 = 16 pages/channel → 所有通道均等负载。

### 4.2 通道塌缩 — 最差情况

当请求步长（跨页数）与 CHANNELS 不互质时，连续请求可能只命中部分通道。这就是之前 `cwdp_stride_for_pages()` 要解决的问题（现已从 `build_trace_lines` 移除）。

### 4.3 小请求 vs 大请求

| 请求大小 | 跨越页数 | 涉及通道数 | 瓶颈 |
|---|---|---|---|
| 4KB (半页) | 0.5 页 | 1 | 单通道，nand tR |
| 8KB (1页) | 1 页 | 1 | 单通道 |
| 64KB (8页) | 8 页 | 8 | 全通道并行 |
| 128KB (16页) | 16 页 | 8 (每通道 2 页) | 通道带宽 |
| 1024KB (128页) | 128 页 | 8 (每通道 16 页) | 通道带宽 |

---

### 5.3 实例：trace 输入后的物理映射

**输入 trace**：

```
0 0 0 256 1       # LBA=0,    16页 → LPN 0..15
0 1 256 256 1     # LBA=256,  16页 → LPN 16..31
0 2 512 256 1     # LBA=512,  16页 → LPN 32..47
0 3 768 256 1     # LBA=768,  16页 → LPN 48..63
```

**Line 0 内部 16 页的 CWDP 分布**（LPN 0..15）：

```
LPN → (Ch, Chip, Die, Plane)
 0:  (Ch0, Chip0, Die0, Plane0)
 1:  (Ch1, Chip0, Die0, Plane0)
 2:  (Ch2, Chip0, Die0, Plane0)
 3:  (Ch3, Chip0, Die0, Plane0)
 4:  (Ch4, Chip0, Die0, Plane0)
 5:  (Ch5, Chip0, Die0, Plane0)
 6:  (Ch6, Chip0, Die0, Plane0)
 7:  (Ch7, Chip0, Die0, Plane0)
 8:  (Ch0, Chip1, Die0, Plane0)
 9:  (Ch1, Chip1, Die0, Plane0)
10:  (Ch2, Chip1, Die0, Plane0)
11:  (Ch3, Chip1, Die0, Plane0)
12:  (Ch4, Chip1, Die0, Plane0)
13:  (Ch5, Chip1, Die0, Plane0)
14:  (Ch6, Chip1, Die0, Plane0)
15:  (Ch7, Chip1, Die0, Plane0)
```

> 单条 128KB line 内部 16 页已经均匀分布在 8 个通道（每通道 2 页，分别在不同 Chip）。**CWDP 在单条大 I/O 内部自动实现了通道交错**。

**4 条 line 起始 LPN 的通道分布**：

```
Line 0 起始 LPN=0  → Ch(0%8)=0
Line 1 起始 LPN=16 → Ch(16%8)=0   ← 同 Ch0
Line 2 起始 LPN=32 → Ch(32%8)=0   ← 同 Ch0
Line 3 起始 LPN=48 → Ch(48%8)=0   ← 同 Ch0
```

> 四条 line 都从 Ch0 开始。因为 `gcd(16, 8) = 8 ≠ 1`，连续 line 的 LPN 步长 16 与通道数 8 不互质，起始页始终落在同一通道。

**这影响性能吗？**

不影响——因为 MQSim 处理 trace 是**串行的**：Line 0 执行完才执行 Line 1，不存在多 line 并行。每条 line 内部 16 页已经通过 CWDP 自动利用全部 8 通道并行读取，所以单条 128KB 请求本身就能达到峰值带宽。通道塌缩只在**多次独立 I/O 之间存在资源竞争**时才会表现出来（如多流并发场景）。

### 5.4 物理地址总结

| Trace Line | LBA 范围 | LPN 范围 | 起始物理位置 | 涉及通道 |
|---|---|---|---|---|
| 0 | 0..255 | 0..15 | (Ch0,Chip0,Die0,Plane0) | 全部 8 通道 |
| 1 | 256..511 | 16..31 | (Ch0,Chip2,Die0,Plane0) | 全部 8 通道 |
| 2 | 512..767 | 32..47 | (Ch0,Chip0,Die1,Plane0) | 全部 8 通道 |
| 3 | 768..1023 | 48..63 | (Ch0,Chip2,Die1,Plane0) | 全部 8 通道 |

**关键结论**：trace 中的 LBA 不等于物理地址。MQSim 通过 CWDP 将连续 LPN 自动交错过 8 个通道，单条 ≥8 页的 I/O 即可利用全部通道带宽。

---

## 六、与 trace.py 的关系

`trace.py` 本身不参与 CWDP 映射——它只负责将 MemoryRequest 的地址写入 trace 文件。MQSim 在仿真时根据 `Plane_Allocation_Scheme` 配置自行完成 LBA → 物理位置的映射。

`load_from_ssdconfig_xml()` 中读取的 `CHANNELS`, `CHIPS_PER_CH`, `DIES_PER_CHIP`, `PLANES_PER_DIE`, `PAGE_SIZE_BYTES` 等几何参数，可用于：
- 理论公式 (`theory_iops`, `theory_bandwidth_mbps`, `theory_bus_utilization`) 的性能估算
- 地址对齐和页边界计算 (`align_lba`, `SECTORS_PER_PAGE`)
