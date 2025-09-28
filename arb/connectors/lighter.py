from __future__ import annotations

from typing import Optional, Dict, Any, List

import lighter
import aiohttp

from .base import Connector


class LighterConnector(Connector):
    """Read-only Lighter connector for reminder mode.

    For MVP, fetch a mid-price proxy via REST by querying order book details
    and computing (best_bid + best_ask)/2 when possible.
    """

    def __init__(self, host: str = "https://mainnet.zklighter.elliot.ai", config: Optional[Dict[str, Any]] = None, limiter=None) -> None:
        super().__init__(name="lighter", config=config, limiter=limiter)
        self.host = host
        self.api_client = lighter.ApiClient(configuration=lighter.Configuration(host=host))
        self.order_api = lighter.OrderApi(self.api_client)
        self.funding_api = lighter.FundingApi(self.api_client)
        self._books_cache: Optional[List[Dict[str, Any]]] = None

    async def get_mid_price(self, symbol: str, market_id: Optional[int] = None, **kwargs) -> float:
        """Return mid price for a market.

        Prefer market_id if provided; symbol-to-id mapping should be handled by caller/config.
        """
        if market_id is None:
            raise ValueError("LighterConnector requires market_id for get_mid_price in MVP.")

        # Use orderBookOrders to fetch top of book (best bid/ask)
        if self.limiter:
            await self.limiter.allow("lighter", "global", 1)
        url = self.host.rstrip("/") + "/api/v1/orderBookOrders"
        params = {"market_id": market_id, "limit": 1}
        s = await self.session()
        async with s.get(url, params=params, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                best_bid = float(bids[0]["price"]) if bids else None
                best_ask = float(asks[0]["price"]) if asks else None
                if best_bid is None and best_ask is None:
                    raise RuntimeError("No bids/asks returned for market_id")
                if best_bid is None:
                    return float(best_ask)
                if best_ask is None:
                    return float(best_bid)
                return (best_bid + best_ask) / 2.0

    async def fetch_market_map(self) -> Dict[str, int]:
        """Fetch symbol -> market_id mapping from /api/v1/orderBooks.

        Returns a dict like {"BTC": 0, "BTCUSDT": 0, ...} depending on API symbols.
        """
        if self.limiter:
            await self.limiter.allow("lighter", "global", 1)
        url = self.host.rstrip("/") + "/api/v1/orderBooks"
        s = await self.session()
        async with s.get(url, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
                books: List[Dict[str, Any]] = data.get("order_books", [])
                self._books_cache = books
                mapping: Dict[str, int] = {}
                for ob in books:
                    sym = str(ob.get("symbol"))
                    mid = int(ob.get("market_id"))
                    mapping[sym] = mid
                return mapping

    async def get_order_book_summary(self, market_id: int, levels: int = 5) -> Dict[str, float]:
        if self.limiter:
            await self.limiter.allow("lighter", "global", 1)
        url = self.host.rstrip("/") + "/api/v1/orderBookOrders"
        params = {"market_id": market_id, "limit": levels}
        s = await self.session()
        async with s.get(url, params=params, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                best_bid = float(bids[0]["price"]) if bids else None
                best_ask = float(asks[0]["price"]) if asks else None
                if best_bid is None and best_ask is None:
                    raise RuntimeError("No bids/asks returned for market_id")
                mid = None
                if best_bid is not None and best_ask is not None:
                    mid = (best_bid + best_ask) / 2.0
                spread_abs = None
                spread_pct = None
                if best_bid is not None and best_ask is not None and mid:
                    spread_abs = best_ask - best_bid
                    spread_pct = spread_abs / mid
                # depth
                def _sum_levels(side):
                    total_qty = 0.0
                    total_notional = 0.0
                    for o in side[:levels]:
                        price = float(o.get("price"))
                        qty = float(o.get("remaining_base_amount", o.get("initial_base_amount", 0.0)))
                        total_qty += qty
                        total_notional += price * qty
                    return total_qty, total_notional

                depth_qty_bids, depth_notional_bids = _sum_levels(bids)
                depth_qty_asks, depth_notional_asks = _sum_levels(asks)
                return {
                    "best_bid": best_bid or 0.0,
                    "best_ask": best_ask or 0.0,
                    "spread_abs": spread_abs or 0.0,
                    "spread_pct": spread_pct or 0.0,
                    "depth_qty": depth_qty_bids + depth_qty_asks,
                    "depth_notional": depth_notional_bids + depth_notional_asks,
                }

    async def get_order_book_levels(self, market_id: int, levels: int = 50) -> Dict[str, list]:
        """Return raw top-N levels [[price, qty], ...] for bids and asks.
        """
        url = self.host.rstrip("/") + "/api/v1/orderBookOrders"
        params = {"market_id": market_id, "limit": levels}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
                bids = []
                asks = []
                for o in data.get("bids", [])[:levels]:
                    price = float(o.get("price"))
                    qty = float(o.get("remaining_base_amount", o.get("initial_base_amount", 0.0)))
                    bids.append([price, qty])
                for o in data.get("asks", [])[:levels]:
                    price = float(o.get("price"))
                    qty = float(o.get("remaining_base_amount", o.get("initial_base_amount", 0.0)))
                    asks.append([price, qty])
                return {"bids": bids, "asks": asks}

    async def get_fees(self, symbol: str) -> Dict[str, Optional[float]]:
        if self._books_cache is None:
            await self.fetch_market_map()
        fees = {"maker": None, "taker": None}
        if not self._books_cache:
            return fees
        for ob in self._books_cache:
            if str(ob.get("symbol")) == symbol:
                try:
                    fees["maker"] = float(ob.get("maker_fee"))
                    fees["taker"] = float(ob.get("taker_fee"))
                except Exception:
                    pass
                break
        return fees

    async def get_24h_stats(self, market_id: int) -> Dict[str, float]:
        url = self.host.rstrip("/") + "/api/v1/orderBookDetails"
        params = {"market_id": market_id}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
                # expect order_book_details list
                lst = data.get("order_book_details", [])
                if not lst:
                    return {"daily_base": 0.0, "daily_quote": 0.0}
                d = lst[0]
                return {
                    "daily_base": float(d.get("daily_base_token_volume", 0.0)),
                    "daily_quote": float(d.get("daily_quote_token_volume", 0.0)),
                }

    async def get_funding_info(self, symbol: str, cycle_hours: int = 8) -> Dict[str, Optional[float]]:
        """Return Lighter funding rate; countdown is approximated by cycle_hours.

        Since API does not expose next funding timestamp, we approximate next time
        by aligning to cycle_hours boundaries from epoch (UTC).
        """
        # funding via SDK (already uses aiohttp within ApiClient pool)
        rates = await self.funding_api.funding_rates()
        rate = None
        try:
            for fr in rates.funding_rates:
                if getattr(fr, "exchange", "") == "lighter" and getattr(fr, "symbol", "") == symbol:
                    rate = float(fr.rate)
                    break
        except Exception:
            pass
        # approximate next funding time assuming fixed cycle hours
        period_ms = cycle_hours * 3600 * 1000
        import time
        now_ms = int(time.time() * 1000)
        next_time_ms = ((now_ms // period_ms) + 1) * period_ms
        return {"rate": rate, "next_time_ms": float(next_time_ms)}
