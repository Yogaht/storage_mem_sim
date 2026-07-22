# MQSim Trace 生成说明

> 文件：`media/mqsim_wrapper/pymqsim/trace.py`

---

## 一、整体调用链

```
configs/mqsim.json  →  run.py  →  MemoryEngine.issue_request()
                                       │
                                       ▼
                              MQSimMediaSystem.__init__()
                               │
                               ├── _init_mqsim()
                               │    ├── load_from_ssdconfig_xml()    ← 解析 ssdconfig.xml
                               │    │    设置 CHANNELS, PAGE_SIZE_BYTES, … 模块全局变量
                               │    └── load_from_workload_xml()    ← 解析 workload.xml
                               │
                               └── handler_mem_request(mem_req_list)
                                    │
                                    ├── ① write_trace_file()
                                    │    └── build_trace_lines()
                                    │         ├── merge_sequential()   ← 合并连续同类请求
                                    │         └── 切片 + sector对齐 + CWDP重分布
                                    │
                                    ├── ② generate_workload_xml()     ← 替换模板中的 trace 路径
                                    └── ③ run_simulation()            ← 调用 MQSim 原生模块
```

---

## 二、初始化 — NAND 几何参数加载

**所有 NAND 几何参数初始值均为 `None`，必须先从 `ssdconfig.xml` 加载。**

### 2.1 `load_from_ssdconfig_xml(xml_path)`

从 MQSim 的 `ssdconfig.xml` 中读取并设置以下模块全局变量：

```
ssdconfig.xml 标签                         → 模块变量
─────────────────────────────────────────────────────────────
Device_Parameter_Set/Flash_Channel_Count   → CHANNELS
Device_Parameter_Set/Chip_No_Per_Channel   → CHIPS_PER_CH
Device_Parameter_Set/Channel_Transfer_Rate → CHANNEL_BW_MBPS
Flash_Parameter_Set/Die_No_Per_Chip        → DIES_PER_CHIP
Flash_Parameter_Set/Plane_No_Per_Die       → PLANES_PER_DIE
Flash_Parameter_Set/Page_No_Per_Block      → PAGES_PER_BLOCK
Flash_Parameter_Set/Page_Capacity          → PAGE_SIZE_BYTES
Flash_Parameter_Set/Page_Read_Latency_LSB  → NAND_tR_NS
```

**协议参数**从 `<Flash_Comm_Protocol>` 查表获得：

| 协议 | CMD_TRANSFER_NS | DATA_SETUP_NS |
|---|---|---|
| NVDDR2 | 290 | 30 |
| NVDDR3 | 200 | 20 |

**固定常量**：`SECTOR_SIZE = 512`（MQSim 内部始终为 512 字节/扇区）。

### 2.2 派生计算

加载完成后调用 `_recompute_derived()` 自动计算：

```
SECTORS_PER_PAGE      = PAGE_SIZE_BYTES / SECTOR_SIZE       (默认: 16)
TOTAL_PLANES          = CHANNELS × CHIPS_PER_CH × DIES_PER_CHIP × PLANES_PER_DIE  (默认: 128)
TOTAL_CHANNEL_BW_MBPS = CHANNELS × CHANNEL_BW_MBPS          (默认: 2664)
```

### 2.3 `_loaded` 防护机制

依赖几何参数的函数（`theory_iops` 等）以 `_require_loaded()` 开头。加载前调用会抛出 `RuntimeError`。

---

## 三、Trace 生成三步管线

**入口**：`write_trace_file(mem_req_list, output_path, cfg)`

### 步骤 1：`merge_sequential()` — 合并连续同类请求

```
输入: [Read(0,4K), Read(4K,4K), Write(8K,4K), Read(16K,8K)]

1. 按类型分组: reads=[(0,4K),(4K,4K),(16K,8K)], writes=[(8K,4K)]
2. 每组按地址升序排列
3. 合并地址连续的请求:
   (0,4K) + (4K,4K) = (0,8K)      ← 连续,合并
   (0,8K) ≠ (16K,8K)              ← 有间隙,不合并
   → reads 输出: [(0,8K), (16K,8K)]
   → writes 输出: [(8K,4K)]
4. reads 在前, writes 在后, 返回

输出: addr=[0, 16K, 8K], size=[8K, 8K, 4K], type=[1(读), 1(读), 0(写)]
```

