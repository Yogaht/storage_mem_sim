# MQSim Wrapper 架构与文件功能分析报告

> 分析对象：`media/mqsim_wrapper/` 全部核心文件  
> 日期：2026-07-13

---

## 一、总体架构

```
media/mqsim_wrapper/
│
├── C++ 桥接层 ──────────────────────────────────────────────┐
│   mqsim_pybind.cpp      pybind11 绑定，编译为 _mqsim.so     │
│   CMakeLists.txt         构建脚本 (CMake → _mqsim)          │
│   setup.py               pip install 入口 (调用 CMake)      │
│   MQSim/                 MQSim C++ 子模块 (只读)            │
│                                                             │
├── Python 库 (pymqsim/) ────────────────────────────────────┤
│   ├── __init__.py         公开 API 入口 (27 个符号)         │
│   ├── trace.py            NAND 几何 + CWDP + 理论公式       │
│   │                        + trace 生成 (合并自 constants)  │
│   ├── workload.py         基于模板生成 workload XML          │
│   ├── simulator.py        运行 MQSim 仿真，返回结果          │
│   └── output.py           解析 MQSim 输出 XML               │
│                                                             │
├── 模板文件 ────────────────────────────────────────────────┤
│   default_ssdconfig.xml   默认 SSD 硬件配置                  │
│   default_workload.xml    默认 workload 模板                 │
│                                                             │
└── 运行产物 (trace/) ───────────────────────────────────────┤
    mqsim_trace_*.txt       生成的 trace 文件                  │
    mqsim_workload_*.xml    生成的 workload XML                │
    workload_scenario_*.xml MQSim 仿真结果输出                  │
```

### 配置文件 (`configs/mqsim.json`)

```json
{
    "backend": "mqsim",
    "capacity": 512.0,
    "ssd_config": "media/mqsim_wrapper/default_ssdconfig.xml",
    "workload_config": "media/mqsim_wrapper/default_workload.xml",
    "merge_contiguous": true,
    "request_size": 131072,
    "dp": 1,
    "instances": 1
}
```

- `merge_contiguous` — 直接控制 trace 生成时是否合并连续同类型请求
- `ssd_config` / `workload_config` — 指向 SSD 硬件配置和 workload 模板 XML

### 外部调用层 (`media/mqsim_media_system.py`)

位于 `media/` 父目录，是 MemEngine 框架的 MQSim 后端适配器：

```
MemoryEngine.issue_request()
  → MQSimMediaSystem.handler_mem_request()
    ├─ 1. write_trace_file()        → trace 文件
    ├─ 2. generate_workload_xml()   → workload XML
    ├─ 3. run_simulation()          → 调用 MQSim
    └─ 4. 返回 MediaMetrics
```

`_init_mqsim()` 在构造时自动调用 `load_from_ssdconfig_xml()` 和 `load_from_workload_xml()`，将 XML 中的 NAND 几何参数加载到 `trace` 模块，确保 CWDP 地址映射与实际硬件配置一致。

---

## 二、C++ 桥接层 (`mqsim_pybind.cpp`)

### 文件概要

| 属性 | 值 |
|---|---|
| 行数 | 313 |
| 语言 | C++11 |
| 依赖 | pybind11, MQSim 源码 (MQSim/src/) |
| 产物 | `_mqsim.so` (Linux) / `_mqsim.pyd` (Windows) |
| Python 接口 | `_mqsim.run()`, `_mqsim.run_with_stats()` |

### 核心功能

将 MQSim 的 C++ 仿真引擎封装为 Python 可直接调用的函数，消除子进程开销。

### 函数逐段分析

#### 1. `read_config()` (第 42-68 行) — SSD 配置 XML 解析

```cpp
static Execution_Parameter_Set* read_config(const std::string& path)
```

- 读取 `ssdconfig.xml`，使用 RapidXML 反序列化
- 返回 MQSim 内部的 `Execution_Parameter_Set` 结构体（包含 Host、SSD、Flash 全部参数）
- 若文件不存在或内容为 `"USE_INTERNAL_PARAMS"`，返回默认参数

#### 2. `read_workloads()` (第 74-117 行) — Workload XML 解析

```cpp
static std::vector<std::vector<IO_Flow_Parameter_Set*>*>*
read_workloads(const std::string& path)
```

- 读取 workload XML，遍历 `<IO_Scenario>` 节点
- 支持两种 flow 类型：
  - `IO_Flow_Parameter_Set_Trace_Based` — 基于 trace 回放
  - `IO_Flow_Parameter_Set_Synthetic` — 合成 workload
