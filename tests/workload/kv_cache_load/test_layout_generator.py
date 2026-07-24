"""KV page layout and request-generation tests."""

import pytest

from ....memory_type import MemoryRequestType
from ....workload.kv_cache_load import (
    KVAccessPattern,
    KVCacheLoadConfig,
    KVCacheLoadGenerator,
    KVLoadGranularity,
)
from ....workload.kv_cache_load.layout import KVPageLayout


class _FixedSelector:
    def __init__(self, token_ids):
        self._token_ids = list(token_ids)

    def select(self, config):
        return list(self._token_ids)


def _config(**overrides):
    values = {
        "access_tokens": 6,
        "context_tokens": 64,
        "token_size_bytes": 576,
        "pattern": KVAccessPattern.CONTIGUOUS,
        "granularity": KVLoadGranularity.TOKEN,
        "page_size_tokens": 16,
        "base_addr": 0,
    }
    values.update(overrides)
    return KVCacheLoadConfig(**values)


def test_layout_maps_tokens_inside_576_byte_pages():
    layout = KVPageLayout(
        base_addr=4096,
        token_size_bytes=576,
        page_size_tokens=16,
    )
    assert layout.page_data_bytes == 9216
    assert layout.page_stride_bytes == 9216
    assert layout.page_id(17) == 1
    assert layout.page_addr(1) == 4096 + 9216
    assert layout.token_addr(17) == 4096 + 9216 + 576


def test_layout_maps_640_byte_pages_with_alignment():
    layout = KVPageLayout(
        base_addr=4096,
        token_size_bytes=640,
        page_size_tokens=3,
        page_alignment_bytes=4096,
    )
    assert layout.page_data_bytes == 1920
    assert layout.page_stride_bytes == 4096
    assert layout.token_addr(4) == 4096 + 4096 + 640


def test_required_region_size_includes_last_partial_page():
    size = KVPageLayout.required_region_size(
        context_tokens=17,
        token_size_bytes=576,
        page_size_tokens=16,
        page_alignment_bytes=512,
    )
    assert size == 2 * 9216


@pytest.mark.parametrize("token_size", [576, 640])
def test_contiguous_token_generation(token_size):
    generated = KVCacheLoadGenerator().generate(
        _config(
            access_tokens=4,
            token_size_bytes=token_size,
            start_token=2,
        )
    )
    assert generated.selected_token_ids == [2, 3, 4, 5]
    assert generated.addresses == [
        2 * token_size,
        3 * token_size,
        4 * token_size,
        5 * token_size,
    ]
    assert generated.sizes == [token_size] * 4
    assert generated.request_types == [MemoryRequestType.KREAD] * 4
    assert generated.stats.logical_requests == 4
    assert generated.stats.demand_bytes == 4 * token_size
    assert generated.stats.issued_bytes == 4 * token_size
    assert generated.stats.read_amplification == 1.0


def test_contiguous_page_generation_loads_each_covered_page_once():
    generated = KVCacheLoadGenerator().generate(
        _config(
            access_tokens=6,
            start_token=14,
            granularity=KVLoadGranularity.PAGE,
        )
    )
    assert generated.selected_token_ids == [14, 15, 16, 17, 18, 19]
    assert generated.touched_page_ids == [0, 1]
    assert generated.addresses == [0, 9216]
    assert generated.sizes == [9216, 9216]
    assert generated.stats.logical_requests == 2
    assert generated.stats.demand_bytes == 6 * 576
    assert generated.stats.issued_bytes == 2 * 9216
    assert generated.stats.page_utilization == pytest.approx(6 / 32)
    assert generated.stats.read_amplification == pytest.approx(16 / 3)


def test_sparse_token_generation_preserves_selection_order():
    selected = [17, 1, 32, 18]
    generated = KVCacheLoadGenerator(
        selector=_FixedSelector(selected)
    ).generate(
        _config(
            access_tokens=len(selected),
            pattern=KVAccessPattern.SPARSE_UNIFORM,
        )
    )
    assert generated.selected_token_ids == selected
    assert generated.addresses == [
        17 * 576,
        1 * 576,
        32 * 576,
        18 * 576,
    ]


def test_sparse_page_generation_deduplicates_in_first_touch_order():
    selected = [17, 1, 32, 18]
    generated = KVCacheLoadGenerator(
        selector=_FixedSelector(selected)
    ).generate(
        _config(
            access_tokens=len(selected),
            pattern=KVAccessPattern.SPARSE_UNIFORM,
            granularity=KVLoadGranularity.PAGE,
        )
    )
    assert generated.touched_page_ids == [1, 0, 2]
    assert generated.addresses == [9216, 0, 2 * 9216]
    assert generated.sizes == [9216] * 3


def test_page_size_one_matches_token_requests_for_unique_tokens():
    config_base = {
        "access_tokens": 4,
        "context_tokens": 32,
        "token_size_bytes": 640,
        "pattern": KVAccessPattern.SPARSE_UNIFORM,
        "page_size_tokens": 1,
        "seed": 7,
    }
    token_load = KVCacheLoadGenerator().generate(
        KVCacheLoadConfig(
            **config_base,
            granularity=KVLoadGranularity.TOKEN,
        )
    )
    page_load = KVCacheLoadGenerator().generate(
        KVCacheLoadConfig(
            **config_base,
            granularity=KVLoadGranularity.PAGE,
        )
    )
    assert page_load.selected_token_ids == token_load.selected_token_ids
    assert page_load.addresses == token_load.addresses
    assert page_load.sizes == token_load.sizes
    assert page_load.stats == token_load.stats


def test_empty_generation_has_zero_metrics():
    generated = KVCacheLoadGenerator().generate(
        _config(
            access_tokens=0,
            context_tokens=0,
        )
    )
    assert generated.addresses == []
    assert generated.sizes == []
    assert generated.touched_page_ids == []
    assert generated.stats.demand_bytes == 0
    assert generated.stats.issued_bytes == 0
    assert generated.stats.page_utilization == 0.0
    assert generated.stats.read_amplification == 0.0

