from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, List
import time


@dataclass
class Market:
    exchange: str  # "lighter" | "aster" | others
    symbol: str    # standardized symbol, e.g. "BTCUSDT" (no dash), or keep raw and use mapping
    market_id: Optional[int] = None  # for lighter (if needed)


@dataclass
class Pair:
    name: str                 # e.g. "BTCUSDT"
    a: Market                 # market leg A
    b: Market                 # market leg B


@dataclass
class SpreadSample:
    ts_ms: int
    price_a: float
    price_b: float
    spread: float


@dataclass
class ZScoreSignal:
    ts_ms: int
    pair: str
    z: float
    spread: float
    mean: float
    std: float
    action: str  # "enter_short_A_long_B", "enter_long_A_short_B", "exit", "hold"


def now_ms() -> int:
    return int(time.time() * 1000)

