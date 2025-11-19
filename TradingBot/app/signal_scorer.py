"""
Signal Scorer - Comprehensive decomposed scoring system (0-100).
Correlates with edge: higher score → higher win rate / better expectancy.
"""

import math
from typing import Optional, Dict, Tuple, Any
from dataclasses import dataclass

from .config import (
    IDEAL_SPREAD_BPS, MAX_SPREAD_BPS, IDEAL_LATENCY_MS, MAX_LATENCY_MS,
    MIN_ORDERBOOK_DEPTH_PCT, BTC_TREND_THRESHOLD, CORRELATION_NORMALIZATION_FACTOR,
    USE_EXTENDED_SCORING, USE_NEW_SCORING_SYSTEM
)
from .indicators import calculate_trend_alignment, calculate_momentum_from_rsi
from .regime import TradingRegime


@dataclass(slots=True)
class SignalScore:
    """Complete signal score breakdown.
    OPTIMIZATION: Using __slots__ for 30-40% memory reduction and faster attribute access.
    """
    # Setup components
    trend_score: float = 0.0
    momentum_score: float = 0.0
    volatility_score: float = 0.0
    setup_score: float = 0.0
    
    # Execution components
    spread_score: float = 0.0
    depth_score: float = 0.0
    latency_score: float = 0.0
    execution_score: float = 0.0
    
    # Risk components
    rr_score: float = 0.0
    exposure_score: float = 0.0
    streak_score: float = 0.0
    risk_score: float = 0.0
    
    # Final score
    final_score: float = 0.0  # 0-100 scale
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for logging."""
        return {
            'trend_score': round(self.trend_score, 3),
            'momentum_score': round(self.momentum_score, 3),
            'volatility_score': round(self.volatility_score, 3),
            'setup_score': round(self.setup_score, 3),
            'spread_score': round(self.spread_score, 3),
            'depth_score': round(self.depth_score, 3),
            'latency_score': round(self.latency_score, 3),
            'execution_score': round(self.execution_score, 3),
            'rr_score': round(self.rr_score, 3),
            'exposure_score': round(self.exposure_score, 3),
            'streak_score': round(self.streak_score, 3),
            'risk_score': round(self.risk_score, 3),
            'final_score': round(self.final_score, 1)
        }


class SignalScorer:
    """Comprehensive signal scoring system."""
    
    def __init__(self):
        pass
    
    def compute_trend_score(
        self,
        ema20: Optional[float],
        ema50: Optional[float],
        ema100: Optional[float],
        pct_change_24h: float,
        btc_trend: Optional[float],
        side: str
    ) -> float:
        """
        Calculate trend alignment score (0-1).
        
        If EMAs available: Use EMA alignment
        If not: Use 24h pct_change vs BTC trend alignment
        More lenient for reasonable trade frequency.
        """
        # Try EMA alignment first
        if ema20 is not None and ema50 is not None and ema100 is not None:
            return calculate_trend_alignment(ema20, ema50, ema100, side)
        
        # Fallback: Use 24h trend vs BTC trend - more lenient
        if btc_trend is not None and abs(btc_trend) > BTC_TREND_THRESHOLD:
            # Check if symbol moves in same direction as BTC
            if (pct_change_24h > 0 and btc_trend > 0) or (pct_change_24h < 0 and btc_trend < 0):
                # Same direction = good alignment
                correlation_strength = min(abs(pct_change_24h) / CORRELATION_NORMALIZATION_FACTOR, 1.0)  # More lenient (was 3.0)
                return 0.5 + (correlation_strength * 0.5)  # 0.5-1.0
            else:
                # Opposite direction = poor alignment, but not zero
                return 0.3  # More lenient (was 0.2)
        
        # No clear trend - stricter scoring
        if abs(pct_change_24h) < 0.3:
            return 0.3  # Flat/mixed (was 0.5)
        elif abs(pct_change_24h) < 0.8:
            return 0.4  # Weak trend (was 0.6)
        elif abs(pct_change_24h) < 1.5:
            return 0.6  # Moderate trend (was 0.7)
        else:
            return 0.8  # Strong trend (unchanged)
    
    def compute_momentum_score(
        self,
        rsi: Optional[float],
        pct_change_24h: float,
        side: str
    ) -> float:
        """
        Calculate momentum score (0-1) with full range utilization.
        
        Uses RSI if available, otherwise falls back to ROC (rate of change).
        Spread scores across full 0-1 range for better differentiation.
        """
        # Try RSI first
        if rsi is not None:
            return calculate_momentum_from_rsi(rsi, side)
        
        # Fallback: Use ROC (rate of change) - spread across full 0-1 range
        if side == "long":
            if pct_change_24h < -1.0:  # Strong negative momentum
                return 0.0
            elif pct_change_24h < -0.3:  # Moderate negative momentum
                # Gradually increase from 0.0 to 0.2
                return (pct_change_24h + 1.0) / 0.7 * 0.2  # 0.0-0.2
            elif pct_change_24h < 0:  # Slight negative momentum
                return 0.2 + (pct_change_24h + 0.3) / 0.3 * 0.2  # 0.2-0.4
            elif pct_change_24h < 0.5:  # Minimal positive momentum
                return 0.4 + (pct_change_24h / 0.5) * 0.2  # 0.4-0.6
            elif pct_change_24h < 1.5:  # Moderate positive: 0.5-1.5% = 0.6-0.8
                return 0.6 + ((pct_change_24h - 0.5) / 1.0) * 0.2  # 0.6-0.8
            elif pct_change_24h < 3.0:  # Strong positive: 1.5-3% = 0.8-0.95
                return 0.8 + ((pct_change_24h - 1.5) / 1.5) * 0.15  # 0.8-0.95
            else:  # Very strong: > 3% = 0.95-1.0 (capped)
                return 0.95 + min((pct_change_24h - 3.0) / 7.0, 1.0) * 0.05  # 0.95-1.0
        else:  # short
            if pct_change_24h > 0.5:  # Strong positive momentum
                return 0.0
            elif pct_change_24h > 0:
                # Positive momentum - no credit for mean reversion
                return 0.0
            elif pct_change_24h > -0.5:  # Minimal negative momentum: 0 to -0.5% = 0.4-0.6
                return 0.4 + (abs(pct_change_24h) / 0.5) * 0.2  # 0.4-0.6
            elif pct_change_24h > -1.5:  # Moderate negative: -0.5 to -1.5% = 0.6-0.8
                return 0.6 + ((abs(pct_change_24h) - 0.5) / 1.0) * 0.2  # 0.6-0.8
            elif pct_change_24h > -3.0:  # Strong negative: -1.5 to -3% = 0.8-0.95
                return 0.8 + ((abs(pct_change_24h) - 1.5) / 1.5) * 0.15  # 0.8-0.95
            else:  # Very strong: < -3% = 0.95-1.0 (capped)
                return 0.95 + min((abs(pct_change_24h) - 3.0) / 7.0, 1.0) * 0.05  # 0.95-1.0
    
    def compute_volatility_score(
        self,
        atr_pct: Optional[float],
        price: float,
        volume_24h: float
    ) -> float:
        """
        Calculate volatility score (0-1) with full range utilization.
        
        Too low vol → 0-0.4
        Sweet spot → 0.8-1.0  
        Moderate → 0.5-0.7
        Too high (whipsaw) → 0.2-0.4
        """
        if atr_pct is not None:
            # Use ATR% if available - spread across full 0-1 range
            if atr_pct < 0.003:  # < 0.3% ATR
                return 0.0  # Too low
            elif atr_pct < 0.005:  # 0.3-0.5% ATR
                # Gradually increase from 0.0 to 0.4
                return (atr_pct - 0.003) / 0.002 * 0.4  # 0.0-0.4
            elif atr_pct < 0.01:  # 0.5-1.0% ATR (sweet spot start)
                # Ramp up to sweet spot: 0.4-0.8
                return 0.4 + (atr_pct - 0.005) / 0.005 * 0.4  # 0.4-0.8
            elif atr_pct < 0.015:  # 1.0-1.5% ATR (sweet spot peak)
                # Peak performance: 0.8-1.0
                return 0.8 + (atr_pct - 0.01) / 0.005 * 0.2  # 0.8-1.0
            elif atr_pct < 0.025:  # 1.5-2.5% ATR
                # Moderate (coming down from peak): 0.6-0.7
                return 0.7 - (atr_pct - 0.015) / 0.01 * 0.1  # 0.7-0.6
            elif atr_pct < 0.04:  # 2.5-4% ATR
                # Getting choppy: 0.4-0.6
                return 0.6 - (atr_pct - 0.025) / 0.015 * 0.2  # 0.6-0.4
            else:  # > 4% ATR
                return 0.2  # Too high (whipsaw risk)
        
        # Fallback: Use volume as proxy for volatility
        # Higher volume often correlates with higher volatility
        vol_log = math.log10(volume_24h + 1.0)
        if vol_log < 6:  # < $1M volume
            return 0.2  # Too low (was 0.3)
        elif vol_log < 7:  # $1M-$10M
            return 0.5  # Moderate (was 0.6)
        elif vol_log < 8:  # $10M-$100M
            return 0.7  # Good (was 0.8)
        else:  # > $100M
            return 0.8  # Very good (was 0.9)
    
    def compute_setup_score(
        self,
        trend_score: float,
        momentum_score: float,
        volatility_score: float
    ) -> float:
        """Combine setup components: 0.4*Trend + 0.4*Momentum + 0.2*Volatility"""
        return (0.4 * trend_score) + (0.4 * momentum_score) + (0.2 * volatility_score)
    
    def compute_spread_score(self, spread_bps: float, regime_config: Optional[Any] = None) -> float:
        """
        Calculate spread score (0-1) with regime-aware thresholds.
        
        spread_pct <= ideal_spread → 1.0
        spread_pct >= max_spread → minimum score (regime-dependent)
        Linear interpolation between
        """
        # Use regime-specific thresholds if available
        if regime_config:
            ideal_spread = regime_config.min_spread_bps  # Use min as ideal
            max_spread = regime_config.max_spread_bps
            # Swing trading slightly more lenient, but still strict
            min_score = 0.1 if regime_config.regime_type == TradingRegime.SWING_TRADING else 0.0
        else:
            ideal_spread = IDEAL_SPREAD_BPS
            max_spread = MAX_SPREAD_BPS
            min_score = 0.0  # No minimum floor - wide spreads get 0.0
        
        if spread_bps <= ideal_spread:
            return 1.0
        elif spread_bps >= max_spread:
            return min_score  # Hard reject for wide spreads (0.0 for scalping/day trading)
        else:
            # Linear interpolation: ideal → 1.0, max → min_score (no minimum floor)
            range_bps = max_spread - ideal_spread
            if range_bps <= 0:
                return min_score
            ratio = (spread_bps - ideal_spread) / range_bps
            return 1.0 - (ratio * (1.0 - min_score))  # 1.0 down to min_score (0.0 or 0.1)
    
    def compute_depth_score(
        self,
        orderbook: Optional[Dict],
        order_size_usd: float,
        side: str
    ) -> float:
        """
        Calculate orderbook depth score (0-1).
        
        If order size <= X% of depth → 1.0
        If eats too much of book → 0.3-0.6
        """
        if orderbook is None:
            return 0.5  # Neutral if no orderbook data
        
        try:
            bids = orderbook.get('bids', [])
            asks = orderbook.get('asks', [])
            
            if side == "long":
                # For longs, we buy at ask, so check ask side depth
                levels = asks[:5]  # Top 5 levels
            else:
                # For shorts, we sell at bid, so check bid side depth
                levels = bids[:5]  # Top 5 levels
            
            if not levels:
                return 0.5
            
            # Calculate total depth at top N levels
            total_depth_usd = 0.0
            for level in levels:
                price = level[0]
                size = level[1]
                total_depth_usd += price * size
            
            if total_depth_usd == 0:
                return 0.3
            
            # Calculate what % of depth our order represents
            depth_ratio = order_size_usd / total_depth_usd
            
            if depth_ratio <= MIN_ORDERBOOK_DEPTH_PCT:
                return 1.0
            elif depth_ratio <= 0.1:  # 10% of depth
                return 0.8
            elif depth_ratio <= 0.2:  # 20% of depth
                return 0.6
            elif depth_ratio <= 0.3:  # 30% of depth
                return 0.4
            else:
                return 0.3  # Eating too much of book
        except Exception:
            return 0.5  # Neutral on error
    
    def compute_latency_score(self, latency_ms: float, regime_config: Optional[Any] = None) -> float:
        """
        Calculate latency score (0-1) with regime-aware thresholds.
        
        latency_ms <= ideal → 1.0
        latency_ms >= hard_limit → 0.0 (reject) or minimum (swing trading)
        Linear interpolation
        """
        # Use regime-specific thresholds if available
        if regime_config:
            ideal_latency = IDEAL_LATENCY_MS  # Keep ideal same
            max_latency = regime_config.max_latency_ms
            # Swing trading more lenient on latency
            min_score = 0.2 if regime_config.regime_type == TradingRegime.SWING_TRADING else 0.0
        else:
            ideal_latency = IDEAL_LATENCY_MS
            max_latency = MAX_LATENCY_MS
            min_score = 0.0
        
        if latency_ms <= ideal_latency:
            return 1.0
        elif latency_ms >= max_latency:
            return min_score  # Hard reject for scalping, minimum for swing
        else:
            # Linear interpolation: ideal → 1.0, max → min_score
            range_ms = max_latency - ideal_latency
            if range_ms <= 0:
                return min_score
            ratio = (latency_ms - ideal_latency) / range_ms
            return 1.0 - (ratio * (1.0 - min_score))  # 1.0 down to min_score
    
    def compute_execution_score(
        self,
        spread_score: float,
        depth_score: float,
        latency_score: float,
        regime_config: Optional[Any] = None
    ) -> float:
        """
        Combine execution components with regime-aware weights.
        
        Scalping: Emphasize latency and spread (fast execution critical)
        Swing Trading: More lenient on execution quality
        """
        # Hard reject only for latency in scalping/day trading (critical for execution)
        # Swing trading can tolerate higher latency
        if latency_score == 0.0 and regime_config and regime_config.regime_type != TradingRegime.SWING_TRADING:
            return 0.0
        
        # Regime-aware weights
        if regime_config:
            if regime_config.regime_type == TradingRegime.SCALPING:
                # Scalping: 0.5*Spread + 0.3*Depth + 0.2*Latency (emphasize spread and latency)
                return (0.5 * spread_score) + (0.3 * depth_score) + (0.2 * latency_score)
            elif regime_config.regime_type == TradingRegime.SWING_TRADING:
                # Swing: 0.4*Spread + 0.4*Depth + 0.2*Latency (less emphasis on spread/latency)
                return (0.4 * spread_score) + (0.4 * depth_score) + (0.2 * latency_score)
        
        # Default: 0.5*Spread + 0.3*Depth + 0.2*Latency
        return (0.5 * spread_score) + (0.3 * depth_score) + (0.2 * latency_score)
    
    def compute_rr_score(
        self,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        side: str
    ) -> float:
        """
        Calculate risk-reward score (0-1).
        
        More lenient: RR < 0.8 → 0.2 (minimum), RR 0.8-1.5 → 0.2-0.6, RR 1.5-2.0 → 0.6-0.9, RR >= 2.0 → 1.0
        """
        if side == "long":
            sl_distance = abs(entry_price - stop_loss)
            tp_distance = abs(take_profit - entry_price)
        else:  # short
            sl_distance = abs(stop_loss - entry_price)
            tp_distance = abs(entry_price - take_profit)
        
        if sl_distance == 0:
            return 0.0  # No credit for invalid stop loss
        
        target_rr = tp_distance / sl_distance
        
        if target_rr < 1.0:
            return 0.0  # No credit for poor risk-reward (RR < 1.0)
        elif target_rr < 1.5:
            # Map 1.0-1.5 to 0.2-0.5 (stricter)
            return 0.2 + ((target_rr - 1.0) / 0.5) * 0.3
        elif target_rr < 2.0:
            # Map 1.5-2.0 to 0.5-0.8 (stricter)
            return 0.5 + ((target_rr - 1.5) / 0.5) * 0.3
        else:
            return 1.0
    
    def compute_exposure_score(
        self,
        open_positions: int,
        max_positions: int,
        correlated_positions: int = 0
    ) -> float:
        """
        Calculate exposure score (0-1).
        
        If open_positions < N and no heavy correlation → 1.0
        If heavily loaded or correlated → 0.3-0.7
        """
        # Position utilization
        if max_positions == 0:
            utilization = 1.0
        else:
            utilization = open_positions / max_positions
        
        # Correlation penalty
        correlation_penalty = min(correlated_positions * 0.1, 0.3)
        
        if utilization < 0.5 and correlated_positions == 0:
            return 1.0
        elif utilization < 0.7 and correlated_positions <= 1:
            return 0.8 - correlation_penalty
        elif utilization < 0.9:
            return 0.6 - correlation_penalty
        else:
            return 0.3 - correlation_penalty
    
    def compute_streak_score(self, loss_streak: int) -> float:
        """
        Calculate streak score (0-1).
        
        Normal → 1.0
        Heavy loss streak → 0.6
        """
        if loss_streak == 0:
            return 1.0
        elif loss_streak == 1:
            return 0.9
        elif loss_streak == 2:
            return 0.8
        elif loss_streak >= 3:
            return 0.6
        else:
            return 1.0
    
    def compute_risk_score(
        self,
        rr_score: float,
        exposure_score: float,
        streak_score: float
    ) -> float:
        """Combine risk components: 0.6*RR + 0.4*Exposure"""
        # StreakScore can scale overall size separately, not included in RiskScore
        return (0.6 * rr_score) + (0.4 * exposure_score)
    
    def score_signal(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        symbol_stats: Dict,
        orderbook: Optional[Dict] = None,
        latency_ms: float = 0.0,
        order_size_usd: float = 0.0,
        indicators: Optional[Dict] = None,
        position_manager_state: Optional[Dict] = None,
        btc_trend: Optional[float] = None,
        regime_config: Optional[Any] = None,
        advanced_features: Optional[Any] = None  # AdvancedFeatures object
    ) -> SignalScore:
        """
        Main entry point: Score a signal with full context.
        
        Returns SignalScore object with all components.
        """
        # Extract data
        spread_bps = symbol_stats.get('spread_bps', 9999)
        volume_24h = symbol_stats.get('vol_quote', 0)
        pct_change_24h = symbol_stats.get('pct_change_24h', 0.0)
        
        # Extract indicators
        rsi = indicators.get('rsi') if indicators else None
        ema20 = indicators.get('ema20') if indicators else None
        ema50 = indicators.get('ema50') if indicators else None
        ema100 = indicators.get('ema100') if indicators else None
        atr_pct = indicators.get('atr_pct') if indicators else None
        
        # Extract position manager state
        open_positions = position_manager_state.get('open_positions', 0) if position_manager_state else 0
        max_positions = position_manager_state.get('max_positions', 8) if position_manager_state else 8
        loss_streak = position_manager_state.get('loss_streak', 0) if position_manager_state else 0
        correlated_positions = position_manager_state.get('correlated_positions', 0) if position_manager_state else 0
        
        # Calculate SetupScore components
        trend_score = self.compute_trend_score(ema20, ema50, ema100, pct_change_24h, btc_trend, side)
        momentum_score = self.compute_momentum_score(rsi, pct_change_24h, side)
        volatility_score = self.compute_volatility_score(atr_pct, entry_price, volume_24h)
        setup_score = self.compute_setup_score(trend_score, momentum_score, volatility_score)
        
        # Calculate ExecutionScore components (with regime-aware thresholds)
        spread_score = self.compute_spread_score(spread_bps, regime_config)
        depth_score = self.compute_depth_score(orderbook, order_size_usd, side)
        latency_score = self.compute_latency_score(latency_ms, regime_config)
        execution_score = self.compute_execution_score(spread_score, depth_score, latency_score, regime_config)
        
        # Calculate RiskScore components
        rr_score = self.compute_rr_score(entry_price, stop_loss, take_profit, side)
        exposure_score = self.compute_exposure_score(open_positions, max_positions, correlated_positions)
        streak_score = self.compute_streak_score(loss_streak)
        risk_score = self.compute_risk_score(rr_score, exposure_score, streak_score)
        
        # Calculate final score with regime-aware weights
        # Scalping: Emphasize execution (fast entries/exits critical)
        # Day Trading: Balanced approach
        # Swing Trading: Emphasize setup (trend/momentum more important)
        if regime_config:
            regime_type = regime_config.regime_type
            if regime_type == TradingRegime.SCALPING:
                # Scalping: 50% Setup, 35% Execution, 15% Risk
                setup_weight = 0.50
                exec_weight = 0.35
                risk_weight = 0.15
            elif regime_type == TradingRegime.SWING_TRADING:
                # Swing: 70% Setup, 15% Execution, 15% Risk
                setup_weight = 0.70
                exec_weight = 0.15
                risk_weight = 0.15
            else:  # DAY_TRADING
                # Day Trading: 60% Setup, 25% Execution, 15% Risk (default)
                setup_weight = 0.60
                exec_weight = 0.25
                risk_weight = 0.15
        else:
            # Default weights (balanced)
            setup_weight = 0.60
            exec_weight = 0.25
            risk_weight = 0.15
        
        final_score = 100.0 * (
            (setup_weight * setup_score) +
            (exec_weight * execution_score) +
            (risk_weight * risk_score)
        )
        
        # Apply multiplicative penalty for moderate to poor execution (prevents compensation)
        # Softened thresholds and penalties to avoid crushing borderline signals
        if regime_config and regime_config.regime_type == TradingRegime.SWING_TRADING:
            exec_threshold = 0.3  # softened from 0.4
            if execution_score < exec_threshold:
                # Penalty: multiply by 0.9 (softened from 0.8)
                final_score = final_score * 0.9
        else:
            exec_threshold = 0.4  # softened from 0.5 (scalping/day trading)
            if execution_score < exec_threshold:
                # Penalty: multiply by 0.9 (softened from 0.5-0.8x range)
                # Still punishes bad execution, but doesn't crush borderline signals
                final_score = final_score * 0.9
        
        # Use new clean scoring system if enabled
        if USE_NEW_SCORING_SYSTEM:
            try:
                from .scoring import compute_final_score, build_signal_context
                
                # Build SignalContext from existing data
                ctx = build_signal_context(
                    symbol=symbol,
                    side=side,
                    base_score=final_score,  # Current base score (0-100) - will be converted to 0-60
                    symbol_stats=symbol_stats,
                    orderbook=orderbook,
                    indicators=indicators,
                    advanced_features=advanced_features,
                    position_manager_state=position_manager_state
                )
                
                # Compute final score using new clean architecture
                final_score, raw_components, capped_components = compute_final_score(ctx)
                
            except Exception as e:
                # Fallback to original scoring if new system fails
                pass
        
        # Legacy: Use old extended scoring if new system disabled but extended enabled
        elif USE_EXTENDED_SCORING:
            try:
                from .extended_scoring import compute_extended_score
                
                # Get regime type from regime_config
                regime_type_str = "scalping"
                if regime_config:
                    if regime_config.regime_type == TradingRegime.SCALPING:
                        regime_type_str = "scalping"
                    elif regime_config.regime_type == TradingRegime.DAY_TRADING:
                        regime_type_str = "day"
                    elif regime_config.regime_type == TradingRegime.SWING_TRADING:
                        regime_type_str = "swing"
                
                # Compute extended score (caps base_score to 0-60, adds all components)
                extended_components, final_score = compute_extended_score(
                    base_score=final_score / 100.0 * 60.0,  # Convert to 0-60 scale for base
                    symbol=symbol,
                    side=side,
                    symbol_stats=symbol_stats,
                    orderbook=orderbook,
                    order_size_usd=order_size_usd,
                    indicators=indicators,
                    advanced_features=advanced_features,
                    position_manager_state=position_manager_state,
                    regime_type=regime_type_str,
                    historical_performance=None,
                    hour_utc=None
                )
            except Exception:
                pass  # Fallback to original scoring
        
        # Legacy: Integrate advanced reversal scores if neither system enabled
        elif advanced_features:
            try:
                from .feature_engine import integrate_reversal_scores
                reversal_score_long = getattr(advanced_features, 'reversal_score_long', 0.0)
                reversal_score_short = getattr(advanced_features, 'reversal_score_short', 0.0)
                final_score = integrate_reversal_scores(
                    final_score,
                    reversal_score_long,
                    reversal_score_short,
                    side
                )
            except Exception:
                pass  # Fallback to base score if integration fails
        
        # Ensure final score is in valid range
        final_score = max(0.0, min(100.0, final_score))
        
        return SignalScore(
            trend_score=trend_score,
            momentum_score=momentum_score,
            volatility_score=volatility_score,
            setup_score=setup_score,
            spread_score=spread_score,
            depth_score=depth_score,
            latency_score=latency_score,
            execution_score=execution_score,
            rr_score=rr_score,
            exposure_score=exposure_score,
            streak_score=streak_score,
            risk_score=risk_score,
            final_score=final_score
        )

