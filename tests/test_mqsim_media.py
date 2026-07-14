"""Tests for MQSimMediaSystem 鈥?event-driven SSD simulation backend.

Tests cover:
- Address merging (sequential, non-sequential, mixed types)
- Trace file generation and LBA conversion
- SSD config XML generation
- Workload XML generation
- handler_mem_request with fallback
- Bandwidth-bound vs IOPS-bound scenarios
- Configuration defaults

The MQSim binary is optional 鈥?tests verify graceful degradation when absent.
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


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_memory_request(addr, size, req_type):
    """Create a MemoryRequest for testing."""
    config = MemoryEngineConfig()
    obj = MemoryObject(addr, size, req_type, config)
    return MemoryRequest(memory_object=obj)


def _make_media_config(**kwargs):
    """Create a MediaConfig with MQSim defaults for testing."""
    defaults = {
        "media_type": MediaSystemBackend.MQSIM,
        "ssd_config_path": "",
        "workload_config_path": "",
        "request_size_bytes": 131072,
    }
    defaults.update(kwargs)
    return MediaConfig(**defaults)


# ------------------------------------------------------------------
# Address Merging Tests
# ------------------------------------------------------------------

class TestAddressMerging(unittest.TestCase):
    """Test the sequential address merge algorithm."""

    def setUp(self):
        self.system = MQSimMediaSystem(_make_media_config())

    def tearDown(self):
        pass

    def test_merge_empty(self):
        """Empty input returns empty lists."""
        addr, size, rtype = self.system.merge_sequential([])
        self.assertEqual(addr, [])
        self.assertEqual(size, [])
        self.assertEqual(rtype, [])

    def test_merge_single(self):
        """Single request passes through unchanged."""
        req = _make_memory_request(0, 512, MemoryRequestType.KREAD)
        addr, size, rtype = self.system.merge_sequential([req])
        self.assertEqual(addr, [0])
        self.assertEqual(size, [512])
        self.assertEqual(rtype, [1])  # read = 1

    def test_merge_consecutive_reads(self):
        """Consecutive reads with contiguous addresses are merged."""
        reqs = [
            _make_memory_request(0, 512, MemoryRequestType.KREAD),
            _make_memory_request(512, 512, MemoryRequestType.KREAD),
            _make_memory_request(1024, 512, MemoryRequestType.KREAD),
        ]
        addr, size, rtype = self.system.merge_sequential(reqs)
        self.assertEqual(addr, [0])
        self.assertEqual(size, [1536])
        self.assertEqual(rtype, [1])

    def test_merge_consecutive_writes(self):
        """Consecutive writes with contiguous addresses are merged."""
        reqs = [
            _make_memory_request(0, 512, MemoryRequestType.KWRITE),
            _make_memory_request(512, 512, MemoryRequestType.KWRITE),
        ]
        addr, size, rtype = self.system.merge_sequential(reqs)
        self.assertEqual(addr, [0])
        self.assertEqual(size, [1024])
        self.assertEqual(rtype, [0])  # write = 0

    def test_merge_gap(self):
        """Non-contiguous addresses are NOT merged."""
        reqs = [
            _make_memory_request(0, 512, MemoryRequestType.KREAD),
            _make_memory_request(2048, 512, MemoryRequestType.KREAD),
        ]
        addr, size, rtype = self.system.merge_sequential(reqs)
        self.assertEqual(addr, [0, 2048])
        self.assertEqual(size, [512, 512])
        self.assertEqual(rtype, [1, 1])

    def test_merge_mixed_types_not_merged(self):
        """Read and write with contiguous addresses are NOT merged."""
        reqs = [
            _make_memory_request(0, 512, MemoryRequestType.KREAD),
            _make_memory_request(512, 512, MemoryRequestType.KWRITE),
        ]
        addr, size, rtype = self.system.merge_sequential(reqs)
        self.assertEqual(len(addr), 2)
        # Reads (1) are processed before writes (0) in merge_sequential
        self.assertEqual(rtype, [1, 0])

    def test_merge_out_of_order_sorted(self):
        """Out-of-order requests are sorted by address within each type group."""
        reqs = [
            _make_memory_request(1024, 512, MemoryRequestType.KREAD),
            _make_memory_request(0, 512, MemoryRequestType.KREAD),
            _make_memory_request(512, 512, MemoryRequestType.KREAD),
        ]
        addr, size, rtype = self.system.merge_sequential(reqs)
        self.assertEqual(addr, [0])
        self.assertEqual(size, [1536])

    def test_merge_mixed_runs(self):
        """Multiple runs of reads and writes."""
        reqs = [
            _make_memory_request(0, 512, MemoryRequestType.KREAD),
            _make_memory_request(512, 512, MemoryRequestType.KREAD),
            _make_memory_request(4096, 512, MemoryRequestType.KWRITE),
            _make_memory_request(4608, 512, MemoryRequestType.KWRITE),
            _make_memory_request(8192, 512, MemoryRequestType.KREAD),
        ]
        addr, size, rtype = self.system.merge_sequential(reqs)
        # Reads (type=1) processed first, then writes (type=0)
        # Read group 1: addr=0, size=1024 (merged from 0+512)
        # Read group 2: addr=8192, size=512 (standalone)
        # Write group: addr=4096, size=1024 (merged from 4096+4608)
        self.assertEqual(rtype, [1, 1, 0])
        self.assertEqual(len(addr), 3)
        # All reads (type=1) emitted before writes (type=0)
        self.assertEqual(addr, [0, 8192, 4096])

    def test_large_merge(self):
        """Large number of consecutive requests all merge into one."""
        n = 100
        reqs = [
            _make_memory_request(i * 512, 512, MemoryRequestType.KREAD)
            for i in range(n)
        ]
        addr, size, rtype = self.system.merge_sequential(reqs)
        self.assertEqual(len(addr), 1)
        self.assertEqual(size[0], n * 512)

    def test_no_merge_different_sizes(self):
        """Different sizes still merge if contiguous."""
        reqs = [
            _make_memory_request(0, 1024, MemoryRequestType.KREAD),
            _make_memory_request(1024, 2048, MemoryRequestType.KREAD),
        ]
        addr, size, rtype = self.system.merge_sequential(reqs)
        self.assertEqual(len(addr), 1)
        self.assertEqual(size[0], 3072)


# ------------------------------------------------------------------
# Trace Generation Tests
# ------------------------------------------------------------------

class TestTraceGeneration(unittest.TestCase):
    """Test MQSim trace file generation."""

    def setUp(self):
        from media.mqsim_wrapper.pymqsim import TraceSliceConfig
        self.tmp_dir = tempfile.mkdtemp(prefix="mqsim_test_")
        # Use a small request_size so tests can verify slicing
        self.cfg_merge = TraceSliceConfig(
            merge_contiguous=True, request_size=8192)
        self.cfg_nomerge = TraceSliceConfig(
            merge_contiguous=False, request_size=8192)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_addr_to_lba(self):
        """Address → LBA conversion."""
        from media.mqsim_wrapper.pymqsim import addr_to_lba
        self.assertEqual(addr_to_lba(0), 0)
        self.assertEqual(addr_to_lba(512), 1)
        self.assertEqual(addr_to_lba(1024), 2)
        self.assertEqual(addr_to_lba(511), 0)

    def test_size_to_sectors(self):
        """Size → sectors (ceiling)."""
        from media.mqsim_wrapper.pymqsim import size_to_sectors
        self.assertEqual(size_to_sectors(0), 0)
        self.assertEqual(size_to_sectors(512), 1)
        self.assertEqual(size_to_sectors(513), 2)
        self.assertEqual(size_to_sectors(1024), 2)

    def test_write_trace_single_read(self):
        """Single read < request_size fits in one line."""
        from media.mqsim_wrapper.pymqsim import write_trace_file
        req = _make_memory_request(0, 512, MemoryRequestType.KREAD)
        path = os.path.join(self.tmp_dir, "trace.txt")
        total_bytes, line_count = write_trace_file([req], path, self.cfg_merge)

        self.assertEqual(total_bytes, 512)
        self.assertEqual(line_count, 1)
        with open(path) as f:
            line = f.readline().strip()
        self.assertEqual(line, "0 0 0 1 1")

    def test_write_trace_single_write(self):
        """Single write: req_type=0."""
        from media.mqsim_wrapper.pymqsim import write_trace_file
        req = _make_memory_request(0, 512, MemoryRequestType.KWRITE)
        path = os.path.join(self.tmp_dir, "trace.txt")
        write_trace_file([req], path, self.cfg_merge)

        with open(path) as f:
            line = f.readline().strip()
        self.assertEqual(line, "0 0 0 1 0")

    def test_write_trace_merged(self):
        """Merge two contiguous reads, still < request_size."""
        from media.mqsim_wrapper.pymqsim import write_trace_file
        reqs = [
            _make_memory_request(0, 4096, MemoryRequestType.KREAD),
            _make_memory_request(4096, 4096, MemoryRequestType.KREAD),
        ]
        path = os.path.join(self.tmp_dir, "trace.txt")
        total_bytes, line_count = write_trace_file(reqs, path, self.cfg_merge)

        self.assertEqual(total_bytes, 8192)
        self.assertEqual(line_count, 1)  # merged: 0+8192 < request_size
        with open(path) as f:
            line = f.readline().strip()
        self.assertEqual(line, "0 0 0 16 1")  # 8192B = 16 sectors

    def test_write_trace_sliced(self):
        """Large merged chunk is sliced by request_size."""
        from media.mqsim_wrapper.pymqsim import write_trace_file
        reqs = [
            _make_memory_request(0, 8192, MemoryRequestType.KREAD),
            _make_memory_request(8192, 8192, MemoryRequestType.KREAD),
        ]  # merge → 16384 bytes, sliced into 2 lines of 8192
        path = os.path.join(self.tmp_dir, "trace.txt")
        total_bytes, line_count = write_trace_file(reqs, path, self.cfg_merge)

        self.assertEqual(total_bytes, 16384)
        self.assertEqual(line_count, 2)
        with open(path) as f:
            lines = f.read().strip().split("\n")
        # Lines are in address order (no CWDP interleave applied)
        sectors_per_chunk = 8192 // 512  # 16 sectors
        self.assertIn("0 0 0 16 1", lines)
        self.assertIn("0 1 16 16 1", lines)

    def test_write_trace_iops_mode_no_merge(self):
        """No merge: each request sliced individually by request_size."""
        from media.mqsim_wrapper.pymqsim import write_trace_file
        # Two 10KB requests → each sliced into 8KB + 2KB = 4 lines total
        reqs = [
            _make_memory_request(0, 10240, MemoryRequestType.KREAD),
            _make_memory_request(20480, 10240, MemoryRequestType.KREAD),
        ]
        path = os.path.join(self.tmp_dir, "trace.txt")
        total_bytes, line_count = write_trace_file(
            reqs, path, self.cfg_nomerge)

        self.assertEqual(total_bytes, 20480)
        # Each 10KB request: 8192 + 2048 = 2 lines each, total 4 lines
        self.assertEqual(line_count, 4)

    def test_write_trace_partial_merge(self):
        """Only contiguous requests merge; gaps prevent merging."""
        from media.mqsim_wrapper.pymqsim import write_trace_file
        reqs = [
            _make_memory_request(0, 4096, MemoryRequestType.KREAD),
            _make_memory_request(4096, 4096, MemoryRequestType.KREAD),  # contiguous → merged
            _make_memory_request(16384, 4096, MemoryRequestType.KREAD),  # gap → separate
        ]
        path = os.path.join(self.tmp_dir, "trace.txt")
        total_bytes, line_count = write_trace_file(reqs, path, self.cfg_merge)

        self.assertEqual(total_bytes, 12288)
        # Merged chunk [0, 8192] → 1 line; standalone [16384, 4096] → 1 line
        self.assertEqual(line_count, 2)

    def test_write_trace_large_request(self):
        """Large request is sliced into request_size chunks."""
        from media.mqsim_wrapper.pymqsim import write_trace_file
        large_size = 32768  # exactly 4 × 8192
        req = _make_memory_request(0, large_size, MemoryRequestType.KREAD)
        path = os.path.join(self.tmp_dir, "trace.txt")
        total_bytes, line_count = write_trace_file([req], path, self.cfg_merge)

        self.assertEqual(total_bytes, large_size)
        self.assertEqual(line_count, 4)  # 32768 / 8192 = 4


# ------------------------------------------------------------------
# SSD Config XML Tests
# ------------------------------------------------------------------
# Workload XML Tests
# ------------------------------------------------------------------

class TestMQSimWorkload(unittest.TestCase):
    """Test workload XML generation via mqsim library."""

    def test_default_workload(self):
        """Default workload factory returns an MQSimWorkload instance."""
        from media.mqsim_wrapper.pymqsim import MQSimWorkload
        wl = MQSimWorkload.default()
        self.assertIsInstance(wl, MQSimWorkload)

    def test_generate_workload_xml(self):
        """generate_workload_xml creates a valid workload XML file."""
        from media.mqsim_wrapper.pymqsim import generate_workload_xml
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "workload.xml")
            generate_workload_xml("/tmp/test_trace.txt", path)
            self.assertTrue(os.path.isfile(path))
            import xml.etree.ElementTree as ET
            tree = ET.parse(path)
            root = tree.getroot()
            self.assertEqual(root.tag, "MQSim_IO_Scenarios")
            # Check trace path is set
            file_path = root.find(".//File_Path")
            self.assertIsNotNone(file_path)
            self.assertEqual(file_path.text, "/tmp/test_trace.txt")

    def test_build_trace_based_backward_compat(self):
        """MQSimWorkload.build_trace_based() still works (backward compat)."""
        from media.mqsim_wrapper.pymqsim import MQSimWorkload
        wl = MQSimWorkload.default()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "workload.xml")
            wl.build_trace_based("/tmp/test_trace.txt", path)
            self.assertTrue(os.path.isfile(path))
            import xml.etree.ElementTree as ET
            tree = ET.parse(path)
            root = tree.getroot()
            self.assertEqual(root.tag, "MQSim_IO_Scenarios")
            file_path = root.find(".//File_Path")
            self.assertIsNotNone(file_path)
            self.assertEqual(file_path.text, "/tmp/test_trace.txt")


# ------------------------------------------------------------------
# Constants XML loading tests
# ------------------------------------------------------------------

class TestConstantsXMLLoading(unittest.TestCase):
    """Test NAND geometry loading from MQSim ssdconfig.xml / workload.xml."""

    @classmethod
    def setUpClass(cls):
        cls._default_ssdconfig = os.path.join(
            os.path.dirname(__file__), "..", "media", "mqsim_wrapper",
            "default_ssdconfig.xml")
        cls._default_workload = os.path.join(
            os.path.dirname(__file__), "..", "media", "mqsim_wrapper",
            "default_workload.xml")

    def setUp(self):
        """Reset _loaded flag so each test starts fresh."""
        import media.mqsim_wrapper.pymqsim.trace as C
        self.trace = C
        C._loaded = False

    def test_load_from_ssdconfig_xml(self):
        """Geometry is correctly parsed from default_ssdconfig.xml."""
        C = self.trace
        loaded = C.load_from_ssdconfig_xml(self._default_ssdconfig)

        self.assertIn('CHANNELS', loaded)
        self.assertEqual(C.CHANNELS, 8)
        self.assertEqual(C.CHIPS_PER_CH, 4)
        self.assertEqual(C.DIES_PER_CHIP, 2)
        self.assertEqual(C.PLANES_PER_DIE, 2)
        self.assertEqual(C.PAGES_PER_BLOCK, 256)
        self.assertEqual(C.PAGE_SIZE_BYTES, 8192)
        self.assertEqual(C.CHANNEL_BW_MBPS, 333)
        self.assertEqual(C.NAND_tR_NS, 75000)
        self.assertTrue(C._loaded)

    def test_load_from_ssdconfig_derived_values(self):
        """Derived values are recomputed after loading."""
        C = self.trace
        C.load_from_ssdconfig_xml(self._default_ssdconfig)

        self.assertEqual(C.SECTORS_PER_PAGE, 8192 // 512)  # 16
        self.assertEqual(C.TOTAL_PLANES, 8 * 4 * 2 * 2)    # 128
        self.assertEqual(C.TOTAL_CHANNEL_BW_MBPS, 8 * 333) # 2664

    def test_unloaded_raises(self):
        """Functions raise RuntimeError if geometry not loaded."""
        C = self.trace
        C._loaded = False
        with self.assertRaises(RuntimeError):
            C.align_lba(0)

    def test_load_missing_file_raises(self):
        """Missing XML file raises FileNotFoundError."""
        C = self.trace
        with self.assertRaises(FileNotFoundError):
            C.load_from_ssdconfig_xml("/nonexistent/ssdconfig.xml")

    def test_default_args_use_current_sector_size(self):
        """addr_to_lba / size_to_sectors work after XML load."""
        C = self.trace
        C.load_from_ssdconfig_xml(self._default_ssdconfig)

        self.assertEqual(C.addr_to_lba(0), 0)
        self.assertEqual(C.addr_to_lba(512), 1)
        self.assertEqual(C.addr_to_lba(1024), 2)
        self.assertEqual(C.size_to_sectors(0), 0)
        self.assertEqual(C.size_to_sectors(512), 1)
        self.assertEqual(C.size_to_sectors(513), 2)

    def test_load_from_workload_xml(self):
        """Resource IDs are correctly parsed from default_workload.xml."""
        C = self.trace
        res = C.load_from_workload_xml(self._default_workload)

        self.assertIn('channel_ids', res)
        self.assertIn('chip_ids', res)
        self.assertIn('die_ids', res)
        self.assertIn('plane_ids', res)
        self.assertEqual(res['channel_ids'], [0, 1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(res['chip_ids'], [0, 1, 2, 3])
        self.assertEqual(res['die_ids'], [0, 1])
        self.assertEqual(res['plane_ids'], [0, 1])


# ------------------------------------------------------------------
# MQSimMediaSystem handler_mem_request Tests
# ------------------------------------------------------------------

class TestMQSimMediaSystemHandler(unittest.TestCase):
    """Test handler_mem_request without MQSim binary."""

    def setUp(self):
        self.system = MQSimMediaSystem(_make_media_config(bandwidth=3.5))

    def _make_req(self, addr, size, req_type):
        return _make_memory_request(addr, size, req_type)

    def test_handler_empty(self):
        """Empty request list returns zero metrics (no binary needed)."""
        metrics = self.system.handler_mem_request([])
        self.assertEqual(metrics.num_media_reqs, 0)
        self.assertEqual(metrics.time, 0.0)

    def test_handler_raises_without_module(self):
        """handler_mem_request raises RuntimeError when _mqsim not built."""
        req = self._make_req(0, 512, MemoryRequestType.KREAD)

        self.system._mqsim_ready = False
        with self.assertRaises(RuntimeError) as ctx:
            self.system.handler_mem_request([req])
        self.assertIn("_mqsim", str(ctx.exception))
        self.assertIn("pip install -e", str(ctx.exception))

    def test_handler_raises_shows_fix_instructions(self):
        """Error message includes build instructions."""
        req = self._make_req(0, 512, MemoryRequestType.KREAD)

        self.system._mqsim_ready = False
        with self.assertRaises(RuntimeError) as ctx:
            self.system.handler_mem_request([req])
        msg = str(ctx.exception)
        self.assertIn("pip install -e", msg)
        self.assertIn("media/mqsim_wrapper", msg)


# ------------------------------------------------------------------
# Trace line-count & IOPS cross-validation tests
# ------------------------------------------------------------------

class TestTraceLineCount(unittest.TestCase):
    """Verify trace line counts for contiguous / non-contiguous inputs."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mqsim_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # -- helpers -------------------------------------------------------

    def _write_and_count(self, reqs, merge, req_size=8192):
        from media.mqsim_wrapper.pymqsim import write_trace_file, TraceSliceConfig
        cfg = TraceSliceConfig(merge_contiguous=merge, request_size=req_size)
        path = os.path.join(self.tmp_dir, "trace.txt")
        total_bytes, line_count = write_trace_file(reqs, path, cfg)
        return total_bytes, line_count, path

    # -- contiguous ----------------------------------------------------

    def test_contiguous_all_merge(self):
        """10 contiguous 4KB reads → merge → 1 chunk of 40KB → sliced by 8KB → 5 lines."""
        reqs = [_make_memory_request(i * 4096, 4096, MemoryRequestType.KREAD)
                for i in range(10)]
        total_bytes, line_count, _ = self._write_and_count(reqs, merge=True)

        self.assertEqual(total_bytes, 40960)
        self.assertEqual(line_count, 5)  # 40KB / 8KB = 5

    def test_contiguous_mixed_types_not_merged(self):
        """Read+write at contiguous addresses → NOT merged across types."""
        reqs = [
            _make_memory_request(0, 4096, MemoryRequestType.KREAD),
            _make_memory_request(4096, 4096, MemoryRequestType.KWRITE),
        ]
        total_bytes, line_count, _ = self._write_and_count(reqs, merge=True)

        self.assertEqual(total_bytes, 8192)
        # Each 4KB < 8KB request_size → 2 lines (read + write separate)
        self.assertEqual(line_count, 2)

    def test_contiguous_with_gap_partial_merge(self):
        """addr=[0,4K,16K], sizes=[4K,4K,4K] → merge first 2, gap, standalone."""
        reqs = [
            _make_memory_request(0, 4096, MemoryRequestType.KREAD),
            _make_memory_request(4096, 4096, MemoryRequestType.KREAD),
            _make_memory_request(16384, 4096, MemoryRequestType.KREAD),
        ]
        total_bytes, line_count, _ = self._write_and_count(reqs, merge=True)

        self.assertEqual(total_bytes, 12288)
        # merged [0, 8KB] → 1 line; standalone [16KB, 4KB] → 1 line
        self.assertEqual(line_count, 2)

    def test_contiguous_large_sliced(self):
        """1 request of 32KB → sliced by 8KB → 4 lines."""
        reqs = [_make_memory_request(0, 32768, MemoryRequestType.KREAD)]
        total_bytes, line_count, _ = self._write_and_count(reqs, merge=True)

        self.assertEqual(total_bytes, 32768)
        self.assertEqual(line_count, 4)

    def test_contiguous_merge_then_slice(self):
        """3 contiguous 8KB reads → merge to 24KB → slice by 8KB → 3 lines."""
        reqs = [
            _make_memory_request(0, 8192, MemoryRequestType.KREAD),
            _make_memory_request(8192, 8192, MemoryRequestType.KREAD),
            _make_memory_request(16384, 8192, MemoryRequestType.KREAD),
        ]
        total_bytes, line_count, _ = self._write_and_count(reqs, merge=True)

        self.assertEqual(total_bytes, 24576)
        self.assertEqual(line_count, 3)

    # -- non-contiguous (no merge) -------------------------------------

    def test_non_contiguous_no_merge(self):
        """Non-contiguous: each request sliced independently."""
        reqs = [
            _make_memory_request(0, 10240, MemoryRequestType.KREAD),
            _make_memory_request(50000, 10240, MemoryRequestType.KREAD),
        ]
        total_bytes, line_count, _ = self._write_and_count(reqs, merge=False)

        self.assertEqual(total_bytes, 20480)
        # Each 10KB → 8KB + 2KB = 2 lines → 4 lines total
        self.assertEqual(line_count, 4)

    def test_non_contiguous_small_requests(self):
        """32 small (4KB) non-contiguous requests, each < request_size."""
        reqs = [_make_memory_request(i * 65536, 4096, MemoryRequestType.KREAD)
                for i in range(32)]
        total_bytes, line_count, _ = self._write_and_count(reqs, merge=False)

        self.assertEqual(total_bytes, 32 * 4096)
        self.assertEqual(line_count, 32)  # each fits in one line

    # -- IOPS computation from trace geometry --------------------------

    def test_iops_from_trace_geometry(self):
        """IOPS = line_count / (total_bytes / bandwidth).

        For a trace with known bytes and line count, with a hypothetical
        bandwidth, the expected IOPS = line_count * bandwidth / total_bytes.
        """
        reqs = [_make_memory_request(i * 4096, 4096, MemoryRequestType.KREAD)
                for i in range(16)]
        total_bytes, line_count, _ = self._write_and_count(reqs, merge=True)

        # 16 × 4KB = 64KB merged → 8KB slices → 8 lines
        self.assertEqual(total_bytes, 65536)
        self.assertEqual(line_count, 8)

        # Theoretical: if bandwidth = 3.5 GB/s, time = 64KB / 3.5GB/s = 18.7us
        # IOPS = 8 / 18.7us ≈ 427k
        # Formula: IOPS = line_count * bandwidth / total_bytes
        bw_hypothetical = 3.5e9
        expected_time = total_bytes / bw_hypothetical
        expected_iops = line_count / expected_time
        self.assertAlmostEqual(expected_iops, line_count * bw_hypothetical / total_bytes)
        self.assertGreater(expected_iops, 0)


