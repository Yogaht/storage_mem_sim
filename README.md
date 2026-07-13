# MemEngine

MemEngine 是一个面向 LLM 推理服务场景的内存/存储介质仿真框架。它位于上层服务/模型逻辑和底层介质仿真器之间，将 tensor、KV cache 等高层访问转换成后端可执行的请求，并返回 timing、bandwidth、IOPS 等性能指标。

更详细的设计说明见 [docs/design.md](docs/design.md)。

## 后端说明

| 后端 | 用途 | 当前状态 |
| --- | --- | --- |
| `ANALYTIC` | 快速 roofline 估算，`time = bytes / bandwidth` | 可直接使用 |
| `RAMULATOR` | 通过 Ramulator2 Python bindings 进行 DRAM 周期级仿真 | 安装/构建 Ramulator2 后可用 |
| `MQSIM` | SSD/NVMe 仿真，通过 pymqsim Python 库对接 MQSim C++ 仿真器 | trace 生成、CWDP 地址分配、XML 输出解析已完成 |

## 快速开始

```bash
# 1. 拉取代码
git clone <repo-url>
cd storage_mem_sim

# 2. 安装 Python 依赖
pip install pyyaml

# 3. 运行 Analytic 示例
python run.py -c configs/analytic.json

# 可选：调整请求数量和单请求大小
python run.py -c configs/analytic.json --num-requests 32 --size 128

# 4. 如需运行 Ramulator2 后端
git submodule update --init media/ramulator_wrapper/ramulator2
pip install -e media/ramulator_wrapper/ramulator2

# 5. 运行 Ramulator2 示例
python run.py -c configs/ramulator.json

# 6. 如需运行 MQSim 后端
git submodule update --init media/mqsim_wrapper/MQSim
cd media/mqsim_wrapper && pip install -e .
cd ../..

# 7. 运行 MQSim 示例（带宽 bound：合并 + 大 I/O）
python run.py -c configs/mqsim.json --num-requests 64 --size 131072

# 8. MQSim IOPS bound（不合并 + 小 I/O）
python run.py -c configs/mqsim.json --num-requests 1024 --size 4096
```

## MQSim 后端说明

### 架构

`MQSimMediaSystem` 通过 `pymqsim` Python 库对接 MQSim C++ 仿真器：

```
MemoryEngine.issue_request()
  → MQSimMediaSystem.handler_mem_request(mem_req_list)
    ├─ 1. write_trace_file()        → MQSim trace 文件
    ├─ 2. generate_workload_xml()   → workload XML
    ├─ 3. run_simulation()          → 调用 MQSim（native 或 subprocess）
    └─ 4. 返回 MediaMetrics         → time, bandwidth, IOPS
```

### MQSim trace 格式

每行一条请求：`<arrival_ns> <device_id> <lba> <sectors> <req_type>`

| 字段 | 说明 |
|---|---|
| `arrival_ns` | 到达时间（固定为 0，MemoryEngine 无时序） |
| `device_id` | 设备号（0..15 循环，适配 MQSim 多队列） |
| `lba` | 逻辑块地址 = `addr / 512` |
| `sectors` | 扇区数 = `ceil(size / 512)` |
| `req_type` | 1 = 读, 0 = 写 |

### 请求合并

`merge_sequential()` 自动合并同一请求类型、地址连续的 MemoryRequest：

- **触发条件**: `addr[i] + size[i] == addr[i+1]` 且 `req_type[i] == req_type[i+1]`
- **带宽测试** (`merge_contiguous: true`): 开启合并 → 少量大 I/O → 饱和通道带宽
- **IOPS 测试** (`merge_contiguous: false`): 关闭合并 → 大量小 I/O → 测量每操作延迟

### CWDP 地址分配

`build_trace_lines()` 在生成 trace 时自动应用 CWDP 感知的地址分配策略（参照 `run_experiment.py`）：

- **子页请求** (`line_size < 8KB`)：多轮遍历 — 每轮固定页内偏移，遍历全部页，连续行命中不同通道
- **超页请求** (`line_size ≥ 8KB`)：CWDP 互质步长 — 用 `cwdp_stride_for_pages()` 将步长调整到与 CHANNELS 互质，防止通道塌缩

详见 [mqsim_params_report.md](media/mqsim_wrapper/mqsim_params_report.md) 第六章。

