from __future__ import annotations

import os
from typing import Any, Dict


def load_config(path: str | None = None) -> Dict[str, Any]:
    """Load YAML config if present; otherwise return a minimal default config.

    We avoid strict YAML dependency for MVP; return defaults when file missing.
    """
    # defer YAML to later to keep MVP light; look for env vars instead
    cfg: Dict[str, Any] = {
        "lighter_host": os.getenv("LIGHTER_HOST", "https://mainnet.zklighter.elliot.ai"),
        "aster_host": os.getenv("ASTER_HOST", "https://fapi.asterdex.com"),
        "depth_levels": int(os.getenv("DEPTH_LEVELS", "5")),
        # Pairs to monitor:
        # NOTE: for Lighter, use the exact `symbol` string from /api/v1/orderBooks
        # (e.g., "BTC", "ETH", or as listed), not necessarily "BTCUSDT".
        "pairs": [
            {
                "name": "BTCUSDT",
                "a": {"exchange": "lighter", "symbol": "BTC", "market_id": None},
                "b": {"exchange": "aster", "symbol": "BTCUSDT"},
            }
        ],
        "lookback": 60,  # samples window
        "ema_window": 30,
        "enter_z": 2.0,
        "exit_z": 0.5,
        "poll_ms": 1000,
        # data freshness thresholds (ms)
        "stale_ms_threshold": 3000,
        "skew_ms_threshold": 500,
        # Optional explicit fees per exchange (fallback when API not available)
        "fees": {
            "aster": {"maker": None, "taker": None},
            "lighter": {"maker": None, "taker": None},
        },
        "funding": {
            # fallback cycle hours if next funding time isn't provided by exchange
            "cycle_hours": {"aster": 8, "lighter": 8},
            # notional used for funding PnL hints (USD)
            "notional_usd": 1000.0,
        },
        # auth secrets (kept in environment/.env; not used until auto-trading mode)
        "auth": {
            "lighter": {
                "private_key": os.getenv("LIGHTER_API_KEY_PRIVATE_KEY"),
                "account_index": _to_int(os.getenv("LIGHTER_ACCOUNT_INDEX")),
                "api_key_index": _to_int(os.getenv("LIGHTER_API_KEY_INDEX")),
            },
            "aster": {
                # futures ECDSA
                "user": os.getenv("ASTER_USER"),
                "signer": os.getenv("ASTER_SIGNER"),
                "ecdsa_private_key": os.getenv("ASTER_ECDSA_PRIVATE_KEY"),
                # spot HMAC (optional)
                "api_key": os.getenv("ASTER_API_KEY"),
                "api_secret": os.getenv("ASTER_API_SECRET"),
            },
            "eth": {
                "private_key": os.getenv("ETH_PRIVATE_KEY"),
            },
        },
    }
    return cfg


def _to_int(val: str | None) -> int | None:
    try:
        return int(val) if val is not None and val != "" else None
    except Exception:
        return None
