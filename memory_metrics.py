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
        iops: Logical engine requests / time for this call.
        backend_iops: IOPS reported by the media backend for this call.
        bandwidth: Bandwidth in bytes/second (from media backend).
    """
    cycles: int = 0
    total_time: float = 0.0
    memory_scale_factor: int = 1
    memory_reqs_num: int = 0
    global_memory_reqs_num: int = 0
    global_memory_read_reqs_num: int = 0
    global_memory_write_reqs_num: int = 0
    bandwidth: float = 0.0
    iops: float = 0.0
    iops_read: float = 0.0
    iops_write: float = 0.0
    backend_iops: float = 0.0
    backend_iops_read: float = 0.0
    backend_iops_write: float = 0.0

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
        global_memory_read_reqs_num: Accumulated global logical reads.
        global_memory_write_reqs_num: Accumulated global logical writes.
        mem_metrics_list: History of per-request MemoryMetrics.
        bandwidth: Cumulative bandwidth = total_bytes / total_time (B/s).
        iops: Global logical engine requests / cumulative time.
        backend_iops: Time-weighted IOPS reported by the media backend.
    """
    cycles: int = 0
    total_time: float = 0.0
    total_bytes: int = 0
    memory_reqs_num: int = 0
    global_memory_reqs_num: int = 0
    global_memory_read_reqs_num: int = 0
    global_memory_write_reqs_num: int = 0
    bandwidth: float = 0.0
    iops: float = 0.0
    iops_read: float = 0.0
    iops_write: float = 0.0
    backend_iops: float = 0.0
    backend_iops_read: float = 0.0
    backend_iops_write: float = 0.0
    mem_metrics_list: List[MemoryMetrics] = field(default_factory=list)

    def update(self, metrics: MemoryMetrics, total_bytes: int):
        old_time = self.total_time

        # ---- additive counters ----
        self.cycles += metrics.cycles
        self.total_time += metrics.total_time
        self.total_bytes += total_bytes
        self.memory_reqs_num += metrics.memory_reqs_num
        self.global_memory_reqs_num += metrics.global_memory_reqs_num
        self.global_memory_read_reqs_num += metrics.global_memory_read_reqs_num
        self.global_memory_write_reqs_num += metrics.global_memory_write_reqs_num
        self.mem_metrics_list.append(metrics)

        if self.total_time <= 0:
            return

        # ---- bandwidth: total_bytes / total_time (exact, always works) ----
        self.bandwidth = self.total_bytes / self.total_time
        # Engine IOPS uses one stable request level across all backends.
        self.iops = self.global_memory_reqs_num / self.total_time
        self.iops_read = self.global_memory_read_reqs_num / self.total_time
        self.iops_write = self.global_memory_write_reqs_num / self.total_time

        if old_time > 0:
            old_weight = old_time / self.total_time
            new_weight = metrics.total_time / self.total_time
            self.backend_iops = (
                self.backend_iops * old_weight
                + metrics.backend_iops * new_weight
            )
            self.backend_iops_read = (
                self.backend_iops_read * old_weight
                + metrics.backend_iops_read * new_weight
            )
            self.backend_iops_write = (
                self.backend_iops_write * old_weight
                + metrics.backend_iops_write * new_weight
            )
        else:
            self.backend_iops = metrics.backend_iops
            self.backend_iops_read = metrics.backend_iops_read
            self.backend_iops_write = metrics.backend_iops_write
