"""MQSim trace generation — geometry, CWDP addressing, theory, trace file I/O.

NAND geometry parameters (CHANNELS, PAGE_SIZE_BYTES, …) have **no defaults** —
they must be loaded from an MQSim ssdconfig.xml via ``load_from_ssdconfig_xml()``
before any trace functions are called.

Quick start::

    from pymqsim.trace import (load_from_ssdconfig_xml,
                                TraceSliceConfig, write_trace_file)

    load_from_ssdconfig_xml("path/to/ssdconfig.xml")

    cfg = TraceSliceConfig(merge_contiguous=True, request_size=131072)
    total_bytes, lines = write_trace_file(mem_req_list, "trace.txt", cfg)
"""

import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ....memory_request import MemoryRequest
from ....memory_type import MemoryRequestType

# =====================================================================
# Protocol timing table — keyed by <Flash_Comm_Protocol> in ssdconfig.xml
# =====================================================================
# Values from MQSim source: NVDDR2 uses ONFI 3.x timings;
# NVDDR3 uses ONFI 4.x timings (higher data rate, shorter cycles).
#
# SECTOR_SIZE is a universal MQSim constant (512 B per logical block)
# and is set during XML loading even though it has no dedicated XML tag.

_PROTOCOL_TABLE = {
    "NVDDR2": {"CMD_TRANSFER_NS": 290, "DATA_SETUP_NS": 30},
    "NVDDR3": {"CMD_TRANSFER_NS": 200, "DATA_SETUP_NS": 20},
}

# =====================================================================
# Parameters loaded from ssdconfig.xml — all start as None
# =====================================================================

CHANNELS:        int = None
CHIPS_PER_CH:    int = None
DIES_PER_CHIP:   int = None
PLANES_PER_DIE:  int = None
PAGES_PER_BLOCK: int = None
PAGE_SIZE_BYTES: int = None
CHANNEL_BW_MBPS: int = None
NAND_tR_NS:      int = None

# Protocol-dependent (set from <Flash_Comm_Protocol>)
CMD_TRANSFER_NS: int = None
DATA_SETUP_NS:   int = None

# Host / system limits (set from ssdconfig.xml)
IO_QUEUE_DEPTH:       int = None
PCIE_LANE_BW_GBPS:  float = None
PCIE_LANE_COUNT:      int = None

# Universal constant (MQSim internal, always 512)
SECTOR_SIZE: int = 512

# Derived — computed after geometry is loaded
SECTORS_PER_PAGE:      int = None
TOTAL_DIES:            int = None
TOTAL_PLANES:          int = None
TOTAL_CHANNEL_BW_MBPS: int = None

_loaded = False

_GEOMETRY_NAMES = (
    'CHANNELS', 'CHIPS_PER_CH', 'DIES_PER_CHIP', 'PLANES_PER_DIE',
    'PAGES_PER_BLOCK', 'PAGE_SIZE_BYTES', 'CHANNEL_BW_MBPS', 'NAND_tR_NS',
    'CMD_TRANSFER_NS', 'DATA_SETUP_NS',
)


def _require_loaded():
    """Raise RuntimeError if NAND geometry has not been loaded from XML."""
    if not _loaded:
        raise RuntimeError(
            "NAND geometry not loaded. "
            "Call load_from_ssdconfig_xml(ssdconfig_path) first."
        )


def _recompute_derived():
    """Recompute derived constants after geometry has been set."""
    global SECTORS_PER_PAGE, TOTAL_DIES, TOTAL_PLANES, TOTAL_CHANNEL_BW_MBPS, _loaded
    if any(globals()[n] is None for n in _GEOMETRY_NAMES):
        raise RuntimeError("Cannot recompute: some geometry values are still None")
    SECTORS_PER_PAGE = PAGE_SIZE_BYTES // SECTOR_SIZE
    TOTAL_DIES = CHANNELS * CHIPS_PER_CH * DIES_PER_CHIP
    TOTAL_PLANES = TOTAL_DIES * PLANES_PER_DIE
    TOTAL_CHANNEL_BW_MBPS = CHANNELS * CHANNEL_BW_MBPS
    _loaded = True


