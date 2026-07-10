"""MemEngine — Memory/Storage Media Simulator Engine.

A multi-backend memory simulation framework providing:
- Address space management with alignment
- Request construction and decomposition
- Multi-DP / multi-storage-instance request transformation
- Pluggable media simulation backends
"""

from memory_type import MemoryType, MemoryRequestType
from memory_config import MemoryEngineConfig
from memory_object import MemoryObject
from memory_request import MemoryRequest
from memory_metrics import MemoryMetrics, MemoryEngineMetrics
from memory_engine import MemoryEngine

__all__ = [
    "MemoryType",
    "MemoryRequestType",
    "MemoryEngineConfig",
    "MemoryObject",
    "MemoryRequest",
    "MemoryMetrics",
    "MemoryEngineMetrics",
    "MemoryEngine",
]
