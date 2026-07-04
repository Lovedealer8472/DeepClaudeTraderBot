"""
Canonical Technical Analysis module — single source of truth for all indicators.

Uses Wilder's smoothing throughout (RSI, ATR, ADX).
Replaces the split-brain: indicators.py (Wilder) vs advanced_features.py (SMA).
Every scoring module imports from here. No local _rsi, _atr, _adx copies.

Reference: Wilder, J.W. (1978). New Concepts in Technical Trading Systems.
"""

from typing import Optional, List, Tuple
import math


def rsi_wilder(prices: List[float], period: int = 14) -> Optional[float]:
    """Wilder-smoothed RSI. Seed with SMA, then avg = (prev*(N-1) + new)/N."""
    if len(prices) < period + 1:
        return None
    if not all(isinstance(p, (int, float)) and math.isfinite(p) for p in prices):
        return None

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas[:period]]
    losses = [-d if d < 0 else 0.0 for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for i in range(period, len(deltas)):
        g = deltas[i] if deltas[i] > 0 else 0.0
        l = -deltas[i] if deltas[i] < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period

    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr_wilder(highs: List[float], lows: List[float], closes: List[float],
               period: int = 14) -> Optional[float]:
    """Wilder-smoothed ATR."""
    n = len(closes)
    if n < period + 1:
        return None

    tr_values = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_values.append(tr)

    if len(tr_values) < period:
        return None

    atr = sum(tr_values[:period]) / period
    for i in range(period, len(tr_values)):
        atr = (atr * (period - 1) + tr_values[i]) / period
    return atr


def adx_wilder(highs: List[float], lows: List[float], closes: List[float],
               period: int = 14) -> Optional[float]:
    """Wilder-smoothed ADX. O(n) incremental algorithm."""
    n = len(closes)
    if n < period * 2:
        return None

    tr = []
    plus_dm = []
    minus_dm = []

    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus = up if up > down and up > 0 else 0.0
        minus = down if down > up and down > 0 else 0.0
        true_range = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr.append(true_range)
        plus_dm.append(plus)
        minus_dm.append(minus)

    atr_sum = sum(tr[:period])
    plus_sum = sum(plus_dm[:period])
    minus_sum = sum(minus_dm[:period])
    dx_values = []

    for i in range(period, len(tr)):
        atr_sum = atr_sum - (atr_sum / period) + tr[i]
        plus_sum = plus_sum - (plus_sum / period) + plus_dm[i]
        minus_sum = minus_sum - (minus_sum / period) + minus_dm[i]

        if atr_sum <= 0:
            dx_values.append(0.0)
            continue
        pdi = 100.0 * plus_sum / atr_sum
        mdi = 100.0 * minus_sum / atr_sum
        di_sum = pdi + mdi
        dx = 0.0 if di_sum == 0 else 100.0 * abs(pdi - mdi) / di_sum
        dx_values.append(dx)

    if len(dx_values) < period:
        return None

    adx = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx = ((adx * (period - 1)) + dx) / period
    return adx


def ema(prices: List[float], period: int) -> Optional[float]:
    """Standard EMA — seed with SMA of oldest `period` values, iterate forward."""
    if len(prices) < period:
        return None
    multiplier = 2.0 / (period + 1.0)
    ema_val = sum(prices[:period]) / period
    for price in prices[period:]:
        ema_val = (price * multiplier) + (ema_val * (1 - multiplier))
    return ema_val


def ema_series(prices: List[float], period: int) -> List[float]:
    """Return EMA series (same length as input, first period-1 values are NaN)."""
    if len(prices) < period:
        return [float('nan')] * len(prices)
    multiplier = 2.0 / (period + 1.0)
    result = [float('nan')] * (period - 1)
    seed = sum(prices[:period]) / period
    result.append(seed)
    for price in prices[period:]:
        seed = (price * multiplier) + (seed * (1 - multiplier))
        result.append(seed)
    return result


def bollinger_bands(prices: List[float], period: int = 20, num_std: float = 2.0
                    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Bollinger Bands — returns (middle, upper, lower). ddof=1 for sample std."""
    if len(prices) < period:
        return None, None, None
    sma = sum(prices[-period:]) / period
    variance = sum((p - sma) ** 2 for p in prices[-period:]) / (period - 1)  # ddof=1
    std = math.sqrt(variance)
    return sma, sma + num_std * std, sma - num_std * std


def find_pivots(highs: List[float], lows: List[float],
                left: int = 3, right: int = 3) -> Tuple[List[int], List[int]]:
    """Find swing highs and swing lows using pivot detection.
    Returns (high_pivot_indices, low_pivot_indices)."""
    high_pivots = []
    low_pivots = []
    n = len(highs)
    for i in range(left, n - right):
        if all(highs[i] >= highs[i - j] for j in range(1, left + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, right + 1)):
            high_pivots.append(i)
        if all(lows[i] <= lows[i - j] for j in range(1, left + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, right + 1)):
            low_pivots.append(i)
    return high_pivots, low_pivots
