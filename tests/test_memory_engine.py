"""Tests for MemoryEngine: address allocation, request construction, metrics."""

import unittest
import sys
import os
import tempfile

from ..memory_type import MemoryType, MemoryRequestType
from ..memory_config import MemoryEngineConfig
from ..memory_object import MemoryObject
from ..memory_request import MemoryRequest
from ..memory_engine import MemoryEngine
from ..memory_metrics import MemoryMetrics, MemoryEngineMetrics
from ..media import (
    MediaConfig,
    MediaSystemBackend,
)


class TestMemoryEngineAddressAllocation(unittest.TestCase):
    """Test MemoryEngine address management."""

    def setUp(self):
        self.engine = MemoryEngine(MemoryEngineConfig(
            memory_type=MemoryType.HBM,
            media_config=MediaConfig(
                media_type=MediaSystemBackend.ANALYTIC,
                capacity=1.0,  # 1 GB → per_dp_capacity = 1GB
                bandwidth=100.0,
            ),
        ))

    def test_align_up(self):
        g = self.engine.mem_config.granularity
        self.assertEqual(self.engine.align_up(1), g)
        self.assertEqual(self.engine.align_up(g - 1), g)
        self.assertEqual(self.engine.align_up(g), g)
        self.assertEqual(self.engine.align_up(g + 1), 2 * g)
        self.assertEqual(self.engine.align_up(0), 0)

    def test_get_tensor_addr_sequential(self):
        g = self.engine.mem_config.granularity
        addr1 = self.engine.get_tensor_addr(g)
        addr2 = self.engine.get_tensor_addr(g)
        self.assertEqual(addr1, 0)
        self.assertEqual(addr2, g)

    def test_get_tensor_addr_alignment(self):
        g = self.engine.mem_config.granularity
        addr = self.engine.get_tensor_addr(100)
        self.assertEqual(addr, 0)
        addr2 = self.engine.get_tensor_addr(g)
        self.assertEqual(addr2, self.engine.align_up(100))

    def test_capacity_overflow(self):
        cap = self.engine.mem_config.per_dp_capacity
        self.engine.get_tensor_addr(cap)
        self.engine.reset_addr()
        with self.assertRaises(OverflowError):
            self.engine.get_tensor_addr(cap + 1)

    def test_reset_addr(self):
        g = self.engine.mem_config.granularity
        self.engine.get_tensor_addr(g * 100)
        self.engine.reset_addr()
        addr = self.engine.get_tensor_addr(g)
        self.assertEqual(addr, 0)


class TestMemoryEngineIssueRequest(unittest.TestCase):
    """Test MemoryEngine issue_request flow."""

    def setUp(self):
        self.engine = MemoryEngine(MemoryEngineConfig(
            memory_type=MemoryType.HBM,
            dp_size=1,
            storage_instance_num=1,
            media_config=MediaConfig(
                media_type=MediaSystemBackend.ANALYTIC,
                capacity=1.0,  # 1 GB
                bandwidth=100.0,
            ),
        ))

    def test_issue_request_no_media_system_raises(self):
        """Engine without media_config raises ValueError at construction."""
        with self.assertRaises(ValueError):
            MemoryEngine(MemoryEngineConfig())

    def test_issue_request_single(self):
        metrics = self.engine.issue_request(
            [0], [64], [MemoryRequestType.KREAD]
        )
        self.assertIsInstance(metrics, MemoryMetrics)
        self.assertGreater(metrics.total_time, 0)

    def test_issue_request_multiple(self):
        metrics = self.engine.issue_request(
            [0, 64, 128],
            [64, 128, 256],
            [MemoryRequestType.KREAD, MemoryRequestType.KWRITE, MemoryRequestType.KREAD],
        )
        self.assertEqual(metrics.memory_reqs_num, 3)  # Analytic: no decomposition

    def test_issue_request_with_dp(self):
        self.engine.mem_config.dp_size = 2
        metrics = self.engine.issue_request(
            [0], [64], [MemoryRequestType.KREAD]
        )
        self.assertEqual(metrics.memory_reqs_num, 2)

    def test_engine_metrics_accumulation(self):
        self.engine.issue_request([0], [64], [MemoryRequestType.KREAD])
        self.engine.issue_request([0], [128], [MemoryRequestType.KWRITE])
        em = self.engine.get_engine_metrics()
        self.assertEqual(len(em.mem_metrics_list), 2)
        self.assertEqual(em.memory_reqs_num, 2)  # 2 engine-level requests

    def test_reset_engine_metrics(self):
        self.engine.issue_request([0], [64], [MemoryRequestType.KREAD])
        self.engine.reset_engine_metrics()
        em = self.engine.get_engine_metrics()
        self.assertEqual(len(em.mem_metrics_list), 0)