# =====================================================================
# XML loaders
# =====================================================================

def load_from_ssdconfig_xml(xml_path: str) -> Dict[str, int]:
    """Load NAND geometry from an MQSim ssdconfig.xml.

    All geometry constants are set from the XML.  Raises if any required
    tag is missing or unparseable.

    Returns a dict ``{name: value}`` of all loaded values.
    """
    if not os.path.isfile(xml_path):
        raise FileNotFoundError(f"SSD config not found: {xml_path}")

    tree = ET.parse(xml_path)
    root = tree.getroot()

    dps = root.find('.//Device_Parameter_Set')
    if dps is None:
        raise ValueError("Missing <Device_Parameter_Set> in SSD config")

    fps = root.find('.//Flash_Parameter_Set')
    if fps is None:
        raise ValueError("Missing <Flash_Parameter_Set> in SSD config")

    loaded = {}
    loaded.update(_xml_int('CHANNELS', dps, 'Flash_Channel_Count'))
    loaded.update(_xml_int('CHIPS_PER_CH', dps, 'Chip_No_Per_Channel'))
    loaded.update(_xml_int('CHANNEL_BW_MBPS', dps, 'Channel_Transfer_Rate'))
    loaded.update(_xml_int('DIES_PER_CHIP', fps, 'Die_No_Per_Chip'))
    loaded.update(_xml_int('PLANES_PER_DIE', fps, 'Plane_No_Per_Die'))
    loaded.update(_xml_int('PAGES_PER_BLOCK', fps, 'Page_No_Per_Block'))
    loaded.update(_xml_int('PAGE_SIZE_BYTES', fps, 'Page_Capacity'))
    loaded.update(_xml_int('NAND_tR_NS', fps, 'Page_Read_Latency_LSB'))

    # ---- protocol timing (from <Flash_Comm_Protocol>) ----
    proto_el = dps.find('Flash_Comm_Protocol')
    if proto_el is None or not (proto_el.text and proto_el.text.strip()):
        proto_el = fps.find('Flash_Comm_Protocol')
    protocol = proto_el.text.strip() if (proto_el is not None and proto_el.text) else "NVDDR2"
    if protocol not in _PROTOCOL_TABLE:
        raise ValueError(
            f"Unknown Flash_Comm_Protocol: {protocol!r}. "
            f"Supported: {list(_PROTOCOL_TABLE.keys())}"
        )
    proto_params = _PROTOCOL_TABLE[protocol]
    globals()['CMD_TRANSFER_NS'] = proto_params['CMD_TRANSFER_NS']
    globals()['DATA_SETUP_NS'] = proto_params['DATA_SETUP_NS']
    loaded['CMD_TRANSFER_NS'] = proto_params['CMD_TRANSFER_NS']
    loaded['DATA_SETUP_NS'] = proto_params['DATA_SETUP_NS']

    # ---- host / system limits ----
    hps = root.find('.//Host_Parameter_Set')
    loaded.update(_xml_int('IO_QUEUE_DEPTH', dps, 'IO_Queue_Depth'))
    loaded.update(_xml_float('PCIE_LANE_BW_GBPS', hps, 'PCIe_Lane_Bandwidth'))
    loaded.update(_xml_int('PCIE_LANE_COUNT', hps, 'PCIe_Lane_Count'))

    # Verify all required names are loaded
    missing = [n for n in _GEOMETRY_NAMES if n not in loaded]
    if missing:
        raise ValueError(
            f"SSD config missing required tags: {missing}\n"
            f"File: {xml_path}"
        )

    _recompute_derived()
    return loaded


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
# Theory formulas  (peak IOPS / bandwidth estimation without simulation)
# =====================================================================

