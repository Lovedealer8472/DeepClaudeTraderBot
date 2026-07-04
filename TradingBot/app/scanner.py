"""
Symbol Scanner Module

RECOMMENDATION #7: Extracted scanner logic from bot.py for better modularity.

This module handles:
- Processing symbols for signal generation
- Fetching market data (tickers, orderbooks)
- Generating trading signals
- Scoring and filtering signals
"""

import asyncio
import time
from typing import Optional, Tuple, Dict, Any

from .logger import get_logger
from .config import (
    MIN_SIGNAL_SCORE, MIN_SIGNAL_STRENGTH,
    SIGNAL_PERCENTILE_THRESHOLD, DRY_RUN
)


class SymbolScanner:
    """Handles scanning symbols and generating signals."""
    
    def __init__(self, bot):
        """
        Initialize scanner with reference to bot.
        
        Args:
            bot: ScalperBot instance (for access to exchange, caches, etc.)
        """
        self.bot = bot
        self.logger = get_logger("SymbolScanner")
    
    async def process_symbol(
        self,
        symbol: str,
        batch_now: float,
        scan_regime_config: Dict,
        scan_recent_trades: list,
        scan_volatility_regime: str,
        scan_btc_trend: Optional[str]
    ) -> Tuple[str, ...]:
        """
        Process a single symbol: fetch data, generate signal, score it.
        
        PERFORMANCE OPTIMIZED:
        - Uses pre-bound invariants (regime_config, etc.)
        - Reuses batch timestamp
        - Cache-first for tickers and orderbooks
        
        Args:
            symbol: Symbol to process
            batch_now: Cached timestamp for this batch
            scan_regime_config: Pre-bound regime config
            scan_recent_trades: Pre-bound recent trades list
            scan_volatility_regime: Pre-bound volatility regime
            scan_btc_trend: Pre-bound BTC trend
        
        Returns:
            Tuple of (status, symbol, stats, signal, latency_ms, was_cache_hit, orderbook, orderbook_fetched)
        """
        try:
            symbol_now = batch_now  # Reuse batch timestamp
            
            # 1) Get denormalized symbol for exchange API (only needed in LIVE mode)
            denormalized_symbol = None
            if self.bot.exchange_wrapper:
                denormalized_symbol = self.bot.exchange_wrapper.denormalize_symbol(symbol)
            
            # 2) Fetch ticker (cache-first, or from replay feed)
            was_cache_hit = False
            if self.bot.replay_mode and self.bot.replay_feed:
                # REPLAY MODE: Get ticker from replay feed
                ticker_data = self.bot.replay_feed.get_ticker_data(symbol)
                if ticker_data:
                    # Convert ticker data to SymbolStats object (for compatibility)
                    from .universe import SymbolStats
                    stats = SymbolStats(symbol)
                    stats.bid = ticker_data.get('bid', 0.0)
                    stats.ask = ticker_data.get('ask', 0.0)
                    stats.last = ticker_data.get('last', 0.0)
                    stats.mark = ticker_data.get('mark', ticker_data.get('last', 0.0))
                    stats.vol_quote = ticker_data.get('quoteVolume', 0.0)
                    # Calculate spread_bps
                    if stats.bid and stats.ask and stats.ask > 0:
                        mid = 0.5 * (stats.bid + stats.ask)
                        if mid > 0:
                            stats.spread_bps = abs(stats.ask - stats.bid) / mid * 1e4
                    else:
                        stats.spread_bps = 9999.0
                    stats.pct_change_24h = 0.0  # Not available in replay
                    was_cache_hit = True
                else:
                    return ('skipped', 'no_replay_ticker', None, None, was_cache_hit, False)
            else:
                # LIVE MODE: Fetch from exchange/cache
                try:
                    stats = await asyncio.wait_for(
                        self.bot.ticker_cache.get_ticker_stats(
                            self.bot.exchange_wrapper, 
                            symbol,
                            denormalized_symbol
                        ),
                        timeout=1.5
                    )
                    was_cache_hit = self.bot.ticker_cache.is_cached(symbol)
                except asyncio.TimeoutError:
                    return ('skipped', 'ticker_timeout', None, None, was_cache_hit, False)
                except Exception as e:
                    self.logger.debug(f"Ticker fetch failed for {symbol}: {e}")
                    return ('skipped', 'ticker_error', None, None, was_cache_hit, False)
            
            if stats is None:
                return ('skipped', 'no_stats', None, None, was_cache_hit, False)
            
            # PERFORMANCE: Pre-extract stats attributes to avoid repeated getattr() calls
            stats_pct_change = getattr(stats, 'pct_change_24h', None)
            stats_spread_bps = getattr(stats, 'spread_bps', None)
            stats_vol_quote = getattr(stats, 'vol_quote', None)
            stats_last = getattr(stats, 'last', None)
            stats_bid = getattr(stats, 'bid', None)
            stats_ask = getattr(stats, 'ask', None)
            
            # 3) Calculate latency
            latency_ms = (time.time() - symbol_now) * 1000.0
            
            # 4) Fetch orderbook (cache-first or fresh, or from replay feed)
            orderbook = None
            orderbook_fetched = False
            
            # REPLAY MODE: Get orderbook from replay feed
            if self.bot.replay_mode and self.bot.replay_feed:
                snapshot = self.bot.replay_feed.get_market_snapshot(symbol)
                if snapshot and 'orderbook' in snapshot:
                    orderbook = snapshot['orderbook']
                    orderbook_fetched = True
                    # Also update cache
                    self.bot.orderbook_cache[symbol] = {
                        'data': orderbook,
                        'timestamp': symbol_now
                    }
            else:
                # LIVE MODE: Try cache first
                cached_ob = self.bot.orderbook_cache.get(symbol)
                if cached_ob and (symbol_now - cached_ob.get('timestamp', 0)) < 5.0:
                    orderbook = cached_ob['data']
                    orderbook_fetched = True
                else:
                    # Fetch fresh orderbook if not in cache or stale
                    if not DRY_RUN and self.bot.budget.remaining() > 5:
                        try:
                            orderbook = await asyncio.wait_for(
                                self.bot.exchange_wrapper.fetch_order_book(denormalized_symbol, limit=20),
                                timeout=0.3
                            )
                            orderbook_fetched = True
                            self.bot.budget.tick(1, "orderbook")
                            
                            self.bot.orderbook_cache[symbol] = {
                                'data': orderbook,
                                'timestamp': symbol_now
                            }
                        except Exception:
                            orderbook = None
                            orderbook_fetched = False
            
            # 5) Convert SymbolStats to dict for signal generator (which expects Dict[str, Any])
            # Signal generator uses .get() method which only works on dicts
            # SymbolStats uses __slots__, so we need to manually extract fields
            if isinstance(stats, dict):
                stats_dict = stats
            else:
                # Convert SymbolStats (dataclass with slots) to dict
                from dataclasses import fields
                stats_dict = {}
                for field in fields(stats):
                    stats_dict[field.name] = getattr(stats, field.name, field.default if hasattr(field, 'default') else None)
                # Ensure all required fields are present with defaults
                stats_dict.setdefault('bid', 0.0)
                stats_dict.setdefault('ask', 0.0)
                stats_dict.setdefault('last', 0.0)
                stats_dict.setdefault('mark', stats_dict.get('last', 0.0))
                stats_dict.setdefault('spread_bps', 9999.0)
                stats_dict.setdefault('vol_quote', 0.0)
                stats_dict.setdefault('pct_change_24h', 0.0)
            
            # 6) Calculate indicators from OHLCV (needed for signal generation)
            indicators = None
            if self.bot.replay_mode and self.bot.replay_feed:
                # REPLAY MODE: Calculate indicators from replay OHLCV
                try:
                    ohlcv = self.bot.replay_feed.get_ohlcv(symbol, timeframe='1m', limit=100)
                    if ohlcv and len(ohlcv) >= 20:  # Need at least 20 candles for RSI
                        from .indicators import calculate_rsi, calculate_ema, calculate_atr, calculate_adx, calculate_efficiency_ratio
                        # Extract close prices
                        closes = [candle[4] for candle in ohlcv]  # close is index 4
                        highs = [candle[2] for candle in ohlcv]  # high is index 2
                        lows = [candle[3] for candle in ohlcv]  # low is index 3

                        # Calculate indicators
                        rsi = calculate_rsi(closes, period=14)
                        ema20 = calculate_ema(closes, period=20)
                        ema50 = calculate_ema(closes, period=50)
                        ema100 = calculate_ema(closes, period=100) if len(closes) >= 100 else None
                        atr = calculate_atr(highs, lows, closes, period=14)
                        adx = calculate_adx(highs, lows, closes, period=14)
                        er = calculate_efficiency_ratio(closes, period=20)

                        # Calculate ATR as percentage of price
                        atr_pct = None
                        if atr and closes and closes[-1] > 0:
                            atr_pct = (atr / closes[-1]) * 100.0

                        # Calculate pct_change_24h from OHLCV (last 24 hours = 1440 1m candles, use last 100)
                        pct_change_24h = 0.0
                        if len(closes) >= 2:
                            # Use first and last close in the window
                            first_close = closes[0]
                            last_close = closes[-1]
                            if first_close > 0:
                                pct_change_24h = ((last_close - first_close) / first_close) * 100.0

                        # Update stats_dict with calculated pct_change_24h
                        stats_dict['pct_change_24h'] = pct_change_24h

                        indicators = {
                            'rsi': rsi,
                            'ema20': ema20,
                            'ema50': ema50,
                            'ema100': ema100,
                            'atr': atr,
                            'atr_pct': atr_pct,
                            'adx': adx if adx is not None else 20.0,  # default to neutral (20 = not trending)
                            'er': er if er is not None else 0.5,  # Efficiency Ratio (0-1), 0.5 = neutral
                        }
                except Exception as e:
                    self.logger.debug(f"Failed to calculate indicators for {symbol} in replay: {e}")
                    indicators = None
                else:
                    # Store indicators in bot's cache for downstream use (ADX gate, exit checks)
                    if hasattr(self.bot, 'indicators_cache'):
                        self.bot.indicators_cache[symbol] = indicators
            else:
                # LIVE MODE: Fetch OHLCV from exchange, calculate indicators
                if not hasattr(self.bot, '_live_ohlcv_cache'):
                    self.bot._live_ohlcv_cache = {}
                cache = self.bot._live_ohlcv_cache
                now_ts = time.time()
                # Only fetch if not cached or stale (>60s)
                if symbol not in cache or (now_ts - cache[symbol][0]) > 60:
                    try:
                        ohlcv = self.bot.exchange.fetch_ohlcv(symbol, '1m', limit=100)
                        if ohlcv and len(ohlcv) >= 20:
                            cache[symbol] = (now_ts, ohlcv)
                    except Exception:
                        pass
                # Calculate indicators from cached OHLCV
                cached = cache.get(symbol)
                if cached:
                    ohlcv = cached[1]
                    try:
                        from .indicators import calculate_rsi, calculate_ema, calculate_atr, calculate_adx, calculate_efficiency_ratio
                        closes = [c[4] for c in ohlcv]
                        highs = [c[2] for c in ohlcv]
                        lows = [c[3] for c in ohlcv]
                        rsi = calculate_rsi(closes, period=14)
                        ema20 = calculate_ema(closes, period=20)
                        ema50 = calculate_ema(closes, period=50)
                        atr = calculate_atr(highs, lows, closes, period=14)
                        adx = calculate_adx(highs, lows, closes, period=14)
                        er = calculate_efficiency_ratio(closes, period=20)
                        atr_pct = (atr / closes[-1]) * 100.0 if atr and closes[-1] > 0 else None
                        pct_change_24h = ((closes[-1] - closes[0]) / closes[0]) * 100.0 if closes[0] > 0 else 0.0
                        stats_dict['pct_change_24h'] = pct_change_24h
                        indicators = {
                            'rsi': rsi,
                            'ema20': ema20,
                            'ema50': ema50,
                            'atr': atr,
                            'atr_pct': atr_pct,
                            'adx': adx if adx is not None else 20.0,
                            'er': er if er is not None else 0.5,
                        }
                        if hasattr(self.bot, 'indicators_cache'):
                            self.bot.indicators_cache[symbol] = indicators
                    except Exception:
                        pass
            
            # 7) Generate signal
            signal = await self.bot.signal_generator.generate_signal(
                symbol=symbol,
                stats=stats_dict,  # Pass dict, not SymbolStats object
                ohlcv=None,  # Not used - indicators already calculated
                indicators=indicators,  # Pass calculated indicators
                orderbook=orderbook,
                regime_config=scan_regime_config,
                recent_trades=scan_recent_trades,
                volatility_regime=scan_volatility_regime,
                btc_trend=scan_btc_trend
            )
            
            # 8) Early filter: no signal
            if not signal:
                return ('skipped', 'no_signal', None, None, was_cache_hit, orderbook_fetched)
            
            # 9) Apply filters
            if signal.final_score < MIN_SIGNAL_SCORE:
                return ('skipped', 'low_score', None, None, was_cache_hit, orderbook_fetched)
            
            if signal.strength < MIN_SIGNAL_STRENGTH:
                return ('skipped', 'low_strength', None, None, was_cache_hit, orderbook_fetched)
            
            # 8) Percentile filter (if enabled)
            if SIGNAL_PERCENTILE_THRESHOLD > 0:
                # Check if signal is in top percentile
                # (This would need access to signal_generator's recent signals)
                pass
            
            return ('processed', symbol, stats, signal, latency_ms, was_cache_hit, orderbook, orderbook_fetched)
            
        except Exception as e:
            self.logger.debug(f"Error processing {symbol}: {e}")
            return ('error', symbol, None, None, False, False)

