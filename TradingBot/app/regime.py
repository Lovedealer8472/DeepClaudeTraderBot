"""
Trading Regime System - Manages different trading styles (Scalping, Day Trading, Swing Trading)
based on market conditions and LLM decisions.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple
from enum import Enum


class TradingRegime(Enum):
    """Trading regime types."""
    SCALPING = "scalping"
    DAY_TRADING = "day_trading"
    SWING_TRADING = "swing_trading"


@dataclass
class RegimeConfig:
    """Configuration for a trading regime."""
    # Regime identification
    name: str
    regime_type: TradingRegime
    
    # Signal generation parameters
    min_signal_strength: float  # Minimum signal strength (0-1)
    min_signal_score: int  # Minimum signal score (0-100)
    min_momentum_pct: float  # Minimum 24h momentum percentage for signals
    
    # Entry filters
    min_spread_bps: float  # Minimum acceptable spread (bps)
    max_spread_bps: float  # Maximum acceptable spread (bps)
    min_volume_24h: float  # Minimum 24h volume (USD)
    max_latency_ms: int  # Maximum acceptable latency (ms)
    
    # Position management
    max_concurrent_positions: int  # Maximum concurrent positions
    max_concurrent_positions_min: int  # Minimum limit
    max_concurrent_positions_max: int  # Maximum limit
    
    # Risk management (stop loss and take profit)
    stop_loss_atr_multiplier: float  # Stop loss as multiple of ATR
    take_profit_atr_multiplier: float  # Take profit as multiple of ATR
    min_stop_distance_pct: float  # Minimum stop distance (%)
    trailing_stop_activation_pct: float  # Activate trailing stop at profit %
    trailing_stop_pct: float  # Trail stop to % of profit
    
    # Exit conditions
    wide_spread_exit_threshold_bps: float  # Exit if spread exceeds (bps)
    early_stop_loss_pct: float  # Early exit at loss %
    
    # Timing
    scan_interval_seconds: float  # How often to scan for signals
    universe_refresh_interval_seconds: float  # How often to refresh universe
    
    # Position holding
    max_hold_time_seconds: Optional[float] = None  # Maximum time to hold position (None = no limit)
    min_hold_time_seconds: float = 0.0  # Minimum time to hold position
    
    # Description
    description: str = ""


# Default regime configurations
REGIME_CONFIGS: Dict[TradingRegime, RegimeConfig] = {
    TradingRegime.SCALPING: RegimeConfig(
        name="Scalping",
        regime_type=TradingRegime.SCALPING,
        description="Ultra-fast in-and-out trades, tight spreads, quick entries/exits",
        # Signal generation
        min_signal_strength=0.85,
        min_signal_score=65,  # Lowered from 70 due to boosted scoring (BASE_SCORE_MAX=70, 0.8 multiplier)
        min_momentum_pct=1.5,
        # Entry filters
        min_spread_bps=10.0,
        max_spread_bps=50.0,
        min_volume_24h=5_000_000,  # $5M
        max_latency_ms=50,
        # Position management
        max_concurrent_positions=8,
        max_concurrent_positions_min=3,
        max_concurrent_positions_max=12,
        # Risk management
        stop_loss_atr_multiplier=2.0,
        take_profit_atr_multiplier=1.2,  # Tight TP for fast capture — trail handles bigger moves
        min_stop_distance_pct=0.5,
        trailing_stop_activation_pct=0.4,  # Activate at 0.4% — protect winners fast, losers hit stop
        trailing_stop_pct=0.2,  # Trail 20% — lock in 80% of peak
        # Exit conditions
        wide_spread_exit_threshold_bps=50.0,
        early_stop_loss_pct=0.8,
        # Timing
        scan_interval_seconds=1.0,
        universe_refresh_interval_seconds=3.0,
        # Position holding
        max_hold_time_seconds=300.0,  # 5 minutes max
        min_hold_time_seconds=10.0,  # 10 seconds minimum
    ),
    
    TradingRegime.DAY_TRADING: RegimeConfig(
        name="Day Trading",
        regime_type=TradingRegime.DAY_TRADING,
        description="Hold positions for hours, medium-term moves, balanced approach",
        # Signal generation
        min_signal_strength=0.75,
        min_signal_score=60,  # Lowered from 65 due to boosted scoring (BASE_SCORE_MAX=70, 0.8 multiplier)
        min_momentum_pct=1.0,
        # Entry filters
        min_spread_bps=15.0,
        max_spread_bps=70.0,
        min_volume_24h=3_000_000,  # $3M
        max_latency_ms=200,
        # Position management
        max_concurrent_positions=6,
        max_concurrent_positions_min=2,
        max_concurrent_positions_max=8,
        # Risk management
        stop_loss_atr_multiplier=2.0,  # 2.4% stop (2.0x ATR)
        take_profit_atr_multiplier=2.0,  # 4.2% target (3.5x ATR)
        min_stop_distance_pct=1.0,
        trailing_stop_activation_pct=2.0,  # Trailing activates at 2% profit
        trailing_stop_pct=0.6,
        # Exit conditions
        wide_spread_exit_threshold_bps=70.0,
        early_stop_loss_pct=1.2,
        # Timing
        scan_interval_seconds=5.0,
        universe_refresh_interval_seconds=10.0,
        # Position holding
        max_hold_time_seconds=28800.0,  # 8 hours max (day trading)
        min_hold_time_seconds=300.0,  # 5 minutes minimum
    ),
    
    TradingRegime.SWING_TRADING: RegimeConfig(
        name="Swing Trading",
        regime_type=TradingRegime.SWING_TRADING,
        description="Hold positions for hours to days, larger moves, wider spreads acceptable",
        # Signal generation
        min_signal_strength=0.68,
        min_signal_score=55,  # Lowered from 60 due to boosted scoring (BASE_SCORE_MAX=70, 0.8 multiplier)
        min_momentum_pct=0.8,
        # Entry filters
        min_spread_bps=20.0,
        max_spread_bps=100.0,
        min_volume_24h=1_000_000,  # $1M
        max_latency_ms=500,
        # Position management
        max_concurrent_positions=4,
        max_concurrent_positions_min=1,
        max_concurrent_positions_max=6,
        # Risk management
        stop_loss_atr_multiplier=2.5,  # 3.0% stop (2.5x ATR)
        take_profit_atr_multiplier=5.0,  # 6.0% target (5.0x ATR)
        min_stop_distance_pct=1.5,
        trailing_stop_activation_pct=2.0,  # Trailing activates at 2% profit
        trailing_stop_pct=0.7,
        # Exit conditions
        wide_spread_exit_threshold_bps=100.0,
        early_stop_loss_pct=1.5,
        # Timing
        scan_interval_seconds=30.0,
        universe_refresh_interval_seconds=60.0,
        # Position holding
        max_hold_time_seconds=None,  # No max limit for swing trading
        min_hold_time_seconds=1800.0,  # 30 minutes minimum
    ),
}


class RegimeManager:
    """Manages trading regime selection and configuration."""
    
    def __init__(self):
        self.current_regime: TradingRegime = TradingRegime.DAY_TRADING  # Shifted from SCALPING — Jul 3
        self.regime_since: float = 0.0
        self.regime_history: list = []  # Track regime changes
        
    def get_current_config(self) -> RegimeConfig:
        """Get current regime configuration."""
        return REGIME_CONFIGS[self.current_regime]
    
    def set_regime(self, regime: TradingRegime, reason: str = ""):
        """Switch to a new trading regime."""
        if regime != self.current_regime:
            old_regime = self.current_regime
            self.current_regime = regime
            import time
            self.regime_since = time.time()
            self.regime_history.append({
                'timestamp': self.regime_since,
                'from': old_regime.value,
                'to': regime.value,
                'reason': reason
            })
            # Keep only last 20 regime changes
            if len(self.regime_history) > 20:
                self.regime_history.pop(0)
    
    def should_switch_regime(
        self,
        volatility_regime: str,
        spread_regime: str,
        btc_trend: float,
        win_rate: float,
        profit_factor: float,
        drawdown_pct: float,
        avg_hold_time: float,
        signal_quality: float
    ) -> Optional[Tuple[TradingRegime, str]]:
        """
        Determine if regime should be switched based on market conditions.
        
        Returns:
            (new_regime, reason) or None if no switch needed
        """
        current = self.current_regime
        
        # Market condition indicators
        low_volatility = volatility_regime == "Low"
        high_volatility = volatility_regime == "High"
        tight_spreads = spread_regime == "Tight"
        wide_spreads = spread_regime == "Wide"
        strong_btc_trend = abs(btc_trend) > 2.0
        weak_btc_trend = abs(btc_trend) < 0.5
        
        # Performance indicators
        strong_performance = win_rate > 0.65 and profit_factor > 1.5
        weak_performance = win_rate < 0.45 or profit_factor < 0.8
        large_drawdown = drawdown_pct < -5.0
        
        # Switch to SWING_TRADING if:
        # - Low volatility (not enough movement for scalping)
        # - Wide spreads (harder to scalp)
        # - Weak BTC trend (consolidation phase)
        # - Scalping not working (low win rate, poor performance)
        if current == TradingRegime.SCALPING:
            reasons = []
            if low_volatility:
                reasons.append("low volatility")
            if wide_spreads:
                reasons.append("wide spreads")
            if weak_btc_trend:
                reasons.append("weak BTC trend")
            if weak_performance and avg_hold_time > 120:  # Scalping not working
                reasons.append("poor scalping performance")
            
            if len(reasons) >= 2:  # Need at least 2 reasons to switch
                return TradingRegime.SWING_TRADING, f"Market conditions favor swing trading: {', '.join(reasons)}"
        
        # Switch to DAY_TRADING if:
        # - Medium volatility (between scalping and swing)
        # - Medium spreads
        # - Moderate BTC trend
        if current == TradingRegime.SCALPING:
            if not low_volatility and not high_volatility and not tight_spreads:
                if weak_performance:
                    return TradingRegime.DAY_TRADING, "Market conditions favor day trading: moderate volatility and spreads"
        
        # Switch back to SCALPING if:
        # - High volatility (good for scalping)
        # - Tight spreads (good for scalping)
        # - Strong BTC trend (momentum)
        # - Strong performance in current regime
        if current in [TradingRegime.DAY_TRADING, TradingRegime.SWING_TRADING]:
            reasons = []
            if high_volatility:
                reasons.append("high volatility")
            if tight_spreads:
                reasons.append("tight spreads")
            if strong_btc_trend:
                reasons.append("strong BTC trend")
            if strong_performance and signal_quality > 0.7:
                reasons.append("strong signal quality")
            
            if len(reasons) >= 2:
                return TradingRegime.SCALPING, f"Market conditions favor scalping: {', '.join(reasons)}"
        
        # Switch from SWING to DAY if:
        # - Volatility increasing
        # - Spreads tightening
        if current == TradingRegime.SWING_TRADING:
            if not low_volatility and spread_regime != "Wide":
                if strong_btc_trend:
                    return TradingRegime.DAY_TRADING, "Market conditions improving: switching to day trading"
        
        # Switch from DAY to SWING if:
        # - Volatility decreasing
        # - Spreads widening
        if current == TradingRegime.DAY_TRADING:
            if low_volatility and wide_spreads:
                return TradingRegime.SWING_TRADING, "Market conditions favor swing trading: low volatility and wide spreads"
        
        return None  # No switch needed

