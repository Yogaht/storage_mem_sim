"""JSON loading helpers for KV-cache workload configurations."""

import json
from pathlib import Path
from typing import Union

from .config import KVCacheLoadConfig


PathLike = Union[str, Path]


def load_kv_cache_load_config(path: PathLike) -> KVCacheLoadConfig:
    """Load and validate a ``KVCacheLoadConfig`` from a JSON file."""
    config_path = Path(path)
    try:
        with config_path.open(encoding="utf-8") as config_file:
            raw = json.load(config_file)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"invalid KV workload JSON in {config_path}: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})"
        ) from exc

    return KVCacheLoadConfig.from_dict(raw)