class TestMemoryEngineConfig(unittest.TestCase):
    """Test MemoryEngineConfig validation."""

    def test_default_config(self):
        config = MemoryEngineConfig()
        self.assertEqual(config.memory_type, MemoryType.HBM)
        self.assertEqual(config.dp_size, 1)
        self.assertEqual(config.storage_instance_num, 1)

    def test_invalid_dp_size(self):
        with self.assertRaises(ValueError):
            MemoryEngineConfig(dp_size=0)

    def test_invalid_storage_instance_num(self):
        with self.assertRaises(ValueError):
            MemoryEngineConfig(storage_instance_num=0)


class TestMemoryEngineMetrics(unittest.TestCase):
    """Test MemoryMetrics and MemoryEngineMetrics."""

    def test_memory_metrics_default(self):
        m = MemoryMetrics()
        self.assertEqual(m.cycles, 0)
        self.assertEqual(m.total_time, 0.0)

    def test_engine_metrics_update(self):
        em = MemoryEngineMetrics()
        m = MemoryMetrics(cycles=100, total_time=0.5, memory_reqs_num=4)
        em.update(m, total_bytes=256)
        self.assertEqual(em.cycles, 100)
        self.assertEqual(em.total_time, 0.5)
        self.assertEqual(em.memory_reqs_num, 4)
        self.assertEqual(len(em.mem_metrics_list), 1)

    def test_engine_metrics_bandwidth_from_total_bytes(self):
        """bandwidth = total_bytes / total_time (exact, no time-weight needed)."""
        em = MemoryEngineMetrics()
        m = MemoryMetrics(cycles=0, total_time=2.0, memory_reqs_num=10,
                          bandwidth=999, iops=999)
        em.update(m, total_bytes=1000)
        self.assertEqual(em.bandwidth, 500.0)  # 1000 / 2.0

    def test_engine_metrics_iops_time_weighted(self):
        """IOPS uses time-weighted average when backend provides it."""
        em = MemoryEngineMetrics()
        # Batch 1: 1000 IOPS for 0.1s → 100 ops
        m1 = MemoryMetrics(cycles=0, total_time=0.1, memory_reqs_num=1,
                           iops=1000.0, bandwidth=500.0)
        em.update(m1, total_bytes=50)
        self.assertEqual(em.iops, 1000.0)

        # Batch 2: 500 IOPS for 0.2s → 100 ops
        m2 = MemoryMetrics(cycles=0, total_time=0.2, memory_reqs_num=1,
                           iops=500.0, bandwidth=250.0)
        em.update(m2, total_bytes=50)
        # 200 ops / 0.3s = 666.7 IOPS (time-weighted, NOT 750 from sum or avg)
        self.assertAlmostEqual(em.iops, 666.666, places=1)

    def test_engine_metrics_iops_no_data_no_update(self):
        """When backend doesn't provide IOPS, cumulative iops stays unchanged."""
        em = MemoryEngineMetrics()
        m = MemoryMetrics(cycles=0, total_time=0.5, memory_reqs_num=1,
                          global_memory_reqs_num=100, iops=0.0)
        em.update(m, total_bytes=1000)
        # Backend gave no IOPS → can't compute it correctly, leave at 0
        self.assertEqual(em.iops, 0.0)


# ------------------------------------------------------------------
# MemoryEngine + MQSim integration tests
# ------------------------------------------------------------------

_mqsim_available = False
try:
    from ..media.mqsim_wrapper.pymqsim import check_mqsim_available
    _mqsim_available = check_mqsim_available()
except Exception:
    pass


