"""MemEngine — run memory simulation from a JSON config file.

Usage:
    python run.py -c configs/analytic.json
    python run.py -c configs/ramulator.json --num-requests 32 --size 128
"""

import argparse
import json
import os

from media import MediaConfig, MediaSystemBackend
from memory_engine import MemoryEngine
from memory_config import MemoryEngineConfig
from memory_type import MemoryRequestType, MemoryType


def parse_args():
    p = argparse.ArgumentParser(description="MemEngine memory simulator")
    p.add_argument("-c", "--config", required=True, help="JSON config file")
    p.add_argument("--num-requests", type=int, default=16)
    p.add_argument("--size", type=int, default=64)
    return p.parse_args()


def main():
    args = parse_args()

    with open(args.config) as f:
        raw = json.load(f)

    mc = raw["media_config"]
    backend = MediaSystemBackend.RAMULATOR if mc["media_type"] == "ramulator" else MediaSystemBackend.ANALYTIC
    mem_type = MemoryType[raw["mem_type"].upper()]

    media_cfg = MediaConfig(
        media_type=backend,
        capacity=mc.get("capacity", 32.0),
        bandwidth=mc.get("bandwidth", 100.0),
        config_path=os.path.abspath(mc["config"]) if mc.get("config") else "",
    )

    engine = MemoryEngine(MemoryEngineConfig(
        memory_type=mem_type,
        media_config=media_cfg,
        dp_size=mc.get("dp", 1),
        storage_instance_num=mc.get("instances", 1),
    ))

    ms = engine.media_system
    tx_bytes = getattr(ms, '_tx_bytes', args.size)
    io_freq = getattr(ms, '_io_frequency_mhz', None)

    print("=" * 50)
    print(f"Mem type:  {mem_type.value}")
    print(f"Backend:   {mc['media_type']}")
    if mc["media_type"] == "ramulator":
        print(f"Cycle freq:{io_freq:.0f} MHz  |  tx_bytes: {tx_bytes}")
        if mc.get("config"):
            print(f"YAML:      {mc['config']}")
    else:
        print(f"Bandwidth: {mc['bandwidth']} GB/s")
    print(f"Capacity:  {mc['capacity']} GB  |  DP: {mc['dp']}  |  Inst: {mc['instances']}")
    print("=" * 50)

    addr = engine.get_tensor_addr(args.num_requests * args.size)
    addrs = [addr + i * args.size for i in range(args.num_requests)]

    metrics = engine.issue_request(
        addrs,
        [args.size] * args.num_requests,
        [MemoryRequestType.KREAD] * args.num_requests,
    )

    print(f"Requests:  {args.num_requests} × {args.size}B → {metrics.memory_reqs_num} media reqs")
    if backend == MediaSystemBackend.RAMULATOR:
        print(f"Cycles:    {metrics.cycles}")
    print(f"Time:      {metrics.total_time * 1e9:.1f} ns")
    print(f"Bandwidth: {engine.get_engine_metrics().avg_bandwidth / 1e9:.2f} GB/s")
    print("=" * 50)


if __name__ == "__main__":
    main()
