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

        IOPS and bandwidth are **rate metrics** (operations/sec, bytes/sec) —
        they cannot be summed across batches.  Each batch's values come from
        the simulator and already reflect the correct trace-line-level request
        count (after slicing by request_size).  ``num_media_reqs``, by
        contrast, counts MemoryRequest objects *before* slicing, so::

            IOPS  ≠  num_media_reqs / time

        ── Why time-weighted averaging is correct ──

        Let batch *i* have rate Rᵢ, duration tᵢ, and underlying quantity Qᵢ
        (operations or bytes such that Rᵢ = Qᵢ / tᵢ).

        The true cumulative rate across *n* batches is the **total quantity
        divided by total time**::

                        Σᵢ Qᵢ      Σᵢ (Rᵢ × tᵢ)
            R_cum  =  ───────  =  ──────────────   … ①
                        Σᵢ tᵢ         Σᵢ tᵢ

        This is the definition of a **time-weighted average**: each batch's
        rate is weighted by its proportion of total duration::

            R_cum  =  Σᵢ ( Rᵢ × (tᵢ / Σⱼ tⱼ) )   … ②

        Formula ① (recalculate from totals) and ② (time-weighted average)
        are **mathematically identical**.  We use the time-weighted form
        because it does not require storing the per-batch quantities Qᵢ
        (total bytes or total trace operations); only the rates and
        durations are needed.

        ── Iterative (online) computation ──

        After *k-1* batches, let ``R`` be the cumulative rate and ``T``
        the accumulated time.  When batch *k* arrives with rate ``r`` and
        duration ``t``::

            R_new  =  R × T/(T+t)  +  r × t/(T+t)

        This avoids storing the full history — O(1) space per metric.

        ── Worked example ──

        Batch 1:  iops=1000K,  t=0.1 s   →   100K ops
        Batch 2:  iops= 500K,  t=0.2 s   →   100K ops

        Wrong:     1000K + 500K = 1500K          ✗
        Time-wtd:  (1000K×0.1 + 500K×0.2)/0.3
                 = (100K + 100K)/0.3 = 666.7K    ✓  (200K ops / 0.3 s)

        Args:
            metrics: Per-batch metrics to accumulate.
        """
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
