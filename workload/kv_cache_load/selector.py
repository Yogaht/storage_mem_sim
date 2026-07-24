"""Logical token selection for KV-cache loads."""

import math
import random
from typing import List

from .config import KVAccessPattern, KVCacheLoadConfig


class KVTokenSelector:
    """Generate ordered logical token IDs from a load configuration."""

    def select(self, config: KVCacheLoadConfig) -> List[int]:
        """Return token IDs in the exact order that should be issued."""
        if config.access_tokens == 0:
            return []
        if config.pattern is KVAccessPattern.CONTIGUOUS:
            return list(
                range(
                    config.start_token,
                    config.start_token + config.access_tokens,
                )
            )
        if config.pattern is KVAccessPattern.SPARSE_UNIFORM:
            rng = random.Random(config.seed)
            return rng.sample(
                range(config.context_tokens),
                config.access_tokens,
            )
        if config.pattern is KVAccessPattern.SPARSE_PAGE_LOCAL:
            return self._select_page_local(config)
        raise ValueError(f"unsupported KV access pattern: {config.pattern!r}")

    @staticmethod
    def _select_page_local(config: KVCacheLoadConfig) -> List[int]:
        """Select up to a fixed number of tokens from random KV pages."""
        rng = random.Random(config.seed)
        page_size = config.page_size_tokens
        per_page = config.selected_tokens_per_page
        num_pages = math.ceil(config.context_tokens / page_size)

        selectable = sum(
            min(per_page, config.context_tokens - page_id * page_size)
            for page_id in range(num_pages)
        )
        if config.access_tokens > selectable:
            raise ValueError(
                "access_tokens cannot be represented with "
                "selected_tokens_per_page; increase "
                "selected_tokens_per_page or reduce access_tokens "
                f"(access_tokens={config.access_tokens}, "
                f"selectable={selectable})"
            )

        page_ids = list(range(num_pages))
        rng.shuffle(page_ids)

        selected: List[int] = []
        for page_id in page_ids:
            page_start = page_id * page_size
            valid_tokens = min(
                page_size,
                config.context_tokens - page_start,
            )
            offsets = list(range(valid_tokens))
            rng.shuffle(offsets)

            remaining = config.access_tokens - len(selected)
            count = min(per_page, remaining, valid_tokens)
            selected.extend(page_start + offset for offset in offsets[:count])
            if len(selected) == config.access_tokens:
                break

        return selected

