"""Media metrics containers.

Defines per-batch (MediaMetrics) and cumulative (MediaSystemMetrics)
metrics structures for the media simulation layer.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class MediaMetrics:
    """Metrics returned by a single handler_mem_request call.

    Attributes:
        num_read_requests: Number of read requests in this batch.
        num_write_requests: Number of write requests in this batch.
        num_other_requests: Number of other request types.
        cycles: Total cycles consumed (filled by Ramulator backend).
        num_media_reqs: Total number of media-level requests processed.
        time: Simulation time in seconds (filled by Analytic/MQSim backends).
        bandwidth: Bandwidth in bytes/second (from backend).
        iops: Total IOPS (from backend).
        iops_read: Read IOPS (from backend).
        iops_write: Write IOPS (from backend).
    """
    num_read_requests: int = 0
    num_write_requests: int = 0
    num_other_requests: int = 0
    cycles: int = 0
    num_media_reqs: int = 0
    time: float = 0.0
    bandwidth: float = 0.0
    iops: float = 0.0
    iops_read: float = 0.0
    iops_write: float = 0.0

    def __add__(self, other: "MediaMetrics") -> "MediaMetrics":
        """Combine counters and time-weight rate metrics."""
        total_time = self.time + other.time

        def combined_rate(left: float, right: float) -> float:
            if total_time <= 0:
                return 0.0
            return (left * self.time + right * other.time) / total_time

        return MediaMetrics(
            num_read_requests=self.num_read_requests + other.num_read_requests,
            num_write_requests=self.num_write_requests + other.num_write_requests,
            num_other_requests=self.num_other_requests + other.num_other_requests,
            cycles=self.cycles + other.cycles,
            num_media_reqs=self.num_media_reqs + other.num_media_reqs,
            time=total_time,
            bandwidth=combined_rate(self.bandwidth, other.bandwidth),
            iops=combined_rate(self.iops, other.iops),
            iops_read=combined_rate(self.iops_read, other.iops_read),
            iops_write=combined_rate(self.iops_write, other.iops_write),
        )


@dataclass
class MediaSystemMetrics:
    """Cumulative metrics across multiple handler_mem_request calls.

    Attributes:
        num_read_requests: Accumulated read count.
        num_write_requests: Accumulated write count.
        num_other_requests: Accumulated other count.
        cycles: Accumulated cycles.
        num_media_reqs: Accumulated media request count.
        time: Accumulated time in seconds.
        bandwidth: Accumulated bandwidth (B/s).
        iops: Accumulated total IOPS.
        iops_read: Accumulated read IOPS.
        iops_write: Accumulated write IOPS.
        media_metrics_list: History of per-batch MediaMetrics.
    """
    num_read_requests: int = 0
    num_write_requests: int = 0
    num_other_requests: int = 0
    cycles: int = 0
    num_media_reqs: int = 0
    time: float = 0.0
    bandwidth: float = 0.0
    iops: float = 0.0
    iops_read: float = 0.0
    iops_write: float = 0.0
    media_metrics_list: List[MediaMetrics] = field(default_factory=list)

    def update_from_media(self, metrics: MediaMetrics):
        old_time = self.time

        # ---- additive counters (sum across batches) ----
        self.num_read_requests += metrics.num_read_requests
        self.num_write_requests += metrics.num_write_requests
        self.num_other_requests += metrics.num_other_requests
        self.cycles += metrics.cycles
        self.num_media_reqs += metrics.num_media_reqs
        self.time += metrics.time
        self.media_metrics_list.append(metrics)

        # ---- rate metrics (time-weighted average) ----
        if self.time <= 0:
            return

        if old_time > 0:
            # R_new = R_old × (T_old / T_new) + r_new × (t_new / T_new)
            old_weight = old_time / self.time
            new_weight = metrics.time / self.time
            self.iops = self.iops * old_weight + metrics.iops * new_weight
            self.iops_read = self.iops_read * old_weight + metrics.iops_read * new_weight
            self.iops_write = self.iops_write * old_weight + metrics.iops_write * new_weight
            self.bandwidth = (self.bandwidth * old_weight
                              + metrics.bandwidth * new_weight)
        else:
            # First batch — seed with its values directly
            self.iops = metrics.iops
            self.iops_read = metrics.iops_read
            self.iops_write = metrics.iops_write
            self.bandwidth = metrics.bandwidth
