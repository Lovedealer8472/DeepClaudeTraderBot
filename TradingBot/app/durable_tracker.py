"""
Durable In-Flight Order Tracker — Hummingbot's pattern for crash recovery.

Persists every non-terminal order to disk as JSON. On restart, restores and
polls lost orders until they reach a venue-confirmed terminal state.

Three buckets (Hummingbot pattern):
  - Active: orders being tracked normally
  - Cached: orders awaiting confirmation
  - Lost: orders being polled for resolution (bot died mid-operation)
"""

import json
import os
import time
import asyncio
from pathlib import Path
from typing import Dict, Optional, List
from dataclasses import dataclass, field


@dataclass
class InFlightOrder:
    """A single in-flight order that needs tracking until terminal state."""
    symbol: str
    order_type: str          # STOP_MARKET, TAKE_PROFIT_MARKET, MARKET, LIMIT
    side: str                # buy, sell
    purpose: str             # sl, tp, entry, exit
    exchange_order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    quantity: float = 0.0
    price: Optional[float] = None
    stop_price: Optional[float] = None
    status: str = "PENDING"  # PENDING, CONFIRMED, LOST, RESOLVED
    created_at: float = field(default_factory=time.time)
    last_checked_at: float = 0.0
    check_count: int = 0
    terminal: bool = False   # True when order reached final state
    resolution: Optional[str] = None  # How it resolved: filled, cancelled, expired, error

    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol,
            'order_type': self.order_type,
            'side': self.side,
            'purpose': self.purpose,
            'exchange_order_id': self.exchange_order_id,
            'client_order_id': self.client_order_id,
            'quantity': self.quantity,
            'price': self.price,
            'stop_price': self.stop_price,
            'status': self.status,
            'created_at': self.created_at,
            'last_checked_at': self.last_checked_at,
            'check_count': self.check_count,
            'terminal': self.terminal,
            'resolution': self.resolution,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'InFlightOrder':
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class DurableTracker:
    """
    Tracks in-flight orders across restarts. Persists to JSON file.

    Usage:
        tracker = DurableTracker("data/in_flight_orders.json")
        tracker.track("BTC/USDT", "STOP_MARKET", "sell", "sl", exchange_id="123")
        # ... bot crashes and restarts ...
        tracker.restore()  # loads lost orders
        for order in tracker.lost_orders():
            tracker.poll_order(order, exchange)
    """

    def __init__(self, filepath: str = "data/in_flight_orders.json"):
        self.filepath = Path(filepath)
        self._active: Dict[str, InFlightOrder] = {}   # exchange_order_id → order
        self._lost: Dict[str, InFlightOrder] = {}      # orders needing resolution
        self._ensure_file()

    def _ensure_file(self):
        """Create data directory and file if they don't exist."""
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        if not self.filepath.exists():
            self._save()

    def _save(self):
        """Persist all non-terminal orders to disk."""
        all_orders = list(self._active.values()) + list(self._lost.values())
        data = {
            'version': 1,
            'updated_at': time.time(),
            'orders': [o.to_dict() for o in all_orders if not o.terminal],
        }
        tmp = self.filepath.with_suffix('.tmp')
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.filepath)  # Atomic write

    def track(self, symbol: str, order_type: str, side: str, purpose: str,
              exchange_order_id: Optional[str] = None,
              quantity: float = 0.0, price: Optional[float] = None,
              stop_price: Optional[float] = None):
        """Register a new in-flight order."""
        order = InFlightOrder(
            symbol=symbol, order_type=order_type, side=side, purpose=purpose,
            exchange_order_id=exchange_order_id, quantity=quantity,
            price=price, stop_price=stop_price)
        key = exchange_order_id or f"client_{order.client_order_id or id(order)}"
        self._active[key] = order
        self._save()

    def confirm(self, exchange_order_id: str):
        """Mark an order as confirmed on exchange."""
        if exchange_order_id in self._active:
            self._active[exchange_order_id].status = "CONFIRMED"
            self._active[exchange_order_id].exchange_order_id = exchange_order_id
            self._save()

    def resolve(self, exchange_order_id: str, resolution: str):
        """Mark an order as terminal (filled, cancelled, etc.)."""
        if exchange_order_id in self._active:
            self._active[exchange_order_id].terminal = True
            self._active[exchange_order_id].resolution = resolution
            self._active.pop(exchange_order_id, None)
        if exchange_order_id in self._lost:
            self._lost[exchange_order_id].terminal = True
            self._lost[exchange_order_id].resolution = resolution
            self._lost.pop(exchange_order_id, None)
        self._save()

    def mark_lost(self, exchange_order_id: str):
        """Move order to lost bucket — needs polling for resolution."""
        if exchange_order_id in self._active:
            order = self._active.pop(exchange_order_id)
            order.status = "LOST"
            order.last_checked_at = time.time()
            self._lost[exchange_order_id] = order
            self._save()

    def restore(self) -> List[InFlightOrder]:
        """
        Restore in-flight orders from disk. Called on startup.
        All non-terminal orders become 'lost' and need polling.
        """
        if not self.filepath.exists():
            return []
        try:
            with open(self.filepath, 'r') as f:
                data = json.load(f)
            orders = [InFlightOrder.from_dict(o) for o in data.get('orders', [])]
            # All restored orders go to lost bucket — need fresh polling
            for order in orders:
                if not order.terminal:
                    order.status = "LOST"
                    order.last_checked_at = time.time()
                    key = order.exchange_order_id or f"client_{order.client_order_id or id(order)}"
                    self._lost[key] = order
            return list(self._lost.values())
        except (json.JSONDecodeError, KeyError, FileNotFoundError):
            return []

    def lost_orders(self) -> List[InFlightOrder]:
        """Orders that need polling for resolution."""
        return [o for o in self._lost.values() if not o.terminal]

    def pending_count(self) -> int:
        """Number of unresolved in-flight orders."""
        return len(self._active) + len(self._lost)

    def cleanup_terminal(self):
        """Remove all terminal orders from tracking."""
        self._lost = {k: v for k, v in self._lost.items() if not v.terminal}
        self._active = {k: v for k, v in self._active.items() if not v.terminal}
        self._save()

    async def resolve_lost_order(self, order: InFlightOrder, exchange) -> bool:
        """
        Poll a lost order against the exchange. Returns True if resolved.

        Steps:
        1. Try fetch_order to check if it exists
        2. If -2013 (not found), the order is terminal
        3. If filled, resolve as filled
        4. If still open, leave in lost bucket
        """
        if not order.exchange_order_id:
            # No exchange ID — can't poll. Mark terminal as abandoned.
            self.resolve(order.exchange_order_id or "", "abandoned")
            return True

        try:
            result = await exchange.fetch_order(order.exchange_order_id, order.symbol)
            status = result.get('status', '')
            if status in ('closed', 'filled', 'canceled', 'expired', 'rejected'):
                self.resolve(order.exchange_order_id, status)
                return True
            # Still open — keep in lost bucket, update check count
            order.check_count += 1
            order.last_checked_at = time.time()
            # If checked >10 times (50+ seconds), give up and mark terminal
            if order.check_count > 10:
                self.resolve(order.exchange_order_id, "abandoned")
                return True
            self._save()
            return False
        except Exception as e:
            err_str = str(e)
            if '2013' in err_str or '2011' in err_str:
                # Order doesn't exist — was filled, cancelled, or expired
                self.resolve(order.exchange_order_id, "terminal_not_found")
                return True
            # Network error — retry next cycle
            order.check_count += 1
            order.last_checked_at = time.time()
            if order.check_count > 10:
                self.resolve(order.exchange_order_id, "abandoned")
                return True
            self._save()
            return False
