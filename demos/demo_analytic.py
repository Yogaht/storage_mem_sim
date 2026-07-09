"""Demo: MemoryEngine → Analytic model (media system auto-created by engine).

Usage:
    PYTHONPATH=. python demos/demo_analytic.py
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

# ---------------------------------------------------------------------------
# Engine config — media_config tells the engine which backend to use
# ---------------------------------------------------------------------------

engine = MemoryEngine(MemoryEngineConfig(
    memory_type=MemoryType.HBM,
    media_config=MediaConfig(
        media_type=MediaSystemBackend.ANALYTIC,
        bandwidth=100.0,
        capacity=16.0,  # 16 GB
    ),
))

print(f"[Analytic] bandwidth = {engine.media_system.config.bandwidth} GB/s")

# ---------------------------------------------------------------------------
# Simulate
# ---------------------------------------------------------------------------

addr = engine.get_tensor_addr(1024 * 1024)

print("\n--- 16 read requests ---")
r = engine.issue_request(
    [addr + i * 64 for i in range(16)],
    [64] * 16,
    [MemoryRequestType.KREAD] * 16,
)
print(f"  Time: {r.total_time * 1e6:.2f} us")

print("\n--- 8 write requests ---")
w = engine.issue_request(
    [addr + i * 64 for i in range(8)],
    [128] * 8,
    [MemoryRequestType.KWRITE] * 8,
)
print(f"  Time: {w.total_time * 1e6:.2f} us")

print(f"\nCumulative: {engine.get_engine_metrics().memory_reqs_num} reqs "
      f"over {len(engine.get_engine_metrics().mem_metrics_list)} batches")