def theory_iops(request_size_bytes: int) -> float:
    """Theoretical max IOPS — min of independent resource bounds.

    The tightest upper bound across:

    1. Channel bus bandwidth (pure data-rate limit):
          iops_bw = TOTAL_CHANNEL_BW_MBPS × 1e6 / S

    2. NAND plane array (pure page-read parallelism limit):
          iops_nand = TOTAL_PLANES × 1e9 / (pages_per_io × tR)

    3. CWDP pipeline (combined NAND tR + channel command serialization):
          PipeCycle = tR + CHANNELS × (CMD + Setup + DataOut)
          iops_cwdp = TOTAL_DIES × 1e9 / PipeCycle

    4. Host PCIe bandwidth (if loaded from ssdconfig.xml):
          iops_pcie = PCIE_LANE_BW_GBPS × 1e9 × PCIE_LANE_COUNT / S

    5. Device queue depth (Little's Law, if loaded from ssdconfig.xml):
          per_io_latency_ns = tR + BusTime  (minimum single-request latency)
          iops_qd = IO_QUEUE_DEPTH × 1e9 / per_io_latency_ns

    Bounds (4) and (5) are only applied when the corresponding XML tags
    are present in the loaded ssdconfig.xml.
    """
    _require_loaded()
    pages_per_io = max(1, math.ceil(request_size_bytes / PAGE_SIZE_BYTES))

    # Bound 1 — pure channel bus bandwidth
    iops_bw = TOTAL_CHANNEL_BW_MBPS * 1e6 / request_size_bytes

    # Bound 2 — pure NAND plane parallelism
    iops_nand = TOTAL_PLANES / (pages_per_io * NAND_tR_NS * 1e-9)

    # Bound 3 — CWDP pipeline (NAND + command + data serialization)
    data_out_ns = (request_size_bytes / 2.0) * (2000.0 / CHANNEL_BW_MBPS)
    bus_time_ns = CMD_TRANSFER_NS + DATA_SETUP_NS + data_out_ns
    pipeline_cycle_ns = NAND_tR_NS + CHANNELS * bus_time_ns
    iops_cwdp = TOTAL_DIES * 1e9 / pipeline_cycle_ns

    # Bound 4 — host PCIe bandwidth
    iops_pcie = PCIE_LANE_BW_GBPS * 1e9 * PCIE_LANE_COUNT / request_size_bytes

    # Bound 5 — device queue depth (Little's Law with minimum latency)
    iops_qd = IO_QUEUE_DEPTH * 1e9 / (NAND_tR_NS + bus_time_ns)

    return min(iops_bw, iops_nand, iops_cwdp, iops_pcie, iops_qd)


def theory_bandwidth_mbps(request_size_bytes: int) -> float:
    """Theoretical peak bandwidth (MB/s), capped by total channel bandwidth."""
    _require_loaded()
    bw = theory_iops(request_size_bytes) * request_size_bytes / 1e6
    return min(bw, float(TOTAL_CHANNEL_BW_MBPS))


def theory_bus_utilization(request_size_bytes: int) -> float:
    """Bus utilization U(S) = variable / (fixed + variable).

    U < 0.50 → IOPS-Bound      (bottleneck: NAND tR + CMD overhead)
    U > 0.90 → Bandwidth-Bound  (bottleneck: channel bus bandwidth)
    """
    _require_loaded()
    data_out_ns = (request_size_bytes / 2.0) * (2000.0 / CHANNEL_BW_MBPS)
    fixed_cost = NAND_tR_NS + CHANNELS * (CMD_TRANSFER_NS + DATA_SETUP_NS)
    variable_cost = CHANNELS * data_out_ns
    return variable_cost / (fixed_cost + variable_cost)


# =====================================================================
# Convenience aliases
# =====================================================================

