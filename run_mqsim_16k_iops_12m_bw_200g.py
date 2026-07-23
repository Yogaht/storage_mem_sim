"""MQSim IOPS vs Bandwidth bound comparison — 16k_iops_12m_bw_200g config.

Runs multiple request sizes through MemoryEngine + MQSim and compares
actual results against theory_iops / theory_bandwidth_mbps from the
SAME ssdconfig.xml.

Usage:
    python run_mqsim_16k_iops_12m_bw_200g.py [--num-requests N]
"""

import argparse
import json
import math
import os
import sys

from media import MediaConfig, MediaSystemBackend
from memory_engine import MemoryEngine
from memory_config import MemoryEngineConfig
from memory_type import MemoryRequestType, MemoryType


def load_theory(ssd_config_path):
    """Load NAND geometry from *ssd_config_path* and return theory functions."""
    from media.mqsim_wrapper.pymqsim.trace import (
        load_from_ssdconfig_xml,
        theory_iops, theory_bandwidth_mbps, theory_bus_utilization,
    )
    load_from_ssdconfig_xml(ssd_config_path)
    return theory_iops, theory_bandwidth_mbps, theory_bus_utilization


def _read_json(config_path):
    with open(config_path) as f:
        return json.load(f)


def run_config(config_path, num_requests, request_size):
    """Run one config through MemoryEngine, return (metrics, total_bytes)."""
    raw = _read_json(config_path)
    mc = raw["media_config"]

    media_cfg = MediaConfig(
        media_type=MediaSystemBackend.MQSIM,
        capacity=mc.get("capacity", 512.0),
        ssd_config_path=os.path.abspath(mc["ssd_config"]),
        workload_config_path=os.path.abspath(mc.get("workload_config", "")),
        request_size_bytes=request_size,
        merge_contiguous=mc.get("merge_contiguous", True),
    )

    engine = MemoryEngine(MemoryEngineConfig(
        memory_type=MemoryType[raw["mem_type"].upper()],
        media_config=media_cfg,
        dp_size=mc.get("dp", 1),
        storage_instance_num=mc.get("instances", 1),
    ))

    total_bytes = request_size * num_requests
    base_addr = engine.get_tensor_addr(total_bytes)
    addrs = [base_addr + i * request_size for i in range(num_requests)]
    sizes = [request_size] * num_requests
    metrics = engine.issue_request(
        addrs, sizes, [MemoryRequestType.KREAD] * num_requests,
    )
    return metrics, total_bytes


def main():
    parser = argparse.ArgumentParser(
        description="MQSim IOPS vs BW — 16k_iops_12m_bw_200g config")
    parser.add_argument("--num-requests", type=int, default=None)
    args = parser.parse_args()

    CONFIG_JSON = os.path.join(os.path.dirname(__file__),
                               "configs", "mqsim_16k_iops_12m_bw_200g.json")
    raw = _read_json(CONFIG_JSON)
    ssd_xml = os.path.join(os.path.dirname(__file__),
                           raw["media_config"]["ssd_config"])

    theory_iops_fn, theory_bw_fn, theory_bus_fn = load_theory(ssd_xml)

    scenarios = [
        ("iosize=512B",  512,   65536),
        ("iosize=1k",    1024,  32768),
        ("iosize=5k",    5120,   6552),
        ("iosize=10k",  10240,   3276),
        ("iosize=16k",  16384,   2048),
        ("iosize=20k",  20480,   1638),
        ("iosize=32k",  32768,   1024),
        ("iosize=37k",  37888,    886),
    ]

    results = []
    for name, req_size, default_n in scenarios:
        n = args.num_requests or default_n
        label = f"{req_size//1024}KB" if req_size >= 1024 else f"{req_size}B"
        print(f"\n{'='*60}")
        print(f"  {name}: {label} sequential, {n} requests")
        print(f"{'='*60}")

        try:
            metrics, total_bytes = run_config(CONFIG_JSON, n, req_size)
        except RuntimeError as e:
            print(f"  SKIP: {e}")
            results.append({"name": name, "status": "SKIP", "error": str(e)})
            continue

        theo_iops = theory_iops_fn(req_size)
        theo_bw   = theory_bw_fn(req_size)
        bus_util  = theory_bus_fn(req_size)

        actual_iops    = metrics.iops
        actual_bw_mbps = metrics.bandwidth / 1e6       # B/s → MB/s
        actual_time_s  = metrics.total_time

        iops_eff = (actual_iops / theo_iops * 100) if theo_iops > 0 else 0
        bw_eff   = (actual_bw_mbps / theo_bw * 100) if theo_bw > 0 else 0

        print(f"  Theory:   IOPS={theo_iops:,.0f}  BW={theo_bw:,.0f} MB/s  "
              f"BusUtil={bus_util:.1%}")
        print(f"  Actual:   IOPS={actual_iops:,.0f}  BW={actual_bw_mbps:,.0f} MB/s  "
              f"Time={actual_time_s:.6f}s")
        print(f"  Efficiency: IOPS={iops_eff:.1f}%  BW={bw_eff:.1f}%")

        results.append({
            "name": name, "status": "OK",
            "req_size": req_size, "n": n, "total_bytes": total_bytes,
            "theo_iops": theo_iops, "theo_bw_mbps": theo_bw, "bus_util": bus_util,
            "actual_iops": actual_iops, "actual_bw_mbps": actual_bw_mbps,
            "iops_eff": iops_eff, "bw_eff": bw_eff,
            "actual_time_s": actual_time_s,
        })

    # ---- summary table ----
    print(f"\n{'='*90}")
    print(f"                 │     Theory │     Actual │ Efficiency │  Bus Util")
    print(f"{'-'*90}")
    for r in results:
        if r["status"] != "OK":
            continue
        print(f"  {r['name'] + ' IOPS':>16} │ "
              f"{r['theo_iops']:>10,.0f} │ "
              f"{r['actual_iops']:>10,.0f} │ "
              f"{r['iops_eff']:>9.1f}% │ "
              f"{r['bus_util']:>8.1%}")
        print(f"  {r['name'] + ' BW':>16} │ "
              f"{r['theo_bw_mbps']:>8,.0f} MB/s │ "
              f"{r['actual_bw_mbps']:>8,.0f} MB/s │ "
              f"{r['bw_eff']:>9.1f}% │")


if __name__ == "__main__":
    main()
