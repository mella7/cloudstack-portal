"""Wakes up SSE streams when a job gets a new log line or status."""

from __future__ import annotations

import asyncio
from collections import defaultdict


class Broker:
    def __init__(self) -> None:
        self._conds: dict[str, asyncio.Condition] = {}
        self._versions: dict[str, int] = defaultdict(int)

    def _cond(self, job_id: str) -> asyncio.Condition:
        if job_id not in self._conds:
            self._conds[job_id] = asyncio.Condition()
        return self._conds[job_id]

    def version(self, job_id: str) -> int:
        return self._versions[job_id]

    async def publish(self, job_id: str) -> None:
        cond = self._cond(job_id)
        async with cond:
            self._versions[job_id] += 1
            cond.notify_all()

    async def wait(self, job_id: str, seen: int, timeout: float) -> bool:
        """Wait until something was published after version `seen`. False on timeout."""
        cond = self._cond(job_id)
        try:
            async with asyncio.timeout(timeout):
                async with cond:
                    await cond.wait_for(lambda: self._versions[job_id] != seen)
            return True
        except TimeoutError:
            return False
