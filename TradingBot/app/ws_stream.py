"""
Binance User-Data WebSocket Stream handler.

Provides real-time ORDER_TRADE_UPDATE and ALGO_UPDATE events, eliminating
REST polling for fill detection and protection status. Survives listenKey
expiry with auto-renewal. Dec 2025 Algo Service migration: ALGO_UPDATE
replaces ORDER_TRADE_UPDATE for conditional order lifecycle.

Architecture: single background task, callback-based event dispatch.
Callers register handlers for specific event types.
"""

import asyncio
import json
import time
from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass, field


@dataclass
class StreamConfig:
    listen_key: str = ""
    renew_interval: float = 1800.0  # 30 min — listenKey expires after 60 min
    reconnect_delay: float = 1.0
    max_reconnect_delay: float = 30.0
    ping_interval: float = 30.0


@dataclass
class OrderUpdate:
    """Parsed ORDER_TRADE_UPDATE event."""
    symbol: str = ""
    order_id: str = ""
    client_order_id: str = ""
    order_type: str = ""         # MARKET, LIMIT, STOP_MARKET
    order_status: str = ""       # NEW, PARTIALLY_FILLED, FILLED, CANCELED, REJECTED
    side: str = ""
    price: float = 0.0
    avg_fill_price: float = 0.0
    original_qty: float = 0.0
    executed_qty: float = 0.0
    cum_quote: float = 0.0
    last_filled_qty: float = 0.0
    last_filled_price: float = 0.0
    reduce_only: bool = False
    close_position: bool = False
    event_time: int = 0


@dataclass
class AlgoUpdate:
    """Parsed ALGO_UPDATE event (post-Dec 2025 Algo Service)."""
    symbol: str = ""
    algo_id: str = ""
    client_algo_id: str = ""
    algo_type: str = ""          # CONDITIONAL
    order_type: str = ""         # STOP_MARKET, TAKE_PROFIT_MARKET, TRAILING_STOP_MARKET
    side: str = ""
    algo_status: str = ""        # NEW, TRIGGERING, TRIGGERED, FINISHED, CANCELED, REJECTED, EXPIRED
    trigger_price: float = 0.0
    close_position: bool = False
    actual_order_id: str = ""    # Set when TRIGGERED
    reject_reason: str = ""
    event_time: int = 0


class UserDataStream:
    """
    Binance user-data WebSocket stream with auto-reconnect and listenKey renewal.

    Usage:
        stream = UserDataStream(exchange)
        stream.on_order_update = lambda u: handle_order(u)
        stream.on_algo_update = lambda u: handle_algo(u)
        await stream.start()
        # ... bot runs ...
        await stream.stop()
    """

    BASE_URL = "wss://fstream.binance.com/ws"

    def __init__(self, exchange):
        self._exchange = exchange
        self._config = StreamConfig()
        self._ws: Any = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._reconnect_count = 0
        self._last_event_time = 0.0

        # Callbacks
        self.on_order_update: Optional[Callable[[OrderUpdate], None]] = None
        self.on_algo_update: Optional[Callable[[AlgoUpdate], None]] = None
        self.on_connected: Optional[Callable[[], None]] = None
        self.on_disconnected: Optional[Callable[[str], None]] = None

    async def start(self):
        """Start the stream. Returns immediately; stream runs in background."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        """Stop the stream and cleanup."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._close_ws()

    async def _run_loop(self):
        while self._running:
            try:
                await self._get_listen_key()
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception as e:
                delay = min(
                    self._config.reconnect_delay * (2 ** self._reconnect_count),
                    self._config.max_reconnect_delay,
                )
                self._reconnect_count += 1
                if self.on_disconnected:
                    self.on_disconnected(f"Reconnecting in {delay:.0f}s: {e}")
                await asyncio.sleep(delay)

    async def _get_listen_key(self):
        """POST /fapi/v1/listenKey — get or renew listenKey."""
        resp = await self._exchange.fapiPrivatePostListenKey()
        self._config.listen_key = resp.get("listenKey", "")
        # Schedule renewal at half the expiry
        asyncio.create_task(self._renew_listen_key())

    async def _renew_listen_key(self):
        """PUT /fapi/v1/listenKey — keep alive."""
        while self._running and self._config.listen_key:
            await asyncio.sleep(self._config.renew_interval)
            try:
                if self._config.listen_key:
                    await self._exchange.fapiPrivatePutListenKey(
                        {"listenKey": self._config.listen_key})
            except Exception:
                pass  # Will re-get on next reconnect

    async def _connect_and_listen(self):
        import websockets
        url = f"{self.BASE_URL}/{self._config.listen_key}"
        async with websockets.connect(url, ping_interval=self._config.ping_interval) as ws:
            self._ws = ws
            self._reconnect_count = 0
            if self.on_connected:
                self.on_connected()
            async for message in ws:
                await self._handle_message(message)

    async def _handle_message(self, raw: str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        event_type = data.get("e", "")
        self._last_event_time = time.time()

        if event_type == "ORDER_TRADE_UPDATE":
            await self._handle_order_update(data)
        elif event_type == "ALGO_UPDATE":
            await self._handle_algo_update(data)
        # ACCOUNT_UPDATE is informative but not critical for our bot

    async def _handle_order_update(self, data: dict):
        o = data.get("o", {})
        update = OrderUpdate(
            symbol=o.get("s", ""),
            order_id=str(o.get("i", "")),
            client_order_id=o.get("c", ""),
            order_type=o.get("o", ""),
            order_status=o.get("X", ""),
            side=o.get("S", ""),
            price=float(o.get("p", 0)),
            avg_fill_price=float(o.get("ap", 0)),
            original_qty=float(o.get("q", 0)),
            executed_qty=float(o.get("z", 0)),
            cum_quote=float(o.get("Z", 0)),
            last_filled_qty=float(o.get("l", 0)),
            last_filled_price=float(o.get("L", 0)),
            reduce_only=o.get("r", False),
            close_position=o.get("cp", False),
            event_time=data.get("E", 0),
        )
        if self.on_order_update:
            self.on_order_update(update)

    async def _handle_algo_update(self, data: dict):
        o = data.get("o", {})
        update = AlgoUpdate(
            symbol=o.get("s", ""),
            algo_id=str(o.get("aid", "")),
            client_algo_id=o.get("caid", ""),
            algo_type=o.get("at", ""),
            order_type=o.get("o", ""),
            side=o.get("S", ""),
            algo_status=o.get("X", ""),
            trigger_price=float(o.get("tp", 0)),
            close_position=o.get("cp", False),
            actual_order_id=str(o.get("ai", "")),
            reject_reason=o.get("rm", ""),
            event_time=data.get("E", 0),
        )
        if self.on_algo_update:
            self.on_algo_update(update)

    async def _close_ws(self):
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not getattr(self._ws, 'close_code', None)

    @property
    def seconds_since_last_event(self) -> float:
        if self._last_event_time == 0:
            return float('inf')
        return time.time() - self._last_event_time
