"""Scoring Modules - Pure functions for each score dimension."""

from .context import SignalContext


def score_liquidity(ctx: SignalContext) -> float:
    """Compute liquidity score (uncapped). SCALPER: Modest range -5 to +5."""
    s = 0.0

    # Spread - slight negatives for bad, small positives for healthy
    if ctx.spread_pct <= 0.0002:      # <= 0.02% (exceptional)
        s += 3.0
    elif ctx.spread_pct <= 0.0005:    # <= 0.05% (good)
        s += 1.5
    elif ctx.spread_pct <= 0.0010:    # <= 0.10% (acceptable)
        s += 0.0                      # Neutral
    elif ctx.spread_pct <= 0.0020:    # <= 0.20% (mediocre)
        s -= 1.5
    else:
        s -= 4.0                      # Wide spread

    # Depth - reward healthy, penalize thin
    if ctx.depth_top_usd >= 50_000:   # Exceptional depth
        s += 2.0
    elif ctx.depth_top_usd >= 20_000: # Good depth
        s += 1.0
    elif ctx.depth_top_usd >= 5_000:  # Normal depth
        s += 0.0                      # Neutral
    elif ctx.depth_top_usd < 2_000:   # Thin
        s -= 2.5

    return s


def score_regime(ctx: SignalContext) -> float:
    """Compute volatility/regime score (uncapped). SCALPER: Small but meaningful -4 to +4."""
    atr = ctx.atr_pct
    adx = ctx.htf_adx
    s = 0.0

    # SCALPER: Reward volatility pockets where scalping works, penalize extreme chop/dead vol
    if atr < 0.003 and adx < 12:
        # Very low-vol chop (rare, ideal for tight scalps)
        s += 2.5
    elif atr < 0.005 and adx < 15:
        # Low-vol (normal scalping)
        s += 1.5
    elif atr > 0.015 and adx > 30:
        # Strong trend (good for momentum scalping)
        s += 2.0
    elif atr > 0.04:
        # Panic/extreme (dangerous for scalper)
        s -= 4.0
    elif atr < 0.002 and adx < 10:
        # Dead volatility (hard to scalp)
        s -= 2.0
    else:
        # Normal conditions (most signals)
        s += 0.0

    return s


def score_structure(ctx: SignalContext) -> float:
    """Compute structure/pattern score (uncapped). BALANCED MODE: Boost for 1 strong signal."""
    s = 0.0

    # BALANCED MODE: Reward confluence, but also boost single strong signals
    # Typical ranges: mediocre 0-3, decent +4-7, strong +8-15, rare A-tier +15-20
    if ctx.side == "long":
        if ctx.htf_trend_dir == 1:
            s += 2.0                     # Basic trend alignment
        if ctx.at_range_low:
            s += 5.0                     # Range position (medium signal)
        if ctx.extreme_down:
            s += 7.0                     # Extreme readings
        if ctx.bullish_divergence:
            s += 6.0                     # Divergence (strong signal)
        if ctx.exhaustion_down:
            s += 10.0                    # Exhaustion + reversal (strong signal)
        if ctx.sfp_bottom:
            s += 15.0                    # SFP (stop hunt) highest conviction (strong signal)
        if ctx.dist_from_vwap < -0.015:
            s += 4.0                     # VWAP distance bonus (medium signal)
        
        # Penalize conflicting signals
        if ctx.bearish_divergence:
            s -= 5.0                     # Conflicting divergence
        if ctx.exhaustion_up:
            s -= 6.0                     # Conflicting exhaustion

    else:  # short
        if ctx.htf_trend_dir == -1:
            s += 2.0
        if ctx.at_range_high:
            s += 5.0                     # Range position (medium signal)
        if ctx.extreme_up:
            s += 7.0
        if ctx.bearish_divergence:
            s += 6.0                     # Divergence (strong signal)
        if ctx.exhaustion_up:
            s += 10.0                    # Exhaustion (strong signal)
        if ctx.sfp_top:
            s += 15.0                    # SFP (strong signal)
        if ctx.dist_from_vwap > 0.015:
            s += 4.0                     # VWAP distance bonus (medium signal)
        
        # Penalize conflicting signals
        if ctx.bullish_divergence:
            s -= 5.0                     # Conflicting divergence
        if ctx.exhaustion_down:
            s -= 6.0                     # Conflicting exhaustion

    return s


def score_portfolio(ctx: SignalContext) -> float:
    """Compute portfolio/correlation score (uncapped)."""
    s = 0.0

    # Sector crowding
    if ctx.open_positions_same_sector >= 3:
        s -= 5.0                     # softened from -8.0
    elif ctx.open_positions_same_sector == 2:
        s -= 2.0                     # softened from -3.0

    # Correlation to BTC/ETH
    if ctx.corr_to_btc_24h > 0.9:
        s -= 3.0                     # softened from -5.0

    return s


