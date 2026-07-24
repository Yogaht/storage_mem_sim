"""JSON configuration tests for KV-cache workloads."""

import json
from pathlib import Path

import pytest

from ....memory_type import MemoryRequestType
from ....workload.kv_cache_load import (
    KVAccessPattern,
    KVCacheLoadConfig,
    KVCacheLoadGenerator,
    KVLoadGranularity,
    load_kv_cache_load_config,
)


_WORKLOAD_CONFIG_DIR = (
    Path(__file__).resolve().parents[3] / "configs" / "workloads"
)


def _raw_config(**overrides):
    values = {
        "workload_type": "kv_cache_load",
        "access_tokens": 128,
        "context_tokens": 4096,
        "token_size_bytes": 576,
        "pattern": "sparse_page_local",
        "granularity": "page",
        "page_size_tokens": 16,
        "selected_tokens_per_page": 4,
        "seed": 42,
    }
    values.update(overrides)
    return values


def test_from_dict_parses_workload_enums():
    config = KVCacheLoadConfig.from_dict(
        _raw_config(request_type="kread")
    )

    assert config.pattern is KVAccessPattern.SPARSE_PAGE_LOCAL
    assert config.granularity is KVLoadGranularity.PAGE
    assert config.request_type is MemoryRequestType.KREAD
    assert config.access_tokens == 128
    assert config.selected_tokens_per_page == 4


def test_from_dict_accepts_case_insensitive_enum_names():
    config = KVCacheLoadConfig.from_dict(
        _raw_config(
            pattern="SPARSE_PAGE_LOCAL",
            granularity="PAGE",
            request_type="KREAD",
        )
    )
    assert config.pattern is KVAccessPattern.SPARSE_PAGE_LOCAL
    assert config.granularity is KVLoadGranularity.PAGE


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"workload_type": "other"}, "workload_type"),
        ({"pattern": "random"}, "pattern"),
        ({"granularity": "block"}, "granularity"),
        ({"unexpected": 1}, "unknown"),
    ],
)
def test_from_dict_rejects_invalid_file_fields(overrides, message):
    with pytest.raises(ValueError, match=message):
        KVCacheLoadConfig.from_dict(_raw_config(**overrides))


def test_from_dict_reports_missing_required_fields():
    raw = _raw_config()
    del raw["token_size_bytes"]
    with pytest.raises(ValueError, match="token_size_bytes"):
        KVCacheLoadConfig.from_dict(raw)


def test_load_json_config(tmp_path):
    config_path = tmp_path / "kv_workload.json"
    config_path.write_text(
        json.dumps(_raw_config()),
        encoding="utf-8",
    )

    config = load_kv_cache_load_config(config_path)

    assert config.context_tokens == 4096
    assert config.pattern is KVAccessPattern.SPARSE_PAGE_LOCAL


def test_load_json_rejects_non_object(tmp_path):
    config_path = tmp_path / "kv_workload.json"
    config_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        load_kv_cache_load_config(config_path)


def test_load_json_reports_parse_location(tmp_path):
    config_path = tmp_path / "kv_workload.json"
    config_path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(ValueError, match="line 1, column 2"):
        load_kv_cache_load_config(config_path)


@pytest.mark.parametrize(
    ("filename", "pattern", "granularity", "expected_requests"),
    [
        (
            "kv_contiguous_token.json",
            KVAccessPattern.CONTIGUOUS,
            KVLoadGranularity.TOKEN,
            128,
        ),
        (
            "kv_contiguous_page.json",
            KVAccessPattern.CONTIGUOUS,
            KVLoadGranularity.PAGE,
            8,
        ),
        (
            "kv_sparse_uniform_token.json",
            KVAccessPattern.SPARSE_UNIFORM,
            KVLoadGranularity.TOKEN,
            128,
        ),
        (
            "kv_sparse_uniform_page.json",
            KVAccessPattern.SPARSE_UNIFORM,
            KVLoadGranularity.PAGE,
            None,
        ),
        (
            "kv_sparse_page_local_token.json",
            KVAccessPattern.SPARSE_PAGE_LOCAL,
            KVLoadGranularity.TOKEN,
            128,
        ),
        (
            "kv_sparse_page.json",
            KVAccessPattern.SPARSE_PAGE_LOCAL,
            KVLoadGranularity.PAGE,
            32,
        ),
    ],
)
def test_example_config_matrix(
    filename,
    pattern,
    granularity,
    expected_requests,
):
    config = load_kv_cache_load_config(_WORKLOAD_CONFIG_DIR / filename)
    generated = KVCacheLoadGenerator().generate(config)

    assert config.pattern is pattern
    assert config.granularity is granularity
    assert generated.stats.selected_tokens == 128
    if expected_requests is not None:
        assert generated.stats.logical_requests == expected_requests
    if granularity is KVLoadGranularity.TOKEN:
        assert generated.stats.logical_requests == config.access_tokens
    else:
        assert generated.stats.logical_requests == generated.stats.unique_pages