- 返回嵌套 vector：`[scenario_index][flow_index]`

#### 3. `write_results()` (第 123-132 行) — 结果 XML 写入

```cpp
static void write_results(SSD_Device& ssd, Host_System& host, const std::string& path)
```

- 调用 MQSim 内部的 `Report_results_in_XML()` 方法
- 写入 `workload_scenario_N.xml`（与 MQSim 独立二进制输出格式一致）
- 与 `output.py` 的 `parse_mqsim_output()` 配套

#### 4. `print_flow_stats()` (第 138-153 行) — 控制台统计输出

- 打印每个 I/O flow 的统计信息（生成请求数、服务请求数、响应时间、端到端延迟）

#### 5. `collect_flow_stats()` (第 159-175 行) — Python 统计字典

```cpp
static py::dict collect_flow_stats(Host_System& host, SSD_Device& ssd)
```

- 提取第一个 I/O flow 的关键统计数据
- 返回 Python dict：`generated_request_count`, `serviced_request_count`, `device_response_time_ns`, `end_to_end_request_delay_ns`

#### 6. `simulate()` (第 181-267 行) — 核心仿真循环

```cpp
static bool simulate(ssd_config_path, workload_config_path, output_dir, out_stats)
```

完整仿真流程：
1. **抑制 MQSim 的 stdout** — 重定向到 stringstream
2. 调用 `read_config()` 和 `read_workloads()` 解析输入文件
3. 对每个 IO scenario：
   - `Simulator->Reset()` — 重置仿真引擎
   - 创建 `SSD_Device` 和 `Host_System` 对象
   - `Simulator->Start_simulation()` — 运行仿真
   - 写入结果 XML
   - 可选：收集统计数据到 Python dict
4. **异常安全清理** — 析构 `Execution_Parameter_Set` 和所有 `IO_Flow_Parameter_Set` 对象

#### 7. Python 导出接口 (第 295-312 行)

```cpp
PYBIND11_MODULE(_mqsim, m) {
    m.def("run", &run, ...);            // 返回 bool
    m.def("run_with_stats", &run_with_stats, ...); // 返回 dict
}
```

### 与 Python 层的协作

```
simulator.py: run_simulation()
  ├─ 优先: _mqsim.run_with_stats() → 原生 C++，获取统计数据
  ├─ 回退: _mqsim.run()            → 原生 C++，仅返回成功/失败
  └─ 兜底: subprocess 调用 MQSim 二进制 → 通用但慢
```


## 三、Python 库 (`pymqsim/`)

### 3.1 `__init__.py` — 公开 API 入口

**功能**：统一导出所有公开符号，是 pymqsim 包的唯一入口。

**导出清单**（27 个符号）：

| 分类 | 符号 |
|---|---|
| 仿真运行 | `run_simulation`, `check_mqsim_available` |
| Trace 生成 | `write_trace_file`, `build_trace_lines`, `merge_sequential` |
| Trace 配置 | `TraceSliceConfig` |
| Workload | `generate_workload_xml`, `MQSimWorkload` |
| 结果 | `MQSimResult`, `parse_mqsim_output` |
| 几何常量 | `CHANNELS`, `SECTOR_SIZE`, `PAGE_SIZE_BYTES`, `SECTORS_PER_PAGE` |
| CWDP 函数 | `cwdp_decode`, `cwdp_stride_for_pages`, `align_lba`, `addr_to_lba`, `size_to_sectors` |
| XML 加载 | `load_from_ssdconfig_xml`, `load_from_workload_xml` |
| 理论公式 | `theory_iops`, `theory_bandwidth_mbps`, `theory_bus_utilization` |

**注意**：已删除 `MQSimConfig`（旧 `config.py` 中的类，生产路径从未使用）。所有符号来自 4 个模块：`trace`、`workload`、`simulator`、`output`。


### 3.2 `trace.py` — NAND 几何 + CWDP + 理论公式 + Trace 生成

**功能**：合并了旧版 `constants.py` 和 `trace.py` 的全部功能，是 pymqsim 的核心模块。包含 NAND 几何常量、XML 动态加载、CWDP 地址解码、理论性能预估、以及从 MemoryRequest 生成 MQSim trace 文件的完整管线。

