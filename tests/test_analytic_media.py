"""Tests for AnalyticMediaSystem — roofline estimation backend."""

import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memengine import (
    MemoryEngineConfig,
    MemoryRequestType,
    MemoryObject,
    MemoryRequest,
)
from media import (
    MediaConfig,
    MediaSystemBackend,
    AnalyticMediaSystem,
    MediaMetrics,
)


class TestAnalyticMediaSystem(unittest.TestCase):
    """Test the analytic/roofline backend."""

    def setUp(self):
        self.media_config = MediaConfig(
            media_type=MediaSystemBackend.ANALYTIC,
            bandwidth=100.0,  # 100 GB/s
        )
        self.system = AnalyticMediaSystem(self.media_config)
        self.mem_config = MemoryEngineConfig(granularity=64)

    def _make_memory_request(self, addr, size, req_type):
        """Helper: create a MemoryRequest for testing."""
        obj = MemoryObject(addr, size, req_type, self.mem_config)
        return MemoryRequest(memory_object=obj)

    def test_single_read(self):
        """Single read request produces valid metrics."""
        req = self._make_memory_request(0, 64, MemoryRequestType.KREAD)
        metrics = self.system.handler_mem_request([req])

        self.assertIsInstance(metrics, MediaMetrics)
        self.assertEqual(metrics.num_read_requests, 1)
        self.assertEqual(metrics.num_write_requests, 0)
        self.assertEqual(metrics.num_media_reqs, 1)
        self.assertGreater(metrics.time, 0)

    def test_single_write(self):
        """Single write request counts correctly."""
        req = self._make_memory_request(0, 64, MemoryRequestType.KWRITE)
        metrics = self.system.handler_mem_request([req])

        self.assertEqual(metrics.num_read_requests, 0)
        self.assertEqual(metrics.num_write_requests, 1)
        self.assertGreater(metrics.time, 0)

    def test_time_scales_with_size(self):
        """Larger requests should take proportionally more time."""
        req_small = self._make_memory_request(0, 64, MemoryRequestType.KREAD)
        req_large = self._make_memory_request(0, 128, MemoryRequestType.KREAD)

        m_small = self.system.handler_mem_request([req_small])
        m_large = self.system.handler_mem_request([req_large])

        # Large (128B) should take about 2x small (64B)
        ratio = m_large.time / m_small.time
        self.assertAlmostEqual(ratio, 2.0, places=1)

    def test_empty_request_list(self):
        """Empty request list returns zero metrics."""
        metrics = self.system.handler_mem_request([])
        self.assertEqual(metrics.time, 0.0)
        self.assertEqual(metrics.num_media_reqs, 0)

    def test_multiple_requests(self):
        """Multiple requests accumulate time correctly."""
        reqs = [
            self._make_memory_request(0, 64, MemoryRequestType.KREAD),
            self._make_memory_request(64, 128, MemoryRequestType.KWRITE),
        ]
        metrics = self.system.handler_mem_request(reqs)
        self.assertEqual(metrics.num_media_reqs, 2)

    def test_system_metrics_accumulate(self):
        """System metrics should accumulate across handler_mem_request calls."""
        req = self._make_memory_request(0, 64, MemoryRequestType.KREAD)

        self.system.handler_mem_request([req])
        self.system.handler_mem_request([req])

        sys_metrics = self.system.get_system_metrics()
        self.assertEqual(sys_metrics.num_read_requests, 2)
        self.assertEqual(len(sys_metrics.media_metrics_list), 2)

    def test_reset_system_metrics(self):
        """Resetting clears accumulated system metrics."""
        req = self._make_memory_request(0, 64, MemoryRequestType.KREAD)
        self.system.handler_mem_request([req])
        self.system.reset_system_metrics()

        sys_metrics = self.system.get_system_metrics()
        self.assertEqual(sys_metrics.num_read_requests, 0)
        self.assertEqual(len(sys_metrics.media_metrics_list), 0)

    def test_bandwidth_config(self):
        """Higher bandwidth → lower access time."""
        req = self._make_memory_request(0, 100 * 1024**3, MemoryRequestType.KREAD)

        # 100 GB/s
        sys_100 = AnalyticMediaSystem(
            MediaConfig(media_type=MediaSystemBackend.ANALYTIC, bandwidth=100.0)
        )
        m_100 = sys_100.handler_mem_request([req])

        # 200 GB/s
        sys_200 = AnalyticMediaSystem(
            MediaConfig(media_type=MediaSystemBackend.ANALYTIC, bandwidth=200.0)
        )
        m_200 = sys_200.handler_mem_request([req])

        # Double bandwidth → half time
        self.assertAlmostEqual(m_100.time, 2 * m_200.time, places=3)


if __name__ == "__main__":
    unittest.main()
