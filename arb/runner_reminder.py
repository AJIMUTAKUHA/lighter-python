from __future__ import annotations

import asyncio
from typing import Dict, Any, List

from .config import load_config
from .models import Market, Pair, SpreadSample, ZScoreSignal, now_ms
from .signal.zscore import RollingZScore, EMA
from .connectors.base import Connector
from .connectors.lighter import LighterConnector
from .connectors.aster import AsterConnector
from .storage.sqlite import open_db, insert_spread
import os
import aiohttp
import math
from .rate_limiter import RateLimiter


def build_connectors(cfg: Dict[str, Any], limiter: RateLimiter | None = None) -> Dict[str, Connector]:
    conns: Dict[str, Connector] = {}
    conns["lighter"] = LighterConnector(host=cfg["lighter_host"], limiter=limiter)
    conns["aster"] = AsterConnector(host=cfg["aster_host"], limiter=limiter)
    return conns


def build_pairs(cfg: Dict[str, Any]) -> List[Pair]:
    pairs: List[Pair] = []
    for p in cfg.get("pairs", []):
        a = p["a"]; b = p["b"]
        pairs.append(
            Pair(
                name=p["name"],
                a=Market(exchange=a["exchange"], symbol=a.get("symbol"), market_id=a.get("market_id")),
                b=Market(exchange=b["exchange"], symbol=b.get("symbol"), market_id=b.get("market_id")),
            )
        )
    return pairs


async def poll_pair(pair: Pair, conns: Dict[str, Connector], z: RollingZScore, enter_z: float, exit_z: float, poll_ms: int) -> None:
    while True:
        try:
            conn_a = conns[pair.a.exchange]
            conn_b = conns[pair.b.exchange]

            price_a_coro = conn_a.get_mid_price(symbol=pair.a.symbol, market_id=pair.a.market_id)
            price_b_coro = conn_b.get_mid_price(symbol=pair.b.symbol, market_id=pair.b.market_id)
            price_a, price_b = await asyncio.gather(price_a_coro, price_b_coro)
            spread = price_a - price_b
            zscore, mean, std = z.update(spread)
            ts = now_ms()

            action = "hold"
            if zscore >= enter_z:
                action = "enter_short_A_long_B"
            elif zscore <= -enter_z:
                action = "enter_long_A_short_B"
            elif abs(zscore) <= exit_z:
                action = "exit"

            # For MVP, print to console; later push to Panel via WS/SSE
            print(
                f"[{ts}] {pair.name} A={pair.a.exchange}:{price_a:.2f} B={pair.b.exchange}:{price_b:.2f} "
                f"spread={spread:.4f} z={zscore:.3f} mean={mean:.4f} std={std:.4f} action={action}"
            )
        except Exception as e:
            print(f"[{now_ms()}] ERROR pair={pair.name}: {e}")

        await asyncio.sleep(poll_ms / 1000)


