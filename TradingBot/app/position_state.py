"""
Position State Machine — Explicit states and transitions for trade lifecycle.

Pattern copied from NautilusTrader: separate order lifecycle from position lifecycle.
Replaces ad-hoc PENDING/EXISTS/RECONCILED/ATOMIC strings with typed states.
Invalid transitions raise explicit errors instead of silently corrupting state.
"""

from enum import Enum, auto
from typing import Optional, Set
import time


class OrderState(Enum):
    """Order lifecycle — every order passes through these states."""
    INITIALIZED = "initialized"       # Created locally, not yet submitted
    SUBMITTING = "submitting"         # Being sent to exchange
    ACCEPTED = "accepted"             # Confirmed on exchange, has venue ID
    PARTIALLY_FILLED = "partially_filled"  # Partial execution
    FILLED = "filled"                 # Complete execution
    PENDING_CANCEL = "pending_cancel" # Cancel requested, awaiting confirmation
    CANCELED = "canceled"             # Cancelled on exchange
    REJECTED = "rejected"             # Exchange rejected the order
    EXPIRED = "expired"               # Order expired (time in force)
    FAILED = "failed"                 # Non-recoverable failure


class PositionState(Enum):
    """Position lifecycle — every position passes through these states."""
    FLAT = "flat"                           # No position
    ENTERING = "entering"                   # Entry order submitted, awaiting fill
    OPEN_UNPROTECTED = "open_unprotected"    # Position exists, no SL/TP yet
    OPEN_PROTECTED = "open_protected"        # Position exists, SL+TP confirmed
    TRAILING = "trailing"                   # SL/TP being actively trailed
    EXITING = "exiting"                     # Exit order submitted, awaiting fill
    CLOSED = "closed"                       # Position fully closed


# Valid transitions for PositionState
POSITION_TRANSITIONS: dict = {
    PositionState.FLAT:              {PositionState.ENTERING},
    PositionState.ENTERING:          {PositionState.OPEN_UNPROTECTED, PositionState.FLAT},  # fill or reject
    PositionState.OPEN_UNPROTECTED:  {PositionState.OPEN_PROTECTED, PositionState.EXITING, PositionState.CLOSED},
    PositionState.OPEN_PROTECTED:    {PositionState.TRAILING, PositionState.EXITING, PositionState.CLOSED},
    PositionState.TRAILING:          {PositionState.OPEN_PROTECTED, PositionState.EXITING, PositionState.CLOSED},
    PositionState.EXITING:           {PositionState.CLOSED, PositionState.FLAT},
    PositionState.CLOSED:            {PositionState.FLAT},  # cleanup resets to flat
}


class InvalidStateTransition(Exception):
    """Raised when a state transition is not allowed."""
    pass


