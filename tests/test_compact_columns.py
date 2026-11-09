from dataclasses import asdict
import random

import pytest

from ..media.media_backend import MediaSystemBackend
from ..media.media_config import MediaConfig
from ..memory_engine import MemoryEngine, MemoryEngineConfig
from ..memory_pool.memory_pool import MemoryPool, MemoryPoolConfig
from ..memory_type import MemoryRequestType
from ..request_columns import RequestColumns, RequestRun


@pytest.mark.parametrize('instances', [1, 2, 3])
def test_compact_analytic_preserves_all_metrics_and_cross_window_fragments(instances):
    pools = [MemoryPool(MemoryPoolConfig(instance_count=instances,
             media_config=MediaConfig(capacity=3, bandwidth=75))) for _ in range(2)]
    randomizer = random.Random(29)
    boundary = 2**30
    for iteration in range(30):
        runs = []
        for component in range(4):
            size = randomizer.randrange(1, 2 * boundary)
            stride = randomizer.randrange(0, 200)
            count = randomizer.randrange(1, 20)
            base = randomizer.randrange(0, 3 * boundary - size - count * stride)
            runs.append(RequestRun((base, base + 1), stride, count, size,
                        MemoryRequestType.KREAD if component % 2 else MemoryRequestType.KWRITE))
        columns = RequestColumns(tuple(runs))
        compact = pools[0].issue_request(columns.addr, columns.size, columns.req_type)
        reference = pools[1].issue_request(list(columns.addr), list(columns.size), list(columns.req_type))
        assert asdict(compact) == asdict(reference), iteration
        for actual, expected in zip(pools[0].instances, pools[1].instances):
            assert asdict(actual.engine_metrics) == asdict(expected.engine_metrics)
            assert asdict(actual.media_system.system_metrics) == asdict(expected.media_system.system_metrics)


@pytest.mark.parametrize('offset', [-1, 2**31])
def test_invalid_compact_batch_is_rejected_before_any_instance_submission(offset):
    pool = MemoryPool(MemoryPoolConfig(instance_count=2,
         media_config=MediaConfig(capacity=2, bandwidth=50)))
    columns = RequestColumns((RequestRun((0,), 64, 4, 64),
                              RequestRun((offset,), 64, 1, 64)))
    with pytest.raises(ValueError):
        pool.issue_request(columns.addr, columns.size, columns.req_type)
    assert all(engine.engine_metrics.total_bytes == 0 for engine in pool.instances)


@pytest.mark.parametrize('backend', [MediaSystemBackend.ANALYTIC, MediaSystemBackend.RAMULATOR,
                                    MediaSystemBackend.MQSIM])
def test_real_backends_consume_original_order_boundaries_and_statistics(backend):
    def create():
        return MemoryEngine(MemoryEngineConfig(media_config=MediaConfig(
            media_type=backend, capacity=2, bandwidth=25, merge_contiguous=False)))
    engines = [create(), create()]
    columns = RequestColumns((RequestRun((3, 65539), 96, 16, 65),
                              RequestRun((4097,), 129, 4, 127, MemoryRequestType.KWRITE)))
    first = engines[0].issue_request(columns.addr, columns.size, columns.req_type)
    second = engines[1].issue_request(list(columns.addr), list(columns.size), list(columns.req_type))
    assert asdict(first) == asdict(second)
    assert first.memory_reqs_num == 36
    assert asdict(engines[0].engine_metrics) == asdict(engines[1].engine_metrics)
    assert asdict(engines[0].media_system.system_metrics) == asdict(engines[1].media_system.system_metrics)


@pytest.mark.parametrize('backend', [MediaSystemBackend.RAMULATOR, MediaSystemBackend.MQSIM])
def test_native_pool_keeps_fragment_order_across_instance_windows(backend):
    pools = [MemoryPool(MemoryPoolConfig(instance_count=2, media_config=MediaConfig(
        media_type=backend, capacity=2, merge_contiguous=False))) for _ in range(2)]
    boundary = 2**30
    columns = RequestColumns((RequestRun((boundary - 80, boundary + 256), 32, 6, 128),))
    compact = pools[0].issue_request(columns.addr, columns.size, columns.req_type)
    reference = pools[1].issue_request(list(columns.addr), list(columns.size), list(columns.req_type))
    assert asdict(compact) == asdict(reference)
    assert compact.memory_reqs_num == 15
    for actual, expected in zip(pools[0].instances, pools[1].instances):
        assert asdict(actual.engine_metrics) == asdict(expected.engine_metrics)
        assert asdict(actual.media_system.system_metrics) == asdict(expected.media_system.system_metrics)
