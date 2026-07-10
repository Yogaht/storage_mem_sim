"""MQSim media system — event-driven SSD simulation.

Integrates MQSim by writing trace files and calling the MQSim C++ binary
via subprocess. A native pybind11 binding is planned but not yet implemented.

The MQSim binary must be built first:
    cd media/mqsim_wrapper && pip install -e .
"""

import os
import math
import logging
import tempfile
import subprocess
import xml.etree.ElementTree as ET
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from memory_request import MemoryRequest

from .base_media import BaseMediaSystem
from .media_config import MediaConfig
from .media_metrics import MediaMetrics

logger = logging.getLogger(__name__)

# Path to the MQSim binary (relative to this file)
_MQSIM_BINARY = os.path.join(
    os.path.dirname(__file__), "mqsim_wrapper", "MQSim", "MQSim"
)


class MQSimMediaSystem(BaseMediaSystem):
    """Event-driven SSD simulation backend using MQSim.

    Converts engine-level MemoryRequests into an MQSim-compatible trace
    file, patches the workload XML configuration, runs the MQSim binary
    via subprocess, and parses output for bandwidth/latency metrics.

    Trace format (per line):
        0 0 <start_lba> <sectors> <req_type>
    where req_type: 1 = read, 0 = write.

    TODO: Replace subprocess with native pybind11 bindings for better
          performance and tighter integration.

    Usage:
        config = MediaConfig(
            media_type=MediaSystemBackend.MQSIM,
            config_path="/path/to/ssdconfig.xml",
        )
        sys = MQSimMediaSystem(config)
        sys.workload_config_path = "/path/to/workload.xml"
        metrics = sys.handler_mem_request(mem_req_list)
    """

    # MQSim-specific constants (derived from SSD spec, not user-configurable)
    _sector_bytes: int = 512
    _max_sectors_per_nvme_io: int = 256

    def __init__(self, config: MediaConfig):
        super().__init__(config)
        self.ssd_config = config.config_path
        self.workload_config_path: str = ""
        self._trace_dir = tempfile.mkdtemp(prefix="mqsim_trace_")
        self.trace_output_path = os.path.join(self._trace_dir, "trace.txt")
        self._pybind_available = False
        self._binary_available = os.path.isfile(_MQSIM_BINARY)
        self._init_mqsim()

    def _init_mqsim(self):
        """Check MQSim availability."""
        if self._binary_available:
            logger.info(f"MQSim binary found: {_MQSIM_BINARY}")
        else:
            logger.warning(
                f"MQSim binary not found at {_MQSIM_BINARY}. "
                "Build with: cd media/mqsim_wrapper && pip install -e ."
            )

        # Try pybind11 bindings (future)
        try:
            import mqsim_backend
            self._pybind_available = True
            logger.info("MQSim pybind11 backend found.")
        except ImportError:
            self._pybind_available = False

    def _addr_to_lba(self, addr: int) -> int:
        """Convert byte address to LBA."""
        return addr // self._sector_bytes

    def _size_to_sectors(self, size: int) -> int:
        """Convert byte size to number of sectors (ceiling)."""
        return math.ceil(size / self._sector_bytes)

    def _write_trace_file(
        self, mem_req_list: List["MemoryRequest"]
    ) -> float:
        """Write MemoryRequests to an MQSim trace file.

        Each request is split if it exceeds max_sectors_per_nvme_io.
        Format: 0 0 <start_lba> <sectors> <req_type>

        Args:
            mem_req_list: List of MemoryRequest objects.

        Returns:
            Total bytes transferred.
        """
        max_sectors = self._max_sectors_per_nvme_io
        total_bytes = 0

        trace_dir = os.path.dirname(self.trace_output_path)
        if trace_dir:
            os.makedirs(trace_dir, exist_ok=True)

        with open(self.trace_output_path, "w") as f:
            for mem_req in mem_req_list:
                obj = mem_req.memory_object
                addr = obj.addr
                remaining_size = obj.size

                from memory_type import MemoryRequestType
                mqsim_req_type = 1 if obj.req_type == MemoryRequestType.KREAD else 0

                while remaining_size > 0:
                    chunk_size = min(
                        remaining_size,
                        max_sectors * self._sector_bytes,
                    )
                    lba = self._addr_to_lba(addr)
                    sectors = self._size_to_sectors(chunk_size)

                    f.write(f"0 0 {lba} {sectors} {mqsim_req_type}\n")

                    addr += chunk_size
                    remaining_size -= chunk_size
                    total_bytes += chunk_size

        return total_bytes

    def _patch_workload_xml(self):
        """Patch the file path in the workload XML configuration."""
        if not self.workload_config_path:
            logger.warning("No workload_config_path set; skipping XML patch.")
            return

        try:
            tree = ET.parse(self.workload_config_path)
            root = tree.getroot()
            for elem in root.iter("File_Path"):
                elem.text = self.trace_output_path
            tree.write(
                self.workload_config_path,
                xml_declaration=True,
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Failed to patch workload XML: {e}")

    def handler_mem_request(
        self, mem_req_list: List["MemoryRequest"]
    ) -> MediaMetrics:
        """Run SSD simulation on the given memory requests.

        1. Write MemoryRequests to trace file.
        2. Patch workload XML with trace path.
        3. Run MQSim binary via subprocess.
        4. Parse output for time estimation.

        Args:
            mem_req_list: List of MemoryRequest objects.

        Returns:
            MediaMetrics with time and request counts.
        """
        from memory_type import MemoryRequestType

        num_read = sum(
            1 for mr in mem_req_list
            if mr.memory_object.req_type == MemoryRequestType.KREAD
        )
        num_write = len(mem_req_list) - num_read

        total_bytes = self._write_trace_file(mem_req_list)
        self._patch_workload_xml()

        total_time = 0.0

        # Try pybind11 bindings first (future), then fall back to subprocess
        if self._pybind_available:
            try:
                import mqsim_backend
                result = mqsim_backend.MQSimAdapter.run(
                    self.ssd_config, self.workload_config_path
                )
                bandwidth_bps = result.bandwidth_bytes_per_sec
                if bandwidth_bps > 0:
                    total_time = total_bytes / bandwidth_bps
            except Exception as e:
                logger.error(f"MQSim pybind11 run failed: {e}")
        elif self._binary_available and self.ssd_config and self.workload_config_path:
            try:
                cmd = [
                    _MQSIM_BINARY,
                    "-i", self.ssd_config,
                    "-w", self.workload_config_path,
                ]
                logger.info(f"Running MQSim: {' '.join(cmd)}")
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5-minute timeout
                )
                # MQSim output parsing is TODO — for now use a rough estimate
                # based on reported bandwidth
                logger.debug(f"MQSim stdout (last 500 chars): {result.stdout[-500:]}")
                if result.returncode != 0:
                    logger.error(f"MQSim exited with code {result.returncode}")
                total_time = 0.0  # Placeholder until output parsing is implemented
            except subprocess.TimeoutExpired:
                logger.error("MQSim simulation timed out")
            except Exception as e:
                logger.error(f"MQSim subprocess failed: {e}")

        metrics = MediaMetrics(
            num_read_requests=num_read,
            num_write_requests=num_write,
            num_other_requests=0,
            cycles=0,
            num_media_reqs=len(mem_req_list),
            time=total_time,
        )

        self.system_metrics.update_from_media(metrics)
        return metrics