@unittest.skipUnless(_mqsim_available, "MQSim engine not built")
class TestMemoryEngineWithMQSim(unittest.TestCase):
    """Integration tests: MemoryEngine with MQSim backend."""

    def setUp(self):
        cfg_dir = os.path.join(os.path.dirname(__file__), "config")
        ssd_config = os.path.join(cfg_dir, "default_ssdconfig.xml")
        workload_config = os.path.join(cfg_dir, "default_workload.xml")
        self.engine = MemoryEngine(MemoryEngineConfig(
            memory_type=MemoryType.SSD,
            media_config=MediaConfig(
                media_type=MediaSystemBackend.MQSIM,
                capacity=512.0,
                ssd_config_path=os.path.abspath(ssd_config),
                workload_config_path=os.path.abspath(workload_config),
                merge_contiguous=True,
                request_size_bytes=131072,
            ),
        ))

    def test_sequential_read_metrics(self):
        """Sequential reads: bandwidth > 0, iops > 0, time > 0."""
        n = 8
        request_size = 131072  # 128 KB
        total_bytes = request_size * n
        base_addr = self.engine.get_tensor_addr(total_bytes)
        addrs = [base_addr + i * request_size for i in range(n)]
        sizes = [request_size] * n

        metrics = self.engine.issue_request(
            addrs, sizes, [MemoryRequestType.KREAD] * n,
        )

        self.assertGreater(metrics.total_time, 0, "Simulation time should be > 0")
        self.assertGreater(metrics.bandwidth, 0, "Bandwidth should be > 0")
        self.assertGreater(metrics.iops, 0, "IOPS should be > 0")
        # bandwidth * time ≈ total_bytes (within ~30%)
        computed_bytes = metrics.bandwidth * metrics.total_time
        ratio = computed_bytes / total_bytes
        self.assertAlmostEqual(ratio, 1.0, delta=0.3,
            msg=f"BW*time={computed_bytes:.0f} vs total={total_bytes}")

    def test_engine_metrics_accumulation(self):
        """Two batches: cumulative metrics accumulate correctly."""
        request_size = 65536  # 64 KB
        n_per_batch = 4
        total_bytes_per_batch = request_size * n_per_batch

        for batch in range(2):
            base_addr = self.engine.get_tensor_addr(total_bytes_per_batch)
            addrs = [base_addr + i * request_size for i in range(n_per_batch)]
            sizes = [request_size] * n_per_batch
            self.engine.issue_request(
                addrs, sizes, [MemoryRequestType.KREAD] * n_per_batch,
            )

        em = self.engine.get_engine_metrics()
        self.assertEqual(len(em.mem_metrics_list), 2)
        self.assertEqual(em.total_bytes, total_bytes_per_batch * 2)
        self.assertGreater(em.total_time, 0)
        self.assertGreater(em.bandwidth, 0)
        self.assertGreater(em.iops, 0)


if __name__ == "__main__":
    unittest.main()


# ======================================================================
# Trace generation scenarios — from docs/trace_generation_analysis.md
# ======================================================================
#
# Test matrix §六 covers 13 addr/size/merge/page combinations.
# Parameter defaults for these tests:
#   PAGE_SIZE_BYTES = 16384 (16 KB, from default_ssdconfig.xml)
#   SECTOR_SIZE     = 512
#   SECTORS_PER_PAGE = 32
#   request_size     = 8192 (8 KB, small enough to exercise slicing)

_SSD_CFG = os.path.join(os.path.dirname(__file__), "config",
                        "default_ssdconfig.xml")


def _load_geometry():
    """Load NAND geometry once for the trace-scenario tests."""
    from ..media.mqsim_wrapper.pymqsim.trace import (
        load_from_ssdconfig_xml, PAGE_SIZE_BYTES, SECTOR_SIZE,
        SECTORS_PER_PAGE,
    )
    try:
        load_from_ssdconfig_xml(_SSD_CFG)
    except RuntimeError:
        pass  # already loaded
    return PAGE_SIZE_BYTES, SECTOR_SIZE, SECTORS_PER_PAGE


def _make_req(addr, size, rtype=MemoryRequestType.KREAD):
    obj = MemoryObject(addr, size, rtype, MemoryEngineConfig())
    return MemoryRequest(memory_object=obj)


