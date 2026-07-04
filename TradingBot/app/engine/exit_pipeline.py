"""
Unified Exit Pipeline - CANONICAL exit path for all position exits.
This is the ONLY way exits should happen in the system.

All exit types route through here:
- R-based exits (scalp/standard/runner)
- PRS full exits
- PRS scale-outs
- Timeout exits
- ATR-based trails
- Stop-loss/take-profit
- Stale position rules
- BERP exits
"""

import time
import logging
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass, field

from .. import config as cfg
from ..logger import get_logger
from ..order_manager import OrderManager
from ..exit_manager import ExitManager, ExitResult
from ..models import Position, Trade

logger = get_logger("ExitPipeline")


@dataclass
class ExitRequest:
    """
    Exit request structure.
    All exit requests must be validated before execution.
    """
    symbol: str
    position: Dict[str, Any]
    reason: str
    target_price: Optional[float] = None
    exit_size_ratio: float = 1.0  # 1.0 = full exit, 0.5 = 50% exit, etc.
    use_limit: bool = False
    priority: int = 0  # Higher priority exits first
    
    def __post_init__(self):
        """Validate exit request."""
        if not 0 < self.exit_size_ratio <= 1.0:
            raise ValueError(f"Invalid exit_size_ratio: {self.exit_size_ratio} (must be 0 < ratio <= 1.0)")


@dataclass
class TrailingConfig:
    start_buffer_r: float = cfg.TRAIL_ENGINE_START_BUFFER_R
    partial_1_r: float = cfg.TRAIL_ENGINE_PARTIAL_1_R
    partial_1_size: float = cfg.TRAIL_ENGINE_PARTIAL_1_SIZE
    partial_1_sl_offset_r: float = cfg.TRAIL_ENGINE_PARTIAL_1_SL_OFFSET_R
    break_even_r: float = cfg.TRAIL_ENGINE_BREAK_EVEN_R
    break_even_buffer_r: float = cfg.TRAIL_ENGINE_BE_BUFFER_R
    partial_2_r: float = cfg.TRAIL_ENGINE_PARTIAL_2_R
    partial_2_size: float = cfg.TRAIL_ENGINE_PARTIAL_2_SIZE
    lock_r_level: float = cfg.TRAIL_ENGINE_LOCK_R_LEVEL
    lock_amount_r: float = cfg.TRAIL_ENGINE_LOCK_AMOUNT_R
    runner_start_r: float = cfg.TRAIL_ENGINE_RUNNER_START_R
    runner_trail_distance_r: float = cfg.TRAIL_ENGINE_RUNNER_TRAIL_DISTANCE_R
    min_r_increment: float = cfg.TRAIL_ENGINE_MIN_R_INCREMENT
    min_update_seconds: float = cfg.TRAIL_ENGINE_MIN_UPDATE_SECONDS


@dataclass
class TrailingAction:
    stop_updated: bool = False
    new_stop: Optional[float] = None
    stop_reason: Optional[str] = None
    locked_r: Optional[float] = None
    partial_actions: List[Tuple[float, str]] = field(default_factory=list)


