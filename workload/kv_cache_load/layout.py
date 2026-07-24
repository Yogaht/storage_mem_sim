"""Byte layout for token-packed KV software pages."""

from dataclasses import dataclass
import math


def align_up(value: int, alignment: int) -> int:
    """Return *value* rounded up to a positive byte alignment."""
    if value < 0:
        raise ValueError(f"value must be >= 0, got {value}")
    if alignment <= 0:
        raise ValueError(f"alignment must be > 0, got {alignment}")
    return ((value + alignment - 1) // alignment) * alignment


@dataclass(frozen=True)
class KVPageLayout:
    """Map logical KV token/page IDs to byte addresses."""

    base_addr: int
    token_size_bytes: int
    page_size_tokens: int
    page_alignment_bytes: int = 1

    def __post_init__(self) -> None:
        if self.base_addr < 0:
            raise ValueError(f"base_addr must be >= 0, got {self.base_addr}")
        if self.token_size_bytes <= 0:
            raise ValueError(
                f"token_size_bytes must be > 0, got {self.token_size_bytes}"
            )
        if self.page_size_tokens <= 0:
            raise ValueError(
                f"page_size_tokens must be > 0, got {self.page_size_tokens}"
            )
        if self.page_alignment_bytes <= 0:
            raise ValueError(
                "page_alignment_bytes must be > 0, got "
                f"{self.page_alignment_bytes}"
            )
        if self.base_addr % self.page_alignment_bytes != 0:
            raise ValueError(
                "base_addr must be aligned to page_alignment_bytes "
                f"(got base_addr={self.base_addr}, "
                f"page_alignment_bytes={self.page_alignment_bytes})"
            )

    @property
    def page_data_bytes(self) -> int:
        """Useful KV bytes in one full software page."""
        return self.page_size_tokens * self.token_size_bytes

    @property
    def page_stride_bytes(self) -> int:
        """Byte stride between page starts, including alignment padding."""
        return align_up(self.page_data_bytes, self.page_alignment_bytes)

    def page_id(self, token_id: int) -> int:
        """Return the software-page ID containing *token_id*."""
        if token_id < 0:
            raise ValueError(f"token_id must be >= 0, got {token_id}")
        return token_id // self.page_size_tokens

    def page_addr(self, page_id: int) -> int:
        """Return the byte address of a software page."""
        if page_id < 0:
            raise ValueError(f"page_id must be >= 0, got {page_id}")
        return self.base_addr + page_id * self.page_stride_bytes

    def token_addr(self, token_id: int) -> int:
        """Return the byte address of a token within its software page."""
        page_id, offset = divmod(token_id, self.page_size_tokens)
        return self.page_addr(page_id) + offset * self.token_size_bytes

    @classmethod
    def required_region_size(
        cls,
        *,
        context_tokens: int,
        token_size_bytes: int,
        page_size_tokens: int,
        page_alignment_bytes: int = 1,
    ) -> int:
        """Return bytes required to store *context_tokens* KV slots."""
        if context_tokens < 0:
            raise ValueError(
                f"context_tokens must be >= 0, got {context_tokens}"
            )
        layout = cls(
            base_addr=0,
            token_size_bytes=token_size_bytes,
            page_size_tokens=page_size_tokens,
            page_alignment_bytes=page_alignment_bytes,
        )
        num_pages = math.ceil(context_tokens / page_size_tokens)
        return num_pages * layout.page_stride_bytes
