"""MQSim IOPS vs Bandwidth bound comparison.

Runs two configurations through MemoryEngine and compares actual
results against theoretical predictions (theory_iops / theory_bandwidth_mbps).

Usage:
    python run_mqsim.py [--num-requests N]
"""

import argparse
import json
import os
import sys

from media import MediaConfig, MediaSystemBackend, MQSimMediaSystem
from memory_engine import MemoryEngine
from memory_config import MemoryEngineConfig
from memory_type import MemoryRequestType, MemoryType


def load_theory():
    """Load NAND geometry and return theory functions."""
    from media.mqsim_wrapper.pymqsim.trace import (
        load_from_ssdconfig_xml,
        theory_iops, theory_bandwidth_mbps, theory_bus_utilization,
    )
    ssd_xml = os.path.join(
        os.path.dirname(__file__),
        "configs", "default_ssdconfig.xml")
    load_from_ssdconfig_xml(ssd_xml)
    return theory_iops, theory_bandwidth_mbps, theory_bus_utilization


def run_config(config_path, num_requests, request_size):
    """Run one config through MemoryEngine, return (metrics, media_system)."""
    with open(config_path) as f:
        raw = json.load(f)

    mc = raw["media_config"]
    mem_type = MemoryType[raw["mem_type"].upper()]
    backend = MediaSystemBackend.MQSIM

    media_cfg = MediaConfig(
        media_type=backend,
        capacity=mc.get("capacity", 512.0),
        config_path="",
        ssd_config_path=os.path.abspath(mc["ssd_config"]) if mc.get("ssd_config") else "",
        workload_config_path=os.path.abspath(mc["workload_config"]) if mc.get("workload_config") else "",
        request_size_bytes=8192,
    )

    engine = MemoryEngine(MemoryEngineConfig(
        memory_type=mem_type,
        media_config=media_cfg,
        dp_size=mc.get("dp", 1),
        storage_instance_num=mc.get("instances", 1),
    ))

    ms = engine.media_system

    # Configure trace slicing
    from media.mqsim_wrapper.pymqsim import TraceSliceConfig
    ms.trace_config = TraceSliceConfig(
        merge_contiguous=mc.get("merge_contiguous", True),
        request_size=request_size)

    # Issue num_requests sequential reads, each of request_size bytes
    total_bytes = request_size * num_requests
    base_addr = engine.get_tensor_addr(total_bytes)
    addrs = [base_addr + i * request_size for i in range(num_requests)]
    sizes = [request_size] * num_requests
    metrics = engine.issue_request(
        addrs,
        sizes,
        [MemoryRequestType.KREAD] * num_requests,
    )
    return metrics, ms, total_bytes


def main():
    parser = argparse.ArgumentParser(
        description="MQSim IOPS vs Bandwidth bound comparison")
    parser.add_argument("--num-requests", type=int, default=None,
                        help="Override request count for both configs")
    args = parser.parse_args()

    theory_iops_fn, theory_bw_fn, theory_bus_fn = load_theory()

    configs = [
            {
                "name": "iosize=512B",
                "path": "configs/mqsim_16k_iops_12m_bw_200g.json",
                "size": 512,
                "n": args.num_requests or 65536,
                "label": "512B sequential",
            },
            {
                "name": "iosize=1k",
                "path": "configs/mqsim_16k_iops_12m_bw_200g.json",
                "size": 1024,
                "n": args.num_requests or 32768,
                "label": "1KB sequential",
            },
            {
                "name": "iosize=5k",
                "path": "configs/mqsim_16k_iops_12m_bw_200g.json",
                "size": 5120,
                "n": args.num_requests or 6552,
                "label": "5KB sequential",
            },
            {
                "name": "iosize=10k",
                "path": "configs/mqsim_16k_iops_12m_bw_200g.json",
                "size": 10240,
                "n": args.num_requests or 3276,
                "label": "10KB sequential",
            },
            {
                "name": "iosize=16k",
                "path": "configs/mqsim_16k_iops_12m_bw_200g.json",
                "size": 16384,
                "n": args.num_requests or 2048,
                "label": "16KB sequential",
            },
            {
                "name": "iosize=20k",
                "path": "configs/mqsim_16k_iops_12m_bw_200g.json",
                "size": 20480,
                "n": args.num_requests or 1638,
                "label": "20KB sequential",
            },
            {
                "name": "iosize=32k",
                "path": "configs/mqsim_16k_iops_12m_bw_200g.json",
                "size": 32768,
                "n": args.num_requests or 1024,
                "label": "32KB sequential",
            },
            {
                "name": "iosize=37k",
                "path": "configs/mqsim_16k_iops_12m_bw_200g.json",
                "size": 37888,
                "n": args.num_requests or 886,
                "label": "128KB sequential",
            },
        ]

    results = []
    for cfg in configs:
        print(f"\n{'='*60}")
        print(f"  {cfg['name']}: {cfg['label']}, "
              f"{cfg['n']} × {cfg['size']//1024}KB")
        print(f"{'='*60}")

        try:
            metrics, ms, total_bytes = run_config(
                cfg["path"], cfg["n"], cfg["size"])
        except RuntimeError as e:
            print(f"  SKIP: {e}")
            results.append({**cfg, "status": "SKIP", "error": str(e)})
            continue

        theo_iops = theory_iops_fn(cfg["size"])
        theo_bw = theory_bw_fn(cfg["size"])
        bus_util = theory_bus_fn(cfg["size"])

        actual_iops = metrics.iops
        actual_bw_mbps = metrics.bandwidth/1024/1024
        actual_time_s = metrics.total_time

        # IOPS efficiency: actual / theoretical
        iops_eff = (actual_iops / theo_iops * 100) if theo_iops > 0 else 0
        bw_eff = (actual_bw_mbps / theo_bw * 100) if theo_bw > 0 else 0

        print(f"  Requests:      {cfg['n']} × {cfg['size']}B = "
              f"{total_bytes/(1024**2):.8f} MB")
        print(f"  Time:          {actual_time_s:.8f} s")

        results.append({
            **cfg,
            "status": "OK",
            "theo_iops": theo_iops,
            "theo_bw_mbps": theo_bw,
            "bus_util": bus_util,
            "actual_iops": actual_iops,
            "actual_bw_mbps": actual_bw_mbps,
            "iops_eff": iops_eff,
            "bw_eff": bw_eff,
            "total_bytes": total_bytes,
            "actual_time_s": actual_time_s,
        })

    # ---- summary table ----
    print(f"\n{'='*90}")
    print(f"  Theory vs Actual Comparison")
    print(f"{'='*90}")
    header = (f"{'':>16} │ {'Theory':>10} │ {'Actual':>10} │ "
              f"{'Efficiency':>10} │ {'Bus Util':>9}")
    print(header)
    print("-" * 90)

    for r in results:
        if r["status"] != "OK":
            print(f"  {r['name']:>16} │ {'SKIPPED':>10} │")
            continue

        print(f"  {r['name']+ ' IOPS':>16} │ "
              f"{r['theo_iops']:>10,.0f} │ "
              f"{r['actual_iops']:>10,.0f} │ "
              f"{r['iops_eff']:>9.1f}% │ "
              f"{r['bus_util']:>8.1%}")

        print(f"  {r['name']+ ' BW':>16} │ "
              f"{r['theo_bw_mbps']:>8,.0f} MB/s │ "
              f"{r['actual_bw_mbps']:>8,.0f} MB/s │ "
              f"{r['bw_eff']:>9.1f}% │")



if __name__ == "__main__":
    main()
