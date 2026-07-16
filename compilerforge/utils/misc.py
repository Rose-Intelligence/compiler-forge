"""Small helpers shared by the neurons."""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
from math import floor
from typing import Any, TypeVar

T = TypeVar("T")


def ttl_cache(maxsize: int = 128, typed: bool = False, ttl: int = -1):
    """LRU cache whose entries expire after ``ttl`` seconds.

    Chain reads are the main user: a neuron asks for the current block many times
    inside one round, and hammering the endpoint for a value that changes every
    twelve seconds is wasteful.
    """
    if ttl <= 0:
        ttl = 65536
    hash_gen = _ttl_hash_gen(ttl)

    def wrapper(func: Callable[..., T]) -> Callable[..., T]:
        @functools.lru_cache(maxsize, typed)
        def ttl_func(ttl_hash, *args, **kwargs):
            return func(*args, **kwargs)

        def wrapped(*args, **kwargs) -> T:
            return ttl_func(next(hash_gen), *args, **kwargs)

        return functools.update_wrapper(wrapped, func)

    return wrapper


def _ttl_hash_gen(seconds: int):
    start = time.time()
    while True:
        yield floor((time.time() - start) / seconds)


@ttl_cache(maxsize=1, ttl=12)
def ttl_get_block(self: Any) -> int:
    """Current chain block, cached for one block interval."""
    return self.subtensor.get_current_block()


def human_bytes(value: int | None) -> str:
    if value is None:
        return "n/a"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{value} B"
        value /= 1024.0
    return f"{value:.1f} TiB"


def human_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def short_digest(digest: str, length: int = 12) -> str:
    """Readable form of a sha256 reference for logs and tables."""
    return digest.removeprefix("sha256:")[:length]
