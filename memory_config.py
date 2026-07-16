"""Memory engine configuration.

Defines the configuration parameters for the MemoryEngine.
Capacity values are auto-derived from media_config.capacity.
"""

from dataclasses import dataclass, field
from typing import Optional

from .memory_type import MemoryType


@dataclass
class MemoryEngineConfig:
    """Configuration for MemoryEngine.

    Attributes:
        memory_type: Type of memory (HBM, DDR, SSD).
        media_config: Media backend configuration. The MemoryEngine uses
                      MediaSystemFactory.create(media_config) internally.
                      Must set media_config.capacity (GB).
        granularity: Address alignment granularity in bytes. For Ramulator,
                     auto-derived from DRAM spec; fallback for other backends.
        dp_size: Data-parallel degree. When > 1, each DP0 request is replicated.
        storage_instance_num: Number of storage instances. Requests are evenly
                              distributed across instances.

    Auto-computed (from media_config.capacity):
        total_capacity: Total device capacity in bytes (= capacity_GB * 1024**3).
        per_dp_capacity: Capacity per DP rank (= total_capacity / dp_size).
        capacity: Capacity per storage instance (= total_capacity / storage_instance_num).
    """
    memory_type: MemoryType = MemoryType.HBM
    media_config: Optional[object] = None   # MediaConfig, set before use
    granularity: int = field(default=0, init=False)  # set by MemoryEngine from backend
    dp_size: int = 1
    storage_instance_num: int = 1

    # Auto-computed in __post_init__ from media_config.capacity
    total_capacity: int = field(default=0, init=False)
    per_dp_capacity: int = field(default=0, init=False)
    capacity: int = field(default=0, init=False)

    def __post_init__(self):
        if self.dp_size < 1:
            raise ValueError(f"dp_size must be >= 1, got {self.dp_size}")
        if self.storage_instance_num < 1:
            raise ValueError(f"storage_instance_num must be >= 1, got {self.storage_instance_num}")

        if self.media_config is not None:
            total_capacity = int(self.media_config.capacity * 1024 ** 3)
            self.total_capacity = total_capacity
            self.per_dp_capacity = total_capacity // self.dp_size
            self.capacity = total_capacity // self.storage_instance_num