async def poll_pair_with_store(pair, conns, z, enter_z, exit_z, poll_ms, db):
    cfg = load_config()
    depth_levels = int(cfg.get("depth_levels", 5))
    ema_calc = EMA(int(cfg.get("ema_window", 30)))
    fees_cfg = cfg.get("fees", {})
    panel_ingest_url = os.getenv("PANEL_INGEST_URL")  # e.g., http://localhost:8000/api/ingest/spread
    funding_cfg = cfg.get("funding", {})
    cycle_hours = funding_cfg.get("cycle_hours", {"aster": 8, "lighter": 8})
    notional_usd = float(funding_cfg.get("notional_usd", 1000.0))
    stale_ms_th = int(cfg.get("stale_ms_threshold", 3000))
    skew_ms_th = int(cfg.get("skew_ms_threshold", 500))
    while True:
        try:
            conn_a = conns[pair.a.exchange]
            conn_b = conns[pair.b.exchange]

            async def timed(coro):
                t0 = now_ms()
                val = await coro
                t1 = now_ms()
                return val, t1, t1 - t0

            (price_a, ts_a, dur_a), (price_b, ts_b, dur_b) = await asyncio.gather(
                timed(conn_a.get_mid_price(symbol=pair.a.symbol, market_id=pair.a.market_id)),
                timed(conn_b.get_mid_price(symbol=pair.b.symbol, market_id=pair.b.market_id)),
            )
            spread = price_a - price_b
            zscore, mean, std = z.update(spread)
            ema = ema_calc.update(spread)
            center_dev = (spread - ema) / std if std > 1e-12 else 0.0
            ts = max(ts_a, ts_b)

            age_a_ms = ts - ts_a
            age_b_ms = ts - ts_b
            skew_ms = abs(ts_a - ts_b)
            latency_ms = max(dur_a, dur_b)

            action = "hold"
            if zscore >= enter_z:
                action = "enter_short_A_long_B"
            elif zscore <= -enter_z:
                action = "enter_long_A_short_B"
            elif abs(zscore) <= exit_z:
                action = "exit"

            stale = 1 if (age_a_ms > stale_ms_th or age_b_ms > stale_ms_th or skew_ms > skew_ms_th) else 0
            if stale:
                action = "hold"

            # per-exchange extras: order book spread %, depth, volume, fees
            ob_a = {}
            ob_b = {}
            vol_a = None
            vol_b = None
            maker_a = None
            taker_a = None
            maker_b = None
            taker_b = None

            if pair.a.exchange == "lighter":
                coro1 = conns["lighter"].get_order_book_summary(market_id=pair.a.market_id, levels=depth_levels)  # type: ignore[attr-defined]
                coro2 = conns["lighter"].get_24h_stats(market_id=pair.a.market_id)  # type: ignore[attr-defined]
                ob_a, stats_a = await asyncio.gather(coro1, coro2)
                vol_a = stats_a.get("daily_quote")
                fees_a = await conns["lighter"].get_fees(pair.a.symbol)  # type: ignore[attr-defined]
                maker_a = fees_a.get("maker")
                taker_a = fees_a.get("taker")
            elif pair.a.exchange == "aster":
                coro1 = conns["aster"].get_order_book_summary(symbol=pair.a.symbol, levels=depth_levels)  # type: ignore[attr-defined]
                coro2 = conns["aster"].get_24h_stats(symbol=pair.a.symbol)  # type: ignore[attr-defined]
                ob_a, stats_a = await asyncio.gather(coro1, coro2)
                vol_a = stats_a.get("quoteVolume")
                fees = fees_cfg.get("aster", {}) if isinstance(fees_cfg, dict) else {}
                maker_a = fees.get("maker")
                taker_a = fees.get("taker")

            if pair.b.exchange == "lighter":
                coro1 = conns["lighter"].get_order_book_summary(market_id=pair.b.market_id, levels=depth_levels)  # type: ignore[attr-defined]
                coro2 = conns["lighter"].get_24h_stats(market_id=pair.b.market_id)  # type: ignore[attr-defined]
                ob_b, stats_b = await asyncio.gather(coro1, coro2)
                vol_b = stats_b.get("daily_quote")
                fees_b = await conns["lighter"].get_fees(pair.b.symbol)  # type: ignore[attr-defined]
                maker_b = fees_b.get("maker")
                taker_b = fees_b.get("taker")
            elif pair.b.exchange == "aster":
                coro1 = conns["aster"].get_order_book_summary(symbol=pair.b.symbol, levels=depth_levels)  # type: ignore[attr-defined]
                coro2 = conns["aster"].get_24h_stats(symbol=pair.b.symbol)  # type: ignore[attr-defined]
                ob_b, stats_b = await asyncio.gather(coro1, coro2)
                vol_b = stats_b.get("quoteVolume")
                fees = fees_cfg.get("aster", {}) if isinstance(fees_cfg, dict) else {}
                maker_b = fees.get("maker")
                taker_b = fees.get("taker")

            ob_spread_a = ob_a.get("spread_abs") if ob_a else None
            ob_spread_b = ob_b.get("spread_abs") if ob_b else None
            ob_spread_pct_a = ob_a.get("spread_pct") if ob_a else None
            ob_spread_pct_b = ob_b.get("spread_pct") if ob_b else None
            depth_qty_a = ob_a.get("depth_qty") if ob_a else None
            depth_qty_b = ob_b.get("depth_qty") if ob_b else None
            depth_notional_a = ob_a.get("depth_notional") if ob_a else None
            depth_notional_b = ob_b.get("depth_notional") if ob_b else None

            print(
                f"[{ts}] {pair.name} A={pair.a.exchange}:{price_a:.2f} B={pair.b.exchange}:{price_b:.2f} "
                f"spread={spread:.4f} z={zscore:.3f} ema={ema:.4f} center_dev={center_dev:.3f} action={action} "
                f"ob_spread_a={ob_spread_a} ob_spread_b={ob_spread_b} vol_a={vol_a} vol_b={vol_b}"
            )

            # store
            await insert_spread(
                db,
                pair.name,
                ts,
                price_a,
                price_b,
                spread,
                zscore,
                mean,
                std,
                ema=ema,
                center_dev=center_dev,
                ob_spread_a=ob_spread_a,
                ob_spread_b=ob_spread_b,
                ob_spread_pct_a=ob_spread_pct_a,
                ob_spread_pct_b=ob_spread_pct_b,
                vol_a=vol_a,
                vol_b=vol_b,
                depth_qty_a=depth_qty_a,
                depth_qty_b=depth_qty_b,
                depth_notional_a=depth_notional_a,
                depth_notional_b=depth_notional_b,
                maker_fee_a=maker_a,
                taker_fee_a=taker_a,
                maker_fee_b=maker_b,
                taker_fee_b=taker_b,
                # enriched later with funding and reversion hints
                age_a_ms=age_a_ms,
                age_b_ms=age_b_ms,
                skew_ms=skew_ms,
                latency_ms=latency_ms,
                stale=stale,
            )

            # optional: push to panel ingest for WS broadcast
            if panel_ingest_url:
                # compute funding info
                fr_a_rate = None; fr_b_rate = None
                next_a = None; next_b = None
                try:
                    if pair.a.exchange == "aster":
                        finfo = await conns["aster"].get_funding_info(symbol=pair.a.symbol)  # type: ignore[attr-defined]
                        fr_a_rate = finfo.get("rate")
                        next_a = finfo.get("next_time_ms")
                    else:
                        finfo = await conns["lighter"].get_funding_info(symbol=pair.a.symbol, cycle_hours=int(cycle_hours.get("lighter", 8)))  # type: ignore[attr-defined]
                        fr_a_rate = finfo.get("rate")
                        next_a = finfo.get("next_time_ms")
                except Exception:
                    pass
                try:
                    if pair.b.exchange == "aster":
                        finfo = await conns["aster"].get_funding_info(symbol=pair.b.symbol)  # type: ignore[attr-defined]
                        fr_b_rate = finfo.get("rate")
                        next_b = finfo.get("next_time_ms")
                    else:
                        finfo = await conns["lighter"].get_funding_info(symbol=pair.b.symbol, cycle_hours=int(cycle_hours.get("lighter", 8)))  # type: ignore[attr-defined]
                        fr_b_rate = finfo.get("rate")
                        next_b = finfo.get("next_time_ms")
                except Exception:
                    pass

                now = ts
                countdown_ms = None
                try:
                    nexts = [x for x in [next_a, next_b] if x]
                    if nexts:
                        countdown_ms = min(nexts) - now
                except Exception:
                    pass

                # estimate half-life and time to exit threshold
                half_life_s, t_exit_s = estimate_reversion_times(z, zscore, float(cfg.get("exit_z", 0.5)), int(cfg.get("poll_ms", 1000)))

                # funding suggestion: if we entered now in suggested direction, compare t_exit with countdown
                advice = None
                if countdown_ms and t_exit_s:
                    # net funding per period for long on X and short on Y is (rate_short - rate_long)
                    if action == "enter_short_A_long_B" and fr_a_rate is not None and fr_b_rate is not None:
                        net_rate = (fr_a_rate) - (fr_b_rate)  # short A (receives fr_a), long B (pays fr_b) => fr_short - fr_long = fr_a - fr_b
                    elif action == "enter_long_A_short_B" and fr_a_rate is not None and fr_b_rate is not None:
                        net_rate = (fr_b_rate) - (fr_a_rate)  # short B, long A
                    else:
                        net_rate = None
                    if net_rate is not None:
                        time_to_funding_s = max(0, int(countdown_ms / 1000))
                        if t_exit_s < time_to_funding_s:
                            advice = "预计在下一次资金费率前收敛，可考虑规避资金费率影响"
                        else:
                            advice = "预计跨越下一次资金费率，可评估净资金费率收益/成本后决定"
                        net_funding_cycle_usd = notional_usd * net_rate
                        expect_funding_next_usd = net_funding_cycle_usd if t_exit_s >= time_to_funding_s else 0.0
                    else:
                        net_funding_cycle_usd = None
                        expect_funding_next_usd = None
                else:
                    net_funding_cycle_usd = None
                    expect_funding_next_usd = None

                payload = {
                    "pair": pair.name,
                    "ts_ms": ts,
                    "price_a": price_a,
                    "price_b": price_b,
                    "spread": spread,
                    "z": zscore,
                    "mean": mean,
                    "std": std,
                    "ema": ema,
                    "center_dev": center_dev,
                    "ob_spread_a": ob_spread_a,
                    "ob_spread_b": ob_spread_b,
                    "ob_spread_pct_a": ob_spread_pct_a,
                    "ob_spread_pct_b": ob_spread_pct_b,
                    "vol_a": vol_a,
                    "vol_b": vol_b,
                    "depth_qty_a": depth_qty_a,
                    "depth_qty_b": depth_qty_b,
                    "depth_notional_a": depth_notional_a,
                    "depth_notional_b": depth_notional_b,
                    "maker_fee_a": maker_a,
                    "taker_fee_a": taker_a,
                    "maker_fee_b": maker_b,
                    "taker_fee_b": taker_b,
                    "fr_a": fr_a_rate,
                    "fr_b": fr_b_rate,
                    "fr_countdown_ms": countdown_ms,
                    "half_life_s": half_life_s,
                    "t_exit_s": t_exit_s,
                    "advice": advice,
                    "net_funding_cycle_usd": net_funding_cycle_usd,
                    "expect_funding_next_usd": expect_funding_next_usd,
                    "age_a_ms": age_a_ms,
                    "age_b_ms": age_b_ms,
                    "skew_ms": skew_ms,
                    "latency_ms": latency_ms,
                    "stale": stale,
                }
                try:
                    async with aiohttp.ClientSession() as s:
                        async with s.post(panel_ingest_url, json=payload, timeout=5) as r:
                            await r.text()
                except Exception:
                    pass
        except Exception as e:
            print(f"[{now_ms()}] ERROR pair={pair.name}: {e}")

        await asyncio.sleep(poll_ms / 1000)


