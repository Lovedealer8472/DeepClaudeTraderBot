"""
Scalper Exit Logic - Tight, protective trailing exits for scalper positions.

Implements:
1. Initial SL at 0.35 * ATR
2. Micro trailing activation at +0.3 ATR
3. Main trailing at +0.6 ATR
4. Structural exit overrides
5. Time-based exits (3-4 candles max)
"""

import time
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass


@dataclass
class ScalperExitAction:
    """Action to take for a scalper position."""
    action: str  # "update_sl", "exit", or None
    new_stop: Optional[float] = None
    exit_reason: Optional[str] = None
    exit_size_ratio: float = 1.0  # 1.0 = full exit


def evaluate_scalper_trailing(
    position: Dict[str, Any],
    current_price: float,
    atr_pct: Optional[float],
    advanced_features: Optional[Any],
    indicators: Optional[Dict],
    side: str,
    entry_price: float,
    entry_time: float,
    bars_in_trade: int
) -> Optional[ScalperExitAction]:
    """
    Evaluate scalper-specific trailing exit logic.
    
    Args:
        position: Position dictionary
        current_price: Current market price
        atr_pct: ATR as percentage of price
        advanced_features: AdvancedFeatures object
        indicators: Indicators dict
        side: "long" or "short"
        entry_price: Entry price
        entry_time: Entry timestamp
        bars_in_trade: Number of candles/bars since entry
    
    Returns:
        ScalperExitAction if action needed, None otherwise
    """
    if not atr_pct or atr_pct <= 0:
        # Fallback to 1% if ATR not available
        atr_pct = 0.01
    
    # Calculate profit in ATR units
    if side == "long":
        profit_pct = ((current_price - entry_price) / entry_price)
        profit_atr = profit_pct / atr_pct
    else:  # short
        profit_pct = ((entry_price - current_price) / entry_price)
        profit_atr = profit_pct / atr_pct
    
    current_stop = position.get('stop_loss', 0)
    initial_stop = position.get('initial_stop_price', current_stop)
    if not initial_stop:
        # Set initial stop if not set
        if side == "long":
            initial_stop = entry_price * (1.0 - 0.35 * atr_pct)
        else:
            initial_stop = entry_price * (1.0 + 0.35 * atr_pct)
        position['initial_stop_price'] = initial_stop
        position['stop_loss'] = initial_stop
    
    # 1. Structural Exit Overrides (hard exits regardless of trailing)
    structural_exit = _check_structural_exit(
        side, advanced_features, current_price, entry_price, atr_pct
    )
    if structural_exit:
        return ScalperExitAction(
            action="exit",
            exit_reason=structural_exit,
            exit_size_ratio=1.0
        )
    
    # 2. Time-based exit (3-4 candles max for scalper)
    max_bars = 4
    if bars_in_trade >= max_bars:
        # Check if position has meaningful profit
        if profit_atr < 0.2:  # Less than 0.2 ATR profit after 4 bars
            return ScalperExitAction(
                action="exit",
                exit_reason=f"time_exit: {bars_in_trade} bars, profit={profit_atr:.2f}ATR",
                exit_size_ratio=1.0
            )
    
    # 3. Micro Trailing Activation (+0.3 ATR)
    if profit_atr >= 0.3:
        # Move SL to -0.1 ATR from entry (protect early gains)
        if side == "long":
            new_stop = entry_price * (1.0 - 0.1 * atr_pct)
            if new_stop > current_stop:  # Only move stop up
                return ScalperExitAction(
                    action="update_sl",
                    new_stop=new_stop,
                    exit_reason=None
                )
        else:  # short
            new_stop = entry_price * (1.0 + 0.1 * atr_pct)
            if new_stop < current_stop or current_stop == 0:  # Only move stop down
                return ScalperExitAction(
                    action="update_sl",
                    new_stop=new_stop,
                    exit_reason=None
                )
    
    # 4. Main Trailing (+0.6 ATR)
    if profit_atr >= 0.6:
        # Use trailing stop tied to current price: SL = current_price -/+ (0.35 * ATR)
        if side == "long":
            new_stop = current_price * (1.0 - 0.35 * atr_pct)
            if new_stop > current_stop:  # Only move stop up
                return ScalperExitAction(
                    action="update_sl",
                    new_stop=new_stop,
                    exit_reason=None
                )
        else:  # short
            new_stop = current_price * (1.0 + 0.35 * atr_pct)
            if new_stop < current_stop or current_stop == 0:  # Only move stop down
                return ScalperExitAction(
                    action="update_sl",
                    new_stop=new_stop,
                    exit_reason=None
                )
    
    # 5. Check if stop-loss hit
    if side == "long" and current_price <= current_stop:
        return ScalperExitAction(
            action="exit",
            exit_reason="stop_loss_hit",
            exit_size_ratio=1.0
        )
    elif side == "short" and current_price >= current_stop:
        return ScalperExitAction(
            action="exit",
            exit_reason="stop_loss_hit",
            exit_size_ratio=1.0
        )
    
    return None


def _check_structural_exit(
    side: str,
    advanced_features: Optional[Any],
    current_price: float,
    entry_price: float,
    atr_pct: float
) -> Optional[str]:
    """
    Check for structural exit conditions (opposite exhaustion, divergence, SFP, VWAP reversal).
    
    Returns:
        Exit reason string if structural exit needed, None otherwise
    """
    if not advanced_features:
        return None
    
    # Check opposite exhaustion
    if side == "long":
        if hasattr(advanced_features, 'exhaustion_up') and advanced_features.exhaustion_up:
            return "structural_exit: opposite_exhaustion"
    else:  # short
        if hasattr(advanced_features, 'exhaustion_down') and advanced_features.exhaustion_down:
            return "structural_exit: opposite_exhaustion"
    
    # Check opposite divergence
    if side == "long":
        if hasattr(advanced_features, 'bearish_divergence') and advanced_features.bearish_divergence:
            return "structural_exit: opposite_divergence"
    else:  # short
        if hasattr(advanced_features, 'bullish_divergence') and advanced_features.bullish_divergence:
            return "structural_exit: opposite_divergence"
    
    # Check opposite SFP
    if side == "long":
        if hasattr(advanced_features, 'sfp_top') and advanced_features.sfp_top:
            return "structural_exit: opposite_sfp"
    else:  # short
        if hasattr(advanced_features, 'sfp_bottom') and advanced_features.sfp_bottom:
            return "structural_exit: opposite_sfp"
    
    # Check VWAP reversal (price returns sharply through VWAP against position)
    if hasattr(advanced_features, 'dist_from_vwap'):
        dist_from_vwap = advanced_features.dist_from_vwap
        # If we've moved significantly and now reversed through VWAP
        move_pct = abs((current_price - entry_price) / entry_price)
        if move_pct > 0.01:  # At least 1% move
            if side == "long" and dist_from_vwap > 0.005:  # Price now above VWAP after long move
                return "structural_exit: vwap_reversal"
            elif side == "short" and dist_from_vwap < -0.005:  # Price now below VWAP after short move
                return "structural_exit: vwap_reversal"
    
    return None

