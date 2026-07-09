"""Media-level request data structure.

MediaRequest represents a single access at the media simulation granularity,
decomposed from a higher-level MemoryObject.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class MediaRequest:
    """A single media-level access request.

    Each MediaRequest corresponds to one granularity-sized chunk of a
    higher-level MemoryObject. For DRAM simulation, this is typically a
    cache-line access; for SSD, a sector-range access.

    Attributes:
        addr: Starting address of this access.
        addr_vec: Vector of sub-addresses (for bank/rank-level simulation).
        req_type: Request type (0=Read, 1=Write).
    """
    addr: int
    req_type: int
    addr_vec: List[int] = field(default_factory=list)