def align_lba(lba_sector: int) -> int:
    """Round LBA down to the nearest NAND page boundary."""
    _require_loaded()
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
                        Must be > 0 and a multiple of 512 (SECTOR_SIZE).
    """
    merge_contiguous: bool = True
    request_size: int = 131072

    def __post_init__(self):
        if self.request_size <= 0:
            raise ValueError(
                f"request_size must be > 0, got {self.request_size}")
        if self.request_size % 512 != 0:
            raise ValueError(
                f"request_size must be a multiple of 512 (SECTOR_SIZE), "
                f"got {self.request_size}")

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

    Pipeline: merge → sector-align → slice by request_size.

    Addresses are sector-aligned: the range [addr, addr+size) is expanded
    to [aligned_down(addr), aligned_up(addr+size)), matching real NVMe
    behaviour where the device only sees sector-granularity LBAs.
    """
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

    # 2. sector-align + slice by request_size
    addr_list, size_list, type_list = [], [], []

    for base_addr, total_size, rtype in chunks:
        aligned_start = (base_addr // SECTOR_SIZE) * SECTOR_SIZE
        aligned_end = ((base_addr + total_size + SECTOR_SIZE - 1)
                       // SECTOR_SIZE) * SECTOR_SIZE
        aligned_total = aligned_end - aligned_start
        line_size = min(aligned_total, cfg.request_size)
        offset = 0
        while offset < aligned_total:
            chunk = min(aligned_total - offset, line_size)
            addr_list.append(aligned_start + offset)
            size_list.append(chunk)
            type_list.append(rtype)
            offset += chunk

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
    All requests arrive at T=0 (MemoryEngine has no concept of time).

    device_id is assigned by simple round-robin (``i % CHANNELS``).
    MQSim's internal Plane Allocation Scheme (CWDP) already handles
    the LPA → Channel mapping, so the trace layer does not need to
    duplicate CWDP logic.  Consecutive trace lines naturally go to
    different device queues, preventing channel clustering.
    """
    _require_loaded()
    addr_list, size_list, type_list = build_trace_lines(mem_req_list, cfg)

    d = os.path.dirname(output_path)
    if d:
        os.makedirs(d, exist_ok=True)

    total_bytes = 0
    line_count = 0
    with open(output_path, "w") as f:
        for i, (addr, size, req_type) in enumerate(
            zip(addr_list, size_list, type_list)
        ):
            lba = addr // SECTOR_SIZE
            sectors = math.ceil(size / SECTOR_SIZE)
            device_id = i % CHANNELS
            f.write(f"0 {device_id} {lba} {sectors} {req_type}\n")
            total_bytes += size
            line_count += 1

    return total_bytes, line_count


# =====================================================================
# Internal helpers
# =====================================================================

def _xml_int(name: str, element: ET.Element, tag: str) -> Dict[str, int]:
    """Extract int from XML element child *tag*, set module global.

    Raises ValueError if the tag is missing or unparseable.
    """
    child = element.find(tag)
    if child is None or not (child.text and child.text.strip()):
        raise ValueError(
            f"Missing or empty tag <{tag}> in SSD config"
        )
    try:
        new_val = int(child.text.strip())
    except ValueError:
        raise ValueError(
            f"Tag <{tag}> has non-integer value: {child.text.strip()!r}"
        ) from None

    globals()[name] = new_val
    return {name: new_val}


def _xml_float(name: str, element: ET.Element, tag: str) -> Dict[str, float]:
    """Extract float from XML element child *tag*, set module global.

    Raises ValueError if the tag is missing or unparseable.
    """
    child = element.find(tag)
    if child is None or not (child.text and child.text.strip()):
        raise ValueError(
            f"Missing or empty tag <{tag}> in SSD config"
        )
    try:
        new_val = float(child.text.strip())
    except ValueError:
        raise ValueError(
            f"Tag <{tag}> has non-float value: {child.text.strip()!r}"
        ) from None

    globals()[name] = new_val
    return {name: new_val}
