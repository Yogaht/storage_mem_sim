"""Media backend enumeration.

Defines the supported media simulation backend types.
"""

from enum import Enum


class MediaSystemBackend(Enum):
    """Supported media simulation backends.

    ANALYTIC: Pure analytical model using size/bandwidth (fast, low fidelity).
    RAMULATOR: Cycle-accurate DRAM simulation via Ramulator2.
    MQSIM: Event-driven SSD simulation via MQSim.
    """
    ANALYTIC = "analytic"
    RAMULATOR = "ramulator"
    MQSIM = "mqsim"