### 控制参数 (`mqsim.json`)

| 参数 | 带宽测试 | IOPS 测试 |
|------|---------|----------|
| `merge_contiguous` | `true` | `false` |
| `request_size` | 131072 (128 KB) | 4096 (4 KB) |

### MQSim 配置文件

- **SSD 设备配置** (`default_ssdconfig.xml`): 定义通道数、芯片数、NAND 参数、FTL 策略等。NAND 几何参数在 `MQSimMediaSystem` 初始化时自动解析并加载到 `trace` 模块
- **Workload 配置** (`default_workload.xml`): 定义 I/O 场景、trace 文件路径占位符、时间单位等

指定自定义配置：

```json
{
    "ssd_config": "path/to/custom_ssdconfig.xml",
    "workload_config": "path/to/custom_workload.xml"
}
```

### pymqsim 库独立使用

```python
from pymqsim import (
    TraceSliceConfig, write_trace_file,
    generate_workload_xml, run_simulation,
    # 理论公式（无需运行仿真即可预估性能）
    theory_iops, theory_bandwidth_mbps, theory_bus_utilization,
)

# 1. 生成 trace 文件
cfg = TraceSliceConfig(merge_contiguous=True, request_size=131072)
total_bytes, lines = write_trace_file(mem_req_list, "trace.txt", cfg)

# 2. 生成 workload XML（基于 default_workload.xml 模板，替换 trace 路径）
generate_workload_xml("trace.txt", "workload.xml")

# 3. 运行仿真
result = run_simulation(
    trace_path="trace.txt",
    ssd_config_path="ssdconfig.xml",
    workload_xml_path="workload.xml",
)

print(f"Latency:  {result.avg_latency_ns:.1f} ns")
print(f"Bandwidth: {result.bandwidth_bytes_per_sec / 1e9:.2f} GB/s")
print(f"IOPS:     {result.total_iops:.0f}")

# 4. 理论预估（不运行仿真）
for size in [4096, 8192, 32768, 65536, 131072]:
    iops = theory_iops(size)
    bw = theory_bandwidth_mbps(size)
    util = theory_bus_utilization(size)
    print(f"{size//1024}KB → IOPS={iops:,.0f}  BW={bw:,.0f} MB/s  U={util:.1%}")
```

输出示例：

```
4KB   → IOPS=363,714  BW=1,490 MB/s  U=55.9%   (IOPS-Bound)
8KB   → IOPS=233,266  BW=1,911 MB/s  U=71.7%   (过渡区)
32KB  → IOPS=74,007   BW=2,425 MB/s  U=91.0%   (带宽-Bound)
64KB  → IOPS=38,741   BW=2,539 MB/s  U=95.3%   (强带宽-Bound)
128KB → IOPS=19,836   BW=2,600 MB/s  U=97.6%   (强带宽-Bound)
```

### 项目结构

```
media/mqsim_wrapper/
├── pymqsim/                   # Python 库
│   ├── __init__.py            # 公开 API（27 个符号）
│   ├── trace.py               # 几何常量 + CWDP + 理论公式 + trace 生成
│   ├── workload.py            # generate_workload_xml — workload XML 生成
│   ├── simulator.py           # run_simulation — 仿真运行器（native + subprocess）
│   └── output.py              # MQSimResult — 输出 XML 解析
├── MQSim/                     # MQSim C++ 子模块
├── default_ssdconfig.xml      # 默认 SSD 设备配置（NAND 几何参数来源）
├── default_workload.xml       # 默认 workload 模板
├── mqsim_pybind.cpp           # pybind11 C++ 桥接
├── CMakeLists.txt             # C++ 构建脚本
├── setup.py                   # pip install 入口
└── trace/                     # 运行时生成的 trace 和 workload 文件
```

### MQSim 构建

```bash
# 1. 初始化 MQSim 子模块
git submodule update --init media/mqsim_wrapper/MQSim

# 2. 编译 MQSim C++ 并安装 pymqsim Python 包
cd media/mqsim_wrapper && pip install -e .

# 3. 验证
python -c "from pymqsim import check_mqsim_available; print(check_mqsim_available())"
```

如果 C++ 扩展未编译成功（非 Linux/WSL 环境），`run_simulation()` 会自动回退到 subprocess 方式调用 MQSim 二进制。
