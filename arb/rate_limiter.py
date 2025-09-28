from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional


class TokenBucket:
    """Simple async token bucket per key.

    capacity: max tokens
    refill_rate: tokens per second
    weight: tokens consumed per request (default 1)
    """

    def __init__(self, capacity: int, refill_rate: float) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def consume(self, weight: int = 1) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._last = now
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
            need = float(weight)
            while self._tokens < need:
                to_wait = (need - self._tokens) / self.refill_rate if self.refill_rate > 0 else 0.05
                await asyncio.sleep(max(to_wait, 0.01))
                now2 = time.monotonic()
                elapsed2 = now2 - self._last
                self._last = now2
                self._tokens = min(self.capacity, self._tokens + elapsed2 * self.refill_rate)
            self._tokens -= need


class RateLimiter:
    """Per-exchange token buckets for endpoints.

    config example:
    {
      "aster": {"global": {"capacity": 20, "refill": 10.0}, "depth": {"capacity": 10, "refill": 5.0}},
      "lighter": {"global": {"capacity": 20, "refill": 10.0}}
    }
    """

    def __init__(self, config: Optional[Dict] = None) -> None:
        self._buckets: Dict[str, TokenBucket] = {}
        if config:
            self.update(config)

    def _key(self, exchange: str, endpoint: str) -> str:
        return f"{exchange}:{endpoint}"

    def update(self, config: Dict) -> None:
        for exch, cfg in config.items():
            for ep, conf in cfg.items():
                cap = int(conf.get("capacity", 10))
                ref = float(conf.get("refill", 5.0))
                self._buckets[self._key(exch, ep)] = TokenBucket(cap, ref)

    async def allow(self, exchange: str, endpoint: str, weight: int = 1) -> None:
        bucket = self._buckets.get(self._key(exchange, endpoint))
        if bucket is None:
            # fallback to exchange global, else default permissive bucket
            bucket = self._buckets.get(self._key(exchange, "global"))
        if bucket is None:
            bucket = self._buckets.setdefault(self._key(exchange, "global"), TokenBucket(1000, 1000.0))
        await bucket.consume(weight)

