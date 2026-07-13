"""Media configuration.

Defines configuration parameters for the media simulation layer.
"""

from dataclasses import dataclass
from .media_backend import MediaSystemBackend


@dataclass
class MediaConfig:
    """Configuration for a media simulation backend.

    Attributes:
        media_type: Which backend to use (ANALYTIC, RAMULATOR, MQSIM).
        config_path: Path to the backend-specific configuration file
                     (YAML for Ramulator, XML for MQSim, unused for Analytic).
        capacity: Device capacity in GB.
        bandwidth: Peak bandwidth in GB/s (used by Analytic backend).
        granularity: Access granularity in bytes. For Ramulator, auto-derived
                     from DRAM spec; fallback for other backends.
    """
    media_type: MediaSystemBackend = MediaSystemBackend.ANALYTIC
    config_path: str = ""
    capacity: float = 0.0
    bandwidth: float = 0.0
    granularity: int = 64
