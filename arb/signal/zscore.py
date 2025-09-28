from __future__ import annotations

from collections import deque
from typing import Deque, Tuple
import math


class RollingZScore:
    """Simple rolling z-score over a fixed window.

    Not optimized for large windows; fine for MVP/reminder mode.
    """

    def __init__(self, window: int) -> None:
        if window <= 1:
            raise ValueError("window must be > 1")
        self.window = window
        self.buf: Deque[float] = deque(maxlen=window)

    def update(self, value: float) -> Tuple[float, float, float]:
        """Add a new value and return (z, mean, std). If insufficient data, std=0 and z=0.
        """
        self.buf.append(value)
        n = len(self.buf)
        mean = sum(self.buf) / n
        # sample std (unbiased) when n>1
        if n > 1:
            var = sum((x - mean) ** 2 for x in self.buf) / (n - 1)
            std = math.sqrt(max(var, 0.0))
        else:
            std = 0.0
        if std > 0:
            z = (value - mean) / std
        else:
            z = 0.0
        return z, mean, std


class EMA:
    """Simple exponential moving average.

    alpha = 2/(window+1). Initialize with first value when seen.
    """

    def __init__(self, window: int) -> None:
        if window <= 0:
            raise ValueError("window must be > 0")
        self.window = window
        self.alpha = 2.0 / (window + 1.0)
        self.value = None  # type: ignore[assignment]

    def update(self, x: float) -> float:
        if self.value is None:
            self.value = x
        else:
            self.value = self.alpha * x + (1.0 - self.alpha) * self.value
        return float(self.value)