def score_time_of_day(ctx: SignalContext) -> float:
    """Compute time-of-day score (uncapped). SCALPER: Small range -3 to +3."""
    s = 0.0
    sess = ctx.session.upper()

    # SCALPER: Slightly reward EU/US core hours, slightly penalize illiquid/dead periods
    if sess == "US" and 14 <= ctx.hour_utc <= 20:
        # Peak US hours (9am-3pm EST) - highest liquidity overlap
        s += 2.0
    elif sess in ("EU", "US"):
        # Normal EU/US hours
        s += 0.5
    elif sess == "ASIA":
        s += 0.0                     # Neutral

    if sess == "WEEKEND" and 0 <= ctx.hour_utc <= 6:
        # Dead weekend hours
        s -= 3.0

    return s


def score_symbol_rating(ctx: SignalContext) -> float:
    """Compute symbol rating score (uncapped). PURE SCALPER: Small magnitude."""
    # PURE SCALPER MODE: Keep magnitude small (-3 to +3) so it doesn't override structure
    # Scale down the rating to keep it as a small adjustment
    return ctx.symbol_rating * 0.3  # Scale to -3 to +3 range


def score_order_flow(ctx: SignalContext) -> float:
    """Orderbook imbalance, OI, funding, taker flow, positioning, absorption. Range -5 to +5."""
    s = 0.0

    # Orderbook imbalance: bid-heavy = buying pressure, ask-heavy = selling pressure
    imb = ctx.orderbook_imbalance
    if ctx.side == "long":
        if imb > 0.65:      s += 2.0   # Strong bid support
        elif imb > 0.55:    s += 1.0   # Mild bid bias
        elif imb < 0.35:    s -= 2.0   # Ask-heavy — bad for longs
        elif imb < 0.45:    s -= 1.0   # Mild ask bias
    else:  # short
        if imb < 0.35:      s += 2.0   # Strong ask pressure
        elif imb < 0.45:    s += 1.0   # Mild ask bias
        elif imb > 0.65:    s -= 2.0   # Bid-heavy — bad for shorts
        elif imb > 0.55:    s -= 1.0   # Mild bid bias

    # Open Interest change: OI rising with price = trend conviction
    oi = ctx.oi_change_pct
    if oi != 0:
        if ctx.side == "long":
            if oi > 2.0:    s += 1.5
            elif oi > 0.5:  s += 0.5
            elif oi < -2.0: s -= 1.0
        else:  # short
            if oi > 2.0:    s += 1.5
            elif oi > 0.5:  s += 0.5
            elif oi < -2.0: s -= 1.0

    # Funding rate: extreme rates = crowded trade → contrarian signal
    fr = ctx.funding_rate
    if abs(fr) > 0.0001:
        if ctx.side == "long" and fr > 0.0005:       s -= 1.5
        elif ctx.side == "long" and fr > 0.0002:     s -= 0.5
        elif ctx.side == "short" and fr < -0.0005:   s -= 1.5
        elif ctx.side == "short" and fr < -0.0002:   s -= 0.5
        elif ctx.side == "long" and fr < -0.0002:    s += 0.5
        elif ctx.side == "short" and fr > 0.0002:    s += 0.5

    # Taker buy/sell ratio: who's crossing the spread?
    # >0.55 = buyers aggressive, <0.45 = sellers aggressive
    taker = ctx.taker_buy_ratio
    if taker > 0.55:
        if ctx.side == "long":   s += 1.0  # Buyers dominating — good for longs
        else:                    s -= 0.5  # Buyers dominating — bad for shorts
    elif taker < 0.45:
        if ctx.side == "short":  s += 1.0  # Sellers dominating — good for shorts
        else:                    s -= 0.5  # Sellers dominating — bad for longs

    # Long/short ratio: extreme positioning = fragile, reversal-prone
    # >2.5 = overcrowded longs, <0.4 = overcrowded shorts
    ls = ctx.long_short_ratio
    if ls > 2.5:
        if ctx.side == "long":   s -= 1.0  # Everyone's long — reversal risk
        else:                    s += 1.5  # Overcrowded longs = good short entry
    elif ls < 0.4:
        if ctx.side == "short":  s -= 1.0  # Everyone's short — reversal risk
        else:                    s += 1.5  # Overcrowded shorts = good long entry

    # Absorption score: bid depth ÷ price drop. Someone absorbing = reversal ahead.
    # Range 0-1, >0.3 = meaningful absorption.
    absorb = ctx.absorption_score
    if absorb > 0.5:
        if ctx.side == "long":   s += 2.0  # Bids absorbing sells = bullish reversal
        else:                    s -= 1.0  # Bids absorbing sells = bad for shorts
    elif absorb > 0.3:
        if ctx.side == "long":   s += 1.0
        else:                    s -= 0.5

    return max(-5.0, min(5.0, s))