# ------------------------------------------------------------------
# Full-pipeline IOPS validation (requires MQSim engine)
# ------------------------------------------------------------------

_mqsim_available = False
try:
    from media.mqsim_wrapper.pymqsim import check_mqsim_available
    _mqsim_available = check_mqsim_available()
except Exception:
    pass


@unittest.skipUnless(_mqsim_available, "MQSim engine not built")
class TestFullPipelineIOPS(unittest.TestCase):
    """End-to-end: handler_mem_request → verify IOPS in metrics."""

    def setUp(self):
        self.system = MQSimMediaSystem(_make_media_config(bandwidth=3.5))

    def _make_req(self, addr, size, req_type):
        return _make_memory_request(addr, size, req_type)

    def test_sequential_bandwidth_metrics_consistency(self):
        """Sequential: bandwidth * time ≈ total_bytes, iops > 0."""
        n = 64
        reqs = [self._make_req(i * 131072, 131072, MemoryRequestType.KREAD)
                for i in range(n)]
        self.system.trace_config = type(self.system.trace_config)(
            merge_contiguous=True, request_size=131072)

        metrics = self.system.handler_mem_request(reqs)

        self.assertGreater(metrics.time, 0)
        self.assertGreater(metrics.bandwidth, 0)
        self.assertGreater(metrics.iops, 0)
        # bandwidth * time ≈ total_bytes
        total_bytes = n * 131072
        computed_bytes = metrics.bandwidth * metrics.time
        self.assertAlmostEqual(computed_bytes / total_bytes, 1.0, delta=0.3)

    def test_random_iops_metrics_consistency(self):
        """Random IOPS: iops * time ≈ num_media_reqs (approx)."""
        n = 256
        reqs = [self._make_req(i * 131072, 4096, MemoryRequestType.KREAD)
                for i in range(n)]
        self.system.trace_config = type(self.system.trace_config)(
            merge_contiguous=False, request_size=4096)

        metrics = self.system.handler_mem_request(reqs)

        self.assertGreater(metrics.time, 0)
        self.assertGreater(metrics.iops, 0)
        self.assertEqual(metrics.num_media_reqs, n)
        # IOPS * time ≈ request count (all-at-once arrival)
        iops_requests = metrics.iops * metrics.time
        self.assertAlmostEqual(iops_requests / n, 1.0, delta=0.5)