**规则**：
- 不同类型（读/写）**永不合并**，即使地址连续
- 有地址间隙**永不合并**
- 所有 reads 排在 writes 之前

### 步骤 2：`build_trace_lines()` — 切片 + sector 对齐 + page-first traversal

对步骤 1 产生的每个 chunk `(base_addr, total_size, rtype)`：

```
line_size = min(total_size, cfg.request_size)

每条 trace line 对齐到 512B sector 边界（保留页内扇区偏移）。

★ page-first traversal（仅在不合并 + 子页请求时触发）：
  当 merge_contiguous=False 且所有 chunk 单行（n_lines=1）且
  request_size < PAGE_SIZE_BYTES 时，使用 page-first 遍历：
  
  lines_per_page = PAGE_SIZE_BYTES // request_size
  for off in range(lines_per_page):
      for pg in range(total_pages):
          放置 chunk 在 page=pg, offset=off

  效果：连续 trace line 在不同 page → 不同 LPA → MQSim CWDP
  自动分配到不同 Channel，避免多个请求落在同一通道。
```

**page-first 重分布示例**：4096 个 4KB 请求 (PAGE=16KB, CH=16)

```
顺序输入: page 0 offset 0,4K,8K,12K → 4 请求全在同一 LPA → 全在 Ch 0
page-first: pg 0 off 0, pg 1 off 0, ..., pg 15 off 0 → 16 LPAs → 16 Chs
            pg 0 off 4K, pg 1 off 4K, ... → 复用 pages

Trace 输出: LBA 0, 32, 64, ..., 480, 8, 40, ...
  连续行步长 = 32 sectors = 16KB = 1 page
```

### 步骤 3：`write_trace_file()` — 写入磁盘

#### 格式

每行 5 个空格分隔的字段：

```
<arrival_ns> <device_id> <lba> <sectors> <req_type>
```

| 字段 | 含义 | 计算方式 |
|---|---|---|
| `arrival_ns` | 到达时间 | 固定为 `0`（MemoryEngine 无时序概念） |
| `device_id` | 设备队列号 | `i % CHANNELS`（简单轮转），MQSim 内部 CWDP 从 LBA 决定物理通道 |
| `lba` | 逻辑块地址 | `addr / SECTOR_SIZE` |
| `sectors` | 扇区数 | `ceil(size / SECTOR_SIZE)` |
| `req_type` | 操作类型 | `1` = 读, `0` = 写 |

device_id 使用简单轮转 `i % CHANNELS`，确保连续 trace 行进入不同设备队列。
物理通道由 MQSim 内部 CWDP 分配器根据 LPA 决定（`LPA % CHANNELS`），trace 层不重复此逻辑。

> MQSim C++ 源码中 `ASCIITraceDeviceColumn`（列 1）已定义但**从未被任何 .cpp 文件读取**。
> device_id 仅用于 trace 文件格式兼容，不影响物理路由。


#### 示例输出

16 条 4KB 读请求，merge_contiguous=False, CH=16, PAGE=16KB：

```
0 0 0 8 1       ← dev 0, LBA 0,   Ch 0 (LPA 0)
0 1 32 8 1      ← dev 1, LBA 32,  Ch 1 (LPA 1)
0 2 64 8 1      ← dev 2, LBA 64,  Ch 2 (LPA 2)
...
0 15 480 8 1    ← dev 15, LBA 480, Ch 15 (LPA 15)
0 0 8 8 1       ← dev 0, LBA 8,   Ch 0 (LPA 0, 第二轮 offset=4KB)
```

连续行 LBA 步长 = 1 page (32 sectors = 16KB)，每个请求不同 LPA → 不同通道。

