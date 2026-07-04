"""
Continuous Z-Score Composite Scoring — replaces binary checklist.

Moss-style 5-pillar architecture: each pillar returns [-1, 1], weighted
by predictive power. Entry at |composite| > 0.6. Positive = short bias,
negative = long bias.

Pillars:
  1. Mean Reversion  (30%) — price deviation from EMA20
  2. Regime/Trend     (20%) — ADX sweet spot detection
  3. Momentum         (25%) — RSI extremity
  4. Liquidity        (10%) — 24h volume adequacy
  5. Volatility       (15%) — ATR% sweet spot

Key insight from research: continuous z-score normalization discriminates
where binary checklists produce uniform scores.
"""

from typing import Dict, Optional, Tuple, List


def clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return lo if value < lo else hi if value > hi else value


def compute_composite(
    side: str,
    indicators: Optional[Dict],
    symbol_stats: Dict,
    pct_change_24h: float = 0.0,
    volume_24h: float = 0.0,
    spread_bps: float = 9999.0,
) -> Tuple[float, bool, Dict[str, float], str]:
    """
    Compute continuous composite score for mean-reversion entry.

    Returns:
        composite: float [-1, 1], positive=short bias, negative=long bias
        passed: bool, |composite| >= 0.6
        pillars: Dict of pillar_name → score
        reason: str explaining the decision
    """
    pillars: Dict[str, float] = {}
    weights = {
        'mr': 0.30,      # Mean reversion — most important
        'trend': 0.20,   # Regime filter
        'momentum': 0.25, # RSI extremity
        'liquidity': 0.10, # Volume
        'volatility': 0.15, # ATR sweet spot
    }

    # Default indicator values if not available
    ema20 = indicators.get('ema20') if indicators else None
    rsi = indicators.get('rsi') if indicators else None
    adx = indicators.get('adx', 20.0) if indicators else 20.0
    atr_pct = indicators.get('atr_pct') if indicators else None
    last_price = symbol_stats.get('last', 0) if symbol_stats else 0

    # --- Pillar 1: Mean Reversion (price deviation from SMA20) ---
    if ema20 and last_price and ema20 > 0:
        deviation_pct = (last_price - ema20) / ema20  # positive = above mean, negative = below
        # For mean reversion: above mean → short bias (positive), below → long bias (negative)
        # Normalize: |2%| deviation → |1.0| score
        mr_raw = -deviation_pct / 0.02  # invert: above mean → short (positive), below → long (negative)
        pillars['mr'] = clamp(mr_raw, -1.0, 1.0)
    else:
        pillars['mr'] = 0.0

    # --- Pillar 2: Regime/Trend (ADX sweet spot) ---
    # ADX 15-25: ideal for mean-reversion (+1). Outside: tapering.
    if adx is not None:
        if 15 <= adx <= 25:
            trend_score = 1.0
        elif adx < 15:
            trend_score = adx / 15.0  # linear: 0→0, 15→1
        elif adx <= 35:
            trend_score = 1.0 - (adx - 25) / 10.0  # linear: 25→1, 35→0
        else:
            trend_score = -1.0  # strong trend → anti-MR
        pillars['trend'] = clamp(trend_score, -1.0, 1.0)
    else:
        pillars['trend'] = 0.0

    # --- Pillar 3: Momentum (RSI extremity) ---
    # RSI > 60: bearish momentum → short bias. RSI < 40: bullish → long bias.
    if rsi is not None:
        if rsi > 50:
            momentum_score = (rsi - 50) / 30.0  # 50→0, 80→1.0
        else:
            momentum_score = (rsi - 50) / 30.0  # 50→0, 20→-1.0
        pillars['momentum'] = clamp(momentum_score, -1.0, 1.0)
    else:
        # Fallback: use 24h pct change
        if abs(pct_change_24h) > 0:
            momentum_score = clamp(pct_change_24h / 10.0, -1.0, 1.0)
            pillars['momentum'] = -momentum_score  # invert: big drop → long bias
        else:
            pillars['momentum'] = 0.0

    # --- Pillar 4: Liquidity (24h volume) ---
    if volume_24h > 0:
        # $50M = 0, $200M+ = +1, < $20M = negative
        vol_score = (volume_24h / 150_000_000) - 0.33
        pillars['liquidity'] = clamp(vol_score, -1.0, 1.0)
    else:
        pillars['liquidity'] = -0.5  # unknown volume = penalty

    # --- Pillar 5: Volatility (ATR sweet spot) ---
    # Sweet spot: 0.3% - 2.0% ATR for crypto mean-reversion
    if atr_pct is not None:
        if 0.3 <= atr_pct <= 2.0:
            vol_score = 1.0
        elif atr_pct < 0.3:
            vol_score = atr_pct / 0.3  # dead vol → 0
        else:
            vol_score = max(0.0, 1.0 - (atr_pct - 2.0) / 3.0)  # >2% ATR tapering
        pillars['volatility'] = clamp(vol_score, -1.0, 1.0)
    else:
        pillars['volatility'] = 0.0

    # --- Weighted composite ---
    composite = sum(weights[name] * pillars.get(name, 0.0) for name in weights)
    composite = clamp(composite, -1.0, 1.0)

    # --- Entry decision ---
    threshold = 0.3  # Aggressive data-gathering mode — lower barrier, more entries
    direction_match = True
    if side.lower() == 'long':
        direction_match = composite <= -threshold  # strong negative = long
    else:  # short
        direction_match = composite >= threshold  # strong positive = short

    passed = abs(composite) >= threshold and direction_match

    # --- Reason string ---
    if passed:
        direction = "SHORT" if composite > 0 else "LONG"
        reason = f"composite={composite:+.2f} {direction} | mr={pillars.get('mr',0):+.2f} trend={pillars.get('trend',0):+.2f} mom={pillars.get('momentum',0):+.2f}"
    else:
        reason = f"composite={composite:+.2f} below threshold ±{threshold}"

    return composite, passed, pillars, reason
