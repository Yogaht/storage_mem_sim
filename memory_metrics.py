"""Memory engine metrics containers.

Defines per-request (MemoryMetrics) and cumulative (MemoryEngineMetrics)
metrics structures for the MemoryEngine.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class MemoryMetrics:
    """Metrics for a single issue_request call.

    Attributes:
        cycles: Total cycles consumed (from media backend).
        total_time: Total time in seconds (from media backend).
        memory_scale_factor: Scale factor applied for time conversion.
        memory_reqs_num: Number of engine-level requests in the simulated
                         storage instance.
        global_memory_reqs_num: Total engine-level requests across all
                                DP ranks and storage instances.
    """
    cycles: int = 0
    total_time: float = 0.0
    memory_scale_factor: int = 1
    memory_reqs_num: int = 0
    global_memory_reqs_num: int = 0


@dataclass
class MemoryEngineMetrics:
    """Cumulative metrics across all issue_request calls.

    Attributes:
        cycles: Accumulated cycles.
        total_time: Accumulated total time in seconds.
        total_bytes: Accumulated bytes transferred by the simulated
                     storage instance.
        memory_reqs_num: Accumulated engine-level requests in the
                         simulated storage instance.
        global_memory_reqs_num: Accumulated engine-level requests
                                across all DP ranks and instances.
        mem_metrics_list: History of per-request MemoryMetrics.
        avg_bandwidth: Cumulative bandwidth = total_bytes / total_time (B/s).
    """
    cycles: int = 0
    total_time: float = 0.0
    total_bytes: int = 0
    memory_reqs_num: int = 0
    global_memory_reqs_num: int = 0
    mem_metrics_list: List[MemoryMetrics] = field(default_factory=list)
    avg_bandwidth: float = 0.0

    def update(self, metrics: MemoryMetrics, total_bytes: int):
        """Accumulate a single MemoryMetrics into the cumulative counters."""
        self.cycles += metrics.cycles
        self.total_time += metrics.total_time
        self.total_bytes += total_bytes
        self.memory_reqs_num += metrics.memory_reqs_num
        self.global_memory_reqs_num += metrics.global_memory_reqs_num
        self.mem_metrics_list.append(metrics)

        if self.total_time > 0:
            self.avg_bandwidth = self.total_bytes / self.total_time