**设计要点**：
- 模块级默认值与 `default_ssdconfig.xml` 一致
- `load_from_ssdconfig_xml()` 可在运行时解析任意 SSD 配置 XML，更新全部常量
- 派生常量在加载后自动重算
- `addr_to_lba()` / `size_to_sectors()` 使用 `None` 哨兵默认参数，确保始终使用当前 `SECTOR_SIZE`

#### 3.2.1 几何常量

| 常量 | 默认值 | 含义 | XML 来源 |
|---|---|---|---|
| `CHANNELS` | 8 | NAND 通道数 | `Flash_Channel_Count` |
| `CHIPS_PER_CH` | 4 | 每通道芯片数 | `Chip_No_Per_Channel` |
| `DIES_PER_CHIP` | 2 | 每芯片 Die 数 | `Die_No_Per_Chip` |
| `PLANES_PER_DIE` | 2 | 每 Die Plane 数 | `Plane_No_Per_Die` |
| `PAGES_PER_BLOCK` | 256 | 每 Block 页数 | `Page_No_Per_Block` |
| `PAGE_SIZE_BYTES` | 8192 | 页大小 (8 KiB) | `Page_Capacity` |
| `SECTOR_SIZE` | 512 | 扇区大小 (512 B) | (固定) |
| `CHANNEL_BW_MBPS` | 333 | 单通道带宽 (MB/s) | `Channel_Transfer_Rate` |
| `NAND_tR_NS` | 75000 | 页读取延迟 (ns) | `Page_Read_Latency_LSB` |

**派生常量**（自动计算）：

| 常量 | 计算方式 | 默认值 |
|---|---|---|
| `SECTORS_PER_PAGE` | `PAGE_SIZE_BYTES // SECTOR_SIZE` | 16 |
| `TOTAL_PLANES` | `CHANNELS × CHIPS_PER_CH × DIES_PER_CHIP × PLANES_PER_DIE` | 128 |
| `TOTAL_CHANNEL_BW_MBPS` | `CHANNELS × CHANNEL_BW_MBPS` | 2664 |

**XML 标签映射**：

```
ssdconfig.xml                              → 常量
────────────────────────────────────────────────────────
Device_Parameter_Set/Flash_Channel_Count   → CHANNELS
Device_Parameter_Set/Chip_No_Per_Channel   → CHIPS_PER_CH
Device_Parameter_Set/Channel_Transfer_Rate → CHANNEL_BW_MBPS
Flash_Parameter_Set/Die_No_Per_Chip        → DIES_PER_CHIP
Flash_Parameter_Set/Plane_No_Per_Die       → PLANES_PER_DIE
Flash_Parameter_Set/Page_No_Per_Block      → PAGES_PER_BLOCK
Flash_Parameter_Set/Page_Capacity          → PAGE_SIZE_BYTES
Flash_Parameter_Set/Page_Read_Latency_LSB  → NAND_tR_NS
```

#### 3.2.2 XML 加载函数

| 函数 | 功能 |
|---|---|
| `load_from_ssdconfig_xml(path)` | 解析 ssdconfig.xml，更新模块全局常量，返回 `{name: (old, new)}` |
| `load_from_workload_xml(path)` | 解析 workload.xml，返回 `{channel_ids, chip_ids, die_ids, plane_ids}` |

#### 3.2.3 CWDP 地址解码

| 函数 | 功能 |
|---|---|
| `cwdp_decode(lba)` | LBA → (Channel, Chip, Die, Plane) 四元组 |
| `cwdp_stride_for_pages(n)` | 计算与 CHANNELS 互质的最小步长 ≥ n，避免通道塌缩 |

**CWDP 通道塌缩原理**：

```
CWDP 映射: page → Channel(page % CHANNELS)

连续请求 i 的起始页 = start + i × stride

若 gcd(stride, CHANNELS) = d > 1:
  → 连续请求只命中 CHANNELS/d 个不同通道
  → 其余通道闲置 → 带宽塌缩!

例: stride=16 (128KB 请求, 16 页/请求):
  gcd(16,8)=8 → 所有请求打在同一通道 → 只用 1/8 带宽!
修正: stride=17 → gcd(17,8)=1 → 8/8 通道全利用
```

#### 3.2.4 理论性能公式

| 函数 | 公式 | 用途 |
|---|---|---|
| `theory_iops(S)` | `64e9 / (tR + 8×(CMD+Setup+DataOut))` | 预估峰值 IOPS |
| `theory_bandwidth_mbps(S)` | `IOPS × S / 1e6` | 预估峰值带宽 (MB/s) |
| `theory_bus_utilization(S)` | `variable / (fixed + variable)` | 判断瓶颈类型 |

