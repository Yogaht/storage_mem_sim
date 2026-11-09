from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

from .memory_type import MemoryRequestType


@dataclass(frozen=True)
class RequestRun:
    """保存等步长请求及交错组件，描述原始 IO 边界和顺序，不持有 KV 数据。

    Args:
        bases: 首个位置的组件地址，单位字节，元组顺序即组件请求顺序。
        stride: 相邻位置的地址步长，单位字节，允许重复访问同一地址。
        count: 位置数量，每个位置按 bases 顺序各产生一个请求。
        size: 每个原始请求的字节数，不合并组件或相邻位置。
        req_type: 所有位置的内存访问类型。
    """

    bases: tuple[int, ...]
    stride: int
    count: int
    size: int
    req_type: MemoryRequestType = MemoryRequestType.KREAD


class RequestColumns:
    """一次搬运独占的只读请求区间；三个列视图沿用 issue_request 的现有参数。

    调用链：Host Pool 准备布局 → issue_request → Analytic 汇总或原序展开。
    实例只保存区间元数据，层偏移通过共享不可变区间实现。
    """

    def __init__(self, runs: tuple[RequestRun, ...], offset: int = 0):
        self.runs = tuple(runs)
        self.offset = offset
        ends = []
        count = 0
        for run in self.runs:
            if run.count <= 0 or not run.bases or run.stride < 0 or run.size <= 0:
                raise ValueError('Invalid compact request run')
            if not isinstance(run.req_type, MemoryRequestType):
                raise ValueError('Invalid compact request type')
            count += run.count * len(run.bases)
            ends.append(count)
        self.ends = tuple(ends)
        self.length = count
        self.total_bytes = sum(run.count * len(run.bases) * run.size for run in self.runs)
        self.addr = _RequestColumn(self, 0)
        self.size = _RequestColumn(self, 1)
        self.req_type = _RequestColumn(self, 2)

    def shifted(self, offset: int) -> 'RequestColumns':
        """生成本层地址视图，共享区间与累计长度，不重新校验或遍历请求。"""
        result = object.__new__(RequestColumns)
        result.runs, result.ends = self.runs, self.ends
        result.length, result.total_bytes = self.length, self.total_bytes
        result.offset = self.offset + offset
        result.addr = _RequestColumn(result, 0)
        result.size = _RequestColumn(result, 1)
        result.req_type = _RequestColumn(result, 2)
        return result

    def summarize(self, windows: Sequence[tuple[int, int]], capacity: int | None = None
                  ) -> list[tuple[int, int, int, int]]:
        """完整校验后按窗口汇总字节、读数、写数和片段数，不展开逐请求数组。

        Args:
            windows: 各实例的全局半开地址窗口，单位为字节。
            capacity: 池容量字节数；单 Engine 调用不额外限制地址上界。
        Returns:
            按窗口排列的 (bytes, reads, writes, requests) 元组。
        Raises:
            ValueError: 请求地址为负或超出池容量。
        """
        totals = [[0, 0, 0, 0] for _ in windows]
        for run in self.runs:
            for base in run.bases:
                base += self.offset
                end = base + (run.count - 1) * run.stride + run.size
                if base < 0 or (capacity is not None and end > capacity):
                    raise ValueError(f'Request range [{base}, {end}) exceeds pool capacity {capacity}')
                for target, (lo, hi) in zip(totals, windows):
                    if not run.stride:
                        count = run.count if base < hi and base + run.size > lo else 0
                        size = count * max(0, min(base + run.size, hi) - max(base, lo))
                    else:
                        stride = run.stride
                        first = max(0, (lo - run.size - base) // stride + 1)
                        last = min(run.count - 1, (hi - 1 - base) // stride)
                        count = max(0, last - first + 1)
                        if not count:
                            continue
                        size = count * run.size
                        # 边界处每条原始请求仍产生一个片段，仅用等差和扣除窗口外字节。
                        left_last = min(last, (lo - base - 1) // stride)
                        if left_last >= first:
                            number = left_last - first + 1
                            size -= number * (lo - base) - stride * (first + left_last) * number // 2
                        right_first = max(first, (hi - base - run.size) // stride + 1)
                        if right_first <= last:
                            number = last - right_first + 1
                            size -= number * (base + run.size - hi) + stride * (right_first + last) * number // 2
                    target[0] += size
                    target[1] += count if run.req_type == MemoryRequestType.KREAD else 0
                    target[2] += count if run.req_type == MemoryRequestType.KWRITE else 0
                    target[3] += count
        return [tuple(total) for total in totals]


class _RequestColumn(Sequence):
    """提供原始请求顺序的列视图；周期级后端按索引完整消费。"""

    def __init__(self, owner: RequestColumns, column: int):
        self.owner, self.column = owner, column

    def __len__(self):
        return self.owner.length

    def __getitem__(self, index):
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        part = bisect_right(self.owner.ends, index)
        run = self.owner.runs[part]
        if self.column == 1:
            return run.size
        if self.column == 2:
            return run.req_type
        previous = self.owner.ends[part - 1] if part else 0
        token, component = divmod(index - previous, len(run.bases))
        return run.bases[component] + token * run.stride + self.owner.offset


def compact_columns(addr, size, req_type) -> RequestColumns | None:
    """识别属于同一搬运的三个完整列，其他输入继续使用现有逐请求校验。"""
    if (isinstance(addr, _RequestColumn) and isinstance(size, _RequestColumn)
            and isinstance(req_type, _RequestColumn)
            and addr.column == 0 and size.column == 1 and req_type.column == 2
            and addr.owner is size.owner is req_type.owner):
        return addr.owner
    return None
