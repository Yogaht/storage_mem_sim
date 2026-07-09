"""Memory type enumeration definitions.

Defines the memory/storage device types and request types used throughout
the MemEngine framework.
"""

from enum import Enum


class MemoryType(Enum):
    """Type of memory/storage medium."""
    HBM = "HBM"
    DDR = "DDR"
    SSD = "SSD"


class MemoryRequestType(Enum):
    """Type of memory request.

    KREAD: Kernel read (e.g., loading weights, KV cache read)
    KWRITE: Kernel write (e.g., storing activations, KV cache write)
    """
    KREAD = 0
    KWRITE = 1

    def to_media_req_type(self) -> int:
        """Convert to media-level request type (0=Read, 1=Write)."""
        return self.value
