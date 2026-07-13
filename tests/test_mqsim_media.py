"""Tests for MQSimMediaSystem — event-driven SSD simulation backend.

Tests cover:
- Trace file generation and LBA conversion
- handler_mem_request with subprocess fallback
- Configuration defaults

The MQSim binary is optional — tests verify graceful degradation when absent.
"""

import unittest
import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memory_type import MemoryRequestType
from memory_config import MemoryEngineConfig
from memory_object import MemoryObject
from memory_request import MemoryRequest
from media import (
    MediaConfig,
    MediaSystemBackend,
    MQSimMediaSystem,
    MediaMetrics,
)


class TestMQSimMediaSystem(unittest.TestCase):
    """Test MQSim backend (trace generation, LBA logic)."""

    def setUp(self):
        self.media_config = MediaConfig(
            media_type=MediaSystemBackend.MQSIM,
            config_path="dummy_ssd.xml",
        )
        self.system = MQSimMediaSystem(self.media_config)
        self.mem_config = MemoryEngineConfig(media_config=MediaConfig(media_type=MediaSystemBackend.ANALYTIC,bandwidth=100.0,capacity=1.0))

    def tearDown(self):
        tp = self.system.trace_output_path
        td = self.system._trace_dir
        if os.path.exists(tp):
            os.remove(tp)
        if os.path.exists(td):
            os.rmdir(td)

    def _make_memory_request(self, addr, size, req_type):
        obj = MemoryObject(addr, size, req_type, self.mem_config)
        return MemoryRequest(memory_object=obj)

    def test_addr_to_lba(self):
        """Address → LBA conversion uses sector_bytes divisor."""
        self.assertEqual(self.system._addr_to_lba(0), 0)
        self.assertEqual(self.system._addr_to_lba(512), 1)
        self.assertEqual(self.system._addr_to_lba(1024), 2)

    def test_size_to_sectors(self):
        """Size → sectors uses ceiling division."""
        self.assertEqual(self.system._size_to_sectors(0), 0)
        self.assertEqual(self.system._size_to_sectors(512), 1)
        self.assertEqual(self.system._size_to_sectors(513), 2)

    def test_write_trace_file_single_read(self):
        """Single read request generates correct trace line."""
        req = self._make_memory_request(0, 512, MemoryRequestType.KREAD)
        total_bytes = self.system._write_trace_file([req])

        self.assertEqual(total_bytes, 512)
        with open(self.system.trace_output_path, "r") as f:
            line = f.readline().strip()
        self.assertEqual(line, "0 0 0 1 1")  # read = 1

    def test_write_trace_file_single_write(self):
        """Single write request → req_type=0 in trace."""
        req = self._make_memory_request(0, 512, MemoryRequestType.KWRITE)
        self.system._write_trace_file([req])

        with open(self.system.trace_output_path, "r") as f:
            line = f.readline().strip()
        self.assertEqual(line, "0 0 0 1 0")  # write = 0

    def test_write_trace_file_chunking(self):
        """Large request is split at max_sectors_per_nvme_io boundary."""
        large_size = 512 * 1024  # 512 KB → 1024 sectors → 4 chunks of 256
        req = self._make_memory_request(0, large_size, MemoryRequestType.KREAD)
        self.system._write_trace_file([req])

        with open(self.system.trace_output_path, "r") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 4)
        self.assertIn("0 0 0 256 1", lines[0].strip())
        self.assertIn("0 0 256 256 1", lines[1].strip())

    def test_handler_mem_request_empty(self):
        """Empty request list returns zero metrics."""
        metrics = self.system.handler_mem_request([])
        self.assertEqual(metrics.num_media_reqs, 0)

    def test_handler_mem_request_single(self):
        """Single request returns valid metrics."""
        req = self._make_memory_request(0, 512, MemoryRequestType.KREAD)
        metrics = self.system.handler_mem_request([req])

        self.assertIsInstance(metrics, MediaMetrics)
        self.assertEqual(metrics.num_read_requests, 1)
        self.assertEqual(metrics.num_media_reqs, 1)

    def test_system_metrics_accumulation(self):
        """System metrics accumulate across handler_mem_request calls."""
        req = self._make_memory_request(0, 512, MemoryRequestType.KREAD)
        self.system.handler_mem_request([req])
        self.system.handler_mem_request([req])

        sys_metrics = self.system.get_system_metrics()
        self.assertEqual(sys_metrics.num_read_requests, 2)
        self.assertEqual(len(sys_metrics.media_metrics_list), 2)


class TestMQSimMediaConfig(unittest.TestCase):
    """Test MQSim-specific defaults."""

    def test_default_mqsim_sector_bytes(self):
        sys = MQSimMediaSystem(MediaConfig(media_type=MediaSystemBackend.MQSIM))
        self.assertEqual(sys._sector_bytes, 512)
        self.assertEqual(sys._max_sectors_per_nvme_io, 256)


if __name__ == "__main__":
    unittest.main()
