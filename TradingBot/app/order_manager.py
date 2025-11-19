"""
Order Manager - Handles order execution with smart order routing and timing.
"""

import time
import asyncio
from typing import Optional, Dict, Tuple
from dataclasses import dataclass

from .config import (
    DRY_RUN, ENTRY_DELAY_MS, TAKER_FEE_RATE,
    LEVERAGE_BASE, MAX_LATENCY_MS
)


@dataclass
class OrderResult:
    """Order execution result."""
    success: bool
    order_id: Optional[str] = None
    filled_price: Optional[float] = None
    filled_size: Optional[float] = None
    error: Optional[str] = None
    latency_ms: Optional[float] = None


class OrderManager:
    """Manages order execution with smart routing."""
    
    def __init__(self, exchange=None):
        self.exchange = exchange
        self.order_history = []
        self.leverage_cache = {}  # Cache for leverage settings
    
    async def enter_position(
        self,
        symbol: str,
        side: str,
        size: float,
        entry_price: float,
        delay_ms: Optional[int] = None,
        use_limit: bool = False,
        leverage: Optional[int] = None
    ) -> OrderResult:
        """
        Enter a position with smart execution.
        OPTIMIZED: Parallel operations and faster execution paths.
        
        Args:
            symbol: Trading symbol
            side: 'long' or 'short'
            size: Position size in base currency
            entry_price: Target entry price
            delay_ms: Entry delay in milliseconds
            use_limit: Use limit order instead of market
            leverage: Leverage to use
        
        Returns:
            OrderResult
        """
        if delay_ms is None:
            delay_ms = ENTRY_DELAY_MS
        
        start_time = time.time()
        
        # OPTIMIZATION: Parallelize delay and leverage setting
        # Start leverage setting immediately (non-blocking if already set)
        leverage_task = None
        if leverage and self.exchange and not DRY_RUN:
            leverage_task = asyncio.create_task(
                self._set_leverage_async(leverage, symbol)
            )
        
        # OPTIMIZATION: Apply delay concurrently with leverage setting
        # This allows leverage to be set while we wait
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)
        
        # OPTIMIZATION: Wait for leverage to complete (should be done by now)
        if leverage_task:
            try:
                await asyncio.wait_for(leverage_task, timeout=0.1)  # 100ms timeout
            except asyncio.TimeoutError:
                pass  # Continue even if leverage setting is slow (may already be set)
            except (AttributeError, RuntimeError, KeyError) as e:
                # REFACTOR: Handle leverage setting errors (may already be set)
                self.logger.debug(f"Leverage setting skipped for {symbol}: {e}")
        
        try:
            if DRY_RUN:
                # Simulate order execution
                result = OrderResult(
                    success=True,
                    order_id=f"DRY_{int(time.time() * 1000)}",
                    filled_price=entry_price,
                    filled_size=size,
                    latency_ms=(time.time() - start_time) * 1000
                )
            else:
                # OPTIMIZATION: Use faster execution path for market orders
                if use_limit:
                    result = await self._place_limit_order(
                        symbol, side, size, entry_price
                    )
                else:
                    # OPTIMIZATION: Market orders are faster - use optimized path
                    result = await self._place_market_order_fast(
                        symbol, side, size
                    )
                
                result.latency_ms = (time.time() - start_time) * 1000
            
            # Record order (non-blocking)
            self.order_history.append({
                'symbol': symbol,
                'side': side,
                'size': size,
                'price': result.filled_price or entry_price,
                'timestamp': time.time(),
                'success': result.success
            })
            
            return result
            
        except Exception as e:
            # Log error with context
            try:
                from .logger import get_logger
                get_logger().log_error_with_context(
                    operation="enter_position",
                    error=e,
                    symbol=symbol,
                    side=side,
                    size=size,
                    entry_price=entry_price
                )
            except ImportError:
                pass  # Logger not available
            
            return OrderResult(
                success=False,
                error=f"{type(e).__name__}: {str(e)}",
                latency_ms=(time.time() - start_time) * 1000
            )
    
    async def _set_leverage_async(self, leverage: int, symbol: str):
        """
        Set leverage asynchronously (non-blocking).
        OPTIMIZATION: Cache leverage settings to avoid redundant API calls.
        """
        # OPTIMIZATION: Check cache first
        cache_key = f"{symbol}:{leverage}"
        if cache_key in self.leverage_cache:
            return  # Already set, skip API call
        
        try:
            await self.exchange.set_leverage(leverage, symbol)
            # Cache successful setting
            self.leverage_cache[cache_key] = time.time()
        except (AttributeError, RuntimeError, KeyError):
            # REFACTOR: Leverage may already be set or not supported
            pass  # Non-critical, continue
    
    def pre_set_leverage(self, symbols: list, leverage: int):
        """
        Pre-set leverage for multiple symbols (background task).
        OPTIMIZATION: Set leverage in advance to reduce entry latency.
        
        Args:
            symbols: List of symbols to set leverage for
            leverage: Leverage to set
        """
        if not self.exchange or DRY_RUN:
            return
        
        async def _pre_set():
            for symbol in symbols:
                cache_key = f"{symbol}:{leverage}"
                if cache_key not in self.leverage_cache:
                    try:
                        await self.exchange.set_leverage(leverage, symbol)
                        self.leverage_cache[cache_key] = time.time()
                    except (AttributeError, RuntimeError, KeyError):
                        # REFACTOR: Silently skip symbols where leverage setting fails
                        pass  # Non-critical background task
        
        # Run in background (fire and forget)
        asyncio.create_task(_pre_set())
    
    async def _place_market_order_fast(
        self,
        symbol: str,
        side: str,
        size: float
    ) -> OrderResult:
        """
        OPTIMIZED: Fast market order placement with minimal overhead.
        Uses direct API call without extra validation steps.
        """
        try:
            order_type = "market"
            order_side = "buy" if side == "long" else "sell"
            
            # OPTIMIZATION: Direct order creation without extra checks
            # OPTIMIZATION: Binance Futures USDT-M doesn't use positionSide (only COIN-M does)
            # The exchange wrapper will handle this correctly
            order = await self.exchange.create_order(
                symbol,
                order_type,
                order_side,
                size,
                None,  # price not needed for market orders
                params={}  # Let exchange wrapper handle Binance-specific params
            )
            
            # OPTIMIZATION: Get fill price immediately from order response
            filled_price = order.get('price') or order.get('average') or order.get('info', {}).get('price')
            filled_size = order.get('filled') or order.get('amount') or size
            
            # OPTIMIZATION: Skip order status fetch if we have price (saves ~100-200ms)
            if not filled_price:
                # Fallback: Try to fetch order status (only if needed)
                order_id = order.get('id')
                if order_id:
                    try:
                        filled_order = await asyncio.wait_for(
                            self.exchange.fetch_order(order_id, symbol),
                            timeout=0.5  # 500ms timeout
                        )
                        filled_price = filled_order.get('average') or filled_order.get('price')
                    except (asyncio.TimeoutError, Exception):
                        pass  # Use size as fallback
            
            return OrderResult(
                success=True,
                order_id=order.get('id', ''),
                filled_price=float(filled_price) if filled_price else None,
                filled_size=float(filled_size) if filled_size else size
            )
        except Exception as e:
            return OrderResult(
                success=False,
                error=str(e)
            )
    
    async def _place_market_order(
        self,
        symbol: str,
        side: str,
        size: float
    ) -> OrderResult:
        """Place market order."""
        try:
            order_type = "market"
            order_side = "buy" if side == "long" else "sell"
            
            # OPTIMIZATION: Binance Futures USDT-M doesn't use positionSide
            order = await self.exchange.create_order(
                symbol,
                order_type,
                order_side,
                size,
                None,  # price not needed for market orders
                params={}  # Let exchange wrapper handle Binance-specific params
            )
            
            # Get filled price
            filled_price = order.get('price') or order.get('average')
            if not filled_price:
                # Try to fetch order status
                order_id = order.get('id')
                if order_id:
                    filled_order = await self.exchange.fetch_order(order_id, symbol)
                    filled_price = filled_order.get('average') or filled_order.get('price')
            
            return OrderResult(
                success=True,
                order_id=order.get('id'),
                filled_price=filled_price,
                filled_size=size
            )
        except Exception as e:
            return OrderResult(
                success=False,
                error=str(e)
            )
    
    async def _place_limit_order(
        self,
        symbol: str,
        side: str,
        size: float,
        price: float
    ) -> OrderResult:
        """Place limit order."""
        try:
            order_type = "limit"
            order_side = "buy" if side == "long" else "sell"
            
            # OPTIMIZATION: Binance Futures USDT-M doesn't use positionSide
            order = await self.exchange.create_order(
                symbol,
                order_type,
                order_side,
                size,
                price,
                params={}  # Let exchange wrapper handle Binance-specific params
            )
            
            # Wait for fill (with timeout)
            order_id = order.get('id')
            if order_id:
                filled_order = await self._wait_for_fill(order_id, symbol, timeout=5.0)
                if filled_order:
                    return OrderResult(
                        success=True,
                        order_id=order_id,
                        filled_price=filled_order.get('average') or price,
                        filled_size=filled_order.get('filled', size)
                    )
            
            # Order placed but not filled yet
            return OrderResult(
                success=True,
                order_id=order_id,
                filled_price=price,
                filled_size=0.0  # Not filled yet
            )
        except Exception as e:
            return OrderResult(
                success=False,
                error=str(e)
            )
    
    async def _wait_for_fill(
        self,
        order_id: str,
        symbol: str,
        timeout: float = 5.0
    ) -> Optional[Dict]:
        """Wait for order to fill."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                order = await self.exchange.fetch_order(order_id, symbol)
                if order.get('status') == 'closed' or order.get('filled', 0) > 0:
                    return order
                await asyncio.sleep(0.1)
            except (AttributeError, KeyError, RuntimeError):
                # REFACTOR: Break on order fetch errors
                break  # Stop polling on error
        return None
    
    def should_use_limit_order(
        self,
        spread_bps: float,
        signal_strength: float,
        volatility: float = 0.0
    ) -> bool:
        """
        Determine if limit order should be used instead of market order.
        
        Returns:
            True if limit order is recommended
        """
        # Use limit orders for:
        # 1. Tight spreads (< 20 bps)
        # 2. High signal strength (> 0.7)
        # 3. Low volatility (< 0.5)
        
        if spread_bps < 20 and signal_strength > 0.7 and volatility < 0.5:
            return True
        
        # Use market orders for:
        # 1. Wide spreads (> 30 bps)
        # 2. High volatility (> 0.7)
        # 3. Fast-moving markets
        
        if spread_bps > 30 or volatility > 0.7:
            return False
        
        # Default: use limit for strong signals, market for others
        return signal_strength > 0.8