---

## 四、完整示例推演

**输入**：`run.py -c configs/mqsim.json --num-requests 4 --size 131072`

```
4 条 MemoryRequest:
  Read(addr=0,       128KB)
  Read(addr=131072,  128KB)
  Read(addr=262144,  128KB)
  Read(addr=393216,  128KB)

配置: merge_contiguous=True, request_size=131072
```

**步骤 1 — merge_sequential()**：

```
4 条 Read, 地址连续:
  0 + 131072 = 131072  →  131072 + 131072 = 262144  →  262144 + 131072 = 393216
  → 合并为 1 个 chunk: (base_addr=0, total_size=524288, type=1)
```

**步骤 2 — build_trace_lines()** (顺序切片)：

```
line_size = min(524288, 131072) = 131072

offset=0:       addr=0       → sector对齐=0
offset=131072:  addr=131072  → sector对齐=131072
offset=262144:  addr=262144  → sector对齐=262144
offset=393216:  addr=393216  → sector对齐=393216
```

**步骤 3 — write_trace_file()**：

```
addr → lba(÷512) → device_id = i % 16
0       → 0    → dev 0
131072  → 256  → dev 1
262144  → 512  → dev 2
393216  → 768  → dev 3

输出:
0 0 0 256 1
0 1 256 256 1
0 2 512 256 1
0 3 768 256 1
```

> 合并后的 4 条 128KB line，每条内部 8 个 page 已通过 MQSim CWDP
> 自动跨 16 个通道（128KB = 8 pages，LPA 0-7 → Ch 0-7）。
> 单条大 I/O 内部已实现通道并行，外部 device_id 轮转是额外保障。

---

## 五、关键设计决策

### 5.1 trace 地址修改策略

trace 层**不感知 CWDP 物理布局**。MQSim C++ 内部通过 `LPA % CHANNELS` 完成通道分配，trace 只需保证连续行 LPA 不聚簇：

- **合并模式** (`merge_contiguous=True`): 顺序切片，地址连续递增。适合大 I/O 带宽测试。单条大 I/O 内部的多 page 已通过 CWDP 自动跨通道
- **不合并 + 子页** (`merge_contiguous=False`, `request_size < PAGE_SIZE`): page-first traversal，连续请求放在不同 page → 不同 LPA → MQSim CWDP 自动分配到不同通道。适合 IOPS 测试
- 所有路径**保留 sector 对齐**（512B），不影响子页扇区位图计算

### 5.2 为什么合并不做类型交叉？

读写混合时，合并只在同类型内进行（reads 合并 reads，writes 合并 writes）。因为读写的 MQSim trace 操作码不同（1 vs 0），MQSim 将它们视为不同的 I/O 流。

### 5.3 SECTOR_SIZE 为什么是固定 512？

SECTOR_SIZE = 512 在 MQSim C++ 源码中硬编码，所有逻辑地址和扇区计数均以此为基准。它不是可配置参数。

---

## 六、TraceSliceConfig 配置说明

```python
@dataclass
class TraceSliceConfig:
    merge_contiguous: bool = True   # 是否先合并连续同类请求
    request_size: int = 131072      # 每条 trace line 最大字节数
```

| 参数 | 效果 |
|---|---|
| `merge_contiguous=True` + 大 request_size | 合并 → 少量大 I/O → 测峰值带宽 |
| `merge_contiguous=False` + 小 request_size | 不合并 + page-first traversal → IOPS 测试，自动 LPA 分集 |

> `merge_contiguous` 已作为 `MediaConfig` 字段，由 `MQSimMediaSystem._init_mqsim()` 自动注入 `TraceSliceConfig`。普通用户无需手动创建。

---

## 七、理论公式

`trace.py` 同时提供三个无需运行仿真的理论预估函数，用于快速性能评估：

