from __future__ import annotations

from typing import Optional, Dict, Any, Tuple
import aiohttp

from .base import Connector


class AsterConnector(Connector):
    """Read-only Aster futures connector for reminder mode.

    For MVP, use latest price as a mid proxy via REST endpoint similar to
    Binance-style `/fapi/v1/ticker/price?symbol=...`.
    """

    def __init__(self, host: str = "https://fapi.asterdex.com", config: Optional[Dict[str, Any]] = None, limiter=None) -> None:
        super().__init__(name="aster", config=config, limiter=limiter)
        self.host = host.rstrip("/")

    async def get_mid_price(self, symbol: str, **kwargs) -> float:
        if self.limiter:
            await self.limiter.allow("aster", "global", 1)
        url = f"{self.host}/fapi/v1/ticker/price"
        params = {"symbol": symbol}
        s = await self.session()
        async with s.get(url, params=params, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
                # Expect {"symbol": "BTCUSDT", "price": "12345.67"}
                price = float(data["price"])  # type: ignore[index]
                return price

    async def get_order_book_summary(self, symbol: str, levels: int = 5) -> Dict[str, float]:
        if self.limiter:
            await self.limiter.allow("aster", "depth", 1)
        url = f"{self.host}/fapi/v1/depth"
        params = {"symbol": symbol, "limit": levels}
        s = await self.session()
        async with s.get(url, params=params, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                best_bid = float(bids[0][0]) if bids else None
                best_ask = float(asks[0][0]) if asks else None
                mid = None
                if best_bid is not None and best_ask is not None:
                    mid = (best_bid + best_ask) / 2.0
                spread_abs = None
                spread_pct = None
                if best_bid is not None and best_ask is not None:
                    spread_abs = best_ask - best_bid
                    spread_pct = spread_abs / mid if mid else None
                depth_qty_bid = sum(float(x[1]) for x in bids[:levels]) if bids else 0.0
                depth_qty_ask = sum(float(x[1]) for x in asks[:levels]) if asks else 0.0
                depth_qty = depth_qty_bid + depth_qty_ask
                depth_notional = 0.0
                for p, q in bids[:levels]:
                    depth_notional += float(p) * float(q)
                for p, q in asks[:levels]:
                    depth_notional += float(p) * float(q)
                return {
                    "best_bid": best_bid or 0.0,
                    "best_ask": best_ask or 0.0,
                    "spread_abs": spread_abs or 0.0,
                    "spread_pct": spread_pct or 0.0,
                    "depth_qty": depth_qty,
                    "depth_notional": depth_notional,
                }

    async def get_24h_stats(self, symbol: str) -> Dict[str, float]:
        if self.limiter:
            await self.limiter.allow("aster", "global", 1)
        url = f"{self.host}/fapi/v1/ticker/24hr"
        params = {"symbol": symbol}
        s = await self.session()
        async with s.get(url, params=params, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
                # volume = base volume, quoteVolume = quote volume
                return {
                    "volume": float(data.get("volume", 0.0)),
                    "quoteVolume": float(data.get("quoteVolume", 0.0)),
                }

    async def get_order_book_levels(self, symbol: str, levels: int = 50) -> Dict[str, list]:
        url = f"{self.host}/fapi/v1/depth"
        params = {"symbol": symbol, "limit": levels}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
                # data: { bids: [[price,qty],...], asks: [[price,qty],...] }
                bids = [[float(p), float(q)] for p, q in data.get("bids", [])[:levels]]
                asks = [[float(p), float(q)] for p, q in data.get("asks", [])[:levels]]
                return {"bids": bids, "asks": asks}

    async def get_funding_info(self, symbol: str) -> Dict[str, Optional[float]]:
        """Return futures funding info using premiumIndex endpoint.

        Expected fields (Binance-style): lastFundingRate, nextFundingTime.
        """
        if self.limiter:
            await self.limiter.allow("aster", "global", 1)
        url = f"{self.host}/fapi/v1/premiumIndex"
        params = {"symbol": symbol}
        s = await self.session()
        async with s.get(url, params=params, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
                rate = None
                next_time = None
                try:
                    rate = float(data.get("lastFundingRate")) if data.get("lastFundingRate") is not None else None
                except Exception:
                    pass
                try:
                    next_time = float(data.get("nextFundingTime")) if data.get("nextFundingTime") is not None else None
                except Exception:
                    pass
                return {"rate": rate, "next_time_ms": next_time}
