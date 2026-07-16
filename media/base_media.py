"""Abstract base class for media simulation systems.

All media simulation backends must inherit from BaseMediaSystem and
implement the handler_mem_request method.
"""

from abc import ABC, abstractmethod
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from ..memory_request import MemoryRequest
from .media_config import MediaConfig
from .media_metrics import MediaMetrics


class BaseMediaSystem(ABC):
    """Abstract base class for all media simulation backends.

    Subclasses must implement handler_mem_request to process a batch of
    MemoryRequest objects and return performance metrics.

    Attributes:
        config: Media configuration (backend type, paths, parameters).
        system_metrics: Cumulative metrics accumulator.
    """

    def __init__(self, config: MediaConfig):
        """Initialize the media system with the given configuration.

        Args:
            config: MediaConfig with backend settings.
        """
        self.config = config
        # Import here to avoid circular dependency at module load time
        from .media_metrics import MediaSystemMetrics
        self.system_metrics = MediaSystemMetrics()

    @abstractmethod
    def handler_mem_request(
        self, mem_req_list: List["MemoryRequest"]
    ) -> MediaMetrics:
        """Process a batch of memory requests and return performance metrics.

        Args:
            mem_req_list: List of MemoryRequest objects to simulate.

        Returns:
            MediaMetrics containing the simulation results.
        """
        ...

    def get_system_metrics(self):
        """Return the cumulative system metrics.

        Returns:
            MediaSystemMetrics with accumulated history.
        """
        return self.system_metrics

    def reset_system_metrics(self):
        """Reset cumulative system metrics."""
        from .media_metrics import MediaSystemMetrics
        self.system_metrics = MediaSystemMetrics()