class TrailingStopEngine:
    """
    Dynamic R-based trailing stop engine.
    Determines when to move stops or request partial exits.
    """
    
    def __init__(self, config: TrailingConfig):
        self.config = config
        self.logger = get_logger("TrailingStopEngine")
    
    def evaluate(
        self,
        symbol: str,
        position: Dict[str, Any],
        current_price: float,
        now: Optional[float] = None
    ) -> Optional[TrailingAction]:
        if not cfg.USE_TRAILING_ENGINE:
            return None
        
        entry_price = position.get('entry_price')
        stop_loss = position.get('stop_loss')
        side = (position.get('side') or '').lower()
        
        if not entry_price or not stop_loss or side not in ('long', 'short') or current_price <= 0:
            return None
        
        now_ts = now or time.time()
        initial_stop = position.get('initial_stop_price', stop_loss)
        position.setdefault('initial_stop_price', initial_stop)
        
        risk_per_unit = abs(entry_price - initial_stop)
        if risk_per_unit <= 0:
            return None
        
        multiplier = 1 if side == 'long' else -1
        current_r = ((current_price - entry_price) * multiplier) / risk_per_unit
        position['current_r'] = current_r
        
        if side == 'long':
            peak_price = max(position.get('peak_price', entry_price), current_price)
            position['peak_price'] = peak_price
            peak_r = (peak_price - entry_price) / risk_per_unit
        else:
            trough_price = min(position.get('trough_price', entry_price), current_price)
            position['trough_price'] = trough_price
            peak_r = (entry_price - trough_price) / risk_per_unit
        
        position['max_r'] = max(position.get('max_r', 0.0) or 0.0, peak_r)
        position['max_r_reached'] = max(position.get('max_r_reached', 0.0) or 0.0, peak_r)
        
        if peak_r < self.config.start_buffer_r:
            return None
        
        state = position.setdefault('trailing_state', {
            'partial_1_taken': False,
            'partial_2_taken': False,
            'runner_mode': False,
            'last_update_r': 0.0,
            'last_update_ts': 0.0,
            'last_locked_r': 0.0
        })
        
        action = TrailingAction()
        current_stop = position.get('stop_loss', initial_stop)
        
        def clamp_stop(candidate: float) -> Optional[float]:
            buffer_pct = 0.0005  # ~5 bps
            if side == 'long':
                candidate = min(candidate, current_price * (1 - buffer_pct))
                if candidate <= current_stop + 1e-9:
                    return None
            else:
                candidate = max(candidate, current_price * (1 + buffer_pct))
                if candidate >= current_stop - 1e-9:
                    return None
            return candidate
        
        def update_stop(candidate: float, reason: str, locked_r_value: Optional[float] = None):
            nonlocal current_stop
            clamped = clamp_stop(candidate)
            if clamped is None:
                return
            action.new_stop = clamped
            action.stop_updated = True
            action.stop_reason = reason
            if locked_r_value is not None:
                action.locked_r = locked_r_value
            state['last_update_r'] = peak_r
            state['last_update_ts'] = now_ts
            position['stop_loss'] = clamped
            position['trailing_stop_price'] = clamped
            current_stop = clamped
            if reason in ("breakeven_lock", "locked_r", "runner_trail"):
                position['sl_moved_to_be'] = True
            # Logging handled at ExitPipeline level to avoid spam
        
        # TIME-BASED TRAIL TIGHTENING (research-backed)
        entry_time = position.get('entry_time', now_ts)
        bars_elapsed = position.get('bars_in_trade', 0)
        if bars_elapsed <= 0:
            hours_elapsed = (now_ts - entry_time) / 3600.0
            bars_elapsed = max(1, int(hours_elapsed))
            position['bars_in_trade'] = bars_elapsed
        trail_mult = 1.5 if bars_elapsed <= 2 else (1.0 if bars_elapsed <= 5 else 0.6)

        def should_progress() -> bool:
            # PEAK-CONFIRMED RATCHET: require meaningful extension before locking
            min_ext = max(0.15, 0.25 * trail_mult)
            if peak_r - state.get('last_update_r', 0.0) >= min_ext:
                return True
            if trail_mult <= 0.6 and peak_r > state.get('last_update_r', -999):
                return True
            return False
        
        # Partial at +1R
        if current_r >= self.config.partial_1_r and not state.get('partial_1_taken', False):
            state['partial_1_taken'] = True
            action.partial_actions.append((self.config.partial_1_size, "trailing_partial_1r"))
            position['partial_exit_done'] = True
            # Stop guard after partial (below entry for LONG, above for SHORT)
            desired = entry_price - (multiplier * self.config.partial_1_sl_offset_r * risk_per_unit)
            update_stop(desired, "partial_1_guard")
        
        # Break-even protection at 1.5R
        if peak_r >= self.config.break_even_r and state.get('last_locked_r', 0.0) < self.config.break_even_r and should_progress():
            desired = entry_price + (multiplier * self.config.break_even_buffer_r * risk_per_unit)
            update_stop(desired, "breakeven_lock", locked_r_value=self.config.break_even_buffer_r)
            state['last_locked_r'] = self.config.break_even_r
        
        # Partial at 2R / lock-in
        if peak_r >= self.config.partial_2_r and not state.get('partial_2_taken', False):
            state['partial_2_taken'] = True
            action.partial_actions.append((self.config.partial_2_size, "trailing_partial_2r"))
            position['partial_exit_done'] = True
        
        if peak_r >= self.config.lock_r_level and state.get('last_locked_r', 0.0) < self.config.lock_r_level and should_progress():
            lock_price = entry_price + (multiplier * self.config.lock_amount_r * risk_per_unit)
            update_stop(lock_price, "locked_r", locked_r_value=self.config.lock_amount_r)
            state['last_locked_r'] = self.config.lock_r_level
        
        # Runner mode
        if peak_r >= self.config.runner_start_r:
            state['runner_mode'] = True
        
        if state.get('runner_mode', False) and should_progress():
            if side == 'long':
                peak_reference = position.get('peak_price', current_price)
                trailing_distance = self.config.runner_trail_distance_r * risk_per_unit
                desired = peak_reference - trailing_distance
            else:
                trough_reference = position.get('trough_price', current_price)
                trailing_distance = self.config.runner_trail_distance_r * risk_per_unit
                desired = trough_reference + trailing_distance
            update_stop(desired, "runner_trail", locked_r_value=max(0.0, peak_r - self.config.runner_trail_distance_r))
        
        if action.stop_updated or action.partial_actions:
            return action
        
        return None
