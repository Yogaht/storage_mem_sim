"""Tests for RamulatorMediaSystem — cycle-accurate DRAM simulation backend.

The Ramulator2 Python package must be built and on sys.path:
    pip install -e media/ramulator_wrapper/ramulator2
"""

import unittest
import sys
import os

from ..memory_type import MemoryType, MemoryRequestType
from ..memory_config import MemoryEngineConfig
from ..memory_object import MemoryObject
from ..memory_request import MemoryRequest
from ..memory_engine import MemoryEngine
from ..memory_metrics import MemoryMetrics, MemoryEngineMetrics
from ..media import (
    MediaConfig,
    MediaSystemBackend,
    RamulatorMediaSystem,
    MediaMetrics,
)


_SETUP_MSG = (
    "Ramulator2 Python package is not installed. To install:\n"
    "  git submodule update --init media/ramulator_wrapper/ramulator2\n"
    "  pip install -e media/ramulator_wrapper/ramulator2\n"
    "If the C++ extension has not been built:\n"
    "  cmake -S media/ramulator_wrapper/ramulator2 -B media/ramulator_wrapper/ramulator2/build \\\n"
    "    -DCMAKE_BUILD_TYPE=Release -DRAMULATOR_PYTHON_BINDINGS=ON -DCMAKE_CXX_COMPILER=g++-14\n"
    "  cmake --build media/ramulator_wrapper/ramulator2/build -j$(sysctl -n hw.ncpu)\n"
    "  pip install -e media/ramulator_wrapper/ramulator2\n"
)
_ramulator_available = False
try:
    import ramulator  # noqa: F401
    _ramulator_available = True
except ImportError:
    import sys
    sys.stderr.write(f"\n[WARNING] {_SETUP_MSG}\n")


def _make_ramulator():
    """Create a RamulatorMediaSystem, skipping with install instructions."""
    if not _ramulator_available:
        raise unittest.SkipTest(_SETUP_MSG)
    return RamulatorMediaSystem(MediaConfig(
        media_type=MediaSystemBackend.RAMULATOR,
        
    ))


class TestRamulatorMediaSystemDecomposition(unittest.TestCase):
    """Test MediaRequest decomposition."""

    def setUp(self):
        self.system = _make_ramulator()
        self.mem_config = MemoryEngineConfig()
        self.g = self.system._tx_bytes

    def _make_memory_request(self, addr, size, req_type):
        obj = MemoryObject(addr, size, req_type, self.mem_config)
        return MemoryRequest(memory_object=obj)

    def test_create_media_requests_single(self):
        req = self._make_memory_request(0, self.g * 2, MemoryRequestType.KREAD)
        media_reqs = self.system.create_media_requests([req])
        self.assertEqual(len(media_reqs), 2)
        self.assertEqual(media_reqs[0].addr, 0)
        self.assertEqual(media_reqs[1].addr, self.g)

    def test_create_media_requests_odd_size(self):
        req = self._make_memory_request(0, self.g + 1, MemoryRequestType.KWRITE)
        media_reqs = self.system.create_media_requests([req])
        self.assertEqual(len(media_reqs), 2)

    def test_create_media_requests_multiple(self):
        reqs = [
            self._make_memory_request(0, self.g, MemoryRequestType.KREAD),
            self._make_memory_request(128, self.g * 3, MemoryRequestType.KWRITE),
        ]
        media_reqs = self.system.create_media_requests(reqs)
        self.assertEqual(len(media_reqs), 1 + 3)

    def test_create_media_requests_address_increment(self):
        req = self._make_memory_request(256, self.g * 4, MemoryRequestType.KREAD)
        media_reqs = self.system.create_media_requests([req])
        addrs = [mr.addr for mr in media_reqs]
        self.assertEqual(addrs, [256, 256+self.g, 256+2*self.g, 256+3*self.g])


class TestRamulatorMediaSystemTraceFormat(unittest.TestCase):
    """Test LoadStoreTrace file format."""

    def setUp(self):
        self.system = _make_ramulator()
        self.mem_config = MemoryEngineConfig()

    def _make_memory_request(self, addr, size, req_type):
        obj = MemoryObject(addr, size, req_type, self.mem_config)
        return MemoryRequest(memory_object=obj)

    def test_trace_format_load_store(self):
        import tempfile
        reqs = [
            self._make_memory_request(0, self.system._tx_bytes, MemoryRequestType.KREAD),
            self._make_memory_request(4096, self.system._tx_bytes, MemoryRequestType.KWRITE),
        ]
        media_reqs = self.system.create_media_requests(reqs)

        tmp = tempfile.mkdtemp()
        tp = os.path.join(tmp, "trace.txt")
        self.system._write_trace_file(media_reqs, tp)

        with open(tp) as f:
            lines = f.read().strip().split("\n")
        os.remove(tp)
        os.rmdir(tmp)

        self.assertEqual(lines[0], "LD 0x0")
        self.assertEqual(lines[1], "ST 0x1000")


class TestRamulatorMediaSystemFull(unittest.TestCase):
    """End-to-end test with actual Ramulator2 simulation."""

    def setUp(self):
        self.system = _make_ramulator()
        self.mem_config = MemoryEngineConfig()

    def _make_memory_request(self, addr, size, req_type):
        obj = MemoryObject(addr, size, req_type, self.mem_config)
        return MemoryRequest(memory_object=obj)

    def test_handler_mem_request_empty(self):
        metrics = self.system.handler_mem_request([])
        self.assertEqual(metrics.num_media_reqs, 0)

    def test_handler_mem_request_single(self):
        req = self._make_memory_request(0, self.system._tx_bytes, MemoryRequestType.KREAD)
        metrics = self.system.handler_mem_request([req])
        self.assertIsInstance(metrics, MediaMetrics)
        self.assertEqual(metrics.num_media_reqs, 1)
        self.assertEqual(metrics.num_read_requests, 1)
        self.assertGreater(metrics.cycles, 0)

    def test_handler_mem_request_multiple(self):
        g = self.system._tx_bytes
        reqs = [
            self._make_memory_request(0, g, MemoryRequestType.KREAD),
            self._make_memory_request(4096, g * 2, MemoryRequestType.KWRITE),
        ]
        metrics = self.system.handler_mem_request(reqs)
        self.assertEqual(metrics.num_media_reqs, 1 + 2)
        self.assertEqual(metrics.num_read_requests, 1)
        self.assertEqual(metrics.num_write_requests, 2)

    def test_system_metrics_accumulation(self):
        req = self._make_memory_request(0, self.system._tx_bytes, MemoryRequestType.KREAD)
        self.system.handler_mem_request([req])
        self.system.handler_mem_request([req])
        sys_metrics = self.system.get_system_metrics()
        self.assertEqual(sys_metrics.num_media_reqs, 2)
        self.assertEqual(len(sys_metrics.media_metrics_list), 2)

    def test_e2e_ramulator_simulation(self):
        g = self.system._tx_bytes
        reqs = [
            self._make_memory_request(i * g, g, MemoryRequestType.KREAD)
            for i in range(16)
        ]
        metrics = self.system.handler_mem_request(reqs)
        self.assertEqual(metrics.num_read_requests, 16)
        self.assertGreater(metrics.cycles, 0)
        self.assertGreater(metrics.time, 0)


if __name__ == "__main__":
    unittest.main()
