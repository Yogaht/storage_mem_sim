"""MediaSystem — Multi-backend memory/storage simulation layer.

Provides pluggable backends for memory/storage performance simulation:
- AnalyticMediaSystem: Pure analytical roofline estimation
- RamulatorMediaSystem: Cycle-accurate DRAM simulation via Ramulator2
- MQSimMediaSystem: Event-driven SSD simulation via MQSim
"""

from .media_backend import MediaSystemBackend
from .media_config import MediaConfig
from .media_request import MediaRequest
from .media_metrics import MediaMetrics, MediaSystemMetrics
from .base_media import BaseMediaSystem
from .media_system_factory import MediaSystemFactory
from .analytic_media_system import AnalyticMediaSystem
from .ramulator_media_system import RamulatorMediaSystem
from .mqsim_media_system import MQSimMediaSystem

__all__ = [
    "MediaSystemBackend",
    "MediaConfig",
    "MediaRequest",
    "MediaMetrics",
    "MediaSystemMetrics",
    "BaseMediaSystem",
    "MediaSystemFactory",
    "AnalyticMediaSystem",
    "RamulatorMediaSystem",
    "MQSimMediaSystem",
]
