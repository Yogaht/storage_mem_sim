"""MQSim trace generation — geometry, CWDP addressing, theory, trace file I/O.

This module combines NAND geometry constants (formerly constants.py), CWDP
address decode, theory performance estimation, and trace file generation in
one place.  Geometry can be reloaded at runtime from ssdconfig.xml via
load_from_ssdconfig_xml().

Quick start::

    from pymqsim.trace import TraceSliceConfig, write_trace_file

    cfg = TraceSliceConfig(merge_contiguous=True, request_size=131072)
    total_bytes, lines = write_trace_file(mem_req_list, "trace.txt", cfg)
"""

import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from memory_request import MemoryRequest

# =====================================================================
# NAND geometry (defaults match bundled default_ssdconfig.xml)
# =====================================================================

CHANNELS        = 8
CHIPS_PER_CH    = 4
DIES_PER_CHIP   = 2
PLANES_PER_DIE  = 2
PAGES_PER_BLOCK = 256
PAGE_SIZE_BYTES = 8192
SECTOR_SIZE     = 512

# Timing
CMD_TRANSFER_NS  = 290
DATA_SETUP_NS    = 30
NAND_tR_NS       = 75_000
CHANNEL_BW_MBPS  = 333

# Derived — call _recompute_derived() after changing any base value above

def _recompute_derived():
    global SECTORS_PER_PAGE, TOTAL_PLANES, TOTAL_CHANNEL_BW_MBPS
    SECTORS_PER_PAGE = PAGE_SIZE_BYTES // SECTOR_SIZE
    TOTAL_PLANES = CHANNELS * CHIPS_PER_CH * DIES_PER_CHIP * PLANES_PER_DIE
    TOTAL_CHANNEL_BW_MBPS = CHANNELS * CHANNEL_BW_MBPS

_recompute_derived()


# =====================================================================
# XML loaders — reload geometry from ssdconfig.xml / workload.xml
# =====================================================================

def load_from_ssdconfig_xml(xml_path: str) -> Dict[str, Tuple[int, int]]:
    """Parse NAND geometry from an MQSim ssdconfig.xml, update module globals.

    Returns ``{name: (old_value, new_value)}`` for every constant that changed.
    Only XML tags that are present & parseable are applied.
    """
    if not os.path.isfile(xml_path):
        raise FileNotFoundError(f"SSD config not found: {xml_path}")

    tree = ET.parse(xml_path)
    root = tree.getroot()
    changes: Dict[str, Tuple[int, int]] = {}

    dps = root.find('.//Device_Parameter_Set')
    if dps is not None:
        changes.update(_xml_int('CHANNELS', dps, 'Flash_Channel_Count'))
        changes.update(_xml_int('CHIPS_PER_CH', dps, 'Chip_No_Per_Channel'))
        changes.update(_xml_int('CHANNEL_BW_MBPS', dps, 'Channel_Transfer_Rate'))

    fps = root.find('.//Flash_Parameter_Set')
    if fps is not None:
        changes.update(_xml_int('DIES_PER_CHIP', fps, 'Die_No_Per_Chip'))
        changes.update(_xml_int('PLANES_PER_DIE', fps, 'Plane_No_Per_Die'))
        changes.update(_xml_int('PAGES_PER_BLOCK', fps, 'Page_No_Per_Block'))
        changes.update(_xml_int('PAGE_SIZE_BYTES', fps, 'Page_Capacity'))
        changes.update(_xml_int('NAND_tR_NS', fps, 'Page_Read_Latency_LSB'))

    if changes:
        _recompute_derived()

    return changes


def load_from_workload_xml(xml_path: str) -> Dict[str, list]:
    """Parse channel / chip / die / plane ID ranges from a workload.xml.

    Returns dict with keys: channel_ids, chip_ids, die_ids, plane_ids.
    """
    if not os.path.isfile(xml_path):
        raise FileNotFoundError(f"Workload config not found: {xml_path}")

    tree = ET.parse(xml_path)
    root = tree.getroot()
    result: Dict[str, list] = {}

    flow = root.find('.//IO_Flow_Parameter_Set_Trace_Based')
    if flow is None:
        return result

    for tag, key in [('Channel_IDs', 'channel_ids'),
                     ('Chip_IDs', 'chip_ids'),
                     ('Die_IDs', 'die_ids'),
                     ('Plane_IDs', 'plane_ids')]:
        el = flow.find(tag)
        if el is not None and el.text:
            try:
                result[key] = [int(x.strip())
                               for x in el.text.split(',') if x.strip()]
            except ValueError:
                result[key] = []

    return result