# ------------------------------------------------------------------
# Native vs Binary cross-validation (requires both engines)
# ------------------------------------------------------------------

_mqsim_binary_available = False
try:
    _mqsim_binary = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..",
                     "media", "mqsim_wrapper", "MQSim", "MQSim"))
    if os.path.isfile(_mqsim_binary) and os.access(_mqsim_binary, os.X_OK):
        _mqsim_binary_available = True
except Exception:
    pass


@unittest.skipUnless(_mqsim_available and _mqsim_binary_available,
                     "Both native _mqsim and MQSim binary required")
class TestNativeVsBinary(unittest.TestCase):
    """Cross-validate: native pybind11 result == MQSim binary result."""

    def setUp(self):
        self.system = MQSimMediaSystem(_make_media_config(bandwidth=3.5))
        # Resolve paths used by both native and binary
        ssd = (self.system.config.ssd_config_path
               or os.path.join(os.path.dirname(__file__), "..",
                               "media", "mqsim_wrapper", "default_ssdconfig.xml"))
        self._ssd_config_path = os.path.abspath(ssd)
        self._trace_dir = os.path.join(
            os.path.dirname(__file__), "..",
            "media", "mqsim_wrapper", "trace")
        os.makedirs(self._trace_dir, exist_ok=True)

    def tearDown(self):
        # Clean up trace files
        for f in os.listdir(self._trace_dir):
            if f.startswith("mqsim_"):
                os.remove(os.path.join(self._trace_dir, f))

    def _make_req(self, addr, size, req_type):
        return _make_memory_request(addr, size, req_type)

    def _write_trace_and_workload(self, reqs, name):
        """Generate trace + workload XML, return (trace_path, workload_path)."""
        from media.mqsim_wrapper.pymqsim import write_trace_file, generate_workload_xml
        trace_path = os.path.join(self._trace_dir, f"mqsim_{name}.txt")
        wl_path = os.path.join(self._trace_dir, f"mqsim_{name}.xml")
        cfg = type(self.system.trace_config)(
            merge_contiguous=True, request_size=131072)
        write_trace_file(reqs, trace_path, cfg)
        generate_workload_xml(trace_path, wl_path)
        return trace_path, wl_path

    def _run_native(self, wl_path):
        """Run via native pybind11, return MQSimResult."""
        from media.mqsim_wrapper.pymqsim import run_simulation
        return run_simulation(
            ssd_config_path=self._ssd_config_path,
            workload_xml_path=wl_path,
            output_dir=self._trace_dir,
        )

    def _run_binary(self, wl_path):
        """Run via MQSim binary subprocess, return MQSimResult."""
        import subprocess
        from media.mqsim_wrapper.pymqsim import parse_mqsim_output

        cmd = [_mqsim_binary, "-i", self._ssd_config_path, "-w", wl_path]
        proc = subprocess.run(
            cmd,
            cwd=self._trace_dir,
            input=b"\n",
            capture_output=True,
            timeout=300,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace")[-500:]
            raise RuntimeError(
                f"MQSim binary exited {proc.returncode}: {stderr}")
        # Binary writes workload_scenario_1.xml in cwd
        result_xml = os.path.join(self._trace_dir, "workload_scenario_1.xml")
        return parse_mqsim_output(result_xml)

    # ---- test cases ----

    def test_sequential_128kb_matches(self):
        """Native and binary produce same bandwidth for 8×128KB sequential."""
        n = 8
        reqs = [self._make_req(i * 131072, 131072, MemoryRequestType.KREAD)
                for i in range(n)]
        _, wl_path = self._write_trace_and_workload(reqs, "cmp_128k")

        native_result = self._run_native(wl_path)
        binary_result = self._run_binary(wl_path)

        self.assertGreater(native_result.bandwidth_bytes_per_sec, 0)
        self.assertGreater(binary_result.bandwidth_bytes_per_sec, 0)
        # Bandwidth should match within 5%
        ratio = (native_result.bandwidth_bytes_per_sec
                 / binary_result.bandwidth_bytes_per_sec)
        self.assertAlmostEqual(ratio, 1.0, delta=0.05,
            msg=f"Native BW={native_result.bandwidth_bytes_per_sec/1e9:.2f} GB/s, "
                f"Binary BW={binary_result.bandwidth_bytes_per_sec/1e9:.2f} GB/s")

        self.assertGreater(native_result.total_iops, 0)
        self.assertGreater(binary_result.total_iops, 0)

    def test_random_4kb_matches(self):
        """Native and binary produce similar IOPS for 32×4KB random."""
        # Non-sequential addresses to simulate random
        addrs = [i * 65536 for i in range(32)]  # Far apart = random-like
        reqs = [self._make_req(a, 4096, MemoryRequestType.KREAD) for a in addrs]
        self.system.trace_config = type(self.system.trace_config)(
            merge_contiguous=False, request_size=4096)
        _, wl_path = self._write_trace_and_workload(reqs, "cmp_4k")

        # Restore trace config
        self.system.trace_config = type(self.system.trace_config)(
            merge_contiguous=True, request_size=131072)

        native_result = self._run_native(wl_path)
        binary_result = self._run_binary(wl_path)

        self.assertGreater(native_result.total_iops, 0)
        self.assertGreater(binary_result.total_iops, 0)
        ratio = (native_result.total_iops / binary_result.total_iops)
        self.assertAlmostEqual(ratio, 1.0, delta=0.05,
            msg=f"Native IOPS={native_result.total_iops:.0f}, "
                f"Binary IOPS={binary_result.total_iops:.0f}")


if __name__ == "__main__":
    unittest.main()
