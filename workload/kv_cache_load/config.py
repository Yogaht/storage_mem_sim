"""Configuration for KV-cache loading workloads."""

from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, Mapping, TypeVar

from ...memory_type import MemoryRequestType


_EnumT = TypeVar("_EnumT", bound=Enum)


def _parse_enum(
    value: Any,
    enum_type: type[_EnumT],
    field_name: str,
) -> _EnumT:
    """Parse a JSON string into an enum with a useful validation error."""
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        normalized = value.lower()
        for member in enum_type:
            if member.name.lower() == normalized:
                return member
            if (
                isinstance(member.value, str)
                and member.value.lower() == normalized
            ):
                return member
    valid = ", ".join(member.name.lower() for member in enum_type)
    raise ValueError(
        f"{field_name} must be one of [{valid}], got {value!r}"
    )


class KVAccessPattern(Enum):
    """How logical KV tokens are selected from the context."""

    CONTIGUOUS = "contiguous"
    SPARSE_UNIFORM = "sparse_uniform"
    SPARSE_PAGE_LOCAL = "sparse_page_local"


class KVLoadGranularity(Enum):
    """Logical request granularity submitted to ``MemoryEngine``."""

    TOKEN = "token"
    PAGE = "page"


@dataclass
class KVCacheLoadConfig:
    """Describe one backend-independent KV-cache load.

    ``token_size_bytes`` is the complete byte size of one logical KV token for
    the object/layer scope being simulated. A token is not decomposed into
    components by this workload.

    ``page_size_tokens`` describes a software KV page (vLLM block / SGLang
    page), not an OS, DRAM, or NAND page.
    """

    access_tokens: int
    context_tokens: int
    token_size_bytes: int
    pattern: KVAccessPattern
    granularity: KVLoadGranularity

    page_size_tokens: int = 1
    base_addr: int = 0
    page_alignment_bytes: int = 1

    # CONTIGUOUS only.
    start_token: int = 0

    # Sparse patterns.
    seed: int = 0
    selected_tokens_per_page: int = 1

    request_type: MemoryRequestType = MemoryRequestType.KREAD

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "KVCacheLoadConfig":
        """Build a validated config from a JSON-compatible mapping.

        ``pattern``, ``granularity`` and the optional ``request_type`` use
        case-insensitive enum names/values. ``workload_type`` may be present
        as ``"kv_cache_load"`` and is treated as file-format metadata.
        """
        if not isinstance(raw, Mapping):
            raise ValueError(
                "KV workload config must be a JSON object, got "
                f"{type(raw).__name__}"
            )

        values = dict(raw)
        workload_type = values.pop("workload_type", "kv_cache_load")
        if workload_type != "kv_cache_load":
            raise ValueError(
                "workload_type must be 'kv_cache_load', got "
                f"{workload_type!r}"
            )

        allowed = {field.name for field in fields(cls)}
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(
                "unknown KV workload config field(s): "
                + ", ".join(unknown)
            )

        required = {
            "access_tokens",
            "context_tokens",
            "token_size_bytes",
            "pattern",
            "granularity",
        }
        missing = sorted(required - set(values))
        if missing:
            raise ValueError(
                "missing required KV workload config field(s): "
                + ", ".join(missing)
            )

        values["pattern"] = _parse_enum(
            values["pattern"],
            KVAccessPattern,
            "pattern",
        )
        values["granularity"] = _parse_enum(
            values["granularity"],
            KVLoadGranularity,
            "granularity",
        )
        if "request_type" in values:
            values["request_type"] = _parse_enum(
                values["request_type"],
                MemoryRequestType,
                "request_type",
            )

        return cls(**values)

    def __post_init__(self) -> None:
        integer_fields = (
            "access_tokens",
            "context_tokens",
            "token_size_bytes",
            "page_size_tokens",
            "base_addr",
            "page_alignment_bytes",
            "start_token",
            "seed",
            "selected_tokens_per_page",
        )
        for field_name in integer_fields:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"{field_name} must be an integer, got "
                    f"{value!r} ({type(value).__name__})"
                )

        if self.access_tokens < 0:
            raise ValueError(
                f"access_tokens must be >= 0, got {self.access_tokens}"
            )
        if self.context_tokens < 0:
            raise ValueError(
                f"context_tokens must be >= 0, got {self.context_tokens}"
            )
        if self.access_tokens > self.context_tokens:
            raise ValueError(
                "access_tokens must not exceed context_tokens "
                f"(got {self.access_tokens} > {self.context_tokens})"
            )
        if self.token_size_bytes <= 0:
            raise ValueError(
                f"token_size_bytes must be > 0, got {self.token_size_bytes}"
            )
        if self.page_size_tokens <= 0:
            raise ValueError(
                f"page_size_tokens must be > 0, got {self.page_size_tokens}"
            )
        if self.base_addr < 0:
            raise ValueError(f"base_addr must be >= 0, got {self.base_addr}")
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
        if self.start_token < 0:
            raise ValueError(
                f"start_token must be >= 0, got {self.start_token}"
            )
        if not isinstance(self.pattern, KVAccessPattern):
            raise ValueError(
                f"pattern must be KVAccessPattern, got {self.pattern!r}"
            )
        if not isinstance(self.granularity, KVLoadGranularity):
            raise ValueError(
                "granularity must be KVLoadGranularity, got "
                f"{self.granularity!r}"
            )
        if not isinstance(self.request_type, MemoryRequestType):
            raise ValueError(
                "request_type must be MemoryRequestType, got "
                f"{self.request_type!r}"
            )

        if self.pattern is KVAccessPattern.CONTIGUOUS:
            end_token = self.start_token + self.access_tokens
            if end_token > self.context_tokens:
                raise ValueError(
                    "contiguous token range exceeds context_tokens "
                    f"(start={self.start_token}, count={self.access_tokens}, "
                    f"context={self.context_tokens})"
                )
        elif self.start_token != 0:
            raise ValueError(
                "start_token is only valid for the contiguous pattern, "
                f"got {self.start_token} for {self.pattern.value}"
            )

        if not 1 <= self.selected_tokens_per_page <= self.page_size_tokens:
            raise ValueError(
                "selected_tokens_per_page must be in "
                f"[1, page_size_tokens], got "
                f"{self.selected_tokens_per_page} with "
                f"page_size_tokens={self.page_size_tokens}"
            )