**瓶颈分类**：
- `U < 0.50` → 强 IOPS-Bound（瓶颈在 NAND tR + CMD 开销）
- `0.50 ≤ U < 0.70` → IOPS-Bound
- `0.70 ≤ U < 0.90` → 过渡区
- `0.90 ≤ U < 0.95` → 带宽-Bound
- `U ≥ 0.95` → 强带宽-Bound（瓶颈在通道总线带宽）

#### 3.2.5 Trace 配置

```python
@dataclass
class TraceSliceConfig:
    merge_contiguous: bool = True   # 合并连续同类型请求
    request_size: int = 131072      # 每条 trace line 最大字节数
```

#### 3.2.6 Trace 生成管线

**`build_trace_lines(reqs, cfg)`** — 三步管线：

1. **合并** (`merge_contiguous=True`)：合并连续同类型 MemoryRequest
2. **切片**：按 `request_size` 将大块切分为 trace line
3. **CWDP 感知地址分配**（参照 `run_experiment.py`）：
   - **子页请求** (`line_size < PAGE_SIZE_BYTES`)：多轮遍历 —— 每轮固定页内偏移，遍历全部页，连续行命中不同通道
   - **超页请求** (`line_size ≥ PAGE_SIZE_BYTES`)：CWDP 互质步长 —— 用 `cwdp_stride_for_pages()` 修正 stride，确保连续行命中不同通道
   - **单行** (`n=1`)：直接页对齐

**`write_trace_file(reqs, path, cfg)`** — 调用 `build_trace_lines` 后写入文件。

**Trace 文件格式**（每行 5 个字段）：
```
<arrival_ns> <device_id> <lba> <sectors> <req_type>
0 0 0 256 1         # T=0, dev=0, LBA=0, 256扇区(128KB), 读
0 1 272 256 1       # T=0, dev=1, LBA=272(页17), 读 (CWDP stride=17!)
```

**关键设计**：CWDP 步长修正在**地址生成阶段**完成（不是事后重排）。这与 `run_experiment.py` 的 `generate_trace()` 策略一致。

**`merge_sequential(reqs)`** — 合并连续同类型请求（供外部测试使用）。

#### 3.2.7 内部函数

| 函数 | 功能 |
|---|---|
| `_recompute_derived()` | 几何常量变化后重算派生值 |
| `_cwdp_interleave()` | 按 CWDP 通道轮询重排 trace line（保留，供显式调用） |
| `_xml_int()` | 从 XML 元素提取 int 并更新模块全局变量 |

**注意**：`_cwdp_interleave` 不再被 `build_trace_lines` 自动调用。事后重排无法修复地址 stride 导致的通道塌缩，正确的做法是在地址生成时使用互质步长。


### 3.3 `workload.py` — Workload XML 生成

**功能**：基于模板生成 MQSim 的 workload 配置文件。

**唯一公开函数 — `generate_workload_xml()`**：

```python
def generate_workload_xml(trace_path, output_path, template_path=None) -> str
```

**流程**：
1. 读取模板 XML（默认 `default_workload.xml`）
2. 正则替换 `<File_Path>.*?</File_Path>` → `<File_Path>{trace_path}</File_Path>`
3. 所有其他参数保持模板原样
4. 写入 `output_path`

**向后兼容类 — `MQSimWorkload`**：薄封装，`build_trace_based()` 委托给 `generate_workload_xml()`。


### 3.4 `simulator.py` — 仿真运行器

**功能**：运行 MQSim 仿真并返回解析后的结果。

**核心函数 — `run_simulation()`**：

```python
def run_simulation(
    trace_path,           # trace 文件
    ssd_config_path,      # ssdconfig.xml 路径
    workload_xml_path,    # workload XML 路径
    output_dir=...,       # 工作目录
    timeout_sec=300,      # 子进程超时
    mqsim_binary=...,     # MQSim 二进制路径（子进程回退）
) -> MQSimResult
```

**双路径策略**：

```
run_simulation()
  │
  ├─ 路径 1: native pybind11 (_mqsim)
  │   ├─ _mqsim.run_with_stats() → 返回统计数据字典
  │   └─ _mqsim.run()            → 返回 True/False
  │   优点: 零进程开销，直接获取统计数据
  │   缺点: 需编译 C++ 扩展 (Linux/WSL)
  │
  └─ 路径 2: subprocess 回退
      └─ MQSim -i ssdconfig.xml -w workload.xml
      优点: 任何平台可用
      缺点: 进程启动开销，需单独编译 MQSim 二进制
```

