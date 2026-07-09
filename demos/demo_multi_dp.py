"""Demo: multi-DP + multi-storage-instance simulation.

Shows how:
 - dp_size: replicates each user request across N DP ranks
 - storage_instance_num: round-robins all replicated requests across M instances
 - per_dp_capacity: each DP rank gets its own independent address range

Usage:
    PYTHONPATH=media/ramulator_wrapper/ramulator2/python:. python demos/demo_multi_dp.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from media import MediaConfig, MediaSystemBackend
from memengine import (
    MemoryEngine,
    MemoryEngineConfig,
    MemoryRequestType,
)

total_cap_gb = 32.0              # 32 GB total
burst = 32                       # DDR5 BC8 tx_bytes
num_requests = 4                 # user-level requests

# ---------------------------------------------------------------------------
# Scenario 1: baseline — dp_size=1, instance_num=1
# ---------------------------------------------------------------------------

engine1 = MemoryEngine(MemoryEngineConfig(
    media_config=MediaConfig(
        media_type=MediaSystemBackend.RAMULATOR,
        io_frequency=2400,
        capacity=total_cap_gb,
    ),
    dp_size=1,
    storage_instance_num=1,
))

msg = engine1.media_system

print("=" * 60)
print("Scenario 1: dp_size=1, instance_num=1 (baseline)")
print(f"  total_capacity={engine1.mem_config.total_capacity//1024**3} GB, "
      f"per_dp={engine1.mem_config.per_dp_capacity//1024**3} GB, "
      f"per_instance={engine1.mem_config.capacity//1024**3} GB")
print("=" * 60)

addrs = [msg._tx_bytes * i for i in range(num_requests)]
metrics1 = engine1.issue_request(
    addrs,
    [burst] * num_requests,
    [MemoryRequestType.KREAD] * num_requests,
)
print(f"  User requests:   {num_requests}")
print(f"  Engine requests: {metrics1.memory_reqs_num}")
print(f"  Cycles:          {metrics1.cycles}")
print(f"  Time:            {metrics1.total_time * 1e9:.2f} ns")

# ---------------------------------------------------------------------------
# Scenario 2: dp_size=2, instance_num=1
#   Each request is replicated ×2 (DP0 + DP1), all sent to 1 instance
# ---------------------------------------------------------------------------

engine2 = MemoryEngine(MemoryEngineConfig(
    media_config=MediaConfig(
        media_type=MediaSystemBackend.RAMULATOR,
        io_frequency=2400,
        capacity=total_cap_gb,
    ),
    dp_size=2,
    storage_instance_num=1,
))

print()
print("=" * 60)
print("Scenario 2: dp_size=2, instance_num=1")
print(f"  total_capacity={engine2.mem_config.total_capacity//1024**3} GB, "
      f"per_dp={engine2.mem_config.per_dp_capacity//1024**3} GB, "
      f"per_instance={engine2.mem_config.capacity//1024**3} GB")
print("=" * 60)
print("  Each user request → 2 DP copies, all to 1 media system")

metrics2 = engine2.issue_request(
    addrs,
    [burst] * num_requests,
    [MemoryRequestType.KREAD] * num_requests,
)
print(f"  User requests:   {num_requests}")
print(f"  Engine requests: {metrics2.memory_reqs_num}  ({num_requests} × 2 DP)")
print(f"  Cycles:          {metrics2.cycles}")
print(f"  Time:            {metrics2.total_time * 1e9:.2f} ns")

# ---------------------------------------------------------------------------
# Scenario 3: dp_size=2, instance_num=2
#   Each request ×2 DP = 8 engine requests, round-robin to 2 instances
# ---------------------------------------------------------------------------

engine3 = MemoryEngine(MemoryEngineConfig(
    media_config=MediaConfig(
        media_type=MediaSystemBackend.RAMULATOR,
        io_frequency=2400,
        capacity=total_cap_gb,
    ),
    dp_size=2,
    storage_instance_num=2,
))

print()
print("=" * 60)
print("Scenario 3: dp_size=2, instance_num=2")
print(f"  total_capacity={engine3.mem_config.total_capacity//1024**3} GB, "
      f"per_dp={engine3.mem_config.per_dp_capacity//1024**3} GB, "
      f"per_instance={engine3.mem_config.capacity//1024**3} GB")
print("=" * 60)
print("  Round-robin distribution:")
print("    instance[0] = req0-dp0, req1-dp0, req2-dp0, req3-dp0")
print("    instance[1] = req0-dp1, req1-dp1, req2-dp1, req3-dp1")

metrics3 = engine3.issue_request(
    addrs,
    [burst] * num_requests,
    [MemoryRequestType.KREAD] * num_requests,
)
print(f"  User requests:   {num_requests}")
print(f"  Engine requests: {metrics3.memory_reqs_num}  ({num_requests} × 2 DP, /2 instances)")
print(f"  Cycles:          {metrics3.cycles}")
print(f"  Time:            {metrics3.total_time * 1e9:.2f} ns")

# ---------------------------------------------------------------------------
# Verify DP address offsets
# ---------------------------------------------------------------------------

print()
print("=" * 60)
print("DP Address Offset Verification")
print("=" * 60)
pdc = engine2.mem_config.per_dp_capacity
print(f"  per_dp_capacity = {pdc // 1024**3} GB (0x{pdc:x})")
print(f"  User addr=0x0")
print(f"    dp_rank=0 → effective_addr=0x0")
print(f"    dp_rank=1 → effective_addr=0x{pdc:x}")
print()
print(f"  User addr=0x{burst}")
print(f"    dp_rank=0 → effective_addr=0x{burst}")
print(f"    dp_rank=1 → effective_addr=0x{pdc + burst:x}")
