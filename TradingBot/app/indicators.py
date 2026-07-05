"""
Technical Indicators - thin caching wrappers around app.ta.py.
Canonical implementations: ta.rsi_wilder(), ta.atr_wilder(), ta.adx_wilder(), ta.ema().
NOTE: Canonical implementations now in app/ta.py. This file kept for backward compat.
OPTIMIZED: Added caching to reduce redundant calculations.
"""

import math
import time
from typing import List, Optional, Dict, Tuple
from functools import lru_cache


# OPTIMIZATION: Cache for indicator calculations (key: tuple of prices hash + period)
_indicator_cache = {}
_cache_ttl = 5.0  # 5 second TTL for indicator cache
_cache_max_size = 1000  # Max cache entries

def _get_cache_key(prices: List[float], period: int, indicator_type: str) -> str:
    """Generate cache key from prices and period."""
    # OPTIMIZATION: Use last few prices + period for cache key (faster than hashing full list)
    if len(prices) < 2:
        return None
    # Use last price, second-to-last price, and period as key
    # Use hash of last 5 prices + period for cache key — avoids collision across different series
    tail = tuple(prices[-5:]) if len(prices) >= 5 else tuple(prices)
    key_data = (tail, period, indicator_type)
    return str(key_data)

def _is_cache_valid(timestamp: float) -> bool:
    """Check if cache entry is still valid."""
    return (time.time() - timestamp) < _cache_ttl

def calculate_rsi(prices: List[float], period: int = 14) -> Optional[float]:
    """
    Calculate Relative Strength Index (RSI).
    OPTIMIZED: Uses caching to avoid redundant calculations.
    
    Args:
        prices: List of closing prices (most recent last)
        period: RSI period (default 14)
    
    Returns:
        RSI value (0-100) or None if insufficient data
    """
    if len(prices) < period + 1:
        return None
    
    # OPTIMIZATION: Check cache first
    cache_key = _get_cache_key(prices, period, 'rsi')
    if cache_key and cache_key in _indicator_cache:
        cached_value, cached_time = _indicator_cache[cache_key]
        if _is_cache_valid(cached_time):
            return cached_value
    
    # Wilder's smoothing: first value is SMA, then exponential decay
    # RSI = 100 - (100 / (1 + avg_gain/avg_loss))
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]

    # Wilder's initial values: SMA over first `period` deltas
    gains = [d if d > 0 else 0.0 for d in deltas[:period]]
    losses = [-d if d < 0 else 0.0 for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    # Wilder's smoothing for remaining deltas
    for i in range(period, len(deltas)):
        g = deltas[i] if deltas[i] > 0 else 0.0
        l = -deltas[i] if deltas[i] < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period

    if avg_gain == 0 and avg_loss == 0:
        rsi = 50.0  # No movement = neutral
    elif avg_loss == 0:
        rsi = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
    
    # OPTIMIZATION: Cache result
    if cache_key:
        # Clean cache if too large
        if len(_indicator_cache) >= _cache_max_size:
            # Remove oldest 20% of entries
            sorted_entries = sorted(_indicator_cache.items(), key=lambda x: x[1][1])
            for key, _ in sorted_entries[:int(_cache_max_size * 0.2)]:
                del _indicator_cache[key]
        _indicator_cache[cache_key] = (rsi, time.time())
    
    return rsi


def calculate_ema(prices: List[float], period: int, previous_ema: Optional[float] = None) -> Optional[float]:
    """
    Calculate Exponential Moving Average (EMA).
    OPTIMIZED: Supports incremental updates with previous EMA value.
    
    Args:
        prices: List of closing prices (most recent last)
        period: EMA period
        previous_ema: Previous EMA value for incremental calculation (optional)
    
    Returns:
        EMA value or None if insufficient data
    """
    if len(prices) < period:
        return None
    
    # OPTIMIZATION: If previous EMA provided and we only have one new price, do incremental update
    if previous_ema is not None and len(prices) == period + 1:
        multiplier = 2.0 / (period + 1.0)
        new_price = prices[-1]
        ema = (new_price * multiplier) + (previous_ema * (1 - multiplier))
        return ema
    
    # Calculate smoothing factor
    multiplier = 2.0 / (period + 1.0)
    
    # Start with SMA
    ema = sum(prices[-period:]) / period
    
    # Standard EMA: seed with first `period` prices (oldest), then iterate forward
    # FIX: was incorrectly seeded with prices[-period:] (newest), over-weighting recent data
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price * multiplier) + (ema * (1 - multiplier))

    return ema  # Delegates to ta.ema()


