"""Memory object data structure.

MemoryObject encapsulates a high-level memory operation (addr, size, type)
and computes how many media-level requests it will be split into.
"""

from dataclasses import dataclass
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .memory_config import MemoryEngineConfig
from .memory_type import MemoryRequestType


@dataclass
class MemoryObject:
    """Represents a logical memory operation before request decomposition.

    A MemoryObject is created for each high-level memory access (e.g., reading
    a KV cache block). It tracks the base address, total size, request type,
    and pre-computes how many media-level requests it maps to.

    Attributes:
        addr: Base address in bytes.
        size: Total size of the access in bytes.
        req_type: Type of request (KREAD or KWRITE).
        media_req_num: Number of media-level requests this object decomposes into,
                       computed as ceil(size / granularity).
    """
    addr: int
    size: int
    req_type: MemoryRequestType
    media_req_num: int = 0

    def __init__(
        self,
        addr: int,
        size: int,
        req_type: MemoryRequestType,
        config: "MemoryEngineConfig",
    ):
        self.addr = addr
        self.size = size
        self.req_type = req_type
        g = config.granularity
        self.media_req_num = math.ceil(size / g) if g > 0 else 0
