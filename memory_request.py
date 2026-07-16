"""Memory request data structure.

MemoryRequest wraps a MemoryObject and maintains the list of decomposed
media-level requests.
"""

from dataclasses import dataclass, field
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from .memory_object import MemoryObject
    from .media.media_request import MediaRequest


@dataclass
class MemoryRequest:
    """Engine-level memory request wrapping a MemoryObject.

    Holds a reference to the MemoryObject and the corresponding list of
    media-level requests (MediaRequest) after decomposition.

    Attributes:
        memory_object: The logical memory operation.
        media_request_list: Decomposed media-level requests.
    """
    memory_object: "MemoryObject"
    media_request_list: List["MediaRequest"] = field(default_factory=list)