def _build_trace_lines(reqs, merge=True, req_size=8192):
    """Shortcut: build trace lines with given config."""
    from ..media.mqsim_wrapper.pymqsim.trace import build_trace_lines, TraceSliceConfig
    cfg = TraceSliceConfig(merge_contiguous=merge, request_size=req_size)
    return build_trace_lines(reqs, cfg)


class TestTraceScenariosAlignOK(unittest.TestCase):
    """§六 cases 1-3, 11: sector-aligned addr — baseline correctness."""

    @classmethod
    def setUpClass(cls):
        cls.PG, cls.SS, cls.SPP = _load_geometry()

    # -- case 1: align, size < req_size, size < page, no cross-page ----

    def test_case1_aligned_small_single_line(self):
        """addr=0, 4KB; req_size=8KB; page=16KB → 1 line, no expansion."""
        addrs, sizes, types = _build_trace_lines(
            [_make_req(0, 4096)], merge=True, req_size=8192)
        self.assertEqual(sizes, [4096])
        self.assertEqual(addrs, [0])
        self.assertEqual(types, [1])

    # -- case 2: align, size == req_size --------------------------------

    def test_case2_aligned_size_equals_req_size(self):
        """addr=0, 8KB; req_size=8KB → exact fit, 1 line."""
        addrs, sizes, types = _build_trace_lines(
            [_make_req(0, 8192)], merge=True, req_size=8192)
        self.assertEqual(sizes, [8192])
        self.assertEqual(addrs, [0])
        self.assertEqual(len(sizes), 1)

    # -- case 3: align, size > req_size, within single page -------------

    def test_case3_aligned_larger_than_req_size(self):
        """addr=0, 12KB; req_size=4KB, page=16KB → 3 lines."""
        addrs, sizes, types = _build_trace_lines(
            [_make_req(0, 12288)], merge=True, req_size=4096)
        self.assertEqual(sizes, [4096, 4096, 4096])
        self.assertEqual(addrs, [0, 4096, 8192])
        self.assertEqual(types, [1, 1, 1])

    # -- case 11: merge contiguous --------------------------------------

    def test_case11_merge_contiguous_same_page(self):
        """2×4KB contiguous reads → merged to 8KB → 1 line."""
        reqs = [_make_req(0, 4096), _make_req(4096, 4096)]
        addrs, sizes, types = _build_trace_lines(
            reqs, merge=True, req_size=8192)
        self.assertEqual(sizes, [8192])
        self.assertEqual(addrs, [0])

    def test_case11_merge_with_gap_does_not_merge(self):
        """addr gap → 2 separate chunks, even with merge=True."""
        reqs = [_make_req(0, 4096), _make_req(16384, 4096)]
        addrs, sizes, types = _build_trace_lines(
            reqs, merge=True, req_size=8192)
        self.assertEqual(len(sizes), 2)

    def test_case11_merge_mixed_type_not_merged(self):
        """Contiguous read+write → still 2 lines (types differ)."""
        reqs = [_make_req(0, 4096, MemoryRequestType.KREAD),
                _make_req(4096, 4096, MemoryRequestType.KWRITE)]
        addrs, sizes, types = _build_trace_lines(
            reqs, merge=True, req_size=8192)
        self.assertEqual(len(sizes), 2)
        self.assertEqual(types, [1, 0])  # reads before writes


