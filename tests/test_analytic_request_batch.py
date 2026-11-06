from dataclasses import asdict

import pytest

from ..media.analytic_media_system import AnalyticMediaSystem
from ..media.media_config import MediaConfig
from ..memory_engine import MemoryEngine, MemoryEngineConfig
from ..memory_pool.memory_pool import MemoryPool, MemoryPoolConfig
from ..memory_request import MemoryRequest
from ..memory_type import MemoryRequestType


def test_analytic_columns_preserve_media_and_engine_metrics():
    config = MediaConfig(capacity=2, bandwidth=25)
    engine = MemoryEngine(MemoryEngineConfig(media_config=config))
    reference = AnalyticMediaSystem(config)
    for addresses, sizes, types in [
        ([3, 999, 4096], [65, 128, 8193],
         [MemoryRequestType.KREAD, MemoryRequestType.KWRITE, MemoryRequestType.KREAD]),
        ([4096, 4096], [128, 128], [MemoryRequestType.KREAD] * 2),
    ]:
        expected = reference.handler_mem_request([
            MemoryRequest(address, size, kind, config=engine.mem_config)
            for address, size, kind in zip(addresses, sizes, types)
        ])
        result = engine.issue_request(addresses, sizes, types)
        assert result.total_time == expected.time
        assert result.memory_reqs_num == expected.num_media_reqs
        assert asdict(engine.media_system.system_metrics) == asdict(reference.system_metrics)
    assert engine.engine_metrics.total_bytes == 65 + 128 + 8193 + 256
    assert engine.engine_metrics.memory_reqs_num == 5
    before = asdict(engine.engine_metrics)
    engine.issue_request([], [], [])
    with pytest.raises(ValueError):
        engine.issue_request([0, -1], [128, 128], [MemoryRequestType.KREAD] * 2)
    assert asdict(engine.engine_metrics) == before


def test_analytic_pool_preserves_cross_window_fragments():
    pool = MemoryPool(MemoryPoolConfig(instance_count=2,
                                     media_config=MediaConfig(capacity=2, bandwidth=50)))
    boundary = 2**30
    result = pool.issue_request([boundary - 64, boundary + 256], [128, 128],
                                [MemoryRequestType.KREAD, MemoryRequestType.KWRITE])
    assert result.memory_reqs_num == 3
    assert result.total_time == 192 / (25 * 2**30)
    assert [engine.engine_metrics.total_bytes for engine in pool.instances] == [64, 192]
    assert pool.instances[0].media_system.system_metrics.num_read_requests == 1
    assert pool.instances[1].media_system.system_metrics.num_read_requests == 1
    assert pool.instances[1].media_system.system_metrics.num_write_requests == 1


@pytest.mark.parametrize('address,size', [(-1, 64), (0, 0), (2**30 - 32, 64)])
def test_single_engine_pool_rejects_invalid_batch_before_io(address, size):
    pool = MemoryPool(MemoryPoolConfig(instance_count=1,
                                     media_config=MediaConfig(capacity=1, bandwidth=25)))
    with pytest.raises(ValueError):
        pool.issue_request([0, address], [64, size], [MemoryRequestType.KREAD] * 2)
    assert pool.instances[0].engine_metrics.total_bytes == 0
    assert pool.instances[0].engine_metrics.memory_reqs_num == 0
