"""Adapter to convert existing data structures to SignalContext."""

import time
from datetime import datetime, timezone
from typing import Dict, Optional, Any

from .context import SignalContext
from .config import BASE_SCORE_MIN, BASE_SCORE_MAX
from .model import clamp
from ..advanced_features import AdvancedFeatures


def build_signal_context(
    symbol: str,
    side: str,
    base_score: float,  # Base score 0-100 (trusted as true primary score)
    symbol_stats: Dict,
    orderbook: Optional[Dict] = None,
    indicators: Optional[Dict] = None,
    advanced_features: Optional[AdvancedFeatures] = None,
    position_manager_state: Optional[Dict] = None,
    bot_positions: Optional[Dict] = None  # Optional: actual positions dict from bot
) -> SignalContext:
    """
    Build SignalContext from existing data structures.
    
    Args:
        symbol: Trading symbol
        side: 'long' or 'short'
        base_score: Base signal score (0-100) from primary scoring engine
        symbol_stats: Symbol statistics dict
        orderbook: Orderbook dict
        indicators: Indicators dict
        advanced_features: AdvancedFeatures object (optional)
        position_manager_state: Position manager state dict
        bot_positions: Optional bot.positions dict for sector tracking
    
    Returns:
        SignalContext object
    
    Notes:
        - base_score is now the true 0-100 value from the primary engine
        - No scaling or artificial capping applied
        - Modules add small adjustments (±5-20 points) around the base
        - Final score = base + modules, clamped to [0, 100]
    """
    # Base score is already 0-100 from primary engine - just clamp for safety
    base_score_clamped = clamp(base_score, BASE_SCORE_MIN, BASE_SCORE_MAX)
    
    # Time context
    now_ts = int(time.time())
    now_utc = datetime.now(timezone.utc)
    hour_utc = now_utc.hour
    weekday = now_utc.weekday()  # 0=Mon, 6=Sun
    
    # Determine session
    if weekday >= 5:  # Sat-Sun
        session = "WEEKEND"
    elif 0 <= hour_utc < 8:
        session = "ASIA"
    elif 8 <= hour_utc < 13:
        session = "EU"
    elif 13 <= hour_utc < 21:
        session = "US"
    else:
        session = "ASIA"  # Late night US / early Asia
    
    # Market microstructure
    spread_bps = symbol_stats.get('spread_bps', 9999)
    spread_pct = spread_bps / 10000.0  # Convert bps to percentage
    
    # Calculate depth and orderbook imbalance from orderbook
    depth_top_usd = 0.0
    orderbook_imbalance = 0.5  # Neutral default
    if orderbook:
        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])
        if bids and asks:
            best_bid = bids[0][0] if bids else 0
            best_ask = asks[0][0] if asks else 0
            mid_price = (best_bid + best_ask) / 2.0 if (best_bid > 0 and best_ask > 0) else 0

            if mid_price > 0:
                # Depth within 10 bps (0.1%) of mid
                depth_window = mid_price * 0.001
                bid_depth = sum(
                    qty * price for price, qty in bids
                    if abs(best_bid - price) <= depth_window
                )
                ask_depth = sum(
                    qty * price for price, qty in asks
                    if abs(price - best_ask) <= depth_window
                )
                depth_top_usd = min(bid_depth, ask_depth)
                # Orderbook imbalance: >0.5 = bid-heavy (buy pressure), <0.5 = ask-heavy
                total_near = bid_depth + ask_depth
                if total_near > 0:
                    orderbook_imbalance = bid_depth / total_near

    # ATR and HTF ADX — try indicators dict first, then advanced_features, then default
    atr_pct = indicators.get('atr_pct') if indicators else 0.01
    if advanced_features and advanced_features.htf_adx_14:
        htf_adx = advanced_features.htf_adx_14
    elif indicators and indicators.get('adx'):
        htf_adx = indicators.get('adx')
    else:
        htf_adx = 20.0  # Neutral default
    
    # Structure / pattern flags
    htf_trend_dir = advanced_features.htf_trend_dir if advanced_features else 0
    at_range_high = False
    at_range_low = False
    
    if advanced_features:
        if advanced_features.htf_range_high and advanced_features.dist_from_htf_ema50 > 0.01:
            at_range_high = True
        if advanced_features.htf_range_low and advanced_features.dist_from_htf_ema50 < -0.01:
            at_range_low = True
    
    extreme_up = advanced_features.extreme_up if advanced_features else False
    extreme_down = advanced_features.extreme_down if advanced_features else False
    bearish_divergence = advanced_features.bearish_divergence if advanced_features else False
    bullish_divergence = advanced_features.bullish_divergence if advanced_features else False
    exhaustion_up = advanced_features.exhaustion_up if advanced_features else False
    exhaustion_down = advanced_features.exhaustion_down if advanced_features else False
    sfp_top = advanced_features.sfp_top if advanced_features else False
    sfp_bottom = advanced_features.sfp_bottom if advanced_features else False
    dist_from_vwap = advanced_features.dist_from_vwap if advanced_features else 0.0
    
    # Portfolio stats — count positions with similar base asset or high BTC correlation
    open_positions_same_sector = 0
    if bot_positions:
        base = symbol.split('/')[0] if '/' in symbol else symbol
        for pos_sym, pos in bot_positions.items():
            if pos_sym == symbol:
                continue
            pos_base = pos_sym.split('/')[0] if '/' in pos_sym else pos_sym
            # Same sector = first 2 chars match (e.g., "SOL" and "SOLANA" are different)
            # or position side correlation with BTC trend suggests same risk factor
            corr_to_btc_24h = symbol_stats.get('btc_correlation', 0.0) if symbol_stats else 0.0
            if pos_base[:2] == base[:2] or abs(corr_to_btc_24h) > 0.85:
                open_positions_same_sector += 1

    # Symbol rating — use reversal score as proxy for historical quality
    symbol_rating = 0.0
    if advanced_features:
        rev_long = getattr(advanced_features, 'reversal_score_long', 0) or 0
        rev_short = getattr(advanced_features, 'reversal_score_short', 0) or 0
        best_rev = max(rev_long, rev_short)
        if best_rev > 25:
            symbol_rating = 6.0
        elif best_rev > 15:
            symbol_rating = 3.0
        elif best_rev > 8:
            symbol_rating = 1.5
    # OI change and funding rate from stats (dict or object)
    _get = lambda k, d: symbol_stats.get(k, d) if isinstance(symbol_stats, dict) else getattr(symbol_stats, k, d)
    oi_change_pct = _get('oi_change_pct', 0.0)
    funding_rate = _get('funding_rate', 0.0)

    # Taker buy/sell ratio and long/short ratio from stats (fetched per-candidate)
    taker_buy_ratio = _get('taker_buy_ratio', 0.5)
    long_short_ratio = _get('long_short_ratio', 1.0)

    # Absorption score: bid depth ÷ price movement.
    # High bid depth + dropping price = someone absorbing sell pressure = reversal ahead.
    # Range 0-1: 0=no absorption, 1=strong absorption signal.
    absorption_score = 0.0
    if orderbook and orderbook_imbalance > 0.5:  # Bid-heavy book
        pct_change = symbol_stats.get('pct_change', 0.0) or 0.0
        if pct_change < 0:  # Price dropping despite bid-heavy book
            # Normalize: deeper bids + bigger drop = stronger absorption
            bid_depth = orderbook.get('bids', [[0, 0]])[0][1] if orderbook.get('bids') else 0
            absorption_score = min(1.0, (bid_depth / max(abs(pct_change), 0.01)) / 1_000_000)
    elif orderbook and orderbook_imbalance < 0.5:  # Ask-heavy book
        pct_change = symbol_stats.get('pct_change', 0.0) or 0.0
        if pct_change > 0:  # Price rising despite ask-heavy book
            ask_depth = orderbook.get('asks', [[0, 0]])[0][1] if orderbook.get('asks') else 0
            absorption_score = min(1.0, (ask_depth / max(abs(pct_change), 0.01)) / 1_000_000)

    return SignalContext(
        symbol=symbol,
        side=side,  # type: ignore
        base_score=base_score_clamped,
        now_ts=now_ts,
        hour_utc=hour_utc,
        session=session,
        spread_pct=spread_pct,
        depth_top_usd=depth_top_usd,
        atr_pct=atr_pct,
        htf_adx=htf_adx,
        htf_trend_dir=htf_trend_dir,
        at_range_high=at_range_high,
        at_range_low=at_range_low,
        extreme_up=extreme_up,
        extreme_down=extreme_down,
        bearish_divergence=bearish_divergence,
        bullish_divergence=bullish_divergence,
        exhaustion_up=exhaustion_up,
        exhaustion_down=exhaustion_down,
        sfp_top=sfp_top,
        sfp_bottom=sfp_bottom,
        dist_from_vwap=dist_from_vwap,
        open_positions_same_sector=open_positions_same_sector,
        corr_to_btc_24h=corr_to_btc_24h,
        symbol_rating=symbol_rating,
        orderbook_imbalance=orderbook_imbalance,
        oi_change_pct=oi_change_pct,
        funding_rate=funding_rate,
        taker_buy_ratio=taker_buy_ratio,
        long_short_ratio=long_short_ratio,
        absorption_score=absorption_score,
    )

