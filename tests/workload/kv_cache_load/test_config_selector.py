"""Configuration and token-selection tests for KV-cache loads."""

import pytest

from ....workload.kv_cache_load import (
    KVAccessPattern,
    KVCacheLoadConfig,
    KVLoadGranularity,
)
from ....workload.kv_cache_load.selector import KVTokenSelector


def _config(**overrides):
    values = {
        "access_tokens": 8,
        "context_tokens": 128,
        "token_size_bytes": 576,
        "pattern": KVAccessPattern.CONTIGUOUS,
        "granularity": KVLoadGranularity.TOKEN,
        "page_size_tokens": 16,
    }
    values.update(overrides)
    return KVCacheLoadConfig(**values)


def test_contiguous_selection_uses_start_token():
    selected = KVTokenSelector().select(
        _config(access_tokens=6, start_token=14)
    )
    assert selected == [14, 15, 16, 17, 18, 19]


def test_sparse_uniform_is_reproducible_and_unique():
    config = _config(
        access_tokens=32,
        pattern=KVAccessPattern.SPARSE_UNIFORM,
        seed=17,
    )
    first = KVTokenSelector().select(config)
    second = KVTokenSelector().select(config)

    assert first == second
    assert len(first) == 32
    assert len(set(first)) == 32
    assert all(0 <= token_id < config.context_tokens for token_id in first)


def test_sparse_uniform_changes_with_seed():
    first = KVTokenSelector().select(
        _config(pattern=KVAccessPattern.SPARSE_UNIFORM, seed=1)
    )
    second = KVTokenSelector().select(
        _config(pattern=KVAccessPattern.SPARSE_UNIFORM, seed=2)
    )
    assert first != second


def test_sparse_page_local_controls_tokens_per_page():
    config = _config(
        access_tokens=12,
        context_tokens=128,
        pattern=KVAccessPattern.SPARSE_PAGE_LOCAL,
        selected_tokens_per_page=4,
        seed=9,
    )
    selected = KVTokenSelector().select(config)
    page_counts = {}
    for token_id in selected:
        page_id = token_id // config.page_size_tokens
        page_counts[page_id] = page_counts.get(page_id, 0) + 1

    assert len(selected) == 12
    assert len(set(selected)) == 12
    assert len(page_counts) == 3
    assert set(page_counts.values()) == {4}


def test_sparse_page_local_rejects_unrepresentable_count():
    config = _config(
        access_tokens=9,
        context_tokens=16,
        pattern=KVAccessPattern.SPARSE_PAGE_LOCAL,
        selected_tokens_per_page=8,
    )
    with pytest.raises(ValueError, match="cannot be represented"):
        KVTokenSelector().select(config)


def test_empty_selection_is_supported():
    config = _config(
        access_tokens=0,
        context_tokens=0,
        page_size_tokens=16,
    )
    assert KVTokenSelector().select(config) == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"access_tokens": -1}, "access_tokens"),
        ({"access_tokens": 9, "context_tokens": 8}, "must not exceed"),
        ({"token_size_bytes": 0}, "token_size_bytes"),
        ({"page_size_tokens": 0}, "page_size_tokens"),
        ({"base_addr": -1}, "base_addr"),
        ({"page_alignment_bytes": 0}, "page_alignment_bytes"),
        ({"selected_tokens_per_page": 17}, "selected_tokens_per_page"),
        (
            {"access_tokens": 4, "context_tokens": 8, "start_token": 6},
            "exceeds context_tokens",
        ),
    ],
)
def test_invalid_config_rejected(overrides, message):
    with pytest.raises(ValueError, match=message):
        _config(**overrides)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("access_tokens", 8.0),
        ("context_tokens", "128"),
        ("token_size_bytes", True),
        ("page_size_tokens", 16.0),
        ("base_addr", "0"),
        ("page_alignment_bytes", False),
        ("start_token", 1.0),
        ("seed", "42"),
        ("selected_tokens_per_page", 1.0),
    ],
)
def test_integer_config_fields_reject_non_integer_values(field, value):
    with pytest.raises(ValueError, match=field):
        _config(**{field: value})


def test_sparse_pattern_rejects_start_token():
    with pytest.raises(ValueError, match="only valid for the contiguous"):
        _config(
            pattern=KVAccessPattern.SPARSE_UNIFORM,
            start_token=1,
        )