class TestTraceScenariosUnaligned(unittest.TestCase):
    """§六 cases 5-6, 13: non-sector-aligned addr"""

    @classmethod
    def setUpClass(cls):
        cls.PG, cls.SS, cls.SPP = _load_geometry()

    # -- case 5: unaligned, size < req_size, single page ----------------

    def test_case5_unaligned_small_expands_tail(self):
        """addr=100, 4KB → aligned to [0, 4608) = 9 sectors, 1 line."""
        addrs, sizes, types = _build_trace_lines(
            [_make_req(100, 4096)], merge=True, req_size=8192)
        self.assertEqual(addrs, [0])
        self.assertEqual(sizes, [4608])
        self.assertEqual(types, [1])

    def test_case5_unaligned_but_end_aligned(self):
        """addr=100, size=502 (ends exactly at sector boundary: 100+502=602, 602%512≠0...)
        Actually test: addr=100, size=412 → end=512 → exactly 1 sector [0,512)."""
        addrs, sizes, types = _build_trace_lines(
            [_make_req(100, 412)], merge=True, req_size=8192)
        # aligned: start=0, end=((100+412+511)//512)*512 = (1023//512)*512 = 512
        self.assertEqual(addrs, [0])
        self.assertEqual(sizes, [512])

    # -- case 6: unaligned, size > req_size, multi-line -----------------

    def test_case6_unaligned_large_multi_line(self):
        """addr=100, 16KB; req_size=4KB → 5 lines (was 4 before fix)."""
        addrs, sizes, types = _build_trace_lines(
            [_make_req(100, 16384)], merge=True, req_size=4096)
        # aligned range: [0, 16896) → 16896 / 4096 = 4.125 → 5 lines
        self.assertEqual(len(sizes), 5)
        self.assertEqual(sum(sizes), 16896)
        self.assertEqual(addrs[0], 0)
        self.assertEqual(addrs[-1], 16384)  # 4×4096

    # -- case 13: unaligned + cross-page, single line -------------------

    def test_case13_unaligned_cross_page_single_line(self):
        """addr=100, 20KB (crosses page @16KB); req_size=32KB → 1 line, expanded."""
        addrs, sizes, types = _build_trace_lines(
            [_make_req(100, 20480)], merge=True, req_size=32768)
        # aligned: [0, 20992) = 20992 B
        self.assertEqual(len(sizes), 1)
        self.assertEqual(sizes[0], 20992)
        self.assertEqual(addrs[0], 0)

    def test_case13_unaligned_cross_page_multi_line(self):
        """addr=100, 20KB cross-page; req_size=8KB → 3 lines."""
        addrs, sizes, types = _build_trace_lines(
            [_make_req(100, 20480)], merge=True, req_size=8192)
        # aligned: [0, 20992) → 20992 / 8192 = 2.56 → 3 lines
        self.assertEqual(len(sizes), 3)
        self.assertEqual(sum(sizes), 20992)


class TestTraceScenariosNoMerge(unittest.TestCase):
    """§六 cases 7-10: merge_contiguous=False — addresses preserved as-is."""

    @classmethod
    def setUpClass(cls):
        cls.PG, cls.SS, cls.SPP = _load_geometry()

    # -- case 7: no-merge, aligned, sub-page, cwdp_aware=False ---------
    #   Default → Path B: preserves original addresses.

    def test_case7_no_merge_preserves_addrs(self):
        """cwdp_aware=False: sector-aligned 4KB requests keep original addrs."""
        # Use sector-aligned addresses so no expansion skews line count
        addrs_in = [0, 8192, 16384, 24576]  # all 512-aligned
        reqs = [_make_req(a, 4096) for a in addrs_in]
        addrs, sizes, types = _build_trace_lines(
            reqs, merge=False, req_size=4096)

        self.assertEqual(len(sizes), 4)
        self.assertEqual(sizes, [4096] * 4)
        # Path B preserves input addresses (all sector-aligned → unchanged)
        self.assertEqual(addrs, addrs_in)

    # -- case 9: no-merge, req_size == PAGE_SIZE → Path B always -------

    def test_case9_no_merge_size_equals_page_size(self):
        """req_size == PAGE_SIZE → Path B, not Path A. Alignment expansion active."""
        reqs = [_make_req(0, 8192), _make_req(100000, 8192)]
        addrs, sizes, types = _build_trace_lines(
            reqs, merge=False, req_size=8192)

        # Path B: chunk 0 → 1 line; chunk 1 (unaligned) → 2 lines
        self.assertEqual(len(sizes), 3)
        self.assertEqual(addrs[0], 0)
        self.assertEqual(sizes[0], 8192)
        self.assertGreater(sum(sizes), 8192 * 2)

    # -- case 10: no-merge, unaligned, cwdp_aware=False → Path B -------

    def test_case10_no_merge_unaligned_path_b(self):
        """cwdp_aware=False: 2 unaligned 4KB → Path B, expanded + sliced."""
        reqs = [_make_req(100, 4096), _make_req(50000, 4096)]
        addrs, sizes, types = _build_trace_lines(
            reqs, merge=False, req_size=4096)

        # Each unaligned 4096B → aligned to 4608B > req_size(4096)
        # → 2 lines per chunk (4096 + 512)
        self.assertEqual(len(sizes), 4)
        # addr=100 → [0, 4608): [0,4096] + [4096,512]
        self.assertEqual(addrs[0], 0)
        self.assertEqual(sizes[0], 4096)
        self.assertEqual(addrs[1], 4096)
        self.assertEqual(sizes[1], 512)
        # addr=50000 → [49664, 54272)
        self.assertEqual(addrs[2], 49664)
        self.assertEqual(sizes[2], 4096)
        self.assertEqual(addrs[3], 53760)
        self.assertEqual(sizes[3], 512)

    # -- no-merge, size > req_size → Path B (multi-line chunk) ---------

    def test_no_merge_large_chunk_uses_path_b(self):
        """12KB request (>8KB req_size) → multi-line → Path B, not A."""
        reqs = [_make_req(100, 12288)]
        addrs, sizes, types = _build_trace_lines(
            reqs, merge=False, req_size=8192)
        self.assertEqual(len(sizes), 2)
        self.assertEqual(addrs[0], 0)
        self.assertEqual(addrs[1], 8192)
        self.assertEqual(sum(sizes), 12800)  # expanded


