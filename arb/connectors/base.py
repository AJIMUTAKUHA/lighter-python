from __future__ import annotations

import abc
from typing import Optional, Dict, Any
import aiohttp


class Connector(abc.ABC):
    """Exchange connector abstract base.

    Minimal surface for reminder-mode MVP: mid-price retrieval.
    Trading methods are defined but optional (implemented later for auto mode).
    """

    name: str

    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None, limiter=None) -> None:
        self.name = name
        self.config = config or {}
        self.limiter = limiter
        self._session: Optional[aiohttp.ClientSession] = None

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    @abc.abstractmethod
    async def get_mid_price(self, symbol: str, **kwargs) -> float:
        """Return mid price for a symbol (or the best proxy).

        Implementations may derive mid from order book or use last/mark price
        if order book is not available or too heavy for MVP.
        """
        raise NotImplementedError

    # --- Trading (to be implemented in auto mode) ---
    async def create_order(self, **kwargs) -> Any:  # pragma: no cover - placeholder
        raise NotImplementedError

    async def cancel_order(self, **kwargs) -> Any:  # pragma: no cover - placeholder
        raise NotImplementedError
