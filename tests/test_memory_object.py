"""Tests for MemoryObject and request decomposition logic."""

import unittest
import sys
import os

from ..memory_type import MemoryType, MemoryRequestType
from ..memory_config import MemoryEngineConfig
from ..memory_object import MemoryObject
from ..memory_request import MemoryRequest
from ..memory_engine import MemoryEngine
from ..memory_metrics import MemoryMetrics, MemoryEngineMetrics
from ..media import MediaConfig, MediaSystemBackend


class TestMemoryObject(unittest.TestCase):
    """Test MemoryObject creation and media_req_num computation."""

    def setUp(self):
        self.config = MemoryEngineConfig(
            memory_type=MemoryType.HBM,
            media_config=MediaConfig(
                media_type=MediaSystemBackend.ANALYTIC, capacity=1.0),
        )
        self.config.granularity = 64  # hardcode for decomposition test

    def test_exact_granularity_size(self):
        """Size exactly one granularity unit → 1 media request."""
        obj = MemoryObject(
            addr=0, size=64, req_type=MemoryRequestType.KREAD, config=self.config
        )
        self.assertEqual(obj.media_req_num, 1)
        self.assertEqual(obj.addr, 0)
        self.assertEqual(obj.size, 64)

    def test_partial_granularity_size(self):
        """Size smaller than granularity → still 1 media request (ceiling)."""
        obj = MemoryObject(
            addr=0, size=32, req_type=MemoryRequestType.KWRITE, config=self.config
        )
        self.assertEqual(obj.media_req_num, 1)

    def test_multi_granularity_size(self):
        """Size spanning multiple granularity units."""
        obj = MemoryObject(
            addr=128, size=256, req_type=MemoryRequestType.KREAD, config=self.config
        )
        self.assertEqual(obj.media_req_num, 4)


class TestMemoryRequest(unittest.TestCase):
    """Test MemoryRequest wrapping."""

    def setUp(self):
        self.config = MemoryEngineConfig(media_config=MediaConfig(media_type=MediaSystemBackend.ANALYTIC,bandwidth=100.0,capacity=1.0))
        self.config.granularity = 64  # hardcode for decomposition test
        self.obj = MemoryObject(
            addr=4096, size=128, req_type=MemoryRequestType.KWRITE, config=self.config
        )

    def test_create_request(self):
        """MemoryRequest wraps a MemoryObject and starts with empty media request list."""
        req = MemoryRequest(memory_object=self.obj)
        self.assertEqual(req.memory_object, self.obj)
        self.assertEqual(req.media_request_list, [])


if __name__ == "__main__":
    unittest.main()