class TestTraceScenariosCrossPage(unittest.TestCase):
    """§六 cases 4, 12: requests that cross NAND page boundaries."""

    @classmethod
    def setUpClass(cls):
        cls.PG, cls.SS, cls.SPP = _load_geometry()

    # -- case 4: aligned, size > req_size, size > page, cross-page ------

    def test_case4_aligned_cross_page_sliced(self):
        """addr=0, 32KB; req_size=8KB; page=16KB → 4 lines, no page split."""
        addrs, sizes, types = _build_trace_lines(
            [_make_req(0, 32768)], merge=True, req_size=8192)
        # 32768 / 8192 = 4 lines — no explicit page-boundary split
        self.assertEqual(sizes, [8192, 8192, 8192, 8192])
        self.assertEqual(addrs, [0, 8192, 16384, 24576])
        # NOTE: line 2 starts at 16384 which is the page boundary.
        #       This is coincidental, not because the code splits at pages.

    def test_case4_aligned_cross_page_large_req_size(self):
        """addr=0, 64KB; req_size=64KB; page=16KB → 1 line crosses 4 pages."""
        addrs, sizes, types = _build_trace_lines(
            [_make_req(0, 65536)], merge=True, req_size=65536)
        # Single line spans 4 pages — MQSim handles this internally
        self.assertEqual(len(sizes), 1)
        self.assertEqual(sizes[0], 65536)

    # -- case 12: merge pushes chunk across page boundary --------------

    def test_case12_merge_crosses_page_boundary(self):
        """3 contiguous 8KB reads → merge to 24KB → slice @8KB → 3 lines."""
        reqs = [_make_req(i * 8192, 8192) for i in range(3)]
        addrs, sizes, types = _build_trace_lines(
            reqs, merge=True, req_size=8192)
        # 24KB, sliced by 8KB = 3 lines
        self.assertEqual(sizes, [8192, 8192, 8192])
        self.assertEqual(addrs, [0, 8192, 16384])
        # addr=16384 is exactly page boundary; addr=8192 is mid-page
        # Both are correct — they preserve the original data layout

    def test_case12_merge_fills_exact_page(self):
        """4 contiguous 4KB reads → merge to 16KB → 1 page → slice @16KB → 1 line."""
        reqs = [_make_req(i * 4096, 4096) for i in range(4)]
        addrs, sizes, types = _build_trace_lines(
            reqs, merge=True, req_size=16384)
        # Merged 16KB = exactly 1 page
        self.assertEqual(sizes, [16384])
        self.assertEqual(addrs, [0])

    def test_case12_merge_exceeds_page(self):
        """5 contiguous 4KB reads → 20KB > 16KB page; slice @8KB → 3 lines."""
        reqs = [_make_req(i * 4096, 4096) for i in range(5)]
        addrs, sizes, types = _build_trace_lines(
            reqs, merge=True, req_size=8192)
        # 20KB / 8KB = 2.5 → 3 lines: 8KB + 8KB + 4KB
        self.assertEqual(len(sizes), 3)
        self.assertEqual(sum(sizes), 20480)


