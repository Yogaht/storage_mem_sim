"""Tests for MemoryEngine: address allocation, request construction, metrics."""

import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memory_type import MemoryType, MemoryRequestType
from memory_config import MemoryEngineConfig
from memory_engine import MemoryEngine
from memory_metrics import MemoryMetrics, MemoryEngineMetrics
from media import (
    MediaConfig,
    MediaSystemBackend,
)


class TestMemoryEngineAddressAllocation(unittest.TestCase):
    """Test MemoryEngine address management."""

    def setUp(self):
        self.engine = MemoryEngine(MemoryEngineConfig(
            memory_type=MemoryType.HBM,
            granularity=64,
            media_config=MediaConfig(
                media_type=MediaSystemBackend.ANALYTIC,
                capacity=1.0,  # 1 GB → per_dp_capacity = 1GB
                bandwidth=100.0,
            ),
        ))

    def test_align_up(self):
        self.assertEqual(self.engine.align_up(1), 64)
        self.assertEqual(self.engine.align_up(63), 64)
        self.assertEqual(self.engine.align_up(64), 64)
        self.assertEqual(self.engine.align_up(65), 128)
        self.assertEqual(self.engine.align_up(0), 0)

    def test_get_tensor_addr_sequential(self):
        addr1 = self.engine.get_tensor_addr(64)
        addr2 = self.engine.get_tensor_addr(64)
        self.assertEqual(addr1, 0)
        self.assertEqual(addr2, 64)

    def test_get_tensor_addr_alignment(self):
        addr = self.engine.get_tensor_addr(100)
        self.assertEqual(addr, 0)
        addr2 = self.engine.get_tensor_addr(64)
        self.assertEqual(addr2, 128)

    def test_capacity_overflow(self):
        cap = self.engine.mem_config.per_dp_capacity  # 1 GB
        self.engine.get_tensor_addr(cap)               # exactly at limit
        self.engine.reset_addr()
        with self.assertRaises(OverflowError):
            self.engine.get_tensor_addr(cap + 1)       # exceeds limit

    def test_reset_addr(self):
        self.engine.get_tensor_addr(4096)
        self.engine.reset_addr()
        addr = self.engine.get_tensor_addr(64)
        self.assertEqual(addr, 0)


class TestMemoryEngineIssueRequest(unittest.TestCase):
    """Test MemoryEngine issue_request flow."""

    def setUp(self):
        self.engine = MemoryEngine(MemoryEngineConfig(
            memory_type=MemoryType.HBM,
            granularity=64,
            dp_size=1,
            storage_instance_num=1,
            media_config=MediaConfig(
                media_type=MediaSystemBackend.ANALYTIC,
                capacity=1.0,  # 1 GB
                bandwidth=100.0,
            ),
        ))

    def test_issue_request_no_media_system_raises(self):
        """Engine without media_config raises RuntimeError."""
        engine = MemoryEngine(MemoryEngineConfig(granularity=64))
        with self.assertRaises(RuntimeError):
            engine.issue_request([0], [64], [MemoryRequestType.KREAD])

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
        self.assertEqual(config.granularity, 64)
        self.assertEqual(config.dp_size, 1)
        self.assertEqual(config.storage_instance_num, 1)

    def test_invalid_granularity(self):
        with self.assertRaises(ValueError):
            MemoryEngineConfig(granularity=0)
        with self.assertRaises(ValueError):
            MemoryEngineConfig(granularity=-1)

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


if __name__ == "__main__":
    unittest.main()