| 函数 | 公式 | 判断标准 |
|---|---|---|
| `theory_iops(S)` | `TOTAL_PLANES × 1e9 / (tR + CHANNELS × BusTime)` | — |
| `theory_bandwidth_mbps(S)` | `IOPS × S / 1e6` | — |
| `theory_bus_utilization(S)` | `variable_cost / (fixed_cost + variable_cost)` | <0.5 IOPS-Bound, >0.9 带宽-Bound |

详见 `run_experiment.py` 的理论公式推导。

---

# 附录：Trace 行如何映射到物理扇区地址

> 当你拿到一条已生成的 trace 行（如 `0 0 0 256 1`），MQSim 是如何确定它最终访问的是哪个物理闪存芯片的哪个扇区的？
> 以下从 MQSim C++ 源码出发，详细分析完整映射链路。

---

## A.1 Trace 各列在地址翻译中的角色

回顾 trace 格式：

```
<Time> <Device> <Address(LBA)> <Size(sectors)> <Type>
  0       0           0             256          1
```

其中只有 **Address** 和 **Size** 两列参与物理地址翻译：

- **Address** = 起始 LBA（逻辑块地址），以 **512B sector** 为单位
- **Size** = 请求长度，以 **sector** 为单位
- 因此实际访问的 LBA 范围为 `[Address, Address + Size - 1]`

Device 列（`i % CHANNELS` 轮转）在 MQSim C++ 源码中**定义但未被读取**（`ASCIITraceDeviceColumn` 无引用）。物理通道由 CWDP 分配器根据 LPA 决定。Time 列决定请求的时序调度。

---

## A.2 完整映射链（4 个阶段）

```
Trace 文件中的 Address (LBA/sector)
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│ 阶段1: LBA → LPA (逻辑页地址)                                  │
│   LPA = LBA / sectors_per_page                                │
│   页内扇区偏移 = LBA % sectors_per_page                        │
│                                                                │
│   源码: FTL::Convert_host_logical_address_to_device_address()  │
│   FTL.cpp:899-901                                              │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│ 阶段2: LPA → (Channel, Chip, Die, Plane)                      │
│   根据 Flash_Plane_Allocation_Scheme (共 24 种) 按轮转方式分配 │
│                                                                │
│   源码: Address_Mapping_Unit_Page_Level::                      │
│            allocate_plane_for_user_write()                    │
│   Address_Mapping_Unit_Page_Level.cpp:987-1143                 │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│ 阶段3: Plane 内分配 (Block, Page)                              │
│   写前沿海 (Write Frontier) 在每个 Plane 内独立维护            │
│   BlockID = Data_wf[stream]->BlockID                           │
│   PageID  = Data_wf[stream]->Current_page_write_index++        │
│                                                                │
│   源码: Flash_Block_Manager::                                  │
│            Allocate_block_and_page_in_plane_for_user_write()   │
│   Flash_Block_Manager.cpp:20-37                                │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│ 阶段4: 物理地址 → PPA (Physical Page Address 编号)            │
│   PPA = page_no_per_chip × (ChID × chip_no_per_channel +      │
│             ChipID)                                            │
│        + page_no_per_die × DieID                               │
│        + page_no_per_plane × PlaneID                           │
│        + pages_no_per_block × BlockID                          │
│        + PageID                                                │
│                                                                │
│   源码: Address_Mapping_Unit_Page_Level::                      │
│            Convert_address_to_ppa()                            │
│   Address_Mapping_Unit_Page_Level.cpp:1450-1454                │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
    最终物理扇区 = 该物理页内的扇区偏移 (LBA % 16)
```

---

## A.3 关键参数

所有参数从 `ssdconfig.xml` 加载（默认值与原 trace 保持一致）：

