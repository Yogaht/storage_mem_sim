"""Demo: MemoryEngine → Ramulator2 (media system auto-created by engine).

Usage:
    cd storage_mem_sim
    PYTHONPATH=media/ramulator_wrapper/ramulator2/python:. python demos/demo_ramulator.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from media import MediaConfig, MediaSystemBackend
from memengine import (
    MemoryEngine,
    MemoryEngineConfig,
    MemoryRequestType,
    MemoryType,
)

YAML_PATH = os.path.join(
    os.path.dirname(__file__), "..", "tests", "ramulator_test_config.yaml"
)

# ---------------------------------------------------------------------------
# 1. Engine config — media_config tells the engine which backend to use
# ---------------------------------------------------------------------------

engine = MemoryEngine(MemoryEngineConfig(
    memory_type=MemoryType.HBM,
    media_config=MediaConfig(
        media_type=MediaSystemBackend.RAMULATOR,
        config_path=os.path.abspath(YAML_PATH),
        io_frequency=2400,
        capacity=16.0,  # 16 GB
    ),
))

# The media system is created internally:
ms = engine.media_system
print(f"[Ramulator] config = {ms.config.config_path}")
print(f"             tx_bytes = {ms._tx_bytes}, "
      f"scale_factor = {ms.config.scale_factor:.3e} s/cycle")

# ---------------------------------------------------------------------------
# 2. Simulate
# ---------------------------------------------------------------------------

tensor_size = 1024 * 1024
addr = engine.get_tensor_addr(tensor_size)

num_reqs = 16
chunk_size = tensor_size // num_reqs   # 65536 bytes per request
addrs = [addr + i * chunk_size for i in range(num_reqs)]

metrics = engine.issue_request(addrs, [chunk_size]*num_reqs, [MemoryRequestType.KREAD]*num_reqs)

print(f"\n--- Results ---")
print(f"Requests:  {num_reqs} × {chunk_size} bytes = {num_reqs * chunk_size} bytes total")
g = ms._tx_bytes
print(f"Media reqs:{metrics.memory_reqs_num} (each {chunk_size}B → {chunk_size // g} × {g}B bursts)")
print(f"Cycles:    {metrics.cycles}")
print(f"Time:      {metrics.total_time * 1e9:.2f} ns")
print(f"Bandwidth: {num_reqs * chunk_size / (metrics.total_time + 1e-12) / 1e9:.2f} GB/s")
