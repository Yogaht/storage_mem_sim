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
                                    │         └── 切片 + 页对齐        ← 按 request_size 切片
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

### 步骤 2：`build_trace_lines()` — 切片 + 页对齐

对步骤 1 产生的每个 chunk `(base_addr, total_size, rtype)`：

```
line_size = min(total_size, cfg.request_size)

从 base_addr 开始，按 line_size 步进切片，每片页对齐后输出。
trace 忠实记录 MemoryRequest 的地址，不做任何重新分配或步长修正。
```

**例子**：1 个合并后的 chunk `(addr=4096, total_size=24576, type=1)`，request_size=8192

```
offset=0:     addr=4096  → 页对齐到 0,    size=8192
offset=8192:  addr=12288 → 页对齐到 8192, size=8192
offset=16384: addr=20480 → 页对齐到 16384, size=8192

输出 3 条 trace line: (addr=0,8192), (addr=8192,8192), (addr=16384,8192)
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
| `device_id` | 设备号 | `i % 16`，0..15 循环，适配 MQSim 多队列 |
| `lba` | 逻辑块地址 | `addr / SECTOR_SIZE` |
| `sectors` | 扇区数 | `ceil(size / SECTOR_SIZE)` |
| `req_type` | 操作类型 | `1` = 读, `0` = 写 |

#### 示例输出

8 条 128KB 读请求，merge_contiguous=True：

```
0 0 0 256 1       # addr=0,      LBA=0,    256扇区(128KB), 读
0 1 256 256 1     # addr=131072, LBA=256
0 2 512 256 1     # addr=262144, LBA=512
0 3 768 256 1     # addr=393216, LBA=768
0 4 1024 256 1    # addr=524288, LBA=1024
0 5 1280 256 1    # addr=655360, LBA=1280
0 6 1536 256 1    # addr=786432, LBA=1536
0 7 1792 256 1    # addr=917504, LBA=1792
```

LBA 连续递增（0, 256, 512, …），每行间隔 256 扇区 = 16 页 = 128KB，与 MemoryEngine 发出的地址一一对应。

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

**步骤 2 — build_trace_lines()**：

```
line_size = min(524288, 131072) = 131072

offset=0:       addr=0       → 页对齐=0
offset=131072:  addr=131072  → 页对齐=131072
offset=262144:  addr=262144  → 页对齐=262144
offset=393216:  addr=393216  → 页对齐=393216
```

**步骤 3 — write_trace_file()**：

```
addr → lba(÷512) → sectors(÷512)
0       → 0    → 256
131072  → 256  → 256
262144  → 512  → 256
393216  → 768  → 256

输出:
0 0 0 256 1
0 1 256 256 1
0 2 512 256 1
0 3 768 256 1
```

---

## 五、关键设计决策

### 5.1 trace 不修改地址

`build_trace_lines` 忠实记录 MemoryEngine 传来的地址，只做合并、切片、页对齐——不做 CWDP 步长修正或多轮遍历等地址重分配。trace 中的 LBA 与 MemoryEngine 发出的地址一一对应。

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

| merge_contiguous | request_size | 效果 |
|---|---|---|
| `True` | 大值 (128KB) | 合并 → 少量大 I/O → 测峰值带宽 |
| `False` | 小值 (4KB) | 不合并 → 多条小 I/O → 测每操作延迟 |

---

## 七、理论公式

`trace.py` 同时提供三个无需运行仿真的理论预估函数，用于快速性能评估：

| 函数 | 公式 | 判断标准 |
|---|---|---|
| `theory_iops(S)` | `TOTAL_PLANES × 1e9 / (tR + CHANNELS × BusTime)` | — |
| `theory_bandwidth_mbps(S)` | `IOPS × S / 1e6` | — |
| `theory_bus_utilization(S)` | `variable_cost / (fixed_cost + variable_cost)` | <0.5 IOPS-Bound, >0.9 带宽-Bound |

详见 `run_experiment.py` 的理论公式推导。
