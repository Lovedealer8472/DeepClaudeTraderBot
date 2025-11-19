"""
Binance Futures Exchange Wrapper - Binance USDT-M Futures trading.
"""
import sys
import logging
import asyncio
import ccxt.async_support as ccxt_async
from typing import Dict, Any, Optional, List
from .base import ExchangeBase
from ..logger import get_logger
from ..config import (
    BINANCE_API_KEY, BINANCE_SECRET, DRY_RUN, EXCHANGE_TIMEOUT_MS, 
    EXCHANGE_RETRIES, RETRY_DELAY_MULTIPLIER
)


class BinanceFuturesExchange(ExchangeBase):
    """Wrapper for Binance USDT-M Futures trading."""
    
    def __init__(self, config=None):
        """
        Initialize Binance Futures exchange wrapper.
        
        Args:
            config: Configuration object (optional, uses module config if not provided)
        """
        self.logger = get_logger("BinanceFutures")
        self.config = config
        self.exchange = None
        self.markets = {}
        
        # OPTIMIZATION: Cache symbol normalization/denormalization (hot path)
        self._normalize_cache = {}
        self._denormalize_cache = {}
        self._cache_max_size = 1000  # Limit cache size
        
        # Get credentials from config or module
        if config:
            self.api_key = getattr(config, 'BINANCE_API_KEY', BINANCE_API_KEY)
            self.api_secret = getattr(config, 'BINANCE_API_SECRET', BINANCE_SECRET)
            self.dry_run = getattr(config, 'DRY_RUN', DRY_RUN)
            self.testnet = getattr(config, 'BINANCE_TESTNET', False)
        else:
            self.api_key = BINANCE_API_KEY
            self.api_secret = BINANCE_SECRET
            self.dry_run = DRY_RUN
            self.testnet = False
    
    async def initialize(self):
        """Initialize Binance Futures exchange connection."""
        # Configure for futures trading
        options = {
            "enableRateLimit": True,
            "timeout": EXCHANGE_TIMEOUT_MS,
            "options": {
                "defaultType": "future",  # Binance futures
                "fetchCurrencies": False,
                "adjustForTimeDifference": True,
            }
        }
        
        if self.api_key and self.api_secret:
            options["apiKey"] = self.api_key
            options["secret"] = self.api_secret
        
        # CRITICAL: Use binanceusdm for USDT-M futures support (includes fetch_tickers)
        # binanceusdm has proper futures support, while binance() with defaultType='future' doesn't
        self.exchange = ccxt_async.binanceusdm(options)
        
        # Enable testnet if configured
        if self.testnet:
            try:
                self.exchange.set_sandbox_mode(True)
                self.logger.info("Binance Futures testnet mode enabled")
            except Exception as e:
                self.logger.warning(f"Could not enable testnet mode: {e}")
        
        # Load markets with retries
        attempts = EXCHANGE_RETRIES
        last_err = None
        for i in range(1, attempts + 1):
            try:
                await self.load_markets(reload=True)
                self.logger.info(f"Binance Futures initialized: {len(self.markets)} markets loaded")
                return
            except Exception as e:
                last_err = e
                self.logger.warning(f"Binance Futures market load attempt {i}/{attempts} failed: {type(e).__name__}: {str(e)}")
                if i < attempts:
                    await asyncio.sleep(RETRY_DELAY_MULTIPLIER * i)
        
        # If all retries failed, raise error
        if last_err:
            self.logger.error(f"Binance Futures initialization failed after {attempts} attempts: {last_err}")
            raise last_err
    
    async def load_markets(self, reload: bool = False, params: Optional[Dict] = None) -> Dict[str, Any]:
        """Load markets from Binance Futures."""
        if not self.exchange:
            raise Exception("Exchange not initialized")
        
        # Binance futures uses defaultType="future" in options
        mkts = await self.exchange.load_markets(reload=reload)
        
        # Filter for USDT-M perpetual futures only
        futures_markets = {}
        for sym, m in mkts.items():
            # Only include USDT-margined futures
            if "USDT" not in sym:
                continue
            # Binance futures use type="future" in ccxt
            market_type = m.get("type", "")
            # Binance futures can be "future" or "swap" in ccxt
            if market_type not in ("future", "swap"):
                continue
            # Only include perpetual contracts (no delivery date)
            if m.get("expiry"):
                continue  # Skip delivery contracts
            # Only USDT-margined
            if m.get("settle") and m.get("settle") != "USDT":
                continue
            
            futures_markets[sym] = m
        
        self.markets = futures_markets
        return futures_markets
    
    async def fetch_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch ticker data for a symbol."""
        if not self.exchange:
            return None
        try:
            # Denormalize symbol for exchange API (CRITICAL: must be "BTCUSDT" format)
            exchange_symbol = self.denormalize_symbol(symbol)
            return await self.exchange.fetch_ticker(exchange_symbol)
        except Exception as e:
            self.logger.warning(f"Error fetching ticker for {symbol}: {e}")
            return None
    
    async def fetch_tickers(
        self,
        symbols: Optional[List[str]] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Fetch ticker data for multiple symbols (USDT-M futures).

        - Prefers unified snake_case fetch_tickers() when available.
        - Falls back to per-symbol fetch_ticker() if batch is unsupported or fails.
        - Uses normalized symbols internally ("BTC/USDT"), denormalized only for API calls ("BTCUSDT").
        """
        if not self.exchange:
            return {}

        # Ensure markets are loaded so self.markets is usable
        if not self.markets:
            await self.load_markets(reload=False)

        params = params or {}

        # Diagnostics: confirm exchange + capabilities
        exchange_class = type(self.exchange).__name__
        exchange_id = getattr(self.exchange, "id", "unknown")
        # CRITICAL: Check both snake_case and camelCase for binanceusdm
        # binanceusdm might have has['fetchTickers']=True but has['fetch_tickers']=None
        has_fetch_tickers = (
            self.exchange.has.get("fetch_tickers") is True or
            self.exchange.has.get("fetchTickers") is True or
            hasattr(self.exchange, "fetch_tickers")  # Fallback: if method exists, try it
        )

        self.logger.debug(
            f"fetch_tickers(): exchange_class={exchange_class}, "
            f"exchange_id={exchange_id}, "
            f"has.fetch_tickers={self.exchange.has.get('fetch_tickers')}, "
            f"has.fetchTickers={self.exchange.has.get('fetchTickers')}, "
            f"can_call={has_fetch_tickers}, "
            f"symbols_requested={len(symbols) if symbols else 'all'}"
        )

        results: Dict[str, Any] = {}

        # ─────────────────────────────────────────────────────────
        # 1) Preferred path: unified snake_case fetch_tickers()
        # ─────────────────────────────────────────────────────────
        if has_fetch_tickers:
            try:
                # CRITICAL: binanceusdm.fetch_tickers() works best when called without arguments
                # It returns all tickers for the futures exchange
                # For specific symbols, fetch all and filter after (more reliable)
                if symbols is None:
                    # Fetch all tickers (no symbols parameter) - this is the most reliable path
                    if self.logger.isEnabledFor(logging.DEBUG):
                        self.logger.debug("fetch_tickers(): calling fetch_tickers() without symbols (fetch all)")
                    raw = await self.exchange.fetch_tickers(params=params)
                else:
                    # For specific symbols, fetch all tickers first, then filter
                    # This is more reliable than passing symbols list (which may not be supported)
                    if self.logger.isEnabledFor(logging.DEBUG):
                        self.logger.debug(
                            f"fetch_tickers(): fetching all tickers, will filter to {len(symbols)} requested symbols"
                        )
                    raw = await self.exchange.fetch_tickers(params=params)
                
                # CRITICAL DEBUG: Log raw response
                self.logger.debug(
                    f"fetch_tickers(): raw response type={type(raw)}, "
                    f"is_dict={isinstance(raw, dict)}, len={len(raw) if isinstance(raw, dict) else 'N/A'}"
                )

                if raw and isinstance(raw, dict):
                    # Process all tickers from raw response
                    # If specific symbols were requested, filter after processing
                    symbol_set = None
                    if symbols is not None:
                        symbol_set = set(symbols)
                    
                    for ex_sym, ticker in raw.items():
                        if not ticker or not isinstance(ticker, dict):
                            continue
                        
                        # CRITICAL: Binance Futures tickers may have bid/ask as None
                        # Use last price as fallback for bid/ask if needed
                        bid = ticker.get("bid")
                        ask = ticker.get("ask")
                        last = ticker.get("last")
                        
                        # Convert None to 0 for validation, but preserve None in ticker data
                        bid_val = bid if bid is not None else 0
                        ask_val = ask if ask is not None else 0
                        last_val = last if last is not None else 0
                        
                        # CRITICAL: Only require last price (required for pricing)
                        # bid/ask can be None for futures tickers (will use last or orderbook)
                        if last_val <= 0:
                            continue  # Must have last price
                        
                        # If bid/ask are None, use last price as fallback (common for futures)
                        if bid is None or bid <= 0:
                            bid = last
                        if ask is None or ask <= 0:
                            ask = last
                        
                        # Update ticker with fallback values for consistency
                        ticker["bid"] = bid
                        ticker["ask"] = ask
                        
                        # Normalize exchange symbol to internal format
                        norm = self.normalize_symbol(ex_sym)
                        
                        # If specific symbols were requested, filter
                        if symbol_set is not None and norm not in symbol_set:
                            continue  # Skip symbols not in request list
                        
                        results[norm] = ticker

                self.logger.info(
                    f"fetch_tickers(): batch fetched {len(results)} valid tickers from {len(raw) if isinstance(raw, dict) else 0} raw tickers"
                )
                # If we got anything useful, return early
                if results:
                    return results

                self.logger.warning(
                    f"fetch_tickers(): batch call returned {len(raw) if isinstance(raw, dict) else 0} raw tickers but 0 valid tickers, "
                    f"falling back to per-symbol fetch_ticker()"
                )
            except Exception as e:
                self.logger.error(
                    f"fetch_tickers(): batch fetch_tickers() failed with {type(e).__name__}: {e}, "
                    f"falling back to per-symbol"
                )

        # ─────────────────────────────────────────────────────────
        # 2) Fallback: per-symbol fetch_ticker() in small batches
        # ─────────────────────────────────────────────────────────
        self.logger.debug("fetch_tickers(): using per-symbol fetch_ticker() fallback")
        
        # If no symbols provided, derive from markets for fallback
        if symbols is None:
            if not self.markets:
                self.logger.warning("fetch_tickers(): fallback - no markets loaded and no symbols provided")
                return results
            market_symbols = list(self.markets.keys())
            # Limit for fallback (smaller batches for individual calls)
            market_symbols = market_symbols[:100]
            symbols = [self.normalize_symbol(s) for s in market_symbols]
        
        if not symbols:
            return results

        batch_size = 10
        success = 0
        failed = 0

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            # IMPORTANT: pass normalized symbols ("BTC/USDT") into self.fetch_ticker()
            # It will denormalize internally.
            tasks = [self.fetch_ticker(sym) for sym in batch]
            results_batch = await asyncio.gather(*tasks, return_exceptions=True)

            for sym, ticker_or_exc in zip(batch, results_batch):
                if isinstance(ticker_or_exc, Exception):
                    failed += 1
                    # Only log a few failures to avoid spam
                    if failed <= 3:
                        self.logger.debug(
                            f"fetch_ticker() exception for {sym}: {ticker_or_exc}"
                        )
                    continue

                ticker = ticker_or_exc
                if not ticker or not isinstance(ticker, dict):
                    failed += 1
                    continue

                # CRITICAL: Binance Futures tickers may have bid/ask as None
                bid = ticker.get("bid")
                ask = ticker.get("ask")
                last = ticker.get("last")
                
                # Convert None to 0 for validation
                bid_val = bid if bid is not None else 0
                ask_val = ask if ask is not None else 0
                last_val = last if last is not None else 0
                
                # CRITICAL: Only require last price (required for pricing)
                # bid/ask can be None for futures tickers (will use last or orderbook)
                if last_val <= 0:
                    continue  # Must have last price
                
                # If bid/ask are None, use last price as fallback (common for futures)
                if bid is None or bid <= 0:
                    bid = last
                    ticker["bid"] = bid
                if ask is None or ask <= 0:
                    ask = last
                    ticker["ask"] = ask

                results[sym] = ticker
                success += 1

            # Small pause between batches to respect rate limits
            if i + batch_size < len(symbols):
                await asyncio.sleep(0.05)

        self.logger.debug(
            f"fetch_tickers(): fallback fetched {success} tickers, {failed} failures "
            f"out of {len(symbols)} symbols"
        )
        return results
    
    async def fetch_ohlcv(self, symbol: str, timeframe: str = "1m", limit: int = 100, params: Optional[Dict] = None) -> List[List]:
        """Fetch OHLCV data for a symbol."""
        if not self.exchange:
            return []
        try:
            # Ensure params is always a dict (CCXT requires it)
            if params is None:
                params = {}
            return await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit, params=params)
        except Exception as e:
            self.logger.warning(f"Error fetching OHLCV for {symbol} {timeframe}: {e}")
            return []
    
    async def fetch_order_book(self, symbol: str, limit: int = 50, params: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """Fetch orderbook for a symbol."""
        if not self.exchange:
            return None
        try:
            # Ensure params is always a dict (CCXT requires it)
            if params is None:
                params = {}
            return await self.exchange.fetch_order_book(symbol, limit=limit, params=params)
        except Exception as e:
            self.logger.warning(f"Error fetching orderbook for {symbol}: {e}")
            return None
    
    async def fetch_balance(self, params: Optional[Dict] = None) -> Dict[str, Any]:
        """Fetch account balance."""
        if not self.exchange:
            return {}
        try:
            # Ensure params is always a dict (CCXT requires it)
            if params is None:
                params = {}
            # Binance futures uses defaultType="future" in options
            return await self.exchange.fetch_balance(params=params)
        except Exception as e:
            self.logger.warning(f"Error fetching balance: {e}")
            return {}
    
    async def fetch_positions(self, symbols: Optional[List[str]] = None, params: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Fetch open futures positions."""
        if not self.exchange:
            return []
        try:
            # Ensure params is always a dict (CCXT requires it)
            if params is None:
                params = {}
            # Binance futures uses defaultType="future" in options
            positions = await self.exchange.fetch_positions(symbols, params=params)
            # Filter for open positions only (contracts != 0)
            return [p for p in positions if p.get("contracts", 0) != 0]
        except Exception as e:
            self.logger.warning(f"Error fetching positions: {e}")
            return []
    
    async def create_order(self, symbol: str, order_type: str, side: str,
                          amount: float, price: Optional[float] = None,
                          params: Optional[Dict] = None) -> Dict[str, Any]:
        """Create a futures order."""
        # Denormalize symbol for exchange API (Binance uses "BTC/USDT" format, same as normalized)
        exchange_symbol = self.denormalize_symbol(symbol)
        
        if self.dry_run:
            import time
            return {
                "id": f"DRY_{int(time.time() * 1000)}",
                "symbol": exchange_symbol,
                "type": order_type,
                "side": side,
                "amount": amount,
                "price": price or 0.0,
                "status": "closed",
                "filled": amount,
                "average": price or 0.0,
            }
        
        if not self.exchange:
            raise Exception("Exchange not initialized")
        
        try:
            # Binance futures doesn't use positionSide for USDT-M
            # Filter out positionSide if present (it's not needed for Binance)
            if params is None:
                params = {}
            # Remove positionSide if present (Binance doesn't use it for USDT-M)
            if "positionSide" in params:
                params = {k: v for k, v in params.items() if k != "positionSide"}
            
            # Binance futures uses "buy" for long, "sell" for short
            # Side is already "buy" or "sell" from our code
            return await self.exchange.create_order(
                exchange_symbol, order_type, side, amount, price, params
            )
        except Exception as e:
            self.logger.error(f"Error creating order {exchange_symbol} {side} {amount}: {e}")
            raise
    
    async def set_leverage(self, leverage: int, symbol: str, params: Optional[Dict] = None):
        """Set leverage for a symbol."""
        # Denormalize symbol for exchange API
        exchange_symbol = self.denormalize_symbol(symbol)
        
        if self.dry_run:
            return
        if not self.exchange:
            raise Exception("Exchange not initialized")
        try:
            # Binance futures uses set_leverage method
            # Binance requires marginMode (ISOLATED or CROSS) - default to ISOLATED
            if params is None:
                params = {}
            if "marginMode" not in params:
                params["marginMode"] = "ISOLATED"
            
            await self.exchange.set_leverage(leverage, exchange_symbol, params=params)
        except Exception as e:
            self.logger.warning(f"Error setting leverage for {exchange_symbol}: {e}")
    
    async def fetch_order(self, order_id: str, symbol: str, params: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """Fetch order status by order ID."""
        # Denormalize symbol for exchange API
        exchange_symbol = self.denormalize_symbol(symbol)
        
        if not self.exchange:
            return None
        try:
            # Ensure params is always a dict (CCXT requires it)
            if params is None:
                params = {}
            return await self.exchange.fetch_order(order_id, exchange_symbol, params=params)
        except Exception as e:
            self.logger.warning(f"Error fetching order {order_id} for {exchange_symbol}: {e}")
            return None
    
    def normalize_symbol(self, symbol: str) -> str:
        """
        Normalize symbol format for internal use.
        OPTIMIZED: Uses caching and string interning to avoid repeated string operations.
        Converts exchange-specific format (e.g., "BTCUSDT") to normalized format (e.g., "BTC/USDT").
        """
        # OPTIMIZATION: Check cache first
        if symbol in self._normalize_cache:
            return self._normalize_cache[symbol]
        
        # Remove any separators
        clean = symbol.replace("/", "").replace("-", "").replace(":", "")
        
        # If it already ends with USDT (like "BTCUSDT"), extract base
        if clean.endswith("USDT"):
            # Check if it's double USDT (like "CHRUSDTUSDT" from bad normalization)
            if clean.endswith("USDTUSDT"):
                base = clean[:-8]  # Remove "USDTUSDT"
            else:
                base = clean[:-4]  # Remove "USDT"
            result = f"{base}/USDT"
        elif "/" in symbol:
            # If it's already in normalized format with slash, return as-is
            result = symbol.upper()
        elif len(clean) <= 10:  # Reasonable base currency length
            # If it's just base currency, add USDT
            result = f"{clean}/USDT"
        else:
            result = symbol.upper()
        
        # OPTIMIZATION: Intern string for faster comparisons and lower memory
        result = sys.intern(result)
        
        # OPTIMIZATION: Cache result (with size limit)
        if len(self._normalize_cache) < self._cache_max_size:
            self._normalize_cache[symbol] = result
        elif len(self._normalize_cache) >= self._cache_max_size:
            # Clear cache if too large (simple FIFO eviction)
            self._normalize_cache.clear()
            self._normalize_cache[symbol] = result
        
        return result
    
    def denormalize_symbol(self, symbol: str) -> str:
        """
        Denormalize symbol format for Binance Futures API.
        OPTIMIZED: Uses caching and string interning to avoid repeated string operations.
        CRITICAL: Binance Futures requires "BTCUSDT" format (no slashes, no :USDT suffix).
        """
        # OPTIMIZATION: Check cache first
        if symbol in self._denormalize_cache:
            return self._denormalize_cache[symbol]
        
        # Remove any separators
        clean = symbol.replace("/", "").replace("-", "").replace(":", "")
        
        # If it already ends with USDT (like "BTCUSDT"), return as-is
        if clean.endswith("USDT"):
            # Check if it's double USDT (like "CHRUSDTUSDT" from bad normalization)
            if clean.endswith("USDTUSDT"):
                result = clean[:-4].upper()  # Remove one "USDT"
            else:
                result = clean.upper()
        elif len(clean) <= 10:  # Reasonable base currency length
            # If it's just base currency, add USDT
            result = f"{clean}USDT".upper()
        else:
            result = clean.upper()
        
        # OPTIMIZATION: Intern string for faster comparisons and lower memory
        result = sys.intern(result)
        
        # OPTIMIZATION: Cache result (with size limit)
        if len(self._denormalize_cache) < self._cache_max_size:
            self._denormalize_cache[symbol] = result
        elif len(self._denormalize_cache) >= self._cache_max_size:
            # Clear cache if too large (simple FIFO eviction)
            self._denormalize_cache.clear()
            self._denormalize_cache[symbol] = result
        
        return result
    
    def is_futures_market(self, market: Dict[str, Any]) -> bool:
        """Check if a market is a futures market."""
        market_type = market.get("type", "")
        # Binance futures can be "future" or "swap" in ccxt
        return market_type in ("future", "swap")
    
    async def close(self):
        """Close exchange connection."""
        if self.exchange:
            try:
                await self.exchange.close()
            except Exception:
                pass

