"""
In-memory OHLCV bar cache for per-candle volume confirmation.

Replaces the 24h volume > $200M proxy with actual SFP candle volume checks.
Uses deque(maxlen=N) keyed by (symbol, timeframe). Memory: ~19 MB for
500 symbols × 3 timeframes × 200 bars.

Thread-safe for asyncio (no locks needed — single-threaded cooperative).
"""

from collections import deque
from typing import Optional, Dict, Tuple, List
from dataclasses import dataclass
import time


@dataclass
class Candle:
    ts: int           # Open timestamp (ms)
    open: float
    high: float
    low: float
    close: float
    volume: float          # Base asset volume
    quote_volume: float    # Quote asset volume (USDT) — use for volume confirmation
    taker_buy_volume: float  # Taker buy quote volume


class BarCache:
    """In-memory OHLCV bar store for volume confirmation and indicator computation."""

    def __init__(self, max_bars: int = 200):
        self._bars: Dict[Tuple[str, str], deque] = {}  # (symbol, timeframe) → deque of Candle
        self._max_bars = max_bars
        self._last_fetch: Dict[Tuple[str, str], float] = {}  # Last REST fetch timestamp

    def get_bars(self, symbol: str, timeframe: str = "1h") -> List[Candle]:
        """Get cached bars for a symbol+timeframe. Returns empty list if not cached."""
        key = (symbol, timeframe)
        if key not in self._bars:
            return []
        return list(self._bars[key])

    def get_volume_ratio(self, symbol: str, timeframe: str = "1h",
                         lookback: int = 20) -> float:
        """
        Compute volume ratio: latest candle quote_volume / average of last N candles.
        Returns 0.0 if insufficient data. Used for SFP volume confirmation.
        """
        bars = self.get_bars(symbol, timeframe)
        if len(bars) < lookback + 1:
            return 0.0
        latest = bars[-1].quote_volume
        if latest <= 0:
            return 0.0
        avg = sum(b.quote_volume for b in bars[-(lookback + 1):-1]) / lookback
        if avg <= 0:
            return 0.0
        return latest / avg

    def update_bars(self, symbol: str, timeframe: str, candles: List[Candle]):
        """Replace cached bars for a symbol+timeframe. Deduplicates by timestamp."""
        key = (symbol, timeframe)
        existing = self._bars.get(key)
        if existing is None:
            self._bars[key] = deque(maxlen=self._max_bars)

        for c in candles:
            # Dedup: skip if timestamp already exists
            if not any(e.ts == c.ts for e in self._bars[key]):
                self._bars[key].append(c)

        self._last_fetch[key] = time.time()

    def needs_refresh(self, symbol: str, timeframe: str = "1h",
                      max_age_seconds: float = 3600.0) -> bool:
        """Check if bars for this symbol+timeframe need a REST refresh."""
        key = (symbol, timeframe)
        if key not in self._last_fetch:
            return True
        return (time.time() - self._last_fetch[key]) > max_age_seconds

    def clear_symbol(self, symbol: str):
        """Remove all cached bars for a symbol."""
        for tf in ("1m", "5m", "15m", "1h", "4h", "1d"):
            key = (symbol, tf)
            self._bars.pop(key, None)
            self._last_fetch.pop(key, None)


# Singleton
_bar_cache: Optional[BarCache] = None


def get_bar_cache(max_bars: int = 200) -> BarCache:
    global _bar_cache
    if _bar_cache is None:
        _bar_cache = BarCache(max_bars=max_bars)
    return _bar_cache
