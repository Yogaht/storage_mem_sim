"""KV-cache loading workload.

The public API intentionally exposes only configuration, generation results,
and workload-level metrics. Token selection and KV page layout remain internal
implementation details.
"""

from .config import KVAccessPattern, KVCacheLoadConfig, KVLoadGranularity
from .generator import GeneratedKVCacheLoad, KVCacheLoadGenerator
from .io import load_kv_cache_load_config
from .layout import KVPageLayout
from .metrics import KVCacheLoadStats

__all__ = [
    "GeneratedKVCacheLoad",
    "KVAccessPattern",
    "KVCacheLoadConfig",
    "KVCacheLoadGenerator",
    "KVCacheLoadStats",
    "KVLoadGranularity",
    "KVPageLayout",
    "load_kv_cache_load_config",
]