# =====================================================================
# CWDP address decode  (Channel-Way-Die-Plane)
# =====================================================================

def cwdp_decode(lba_sector: int) -> Tuple[int, int, int, int]:
    """LBA sector → (Channel, Chip, Die, Plane).

    CWDP mapping: consecutive pages cycle through channels first, then
    chips, then dies, then planes.  This is the addressing scheme used
    by MQSim's PAGE_LEVEL address mapping with CWDP plane allocation.
    """
    page = lba_sector // SECTORS_PER_PAGE
    channel = page % CHANNELS
    chip    = (page // CHANNELS) % CHIPS_PER_CH
    die     = (page // (CHANNELS * CHIPS_PER_CH)) % DIES_PER_CHIP
    plane   = (page // (CHANNELS * CHIPS_PER_CH * DIES_PER_CHIP)) % PLANES_PER_DIE
    return channel, chip, die, plane


def cwdp_stride_for_pages(pages_per_request: int) -> int:
    """Smallest stride ≥ *pages_per_request* that is co-prime with CHANNELS.

    Why: when gcd(stride, CHANNELS) = d > 1, consecutive requests only hit
    CHANNELS/d distinct channels → the other channels go idle → bandwidth
    collapses.  A co-prime stride keeps all channels busy.
    """
    stride = pages_per_request
    while math.gcd(stride, CHANNELS) != 1:
        stride += 1
    return stride


# =====================================================================
# Theory formulas  (peak IOPS / bandwidth estimation without simulation)
# =====================================================================

def theory_iops(request_size_bytes: int) -> float:
    """Theoretical max IOPS under CWDP perfect interleaving (8-ch pipeline).

    Pipeline model (NVDDR2):
      BusTime  = CMD(290) + Setup(30) + DataOut
      DataOut  = (S / 2) × (2000 / CHANNEL_BW_MBPS)  ns
      PipeCycle = tR + 8 × BusTime
      IOPS     = 64 / PipeCycle  (ns⁻¹ → s⁻¹)
    """
    data_out_ns = (request_size_bytes / 2.0) * (2000.0 / CHANNEL_BW_MBPS)
    bus_time_ns = CMD_TRANSFER_NS + DATA_SETUP_NS + data_out_ns
    pipeline_cycle_ns = NAND_tR_NS + 8 * bus_time_ns
    return 64e9 / pipeline_cycle_ns


def theory_bandwidth_mbps(request_size_bytes: int) -> float:
    """Theoretical peak bandwidth (MB/s) = IOPS × request_size_bytes / 1e6."""
    return theory_iops(request_size_bytes) * request_size_bytes / 1e6


def theory_bus_utilization(request_size_bytes: int) -> float:
    """Bus utilization U(S) = variable / (fixed + variable).

    U < 0.50 → IOPS-Bound      (bottleneck: NAND tR + CMD overhead)
    U > 0.90 → Bandwidth-Bound  (bottleneck: channel bus bandwidth)
    """
    data_out_ns = (request_size_bytes / 2.0) * (2000.0 / CHANNEL_BW_MBPS)
    fixed_cost = NAND_tR_NS + 8 * (CMD_TRANSFER_NS + DATA_SETUP_NS)
    variable_cost = 8 * data_out_ns
    return variable_cost / (fixed_cost + variable_cost)


# =====================================================================
# Convenience aliases (kept short — prefer inline addr//SECTOR_SIZE)
# =====================================================================

def align_lba(lba_sector: int) -> int:
    """Round LBA down to the nearest NAND page boundary."""
    return (lba_sector // SECTORS_PER_PAGE) * SECTORS_PER_PAGE


def addr_to_lba(addr: int, sector_bytes: int = None) -> int:
    """Byte address → LBA sector index."""
    if sector_bytes is None:
        sector_bytes = SECTOR_SIZE
    return addr // sector_bytes


def size_to_sectors(size: int, sector_bytes: int = None) -> int:
    """Byte size → sector count (ceil)."""
    if sector_bytes is None:
        sector_bytes = SECTOR_SIZE
    return math.ceil(size / sector_bytes)


# =====================================================================
# Trace configuration
# =====================================================================

@dataclass
class TraceSliceConfig:
    """Controls how MemoryRequests become MQSim trace lines.

    merge_contiguous  — merge adjacent same-type requests before slicing.
    request_size      — max bytes per trace line after slicing.
    """
    merge_contiguous: bool = True
    request_size: int = 131072

    @classmethod
    def from_dict(cls, d: Dict | None) -> "TraceSliceConfig":
        if not d:
            return cls()
        return cls(
            merge_contiguous=d.get("merge_contiguous", True),
            request_size=d.get("request_size", 131072),
        )


# =====================================================================
# Trace generation pipeline
# =====================================================================

def merge_sequential(
    mem_req_list: List["MemoryRequest"],
) -> Tuple[List[int], List[int], List[int]]:
    """Merge consecutive same-type MemoryRequests with contiguous addresses.

    Returns (addr_list, size_list, req_type_list) where req_type is 1=read, 0=write.
    """
    if not mem_req_list:
        return [], [], []

    from memory_type import MemoryRequestType

    reads  = [mr for mr in mem_req_list
              if mr.memory_object.req_type == MemoryRequestType.KREAD]
    writes = [mr for mr in mem_req_list
              if mr.memory_object.req_type == MemoryRequestType.KWRITE]

    merged_addr, merged_size, merged_type = [], [], []

    for group, mqsim_type in ((reads, 1), (writes, 0)):
        if not group:
            continue
        group.sort(key=lambda mr: mr.memory_object.addr)

        cur_addr = group[0].memory_object.addr
        cur_size = group[0].memory_object.size
        for req in group[1:]:
            obj = req.memory_object
            if obj.addr == cur_addr + cur_size:
                cur_size += obj.size
            else:
                merged_addr.append(cur_addr)
                merged_size.append(cur_size)
                merged_type.append(mqsim_type)
                cur_addr = obj.addr
                cur_size = obj.size
        merged_addr.append(cur_addr)
        merged_size.append(cur_size)
        merged_type.append(mqsim_type)

    return merged_addr, merged_size, merged_type


def build_trace_lines(
    mem_req_list: List["MemoryRequest"],
    cfg: TraceSliceConfig,
) -> Tuple[List[int], List[int], List[int]]:
    """MemoryRequests → (addr, size, type) trace-line triples.

    Pipeline: merge → slice by request_size → CWDP-aware address distribution.

    Address distribution follows the same strategy as run_experiment.py::

      - Sub-page (line_size < PAGE_SIZE_BYTES): multi-round traversal —
        for each offset within a page, iterate all pages.  Consecutive
        lines hit consecutive pages → consecutive channels.

      - Super-page (line_size >= PAGE_SIZE_BYTES): CWDP-safe stride —
        the gap between consecutive line starting pages is made co-prime
        with CHANNELS, preventing channel collapse (see cwdp_stride_for_pages).
    """
    from memory_type import MemoryRequestType

    if not mem_req_list:
        return [], [], []

    # 1. merge
    if cfg.merge_contiguous:
        chunks = list(zip(*merge_sequential(mem_req_list)))
    else:
        chunks = [
            (mr.memory_object.addr, mr.memory_object.size,
             1 if mr.memory_object.req_type == MemoryRequestType.KREAD else 0)
            for mr in mem_req_list
        ]

    # 2. slice by request_size → determine line count + line size per chunk,
    #    then distribute addresses CWDP-aware
    addr_list, size_list, type_list = [], [], []

    for base_addr, total_size, rtype in chunks:
        line_size = min(total_size, cfg.request_size)
        n_lines = math.ceil(total_size / line_size)

        if n_lines == 1:
            # Single line — page-align the address for consistency
            aligned = align_lba(base_addr // SECTOR_SIZE) * SECTOR_SIZE
            addr_list.append(aligned)
            size_list.append(total_size)
            type_list.append(rtype)
            continue

        start_page = base_addr // PAGE_SIZE_BYTES

        if line_size < PAGE_SIZE_BYTES:
            # ── sub-page: multi-round traversal ──
            #   Round 0: page 0 offset 0, page 1 offset 0, …, page N-1 offset 0
            #   Round 1: page 0 offset 1, page 1 offset 1, …
            #   … until all lines are emitted.
            #   Sequential pages → sequential channels via CWDP mapping.
            lines_per_page = PAGE_SIZE_BYTES // line_size
            num_pages = math.ceil(n_lines / lines_per_page)
            emitted = 0
            for off_idx in range(lines_per_page):
                for pg in range(num_pages):
                    if emitted >= n_lines:
                        break
                    addr = ((start_page + pg) * PAGE_SIZE_BYTES
                            + off_idx * line_size)
                    actual = min(line_size, total_size - emitted * line_size)
                    addr_list.append(addr)
                    size_list.append(actual)
                    type_list.append(rtype)
                    emitted += 1
                if emitted >= n_lines:
                    break
        else:
            # ── super-page: CWDP-safe stride ──
            #   Each line spans pages_per_line pages. If the gap between
            #   consecutive starting pages shares a factor d>1 with CHANNELS,
            #   only CHANNELS/d channels are used → bandwidth collapses.
            #   cwdp_stride_for_pages() bumps the stride to be co-prime
            #   with CHANNELS, adding padding pages that are skipped.
            pages_per_line = line_size // PAGE_SIZE_BYTES
            stride_pages = cwdp_stride_for_pages(pages_per_line)
            for i in range(n_lines):
                addr = (start_page + i * stride_pages) * PAGE_SIZE_BYTES
                actual = min(line_size, total_size - i * line_size)
                addr_list.append(addr)
                size_list.append(actual)
                type_list.append(rtype)

    # The CWDP-aware branches above already generate page-aligned starting
    # addresses.  Sub-page offsets within a page are intentional and must
    # NOT be aligned away.  Only the single-line fallthrough may have an
    # unaligned base_addr — page-align it for consistency.

    return addr_list, size_list, type_list


def write_trace_file(
    mem_req_list: List["MemoryRequest"],
    output_path: str,
    cfg: TraceSliceConfig,
) -> Tuple[int, int]:
    """Build trace lines and write to *output_path*.

    Returns (total_bytes, line_count).

    Trace format (per line):
        <arrival_ns> <device_id> <lba> <sectors> <req_type>
    All requests arrive at T=0 (MemoryEngine has no concept of time);
    device_id cycles 0..15 for MQSim multi-queue compatibility.
    """
    addr_list, size_list, type_list = build_trace_lines(mem_req_list, cfg)

    d = os.path.dirname(output_path)
    if d:
        os.makedirs(d, exist_ok=True)

    total_bytes = 0
    with open(output_path, "w") as f:
        for i, (addr, size, req_type) in enumerate(
                zip(addr_list, size_list, type_list)):
            lba = addr // SECTOR_SIZE
            sectors = math.ceil(size / SECTOR_SIZE)
            f.write(f"0 {i % 16} {lba} {sectors} {req_type}\n")
            total_bytes += size

    return total_bytes, len(addr_list)


# =====================================================================
# Internal helpers
# =====================================================================

def _cwdp_interleave(
    addr: List[int], size: List[int], rtype: List[int],
) -> Tuple[List[int], List[int], List[int]]:
    """Re-order lines so consecutive entries hit distinct channels.

    Groups by CWDP channel, then round-robins across channels.
    This ensures all CHANNELS are utilised when there are multiple
    merged trace lines.
    """
    if len(addr) <= 1:
        return addr, size, rtype

    entries = []
    for a, s, t in zip(addr, size, rtype):
        ch, _, _, _ = cwdp_decode(a // SECTOR_SIZE)
        entries.append((a, s, t, ch))

    entries.sort(key=lambda x: (x[3], x[0]))

    queues: Dict[int, list] = {ch: [] for ch in range(CHANNELS)}
    for a, s, t, ch in entries:
        queues[ch].append((a, s, t))

    out_a, out_s, out_t = [], [], []
    while True:
        emitted = False
        for ch in range(CHANNELS):
            if queues[ch]:
                a, s, t = queues[ch].pop(0)
                out_a.append(a)
                out_s.append(s)
                out_t.append(t)
                emitted = True
        if not emitted:
            break

    return out_a, out_s, out_t


def _xml_int(name: str, element: ET.Element, tag: str) -> Dict[str, Tuple[int, int]]:
    """Extract int from XML element child *tag*, set module global if changed."""
    child = element.find(tag)
    if child is None or not (child.text and child.text.strip()):
        return {}
    try:
        new_val = int(child.text.strip())
    except ValueError:
        return {}

    old_val = globals()[name]
    if old_val == new_val:
        return {}
    globals()[name] = new_val
    return {name: (old_val, new_val)}
