from __future__ import annotations

import os
from typing import Any, Dict
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import aiosqlite

from ..storage.sqlite import open_db, get_spreads, get_pairs, get_latest_all, admin_get_config, admin_set_config
from ..config import load_config
from ..connectors.lighter import LighterConnector
from ..connectors.aster import AsterConnector
from ..rate_limiter import RateLimiter
import time


app = FastAPI(title="Arb Panel API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = os.getenv("ARB_DB_PATH", os.path.join("data", "arb.db"))


@app.on_event("startup")
async def on_startup():
    app.state.db = await open_db(DB_PATH)
    cfg = load_config()
    app.state.cfg = cfg
    # init connectors for on-demand depth endpoints
    # load admin limiter config from db (or defaults)
    adm = await admin_get_config(app.state.db)
    limiter = RateLimiter(adm.get("ratelimits") if adm else None)
    app.state.ratelimiter = limiter
    app.state.conns = {
        "lighter": LighterConnector(host=cfg["lighter_host"], limiter=limiter),
        "aster": AsterConnector(host=cfg["aster_host"], limiter=limiter),
    }
    # prefetch lighter market map for quick lookup
    try:
        app.state.lighter_map = await app.state.conns["lighter"].fetch_market_map()
    except Exception:
        app.state.lighter_map = {}


@app.on_event("shutdown")
async def on_shutdown():
    db: aiosqlite.Connection = app.state.db
    await db.close()
    # close connector sessions
    try:
        await app.state.conns["lighter"].close()
        await app.state.conns["aster"].close()
    except Exception:
        pass


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/spreads")
async def api_spreads(pair: str, limit: int = 500):
    if not pair:
        raise HTTPException(status_code=400, detail="pair is required")
    db: aiosqlite.Connection = app.state.db
    rows = await get_spreads(db, pair=pair, limit=min(max(limit, 1), 5000))
    # return oldest -> newest for charts
    return list(reversed(rows))


@app.get("/api/pairs")
async def api_pairs():
    db: aiosqlite.Connection = app.state.db
    rows = await get_pairs(db)
    # fallback to config pairs if db empty
    if not rows:
        cfg = app.state.cfg
        rows = [p.get("name") for p in cfg.get("pairs", [])]
    return rows


@app.get("/api/latest")
async def api_latest():
    db: aiosqlite.Connection = app.state.db
    return await get_latest_all(db)


def _parse_edges(edges: str):
    try:
        parts = [float(x) for x in edges.split(',') if x]
        parts.sort()
        out = []
        for i in range(len(parts)):
            lo = parts[i]
            hi = parts[i+1] if i+1 < len(parts) else None
            out.append((lo, hi))
        return out
    except Exception:
        return [(1.5, 2.0), (2.0, 2.5), (2.5, 3.0), (3.0, None)]


@app.get("/api/stats/bins")
async def api_stats_bins(pair: str, days: int = 7, exit_z: float = 0.5, edges: str = "1.5,2,2.5,3"):
    if not pair:
        raise HTTPException(status_code=400, detail="pair is required")
    db: aiosqlite.Connection = app.state.db
    # pull up to 10000 points and filter by time window
    rows = await get_spreads(db, pair=pair, limit=10000)
    since_ms = int(time.time() * 1000) - days * 86400000
    seq = [r for r in rows if r["ts_ms"] >= since_ms]
    # ensure ascending by time
    seq.sort(key=lambda r: r["ts_ms"])
    bins = _parse_edges(edges)
    stats = []
    # build time series of |z| and fr_countdown
    absz = [abs(r.get("z") or 0.0) for r in seq]
    tms = [r["ts_ms"] for r in seq]
    countdown = [r.get("fr_countdown_ms") for r in seq]
    for (lo, hi) in bins:
        samples = []
        prob_before_funding = []
        i = 1
        n = len(seq)
        while i < n:
            prev = absz[i-1]
            cur = absz[i]
            enter = (prev < lo) and (cur >= lo) and (hi is None or cur < hi)
            if enter:
                start_t = tms[i]
                # find first j>=i where |z| <= exit_z
                j = i
                reached = False
                while j < n:
                    if absz[j] <= exit_z:
                        reached = True
                        break
                    j += 1
                if reached:
                    dt_ms = tms[j] - start_t
                    samples.append(dt_ms / 1000.0)
                    # funding check: use countdown at i if available
                    c = countdown[i]
                    if c is not None:
                        prob_before_funding.append(1.0 if dt_ms <= c else 0.0)
                # move i forward to avoid overlapping entries too much
                i = j if reached else i + 1
            else:
                i += 1
        samples.sort()
        cnt = len(samples)
        def pct(p):
            if cnt == 0:
                return None
            k = int(max(0, min(cnt-1, round(p*(cnt-1)))))
            return samples[k]
        p25 = pct(0.25)
        median = pct(0.5)
        p75 = pct(0.75)
        p90 = pct(0.90)
        prob = None
        if prob_before_funding:
            prob = sum(prob_before_funding)/len(prob_before_funding)
        stats.append({
            "bin": {"lo": lo, "hi": hi},
            "samples": cnt,
            "p25_s": p25,
            "median_s": median,
            "p75_s": p75,
            "p90_s": p90,
            "prob_exit_before_funding": prob,
        })
    return {"pair": pair, "days": days, "exit_z": exit_z, "stats": stats}


@app.get("/api/depth")
async def api_depth(pair: str, levels: int = 50):
    if not pair:
        raise HTTPException(status_code=400, detail="pair is required")
    cfg = app.state.cfg
    conns = app.state.conns
    pairs = cfg.get("pairs", [])
    pdef = next((p for p in pairs if p.get("name") == pair), None)
    if not pdef:
        raise HTTPException(status_code=404, detail="pair not configured")
    a = pdef["a"]; b = pdef["b"]

    # resolve lighter market_ids if needed
    if a["exchange"] == "lighter" and (a.get("market_id") is None):
        mid = app.state.lighter_map.get(a.get("symbol"))
        if mid is None:
            raise HTTPException(status_code=400, detail="lighter market_id not resolved for A")
        a["market_id"] = mid
    if b["exchange"] == "lighter" and (b.get("market_id") is None):
        mid = app.state.lighter_map.get(b.get("symbol"))
        if mid is None:
            raise HTTPException(status_code=400, detail="lighter market_id not resolved for B")
        b["market_id"] = mid

    out = {"a": {}, "b": {}}
    if a["exchange"] == "lighter":
        out["a"] = await conns["lighter"].get_order_book_levels(market_id=a["market_id"], levels=levels)
    else:
        out["a"] = await conns["aster"].get_order_book_levels(symbol=a["symbol"], levels=levels)

    if b["exchange"] == "lighter":
        out["b"] = await conns["lighter"].get_order_book_levels(market_id=b["market_id"], levels=levels)
    else:
        out["b"] = await conns["aster"].get_order_book_levels(symbol=b["symbol"], levels=levels)

    return out


# --- WebSocket broadcasting ---
class WSManager:
    def __init__(self) -> None:
        self.active: dict[str, set[WebSocket]] = {}

    async def connect(self, pair: str, ws: WebSocket):
        await ws.accept()
        self.active.setdefault(pair, set()).add(ws)

    def disconnect(self, pair: str, ws: WebSocket):
        try:
            self.active.get(pair, set()).discard(ws)
        except Exception:
            pass

    async def broadcast(self, pair: str, message: dict):
        conns = list(self.active.get(pair, set()))
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                # drop broken connection
                self.disconnect(pair, ws)


manager = WSManager()


@app.websocket("/ws/stream")
async def ws_stream(ws: WebSocket, pair: str):
    await manager.connect(pair, ws)
    try:
        while True:
            # keep alive, clients may send pings
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(pair, ws)


# --- Ingestion endpoint from Runner ---
from ..storage.sqlite import insert_spread


@app.post("/api/ingest/spread")
async def ingest_spread(payload: dict):
    required = ["pair", "ts_ms", "price_a", "price_b", "spread", "z", "mean", "std"]
    for k in required:
        if k not in payload:
            raise HTTPException(status_code=400, detail=f"missing field {k}")
    pair = payload["pair"]
    db: aiosqlite.Connection = app.state.db
    await insert_spread(
        db,
        pair=pair,
        ts_ms=int(payload["ts_ms"]),
        price_a=float(payload["price_a"]),
        price_b=float(payload["price_b"]),
        spread=float(payload["spread"]),
        z=float(payload["z"]),
        mean=float(payload["mean"]),
        std=float(payload["std"]),
        ema=payload.get("ema"),
        center_dev=payload.get("center_dev"),
        ob_spread_a=payload.get("ob_spread_a"),
        ob_spread_b=payload.get("ob_spread_b"),
        ob_spread_pct_a=payload.get("ob_spread_pct_a"),
        ob_spread_pct_b=payload.get("ob_spread_pct_b"),
        vol_a=payload.get("vol_a"),
        vol_b=payload.get("vol_b"),
        depth_qty_a=payload.get("depth_qty_a"),
        depth_qty_b=payload.get("depth_qty_b"),
        depth_notional_a=payload.get("depth_notional_a"),
        depth_notional_b=payload.get("depth_notional_b"),
        maker_fee_a=payload.get("maker_fee_a"),
        taker_fee_a=payload.get("taker_fee_a"),
        maker_fee_b=payload.get("maker_fee_b"),
        taker_fee_b=payload.get("taker_fee_b"),
        fr_a=payload.get("fr_a"),
        fr_b=payload.get("fr_b"),
        fr_countdown_ms=payload.get("fr_countdown_ms"),
        half_life_s=payload.get("half_life_s"),
        t_exit_s=payload.get("t_exit_s"),
        advice=payload.get("advice"),
        net_funding_cycle_usd=payload.get("net_funding_cycle_usd"),
        expect_funding_next_usd=payload.get("expect_funding_next_usd"),
        age_a_ms=payload.get("age_a_ms"),
        age_b_ms=payload.get("age_b_ms"),
        skew_ms=payload.get("skew_ms"),
        latency_ms=payload.get("latency_ms"),
        stale=payload.get("stale"),
    )
    # broadcast to subscribers
    await manager.broadcast(pair, payload)
    return {"status": "ok"}


def _avg_exec_price(levels: list[list[float]], base_qty: float, side: str) -> tuple[float, float]:
    """Walk the book and compute average execution price and filled qty.

    side: 'buy' consumes asks ascending; 'sell' consumes bids descending (as provided).
    levels: [[price, qty], ...]
    """
    remaining = base_qty
    total_quote = 0.0
    filled = 0.0
    seq = levels if side == 'buy' else levels  # assume order provided is suitable
    for price, qty in seq:
        if remaining <= 0:
            break
        take = min(remaining, qty)
        total_quote += take * price
        remaining -= take
        filled += take
    avg = total_quote / filled if filled > 0 else 0.0
    return avg, filled


@app.get("/api/simulate")
async def api_simulate(pair: str, notional_usd: float = 1000.0, pattern: str = "enter_short_A_long_B"):
    cfg = app.state.cfg
    conns = app.state.conns
    pairs = cfg.get("pairs", [])
    pdef = next((p for p in pairs if p.get("name") == pair), None)
    if not pdef:
        raise HTTPException(status_code=404, detail="pair not configured")
    a = pdef["a"]; b = pdef["b"]
    # resolve lighter market_ids if needed
    if a["exchange"] == "lighter" and (a.get("market_id") is None):
        a["market_id"] = app.state.lighter_map.get(a.get("symbol"))
    if b["exchange"] == "lighter" and (b.get("market_id") is None):
        b["market_id"] = app.state.lighter_map.get(b.get("symbol"))

    # get mid prices
    mid_a = await conns[a["exchange"]].get_mid_price(symbol=a.get("symbol"), market_id=a.get("market_id"))
    mid_b = await conns[b["exchange"]].get_mid_price(symbol=b.get("symbol"), market_id=b.get("market_id"))
    base_qty_a = notional_usd / mid_a
    base_qty_b = notional_usd / mid_b

    # order book levels
    if a["exchange"] == "lighter":
        levels_a = await conns["lighter"].get_order_book_levels(market_id=a["market_id"], levels=50)
    else:
        levels_a = await conns["aster"].get_order_book_levels(symbol=a["symbol"], levels=50)
    if b["exchange"] == "lighter":
        levels_b = await conns["lighter"].get_order_book_levels(market_id=b["market_id"], levels=50)
    else:
        levels_b = await conns["aster"].get_order_book_levels(symbol=b["symbol"], levels=50)

    # pattern mapping
    if pattern == "enter_short_A_long_B":
        side_a, side_b = "sell", "buy"
    elif pattern == "enter_long_A_short_B":
        side_a, side_b = "buy", "sell"
    else:
        raise HTTPException(status_code=400, detail="invalid pattern")

    avg_a, filled_a = _avg_exec_price(levels_a["asks"] if side_a=="buy" else levels_a["bids"], base_qty_a, side_a)
    avg_b, filled_b = _avg_exec_price(levels_b["asks"] if side_b=="buy" else levels_b["bids"], base_qty_b, side_b)

    # costs
    slip_a_pct = (abs(avg_a - mid_a) / mid_a) if avg_a > 0 else 0.0
    slip_b_pct = (abs(avg_b - mid_b) / mid_b) if avg_b > 0 else 0.0
    slip_a_usd = slip_a_pct * notional_usd
    slip_b_usd = slip_b_pct * notional_usd

    # taker fees
    fees_cfg = cfg.get("fees", {}) or {}
    if a["exchange"] == "lighter":
        fa = await conns["lighter"].get_fees(a["symbol"])  # type: ignore
        taker_a = fa.get("taker") or 0.0
    else:
        taker_a = float((fees_cfg.get("aster", {}) or {}).get("taker") or 0.0)
    if b["exchange"] == "lighter":
        fb = await conns["lighter"].get_fees(b["symbol"])  # type: ignore
        taker_b = fb.get("taker") or 0.0
    else:
        taker_b = float((fees_cfg.get("aster", {}) or {}).get("taker") or 0.0)

    fee_a_usd = taker_a * notional_usd
    fee_b_usd = taker_b * notional_usd

    return {
        "mid_a": mid_a,
        "mid_b": mid_b,
        "avg_a": avg_a,
        "avg_b": avg_b,
        "slip_a_pct": slip_a_pct,
        "slip_b_pct": slip_b_pct,
        "slip_a_usd": slip_a_usd,
        "slip_b_usd": slip_b_usd,
        "fee_a_usd": fee_a_usd,
        "fee_b_usd": fee_b_usd,
        "total_cost_usd": slip_a_usd + slip_b_usd + fee_a_usd + fee_b_usd,
        "filled_base_a": filled_a,
        "filled_base_b": filled_b,
    }


# Serve static front-end
app.mount("/", StaticFiles(directory="web", html=True), name="web")


# --- Admin endpoints for rate limits ---
@app.get("/api/admin/config")
async def api_admin_get():
    db: aiosqlite.Connection = app.state.db
    cfg = await admin_get_config(db)
    return cfg or {"ratelimits": {"aster": {"global": {"capacity": 20, "refill": 10.0}}, "lighter": {"global": {"capacity": 20, "refill": 10.0}}}}


@app.post("/api/admin/config")
async def api_admin_set(payload: dict):
    db: aiosqlite.Connection = app.state.db
    if "ratelimits" not in payload:
        raise HTTPException(status_code=400, detail="missing ratelimits")
    await admin_set_config(db, payload)
    # update runtime limiter
    app.state.ratelimiter.update(payload.get("ratelimits"))
    return {"status": "ok"}