**辅助函数**：

| 函数 | 功能 |
|---|---|
| `_get_native()` | 懒加载 `_mqsim` 模块，缓存结果 |
| `_find_mqsim_binary(path)` | 三级查找：用户指定 → 捆绑路径 → `$PATH` |
| `check_mqsim_available()` | 检查 MQSim 是否可用（native 或 binary） |
| `_copy_file(src, dst)` | 文件复制 |
| `_find_output_xml(dir)` | 扫描 `workload_scenario_*.xml` |


### 3.5 `output.py` — 仿真结果解析

**功能**：解析 MQSim 输出的 XML 文件，提取性能指标。

**核心数据类 — `MQSimResult`**：

| 字段 | 类型 | 含义 |
|---|---|---|
| `total_read_requests` | int | 读请求总数 |
| `total_write_requests` | int | 写请求总数 |
| `total_bytes_read` | int | 读取总字节数 |
| `total_bytes_written` | int | 写入总字节数 |
| `bandwidth_bytes_per_sec` | float | 带宽 (B/s) |
| `iops_read` | float | 读 IOPS |
| `iops_write` | float | 写 IOPS |
| `device_response_time_ns` | float | 设备响应时间 (ns) |
| `end_to_end_delay_ns` | float | 端到端延迟 (ns) |
| `total_time_ns` | float | 仿真总时间 (ns, 由 bytes/bandwidth 推算) |

**派生属性**：

| 属性 | 计算方式 |
|---|---|
| `total_requests` | reads + writes |
| `total_bytes` | bytes_read + bytes_written |
| `avg_latency_ns` | `device_response_time_ns` |
| `total_iops` | iops_read + iops_write |

**核心函数 — `parse_mqsim_output(xml_path)`**：

- 通过 `xml.etree.ElementTree` 解析 `<Host.IO_Flow>` 子元素
- 使用 `_TAG_MAP` 字典将 XML 标签映射到 `MQSimResult` 字段
- 自动计算 `total_time_ns = total_bytes / bandwidth * 1e9`

**XML 标签映射表**：

| XML 标签 | 结果字段 |
|---|---|
| `Read_Request_Count` | `total_read_requests` |
| `Write_Request_Count` | `total_write_requests` |
| `Bytes_Transferred_Read` | `total_bytes_read` |
| `Bytes_Transferred_Write` | `total_bytes_written` |
| `Bandwidth` | `bandwidth_bytes_per_sec` |
| `IOPS_Read` | `iops_read` |
| `IOPS_Write` | `iops_write` |
| `Device_Response_Time` | `device_response_time_ns` |
| `End_to_End_Request_Delay` | `end_to_end_delay_ns` |


## 四、构建系统

### 4.1 `CMakeLists.txt` — C++ 构建

**构建产物**：`_mqsim.so` / `_mqsim.pyd`

**构建流程**：
1. `find_package(Python)` — 查找 Python 3.10+
2. `FetchContent` — 自动下载 pybind11 v2.12.0
3. `file(GLOB_RECURSE)` — 收集 MQSim C++ 源文件（排除 `main.cpp`）
4. `add_library(mqsim_lib STATIC)` — 编译 MQSim 为静态库
5. `pybind11_add_module(_mqsim)` — 编译 `mqsim_pybind.cpp` 链接 `mqsim_lib`
6. 输出到 `pymqsim/` 目录，与 Python 包并列

### 4.2 `setup.py` — pip 安装集成

**安装流程** (`pip install -e .`)：
1. 检查 `MQSim/src/` 子模块是否存在
2. `cmake -S . -B build` — 配置
3. `cmake --build build` — 编译
4. 安装 `pymqsim` Python 包


## 五、完整数据流