class TrackedPosition:
    """
    A position with explicit state tracking.

    Replaces the loosely-typed dict approach:
        position['sl_order_id'] = 'PENDING'  → tracked_position.sl_state = OrderState.SUBMITTING
        position['tp_order_id'] = 'EXISTS'   → tracked_position.tp_state = OrderState.ACCEPTED

    All transitions are validated against the state machine.
    """
    __slots__ = (
        'symbol', 'side', 'size', 'entry_price', 'entry_time',
        'state', 'sl_state', 'tp_state', 'sl_order_id', 'tp_order_id',
        'stop_loss', 'initial_stop_price', 'take_profit',
        'signal_score', 'signal_strength', 'signal_type',
        'atr_pct', 'leverage', 'peak_price', 'trough_price', 'peak_pnl',
        'rescue_flag', 'rescue_start_time', 'is_unicorn',
        'funding_rate', 'initial_r', 'exit_profile', 'max_r_reached',
        'bars_in_trade', 'partial_exit_done', 'sl_moved_to_be',
        'last_bar_update_time', 'stage', 'survived_msx1',
        '_state_history', '_created_at', '_last_transition_at',
    )

    def __init__(self, symbol: str, side: str, size: float, entry_price: float,
                 stop_loss: float, take_profit: float, signal_score: float = 0.0):
        self.symbol = symbol
        self.side = side
        self.size = size
        self.entry_price = entry_price
        self.entry_time = time.time()
        self.stop_loss = stop_loss
        self.initial_stop_price = stop_loss
        self.take_profit = take_profit
        self.signal_score = signal_score

        # --- State tracking ---
        self.state = PositionState.ENTERING
        self.sl_state = OrderState.INITIALIZED
        self.tp_state = OrderState.INITIALIZED
        self.sl_order_id: Optional[str] = None
        self.tp_order_id: Optional[str] = None

        # --- Defaults ---
        self.signal_strength = 0.0
        self.signal_type = ""
        self.atr_pct: Optional[float] = None
        self.leverage = 1
        self.peak_price = entry_price
        self.trough_price = entry_price
        self.peak_pnl = 0.0
        self.rescue_flag = False
        self.rescue_start_time = 0.0
        self.is_unicorn = False
        self.funding_rate: Optional[float] = None
        self.initial_r = 0.0
        self.exit_profile = "standard"
        self.max_r_reached = 0.0
        self.bars_in_trade = 0
        self.partial_exit_done = False
        self.sl_moved_to_be = False
        self.last_bar_update_time = self.entry_time
        self.stage = 1
        self.survived_msx1 = False

        # --- Audit trail ---
        self._state_history: list = [(time.time(), PositionState.FLAT, PositionState.ENTERING)]
        self._created_at = time.time()
        self._last_transition_at = time.time()

    def transition_to(self, new_state: PositionState):
        """Validate and execute a state transition."""
        if new_state not in POSITION_TRANSITIONS.get(self.state, set()):
            raise InvalidStateTransition(
                f"Cannot transition from {self.state.value} to {new_state.value}. "
                f"Valid transitions: {[s.value for s in POSITION_TRANSITIONS.get(self.state, set())]}"
            )
        old_state = self.state
        self.state = new_state
        self._last_transition_at = time.time()
        self._state_history.append((time.time(), old_state, new_state))

    @property
    def is_protected(self) -> bool:
        """Position has confirmed SL and TP on exchange."""
        return self.sl_state in (OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED) and \
               self.tp_state in (OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED)

    @property
    def is_naked(self) -> bool:
        """Position exists on exchange without confirmed protection."""
        return self.state in (PositionState.OPEN_UNPROTECTED,) and not self.is_protected

    @property
    def sl_pending(self) -> bool:
        """SL order is still in-flight (not yet confirmed on exchange)."""
        return self.sl_state in (OrderState.INITIALIZED, OrderState.SUBMITTING)

    @property
    def tp_pending(self) -> bool:
        """TP order is still in-flight (not yet confirmed on exchange)."""
        return self.tp_state in (OrderState.INITIALIZED, OrderState.SUBMITTING)

    def sl_placed(self, order_id: str):
        """Mark SL as accepted on exchange."""
        self.sl_order_id = order_id
        self.sl_state = OrderState.ACCEPTED
        if self.tp_state == OrderState.ACCEPTED:
            self.transition_to(PositionState.OPEN_PROTECTED)

    def tp_placed(self, order_id: str):
        """Mark TP as accepted on exchange."""
        self.tp_order_id = order_id
        self.tp_state = OrderState.ACCEPTED
        if self.sl_state == OrderState.ACCEPTED:
            self.transition_to(PositionState.OPEN_PROTECTED)

    def sl_failed(self):
        """Mark SL placement as failed."""
        self.sl_state = OrderState.FAILED

    def tp_failed(self):
        """Mark TP placement as failed."""
        self.tp_state = OrderState.FAILED

    def start_trailing(self):
        """Transition to trailing state."""
        self.transition_to(PositionState.TRAILING)

    def start_exiting(self):
        """Transition to exiting state."""
        self.transition_to(PositionState.EXITING)

    def close(self):
        """Mark position as closed."""
        self.transition_to(PositionState.CLOSED)

    def mark_unprotected(self):
        """Position exists on exchange but protection not yet confirmed."""
        self.transition_to(PositionState.OPEN_UNPROTECTED)

    def to_dict(self) -> dict:
        """Convert to dict for backward compatibility with existing code."""
        return {
            'symbol': self.symbol,
            'side': self.side,
            'size': self.size,
            'entry_price': self.entry_price,
            'entry_time': self.entry_time,
            'stop_loss': self.stop_loss,
            'initial_stop_price': self.initial_stop_price,
            'take_profit': self.take_profit,
            'sl_order_id': self.sl_order_id or 'PENDING',
            'tp_order_id': self.tp_order_id or 'PENDING',
            'signal_score': self.signal_score,
            'signal_strength': self.signal_strength,
            'signal_type': self.signal_type,
            'atr_pct': self.atr_pct,
            'leverage': self.leverage,
            'peak_price': self.peak_price,
            'trough_price': self.trough_price,
            'peak_pnl': self.peak_pnl,
            'rescue_flag': self.rescue_flag,
            'rescue_start_time': self.rescue_start_time,
            'is_unicorn': self.is_unicorn,
            'funding_rate': self.funding_rate,
            'initial_r': self.initial_r,
            'exit_profile': self.exit_profile,
            'max_r_reached': self.max_r_reached,
            'bars_in_trade': self.bars_in_trade,
            'partial_exit_done': self.partial_exit_done,
            'sl_moved_to_be': self.sl_moved_to_be,
            'last_bar_update_time': self.last_bar_update_time,
            'stage': self.stage,
            'survived_msx1': self.survived_msx1,
            # State machine fields
            '_state': self.state.value,
            '_sl_state': self.sl_state.value,
            '_tp_state': self.tp_state.value,
        }
