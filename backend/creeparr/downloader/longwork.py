"""The thread pool for blocking work that can take minutes to hours."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

T = TypeVar("T")


class LongWork:
    """yt-dlp, ffmpeg and hashing run here. They get their own pool so claims, DB updates
    and scans (asyncio's default pool, only cpu_count + 4 threads) never queue behind them."""

    def __init__(self, max_workers: int = 16) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="creeparr-long")

    async def run(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, functools.partial(fn, *args, **kwargs))