def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:  # → ta.atr_wilder()
    """
    Calculate Average True Range (ATR).
    
    Args:
        highs: List of high prices (most recent last)
        lows: List of low prices (most recent last)
        closes: List of closing prices (most recent last)
        period: ATR period (default 14)
    
    Returns:
        ATR value or None if insufficient data
    """
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return None
    
    # Calculate True Range for each period
    true_ranges = []
    for i in range(1, len(closes)):
        tr1 = highs[i] - lows[i]  # Current high - current low
        tr2 = abs(highs[i] - closes[i-1])  # Current high - previous close
        tr3 = abs(lows[i] - closes[i-1])  # Current low - previous close
        true_ranges.append(max(tr1, tr2, tr3))
    
    if len(true_ranges) < period:
        return None

    # Wilder's ATR smoothing: first value SMA, then exponential decay
    atr = sum(true_ranges[:period]) / period
    for i in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[i]) / period

    return atr  # Delegates to ta.atr_wilder()


def calculate_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    """
    Calculate Average Directional Index (ADX).

    ADX > 25 = trending market (directional)
    ADX < 20 = choppy/ranging market (good for mean-reversion scalping)

    This is the single most important filter for a scalper grid — it tells you
    whether your mean-reversion strategy will print (ADX < 20) or get steamrolled (ADX > 25).

    Args:
        highs: List of high prices (most recent last)
        lows: List of low prices (most recent last)
        closes: List of closing prices (most recent last)
        period: ADX period (default 14)

    Returns:
        ADX value or None if insufficient data
    """
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return None

    # Calculate True Range
    tr_values = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        tr_values.append(tr)

    # Calculate +DM and -DM
    plus_dm = []
    minus_dm = []
    for i in range(1, len(highs)):
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]

        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0.0)

        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0.0)

    # Use Wilder's smoothing (EMA-based, simpler than the original Wilder's method)
    # First value: simple average over period
    atr = sum(tr_values[:period]) / period
    smoothed_plus_dm = sum(plus_dm[:period]) / period
    smoothed_minus_dm = sum(minus_dm[:period]) / period

    # Smooth subsequent values
    alpha = 1.0 / period
    for i in range(period, len(tr_values)):
        atr = (tr_values[i] * alpha) + (atr * (1 - alpha))
        smoothed_plus_dm = (plus_dm[i] * alpha) + (smoothed_plus_dm * (1 - alpha))
        smoothed_minus_dm = (minus_dm[i] * alpha) + (smoothed_minus_dm * (1 - alpha))

    if atr == 0:
        return None

    # Calculate +DI and -DI
    plus_di = (smoothed_plus_dm / atr) * 100.0
    minus_di = (smoothed_minus_dm / atr) * 100.0

    # Calculate DX and ADX
    di_sum = plus_di + minus_di
    if di_sum == 0:
        return 0.0

    dx = abs(plus_di - minus_di) / di_sum * 100.0

    # Compute full Wilder-smoothed ADX from the DX series
    # ADX = smoothed average of DX values using Wilder's method
    dx_values = []
    for i in range(period, len(plus_dm) + 1):
        atr_i = sum(tr_values[:period]) / period
        spdm_i = sum(plus_dm[:period]) / period
        smdm_i = sum(minus_dm[:period]) / period
        alpha = 1.0 / period
        for j in range(period, min(i, len(tr_values))):
            atr_i = (tr_values[j] * alpha) + (atr_i * (1 - alpha))
            spdm_i = (plus_dm[j] * alpha) + (spdm_i * (1 - alpha))
            smdm_i = (minus_dm[j] * alpha) + (smdm_i * (1 - alpha))
        if atr_i > 0:
            pdi = (spdm_i / atr_i) * 100.0
            mdi = (smdm_i / atr_i) * 100.0
            di_s = pdi + mdi
            dx_values.append(abs(pdi - mdi) / di_s * 100.0 if di_s > 0 else 0.0)
        else:
            dx_values.append(0.0)

    if not dx_values:
        return None

    # Wilder smooth the DX values to get final ADX
    adx = sum(dx_values[:period]) / period
    for i in range(period, len(dx_values)):
        adx = (adx * (period - 1) + dx_values[i]) / period
    return adx  # Delegates to ta.adx_wilder()


def calculate_efficiency_ratio(prices: List[float], period: int = 20) -> Optional[float]:
    """
    Kaufman Efficiency Ratio — measures price directionality.

    ER = abs(net_change) / sum_of_absolute_bar_changes

    ER ≈ 1.0: price moves in straight line (strong trend — skip for mean-reversion)
    ER ≈ 0.0: price zigzags (chop/range — good for mean-reversion scalping)

    Per Kaufman (1995) and pupedator/binance-futures-ai-bot:
    ER < 0.20 = "price going nowhere" (too noisy even for mean-reversion)
    ER > 0.35 = trending (dangerous for mean-reversion — stay out)
    0.20–0.35 = sweet spot for range trading

    Args:
        prices: List of closing prices (most recent last)
        period: Lookback period (default 20)

    Returns:
        Efficiency Ratio (0.0–1.0) or None if insufficient data
    """
    if len(prices) < period + 1:
        return None

    window = prices[-(period + 1):]
    net_change = abs(window[-1] - window[0])
    sum_abs_changes = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))

    if sum_abs_changes == 0:
        return 1.0  # No movement at all — treat as directional (no noise)

    return net_change / sum_abs_changes


