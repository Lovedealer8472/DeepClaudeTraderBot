"""
Signal Generator - Multi-factor signal generation with real-world trading criteria.
"""

import time
import math
from typing import Optional, Dict, Tuple, Any
from dataclasses import dataclass, field

from .config import (
    MIN_SIGNAL_STRENGTH, MIN_SIGNAL_SCORE, HARD_MIN_SCORE, SIGNAL_PERCENTILE_THRESHOLD, SIGNAL_HISTORY_SIZE,
    DYNAMIC_THRESHOLDS_ENABLED, THRESHOLD_ADJUSTMENT_WINDOW, THRESHOLD_ADJUSTMENT_STEP,
    WIN_RATE_RELAX_THRESHOLD, WIN_RATE_TIGHTEN_THRESHOLD, MIN_SCORE_RANGE, MIN_STRENGTH_RANGE,
    DRY_RUN, USE_SIGNAL_PERCENTILE_FILTER
)
from .signal_scorer import SignalScorer, SignalScore
from .scoring.adapter import build_signal_context
from .scoring.engine import compute_final_score
from .scoring.config import MIN_SIGNAL_SCORE as SCORING_V2_MIN_SCORE


@dataclass(slots=True)
class TradingSignal:
    """Trading signal with all relevant information.
    OPTIMIZATION: Using __slots__ for 30-40% memory reduction and faster attribute access.
    """
    symbol: str
    side: str  # 'long' or 'short'
    entry_price: float
    stop_loss: float
    take_profit: float
    strength: float  # 0-1 (backward compatibility)
    signal_type: str  # 'momentum', 'mean_reversion', 'trend', 'breakout'
    reason: str
    timestamp: float = None
    signal_score: Optional[SignalScore] = None  # Full score breakdown (old scorer)
    final_score: float = 0.0  # 0-100 scale (Scoring v2 final score)
    composite_score: float = 0.0  # [-1, 1] Moss-style continuous composite
    score_v2: Optional[float] = None  # Scoring v2 final score
    score_components_raw: Optional[Dict] = None  # Scoring v2 raw components
    score_components_capped: Optional[Dict] = None  # Scoring v2 capped components
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()
        # Backward compatibility: if final_score set but strength not, normalize
        if self.final_score > 0 and self.strength == 0:
            self.strength = self.final_score / 100.0