```
configs/mqsim.json                     ← 用户配置
        │                               merge_contiguous, request_size,
        │                               ssd_config, workload_config
        ▼
run.py                                 ← 解析 JSON, 创建 MediaConfig
        │                               merge_contiguous → TraceSliceConfig
        ▼
MemoryEngine.issue_request()           ← 生成 MemoryRequest 列表
        │
        ▼
MQSimMediaSystem.handler_mem_request() ← 编排层
        │
        ├─ _init_mqsim()  (构造时)
        │     ├─ load_from_ssdconfig_xml()   ← 解析 ssdconfig.xml
        │     │     更新 trace.CHANNELS, .PAGE_SIZE_BYTES, …
        │     └─ load_from_workload_xml()    ← 解析 workload.xml
        │
        ├─ ① write_trace_file()        ← trace.py
        │     merge → slice → CWDP-aware address distribution
        │       子页: 多轮遍历 (offset→pages)
        │       超页: 互质步长 (cwdp_stride_for_pages)
        │     → trace.txt
        │
        ├─ ② generate_workload_xml()   ← workload.py
        │     模板 <File_Path> 替换 → workload.xml
        │
        └─ ③ run_simulation()         ← simulator.py
              ├─ _mqsim.run_with_stats() ← mqsim_pybind.cpp → MQSim C++
              │   或 subprocess: MQSim binary
              └─ parse_mqsim_output()   ← output.py
                    workload_scenario_1.xml → MQSimResult
        │
        ▼
MediaMetrics                           ← 返回给上层
```


## 六、CWDP 通道塌缩与修复

### 问题

CWDP 映射 `page → Channel(page % 8)` 下，连续请求的起始页步长 `stride` 若与 8 不互质，会导致通道塌缩：

| 请求大小 | 跨页数 | stride | gcd(stride,8) | 实际通道数 |
|---|---|---|---|---|
| 8KB | 1 页 | 1 | 1 | 8/8 ✓ |
| 16KB | 2 页 | 2 | 2 | 4/8 ✗ |
| 32KB | 4 页 | 4 | 4 | 2/8 ✗ |
| 64KB | 8 页 | 8 | 8 | 1/8 ✗ |
| 128KB | 16 页 | 16 | 8 | 1/8 ✗ |

### 修复

`cwdp_stride_for_pages()` 将步长调整为与 8 互质：

| 请求大小 | 原 stride | 修正 stride | gcd(修正,8) | 效果 |
|---|---|---|---|---|
| 16KB | 2 | 3 | 1 | 8/8 ✓ |
| 32KB | 4 | 5 | 1 | 8/8 ✓ |
| 64KB | 8 | 9 | 1 | 8/8 ✓ |
| 128KB | 16 | 17 | 1 | 8/8 ✓ |

多出的填充页不会被读取，仅用于保证 CWDP 交错。

### 子页请求的多轮遍历

当 `line_size < PAGE_SIZE_BYTES` 时（如 4KB 请求、8KB 页），多个 trace line 可放入同一页。多轮遍历确保连续行命中不同页 → 不同通道：

```
Round 0 (offset=0):    page 0 (Ch0) → page 1 (Ch1) → page 2 (Ch2)
Round 1 (offset=4096): page 0 (Ch0) → page 1 (Ch1) → page 2 (Ch2)
```

相比顺序填充（page 0,0→page 1,1→…），多轮遍历避免了连续行命中同一通道。


## 七、文件职责总结

| 文件 | 层级 | 行数 | 核心职责 |
|---|---|---|---|
| `mqsim_pybind.cpp` | C++ | 313 | pybind11 桥接：XML 解析 → MQSim 仿真 → 结果输出 |
| `CMakeLists.txt` | 构建 | 82 | 编译 MQSim 静态库 + _mqsim 扩展 |
| `setup.py` | 构建 | 115 | pip install 入口，触发 CMake 编译 |
| `pymqsim/__init__.py` | Python | 63 | 公开 API 统一导出（27 个符号） |
| `pymqsim/trace.py` | Python | 471 | 几何常量 + CWDP + 理论公式 + Trace 生成管线 |
| `pymqsim/workload.py` | Python | 68 | Workload XML 模板替换 |
| `pymqsim/simulator.py` | Python | 212 | 仿真运行器（native + subprocess 双路径） |
| `pymqsim/output.py` | Python | 123 | 结果 XML 解析 → MQSimResult |
| `default_ssdconfig.xml` | 模板 | 70 | 默认 SSD 硬件配置（NAND 几何参数来源） |
| `default_workload.xml` | 模板 | 18 | 默认 workload 模板（trace 路径占位符） |
| `mqsim_media_system.py` | 编排 | 197 | MemEngine 的 MQSim 后端适配器 |
| `run.py` | 入口 | 140 | CLI 入口，解析 mqsim.json，构建 pipeline |
| `configs/mqsim.json` | 配置 | 10 | 用户配置：merge_contiguous, request_size, XML 路径 |

**已删除的文件**（相比旧版）：
- `pymqsim/constants.py` → 合并到 `trace.py`
- `pymqsim/config.py` → `MQSimConfig` 在生产路径从未使用，已删除
