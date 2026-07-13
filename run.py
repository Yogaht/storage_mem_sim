"""MemEngine — run memory simulation from a JSON config file.

Usage:
    python run.py -c configs/analytic.json
    python run.py -c configs/ramulator.json --num-requests 32
    python run.py -c configs/mqsim.json --num-requests 64 --size 131072

All MQSim-specific parameters (merge_contiguous, request_size, etc.)
are read from the JSON config file.  Command-line --num-requests and --size
override the config values when provided.
"""

import argparse
import json
import os

from media import MediaConfig, MediaSystemBackend
from memory_engine import MemoryEngine
from memory_config import MemoryEngineConfig
from memory_type import MemoryRequestType


def parse_args():
    p = argparse.ArgumentParser(description="MemEngine memory simulator")
    p.add_argument("-c", "--config", required=True, help="JSON config file")
    p.add_argument("--num-requests", type=int, default=None,
                   help="Number of requests (overrides config)")
    p.add_argument("--size", type=int, default=None,
                   help="Request size in bytes (overrides config)")
    return p.parse_args()


def main():
    args = parse_args()

    with open(args.config) as f:
        cfg = json.load(f)

    # ---- backend ----
    backend_name = cfg["backend"]
    if backend_name == "ramulator":
        backend = MediaSystemBackend.RAMULATOR
    elif backend_name == "mqsim":
        backend = MediaSystemBackend.MQSIM
    else:
        backend = MediaSystemBackend.ANALYTIC

    # ---- MQSim parameters from JSON (with CLI overrides) ----
    request_size = args.size or cfg.get("request_size", 131072)
    num_requests = args.num_requests or cfg.get("num_requests", 64)
    merge_contiguous = cfg.get("merge_contiguous", True)

    # ---- build MediaConfig ----
    media_cfg = MediaConfig(
        media_type=backend,
        capacity=cfg.get("capacity", 32.0),
        bandwidth=cfg.get("bandwidth", 100.0),
        io_frequency=cfg.get("io_freq", 2400.0),
        config_path=os.path.abspath(cfg["config"]) if cfg.get("config") else "",
        # MQSim-specific
        ssd_config_path=os.path.abspath(cfg["ssd_config"]) if cfg.get("ssd_config") else "",
        workload_config_path=os.path.abspath(cfg["workload_config"]) if cfg.get("workload_config") else "",
        request_size_bytes=request_size,
    )

    engine = MemoryEngine(MemoryEngineConfig(
        media_config=media_cfg,
        dp_size=cfg.get("dp", 1),
        storage_instance_num=cfg.get("instances", 1),
    ))

    ms = engine.media_system

    # ---- pass trace slicing config to MQSim backend ----
    if backend == MediaSystemBackend.MQSIM:
        from media.mqsim_wrapper.pymqsim import TraceSliceConfig
        trace_cfg = TraceSliceConfig(
            merge_contiguous=merge_contiguous,
            request_size=request_size)
        ms.trace_config = trace_cfg

    tx_bytes = getattr(ms, '_tx_bytes', request_size)

    # ---- status banner ----
    print("=" * 60)
    print(f"Backend:     {backend_name}")
    if backend_name == "ramulator":
        print(f"IO freq:     {cfg['io_freq']} MHz  |  tx_bytes: {tx_bytes}")
        if cfg.get("config"):
            print(f"YAML:        {cfg['config']}")
    elif backend_name == "mqsim":
        print(f"Merge:       {merge_contiguous}")
        print(f"Req size:    {request_size} B")
        print(f"Num reqs:    {num_requests}")
        if cfg.get("ssd_config"):
            print(f"SSD config:  {cfg['ssd_config']}")
        if cfg.get("workload_config"):
            print(f"Workload:    {cfg['workload_config']}")
        if hasattr(ms, 'mqsim_available'):
            status = "loaded" if ms.mqsim_available else "NOT BUILT"
            print(f"_mqsim:      {status}")
            if not ms.mqsim_available:
                print(f"  Build: cd media/mqsim_wrapper && pip install -e .")
    else:
        print(f"Bandwidth:   {cfg['bandwidth']} GB/s")
    print(f"Capacity:    {cfg['capacity']} GB  |  DP: {cfg.get('dp', 1)}  |  Inst: {cfg.get('instances', 1)}")
    print("=" * 60)

    # ---- issue requests ----
    addr = engine.get_tensor_addr(num_requests * request_size)
    addrs = [addr + i * request_size for i in range(num_requests)]

    metrics = engine.issue_request(
        addrs,
        [request_size] * num_requests,
        [MemoryRequestType.KREAD] * num_requests,
    )

    # ---- results ----
    eng_metrics = engine.get_engine_metrics()
    print(f"Requests:   {num_requests} x {request_size}B → {metrics.memory_reqs_num} engine reqs")
    if backend == MediaSystemBackend.RAMULATOR:
        print(f"Cycles:     {metrics.cycles}")
    print(f"Time:       {metrics.total_time * 1e9:.1f} ns")
    print(f"Bandwidth:  {metrics.bandwidth / 1e9:.2f} GB/s")
    print(f"IOPS:       {metrics.iops:.0f}")

    # MQSim-specific: latency from last simulation result
    if backend == MediaSystemBackend.MQSIM and hasattr(ms, 'last_result'):
        lr = ms.last_result
        if lr is not None and lr.avg_latency_ns > 0:
            print(f"Read Lat:   {lr.avg_latency_ns:.1f} ns")

    print("=" * 60)


if __name__ == "__main__":
    main()
