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
        """Combine two MediaMetrics by summing all fields."""
        return MediaMetrics(
            num_read_requests=self.num_read_requests + other.num_read_requests,
            num_write_requests=self.num_write_requests + other.num_write_requests,
            num_other_requests=self.num_other_requests + other.num_other_requests,
            cycles=self.cycles + other.cycles,
            num_media_reqs=self.num_media_reqs + other.num_media_reqs,
            time=self.time + other.time,
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
        """Accumulate a MediaMetrics batch into the cumulative counters.

        Args:
            metrics: Per-batch metrics to accumulate.
        """
        self.num_read_requests += metrics.num_read_requests
        self.num_write_requests += metrics.num_write_requests
        self.num_other_requests += metrics.num_other_requests
        self.cycles += metrics.cycles
        self.num_media_reqs += metrics.num_media_reqs
        self.time += metrics.time
        self.media_metrics_list.append(metrics)
