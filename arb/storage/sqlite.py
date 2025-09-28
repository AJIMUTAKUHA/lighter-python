from __future__ import annotations

import os
from typing import Optional, List, Tuple, Any, Dict
import aiosqlite


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS spreads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    price_a REAL NOT NULL,
    price_b REAL NOT NULL,
    spread REAL NOT NULL,
    z REAL NOT NULL,
    mean REAL NOT NULL,
    std REAL NOT NULL,
    ema REAL,
    center_dev REAL,
    ob_spread_a REAL,
    ob_spread_b REAL,
    ob_spread_pct_a REAL,
    ob_spread_pct_b REAL,
    vol_a REAL,
    vol_b REAL,
    depth_qty_a REAL,
    depth_qty_b REAL,
    depth_notional_a REAL,
    depth_notional_b REAL,
    maker_fee_a REAL,
    taker_fee_a REAL,
    maker_fee_b REAL,
    taker_fee_b REAL,
    fr_a REAL,
    fr_b REAL,
    fr_countdown_ms REAL,
    half_life_s REAL,
    t_exit_s REAL,
    advice TEXT
);
CREATE INDEX IF NOT EXISTS idx_spreads_pair_ts ON spreads(pair, ts_ms);
"""


async def open_db(path: str) -> aiosqlite.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = await aiosqlite.connect(path)
    await db.executescript(CREATE_TABLE_SQL)
    # admin config table
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS admin_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            json TEXT NOT NULL
        );
        """
    )
    await db.commit()
    return db


EXPECTED_COLUMNS = {
    "pair",
    "ts_ms",
    "price_a",
    "price_b",
    "spread",
    "z",
    "mean",
    "std",
    "ema",
    "center_dev",
    "ob_spread_a",
    "ob_spread_b",
    "ob_spread_pct_a",
    "ob_spread_pct_b",
    "vol_a",
    "vol_b",
    "depth_qty_a",
    "depth_qty_b",
    "depth_notional_a",
    "depth_notional_b",
    "maker_fee_a",
    "taker_fee_a",
    "maker_fee_b",
    "taker_fee_b",
    "fr_a",
    "fr_b",
    "fr_countdown_ms",
    "half_life_s",
    "t_exit_s",
    "advice",
    "net_funding_cycle_usd",
    "expect_funding_next_usd",
    "age_a_ms",
    "age_b_ms",
    "skew_ms",
    "latency_ms",
    "stale",
}


async def ensure_schema(db: aiosqlite.Connection) -> None:
    # ensure all columns exist (id excluded)
    cols = []
    async with db.execute("PRAGMA table_info(spreads)") as cursor:
        rows = await cursor.fetchall()
        cols = [r[1] for r in rows]
    missing = [c for c in EXPECTED_COLUMNS if c not in cols]
    for c in missing:
        # advice is TEXT; others REAL
        if c == "advice":
            await db.execute(f"ALTER TABLE spreads ADD COLUMN {c} TEXT")
        else:
            await db.execute(f"ALTER TABLE spreads ADD COLUMN {c} REAL")
    if missing:
        await db.commit()


async def insert_spread(
    db: aiosqlite.Connection,
    pair: str,
    ts_ms: int,
    price_a: float,
    price_b: float,
    spread: float,
    z: float,
    mean: float,
    std: float,
    **extras: float,
) -> None:
    await ensure_schema(db)
    columns = [
        "pair",
        "ts_ms",
        "price_a",
        "price_b",
        "spread",
        "z",
        "mean",
        "std",
        "ema",
        "center_dev",
        "ob_spread_a",
        "ob_spread_b",
        "ob_spread_pct_a",
        "ob_spread_pct_b",
        "vol_a",
        "vol_b",
        "depth_qty_a",
        "depth_qty_b",
        "depth_notional_a",
        "depth_notional_b",
        "maker_fee_a",
        "taker_fee_a",
        "maker_fee_b",
        "taker_fee_b",
        "fr_a",
        "fr_b",
        "fr_countdown_ms",
        "half_life_s",
        "t_exit_s",
        "advice",
        "net_funding_cycle_usd",
        "expect_funding_next_usd",
        "age_a_ms",
        "age_b_ms",
        "skew_ms",
        "latency_ms",
        "stale",
    ]
    values = [
        pair,
        ts_ms,
        price_a,
        price_b,
        spread,
        z,
        mean,
        std,
        extras.get("ema"),
        extras.get("center_dev"),
        extras.get("ob_spread_a"),
        extras.get("ob_spread_b"),
        extras.get("ob_spread_pct_a"),
        extras.get("ob_spread_pct_b"),
        extras.get("vol_a"),
        extras.get("vol_b"),
        extras.get("depth_qty_a"),
        extras.get("depth_qty_b"),
        extras.get("depth_notional_a"),
        extras.get("depth_notional_b"),
        extras.get("maker_fee_a"),
        extras.get("taker_fee_a"),
        extras.get("maker_fee_b"),
        extras.get("taker_fee_b"),
        extras.get("fr_a"),
        extras.get("fr_b"),
        extras.get("fr_countdown_ms"),
        extras.get("half_life_s"),
        extras.get("t_exit_s"),
        extras.get("advice"),
        extras.get("net_funding_cycle_usd"),
        extras.get("expect_funding_next_usd"),
        extras.get("age_a_ms"),
        extras.get("age_b_ms"),
        extras.get("skew_ms"),
        extras.get("latency_ms"),
        extras.get("stale"),
    ]
    placeholders = ", ".join(["?"] * len(values))
    cols_sql = ", ".join(columns)
    sql = f"INSERT INTO spreads({cols_sql}) VALUES ({placeholders})"
    await db.execute(sql, tuple(values))
    await db.commit()