| 参数 | 符号 | 默认值 | 来源 |
|------|------|--------|------|
| Page_Capacity | — | 8192 bytes | Flash_Parameter_Set |
| SECTOR_SIZE_IN_BYTE | — | 512 (硬编码) | SSD_Defs.h |
| `sectors_per_page` | — | **16** (= 8192/512) | 计算得出 |
| Flash_Channel_Count | `channel_count` | 8 | Device_Parameter_Set |
| Chip_No_Per_Channel | `chip_no_per_channel` | 4 | Device_Parameter_Set |
| Die_No_Per_Chip | `die_no_per_chip` | 2 | Flash_Parameter_Set |
| Plane_No_Per_Die | `plane_no_per_die` | 2 | Flash_Parameter_Set |
| Block_No_Per_Plane | `block_no_per_plane` | 2048 | Flash_Parameter_Set |
| Page_No_Per_Block | `page_no_per_block` | 256 | Flash_Parameter_Set |

---

## A.4 阶段1 详解：LBA → LPA

**核心代码**（`FTL.cpp:899-901`）：

```cpp
LPA_type FTL::Convert_host_logical_address_to_device_address(LHA_type lha)
{
    return lha / page_size_in_sectors;  // = lha / 16
}
```

**含义**：每 16 个连续扇区 (LBA) 属于同一个逻辑页 (LPA)。LBA 对 16 取余的结果就是**页内扇区偏移**（0~15）。

**具体例子**：

| LBA | LPA | 页内偏移 | 说明 |
|-----|-----|---------|------|
| 0 | 0 | 0 | 第 0 页的第 0 个 512B 扇区 |
| 1 | 0 | 1 | 第 0 页的第 1 个 512B 扇区 |
| 15 | 0 | 15 | 第 0 页的最后一个扇区 |
| **16** | **1** | 0 | **第 1 页**的第 0 个扇区 |
| 255 | 15 | 15 | 第 15 页的最后一个扇区 |
| 256 | 16 | 0 | 第 16 页的第 0 个扇区 |

对于 trace 行 `0 0 0 256 1`（LBA=0, Size=256 sectors, Type=READ）：
- 访问范围：LBA 0 ~ 255，共 256 个扇区 = 128KB
- 这 256 个扇区属于 LPA 0 到 LPA 15（256/16 = 16 个逻辑页）

---

## A.5 阶段2 详解：LPA → (Channel, Chip, Die, Plane)

MQSim 通过 **Flash_Plane_Allocation_Scheme**（共 24 种，四字母排列）将 LPA 映射到具体的 Plane。

### 默认方案 CWDP = Channel → Way(Chip) → Die → Plane

**代码**（`trace_physical_mapping.py` 复刻，与 MQSim 源码一致）：

```python
ch = channel_ids[lpa % ch_no]                                    # 先轮转 Channel
cp = chip_ids[(lpa // ch_no) % cp_no]                             # 再轮转 Chip
d  = die_ids[(lpa // (cp_no * ch_no)) % d_no]                     # 再轮转 Die
p  = plane_ids[(lpa // (d_no * cp_no * ch_no)) % p_no]            # 最后轮转 Plane
```

以默认配置 8Ch × 4Chip × 2Die × 2Plane 代入：
- `ch_no = 8`, `cp_no = 4`, `d_no = 2`, `p_no = 2`
- 一个完整循环 = 8 × 4 × 2 × 2 = **128 个 LPA**

**逐页推演**：

| LPA | Ch | Chip | Die | Plane | 变化说明 |
|-----|-----|------|-----|-------|---------|
| 0 | 0 | 0 | 0 | 0 | 起点 |
| 1 | 1 | 0 | 0 | 0 | ↑Channel |
| 2 | 2 | 0 | 0 | 0 | |
| … | … | | | | |
| 7 | 7 | 0 | 0 | 0 | Channel 轮完一圈 |
| 8 | 0 | **1** | 0 | 0 | ↑Chip |
| … | … | | | | |
| 31 | 7 | 3 | 0 | 0 | |
| 32 | 0 | 0 | **1** | 0 | ↑Die |
| … | … | | | | |
| 63 | 7 | 3 | 1 | 0 | |
| 64 | 0 | 0 | 0 | **1** | ↑Plane |
| … | … | | | | |
| 127 | 7 | 3 | 1 | 1 | 完整循环尾 |
| 128 | 0 | 0 | 0 | 0 | 回到起点 |