def estimate_reversion_times(z: RollingZScore, current_z: float, exit_z: float, poll_ms: int) -> tuple[Optional[float], Optional[float]]:
    """Estimate AR(1) half-life (seconds) and time to reach exit_z threshold.

    Uses simple OLS on spread series stored in z.buf to estimate phi.
    """
    try:
        series = list(z.buf)
        n = len(series)
        if n < 10:
            return None, None
        x = series[:-1]
        y = series[1:]
        mean_x = sum(x) / len(x)
        mean_y = sum(y) / len(y)
        num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        den = sum((xi - mean_x) ** 2 for xi in x)
        if den == 0:
            return None, None
        phi = num / den
        # guard against invalid phi
        if phi <= 0 or phi >= 0.9999:
            return None, None
        half_life_samples = math.log(2) / -math.log(phi)
        half_life_s = half_life_samples * (poll_ms / 1000.0)
        if exit_z <= 0 or abs(current_z) <= exit_z:
            t_exit_s = 0.0
        else:
            k = math.log(2) / half_life_s
            t_exit_s = math.log(abs(current_z) / exit_z) / k
        return half_life_s, t_exit_s
    except Exception:
        return None, None


async def main() -> None:
    cfg = load_config()
    # optional: fetch admin rate limits from panel
    limiter = RateLimiter()
    admin_url = os.getenv("PANEL_ADMIN_URL")
    if admin_url:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(admin_url, timeout=5) as r:
                    if r.status == 200:
                        adm = await r.json()
                        if adm and adm.get("ratelimits"):
                            limiter.update(adm["ratelimits"])
        except Exception:
            pass
    conns = build_connectors(cfg, limiter)
    pairs = build_pairs(cfg)
    lookback = int(cfg.get("lookback", 60))
    enter_z = float(cfg.get("enter_z", 2.0))
    exit_z = float(cfg.get("exit_z", 0.5))
    poll_ms = int(cfg.get("poll_ms", 1000))

    # Optional: auto-discover Lighter market_id via /api/v1/orderBooks when missing
    # to avoid hardcoding market ids in config.
    try:
        lighter_map = await conns["lighter"].fetch_market_map()  # type: ignore[attr-defined]
    except Exception:
        lighter_map = {}

    for p in pairs:
        if p.a.exchange == "lighter" and p.a.market_id is None:
            if p.a.symbol in lighter_map:
                p.a.market_id = lighter_map[p.a.symbol]
        if p.b.exchange == "lighter" and p.b.market_id is None:
            if p.b.symbol in lighter_map:
                p.b.market_id = lighter_map[p.b.symbol]

    # optional sqlite history
    db_path = os.getenv("ARB_DB_PATH", os.path.join("data", "arb.db"))
    db = await open_db(db_path)

    tasks = []
    for p in pairs:
        z = RollingZScore(window=lookback)
        tasks.append(asyncio.create_task(poll_pair_with_store(p, conns, z, enter_z, exit_z, poll_ms, db)))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