class SignalGenerator:
    """Generates trading signals using multiple factors."""
    
    def __init__(self):
        self.signal_history = []
        self.signal_scorer = SignalScorer()
        # OPTIMIZATION: Cache percentile threshold to avoid recalculating
        self._cached_threshold = None
        self._cached_history_size = 0
        self._cached_percentile = SIGNAL_PERCENTILE_THRESHOLD
        # Dynamic threshold adjustment with caching
        self._cached_dynamic_score = None
        self._cached_dynamic_strength = None
        self._cached_dynamic_percentile = None
        self._last_threshold_update = 0.0
        self._threshold_cache_ttl = 2.0  # Cache for 2 seconds
        self._cached_trades_hash = None  # Hash of recent trades for cache invalidation
        self._cached_regime = None
        self._cached_btc_trend = None
    
    def _calculate_dynamic_thresholds(
        self,
        recent_trades: list,
        volatility_regime: str = "Normal",
        btc_trend: float = 0.0
    ) -> Tuple[int, float, float]:
        """
        Calculate dynamic thresholds based on recent performance and market conditions.
        OPTIMIZED: Caches results with TTL to avoid redundant calculations.
        
        Returns:
            (adjusted_min_score, adjusted_min_strength, adjusted_percentile)
        """
        # OPTIMIZATION: Check cache first
        now = time.time()
        # OPTIMIZATION: Create cache key from inputs (only hash if we have enough trades)
        if recent_trades and len(recent_trades) >= THRESHOLD_ADJUSTMENT_WINDOW:
            # Only hash the relevant window to avoid hashing entire list
            start_idx = len(recent_trades) - THRESHOLD_ADJUSTMENT_WINDOW
            # OPTIMIZATION: Use a simpler hash - just count wins and use last timestamp
            wins_count = sum(1 for i in range(start_idx, len(recent_trades)) if recent_trades[i].get('was_win', False))
            last_timestamp = recent_trades[-1].get('timestamp', 0) if recent_trades else 0
            trades_hash = hash((wins_count, last_timestamp))
        else:
            trades_hash = 0
        cache_key = (trades_hash, volatility_regime, round(btc_trend, 2))
        
        # Check if cache is valid
        if (self._cached_dynamic_score is not None and 
            (now - self._last_threshold_update) < self._threshold_cache_ttl and
            self._cached_trades_hash == trades_hash and
            self._cached_regime == volatility_regime and
            abs(self._cached_btc_trend - btc_trend) < 0.01):
            return self._cached_dynamic_score, self._cached_dynamic_strength, self._cached_dynamic_percentile
        
        # Start with base thresholds
        base_score = MIN_SIGNAL_SCORE
        base_strength = MIN_SIGNAL_STRENGTH
        base_percentile = SIGNAL_PERCENTILE_THRESHOLD
        
        # LIVE/DRY_RUN MODE: Force percentile to 0.0 if threshold is disabled
        # This ensures percentile gating is completely disabled for LIVE/DRY_RUN (not REPLAY)
        if SIGNAL_PERCENTILE_THRESHOLD <= 0.0:
            base_percentile = 0.0
        
        if not DYNAMIC_THRESHOLDS_ENABLED:
            # Cache the result
            self._cached_dynamic_score = base_score
            self._cached_dynamic_strength = base_strength
            self._cached_dynamic_percentile = base_percentile
            self._last_threshold_update = now
            self._cached_trades_hash = trades_hash
            self._cached_regime = volatility_regime
            self._cached_btc_trend = btc_trend
            return base_score, base_strength, base_percentile
        
        # OPTIMIZATION: Calculate recent win rate more efficiently
        # Avoid list slicing by using negative indexing directly
        win_rate = 50.0  # Default
        if len(recent_trades) >= THRESHOLD_ADJUSTMENT_WINDOW:
            # OPTIMIZATION: Count wins without creating a new list slice
            # Use iterator to avoid list creation
            wins = 0
            start_idx = len(recent_trades) - THRESHOLD_ADJUSTMENT_WINDOW
            for i in range(start_idx, len(recent_trades)):
                if recent_trades[i].get('was_win', False):
                    wins += 1
            win_rate = (wins / THRESHOLD_ADJUSTMENT_WINDOW) * 100.0
            
            # Adjust based on win rate
            if win_rate > WIN_RATE_RELAX_THRESHOLD:
                # Performing well - relax thresholds (allow more trades)
                base_score -= THRESHOLD_ADJUSTMENT_STEP
                base_strength -= (THRESHOLD_ADJUSTMENT_STEP / 100.0)
                base_percentile += 0.02  # Increase percentile (toward top 50%)
            elif win_rate < WIN_RATE_TIGHTEN_THRESHOLD:
                # Underperforming - tighten thresholds (be more selective)
                base_score += THRESHOLD_ADJUSTMENT_STEP
                base_strength += (THRESHOLD_ADJUSTMENT_STEP / 100.0)
                base_percentile -= 0.02  # Decrease percentile (away from top 50%)
        
        # Regime-aware adjustments
        # In quiet/calm markets: tighten (be more selective)
        # In volatile/trending markets: loosen (more opportunities)
        if volatility_regime in ["Low", "Quiet"]:
            base_score += 1  # Slightly tighter
            base_strength += 0.01
        elif volatility_regime in ["High", "Volatile"] and abs(btc_trend) > 1.0:
            # High volatility + strong trend = more opportunities
            base_score -= 1  # Slightly looser
            base_strength -= 0.01
            base_percentile += 0.02  # Allow more signals (toward top 50%)
        
        # Enforce hard limits
        min_score, max_score = MIN_SCORE_RANGE
        min_strength, max_strength = MIN_STRENGTH_RANGE
        base_score = max(min_score, min(max_score, base_score))
        base_strength = max(min_strength, min(max_strength, base_strength))
        # LIVE/DRY_RUN MODE: If percentile threshold is disabled (0.0), keep it at 0.0
        # Otherwise, keep percentile reasonable (top 50% max) for REPLAY mode
        if SIGNAL_PERCENTILE_THRESHOLD <= 0.0:
            base_percentile = 0.0  # Force to 0.0 for LIVE/DRY_RUN
        else:
            base_percentile = max(0.10, min(0.50, base_percentile))  # Keep percentile reasonable (top 50% max) for REPLAY
        
        # OPTIMIZATION: Cache the result
        result = (int(base_score), base_strength, base_percentile)
        self._cached_dynamic_score = result[0]
        self._cached_dynamic_strength = result[1]
        self._cached_dynamic_percentile = result[2]
        self._last_threshold_update = now
        self._cached_trades_hash = trades_hash
        self._cached_regime = volatility_regime
        self._cached_btc_trend = btc_trend
        
        return result
    
    def _is_in_top_percentile(self, signal_score: float, percentile_threshold: float = None) -> bool:
        """
        Check if signal score is in top percentile of recent signals.
        OPTIMIZED: Caches percentile threshold calculation.
        
        Args:
            signal_score: Final score of the signal (0-100)
            percentile_threshold: Optional percentile threshold (0-1), uses dynamic if None
        
        Returns:
            True if signal is in top percentile, False otherwise
            If history is too small (< 20 signals), uses relaxed fallback (75+) instead of 80+
        """
        # Use provided percentile threshold or default
        pct_threshold = percentile_threshold if percentile_threshold is not None else SIGNAL_PERCENTILE_THRESHOLD
        
        # Need at least 20 signals for reliable percentile calculation
        # When history is insufficient, use MIN_SIGNAL_SCORE as fallback
        history_size = len(self.signal_history)
        if history_size < 20:
            from .config import MIN_SIGNAL_SCORE
            return signal_score >= MIN_SIGNAL_SCORE  # Use configured minimum score
        
        # OPTIMIZATION: Only recalculate threshold if history changed or percentile threshold changed
        cache_key = (history_size, pct_threshold)
        if self._cached_threshold is None or self._cached_history_size != history_size or \
           abs(self._cached_percentile - pct_threshold) > 0.001:
            # Extract final scores from recent history (limit to SIGNAL_HISTORY_SIZE)
            recent_scores = []
            for signal_record in self.signal_history[-SIGNAL_HISTORY_SIZE:]:
                final_score = signal_record.get('final_score', 0.0)
                if final_score > 0:  # Only include scored signals
                    recent_scores.append(final_score)
            
            if len(recent_scores) < 20:
                # Use MIN_SIGNAL_SCORE as fallback when history is insufficient
                from .config import MIN_SIGNAL_SCORE
                self._cached_threshold = float(MIN_SIGNAL_SCORE)
                self._cached_history_size = history_size
                self._cached_percentile = pct_threshold
                return signal_score >= MIN_SIGNAL_SCORE  # Use configured minimum score
            
            # Sort scores descending
            recent_scores_sorted = sorted(recent_scores, reverse=True)
            
            # Calculate percentile threshold index
            # pct_threshold = 0.50 means top 50%, so we want scores at or above the 50th percentile (median)
            threshold_index = int(len(recent_scores_sorted) * pct_threshold)
            threshold_index = max(0, min(threshold_index, len(recent_scores_sorted) - 1))  # Clamp to valid range
            
            self._cached_threshold = recent_scores_sorted[threshold_index]
            self._cached_history_size = history_size
            self._cached_percentile = pct_threshold
        
        # Accept if signal score is at or above threshold
        return signal_score >= self._cached_threshold
    
    def generate_signal(
        self,
        symbol: str,
        symbol_stats: Dict[str, Any],
        price_data: Optional[Dict[str, Any]] = None,
        indicators: Optional[Dict[str, Any]] = None,
        orderbook: Optional[Dict[str, Any]] = None,
        latency_ms: float = 0.0,
        order_size_usd: float = 0.0,
        position_manager_state: Optional[Dict[str, Any]] = None,
        btc_trend: Optional[float] = None,
        regime_config: Optional[Any] = None,
        recent_trades: Optional[list] = None,
        volatility_regime: str = "Normal",
        bot_positions: Optional[Dict] = None  # SCORING V2: For portfolio scoring
    ) -> Tuple[Optional[TradingSignal], Optional[str]]:
        """
        Generate trading signal for symbol.
        
        Args:
            symbol: Trading symbol (e.g., 'BTC/USDT:USDT')
            symbol_stats: Symbol statistics from universe
            price_data: Optional OHLCV price data
            indicators: Optional technical indicators
            orderbook: Optional orderbook data
            latency_ms: Current latency in milliseconds
            order_size_usd: Order size in USD
            position_manager_state: Current position manager state
            btc_trend: BTC trend percentage
            regime_config: Optional regime configuration
            recent_trades: Optional list of recent trades
            volatility_regime: Current volatility regime
        
        Returns:
            TradingSignal if valid signal found, None otherwise
        """
        # Basic validation
        if not symbol_stats:
            return None
        
        bid = symbol_stats.get('bid', 0)
        ask = symbol_stats.get('ask', 0)
        last = symbol_stats.get('last', 0)
        spread_bps = symbol_stats.get('spread_bps', 9999)
        volume_24h = symbol_stats.get('vol_quote', 0)
        
        if not (bid > 0 and ask > 0 and last > 0):
            return None
        
        entry_price = (bid + ask) / 2  # Use mid price
        
        # Try multiple signal types
        signals = []
        
        # 1. Momentum signal
        momentum_signal = self._generate_momentum_signal(
            symbol, entry_price, symbol_stats, price_data, indicators, regime_config
        )
        if momentum_signal:
            # logger.debug(f"Momentum signal generated: {momentum_signal.strength}")
            signals.append(momentum_signal)
        else:
            # logger.debug(f"No momentum signal for {symbol}")
            pass

        # 2. Mean reversion signal (RSI-based — only fires with OHLCV indicators)
        mean_reversion_signal = self._generate_mean_reversion_signal(
            symbol, entry_price, symbol_stats, price_data, indicators
        )
        if mean_reversion_signal:
            signals.append(mean_reversion_signal)

        # 3. Reversal signal (24h-based — always available, no OHLCV needed)
        reversal_signal = self._generate_reversal_signal(
            symbol, entry_price, symbol_stats
        )
        if reversal_signal:
            signals.append(reversal_signal)

        # PURE SCALPER MODE: Trend signals disabled - only scalper signals (momentum/mean_reversion)
        # Trend following signal DISABLED for pure scalper mode
        # trend_signal = self._generate_trend_signal(
        #     symbol, entry_price, symbol_stats, price_data, indicators
        # )
        # if trend_signal:
        #     signals.append(trend_signal)
        
        # Select best signal
        if not signals:
            return None, "No signals generated (no momentum/mean_reversion signals)"
        
        # Sort by strength and take the best
        best_signal = max(signals, key=lambda s: s.strength)
        
        # Score the signal with comprehensive scoring system (regime-aware)
        signal_score = self.signal_scorer.score_signal(
            symbol=symbol,
            side=best_signal.side,
            entry_price=best_signal.entry_price,
            stop_loss=best_signal.stop_loss,
            take_profit=best_signal.take_profit,
            symbol_stats=symbol_stats,
            orderbook=orderbook,
            latency_ms=latency_ms,
            order_size_usd=order_size_usd,
            indicators=indicators,
            position_manager_state=position_manager_state,
            btc_trend=btc_trend,
            regime_config=regime_config
        )
        
        # Store old score in signal (for backward compatibility)
        best_signal.signal_score = signal_score
        base_final_score = signal_score.final_score
        
        # SCORING V2: Build context and compute final score with multi-dimensional adjustments
        try:
            # Get advanced_features if available (needed for Scoring v2)
            advanced_features = None
            if hasattr(self, '_advanced_features_cache'):
                advanced_features = self._advanced_features_cache.get(symbol)
            
            # Build SignalContext for Scoring v2
            ctx = build_signal_context(
                symbol=symbol,
                side=best_signal.side,
                base_score=base_final_score,  # Use old scorer's final_score as base
                symbol_stats=symbol_stats,
                orderbook=orderbook,
                indicators=indicators,
                advanced_features=advanced_features,
                position_manager_state=position_manager_state,
                bot_positions=bot_positions  # SCORING V2: For portfolio scoring
            )
            
            # Compute Scoring v2 final score
            final_score_v2, raw_components, capped_components = compute_final_score(ctx)
            
            # Store Scoring v2 results in signal
            best_signal.final_score = final_score_v2  # Use Scoring v2 final score
            best_signal.strength = final_score_v2 / 100.0  # Backward compatibility
            best_signal.score_v2 = final_score_v2
            best_signal.score_components_raw = raw_components.to_dict()
            best_signal.score_components_capped = capped_components.to_dict()
            
        except Exception as e:
            # Fallback to old score if Scoring v2 fails
            import logging
            logger = logging.getLogger("SignalGenerator")
            logger.debug(f"Scoring v2 failed for {symbol}, using base score: {e}")
            best_signal.final_score = base_final_score
            best_signal.strength = base_final_score / 100.0
            best_signal.score_v2 = None
            best_signal.score_components_raw = None
            best_signal.score_components_capped = None
        
        # Calculate dynamic thresholds (always called, but only used if DYNAMIC_THRESHOLDS_ENABLED is True)
        recent_trades_list = recent_trades if recent_trades else []
        btc_trend_value = btc_trend if btc_trend is not None else 0.0
        dynamic_score, dynamic_strength, dynamic_percentile = self._calculate_dynamic_thresholds(
            recent_trades_list, volatility_regime, btc_trend_value
        )
        
        # PURE_SCALPER: static thresholds by default.
        # If DYNAMIC_THRESHOLDS_ENABLED is set, we can re-enable adaptive behavior later.
        if DYNAMIC_THRESHOLDS_ENABLED:
            min_score = dynamic_score
            min_strength = dynamic_strength
            percentile_threshold = dynamic_percentile
        else:
            # Static thresholds: use configured values only
            min_score = float(MIN_SIGNAL_SCORE)
            min_strength = MIN_SIGNAL_STRENGTH
            percentile_threshold = 0.0  # percentile filter effectively off in LIVE/DRY
        
        # DIAGNOSTIC: Track filter rejections
        if not hasattr(self, '_filter_stats'):
            self._filter_stats = {
                'rejected_hard_min': 0,
                'rejected_score': 0,
                'rejected_strength': 0,
                'rejected_percentile': 0,
                'accepted': 0
            }
        
        # HARD MINIMUM SCORE THRESHOLD: Reject immediately if below HARD_MIN_SCORE
        # Use Scoring v2 final_score (or base if v2 failed)
        score_to_check = best_signal.final_score
        if score_to_check < HARD_MIN_SCORE:
            # Track rejected signal in history for percentile calculation
            self._add_to_history(symbol, best_signal.final_score, best_signal.strength)
            self._filter_stats['rejected_hard_min'] += 1
            return best_signal, f"RJ – score below minimum ({score_to_check:.1f} < {HARD_MIN_SCORE})"
        
        # Check minimum score (0-100 scale) using min_score (static or dynamic based on DYNAMIC_THRESHOLDS_ENABLED)
        # This is the main entry gate score threshold (after scaling calibration).
        effective_min_score = float(min_score)
        if score_to_check < effective_min_score:
            # Track rejected signal in history for percentile calculation
            self._add_to_history(symbol, best_signal.final_score, best_signal.strength)
            self._filter_stats['rejected_score'] += 1
            return best_signal, f"Score too low ({score_to_check:.1f} < {effective_min_score:.1f})"
        
        # Also check backward compatibility threshold
        if best_signal.strength < min_strength:
            # Track rejected signal in history for percentile calculation
            self._add_to_history(symbol, best_signal.final_score, best_signal.strength)
            self._filter_stats['rejected_strength'] += 1
            return best_signal, f"Strength too low ({best_signal.strength:.3f} < {min_strength})"
        
        # Check percentile threshold (only accept top X% of recent signals) - use dynamic percentile
        # MIGRATION PATCH: Completely disabled percentile filter - never reject based on percentile
        # Percentile filtering is now handled by scoring modules, not as a hard gate
        # If USE_SIGNAL_PERCENTILE_FILTER is enabled, only log for debugging (never reject)
        if USE_SIGNAL_PERCENTILE_FILTER and percentile_threshold > 0.0 and len(self.signal_history) >= 20:
            # MIGRATION PATCH: Only log percentile status, never reject
            # Lower threshold if too strict (>80% -> 45%) for logging purposes
            effective_threshold = percentile_threshold
            if percentile_threshold > 0.80:
                effective_threshold = 0.45  # Top 45% for logging
                from .logger import get_logger
                logger = get_logger("SignalGenerator")
                logger.debug(
                    f"[PERCENTILE_INFO] Threshold adjusted from {percentile_threshold*100:.0f}% to 45% "
                    f"for symbol={symbol} score={best_signal.final_score:.1f} (filter disabled)"
                )
            
            if not self._is_in_top_percentile(best_signal.final_score, effective_threshold):
                # MIGRATION PATCH: Log info only, never reject (percentile filter disabled)
                from .logger import get_logger
                logger = get_logger("SignalGenerator")
                logger.debug(
                    f"[PERCENTILE_INFO] Signal not in top {effective_threshold*100:.0f}% percentile "
                    f"but allowing through (filter disabled) | symbol={symbol} score={best_signal.final_score:.1f}"
                )
                # Track for stats but don't reject
                self._filter_stats['rejected_percentile'] += 1
                # Continue - percentile filter is disabled, never reject
        # If USE_SIGNAL_PERCENTILE_FILTER is False or percentile_threshold is 0.0, percentile check is completely bypassed (PURE_SCALPER mode)
        # For initial signals (history < 20), percentile check uses MIN_SIGNAL_SCORE as fallback
        # This ensures early signals still meet minimum quality standards
        
        # Track accepted signal in history for percentile calculation
        self._add_to_history(symbol, best_signal.final_score, best_signal.strength)
        self._filter_stats['accepted'] += 1
        
        return best_signal, None  # None means no rejection
    
    def _add_to_history(self, symbol: str, final_score: float, strength: float):
        """Add signal to history for percentile calculation.
        OPTIMIZED: Invalidates cache when history changes."""
        signal_record = {
            'timestamp': time.time(),
            'symbol': symbol,
            'final_score': final_score,
            'strength': strength
        }
        old_size = len(self.signal_history)
        self.signal_history.append(signal_record)
        # Keep only last SIGNAL_HISTORY_SIZE signals
        if len(self.signal_history) > SIGNAL_HISTORY_SIZE:
            self.signal_history = self.signal_history[-SIGNAL_HISTORY_SIZE:]
        
        # OPTIMIZATION: Invalidate cache if history size changed
        if len(self.signal_history) != old_size:
            self._cached_threshold = None
            self._cached_history_size = 0
    
    def _generate_momentum_signal(
        self,
        symbol: str,
        entry_price: float,
        symbol_stats: Dict,
        price_data: Optional[Dict],
        indicators: Optional[Dict],
        regime_config: Optional[Any] = None
    ) -> Optional[TradingSignal]:
        """Generate momentum-based signal."""
        # Use regime-specific parameters if available
        if regime_config:
            min_momentum_pct = regime_config.min_momentum_pct
            min_signal_strength = regime_config.min_signal_strength
            stop_loss_atr_mult = regime_config.stop_loss_atr_multiplier
            take_profit_atr_mult = regime_config.take_profit_atr_multiplier
        else:
            # Fallback to defaults (scalping) - VERY relaxed for Binance Futures testing
            min_momentum_pct = 0.1  # Lower from 0.8% to 0.1% for Binance Futures (allows more signals)
            min_signal_strength = MIN_SIGNAL_STRENGTH
            stop_loss_atr_mult = 1.5
            take_profit_atr_mult = 2.5
        
        # Simple momentum based on price change and volume
        pct_change = symbol_stats.get('pct_change_24h', 0)
        volume_24h = symbol_stats.get('vol_quote', 0)
        
        # Need significant momentum (regime-specific threshold)
        if abs(pct_change) < min_momentum_pct:
            # logger.debug(f"Momentum too low: {pct_change} < {min_momentum_pct}")
            return None
        
        # Calculate strength with relaxed normalization for testing
        # RELAXED: Require 4% change for full strength (was 8%)
        momentum_strength = min(abs(pct_change) / 4.0, 1.0)  # Normalize to 0-1, capped at 4%
        
        # Volume boost - relaxed for testing
        # RELAXED: Require $1M+ volume for full boost (was $10M)
        volume_factor = min(math.log10(volume_24h / 1e6 + 1) / 2.0, 1.0)  # Normalize to 0-1, requires $1M+ for full score (relaxed for testing)
        
        # TIGHTENED: Weight momentum more heavily (80% vs 20% volume)
        strength = (momentum_strength * 0.8) + (volume_factor * 0.2)
        
        # logger.debug(f"Momentum strength: {strength} (mom={momentum_strength}, vol={volume_factor}, raw_vol={volume_24h})")

        if strength < min_signal_strength:
            # logger.debug(f"Strength too low: {strength} < {min_signal_strength}")
            return None
        
        # Determine direction
        side = "long" if pct_change > 0 else "short"
        
        # Calculate stop loss and take profit (realistic with fees)
        # Account for fees: Entry fee + Exit fee + Slippage
        from .config import TAKER_FEE_RATE, SLIPPAGE_BPS
        total_fee_rate = (TAKER_FEE_RATE * 2) + (SLIPPAGE_BPS / 10000)  # Entry + Exit + Slippage
        
        # Use real ATR from indicators if available, otherwise fallback to 1.2%
        if indicators and indicators.get('atr_pct'):
            raw_atr = indicators['atr_pct']  # percentage (e.g., 0.83 = 0.83%)
            atr_pct = (raw_atr / 100.0) * 8  # Convert 1-min % to hourly decimal (×8 ≈ √60)
        else:
            atr_pct = 0.012  # Fallback: 1.2% hourly ATR

        stop_loss_pct = atr_pct * stop_loss_atr_mult
        take_profit_pct = atr_pct * take_profit_atr_mult

        # Ensure minimum R:R of 1.5:1 after fees — TP must be at least 1.5× SL
        min_tp_pct = stop_loss_pct * 1.5 + total_fee_rate
        take_profit_pct = max(take_profit_pct, min_tp_pct)

        # STOP JITTER: randomize ±STOP_JITTER_PCT to avoid predictable stop-hunt levels
        from .config import STOP_JITTER_PCT
        import random
        jitter = 1.0 + random.uniform(-STOP_JITTER_PCT, STOP_JITTER_PCT)
        stop_loss_pct *= jitter

        if side == "long":
            stop_loss = entry_price * (1 - stop_loss_pct)
            take_profit = entry_price * (1 + take_profit_pct)
        else:
            stop_loss = entry_price * (1 + stop_loss_pct)
            take_profit = entry_price * (1 - take_profit_pct)
        
        return TradingSignal(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strength=strength,
            signal_type="momentum",
            reason=f"Momentum {'breakout' if side == 'long' else 'breakdown'} ({pct_change:.2f}%, vol: ${volume_24h/1e6:.1f}M)"
        )
    
    def _generate_mean_reversion_signal(
        self,
        symbol: str,
        entry_price: float,
        symbol_stats: Dict,
        price_data: Optional[Dict],
        indicators: Optional[Dict]
    ) -> Optional[TradingSignal]:
        """Generate mean reversion signal (RSI-based)."""
        # Would use real RSI if available
        # For now, use simplified version based on price position
        
        # This is a placeholder - would need actual RSI calculation
        # RSI would come from indicators dict if available
        
        # Skip if no RSI data
        if not indicators or 'rsi' not in indicators:
            return None
        
        rsi = indicators.get('rsi', 50)
        
        # Oversold (RSI < 30) -> Long (tightened for quality)
        if rsi < 20:  # More extreme oversold (was 25) - only very oversold
            strength = (20 - rsi) / 20.0  # 0-1 based on oversold level (RSI 0-20)
            stop_loss_pct = 0.015  # 1.5% stop
            take_profit_pct = 0.030  # 3.0% target (ensures proper R:R with 1.5-2.0% stops)
            
            # Ensure minimum R:R of 1.5:1 after costs
            from .config import TAKER_FEE_RATE, SLIPPAGE_BPS
            total_fee_rate = (TAKER_FEE_RATE * 2) + (SLIPPAGE_BPS / 10000)
            min_tp_pct = stop_loss_pct * 1.5 + total_fee_rate
            take_profit_pct = max(take_profit_pct, min_tp_pct)
            # Cap at 2.0% for scalping
            take_profit_pct = max(take_profit_pct, stop_loss_pct * 1.5)  # Min 1.5:1 R:R
            
            return TradingSignal(
                symbol=symbol,
                side="long",
                entry_price=entry_price,
                stop_loss=entry_price * (1 - stop_loss_pct),
                take_profit=entry_price * (1 + take_profit_pct),
                strength=min(strength, 0.85),  # Cap at 0.85 (was 0.9) - tighter
                signal_type="mean_reversion",
                reason=f"RSI oversold ({rsi:.1f})"
            )
        
        # Overbought (RSI > 70) -> Short (tightened for quality)
        if rsi > 80:  # More extreme overbought (was 75) - only very overbought
            strength = (rsi - 80) / 20.0  # 0-1 based on overbought level (RSI 80-100)
            stop_loss_pct = 0.015  # 1.5% stop
            take_profit_pct = 0.030  # 3.0% target (ensures proper R:R with 1.5-2.0% stops)
            
            # Ensure minimum R:R of 1.5:1 after costs
            from .config import TAKER_FEE_RATE, SLIPPAGE_BPS
            total_fee_rate = (TAKER_FEE_RATE * 2) + (SLIPPAGE_BPS / 10000)
            min_tp_pct = stop_loss_pct * 1.5 + total_fee_rate
            take_profit_pct = max(take_profit_pct, min_tp_pct)
            # Cap at 2.0% for scalping
            take_profit_pct = max(take_profit_pct, stop_loss_pct * 1.5)  # Min 1.5:1 R:R
            
            return TradingSignal(
                symbol=symbol,
                side="short",
                entry_price=entry_price,
                stop_loss=entry_price * (1 + stop_loss_pct),
                take_profit=entry_price * (1 - take_profit_pct),
                strength=min(strength, 0.85),  # Cap at 0.85 (was 0.9) - tighter
                signal_type="mean_reversion",
                reason=f"RSI overbought ({rsi:.1f})"
            )
        
        return None
    
    def _generate_reversal_signal(
        self,
        symbol: str,
        entry_price: float,
        symbol_stats: Dict
    ) -> Optional[TradingSignal]:
        """Generate mean-reversion reversal signal from 24h price change.

        Works in live/DRY mode without OHLCV indicators.
        Uses 24h change as a simple overbought/oversold proxy:
        - Up > 5% in 24h → overextended, potential pullback → SHORT
        - Down > 5% in 24h → oversold, potential bounce → LONG
        """
        pct_change = symbol_stats.get('pct_change_24h', 0)
        volume_24h = symbol_stats.get('vol_quote', 0)

        # Need significant move for reversal signal
        if abs(pct_change) < 3.0:
            return None

        # Volume check: need at least $5M 24h volume
        if volume_24h < 5_000_000:
            return None

        # Overbought → short (up too much, due for pullback)
        if pct_change > 3.0:
            side = "short"
            # Strength: linear 3%→10%, capped at 0.75
            strength = min((pct_change - 3.0) / 7.0, 0.75)
            # Wider stops for reversal (counter-trend trades need room)
            stop_loss_pct = 0.025  # 2.5% stop
            take_profit_pct = 0.030  # 3.0% target (tight — quick pullback)
            reason = f"Overbought reversal ({pct_change:.1f}% up in 24h)"
        # Oversold → long (down too much, due for bounce)
        elif pct_change < -3.0:
            side = "long"
            strength = min(abs(pct_change + 5.0) / 10.0, 0.75)
            stop_loss_pct = 0.025  # 2.5% stop
            take_profit_pct = 0.030  # 3.0% target
            reason = f"Oversold reversal ({pct_change:.1f}% down in 24h)"
        else:
            return None

        # STOP JITTER: randomize to avoid predictable stop-hunt levels
        import random
        from .config import STOP_JITTER_PCT
        stop_loss_pct *= 1.0 + random.uniform(-STOP_JITTER_PCT, STOP_JITTER_PCT)

        if side == "long":
            stop_loss = entry_price * (1 - stop_loss_pct)
            take_profit = entry_price * (1 + take_profit_pct)
        else:
            stop_loss = entry_price * (1 + stop_loss_pct)
            take_profit = entry_price * (1 - take_profit_pct)

        return TradingSignal(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strength=strength,
            signal_type="mean_reversion",
            reason=reason
        )

    def _generate_trend_signal(
        self,
        symbol: str,
        entry_price: float,
        symbol_stats: Dict,
        price_data: Optional[Dict],
        indicators: Optional[Dict]
    ) -> Optional[TradingSignal]:
        """Generate trend following signal (EMA-based)."""
        # Would use EMA crossovers if available
        # For now, use simplified trend based on price change
        
        pct_change = symbol_stats.get('pct_change_24h', 0)
        
        # Need clear trend - relaxed for testing
        if abs(pct_change) < 0.8:  # Less than 0.8% change - relaxed for testing (was 2.0%)
            return None
        
        # Calculate strength with relaxed normalization for testing
        # RELAXED: Require 4% change for full strength (was 8%)
        trend_strength = min(abs(pct_change) / 4.0, 1.0)
        
        if trend_strength < MIN_SIGNAL_STRENGTH:
            return None
        
        # Determine direction
        side = "long" if pct_change > 0 else "short"
        
        # Calculate stop loss and take profit (realistic with fees)
        from .config import TAKER_FEE_RATE, SLIPPAGE_BPS
        total_fee_rate = (TAKER_FEE_RATE * 2) + (SLIPPAGE_BPS / 10000)
        
        stop_loss_pct = 0.012  # 1.2% stop (tighter for trend following)
        take_profit_pct = 0.020  # 2.0% target (tightened for faster scalping exits)
        
        # Ensure minimum R:R of 1.5:1 after costs
        min_tp_pct = stop_loss_pct * 1.5 + total_fee_rate  # 1.5:1 R:R + fees
        take_profit_pct = max(take_profit_pct, min_tp_pct)
        
        if side == "long":
            stop_loss = entry_price * (1 - stop_loss_pct)
            take_profit = entry_price * (1 + take_profit_pct)
        else:
            stop_loss = entry_price * (1 + stop_loss_pct)
            take_profit = entry_price * (1 - take_profit_pct)
        
        return TradingSignal(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strength=trend_strength,
            signal_type="trend",
            reason=f"Trend {'up' if side == 'long' else 'down'} ({pct_change:.2f}%)"
        )