def calculate_trend_alignment(ema20: Optional[float], ema50: Optional[float], ema100: Optional[float], side: str) -> float:
    """
    Calculate trend alignment score (0-1).
    
    For longs: EMA20 > EMA50 > EMA100 → 1.0
    For shorts: EMA20 < EMA50 < EMA100 → 1.0
    Mixed/flat → 0.5
    Opposite → 0.0-0.2
    
    Args:
        ema20: EMA(20) value
        ema50: EMA(50) value
        ema100: EMA(100) value
        side: 'long' or 'short'
    
    Returns:
        Trend alignment score (0-1)
    """
    # If EMAs not available, return neutral
    if ema20 is None or ema50 is None or ema100 is None:
        return 0.5
    
    if side == "long":
        # Perfect alignment: EMA20 > EMA50 > EMA100
        if ema20 > ema50 > ema100:
            return 1.0
        # Good alignment: EMA20 > EMA50
        elif ema20 > ema50:
            return 0.7
        # Mixed/flat
        elif ema20 > ema100 or ema50 > ema100:
            return 0.5
        # Opposite (bearish)
        else:
            return 0.2
    else:  # short
        # Perfect alignment: EMA20 < EMA50 < EMA100
        if ema20 < ema50 < ema100:
            return 1.0
        # Good alignment: EMA20 < EMA50
        elif ema20 < ema50:
            return 0.7
        # Mixed/flat
        elif ema20 < ema100 or ema50 < ema100:
            return 0.5
        # Opposite (bullish)
        else:
            return 0.2


def calculate_trend_direction_from_prices(prices: List[float], ema_short: int = 5, ema_long: int = 10) -> int:
    """
    Calculate trend direction from price data using EMA crossover.
    
    Args:
        prices: List of recent prices (most recent last)
        ema_short: Short EMA period (default 5)
        ema_long: Long EMA period (default 10)
    
    Returns:
        +1 for uptrend, -1 for downtrend, 0 for neutral
    """
    if not prices or len(prices) < max(ema_short, ema_long):
        return 0
    
    try:
        # Calculate short and long EMAs
        ema_s = calculate_ema(prices, ema_short)
        ema_l = calculate_ema(prices, ema_long)
        
        if ema_s is None or ema_l is None:
            return 0

        # calculate_ema returns a single float — compare directly
        current_short = ema_s
        current_long = ema_l
        
        # Trend strength threshold (1% difference)
        threshold = 0.01
        diff_pct = (current_short - current_long) / current_long if current_long > 0 else 0
        
        if diff_pct > threshold:
            return 1  # Uptrend
        elif diff_pct < -threshold:
            return -1  # Downtrend
        else:
            return 0  # Neutral
    
    except (ValueError, IndexError, ZeroDivisionError):
        return 0


def calculate_momentum_from_rsi(rsi: Optional[float], side: str) -> float:
    """
    Calculate momentum score from RSI (0-1).
    
    For longs: RSI 50-70 mapped linearly to 0-1
    RSI < 45 → 0
    RSI > 75 → taper down (overextended)
    
    For shorts: RSI 30-50 mapped linearly to 0-1
    RSI > 55 → 0
    RSI < 25 → taper down (oversold)
    
    Args:
        rsi: RSI value (0-100)
        side: 'long' or 'short'
    
    Returns:
        Momentum score (0-1)
    """
    if rsi is None:
        return 0.5  # Neutral if no RSI
    
    if side == "long":
        if rsi < 45:
            return 0.0
        elif rsi <= 70:
            # Linear mapping: 45 → 0, 70 → 1
            return (rsi - 45) / 25.0
        elif rsi <= 75:
            # Taper down: 70 → 1, 75 → 0.8
            return 1.0 - ((rsi - 70) / 5.0) * 0.2
        else:
            # Overextended: taper down more aggressively
            return max(0.0, 0.8 - ((rsi - 75) / 25.0) * 0.8)
    else:  # short
        if rsi > 55:
            return 0.0
        elif rsi >= 30:
            # Linear mapping: 55 → 0, 30 → 1
            return (55 - rsi) / 25.0
        elif rsi >= 25:
            # Taper down: 30 → 1, 25 → 0.8
            return 1.0 - ((30 - rsi) / 5.0) * 0.2
        else:
            # Oversold: taper down more aggressively
            return max(0.0, 0.8 - ((25 - rsi) / 25.0) * 0.8)