async def get_spreads(
    db: aiosqlite.Connection, pair: str, limit: int = 1000
) -> List[Dict[str, Any]]:
    cursor = await db.execute(
        "SELECT ts_ms, price_a, price_b, spread, z, mean, std, ema, center_dev, ob_spread_a, ob_spread_b, ob_spread_pct_a, ob_spread_pct_b, vol_a, vol_b, depth_qty_a, depth_qty_b, depth_notional_a, depth_notional_b, maker_fee_a, taker_fee_a, maker_fee_b, taker_fee_b, fr_a, fr_b, fr_countdown_ms, half_life_s, t_exit_s, advice, net_funding_cycle_usd, expect_funding_next_usd, age_a_ms, age_b_ms, skew_ms, latency_ms, stale FROM spreads WHERE pair = ? ORDER BY ts_ms DESC LIMIT ?",
        (pair, limit),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    # newest first
    return [
        {
            "ts_ms": r[0],
            "price_a": r[1],
            "price_b": r[2],
            "spread": r[3],
            "z": r[4],
            "mean": r[5],
            "std": r[6],
            "ema": r[7],
            "center_dev": r[8],
            "ob_spread_a": r[9],
            "ob_spread_b": r[10],
            "ob_spread_pct_a": r[11],
            "ob_spread_pct_b": r[12],
            "vol_a": r[13],
            "vol_b": r[14],
            "depth_qty_a": r[15],
            "depth_qty_b": r[16],
            "depth_notional_a": r[17],
            "depth_notional_b": r[18],
            "maker_fee_a": r[19],
            "taker_fee_a": r[20],
            "maker_fee_b": r[21],
            "taker_fee_b": r[22],
            "fr_a": r[23],
            "fr_b": r[24],
            "fr_countdown_ms": r[25],
            "half_life_s": r[26],
            "t_exit_s": r[27],
            "advice": r[28],
            "net_funding_cycle_usd": r[29],
            "expect_funding_next_usd": r[30],
            "age_a_ms": r[31],
            "age_b_ms": r[32],
            "skew_ms": r[33],
            "latency_ms": r[34],
            "stale": r[35],
        }
        for r in rows
    ]


# --- Admin config helpers ---
import json as _json


async def admin_get_config(db: aiosqlite.Connection):
    async with db.execute("SELECT json FROM admin_config WHERE id = 1") as cur:
        row = await cur.fetchone()
        if not row:
            return None
        try:
            return _json.loads(row[0])
        except Exception:
            return None


async def admin_set_config(db: aiosqlite.Connection, cfg: dict) -> None:
    s = _json.dumps(cfg)
    await db.execute("INSERT INTO admin_config (id, json) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET json=excluded.json", (s,))
    await db.commit()


async def get_pairs(db: aiosqlite.Connection) -> List[str]:
    cursor = await db.execute("SELECT DISTINCT pair FROM spreads ORDER BY pair")
    rows = await cursor.fetchall()
    await cursor.close()
    return [r[0] for r in rows]


async def get_latest_all(db: aiosqlite.Connection) -> List[Dict[str, Any]]:
    sql = (
        "SELECT t.pair, t.ts_ms, t.price_a, t.price_b, t.spread, t.z, t.mean, t.std, t.ema, t.center_dev, "
        "t.ob_spread_a, t.ob_spread_b, t.ob_spread_pct_a, t.ob_spread_pct_b, t.vol_a, t.vol_b, t.depth_qty_a, t.depth_qty_b, "
        "t.depth_notional_a, t.depth_notional_b, t.maker_fee_a, t.taker_fee_a, t.maker_fee_b, t.taker_fee_b, t.fr_a, t.fr_b, t.fr_countdown_ms, t.half_life_s, t.t_exit_s, t.advice, t.net_funding_cycle_usd, t.expect_funding_next_usd, t.age_a_ms, t.age_b_ms, t.skew_ms, t.latency_ms, t.stale "
        "FROM spreads t JOIN (SELECT pair, MAX(ts_ms) ts FROM spreads GROUP BY pair) m ON t.pair = m.pair AND t.ts_ms = m.ts"
    )
    cursor = await db.execute(sql)
    rows = await cursor.fetchall()
    await cursor.close()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "pair": r[0],
            "ts_ms": r[1],
            "price_a": r[2],
            "price_b": r[3],
            "spread": r[4],
            "z": r[5],
            "mean": r[6],
            "std": r[7],
            "ema": r[8],
            "center_dev": r[9],
            "ob_spread_a": r[10],
            "ob_spread_b": r[11],
            "ob_spread_pct_a": r[12],
            "ob_spread_pct_b": r[13],
            "vol_a": r[14],
            "vol_b": r[15],
            "depth_qty_a": r[16],
            "depth_qty_b": r[17],
            "depth_notional_a": r[18],
            "depth_notional_b": r[19],
            "maker_fee_a": r[20],
            "taker_fee_a": r[21],
            "maker_fee_b": r[22],
            "taker_fee_b": r[23],
            "fr_a": r[24],
            "fr_b": r[25],
            "fr_countdown_ms": r[26],
            "half_life_s": r[27],
            "t_exit_s": r[28],
            "advice": r[29],
            "net_funding_cycle_usd": r[30],
            "expect_funding_next_usd": r[31],
            "age_a_ms": r[32],
            "age_b_ms": r[33],
            "skew_ms": r[34],
            "latency_ms": r[35],
            "stale": r[36],
        })
    return out
