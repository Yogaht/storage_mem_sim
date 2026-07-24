"""Workload-level metrics for KV-cache loads."""

from dataclasses import dataclass


@dataclass(frozen=True)
class KVCacheLoadStats:
    """Describe logical demand and generated MemoryEngine traffic."""

    selected_tokens: int
    unique_pages: int
    logical_requests: int
    demand_bytes: int
    issued_bytes: int
    page_utilization: float
    read_amplification: float