class TestTraceWriteFileOutput(unittest.TestCase):
    """End-to-end: write_trace_file → verify trace line format."""

    @classmethod
    def setUpClass(cls):
        cls.PG, cls.SS, cls.SPP = _load_geometry()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="trace_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, reqs, merge=True, req_size=8192):
        from ..media.mqsim_wrapper.pymqsim import write_trace_file, TraceSliceConfig
        cfg = TraceSliceConfig(merge_contiguous=merge, request_size=req_size)
        path = os.path.join(self.tmp, "trace.txt")
        total_bytes, line_count = write_trace_file(reqs, path, cfg)
        with open(path) as f:
            lines = [ln.strip() for ln in f]
        return total_bytes, line_count, lines

    # -- trace line format verification --------------------------------

    def test_trace_format_single_read(self):
        """Format: '0 <device_id> <lba> <sectors> 1'."""
        _, _, lines = self._write([_make_req(0, 512)])
        parts = lines[0].split()
        self.assertEqual(parts[0], "0")       # arrival time
        self.assertIn(parts[1], str(range(16)))  # device_id < CHANNELS
        self.assertEqual(parts[2], "0")       # LBA = addr/512
        self.assertEqual(parts[3], "1")       # sectors = ceil(512/512)
        self.assertEqual(parts[4], "1")       # read=1

    def test_trace_format_write(self):
        """Write → req_type=0."""
        _, _, lines = self._write([_make_req(0, 512, MemoryRequestType.KWRITE)])
        self.assertEqual(lines[0].split()[4], "0")

    def test_trace_lba_computation(self):
        """LBA = aligned_addr / 512."""
        _, _, lines = self._write([_make_req(1024, 4096)])
        self.assertEqual(lines[0].split()[2], "2")  # 1024/512=2

    def test_trace_sectors_computation(self):
        """sectors = ceil(aligned_size / 512)."""
        _, _, lines = self._write([_make_req(0, 8192)])
        self.assertEqual(lines[0].split()[3], "16")  # 8192/512=16

    def test_trace_unaligned_addr_sectors_expanded(self):
        """addr=100, 4096B → aligned [0,4608) → sectors=9."""
        _, _, lines = self._write([_make_req(100, 4096)])
        self.assertEqual(lines[0].split()[2], "0")   # LBA=0
        self.assertEqual(lines[0].split()[3], "9")   # 4608/512=9 ✅

    def test_trace_multiple_lines_device_round_robin(self):
        """device_id increments per line (round-robin)."""
        reqs = [_make_req(i * 4096, 4096) for i in range(8)]
        _, _, lines = self._write(reqs, merge=True, req_size=4096)
        devices = [int(ln.split()[1]) for ln in lines]
        # Each line gets device_id = line_index % CHANNELS
        self.assertEqual(devices, list(range(len(lines))))

    # -- byte accounting -----------------------------------------------

    def test_total_bytes_matches_sum_of_sizes(self):
        """write_trace_file total_bytes == sum of trace-line sizes."""
        reqs = [_make_req(0, 4096), _make_req(16384, 8192)]
        total_bytes, line_count, lines = self._write(reqs, merge=True, req_size=8192)
        # Both aligned, so total = 4096+8192 = 12288
        self.assertEqual(total_bytes, 12288)
        self.assertEqual(line_count, 2)

    def test_total_bytes_includes_alignment_expansion(self):
        """Unaligned addr → total_bytes includes the expanded tail."""
        total_bytes, _, _ = self._write([_make_req(100, 4096)])
        self.assertEqual(total_bytes, 4608)  # not 4096

    # -- empty / edge --------------------------------------------------

    def test_empty_request_list(self):
        """No requests → 0 bytes, 0 lines, empty file."""
        total_bytes, line_count, lines = self._write([])
        self.assertEqual(total_bytes, 0)
        self.assertEqual(line_count, 0)
        self.assertEqual(lines, [])  # empty write produces no lines
