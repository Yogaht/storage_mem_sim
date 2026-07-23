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
        iops: IOPS (from media backend).
        avg_bandwidth: Bandwidth in bytes/second (from media backend).
    """
    cycles: int = 0
    total_time: float = 0.0
    memory_scale_factor: int = 1
    memory_reqs_num: int = 0
    global_memory_reqs_num: int = 0
    avg_bandwidth: float = 0.0
    iops: float = 0.0
    iops_read: float = 0.0
    iops_write: float = 0.0

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
        avg_bandwidth: Cumulative avg_bandwidth = total_bytes / total_time (B/s).
    """
    cycles: int = 0
    total_time: float = 0.0
    total_bytes: int = 0
    memory_reqs_num: int = 0
    global_memory_reqs_num: int = 0
    avg_bandwidth: float = 0.0
    iops: float = 0.0
    iops_read: float = 0.0
    iops_write: float = 0.0
    mem_metrics_list: List[MemoryMetrics] = field(default_factory=list)

    def update(self, metrics: MemoryMetrics, total_bytes: int):
        old_time = self.total_time

        # ---- additive counters ----
        self.cycles += metrics.cycles
        self.total_time += metrics.total_time
        self.total_bytes += total_bytes
        self.memory_reqs_num += metrics.memory_reqs_num
        self.global_memory_reqs_num += metrics.global_memory_reqs_num
        self.mem_metrics_list.append(metrics)

        if self.total_time <= 0:
            return

        # ---- avg_bandwidth: total_bytes / total_time (exact, always works) ----
        self.avg_bandwidth = self.total_bytes / self.total_time

        if old_time > 0 and metrics.iops > 0:
            old_weight = old_time / self.total_time
            new_weight = metrics.total_time / self.total_time
            self.iops = (self.iops * old_weight + metrics.iops * new_weight)
            self.iops_read = (self.iops_read * old_weight + metrics.iops_read * new_weight)
            self.iops_write = (self.iops_write * old_weight + metrics.iops_write * new_weight)
        elif old_time == 0 and metrics.iops > 0:
            self.iops = metrics.iops
            self.iops_read = metrics.iops_read
            self.iops_write = metrics.iops_write