### 24 种分配方案的含义

| 缩写 | 全称 | 分配优先级 |
|------|------|-----------|
| C | Channel | 通道 |
| W | Way (Chip) | 芯片/路 |
| D | Die | 晶粒 |
| P | Plane | 平面 |

- **CWDP**：先跨 Channel → 再跨 Chip → 再跨 Die → 最后跨 Plane
- **WCDP**：先跨 Chip → 再跨 Channel → 再跨 Die → 最后跨 Plane
- 以此类推，共 4! = 24 种排列

> **设计意图**：CWDP 让地址空间中相邻的 LPA 优先分散到不同 Channel，最大化读写的并行度。

---

## A.6 阶段3 详解：Plane 内 Block/Page 分配 — 写前沿海

每个 Plane 独立维护一个**写前沿海（Write Frontier）**。初始状态：Block=0, Page=0。

每次在该 Plane 分配一个新页时：
- 使用当前写前沿海位置的 (Block, Page)
- PageID 自增 1
- 若 PageID 达到 `pages_no_per_block`（默认 256），Block 递增，Page 重置为 0

**示例**：Plane (Ch=0, Chip=0, Die=0, Plane=0) 的写前沿海演进

```
初始: Block=0, Page=0
第1次分配 → (Block=0, Page=0),  推进到 Page=1
第2次分配 → (Block=0, Page=1),  推进到 Page=2
...
第256次分配 → (Block=0, Page=255), 推进到 Block=1, Page=0
第257次分配 → (Block=1, Page=0),  推进到 Page=1
```

对于 trace 行 `0 0 0 256 1`（16 个 LPA 按 CWDP 分散到 16 个不同 Plane）：
- 每个 Plane 仅分配 1 次，因此全部位于各自的 Block=0, Page=0

---

## A.7 阶段4 详解：物理坐标 → PPA 编号

PPA 是一个连续整数编号，将 6 维物理地址压缩为 1 维（`Address_Mapping_Unit_Page_Level.cpp:1450-1454`）：

```cpp
PPA = page_no_per_chip × (ChannelID × chip_no_per_channel + ChipID)
    + page_no_per_die × DieID
    + page_no_per_plane × PlaneID
    + pages_no_per_block × BlockID
    + PageID
```

以默认配置代入：
- `page_no_per_chip`   = 256 × 2048 × 2 = 1,048,576
- `page_no_per_die`    = 256 × 2048 × 2 = 1,048,576
- `page_no_per_plane`  = 256 × 2048 = 524,288
- `pages_no_per_block` = 256

PPA 的值域：0 ~ 8×4×2×2×2048×256 - 1 = 0 ~ 67,108,863（约 67M 个物理页 = 512GB）

---

## A.8 完整示例推演

以 `mqsim_trace_1.txt` 前 17 行为例（均为 Size=1 sector 的 READ）：

```
0 0 0 1 1     → LBA=0,  1 sector
0 1 0 1 1     → LBA=0,  1 sector  (与第 1 行 LBA 相同，但不同 Device)
0 2 0 1 1     → LBA=0,  1 sector
...
0 0 16 1 1    → LBA=16, 1 sector  (第 17 行)
```

### 第 1 行：`0 0 0 1 1`

| 阶段 | 输入 | 输出 |
|------|------|------|
| LBA→LPA | LBA=0 | LPA=0, 页内偏移=0 |
| LPA→Plane (CWDP) | LPA=0 | Ch=0, Chip=0, Die=0, Plane=0 |
| 写前沿海 | Plane(0,0,0,0) 第1次 | Block=0, Page=0 |
| →PPA | | PPA = 1,048,576×(0×4+0) + 1,048,576×0 + 524,288×0 + 256×0 + 0 = **0** |
| **最终物理扇区** | | **Ch=0, Chip=0, Die=0, Plane=0, Block=0, Page=0, 扇区偏移=0** |

### 第 17 行：`0 0 16 1 1`

