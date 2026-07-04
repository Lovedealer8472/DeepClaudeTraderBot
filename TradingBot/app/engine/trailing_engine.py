"""
Dynamic R-Based Trailing Stop Engine

Implements intelligent trailing stop logic based on R multiples and peak tracking.
Banks profits early while allowing winners to run.
"""

import time
from typing import Optional, Dict, Any
from dataclasses import dataclass

from .. import config as cfg
from ..logger import get_logger

logger = get_logger("TrailingStopEngine")


@dataclass
class TrailingResult:
    """Result from trailing stop evaluation."""
    action: str  # "update_sl", "exit", or None
    new_stop: Optional[float] = None
    exit_pct: Optional[float] = None
    reason: Optional[str] = None
    price: Optional[float] = None
    current_r: Optional[float] = None
    peak_r: Optional[float] = None


class TrailingStopEngine:  # NOTE: duplicate of exit_pipeline.py:77 — controlled by USE_NEW_TRAILING_ENGINE
    """
    Dynamic R-based trailing stop engine.
    
    Implements zone-based trailing logic:
    - Zone 0 (<0.5R): No trailing
    - Zone 1 (0.5-1R): Light tighten, no break-even
    - Zone 2 (1R+): Partial exit + move SL toward BE
    - Zone 3 (1.5-3R): Controlled trail in R increments
    - Zone 4 (>3R): Runner mode with ATR or fixed-R trail
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize trailing stop engine with configuration."""
        self.config = config or {}
        self.logger = get_logger("TrailingStopEngine")
        
        # Load config values with defaults
        self.start_buffer_r = self.config.get('start_buffer_r', cfg.TRAIL_ENGINE_START_BUFFER_R)
        self.partial_1_r = self.config.get('partial_1_r', cfg.TRAIL_ENGINE_PARTIAL_1_R)
        self.partial_1_size = self.config.get('partial_1_size', cfg.TRAIL_ENGINE_PARTIAL_1_SIZE)
        self.partial_1_sl_offset_r = self.config.get('partial_1_sl_offset_r', cfg.TRAIL_ENGINE_PARTIAL_1_SL_OFFSET_R)
        self.break_even_r = self.config.get('break_even_r', cfg.TRAIL_ENGINE_BREAK_EVEN_R)
        self.be_buffer_r = self.config.get('be_buffer_r', cfg.TRAIL_ENGINE_BE_BUFFER_R)
        self.partial_2_r = self.config.get('partial_2_r', cfg.TRAIL_ENGINE_PARTIAL_2_R)
        self.partial_2_size = self.config.get('partial_2_size', cfg.TRAIL_ENGINE_PARTIAL_2_SIZE)
        self.lock_r_level = self.config.get('lock_r_level', cfg.TRAIL_ENGINE_LOCK_R_LEVEL)
        self.lock_amount_r = self.config.get('lock_amount_r', cfg.TRAIL_ENGINE_LOCK_AMOUNT_R)
        self.runner_start_r = self.config.get('runner_start_r', cfg.TRAIL_ENGINE_RUNNER_START_R)
        self.runner_trail_distance_r = self.config.get('runner_trail_distance_r', cfg.TRAIL_ENGINE_RUNNER_TRAIL_DISTANCE_R)
        self.min_r_increment = self.config.get('min_r_increment', cfg.TRAIL_ENGINE_MIN_R_INCREMENT)
        self.min_update_seconds = self.config.get('min_update_seconds', cfg.TRAIL_ENGINE_MIN_UPDATE_SECONDS)
    
    def update(
        self,
        position: Dict[str, Any],
        market: Dict[str, Any]
    ) -> Optional[TrailingResult]:
        """
        Update trailing stop logic for a position.
        
        Args:
            position: Position dictionary from PositionRegistry
            market: Market data dict with:
                - current_price: float
                - atr: Optional[float] (ATR value or None)
                - side: str ("long" or "short")
        
        Returns:
            TrailingResult if action needed, None otherwise
        """
        if not cfg.USE_NEW_TRAILING_ENGINE:
            return None
        
        current_price = market.get('current_price')
        atr = market.get('atr')
        side = market.get('side', position.get('side', '')).lower()
        
        entry_price = position.get('entry_price')
        stop_loss = position.get('stop_loss')
        
        if not entry_price or not stop_loss or side not in ('long', 'short') or not current_price or current_price <= 0:
            return None
        
        now_ts = time.time()
        initial_stop = position.get('initial_stop_price', stop_loss)
        position.setdefault('initial_stop_price', initial_stop)
        
        risk_per_unit = abs(entry_price - initial_stop)
        if risk_per_unit <= 0:
            return None
        
        multiplier = 1 if side == 'long' else -1
        
        # Track peak favorable price
        peak_favorable_price = position.get('peak_favorable_price')
        if peak_favorable_price is None:
            peak_favorable_price = entry_price
            position['peak_favorable_price'] = peak_favorable_price
        
        if side == 'long':
            peak_favorable_price = max(peak_favorable_price, current_price)
        else:
            peak_favorable_price = min(peak_favorable_price, current_price)
        
        position['peak_favorable_price'] = peak_favorable_price
        
        # Compute current R and peak R
        current_r = ((current_price - entry_price) * multiplier) / risk_per_unit
        peak_r = ((peak_favorable_price - entry_price) * multiplier) / risk_per_unit
        
        position['current_r'] = current_r
        position['peak_r'] = peak_r
        position['max_r'] = max(position.get('max_r', 0.0) or 0.0, peak_r)
        
        # Zone 0: <0.5R - no trailing
        if peak_r < self.start_buffer_r:
            return None
        
        # Initialize trailing state
        trailing_state = position.setdefault('trailing_state', {
            'partial_1_taken': False,
            'partial_2_taken': False,
            'runner_mode': False,
            'last_update_r': 0.0,
            'last_update_ts': 0.0,
            'last_locked_r': 0.0
        })
        
        current_stop = position.get('stop_loss', initial_stop)
        result = TrailingResult(action=None)
        result.current_r = current_r
        result.peak_r = peak_r
        
        def clamp_stop(candidate: float) -> Optional[float]:
            """Clamp stop to valid range and ensure it only moves in favorable direction."""
            buffer_pct = 0.0005  # ~5 bps minimum distance from price
            if side == 'long':
                # LONG: stop can only move up
                candidate = min(candidate, current_price * (1 - buffer_pct))
                if candidate <= current_stop + 1e-9:
                    return None  # No improvement
            else:
                # SHORT: stop can only move down
                candidate = max(candidate, current_price * (1 + buffer_pct))
                if candidate >= current_stop - 1e-9:
                    return None  # No improvement
            return candidate
        
        def update_stop(candidate: float, reason: str, locked_r_value: Optional[float] = None):
            """Update stop loss if valid."""
            nonlocal current_stop
            clamped = clamp_stop(candidate)
            if clamped is None:
                return False
            result.action = "update_sl"
            result.new_stop = clamped
            result.reason = reason
            trailing_state['last_update_r'] = peak_r
            trailing_state['last_update_ts'] = now_ts
            position['stop_loss'] = clamped
            position['last_trailing_update_ts'] = now_ts
            current_stop = clamped  # Keep closure in sync — prevents backward movement
            if locked_r_value is not None:
                position['locked_r'] = locked_r_value
            return True
        
        def should_progress() -> bool:
            """Check if enough R increment or time has passed to update."""
            if peak_r - trailing_state.get('last_update_r', 0.0) >= self.min_r_increment:
                return True
            if now_ts - trailing_state.get('last_update_ts', 0.0) >= self.min_update_seconds:
                return True
            return False
        
        # Zone 1: 0.5-1R - light tighten, no break-even
        if 0.5 <= peak_r < 1.0 and should_progress():
            # Move stop slightly closer but stay below entry for LONG (above for SHORT)
            desired = entry_price - (multiplier * 0.3 * risk_per_unit)  # 0.3R from entry
            update_stop(desired, "zone_1_tighten")
        
        # Zone 2: >=1R - partial exit + move SL toward BE
        if current_r >= self.partial_1_r and not trailing_state.get('partial_1_taken', False):
            trailing_state['partial_1_taken'] = True
            result.action = "exit"
            result.exit_pct = self.partial_1_size
            result.reason = "trailing_partial_1r"
            result.price = current_price
            # Move stop closer but still below entry for LONG (above for SHORT)
            desired = entry_price - (multiplier * self.partial_1_sl_offset_r * risk_per_unit)
            update_stop(desired, "partial_1_guard")
            # Return partial exit (stop update will be applied in position)
            return result
        
        # Break-even protection at 1.5R
        if peak_r >= self.break_even_r and trailing_state.get('last_locked_r', 0.0) < self.break_even_r and should_progress():
            desired = entry_price + (multiplier * self.be_buffer_r * risk_per_unit)
            if update_stop(desired, "breakeven_lock", locked_r_value=self.be_buffer_r):
                trailing_state['last_locked_r'] = self.break_even_r
        
        # Zone 3: 1.5-3R - controlled trail in R increments
        if 1.5 <= peak_r < self.runner_start_r and should_progress():
            # Tighten trailing in 0.5R increments
            locked_r = trailing_state.get('last_locked_r', 0.0)
            if peak_r >= locked_r + 0.5:
                desired = entry_price + (multiplier * (locked_r + 0.5) * risk_per_unit)
                update_stop(desired, "zone_3_trail", locked_r_value=locked_r + 0.5)
        
        # Partial at 2R
        if peak_r >= self.partial_2_r and not trailing_state.get('partial_2_taken', False):
            trailing_state['partial_2_taken'] = True
            if result.action != "exit":  # Don't override if already exiting
                result.action = "exit"
                result.exit_pct = self.partial_2_size
                result.reason = "trailing_partial_2r"
                result.price = current_price
        
        # Lock-in at 2R
        if peak_r >= self.lock_r_level and trailing_state.get('last_locked_r', 0.0) < self.lock_r_level and should_progress():
            lock_price = entry_price + (multiplier * self.lock_amount_r * risk_per_unit)
            if update_stop(lock_price, "locked_r", locked_r_value=self.lock_amount_r):
                trailing_state['last_locked_r'] = self.lock_r_level
        
        # Zone 4: >3R - runner mode
        if peak_r >= self.runner_start_r:
            trailing_state['runner_mode'] = True
        
        if trailing_state.get('runner_mode', False) and should_progress():
            if atr and atr > 0:
                # Use ATR-based trailing
                atr_mult = 2.0  # 2x ATR trailing distance
                trailing_distance = atr_mult * atr
            else:
                # Fallback to fixed R-based trailing
                trailing_distance = self.runner_trail_distance_r * risk_per_unit
            
            if side == 'long':
                desired = peak_favorable_price - trailing_distance
            else:
                desired = peak_favorable_price + trailing_distance
            
            locked_r_value = max(0.0, peak_r - self.runner_trail_distance_r)
            update_stop(desired, "runner_trail", locked_r_value=locked_r_value)
        
        # Check if trailing stop was hit
        if result.action != "exit":  # Don't override partial exits
            if side == 'long' and current_price <= current_stop:
                result.action = "exit"
                result.exit_pct = 1.0
                result.reason = "trailing_stop_hit"
                result.price = current_price
            elif side == 'short' and current_price >= current_stop:
                result.action = "exit"
                result.exit_pct = 1.0
                result.reason = "trailing_stop_hit"
                result.price = current_price
        
        return result if result.action else None

