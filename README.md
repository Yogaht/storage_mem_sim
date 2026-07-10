# MemEngine

MemEngine 是一个面向 LLM 推理服务场景的内存/存储介质仿真框架。它位于上层服务/模型逻辑和底层介质仿真器之间，将 tensor、KV cache 等高层访问转换成后端可执行的请求，并返回 timing、cycles、bandwidth 等性能指标。

更详细的设计说明见 [docs/design.md](docs/design.md)。

## 后端说明

| 后端 | 用途 | 当前状态 |
| --- | --- | --- |
| `ANALYTIC` | 快速 roofline 估算，`time = bytes / bandwidth` | 可直接使用 |
| `RAMULATOR` | 通过 Ramulator2 Python bindings 进行 DRAM 周期级仿真 | 安装/构建 Ramulator2 后可用 |
| `MQSIM` | SSD/NVMe trace 生成和 MQSim subprocess 对接 | trace 路径已实现，输出解析仍较有限 |

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

# 4. 如需运行 Ramulator2 后端，先初始化并安装 Ramulator2
git submodule update --init media/ramulator_wrapper/ramulator2
pip install -e media/ramulator_wrapper/ramulator2

# 如果 Ramulator2 C++ extension 尚未构建，先执行：
cmake -S media/ramulator_wrapper/ramulator2 \
  -B media/ramulator_wrapper/ramulator2/build \
  -DCMAKE_BUILD_TYPE=Release \
  -DRAMULATOR_PYTHON_BINDINGS=ON \
  -DCMAKE_CXX_COMPILER=g++-14
cmake --build media/ramulator_wrapper/ramulator2/build -j$(sysctl -n hw.ncpu)
pip install -e media/ramulator_wrapper/ramulator2

# 5. 运行 Ramulator2 示例
python run.py -c configs/ramulator.json
```