| 阶段 | 输入 | 输出 |
|------|------|------|
| LBA→LPA | LBA=16 | LPA=1, 页内偏移=0 |
| LPA→Plane (CWDP) | LPA=1 | Ch=1, Chip=0, Die=0, Plane=0 |
| 写前沿海 | Plane(1,0,0,0) 第1次 | Block=0, Page=0 |
| →PPA | | PPA = 1,048,576×(1×4+0) + 0 + 0 + 0 + 0 = **4,194,304** |
| **最终物理扇区** | | **Ch=1, Chip=0, Die=0, Plane=0, Block=0, Page=0, 扇区偏移=0** |

### 跨页请求：`0 0 14 4 1`（假想的示例行）

此请求要读取 4 个扇区：LBA 14, 15, 16, 17

| LBA | LPA | 页内偏移 | 物理位置 |
|-----|-----|---------|---------|
| 14 | 0 | 14 | Plane(0,0,0,0), Block=0, Page=0, 扇区偏移=14 |
| 15 | 0 | 15 | Plane(0,0,0,0), Block=0, Page=0, 扇区偏移=15 |
| 16 | 1 | 0 | Plane(1,0,0,0), Block=0, Page=0, 扇区偏移=0 |
| 17 | 1 | 1 | Plane(1,0,0,0), Block=0, Page=0, 扇区偏移=1 |

> 一个 trace 请求如果 size > 1，可能跨越多个物理页甚至多个 Channel。MQSim 会按扇区粒度拆分为多个子请求，各自路由到对应的物理位置。

---

## A.9 子页访问位图（Sub-page Access Bitmap）

对于每个 LBA，MQSim 调用 `FTL::Find_NVM_subunit_access_bitmap()` 来确定该页内哪些扇区被访问：

```cpp
page_status_type FTL::Find_NVM_subunit_access_bitmap(LHA_type lha)
{
    return ((page_status_type)~(0xffffffffffffffff << (int)1))
           << (int)(lha % page_size_in_sectors);
}
```

这生成一个位图：LBA 对应页内偏移处的 bit 为 1，其余为 0。多条连续的 LBA 的位图做 OR 运算，最终得到每个物理页的扇区访问位图。这对于 GC（垃圾回收）时判断页内有效数据非常重要。

---

## A.10 读与写的映射差异

| 特性 | 读请求 (Type=1) | 写请求 (Type=0) |
|------|----------------|----------------|
| LPA 已访问过 | 复用已有 PPA（映射表查找） | 总是分配新 PPA（out-of-place 写入） |
| LPA 首次访问 | 分配新 PPA（模拟预填充） | 分配新 PPA |
| 对写前沿海的影响 | 不推进 | 推进（消耗一个 Page） |

在 `trace_physical_mapping.py` 中，`map_entry()` 复刻了此逻辑：

```python
if entry.req_type == "READ" and not is_first_access:
    phys = self.lpa_to_physical[lpa]  # 复用已有映射
    ppa = self.lpa_to_ppa[lpa]
else:
    ch, cp, d, p = self.allocator.allocate(lpa, scheme)
    blk, pg = self.wf_sim.allocate(ch, cp, d, p)  # 新分配
    phys = PhysicalAddress(...)
```

---

## A.11 总结

| 问题 | 答案 |
|------|------|
| trace 中的 Address 是什么？ | **起始 LBA**，以 512B sector 为单位 |
| 如何知道访问哪个具体扇区？ | LBA → LPA（÷16）→ (Ch,Chip,Die,Plane)（CWDP 轮转）→ (Block,Page)（写前沿海）→ **页内扇区偏移 = LBA % 16** |
| 为什么除以 16？ | 一个闪存页 8192B，一个扇区 512B，每页恰好 16 个扇区 |
| Size 列的作用？ | 表示要访问多少个连续扇区，可能跨多个 LPA / 物理页 |
| Device 列的作用？ | 仅用于 NVMe 多队列调度（`device_id % 16`），不影响物理地址映射 |
| Time 列的作用？ | 仅用于时序调度，不影响物理地址映射 |