class ExitPipeline:
    """
    Unified exit pipeline - single entry point for all exits.
    
    Responsibilities:
    1. Validate exit request
    2. Compute new position size
    3. Apply partial/full reduction
    4. Update PnL
    5. Update portfolio state
    6. Generate ONE log event
    7. Communicate with UI (single-line update)
    
    NO duplicates, NO scattered logic.
    """
    
    def __init__(
        self,
        order_manager: OrderManager,
        exit_manager: ExitManager,
        position_registry: Any  # PositionRegistry from core
    ):
        """
        Initialize exit pipeline.
        
        Args:
            order_manager: Order execution manager
            exit_manager: Exit logic manager
            position_registry: Position registry for state updates
        """
        self.order_manager = order_manager
        self.exit_manager = exit_manager
        self.position_registry = position_registry
        self.logger = get_logger("ExitPipeline")
        
        # Exit queue (priority-based)
        self._exit_queue: List[ExitRequest] = []
        self._processing = False
        
        # Statistics
        self.exits_processed = 0
        self.exits_failed = 0
        self.exits_by_reason: Dict[str, int] = {}

        # Trailing stop engine
        self.trailing_engine = TrailingStopEngine(TrailingConfig()) if cfg.USE_TRAILING_ENGINE else None
    
    def queue_exit(self, request: ExitRequest) -> bool:
        """
        Queue an exit request for processing.
        
        Args:
            request: Exit request
            
        Returns:
            True if queued successfully, False if duplicate/invalid
        """
        # Check for duplicates (same symbol, same reason)
        for existing in self._exit_queue:
            if existing.symbol == request.symbol and existing.reason == request.reason:
                self.logger.debug(f"Duplicate exit request ignored: {request.symbol} {request.reason}")
                return False
        
        # Validate position exists
        if request.symbol not in self.position_registry.positions:
            self.logger.warning(f"Exit request for non-existent position: {request.symbol}")
            return False
        
        # Add to queue (sorted by priority)
        self._exit_queue.append(request)
        self._exit_queue.sort(key=lambda x: x.priority, reverse=True)
        
        return True
    
    async def process_exits(self, bot_instance: Any = None) -> int:
        """
        Process all queued exit requests.
        
        Args:
            bot_instance: Bot instance for PnL/statistics updates (optional)
        
        Returns:
            Number of exits processed
        """
        if self._processing or not self._exit_queue:
            return 0
        
        self._processing = True
        processed = 0
        
        try:
            # Process queue (highest priority first)
            # Track symbols being processed to prevent duplicate exits
            symbols_processed_this_batch = set()
            
            while self._exit_queue:
                request = self._exit_queue.pop(0)
                
                # Skip if we've already processed an exit for this symbol in this batch
                # (prevents duplicate exits when multiple requests are queued)
                if request.symbol in symbols_processed_this_batch:
                    self.logger.debug(
                        f"Skipping duplicate exit request: {request.symbol} reason={request.reason}"
                    )
                    continue
                
                try:
                    success = await self._execute_exit(request, bot_instance=bot_instance)
                    if success:
                        processed += 1
                        self.exits_processed += 1
                        self.exits_by_reason[request.reason] = self.exits_by_reason.get(request.reason, 0) + 1
                        # Mark symbol as processed (only if full exit)
                        if request.exit_size_ratio >= 1.0:
                            symbols_processed_this_batch.add(request.symbol)
                    # Note: Expected failures (position already closed) are logged as debug
                    # Only unexpected failures are counted and logged as errors
                    # The _execute_exit method handles this appropriately
                except Exception as e:
                    self.logger.error(
                        f"Exit execution failed: {request.symbol}",
                        error_type=type(e).__name__,
                        error_message=str(e),
                        reason=request.reason
                    )
                    self.exits_failed += 1
        finally:
            self._processing = False
        
        return processed
    
    async def _execute_exit(
        self, 
        request: ExitRequest,
        bot_instance: Any = None  # Bot instance for PnL updates
    ) -> bool:
        """
        Execute a single exit request.
        
        Args:
            request: Exit request
            bot_instance: Bot instance for PnL/statistics updates (optional)
            
        Returns:
            True if exit successful, False otherwise
        """
        symbol = request.symbol
        position = request.position
        
        # Validate position state - always use current position from registry
        if symbol not in self.position_registry.positions:
            # Position already closed - this is expected when multiple exits are queued
            # Don't log as warning, just debug
            self.logger.debug(f"Exit skipped: position already closed: {symbol}")
            return False
        
        current_position = self.position_registry.positions[symbol]
        current_size = current_position.get('size', 0)
        
        if current_size <= 0:
            # Position size invalid - likely already closed
            # Don't log as warning, just debug
            self.logger.debug(f"Exit skipped: position size invalid: {symbol} size={current_size}")
            return False
        
        # Calculate exit size
        exit_size = current_size * request.exit_size_ratio
        new_size = current_size - exit_size
        
        # Validate exit size
        if exit_size <= 0:
            self.logger.warning(f"Invalid exit size: {symbol} exit_size={exit_size}")
            return False
        
        # Create temporary position dict for exit calculation
        exit_position_dict = current_position.copy()
        exit_position_dict['size'] = exit_size
        
        # Determine order type
        # Trailing stops always use market orders to avoid price validation issues
        use_limit = (request.use_limit or (request.reason in [
            "take_profit", "scalp_tp_1r", "standard_partial_1r", "runner_partial_1r"
        ])) and "trailing" not in request.reason.lower()
        
        # Execute exit via exit_manager (canonical path)
        exit_result = await self.exit_manager.exit_position(
            symbol=symbol,
            position=exit_position_dict,
            reason=request.reason,
            target_price=request.target_price,
            use_limit=use_limit
        )
        
        if not exit_result.success:
            error_msg = exit_result.error or "Unknown error"
            
            # Check if this is an expected error (position already closed/invalid)
            expected_errors = [
                "Invalid position size (zero or negative)",
                "Invalid entry price (zero or negative)",
                "Position already closed",
                "ReduceOnly Order is rejected",  # -2022: position already closed by exchange
                "invalid_limit_price",
                "invalid_price"
            ]
            # Errors that mean the position is definitively gone — clean up tracking
            position_gone_errors = [
                "Position already closed",
                "ReduceOnly Order is rejected",
            ]
            
            # Special handling for trailing stop exits - fallback to market if limit fails
            is_trailing_exit = "trailing" in request.reason.lower()
            
            if is_trailing_exit and ("invalid_price" in error_msg or "invalid_limit_price" in error_msg):
                # For trailing stops, if price validation fails, try market order
                # Mark position to prevent retry spam
                if symbol in self.position_registry.positions:
                    position = self.position_registry.positions[symbol]
                    position['trailing_stop_exit_triggered'] = True
                    # If position size is tiny, just close it
                    if position.get('size', 0) <= 0.001:
                        self.logger.debug(f"EXIT_SKIPPED {symbol} trailing_stop (tiny size)")
                        # Remove position to prevent retries
                        self.position_registry.remove(symbol)
                        return False
                
                # Try market order instead
                self.logger.debug(
                    f"Trailing exit {symbol} falling back to market order (price validation failed)"
                )
                # Re-queue as market order
                request.use_limit = False
                request.target_price = None
                # Don't retry immediately - let it be processed in next cycle
                return False
            
            is_expected = any(expected in error_msg for expected in expected_errors)
            position_gone = any(gone in error_msg for gone in position_gone_errors)

            if position_gone:
                # "Position already closed" or "ReduceOnly Order is rejected"
                # — but this can be a FALSE POSITIVE (-2022 can fire for other reasons).
                # VERIFY against the exchange before cleaning up tracking.
                verified_gone = False
                try:
                    import asyncio
                    # Get exchange from bot_instance — never use bare 'exchange'
                    _ex = bot_instance.exchange_wrapper if bot_instance else None
                    if _ex is None:
                        self.logger.error(f"[GHOST_EXIT] {symbol} no exchange — assuming position gone")
                        verified_gone = True
                    else:
                        await asyncio.sleep(0.5)
                        pos_check = await _ex.fetch_positions([symbol])
                    still_open = any(
                        float(p.get('contracts', 0)) > 0
                        for p in (pos_check if isinstance(pos_check, list) else [pos_check])
                    )
                    verified_gone = not still_open
                    if still_open:
                        self.logger.warning(
                            f"[GHOST_EXIT] {symbol} -2022 was FALSE POSITIVE! Position still open on exchange. "
                            f"Will retry exit differently."
                        )
                        # DON'T remove tracking — position is still alive
                        # Try a direct market close as fallback
                        try:
                            pos_details = self.position_registry.positions.get(symbol, {})
                            close_side = 'sell' if pos_details.get('side', 'long') == 'long' else 'buy'
                            qty = abs(float(pos_details.get('size', 0)))
                            if qty > 0:
                                await _ex.create_order(
                                    symbol, 'market', close_side, qty, None,
                                    {'reduceOnly': 'true'}
                                )
                                # Check again after market close
                                await asyncio.sleep(1)
                                pos_check2 = await _ex.fetch_positions([symbol])
                                still_open2 = any(
                                    float(p.get('contracts', 0)) > 0
                                    for p in (pos_check2 if isinstance(pos_check2, list) else [pos_check2])
                                )
                                if not still_open2:
                                    verified_gone = True
                                    self.logger.info(f"[GHOST_EXIT] {symbol} closed via market fallback")
                        except Exception as fallback_err:
                            self.logger.error(f"[GHOST_EXIT] {symbol} fallback close failed: {fallback_err}")
                except Exception as verify_err:
                    self.logger.error(f"[GHOST_EXIT] {symbol} verification failed: {verify_err} — assuming still open")

                if not verified_gone:
                    # Position still exists — DO NOT clean up. Let next cycle handle it.
                    # But mark it so we don't infinite loop on the same -2022
                    if symbol in self.position_registry.positions:
                        self.position_registry.positions[symbol]['ghost_exit_count'] = \
                            self.position_registry.positions[symbol].get('ghost_exit_count', 0) + 1
                    return False

                # VERIFIED: Position is really gone
                self.logger.info(
                    f"Exit confirmed (exchange): {symbol} reason={request.reason} "
                    f"— position already closed"
                )
                # Remove from position registry so we don't retry
                self.position_registry.remove(symbol)
                # Record PnL from the position data we have
                if bot_instance and symbol in bot_instance.positions:
                    pos = bot_instance.positions[symbol]
                    entry_px = pos.get('entry_price', 0)
                    exit_px = pos.get('current_price', entry_px)
                    side = pos.get('side', 'long')
                    size = pos.get('size', 0)
                    pnl = ((exit_px - entry_px) * size) if side == 'long' else ((entry_px - exit_px) * size)
                    bot_instance.realized_pnl_total += pnl
                    if pnl > 0:
                        bot_instance.win_count += 1
                        bot_instance.gross_win += pnl
                    else:
                        bot_instance.loss_count += 1
                        bot_instance.gross_loss += abs(pnl)
                    bot_instance.logger.info(
                        f"EXIT: {symbol} | exchange_close | size={size} | "
                        f"price={exit_px:.2f} | pnl={pnl:.2f}"
                    )
                return True  # Treat as success — position is gone

            if is_expected:
                # Expected error - log as debug only, don't count as failure
                self.logger.debug(
                    f"Exit skipped (expected): {symbol} reason={request.reason} error={error_msg}"
                )
            else:
                # Unexpected error - log as error and count as failure
                self.logger.error(
                    f"Exit failed: {symbol}",
                    reason=request.reason,
                    error=error_msg
                )
                # Track unexpected failures (will be counted in process_exits)
                self.exits_failed += 1
            return False
        
        # Update bot statistics if bot_instance provided
        if bot_instance:
            was_win = exit_result.net_pnl > 0 if exit_result.net_pnl else False
            
            # Update statistics
            if was_win:
                bot_instance.win_count += 1
                bot_instance.gross_win += exit_result.gross_pnl or 0
            else:
                bot_instance.loss_count += 1
                bot_instance.gross_loss += abs(exit_result.gross_pnl or 0)
            
            # Update PnL totals
            bot_instance.realized_pnl_total += exit_result.net_pnl or 0
            bot_instance.realized_fees_total += exit_result.total_costs or 0
            
            # Track fee breakdown
            if exit_result.entry_fee is not None:
                bot_instance.realized_entry_fees_total += exit_result.entry_fee
            if exit_result.exit_fee is not None:
                bot_instance.realized_exit_fees_total += exit_result.exit_fee
            if exit_result.slippage is not None:
                bot_instance.realized_slippage_total += exit_result.slippage
            if exit_result.funding_cost is not None:
                bot_instance.realized_funding_total += exit_result.funding_cost
        
        # Update position state using canonical apply_exit_and_log
        from ..position_utils import apply_exit_and_log
        
        entry_price = current_position.get('entry_price', 0)
        entry_time = current_position.get('entry_time', time.time())
        exit_time = time.time()
        position_side = current_position.get('side', '')
        
        # Calculate PnL percentage
        pnl_pct = 0.0
        if entry_price > 0 and exit_result.exit_price and exit_result.exit_price > 0:
            if position_side.lower() == 'long':
                pnl_pct = ((exit_result.exit_price - entry_price) / entry_price) * 100
            else:
                pnl_pct = ((entry_price - exit_result.exit_price) / entry_price) * 100
        
        # Determine action type
        if request.exit_size_ratio < 1.0:
            action = "PARTIAL_EXIT" if "prs_" in request.reason or "scale" in request.reason.lower() else "SCALE_OUT"
        else:
            action = "EXIT"
        
        size_before = current_size
        size_after = new_size if request.exit_size_ratio < 1.0 else 0.0
        prs = current_position.get('recovery_score')
        
        # CANONICAL: Apply exit and log atomically
        success, event_created = apply_exit_and_log(
            positions=self.position_registry._positions,
            positions_set=self.position_registry._positions_set,
            symbol=symbol,
            new_size=new_size,
            action=action,
            exit_price=exit_result.exit_price,
            entry_price=entry_price,
            entry_time=entry_time,
            exit_time=exit_time,
            exit_size=exit_result.exit_size,
            size_before=size_before,
            size_after=size_after,
            side=position_side,
            pnl_value=exit_result.net_pnl,
            pnl_pct=pnl_pct,
            gross_pnl=exit_result.gross_pnl,
            net_pnl=exit_result.net_pnl,
            total_costs=exit_result.total_costs,
            reason=request.reason,
            prs=prs,
            was_win=was_win if bot_instance else None,
            is_unicorn=current_position.get('is_unicorn', False),
            bot_instance=bot_instance
        )
        
        if not success:
            self.logger.warning(f"Position update failed: {symbol}")
            return False
        
        # Record exit in position manager (only for full exits)
        if bot_instance and request.exit_size_ratio >= 1.0:
            equity = bot_instance.equity_now() if hasattr(bot_instance, 'equity_now') else 0
            pnl_pct_for_manager = (exit_result.net_pnl / equity * 100) if exit_result.net_pnl and equity > 0 else None
            # Calculate profit_atr for churn tracking (if time_exit)
            profit_atr = None
            if request.reason and request.reason.startswith("time_exit"):
                # Try to extract from reason string: "time_exit: 4 bars, profit=0.00ATR"
                import re
                match = re.search(r'profit=([\d\.-]+)ATR', request.reason)
                if match:
                    try:
                        profit_atr = float(match.group(1))
                    except (ValueError, AttributeError):
                        pass
                # Fallback: calculate from position data if available
                if profit_atr is None and entry_price > 0 and exit_result.exit_price > 0:
                    atr_pct = current_position.get('atr_pct', None)
                    if atr_pct and atr_pct > 0:
                        if position_side.lower() == 'long':
                            profit_pct = ((exit_result.exit_price - entry_price) / entry_price)
                        else:
                            profit_pct = ((entry_price - exit_result.exit_price) / entry_price)
                        profit_atr = profit_pct / atr_pct
            bot_instance.position_manager.record_exit(symbol, was_win, pnl_pct=pnl_pct_for_manager, exit_reason=request.reason, profit_atr=profit_atr)
        
        # Update metrics
        if bot_instance and hasattr(bot_instance, 'metrics'):
            try:
                bot_instance.metrics['exits_by_reason'][request.reason] = \
                    bot_instance.metrics['exits_by_reason'].get(request.reason, 0) + 1
            except Exception:
                pass
        
        # Log exit (ONE log event per exit - compact format)
        self.logger.info(
            f"EXIT: {symbol} | {request.reason} | "
            f"size={exit_size:.4f} | price={exit_result.exit_price:.2f} | "
            f"pnl={exit_result.net_pnl:.2f}"
        )
        
        return True
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get exit pipeline statistics.
        
        Returns:
            Statistics dictionary
        """
        return {
            'exits_processed': self.exits_processed,
            'exits_failed': self.exits_failed,
            'exits_by_reason': self.exits_by_reason.copy(),
            'queue_length': len(self._exit_queue)
        }

    def evaluate_trailing(
        self,
        symbol: str,
        position: Dict[str, Any],
        current_price: float,
        now: Optional[float] = None
    ) -> Optional[TrailingAction]:
        """
        Evaluate trailing stop logic for a position.
        
        Args:
            symbol: Trading symbol
            position: Position dictionary
            current_price: Latest mark price
            now: Optional timestamp
        
        Returns:
            TrailingAction if any updates/partials were triggered, or if trailing stop was hit
        """
        if not self.trailing_engine:
            return None
        
        # Check if trailing stop exit is already queued/processing for this position
        # This prevents duplicate trailing stop hit detections
        for queued_request in self._exit_queue:
            if queued_request.symbol == symbol and "trailing_stop" in queued_request.reason:
                # Already queued - skip detection
                return None
        
        side = (position.get('side') or '').lower()
        current_stop = position.get('stop_loss')
        
        # Check if we've already triggered a trailing stop exit for this position
        # (prevents repeated detections in subsequent loops)
        if position.get('trailing_stop_exit_triggered', False):
            return None
        
        # First, check if current stop has been hit (before any updates)
        if current_stop and current_stop > 0:
            stop_hit = False
            if side == 'long' and current_price <= current_stop:
                stop_hit = True
            elif side == 'short' and current_price >= current_stop:
                stop_hit = True
            
            if stop_hit:
                # Stop was hit - create action to trigger exit
                # Mark position to prevent duplicate detections
                position['trailing_stop_exit_triggered'] = True
                action = TrailingAction()
                action.partial_actions.append((1.0, "trailing_stop_hit"))
                self.logger.info(
                    f"[TRAIL] {symbol} TRAILING STOP HIT | "
                    f"price={current_price:.4f} stop={current_stop:.4f}"
                )
                return action
        
        # Stop not hit, evaluate trailing logic (may update stop, return partial actions)
        action = self.trailing_engine.evaluate(symbol, position, current_price, now=now)
        
        # After trailing evaluation, check if updated stop was hit
        if action and action.stop_updated:
            updated_stop = position.get('stop_loss')
            
            if updated_stop and updated_stop > 0:
                # Check if price crossed the newly updated stop
                stop_hit = False
                if side == 'long' and current_price <= updated_stop:
                    stop_hit = True
                elif side == 'short' and current_price >= updated_stop:
                    stop_hit = True
                
                if stop_hit:
                    # Newly updated stop was immediately hit - trigger full exit
                    # Mark position to prevent duplicate detections
                    position['trailing_stop_exit_triggered'] = True
                    action.partial_actions.append((1.0, "trailing_stop_hit"))
                    self.logger.info(
                        f"[TRAIL] {symbol} TRAILING STOP HIT (after update) | "
                        f"price={current_price:.4f} stop={updated_stop:.4f} "
                        f"reason={action.stop_reason}"
                    )
                else:
                    # Stop was updated but not hit - log the update
                    locked_r = action.locked_r or 0.0
                    self.logger.info(
                        f"[TRAIL] {symbol} TRAIL_SL_UPDATE | "
                        f"side={side.upper()} new_SL={updated_stop:.4f} "
                        f"locked_R={locked_r:.2f} reason={action.stop_reason}"
                    )
        
        # Persist stop updates to position registry
        if action and action.stop_updated:
            self.position_registry.update(symbol, position)
        
        return action

