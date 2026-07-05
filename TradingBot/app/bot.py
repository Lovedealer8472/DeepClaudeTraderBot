# OPTIMIZATION: Removed unused imports (ccxt_async, math) and config (USE_WS)
import sys
import os
import time
import asyncio
import math
import logging
from collections import deque
from typing import Optional, Dict, Any

from .logger import get_logger
from .config import (
    DRY_RUN, EXCHANGE, EXCHANGE_TIMEOUT_MS, EXCHANGE_RETRIES,
    ACCOUNT_BAL, LEVERAGE_BASE, MAX_LATENCY_MS, MAX_POSITION_SIZE,
    RETRY_DELAY_MULTIPLIER, MID_PRICE_FACTOR, BTC_TREND_WEAK_THRESHOLD,
    HIGH_VOLATILITY_MEDIAN_THRESHOLD, HIGH_VOLATILITY_P75_THRESHOLD,
    MAX_DRAWDOWN_PCT, DRAWDOWN_CIRCUIT_BREAKER_ENABLED, WHITELIST_SYMBOLS, MIN_24H_VOLUME_USDT,
    MAX_CONCURRENT_POS, MAX_OPEN_POSITIONS, USE_RICH_UI,
    UNICORN_SCORE_THRESHOLD, UNICORN_PROTOCOL_ENABLED,
    STARTUP_DELAY_SEC, PRS_MIN_AGE_MIN
)
from .budget import ApiBudgeter
from .universe import DynamicUniverse, SymbolStats
from .llm_control import LlmController
from .ui import draw_panel
from .position_manager import PositionManager
from .signals import SignalGenerator
from .order_manager import OrderManager
from .exit_manager import ExitManager
from .decision_logger import get_decision_logger
from .decision_event import DecisionEvent, log_trade_decision
from .ticker_cache import get_ticker_cache
from .fast_storage import get_fast_storage
from .exchanges.factory import create_exchange
import app.config as config_module

# NEW ARCHITECTURE: Core modules
from .core.positions import PositionRegistry
from .engine.exit_pipeline import ExitPipeline, ExitRequest

# OPTIMIZATION: Removed unused now_ms() function

# ────────────────────────────────────────────────────────────────
# TRADE LIFECYCLE DOCUMENTATION
# ────────────────────────────────────────────────────────────────
#
# The complete lifecycle of a trade in GreenUniRabbit:
#
# 1. DISCOVERY (scan_and_enter_signals)
#    - Universe refresh: DynamicUniverse refreshes symbol list based on volume/liquidity
#    - Symbol scanning: Each symbol is scanned for signals (momentum, RSI, trend, etc.)
#    - Signal generation: SignalGenerator creates signals with entry/exit prices and scores
#
# 2. FILTERS & CONFIRMATION
#    - Signal Confirmation Window (SCW): Multi-bar confirmation for signal quality
#    - Risk filters: Spread, volume, latency, volatility checks
#    - Correlation blocker: Prevents correlated positions
#    - Risk budget: Total risk budget check (TOTAL_RISK_BUDGET)
#    - Position limit: Max concurrent positions check (MAX_CONCURRENT_POS)
#    - Score-aware replacement: Can replace weakest position if new signal is better
#
# 3. ENTRY (order_manager.enter_position)
#    - Position sizing: Dynamic position sizing based on signal strength, risk budget, Kelly
#    - Leverage: Dynamic leverage based on signal strength and market conditions
#    - Order placement: Market or limit order via exchange wrapper
#    - Position tracking: Added to self.positions dict with metadata
#
# 4. MONITORING (monitor_and_exit_positions)
#    - Price updates: Current price fetched from cache or exchange
#    - PnL calculation: Real-time PnL tracking (peak_pnl, trough_price)
#    - PRS evaluation: Position Recovery Score computed (age, trend, volatility, PnL)
#    - Exit checks: Multiple exit systems checked:
#      * R-based exits (if USE_R_BASED_EXITS): Scalp/Standard/Runner profiles
#      * Legacy exits: Stop-loss, take-profit, trailing stops, stale position rules
#      * PRS exits: Full exit (PRS < 30) or scale-out (PRS < 50)
#      * BERP: Break-Even Rescue Protocol for long-running unprofitable positions
#      * Stale position rules: 90min, extended leash, drawdown resume
#
# 5. EXIT (exit_manager.exit_position - CANONICAL EXIT PATH)
#    - ALL exits must go through exit_manager.exit_position()
#    - Validates position state (not already closed, size > 0)
#    - Calculates exit price (target_price or market)
#    - Executes order (market or limit)
#    - Records PnL, fees, slippage, funding costs
#    - Updates metrics (exits_by_reason)
#    - Removes from self.positions
#    - Logs trade decision
#
# 6. POST-EXIT
#    - PnL aggregation: Updates realized_pnl_total, win_count, loss_count
#    - Performance tracking: Updates win rate, profit factor
#    - Risk budget release: Frees up risk budget for new entries
#
# EXIT REASONS (see app/exit_reasons.py for full list):
# - Stop-loss/take-profit: stop_loss_hit, take_profit_hit, trailing_stop_hit
# - PRS: prs_full_exit_<score>, prs_scale_out_<score>
# - Time-based: stale_position_timeout, max_age_exceeded, stale_90min_exit
# - Risk: risk_budget_exceeded, max_positions_reached, drawdown_circuit_breaker
# - R-based: r_scalp_tp, r_standard_partial_tp, r_runner_trailing_stop, etc.
# - MSX: msx_stage1_invalidation, msx_partial_scalp, etc.
# - Replacement: replaced_by_better_signal, score_aware_replacement
# - Manual: manual_exit, llm_override
#
# ────────────────────────────────────────────────────────────────

class ScalperBot:
    def __init__(self):
        self.logger = get_logger("ScalperBot")
        # Config access via cfg object
        self.cfg = config_module
        self.exchange = None  # Backward compatibility (may be used by some code)
        self.exchange_wrapper = None  # Exchange abstraction wrapper
        self.universe = DynamicUniverse()
        self.budget = ApiBudgeter()
        self.ctrl = LlmController()
        self.position_manager = PositionManager()
        self.signal_generator = SignalGenerator()
        self.order_manager = None  # Will be set after exchange init
        self.exit_manager = None  # Will be set after exchange init
        
        # REPLAY MODE: Data feed for backtesting
        self.replay_mode = False
        self.replay_feed = None
        self.replay_start_time = 0.0
        self._replay_current_time = None  # Current replay time (set by replay_runner)
        
        # Signal Confirmation Window (SCW)
        from .signal_confirmation import SignalConfirmationManager
        
        # RECOMMENDATION #7: Scanner module for better modularity
        from .scanner import SymbolScanner
        self.scanner = SymbolScanner(self)
        self.signal_confirmation = SignalConfirmationManager()
        
        # LATENCY OPTIMIZATION: Initialize caches for ultra-fast data access
        self.ticker_cache = get_ticker_cache(ttl=0.5)  # 500ms cache TTL
        self.fast_storage = get_fast_storage()  # Persistent SQLite cache

        # NEW ARCHITECTURE: Use PositionRegistry for centralized position management
        self.position_registry = PositionRegistry()
        # Backward compatibility: expose positions dict directly
        self.positions = self.position_registry._positions  # Direct access for compatibility
        self.cooldown_until = {}

        self.start_equity = ACCOUNT_BAL
        self.equity_peak = self.start_equity
        self.started_at = self._get_current_time()  # Use replay time if in replay mode
        self.circuit_breaker_triggered = False  # Track if drawdown circuit breaker has triggered
        self.realized_pnl_total = 0.0
        self.realized_fees_total = 0.0
        self.realized_entry_fees_total = 0.0
        self.realized_exit_fees_total = 0.0
        self.realized_slippage_total = 0.0
        self.realized_funding_total = 0.0
        self.gross_win = 0.0
        self.gross_loss = 0.0
        self.win_count = 0
        self.loss_count = 0
        self.recent_errors = deque(maxlen=12)
        self.symbols_scanned_last = 0
        self.current_mode = "Neutral"
        self.mode_since = self._get_current_time()
        self.last_draw = 0
        self.last_signal_scan = 0
        # PURE SCALPER MODE: Hard-locked to scalping, no regime switching
        self.current_regime = "scalping"  # Always scalping
        self.regime_since = self._get_current_time()
        # Force scalper regime config - no LLM switching
        from .regime import TradingRegime, REGIME_CONFIGS
        self.regime_config = REGIME_CONFIGS[TradingRegime.SCALPING]
        
        # Enhanced tracking for UI
        self.signal_history = deque(maxlen=20)  # Last 20 signals with full context
        self.recent_trades = deque(maxlen=15)  # Last 15 trades with enhanced details
        self.loop_times = deque(maxlen=10)  # Track scan cycle times
        self.api_call_times = deque(maxlen=60)  # Track API calls for rate calculation
        self.last_universe_refresh = time.time()
        self.last_discovery_scan = 0.0  # Track last discovery scan time
        self.btc_trend = 0.0  # BTC trend percentage
        self.volatility_regime = "Normal"  # Low/Normal/High
        self.spread_regime = "Normal"  # Tight/Normal/Wide
        
        # API BUDGET OPTIMIZATION: Enhanced data fetching
        self.orderbook_cache = {}  # Cache orderbooks for top symbols
        self.funding_rates = {}  # Cache funding rates
        # Durable in-flight tracking — crash recovery (Hummingbot pattern)
        from .durable_tracker import DurableTracker
        self.durable_tracker = DurableTracker("data/in_flight_orders.json")
        self.indicators_cache = {}  # Cache computed indicators per symbol (RSI, ADX, ATR, etc.)
        self.last_orderbook_refresh = 0.0
        self._last_positions_ob_refresh = 0.0  # Separate timer to avoid gate conflict
        self.last_funding_refresh = 0.0
        self.orderbook_refresh_interval = 5.0  # Refresh orderbooks every 5s (20 calls = 240/min)
        self._positions_ob_interval = 3.0  # Position orderbooks every 3s (was 1s — 5x API reduction)
        self.funding_refresh_interval = 60.0  # Refresh funding rates every 60s (30 calls = 30/min)
        self.signal_stats = {
            'signals_generated': 0,
            'signals_blocked': 0,
            'last_signal_time': 0
        }
        
        # Scan tracking for detailed metrics
        self.scan_times = deque(maxlen=20)  # Track scan durations
        self.scan_history = deque(maxlen=20)  # Store detailed scan records
        self.last_scan_start = 0.0  # Track when current scan started
        self.last_scan_end = 0.0  # Track when last scan completed
        self.next_scan_time = 0.0  # Track when next scan will occur
        self.is_scanning = False  # Track if currently scanning
        self.startup_period_ended = False  # Track if startup delay period has ended
        self.scan_stats = {
            'total_scans': 0,
            'total_symbols_processed': 0,
            'total_cache_hits': 0,
            'total_cache_misses': 0,
            'total_orderbook_success': 0,
            'total_orderbook_failures': 0
        }
        # FIX: Cumulative filter stats for Signal Health panel
        self.filter_stats_cumulative = {
            'signals_total': 0,      # Total signals found across all scans
            'signals_passed': 0,      # Total signals that passed all filters
            'signals_rejected': 0,    # Total signals rejected
            'avg_score_sum': 0.0,     # Sum of all signal scores (for running average)
            'avg_score_count': 0     # Count of signals with scores (for running average)
        }
        # Minimal metrics counters
        self.metrics = {
            'entries_attempted': 0,
            'entries_opened': 0,
            'rejections_by_reason': {},
            'exits_by_reason': {}
        }
        self._last_metrics_log = time.time()
        
        # DEBUG: Scan cycle counter for replay debugging
        self.debug_scan_counter = 0

    async def cleanup(self):
        """Cleanup resources."""
        try:
            # Close lightweight exchange (separate connection pool)
            if hasattr(self, '_lightweight_ex') and self._lightweight_ex is not None:
                try:
                    await self._lightweight_ex.close()
                except Exception:
                    pass
                self._lightweight_ex = None
            # Cleanup: Close storage connection
            if hasattr(self, 'fast_storage'):
                self.fast_storage.close()
            # Close exchange wrapper
            if self.exchange_wrapper:
                await self.exchange_wrapper.close()
            elif self.exchange:
                # Fallback to direct exchange close (backward compatibility)
                await self.exchange.close()
        except Exception as e:
            self.logger.warning(f"Error during cleanup: {e}")
    
    def _calculate_pnl_pct(self, entry_price: float, current_price: float, side: str) -> float:
        """
        REFACTOR: Helper function to calculate PnL percentage safely.
        Prevents division by zero and provides consistent PnL calculation.
        
        Args:
            entry_price: Entry price
            current_price: Current market price
            side: 'long' or 'short'
        
        Returns:
            PnL percentage (can be negative)
        """
        if not entry_price or entry_price <= 0:
            return 0.0
        
        if side == 'long':
            return ((current_price - entry_price) / entry_price) * 100
        else:  # short
            return ((entry_price - current_price) / entry_price) * 100

    def _get_current_time(self) -> float:
        """Get current time (replay time if in replay mode, else real time)."""
        if self.replay_mode and self._replay_current_time is not None:
            return self._replay_current_time
        return time.time()
    
    async def init_exchange(self):
        """Initialize exchange using exchange abstraction layer."""
        # REPLAY MODE: Skip exchange initialization
        if self.replay_mode:
            self.logger.info("REPLAY MODE: Skipping exchange initialization")
            self.exchange_wrapper = None
            self.order_manager = OrderManager(None)
            self.exit_manager = ExitManager(None, self.order_manager)
            # Initialize ExitPipeline even without exchange
            self.exit_pipeline = ExitPipeline(
                order_manager=self.order_manager,
                exit_manager=self.exit_manager,
                position_registry=self.position_registry
            )
            return
        
        # Create exchange wrapper using factory
        try:
            self.exchange_wrapper = create_exchange(config_module)
            await self.exchange_wrapper.initialize()
        except Exception as e:
            self.logger.error(f"Failed to create exchange wrapper: {e}")
            self.exchange_wrapper = None
            self.order_manager = OrderManager(None)
            self.exit_manager = ExitManager(None, self.order_manager)
            return
        
        # Load markets
        attempts = EXCHANGE_RETRIES
        last_err = None
        for i in range(1, attempts + 1):
            try:
                # Load markets from exchange wrapper
                mkts = await self.exchange_wrapper.load_markets(reload=True)
                
                # Filter for futures/swap markets only (USDT margined linear contracts)
                futures_markets = {}
                for sym, m in mkts.items():
                    # Only include USDT-margined linear swap contracts
                    if "USDT" not in sym:
                        continue
                    
                    # Check if it's a futures market using exchange wrapper
                    if not self.exchange_wrapper.is_futures_market(m):
                        continue
                    
                    # Normalize symbol for internal use
                    normalized_sym = self.exchange_wrapper.normalize_symbol(sym)
                    
                    futures_markets[normalized_sym] = m
                    st = self.universe.stats.get(normalized_sym) or SymbolStats(normalized_sym)
                    prec = m.get("precision", {}) or {}
                    st.amount_step = prec.get("amount", 0.0)
                    st.price_tick = prec.get("price", 0.0)
                    self.universe.stats[normalized_sym] = st
                
                # Apply whitelist filter if configured
                if WHITELIST_SYMBOLS:
                    # Normalize whitelist symbols
                    normalized_whitelist = [self.exchange_wrapper.normalize_symbol(s) for s in WHITELIST_SYMBOLS]
                    futures_markets = {k: v for k, v in futures_markets.items() if k in normalized_whitelist}
                    self.logger.info(f"Applied whitelist filter: {len(futures_markets)} symbols")
                
                self.logger.info(
                    f"Initialized exchange: {EXCHANGE} (futures only)",
                    markets=len(futures_markets),
                    total_markets=len(mkts),
                    dry_run=DRY_RUN
                )
                
                # Log universe size and sample (one-time after initial population in LIVE mode)
                if self.universe.stats and not hasattr(self, '_universe_logged'):
                    universe_symbols = list(self.universe.stats.keys())
                    sample_size = min(5, len(universe_symbols))
                    self.logger.info(
                        f"[UNIVERSE] Size={len(universe_symbols)} | Sample={universe_symbols[:sample_size]}"
                    )
                    self._universe_logged = True
                
                # Initialize order manager and exit manager with exchange wrapper
                self.order_manager = OrderManager(self.exchange_wrapper)
                # Wire DurableTracker for crash recovery
                self.order_manager._durable_tracker = self.durable_tracker
                self.exit_manager = ExitManager(self.exchange_wrapper, self.order_manager)
                
                # NEW ARCHITECTURE: Initialize unified ExitPipeline
                self.exit_pipeline = ExitPipeline(
                    order_manager=self.order_manager,
                    exit_manager=self.exit_manager,
                    position_registry=self.position_registry
                )
                
                # Store reference to underlying exchange for backward compatibility
                # (Some code may still reference self.exchange directly)
                if hasattr(self.exchange_wrapper, 'exchange'):
                    self.exchange = self.exchange_wrapper.exchange
                else:
                    self.exchange = self.exchange_wrapper
                
                return
            except Exception as e:
                last_err = e
                error_msg = f"load_markets attempt {i}/{attempts} failed: {type(e).__name__}: {str(e)}"
                self.ctrl.recent_errors.append(error_msg)
                self.logger.warning(
                    f"Exchange initialization retry {i}/{attempts}",
                    error_type=type(e).__name__,
                    error_message=str(e)
                )
                await asyncio.sleep(RETRY_DELAY_MULTIPLIER * i)
        
        # If all retries failed, log error but continue with cached data
        self.logger.error(
            f"Exchange initialization failed after {attempts} attempts. Bot will continue with cached data only.",
            error_type=type(last_err).__name__ if last_err else "Unknown",
            error_message=str(last_err) if last_err else "Unknown error"
        )
        self.logger.warning("Bot will run in read-only mode using cached/stored data. No live trading possible.")
        
        # Close exchange connection
        if self.exchange_wrapper:
            try:
                await self.exchange_wrapper.close()
            except (AttributeError, RuntimeError, OSError) as e:
                # REFACTOR: More specific exception handling with logging
                self.logger.warning(f"Error closing exchange connection: {e}")
        self.exchange_wrapper = None
        self.exchange = None  # Set to None to indicate no connection
        
        # Initialize order/exit managers with None (they'll handle it)
        self.order_manager = OrderManager(None)
        self.exit_manager = ExitManager(None, self.order_manager)
        
        # NEW ARCHITECTURE: Initialize ExitPipeline even without exchange (for fallback)
        self.exit_pipeline = ExitPipeline(
            order_manager=self.order_manager,
            exit_manager=self.exit_manager,
            position_registry=self.position_registry
        )

    def health_check(self) -> Dict[str, Any]:
        """
        Return bot health status.
        
        Returns:
            Dictionary containing health metrics
        """
        return {
            'exchange_connected': self.exchange is not None,
            'uptime_seconds': time.time() - self.started_at,
            'positions_count': len(self.positions),
            'recent_errors_count': len(self.recent_errors),
            'last_scan_time': time.time() - self.last_signal_scan if self.last_signal_scan else None,
            'universe_size': len(self.universe.active) if hasattr(self.universe, 'active') else 0,
            'current_regime': self.current_regime,
            'equity': self.equity_now()
        }

    def equity_now(self) -> float:
        """
        Calculate current equity.
        
        CANONICAL ACCOUNTING: Equity = Starting Balance + Realized PnL + Realized Funding + Unrealized PnL
        
        Note: realized_pnl_total contains NET PnL (after costs),
        so we don't subtract fees again here. Fees are tracked
        separately in realized_fees_total for transparency.
        """
        from .accounting import calculate_total_unrealized_pnl, get_current_price_from_bot, calculate_equity
        
        # Calculate unrealized PnL from open positions
        unrealized_pnl = 0.0
        if self.positions:
            try:
                unrealized_pnl = calculate_total_unrealized_pnl(
                    self.positions,
                    lambda symbol: get_current_price_from_bot(self, symbol)
                )
            except Exception as e:
                self.logger.debug(f"Error calculating unrealized PnL: {e}")
        
        # Calculate total equity using canonical accounting
        equity = calculate_equity(
            self.start_equity,
            self.realized_pnl_total,
            self.realized_funding_total,
            unrealized_pnl
        )
        
        # Update equity peak (for drawdown calculation)
        if equity > self.equity_peak:
            self.equity_peak = equity
        
        return equity
    
    def get_drawdown_pct(self) -> float:
        """
        Calculate current drawdown percentage.
        
        Returns:
            Drawdown percentage (negative if below peak, 0 if at/above peak)
        """
        equity = self.equity_now()
        if self.equity_peak > 0:
            drawdown_pct = ((equity - self.equity_peak) / self.equity_peak) * 100
            return min(0.0, drawdown_pct)  # Only negative values (drawdown)
        return 0.0
    
    def check_drawdown_circuit_breaker(self) -> bool:
        """
        Check if drawdown circuit breaker should trigger.
        
        Returns:
            True if circuit breaker should trigger (stop trading), False otherwise
        """
        if not DRAWDOWN_CIRCUIT_BREAKER_ENABLED:
            return False
        
        if self.circuit_breaker_triggered:
            return True  # Already triggered, stay stopped
        
        drawdown_pct = self.get_drawdown_pct()
        
        if drawdown_pct <= -MAX_DRAWDOWN_PCT:
            self.circuit_breaker_triggered = True
            self.logger.critical(
                f"DRAWDOWN CIRCUIT BREAKER TRIGGERED: Drawdown {drawdown_pct:.2f}% exceeds maximum {MAX_DRAWDOWN_PCT}%",
                drawdown_pct=drawdown_pct,
                max_drawdown_pct=MAX_DRAWDOWN_PCT,
                equity=self.equity_now(),
                start_equity=self.start_equity,
                equity_peak=self.equity_peak
            )
            return True
        
        return False
    
    def _calculate_market_regimes(self):
        """
        Calculate volatility and spread regimes from active symbols.
        Uses median and percentile-based thresholds for robustness.
        """
        if not self.universe.active:
            return

        spreads = []
        volatilities = []
        
        # Collect data from top symbols (more reliable)
        for symbol in self.universe.active[:50]:
            stats = self.universe.stats.get(symbol)
            if stats:
                spread = stats.spread_bps
                vol = abs(getattr(stats, 'pct_change_24h', 0.0))
                # Filter out outliers
                if 0 < spread < 200 and 0 <= vol < 20:
                    spreads.append(spread)
                    volatilities.append(vol)
        
        # Need at least 5 symbols for reliable regime detection
        # Ensure both lists have sufficient data and are in sync
        if len(spreads) < 5 or len(volatilities) < 5 or len(spreads) != len(volatilities):
            return
        
        # Use median for robustness (less affected by outliers)
        spreads_sorted = sorted(spreads)
        volatilities_sorted = sorted(volatilities)
        n = len(spreads_sorted)
        median_spread = spreads_sorted[n // 2]
        median_vol = volatilities_sorted[n // 2]
        
        # Use 25th and 75th percentiles for thresholds
        p25_spread = spreads_sorted[n // 4]
        p75_spread = spreads_sorted[3 * n // 4]
        p25_vol = volatilities_sorted[n // 4]
        p75_vol = volatilities_sorted[3 * n // 4]
        
        # Spread regime: Use median with percentile-based thresholds
        # Tight: median < 25 bps OR p75 < 30 bps
        # Wide: median > 45 bps OR p75 > 60 bps
        if median_spread < 25 or p75_spread < 30:
            self.spread_regime = "Tight"
        elif median_spread > 45 or p75_spread > 60:
            self.spread_regime = "Wide"
        else:
            self.spread_regime = "Normal"
        
        # Volatility regime: Use median with percentile-based thresholds
        # Low: median < 0.8% OR p75 < 1.2%
        # High: median > threshold OR p75 > threshold
        if median_vol < 0.8 or p75_vol < 1.2:
            self.volatility_regime = "Low"
        elif median_vol > HIGH_VOLATILITY_MEDIAN_THRESHOLD or p75_vol > HIGH_VOLATILITY_P75_THRESHOLD:
            self.volatility_regime = "High"
        else:
            self.volatility_regime = "Normal"

    async def refresh_universe(self) -> None:
        # self.logger.debug("refresh_universe called")
        loop_start = self._get_current_time()
        refresh_now = loop_start
        
        # REPLAY MODE: Use replay feed for ticker data
        if self.replay_mode and self.replay_feed:
            try:
                # Get ticker data from replay feed for all symbols
                tickers = {}
                for symbol in self.replay_feed.candles_by_symbol.keys():
                    ticker_data = self.replay_feed.get_ticker_data(symbol)
                    if ticker_data:
                        # Normalize symbol for internal use
                        normalized_sym = symbol  # Assume already normalized
                        tickers[normalized_sym] = ticker_data
                
                # Update universe stats from replay tickers
                for sym, t in tickers.items():
                    if not sym or not t:
                        continue
                    
                    bid = t.get("bid", 0.0)
                    ask = t.get("ask", 0.0)
                    last = t.get("last", 0.0)
                    mark = t.get("mark", last)
                    vol_quote = t.get("quoteVolume", 0.0)
                    
                    # Calculate spread
                    spread_bps = 9999.0
                    if bid and ask and ask > 0:
                        mid = MID_PRICE_FACTOR * (bid + ask)
                        if mid > 0:
                            spread_bps = abs(ask - bid) / mid * 1e4
                    
                    # Get or create stats
                    st = self.universe.stats.get(sym)
                    if not st:
                        st = SymbolStats(sym)
                        self.universe.stats[sym] = st
                    
                    st.bid = bid
                    st.ask = ask
                    st.last = last
                    st.mark = float(mark or 0.0)
                    st.vol_quote = vol_quote
                    st.spread_bps = float(spread_bps)
                    st.heat = (math.log10(vol_quote + 1.0) - 5.0) - (spread_bps / 200.0)
                    st.last_seen = refresh_now
                    # Use percentage from ticker data if available, else 0.0
                    st.pct_change_24h = float(t.get("percentage", 0.0) or 0.0)
                    
                    # self.logger.debug(f"Updated stats for {sym}")
                
                # Update ticker cache
                self.ticker_cache.batch_set(tickers)
                
                # REPLAY MODE: Populate orderbook cache with synthetic orderbooks
                for sym in self.replay_feed.candles_by_symbol.keys():
                    snapshot = self.replay_feed.get_market_snapshot(sym)
                    if snapshot and 'orderbook' in snapshot:
                        self.orderbook_cache[sym] = {
                            'data': snapshot['orderbook'],
                            'timestamp': refresh_now
                        }
                
                # Log universe size and sample (one-time after initial population in REPLAY mode)
                if self.universe.stats and not hasattr(self, '_universe_logged'):
                    universe_symbols = list(self.universe.stats.keys())
                    sample_size = min(5, len(universe_symbols))
                    self.logger.info(
                        f"[UNIVERSE] Size={len(universe_symbols)} | Sample={universe_symbols[:sample_size]}"
                    )
                    self._universe_logged = True
                
                # FORCE ROTATION IN REPLAY MODE
                if not self.universe.active:
                     self.universe.rotate(force_full_rotation=True)
                     # self.logger.debug(f"Active symbols after rotation: {self.universe.active}")
                
                return
            except Exception as e:
                self.logger.warning(f"Replay universe refresh failed: {e}")
                return
        
        # Check if exchange wrapper is available
        if not self.exchange_wrapper:
            # OPTIMIZATION: Removed debug log (not critical)
            return
        
        try:
            self.budget.tick(1, "rot")
            self.api_call_times.append(refresh_now)
            # Use exchange wrapper to fetch tickers
            # Binance uses defaultType="future" from options
            tickers = await self.exchange_wrapper.fetch_tickers()
            
            # CRITICAL DEBUG: Log ticker fetch results
            self.logger.info(
                f"🔄 UNIVERSE REFRESH | tickers_fetched={len(tickers)} | "
                f"markets_loaded={len(self.exchange_wrapper.markets) if hasattr(self.exchange_wrapper, 'markets') else 0}"
            )
            
            # OPTIMIZATION: Filter for futures/swap markets only (optimized filtering)
            # Pre-compute market lookup for faster access
            markets_dict = self.exchange_wrapper.markets if hasattr(self.exchange_wrapper, 'markets') and self.exchange_wrapper.markets else {}
            futures_tickers = {}
            
            for sym, t in tickers.items():
                # OPTIMIZATION: Early exit for None values
                if not sym or not t:
                    continue
                
                # OPTIMIZATION: Fast USDT check first (most common filter)
                if "USDT" not in sym:
                    continue
                
                # OPTIMIZATION: Check market type from exchange wrapper's markets first (fastest)
                market = markets_dict.get(sym)
                if market:
                    # Use exchange wrapper's method if available (most reliable)
                    if hasattr(self.exchange_wrapper, 'is_futures_market') and not self.exchange_wrapper.is_futures_market(market):
                        continue
                else:
                    # Fallback: Check ticker info (slower, but necessary if market not loaded)
                    if isinstance(t, dict):
                        info = t.get("info", {})
                        market_type = info.get("type") if isinstance(info, dict) else t.get("type", "")
                        if market_type and market_type not in ("swap", "future"):
                            continue
                
                # Normalize symbol for internal use
                normalized_sym = self.exchange_wrapper.normalize_symbol(sym)
                futures_tickers[normalized_sym] = t

            # WHITELIST ENFORCEMENT: only keep whitelisted symbols if configured
            if WHITELIST_SYMBOLS:
                normalized_whitelist = [self.exchange_wrapper.normalize_symbol(s) for s in WHITELIST_SYMBOLS]
                futures_tickers = {k: v for k, v in futures_tickers.items() if k in normalized_whitelist}

            # LATENCY OPTIMIZATION: Batch update cache and storage (futures only)
            self.ticker_cache.batch_set(futures_tickers)
            self.fast_storage.batch_save(futures_tickers)
        except Exception as e:
            self.ctrl.recent_errors.append(f"tickers ERR {type(e).__name__}")
            self.logger.warning(f"Universe refresh failed: {type(e).__name__}: {str(e)}. Using cached data.")
            return
        
        # API BUDGET OPTIMIZATION: Refresh orderbooks for top symbols in parallel
        now = self._get_current_time()
        if now - self.last_orderbook_refresh >= self.orderbook_refresh_interval:
            await self._refresh_orderbooks_for_top_symbols()
            self.last_orderbook_refresh = now
        
        # API BUDGET OPTIMIZATION: Refresh funding rates periodically
        if now - self.last_funding_refresh >= self.funding_refresh_interval:
            await self._refresh_funding_rates()
            self.last_funding_refresh = now

        cnt = 0
        # OPTIMIZATION: Pre-compute constants outside loop
        mid_price_factor = MID_PRICE_FACTOR
        
        for sym, t in futures_tickers.items():
            # OPTIMIZATION: Cache ticker info lookup (avoid repeated dict access)
            ticker_info = t.get("info", {}) if isinstance(t, dict) else {}
            
            # CRITICAL: Binance Futures tickers may have bid/ask as None
            # Extract raw values first, then apply fallback logic
            bid_raw = t.get("bid")
            ask_raw = t.get("ask")
            last_raw = t.get("last") or 0.0
            last = float(last_raw)
            
            # If bid/ask are None or <= 0, use last price as fallback (common for futures)
            if bid_raw is None or (isinstance(bid_raw, (int, float)) and float(bid_raw) <= 0):
                bid = last
            else:
                bid = float(bid_raw)
            
            if ask_raw is None or (isinstance(ask_raw, (int, float)) and float(ask_raw) <= 0):
                ask = last
            else:
                ask = float(ask_raw)
            
            # OPTIMIZATION: Cache info lookups
            mark = (
                ticker_info.get("fairPx")
                or ticker_info.get("markPrice")
                or last
            )
            
            # OPTIMIZATION: Cache volume lookups
            vol_quote = (
                t.get("quoteVolume")
                or ticker_info.get("amount24h")
                or ticker_info.get("quote_volume")
                or 0.0
            )
            try:
                vol_quote = float(vol_quote)
            except (TypeError, ValueError):
                # REFACTOR: Handle invalid/missing volume data
                vol_quote = 0.0

            # OPTIMIZATION: Calculate spread more efficiently
            spread_bps = 9999.0
            if bid and ask and ask > 0:
                mid = mid_price_factor * (bid + ask)
                if mid > 0:
                    spread_bps = abs(ask - bid) / mid * 1e4

            # OPTIMIZATION: Get or create stats once
            st = self.universe.stats.get(sym)
            if not st:
                st = SymbolStats(sym)
                self.universe.stats[sym] = st
            
            # OPTIMIZATION: Batch update stats
            st.bid = bid
            st.ask = ask
            st.last = last
            st.mark = float(mark or 0.0)
            st.vol_quote = vol_quote
            st.spread_bps = float(spread_bps)
            st.heat = (math.log10(vol_quote + 1.0) - 5.0) - (spread_bps / 200.0)
            st.last_seen = refresh_now  # OPTIMIZATION: Use cached time
            
            # OPTIMIZATION: Extract 24h price change percentage (cache info lookup)
            pct_change = t.get("percentage") or ticker_info.get("priceChangePercent") or 0.0
            try:
                st.pct_change_24h = float(pct_change)
            except (ValueError, TypeError):
                st.pct_change_24h = 0.0
            
            cnt += 1

        self.symbols_scanned_last = cnt
        
        # CRITICAL DEBUG: Log stats population
        self.logger.info(
            f"🔄 UNIVERSE STATS POPULATED | stats_count={len(self.universe.stats)} | "
            f"futures_tickers={len(futures_tickers)} | samples={list(self.universe.stats.keys())[:5] if self.universe.stats else []}"
        )
        
        # Calculate BTC trend BEFORE rotation (needed for correlation scoring)
        # Try both formats: BTC/USDT:USDT (full) and BTC/USDT (short)
        btc_stats = self.universe.stats.get("BTC/USDT:USDT") or self.universe.stats.get("BTC/USDT")
        if btc_stats:
            self.btc_trend = getattr(btc_stats, 'pct_change_24h', 0.0)
            # Set BTC trend in universe for correlation scoring
            self.universe.set_btc_trend(self.btc_trend)
        else:
            self.universe.set_btc_trend(0.0)
        
        # Rotate universe (replace stale symbols with fresh candidates)
        # Check if rotation is needed (periodic check to avoid excessive rotation)
        from .config import ROTATION_CHECK_INTERVAL_SEC, MAX_ACTIVE_SYMBOLS
        now = time.time()
        time_since_rotation = now - self.universe.last_rotation_time
        
        # First rotation or if active list is too small: do full rotation
        if self.universe.last_rotation_time == 0.0 or len(self.universe.active) < 50:
            self.universe.rotate(force_full_rotation=True)
        elif time_since_rotation >= ROTATION_CHECK_INTERVAL_SEC:
            # Periodic smart rotation: only replace stale symbols (preserves good symbols)
            self.universe.rotate(force_full_rotation=False)
    
        # Track loop time
        loop_time = time.time() - loop_start
        self.loop_times.append(loop_time)
        self.last_universe_refresh = time.time()
        
        # Calculate volatility and spread regimes
        self._calculate_market_regimes()
    
    async def _refresh_orderbooks_for_top_symbols(self):
        """Refresh orderbooks for top symbols to improve signal quality."""
        # REPLAY MODE: Use synthetic orderbooks from replay feed
        if self.replay_mode and self.replay_feed:
            top_symbols = self.universe.active[:20]
            refresh_now = self._get_current_time()
            for symbol in top_symbols:
                snapshot = self.replay_feed.get_market_snapshot(symbol)
                if snapshot and 'orderbook' in snapshot:
                    self.orderbook_cache[symbol] = {
                        'data': snapshot['orderbook'],
                        'timestamp': refresh_now
                    }
            return
        
        if not self.exchange_wrapper:
            return
        # Note: Orderbooks are fetched in live mode only (not in DRY_RUN to save API budget)
        
        # Get top 20 symbols (most likely to trade)
        top_symbols = self.universe.active[:20]
        if not top_symbols:
            return
        
        # Fetch orderbooks in parallel (batch of 10)
        async def fetch_orderbook(symbol):
            try:
                remaining = self.budget.remaining()
                if remaining < 5:  # Reserve some budget
                    return None
                
                self.budget.tick(1, "orderbook")
                # Denormalize symbol for exchange API
                denormalized_symbol = self.exchange_wrapper.denormalize_symbol(symbol)
                orderbook = await asyncio.wait_for(
                    self.exchange_wrapper.fetch_order_book(denormalized_symbol, limit=5),  # limit=5 = weight 1 (vs weight 5 for limit=20)
                    timeout=0.3
                )
                return (symbol, orderbook)
            except Exception:
                return None
        
        # OPTIMIZATION: Increased batch size from 10 to 20 for better parallelization
        batch_size = 20
        # OPTIMIZATION: Cache time once per batch
        batch_timestamp = time.time()
        for i in range(0, len(top_symbols), batch_size):
            batch = top_symbols[i:i+batch_size]
            results = await asyncio.gather(*[fetch_orderbook(s) for s in batch], return_exceptions=True)
            
            # OPTIMIZATION: Update timestamp once per batch
            batch_timestamp = time.time()
            for result in results:
                if result and isinstance(result, tuple):
                    symbol, orderbook = result
                    self.orderbook_cache[symbol] = {
                        'data': orderbook,
                        'timestamp': batch_timestamp  # OPTIMIZATION: Use cached time
                    }
    
    async def _refresh_funding_rates(self):
        """
        Refresh funding rates for active symbols.
        OPTIMIZED: Uses Binance Futures batch premium index endpoint when possible.
        """
        if not self.exchange_wrapper or DRY_RUN:
            return
        
        # Get active symbols
        active_symbols = self.universe.active[:30]  # Top 30 symbols
        if not active_symbols:
            return
        
        # OPTIMIZATION: Try to use Binance Futures batch premium index endpoint
        # Binance Futures /fapi/v1/premiumIndex can get funding rates for all symbols in one call
        # Check if exchange wrapper supports batch funding rate fetch
        if hasattr(self.exchange_wrapper, 'fetch_funding_rates_batch'):
            try:
                remaining = self.budget.remaining()
                if remaining >= 1:  # Only 1 API call for batch
                    self.budget.tick(1, "funding")
                    denormalized_symbols = [self.exchange_wrapper.denormalize_symbol(s) for s in active_symbols]
                    funding_data = await asyncio.wait_for(
                        self.exchange_wrapper.fetch_funding_rates_batch(denormalized_symbols),
                        timeout=1.0
                    )
                    # Update funding rates cache
                    batch_timestamp = time.time()
                    for symbol, rate in funding_data.items():
                        if rate is not None:
                            # Map back to normalized symbol
                            normalized_symbol = self.exchange_wrapper.normalize_symbol(symbol)
                            self.funding_rates[normalized_symbol] = {
                                'rate': float(rate),
                                'timestamp': batch_timestamp
                            }
                    return  # Success with batch method
            except Exception as e:
                # Fallback to individual calls if batch method fails
                # OPTIMIZATION: Removed debug log (fallback is expected behavior)
                pass
        
        # Fallback: Fetch funding rates in parallel (batch of 20 - OPTIMIZED from 10)
        async def fetch_funding(symbol):
            try:
                remaining = self.budget.remaining()
                if remaining < 5:
                    return None
                
                self.budget.tick(1, "funding")
                # OPTIMIZATION: Use premium index endpoint directly if available
                # Binance Futures /fapi/v1/premiumIndex is faster than full ticker
                denormalized_symbol = self.exchange_wrapper.denormalize_symbol(symbol)
                
                # Try to get funding rate from ticker info (CCXT handles this)
                ticker = await asyncio.wait_for(
                    self.exchange_wrapper.fetch_ticker(denormalized_symbol),
                    timeout=0.2
                )
                funding_rate = ticker.get('info', {}).get('fundingRate') or ticker.get('fundingRate')
                if funding_rate:
                    return (symbol, float(funding_rate))
                return None
            except Exception:
                return None
        
        # OPTIMIZATION: Increased batch size from 10 to 20 for better parallelization
        batch_size = 20
        # OPTIMIZATION: Cache time once per batch
        batch_timestamp = time.time()
        for i in range(0, len(active_symbols), batch_size):
            batch = active_symbols[i:i+batch_size]
            results = await asyncio.gather(*[fetch_funding(s) for s in batch], return_exceptions=True)
            
            # OPTIMIZATION: Update timestamp once per batch
            batch_timestamp = time.time()
            for result in results:
                if result and isinstance(result, tuple):
                    symbol, rate = result
                    self.funding_rates[symbol] = {
                        'rate': rate,
                        'timestamp': batch_timestamp  # OPTIMIZATION: Use cached time
                    }
    
    async def _execute_single_exit(self, symbol: str, position: dict, reason: str,
                                    target_price: float, exit_size_pct: float = 1.0):
        """Execute exit immediately via exit_manager (direct, reliable).
        Runs as background task — survives monitor timeout."""
        if not self.exit_manager:
            return
        try:
            # Cancel exchange SL/TP orders (skip virtual IDs)
            sl_id = position.get('sl_order_id')
            tp_id = position.get('tp_order_id')
            for oid in (sl_id, tp_id):
                if oid and oid not in ("PENDING", "EXISTS", "RECONCILED", "ATOMIC"):
                    try:
                        await self.order_manager.cancel_order(symbol, oid)
                    except Exception:
                        pass

            # Direct exit via exit_manager (proven reliable — RE exit worked)
            result = await self.exit_manager.exit_position(
                symbol=symbol, position=position, reason=reason)
            if result.success:
                pnl = result.net_pnl or 0
                self.realized_pnl_total += pnl
                if pnl > 0:
                    self.win_count += 1; self.gross_win += pnl
                else:
                    self.loss_count += 1; self.gross_loss += abs(pnl)
                self.logger.info(
                    f"EXIT: {symbol} | {reason} | size={position.get('size',0):.4f} | "
                    f"price={result.exit_price:.2f} | pnl={pnl:.2f}")
                self.metrics['exits_by_reason'][reason] = \
                    self.metrics['exits_by_reason'].get(reason, 0) + 1
                # Remove from tracking
                if symbol in self.positions:
                    del self.positions[symbol]
                self.position_manager.record_exit(symbol, pnl > 0)
            else:
                self.logger.warning(
                    f"[EXIT_FAIL] {symbol} reason={reason} error={result.error}")
        except Exception as e:
            self.logger.error(f"[EXIT_EXEC_ERROR] {symbol}: {type(e).__name__}: {e}")

    async def _prefetch_news(self, ns, symbols: list):
        """Background task: fetch news sentiment for all symbols, log summary."""
        try:
            results = await ns.prefetch_all(symbols, batch_size=10, batch_delay=2.0)
            if results:
                bullish = sum(1 for r in results.values() if r.sentiment == 'BULLISH')
                bearish = sum(1 for r in results.values() if r.sentiment == 'BEARISH')
                neutral = sum(1 for r in results.values() if r.sentiment == 'NEUTRAL')
                self.logger.info(
                    f'[MARKET] {len(results)} tokens scanned: '
                    f'{bullish}▲BULLISH {bearish}▼BEARISH {neutral}─NEUTRAL')
        except Exception as e:
            self.logger.debug(f"[NEWS] prefetch skipped: {e}")

    async def _fetch_positions_lightweight(self) -> dict:
        """Fetch open positions using a dedicated lightweight exchange (separate connection pool).
        Returns {symbol: {'contracts': abs_amt, 'side': 'long'|'short', 'entryPrice': float}}."""
        if not self.exchange_wrapper or not self.exchange_wrapper.exchange:
            return {}
        try:
            # Lazy-init or recycle lightweight exchange every 5 min (keeps connection pool fresh)
            if not hasattr(self, '_lightweight_ex') or self._lightweight_ex is None or \
               not hasattr(self, '_lightweight_ex_created') or \
               time.time() - self._lightweight_ex_created > 300:
                import ccxt.async_support as ccxt_async
                # Close old instance if exists
                if hasattr(self, '_lightweight_ex') and self._lightweight_ex is not None:
                    try:
                        await self._lightweight_ex.close()
                    except Exception:
                        pass
                self._lightweight_ex = ccxt_async.binanceusdm({
                    'apiKey': self.exchange_wrapper.api_key,
                    'secret': self.exchange_wrapper.api_secret,
                    'enableRateLimit': True,  # Prevent Binance hard rate limits
                    'timeout': 10000,
                })
                self._lightweight_ex_created = time.time()
            pos_list = await asyncio.wait_for(
                self._lightweight_ex.fetch_positions(), timeout=10.0)
            result = {}
            for p in (pos_list or []):
                contracts = abs(float(p.get('contracts', 0)))
                if contracts > 0:
                    sym = p.get('symbol', '')
                    sym = self.exchange_wrapper.normalize_symbol(sym)
                    result[sym] = {
                        'contracts': contracts,
                        'side': p.get('side', 'long'),
                        'entryPrice': float(p.get('entryPrice', 0)),
                    }
            return result
        except Exception:
            return {}

    async def _refresh_orderbooks_for_positions(self):
        """Refresh orderbooks for open positions for better exit decisions."""
        if not self.exchange_wrapper or DRY_RUN or not self.positions:
            return
        
        # Fetch orderbooks for all open positions
        async def fetch_pos_orderbook(symbol):
            try:
                remaining = self.budget.remaining()
                if remaining < 3:
                    return None
                
                self.budget.tick(1, "orderbook")
                # Denormalize symbol for exchange API
                denormalized_symbol = self.exchange_wrapper.denormalize_symbol(symbol)
                orderbook = await asyncio.wait_for(
                    self.exchange_wrapper.fetch_order_book(denormalized_symbol, limit=5),
                    timeout=0.2
                )
                return (symbol, orderbook)
            except Exception:
                return None
        
        # OPTIMIZATION: Fetch in parallel, cache time once
        symbols = list(self.positions.keys())
        results = await asyncio.gather(*[fetch_pos_orderbook(s) for s in symbols], return_exceptions=True)
        
        # OPTIMIZATION: Cache time once for all cache updates
        cache_timestamp = time.time()
        for result in results:
            if result and isinstance(result, tuple):
                symbol, orderbook = result
                self.orderbook_cache[symbol] = {
                    'data': orderbook,
                    'timestamp': cache_timestamp
                }
    
    async def scan_and_enter_signals(self):
        """Scan for trading signals and enter positions."""
        global asyncio  # Fix: Python 3.14 closure scoping — consider passing loop explicitly — inner process_symbol ref forces local binding
        # DEBUG: Log strategy cycle entry point
        self.logger.info("[DEBUG] Strategy cycle triggered - scan_and_enter_signals() called")
        # DEBUG: Increment scan counter and log every 100 cycles
        self.debug_scan_counter += 1
        if self.debug_scan_counter % 100 == 0:
            self.logger.info(f"[DEBUG] Scan cycle #{self.debug_scan_counter}")

        # CIRCUIT BREAKER: pause after 3 consecutive losses
        if not hasattr(self, '_recent_exit_pnls'):
            self._recent_exit_pnls = []
        if len(self._recent_exit_pnls) >= 3 and all(p <= 0 for p in self._recent_exit_pnls[-3:]):
            if not hasattr(self, '_circuit_paused_until'):
                self._circuit_paused_until = 0
            now = time.time()
            if now < self._circuit_paused_until:
                return  # Still paused
            # Start/reset pause
            self._circuit_paused_until = now + 1800  # 30 min
            self.logger.warning("[BREAKER] 3 consecutive losses — pausing 30 min")
            return

        from .config import (
            MAX_LATENCY_MS, MIN_VOLUME_24H, MIN_SPREAD_BPS, DRY_RUN
        )
        
        if not self.order_manager:
            return
        
        # Track scan start
        scan_start_time = time.time()
        self.last_scan_start = scan_start_time
        self.is_scanning = True
        
        # STARTUP DELAY: Informational only - never blocks entries
        # REPLAY MODE: Skip time-based warmup (entries allowed immediately)
        if self.replay_mode:
            # No time-based startup warmup in replay; allow entries immediately
            in_startup_period = False
            if not self.startup_period_ended:
                self.startup_period_ended = True
                self.logger.info("✅ REPLAY MODE: Startup warmup skipped (entries enabled immediately)")
        else:
            # LIVE MODE: Calculate warmup time for informational logging only
            time_since_start = scan_start_time - self.started_at
            in_startup_period = time_since_start < STARTUP_DELAY_SEC if STARTUP_DELAY_SEC > 0 else False
            
            # FIX: Never block entries - warmup is informational only
            # Log startup period status but don't prevent entry evaluation
            if in_startup_period and not self.startup_period_ended:
                remaining = STARTUP_DELAY_SEC - time_since_start
                self.logger.info(
                    f"⏳ STARTUP PERIOD: Building signal history (entries still enabled) | "
                    f"time_elapsed={time_since_start:.1f}s | remaining={remaining:.1f}s"
                )
            elif not self.startup_period_ended and STARTUP_DELAY_SEC > 0:
                # Startup period just ended (only log if warmup was actually enabled)
                self.startup_period_ended = True
                self.logger.info(
                    f"✅ STARTUP PERIOD ENDED | "
                    f"startup_duration={time_since_start:.1f}s | "
                    f"signal_history_size={len(self.signal_history)}"
                )
        
        # FIX: Always allow entries - warmup never blocks
        in_startup_period = False
        
        # OPTIMIZATION: Cache equity calculation (used multiple times)
        equity = self.equity_now()
        current_positions = len(self.positions)
        
        # SIGNAL CONFIRMATION WINDOW (SCW): Update waiting signals with new bar data
        from .config import USE_SIGNAL_CONFIRMATION, R_BAR_SCAN_CYCLE_SEC
        if USE_SIGNAL_CONFIRMATION:
            # Cleanup stale signals (older than 5 minutes)
            self.signal_confirmation.cleanup_stale_signals(max_age_sec=300.0)
            
            # Update all waiting signals (bar closed = True since we're in a new scan cycle)
            for symbol in list(self.signal_confirmation.waiting_signals.keys()):
                waiting = self.signal_confirmation.waiting_signals.get(symbol)
                if not waiting:
                    continue
                
                # Get current market data for confirmation check
                stats = self.universe.stats.get(symbol)
                if not stats:
                    continue
                
                current_price = stats.mark or stats.last
                spread_bps = stats.spread_bps
                volume_24h = stats.vol_quote
                
                # Get orderbook data if available (for high/low)
                orderbook = self.orderbook_cache.get(symbol, {}).get('data')
                low = None
                high = None
                if orderbook:
                    bids = orderbook.get('bids', [])
                    asks = orderbook.get('asks', [])
                    if bids and asks:
                        low = bids[-1][0] if bids else None  # Lowest bid
                        high = asks[-1][0] if asks else None  # Highest ask
                
                # Get ATR if available
                atr_pct = None
                if hasattr(stats, 'atr_pct'):
                    atr_pct = stats.atr_pct
                
                # CRITICAL NOTE: Opposite signal detection disabled (incomplete feature)
                # To enable: Check signal generator for opposite-side signals and pass score here
                opposite_signal_score = None  # Disabled: would need signal generator integration
                
                # CRITICAL NOTE: Volume MA calculation disabled (incomplete feature)
                # To enable: Calculate rolling volume MA (e.g., 20-period) and pass here
                volume_ma = None  # Disabled: would need volume history tracking
                
                # Update waiting signal
                is_ready, updated_waiting = self.signal_confirmation.update_waiting_signal(
                    symbol=symbol,
                    current_price=current_price,
                    low=low,
                    high=high,
                    spread_bps=spread_bps,
                    atr_pct=atr_pct,
                    volume=volume_24h,
                    volume_ma=volume_ma,
                    opposite_signal_score=opposite_signal_score,
                    bar_closed=True  # New scan cycle = bar closed
                )
                
                    # REMOVED: Individual signal confirmation log (aggregated in summary)
        
        # Get active symbols from universe (scan all active symbols, uses cached data - no budget impact)
        # OPTIMIZATION: Scan ALL active symbols since scanning uses cached ticker data and doesn't consume API budget
        # This maximizes target discovery while staying within budget constraints
        active_symbols = self.universe.active  # Scan all active symbols (uses cached data, no budget impact)
        
        # DEBUG ONLY: Log universe state (too frequent for LOG panel)
        self.logger.debug(
            f"SCAN START | active_symbols={len(active_symbols)} | "
            f"universe_size={len(self.universe.stats)} | "
            f"current_positions={current_positions} | equity={equity:.2f}"
        )
        
        # OPTION 3: Smart Discovery - Add discovery candidates periodically
        from .config import (
            DISCOVERY_SCAN_INTERVAL_SEC, DISCOVERY_SYMBOLS_PER_CYCLE,
            DISCOVERY_MIN_VOLUME_24H, DISCOVERY_MAX_SPREAD_BPS, DISCOVERY_MIN_MOMENTUM_PCT,
            STALE_SYMBOL_THRESHOLD_SEC
        )
        
        now = time.time()
        discovery_symbols = []
        if now - self.last_discovery_scan >= DISCOVERY_SCAN_INTERVAL_SEC:
            # Get discovery candidates (never-scanned, stale, or high-momentum symbols)
            discovery_symbols = self.universe.get_discovery_candidates(
                max_candidates=DISCOVERY_SYMBOLS_PER_CYCLE,
                min_volume=DISCOVERY_MIN_VOLUME_24H,
                max_spread_bps=DISCOVERY_MAX_SPREAD_BPS,
                min_momentum_pct=DISCOVERY_MIN_MOMENTUM_PCT,
                stale_threshold_sec=STALE_SYMBOL_THRESHOLD_SEC
            )
            self.last_discovery_scan = now
        
        # Combine active and discovery symbols (remove duplicates)
        all_symbols_to_scan = list(set(active_symbols + discovery_symbols))
        total_symbols = len(all_symbols_to_scan)
        
        # OPTIMIZATION: Pre-set leverage for symbols to reduce entry latency
        if self.order_manager and all_symbols_to_scan:
            try:
                from .config import LEVERAGE_BASE
                # Pre-set leverage in background (non-blocking)
                self.order_manager.pre_set_leverage(all_symbols_to_scan, LEVERAGE_BASE)
            except Exception:
                pass  # Non-critical
        
        signals_found = 0
        entries_attempted = 0
        signals_blocked_circuit_breaker = 0
        signals_blocked_position_manager = 0
        
        # PURE_SCALPER: Per-scan rejection counters
        rejected_score = 0  # Rejected by score < MIN_SIGNAL_SCORE
        rejected_percentile = 0  # Rejected by percentile filter (if enabled)
        rejected_micro = 0  # Rejected by microstructure filter
        rejected_risk = 0  # Rejected by risk/cooldown state
        rejected_capacity = 0  # Rejected by max positions
        passed_filters = 0  # Signals that passed all filters
        entry_candidates = 0  # Signals that passed position manager check (considered for entry)
        entries_opened_this_scan = 0  # Entries actually opened this scan
        
        # DIAGNOSTIC: Track filtering stages
        signals_pass_score_filter = 0
        signals_pass_strength_filter = 0
        signals_pass_percentile_filter = 0
        signals_pass_position_manager = 0
        
        # Scan tracking metrics
        symbols_processed = 0  # Symbols that were processed (had stats, attempted signal generation)
        symbols_skipped = {
            'in_position': 0,
            'no_stats': 0,
            'no_signal': 0,
            'score_below_threshold': 0
        }
        cache_hits = 0
        cache_misses = 0
        orderbook_success = 0
        orderbook_failures = 0
        
        # LATENCY OPTIMIZATION: Process symbols in parallel batches
        # OPTIMIZATION: Cache time.time() at batch level to avoid repeated calls
        batch_now = time.time()
        
        async def process_symbol(symbol):
            # PURE_SCALPER: Access outer scope counters (only those used inside this function)
            nonlocal rejected_score, rejected_percentile
            """Process a single symbol for signals (optimized)."""
            # OPTIMIZATION: Use cached time from batch level
            symbol_now = batch_now
            
            # Track cache hit/miss for all symbols (even skipped ones)
            cached_ticker = self.ticker_cache.get(symbol, max_age=2.0)  # 2s max age (universe refreshes every 3s)
            was_cache_hit = cached_ticker is not None
            
            # OPTIMIZATION: Use set for O(1) position lookup
            if symbol in self.positions:
                return ('skipped', 'in_position', None, None, was_cache_hit, False)
            
            # Get symbol stats from universe (already cached)
            stats = self.universe.stats.get(symbol)
            if not stats:
                # CRITICAL DEBUG: Log missing stats - check first few symbols to diagnose format mismatch
                # Only log first 3 to avoid spam
                if not hasattr(self, '_no_stats_logged_count'):
                    self._no_stats_logged_count = 0
                # DEBUG ONLY: NO STATS messages (excluded from LOG panel by UILogHandler)
                if self._no_stats_logged_count < 3:
                    self.logger.debug(
                        f"NO STATS for {symbol} | "
                        f"stats_keys_count={len(self.universe.stats)} | "
                        f"sample_stats_keys={list(self.universe.stats.keys())[:3] if self.universe.stats else []} | "
                        f"symbol_in_stats={symbol in (self.universe.stats or {})}"
                    )
                    self._no_stats_logged_count += 1
                return ('skipped', 'no_stats', None, None, was_cache_hit, False)
            
            # Track scan time (for stale detection) - NO API CALL, just timestamp
            stats.last_scanned = symbol_now
            
            # LATENCY OPTIMIZATION: Use cache or universe stats only - NO API CALLS
            # Update stats from cache if available and fresher
            if was_cache_hit:
                # Use cached data - sub-millisecond access
                latency_ms = 1  # Cache access is <1ms
                # Update stats from cache if fresher
                if cached_ticker.timestamp > stats.last_seen:
                    stats.bid = cached_ticker.bid
                    stats.ask = cached_ticker.ask
                    stats.last = cached_ticker.last
                    stats.mark = cached_ticker.mark
                    stats.spread_bps = cached_ticker.spread_bps
                    stats.pct_change_24h = cached_ticker.pct_change_24h
            else:
                # Cache miss - use universe stats (fresh from last refresh, no API call)
                # Universe refreshes every 3s, so stats are at most 3s old
                # This is acceptable for signal generation
                latency_ms = 2  # Assume 2ms for universe stats access (in-memory)
                # Stats are already populated from last universe refresh
                # No API call needed - we use what we have
            
            # PERFORMANCE: Pre-extract stats attributes (avoid repeated getattr in logging)
            stats_pct_change = stats.pct_change_24h if hasattr(stats, 'pct_change_24h') else 0.0
            stats_spread = stats.spread_bps
            stats_vol = stats.vol_quote
            stats_last = stats.last if hasattr(stats, 'last') else 0.0
            stats_bid = stats.bid if hasattr(stats, 'bid') else 0.0
            stats_ask = stats.ask if hasattr(stats, 'ask') else 0.0
            
            # OPTIMIZATION: Create symbol_stats dict efficiently
            symbol_stats = {
                'bid': stats_bid,
                'ask': stats_ask,
                'last': stats_last,
                'spread_bps': stats_spread,
                'vol_quote': stats_vol,
                'pct_change_24h': stats_pct_change,
                'pct_change_15m': getattr(stats, 'pct_change_15m', 0.0),
                'oi_change_pct': getattr(stats, 'oi_change_pct', 0.0),
                'funding_rate': self.funding_rates.get(symbol, {}).get('rate', 0.0),
            }
            
            # Fetch orderbook for depth scoring (with fallback)
            # API BUDGET OPTIMIZATION: Use cached orderbook if available (fresher data)
            orderbook = None
            orderbook_fetched = False
            
            # OPTIMIZATION: Check cache first (updated every 2s)
            # OPTIMIZATION: Cache timestamp check result
            cached_ob = self.orderbook_cache.get(symbol)
            if cached_ob:
                cache_age = symbol_now - cached_ob['timestamp']
                if cache_age < 3.0:  # Use if < 3s old
                    orderbook = cached_ob['data']
                    orderbook_fetched = True
            else:
                # Fallback to fresh fetch if cache miss or stale
                try:
                    if self.exchange_wrapper and not DRY_RUN:
                        remaining = self.budget.remaining()
                        if remaining >= 5:  # Only fetch if we have budget
                            # Denormalize symbol for exchange API
                            denormalized_symbol = self.exchange_wrapper.denormalize_symbol(symbol)
                            # Fetch orderbook with timeout
                            orderbook = await asyncio.wait_for(
                                self.exchange_wrapper.fetch_order_book(denormalized_symbol, limit=5),  # limit=5 = weight 1 (vs weight 5 for limit=20)
                                timeout=0.2  # 200ms timeout
                            )
                            orderbook_fetched = True
                            self.budget.tick(1, "orderbook")
                            # Update cache - OPTIMIZATION: Use cached time
                            self.orderbook_cache[symbol] = {
                                'data': orderbook,
                                'timestamp': symbol_now
                            }
                except Exception:
                    orderbook = None  # Fallback to None if fetch fails
                    orderbook_fetched = False
            
            # Get position manager state for exposure scoring
            position_manager_state = self.position_manager.get_exposure_score_data()
            position_manager_state['loss_streak'] = self.position_manager.get_loss_streak()
            
            # Estimate order size (will be refined later, but needed for depth scoring)
            # Use a conservative estimate: 10% of account / entry_price
            equity = self.equity_now()
            estimated_order_size_usd = min(equity * 0.1, MAX_POSITION_SIZE)
            
            # PERFORMANCE: Use pre-bound scan-level invariants
            # SCORING V2: Pass bot_positions for portfolio scoring
            signal, rejection_reason = self.signal_generator.generate_signal(
                symbol=symbol,
                symbol_stats=symbol_stats,
                orderbook=orderbook,
                latency_ms=latency_ms,
                order_size_usd=estimated_order_size_usd,
                position_manager_state=position_manager_state,
                btc_trend=scan_btc_trend,
                regime_config=scan_regime_config,
                recent_trades=scan_recent_trades,
                volatility_regime=scan_volatility_regime,
                bot_positions=self.positions  # SCORING V2: Pass positions for portfolio scoring
            )
            
            # Handle rejected signals (rejection_reason is not None)
            if rejection_reason is not None:
                # self.logger.debug(f"Signal REJECTED for {symbol}: {rejection_reason}")
                # Signal was generated but rejected by signal generator filters
                # PURE_SCALPER: Track rejection reasons for summary
                # Note: These counters are per-symbol, we'll aggregate at scan end
                # For now, just track in signal_history - summary will count from history
                
                # Add to signal_history so it appears in signal queue
                if signal is not None:
                    signal_record = {
                        'timestamp': batch_now,
                        'symbol': symbol,
                        'strength': signal.strength,
                        'final_score': signal.final_score,
                        'type': signal.signal_type,
                        'side': signal.side,
                        'spread_bps': stats_spread,
                        'volatility': abs(stats_pct_change),
                        'btc_trend': scan_btc_trend,
                        'btc_filter_passed': True,
                        'approved': False,  # Rejected by signal generator
                        'rejection_reason': rejection_reason
                    }
                    # Add score breakdown if available
                    if signal.signal_score:
                        signal_record.update(signal.signal_score.to_dict())
                    self.signal_history.append(signal_record)
                    
                    # PURE_SCALPER: Count rejections by reason (for summary)
                    # These are from signal generator (score/percentile/strength checks)
                    if "score below" in rejection_reason or "Score too low" in rejection_reason:
                        rejected_score += 1
                    elif "percentile" in rejection_reason:
                        rejected_percentile += 1
                
                # PERFORMANCE: Guard logging with isEnabledFor and use pre-extracted stats
                if not hasattr(self, '_signal_reject_log_count'):
                    self._signal_reject_log_count = 0
                # DEBUG ONLY: Signal rejections are aggregated in loop summary (too frequent for LOG panel)
                if self._signal_reject_log_count < 10:
                    self.logger.debug(
                        f"Signal REJECTED by generator: {symbol} | "
                        f"reason={rejection_reason} | "
                        f"score={signal.final_score if signal else 0:.1f} | "
                        f"pct={stats_pct_change:.2f}% | spread={stats_spread:.1f}bps"
                    )
                    self._signal_reject_log_count += 1
                return ('skipped', 'no_signal', None, None, was_cache_hit, orderbook_fetched)
            
            # Handle case where no signal was generated at all
            if signal is None:
                # CRITICAL DEBUG: Log why signal wasn't generated (log first 10 to diagnose)
                # PERFORMANCE: Guard logging and use pre-extracted stats
                if not hasattr(self, '_signal_reject_log_count'):
                    self._signal_reject_log_count = 0
                # DEBUG ONLY: Signal not generated messages (too frequent for LOG panel)
                if self._signal_reject_log_count < 10:
                    self.logger.debug(
                        f"Signal NOT generated: {symbol} | "
                        f"pct={stats_pct_change:.2f}% | spread={stats_spread:.1f}bps | "
                        f"vol=${stats_vol/1e6:.1f}M | last={stats_last:.4f} | bid={stats_bid:.4f} | ask={stats_ask:.4f}"
                    )
                    self._signal_reject_log_count += 1
                return ('skipped', 'no_signal', None, None, was_cache_hit, orderbook_fetched)
            
            return ('processed', symbol, stats, signal, latency_ms, was_cache_hit, orderbook, orderbook_fetched)
        
        # PERFORMANCE: Pre-bind invariants outside per-symbol loop
        scan_regime_config = self.regime_config if hasattr(self, 'regime_config') else None
        scan_recent_trades = list(self.recent_trades) if hasattr(self, 'recent_trades') else []
        scan_volatility_regime = self.volatility_regime if hasattr(self, 'volatility_regime') else 'Normal'
        scan_btc_trend = self.btc_trend
        
        # LATENCY OPTIMIZATION: Process symbols in parallel (OPTIMIZED: larger batch size)
        # LOOP STATS ACCUMULATOR: Aggregate signal stats for summary log
        loop_stats = {
            'signals': 0,           # Total signals generated
            'unicorns': 0,          # Unicorn-level signals (score >= threshold)
            'longs': 0,             # Long signals
            'shorts': 0,            # Short signals
            'scores': [],           # All signal scores (for avg/best)
            'rejected_by_filters': 0  # Optional: rejected by trend/corr/etc.
        }
        from .config import UNICORN_SCORE_THRESHOLD
        
        # PERFORMANCE: Increased batch size from 25 to 50 for better parallelization
        batch_size = 50
        total_symbols_count = len(all_symbols_to_scan)  # OPTIMIZATION: Cache len() result
        for i in range(0, total_symbols_count, batch_size):
            # OPTIMIZATION: Update cached time for each batch
            batch_now = time.time()
            batch = all_symbols_to_scan[i:i+batch_size]
            tasks = [process_symbol(symbol) for symbol in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # OPTIMIZATION: Process results efficiently
            for result in results:
                if isinstance(result, Exception) or result is None:
                    continue
            
                result_type = result[0]
            
                if result_type == 'skipped':
                    skip_reason = result[1]
                    was_cache_hit = result[4] if len(result) > 4 else False
                    orderbook_fetched = result[5] if len(result) > 5 else False
            
                    if skip_reason in symbols_skipped:
                        symbols_skipped[skip_reason] += 1
            
                    if was_cache_hit:
                        cache_hits += 1
                    else:
                        cache_misses += 1
            
                    if orderbook_fetched:
                        orderbook_success += 1
                    else:
                        orderbook_failures += 1
            
                    continue
            
                if result_type != 'processed' or len(result) < 8:
                    continue
            
                status, symbol, stats, signal, latency_ms, was_cache_hit, orderbook, orderbook_fetched = result
                symbols_processed += 1
            
                if was_cache_hit:
                    cache_hits += 1
                else:
                    cache_misses += 1
            
                if orderbook_fetched:
                    orderbook_success += 1
                else:
                    orderbook_failures += 1
            
                if signal is None:
                    continue
                
                signals_found += 1
                signals_pass_score_filter += 1
                signals_pass_strength_filter += 1
                signals_pass_percentile_filter += 1
                self.signal_stats['signals_generated'] += 1
                self.signal_stats['last_signal_time'] = batch_now
                
                # SCALPER UPGRADE: Recreate symbol_stats dict from stats object (needed for filter)
                symbol_stats = {
                    'bid': getattr(stats, 'bid', 0.0),
                    'ask': getattr(stats, 'ask', 0.0),
                    'last': getattr(stats, 'last', 0.0),
                    'spread_bps': getattr(stats, 'spread_bps', 9999),
                    'vol_quote': getattr(stats, 'vol_quote', 0.0),
                    'pct_change_24h': getattr(stats, 'pct_change_24h', 0.0),
                    'pct_change_15m': getattr(stats, 'pct_change_15m', 0.0),
                    'oi_change_pct': getattr(stats, 'oi_change_pct', 0.0),
                    'funding_rate': self.funding_rates.get(symbol, {}).get('rate', 0.0),
                }
                
                # Get indicators for filter evaluation
                indicators = None
                if hasattr(self, 'indicators_cache'):
                    indicators = self.indicators_cache.get(symbol)
                
                # COLLECT STATS (NO LOGGING YET - aggregate into summary)
                final_score_float = float(signal.final_score) if signal.final_score is not None else 0.0
                threshold_float = float(UNICORN_SCORE_THRESHOLD)
                is_unicorn = UNICORN_PROTOCOL_ENABLED and final_score_float >= threshold_float
                
                # Accumulate stats for summary
                loop_stats['signals'] += 1
                loop_stats['scores'].append(final_score_float)
                if is_unicorn:
                    loop_stats['unicorns'] += 1
                if signal.side.lower() == 'long':
                    loop_stats['longs'] += 1
                else:
                    loop_stats['shorts'] += 1
            
                if stats:
                    stats.signals_generated_count += 1
                    stats.last_signal_time = batch_now
            
                if self.check_drawdown_circuit_breaker():
                    signals_blocked_circuit_breaker += 1
                    self._log_signal_decision(symbol, signal, "rejected", "drawdown_circuit_breaker")
                    continue
            
                # TREND ALIGNMENT REQUIREMENT: Check 5m and 15m trend alignment (optional - can be too restrictive)
                from .config import TREND_ALIGNMENT_REQUIRED
                if TREND_ALIGNMENT_REQUIRED:  # Disabled by default - too restrictive for scalping
                    trend5 = 0
                    trend15 = 0
                    try:
                        # Get trend from OHLCV data (replay feed or fast_storage)
                        ohlcv_5m = None
                        ohlcv_15m = None
                        
                        # REPLAY MODE: Get OHLCV from replay feed
                        if self.replay_mode and self.replay_feed:
                            ohlcv_5m = self.replay_feed.get_ohlcv(symbol, timeframe='5m', limit=20)
                            ohlcv_15m = self.replay_feed.get_ohlcv(symbol, timeframe='15m', limit=20)
                        # LIVE MODE: Try fast_storage (if it has get_ohlcv method)
                        elif hasattr(self.fast_storage, 'get_ohlcv'):
                            ohlcv_5m = self.fast_storage.get_ohlcv(symbol, timeframe='5m', limit=20)
                            ohlcv_15m = self.fast_storage.get_ohlcv(symbol, timeframe='15m', limit=20)
                        
                        if ohlcv_5m and len(ohlcv_5m) >= 10:
                            prices_5m = [bar[4] for bar in ohlcv_5m]  # Close prices
                            from .indicators import calculate_trend_direction_from_prices
                            trend5 = calculate_trend_direction_from_prices(prices_5m, ema_short=3, ema_long=8)
                        else:
                            trend5 = 0
                        
                        if ohlcv_15m and len(ohlcv_15m) >= 10:
                            prices_15m = [bar[4] for bar in ohlcv_15m]  # Close prices
                            trend15 = calculate_trend_direction_from_prices(prices_15m, ema_short=5, ema_long=10)
                        else:
                            trend15 = 0
                    except Exception as e:
                        # If trend calculation fails, reject (conservative)
                        self.logger.debug(f"Trend calculation failed for {symbol}: {e}")
                        trend5 = 0
                        trend15 = 0
                    
                    # Check trend alignment: both 5m and 15m must align with trade direction
                    side_mult = 1 if signal.side == 'long' else -1
                    trend5_aligned = (trend5 * side_mult) > 0  # Same sign = aligned
                    trend15_aligned = (trend15 * side_mult) > 0
                    
                    if not (trend5_aligned and trend15_aligned):
                        # Reject: misaligned trend
                        self._log_signal_decision(symbol, signal, "rejected", "RJ – misaligned trend")
                        loop_stats['rejected_by_filters'] += 1
                        signal_record = {
                            'timestamp': batch_now,
                            'symbol': symbol,
                            'strength': signal.strength,
                            'final_score': signal.final_score,
                            'type': signal.signal_type,
                            'side': signal.side,
                            'spread_bps': stats.spread_bps,
                            'volatility': abs(getattr(stats, 'pct_change_24h', 0.0)),
                            'btc_trend': self.btc_trend,
                            'btc_filter_passed': True,
                            'approved': False,
                            'rejection_reason': "RJ – misaligned trend",
                            'is_unicorn': is_unicorn
                        }
                        if signal.signal_score:
                            signal_record.update(signal.signal_score.to_dict())
                        self.signal_history.append(signal_record)
                        continue
                
                # CORRELATION BLOCKER: Check correlation with open positions
                from .config import CORRELATION_BLOCK_THRESHOLD
                if CORRELATION_BLOCK_THRESHOLD > 0 and self.positions:
                    symbol_pct_change = getattr(stats, 'pct_change_24h', 0.0)
                    max_correlation = 0.0
                    correlated_symbol = None
                    
                    for pos_symbol, pos_data in self.positions.items():
                        pos_stats = self.universe.stats.get(pos_symbol)
                        if not pos_stats:
                            continue
                        
                        pos_pct_change = getattr(pos_stats, 'pct_change_24h', 0.0)
                        
                        # Simple correlation: if both move in same direction with similar magnitude
                        # Correlation = 1.0 if identical, 0.0 if opposite
                        if abs(symbol_pct_change) > 0.01 and abs(pos_pct_change) > 0.01:
                            # Same direction check
                            same_direction = (symbol_pct_change > 0) == (pos_pct_change > 0)
                            if same_direction:
                                # Magnitude similarity (normalized)
                                mag1 = abs(symbol_pct_change)
                                mag2 = abs(pos_pct_change)
                                avg_mag = (mag1 + mag2) / 2.0
                                if avg_mag > 0:
                                    mag_diff = abs(mag1 - mag2) / avg_mag
                                    correlation = 1.0 - min(mag_diff, 1.0)  # 1.0 if identical, 0.0 if very different
                                    
                                    if correlation > max_correlation:
                                        max_correlation = correlation
                                        correlated_symbol = pos_symbol
                    
                    if max_correlation > CORRELATION_BLOCK_THRESHOLD:
                        # Reject: too correlated
                        self._log_signal_decision(symbol, signal, "rejected", f"RJ – correlation > 0.85 (with {correlated_symbol})")
                        loop_stats['rejected_by_filters'] += 1
                        signal_record = {
                            'timestamp': batch_now,
                            'symbol': symbol,
                            'strength': signal.strength,
                            'final_score': signal.final_score,
                            'type': signal.signal_type,
                            'side': signal.side,
                            'spread_bps': stats.spread_bps,
                            'volatility': abs(getattr(stats, 'pct_change_24h', 0.0)),
                            'btc_trend': self.btc_trend,
                            'btc_filter_passed': True,
                            'approved': False,
                            'rejection_reason': f"RJ – correlation > 0.85 (with {correlated_symbol})",
                            'is_unicorn': is_unicorn
                        }
                        if signal.signal_score:
                            signal_record.update(signal.signal_score.to_dict())
                        self.signal_history.append(signal_record)
                        continue
            
                btc_filter_passed = True
            
                # PURE_SCALPER: Three-stage filter pipeline (optional, softened for PURE_SCALPER)
                from .config import USE_THREE_STAGE_FILTER
                from .engine.scalper_filters import evaluate_three_stage_filter
                
                # self.logger.debug(f"Processing signal for {symbol}, score={signal.final_score}")
                
                filter_passed = True  # Default: pass if filter disabled
                filter_rejection_reason = None
                filter_details = None
                structure_confluence = None
                
                if USE_THREE_STAGE_FILTER:
                    # Get advanced_features for filter evaluation
                    advanced_features = None
                    if hasattr(self, '_advanced_features_cache'):
                        advanced_features = self._advanced_features_cache.get(symbol)
                    
                    # Evaluate three-stage filter (softened - microstructure only rejects garbage)
                    filter_passed, filter_rejection_reason, filter_details, structure_confluence = evaluate_three_stage_filter(
                        symbol=symbol,
                        side=signal.side,
                        symbol_stats=symbol_stats,
                        orderbook=orderbook,
                        indicators=indicators,
                        advanced_features=advanced_features,
                        entry_price=signal.entry_price,
                        final_score=signal.final_score  # DEBUG: Pass final_score for debug logging
                    )
                    
                    # self.logger.debug(f"Filter result for {symbol}: passed={filter_passed}")
                    
                    if not filter_passed:
                        # Reject signal - filter failed
                        # PURE_SCALPER: Track microstructure rejections
                        if filter_rejection_reason and 'microstructure_fail' in filter_rejection_reason:
                            rejected_micro += 1
                        self._log_signal_decision(symbol, signal, "rejected", filter_rejection_reason)
                        loop_stats['rejected_by_filters'] += 1
                        signal_record = {
                            'timestamp': batch_now,
                            'symbol': symbol,
                            'strength': signal.strength,
                            'final_score': signal.final_score,
                            'type': signal.signal_type,
                            'side': signal.side,
                            'spread_bps': stats.spread_bps,
                            'volatility': abs(getattr(stats, 'pct_change_24h', 0.0)),
                            'btc_trend': self.btc_trend,
                            'btc_filter_passed': btc_filter_passed,
                            'approved': False,
                            'rejection_reason': filter_rejection_reason,
                            'is_unicorn': is_unicorn,
                            'filter_details': filter_details
                        }
                        if signal.signal_score:
                            signal_record.update(signal.signal_score.to_dict())
                        self.signal_history.append(signal_record)
                        continue
                
                # Filter pipeline passed — check ADX trend gate before proceeding
                # NOTE: Do NOT increment passed_filters here — wait until position manager check passes

                # ADX TREND GATE: Block entries in trending markets
                # The scalper grid profits from mean-reversion in chop. In trends, it gets steamrolled.
                # When ADX > threshold, the market is directional — sit out, don't enter.
                from .config import ADX_TREND_GATE_ENABLED, ADX_TREND_GATE_THRESHOLD
                if ADX_TREND_GATE_ENABLED and indicators:
                    adx = indicators.get('adx', 0.0)
                    if adx > ADX_TREND_GATE_THRESHOLD:
                        self._log_signal_decision(symbol, signal, "rejected",
                            f"RJ – trend market (ADX {adx:.1f} > {ADX_TREND_GATE_THRESHOLD})")
                        loop_stats['rejected_by_filters'] += 1
                        rejected_risk += 1
                        signal_record = {
                            'timestamp': batch_now,
                            'symbol': symbol,
                            'strength': signal.strength,
                            'final_score': signal.final_score,
                            'type': signal.signal_type,
                            'side': signal.side,
                            'spread_bps': stats.spread_bps,
                            'volatility': abs(getattr(stats, 'pct_change_24h', 0.0)),
                            'btc_trend': self.btc_trend,
                            'btc_filter_passed': True,
                            'approved': False,
                            'rejection_reason': f"RJ – trend market (ADX {adx:.1f} > {ADX_TREND_GATE_THRESHOLD})",
                            'is_unicorn': is_unicorn
                        }
                        if signal.signal_score:
                            signal_record.update(signal.signal_score.to_dict())
                        self.signal_history.append(signal_record)
                        continue

                # ER REGIME FILTER: Kaufman Efficiency Ratio — skip when price is too directional
                # ER > 0.35 = trending (dangerous for mean-reversion). ER < 0.15 = pure noise.
                # Mean-reversion scalping prints in the 0.20–0.35 sweet spot.
                from .config import ER_FILTER_ENABLED, ER_FILTER_MAX_THRESHOLD, ER_FILTER_MIN_THRESHOLD
                if ER_FILTER_ENABLED and indicators:
                    er = indicators.get('er', 0.5)
                    if er > ER_FILTER_MAX_THRESHOLD:
                        self._log_signal_decision(symbol, signal, "rejected",
                            f"RJ – too directional (ER {er:.3f} > {ER_FILTER_MAX_THRESHOLD})")
                        loop_stats['rejected_by_filters'] += 1
                        rejected_risk += 1
                        continue
                    if er < ER_FILTER_MIN_THRESHOLD:
                        self._log_signal_decision(symbol, signal, "rejected",
                            f"RJ – too noisy (ER {er:.3f} < {ER_FILTER_MIN_THRESHOLD})")
                        loop_stats['rejected_by_filters'] += 1
                        rejected_risk += 1
                        continue

                # DAY TRADING: Dead zone gate REMOVED — day trades hold through weekends.
                # Scalping dead zone (Sat/Sun before 10 UTC) was 0% WR historically for 5m bars,
                # but 1h-4h day trading structure works on weekends.

                # BTC CHOP FILTER: relaxed for day trading (was 0.5%, now 0.2%).
                # Day trading needs moderate BTC direction, not extreme.
                from .config import BTC_CHOP_THRESHOLD
                if abs(self.btc_trend) < BTC_CHOP_THRESHOLD:
                    continue  # Silent — market has no direction for day trading either

                # HARD PRICE FLOOR: Day trading needs real market structure. $2 minimum.
                # Tokens below $2 are random walks — HTF trend doesn't apply.
                _entry_price = getattr(stats, 'mark', 0.0) or getattr(stats, 'last', 0.0)
                if _entry_price < 2.00:
                    self._log_signal_decision(symbol, signal, "rejected",
                        f"RJ – price < \$2 (${_entry_price:.2f})")
                    loop_stats['rejected_by_filters'] += 1
                    continue

                # 5-FACTOR CHECKLIST: 5 binary checks, need 3/5 to enter.
                # Uses indicators/AdvancedFeatures when available, falls back to 24h data.
                from .score_checklist import score_signal as checklist_score

                _af = None
                if USE_THREE_STAGE_FILTER:
                    _af = advanced_features if 'advanced_features' in dir() else None
                try:
                    _af = _af or advanced_features
                except (NameError, UnboundLocalError):
                    _af = None

                # NEWS SENTIMENT: read from cache (prefetched in background every 15 min)
                _news_mod = 0
                try:
                    from .news_sentiment import get_news_sentiment
                    _ns = get_news_sentiment()
                    _news_mod = await _ns.get_confluence_modifier(symbol, signal.side)
                except Exception:
                    pass  # Never block entry

                checklist = checklist_score(
                    side=signal.side,
                    symbol_stats=symbol_stats,
                    indicators=indicators,
                    pct_change_24h=getattr(stats, 'pct_change_24h', 0.0),
                    volume_24h=getattr(stats, 'vol_quote', 0.0),
                    spread_bps=getattr(stats, 'spread_bps', 9999.0),
                    advanced_features=_af,
                    current_price=_entry_price,
                    news_modifier=_news_mod,
                )

                # Need 3/5 to pass
                if not checklist.passed:
                    self._log_signal_decision(symbol, signal, "rejected",
                        checklist.reason)
                    loop_stats['rejected_by_filters'] += 1
                    continue

                # Keep SignalScorer score (0-100), use checklist only as gate
                # signal.final_score already set by scoring pipeline — don't clobber it
                if signal.final_score <= 0:
                    # Use confluence score as conviction: 65 → 65, 80 → 80, 100 → 100
                    signal.final_score = float(getattr(checklist, 'confluence_score', 50) or 50)
                signal.strength = signal.final_score / 100.0  # normalize 0-100 → 0.0-1.0

                # Initialize entry decision variables
                can_enter = True
                reason = None
                signal_needs_confirmation = False

                # Signal Confirmation Window (SCW) check — if enabled
                if USE_SIGNAL_CONFIRMATION and symbol in self.signal_confirmation.waiting_signals:
                    # Check if signal is already confirmed
                    ready_signal = self.signal_confirmation.get_ready_signal(symbol)
                    if ready_signal:
                        # Signal is confirmed and ready
                        can_enter = True
                        reason = "SCW: Signal confirmed"
                        self.signal_confirmation.remove_signal(symbol)
                    else:
                        # Still waiting
                        can_enter = False
                        waiting = self.signal_confirmation.waiting_signals[symbol]
                        reason = f"SCW: Waiting for confirmation ({waiting.confirmation_count}/{waiting.confirmation_required})"
                        signal_needs_confirmation = True
                
                self._log_signal_decision(
                    symbol,
                    signal,
                    "approved" if can_enter else "rejected",
                    reason if not can_enter else None,
                )
                
                if not can_enter:
                    self.signal_stats['signals_blocked'] += 1
                    if not signal_needs_confirmation:
                        signals_blocked_position_manager += 1
                    
                    # PURE_SCALPER: Categorize rejection reasons for counters
                    reason_str = reason or "unknown"
                    if "score below" in reason_str or "Score too low" in reason_str or "min_score_guard" in reason_str:
                        rejected_score += 1
                    elif "percentile" in reason_str:
                        rejected_percentile += 1
                    elif "capacity" in reason_str or "max positions" in reason_str or "max_positions" in reason_str or "RJ – max positions" in reason_str:
                        rejected_capacity += 1
                    elif "risk" in reason_str or "cooldown" in reason_str or "loss_streak" in reason_str or "drawdown" in reason_str:
                        rejected_risk += 1
                    
                    # DEBUG: Log rejection reason (demoted to DEBUG to reduce spam)
                    # Use dynamic cap for logging if equity available, else fallback to legacy
                    try:
                        equity = self.equity_now()
                        max_pos = self.position_manager.get_effective_max_positions(equity=equity) if equity > 0 else self.cfg.MAX_OPEN_POSITIONS
                    except Exception:
                        max_pos = self.cfg.MAX_OPEN_POSITIONS
                    self.logger.debug(
                        f"[ENTRY_REJECTED] symbol={symbol} side={signal.side} "
                        f"score={signal.final_score:.1f} reason={reason_str} "
                        f"current_positions={current_positions}/{max_pos}"
                    )
                    
                    # METRICS: count rejection reasons
                    try:
                        key = (reason or "unknown").split()[0]
                        self.metrics['rejections_by_reason'][key] = self.metrics['rejections_by_reason'].get(key, 0) + 1
                    except Exception:
                        pass
                    # We may skip extra logging for startup / SCW, but we must ALWAYS skip entry:
                    continue
                
                # PURE_SCALPER: Signal passed all checks (filter pipeline + position manager) - ready for entry
                # (Entry will be attempted below)
                # CRITICAL: Only count as "passed" if signal passed BOTH filter pipeline AND position manager check
                # This ensures Signal Health panel shows accurate pass rate (signals ready for entry)
                passed_filters += 1  # Signal passed filter pipeline AND position manager check
                entry_candidates += 1  # Track signals that passed position manager check
                
                # DEBUG: Log that we're about to attempt entry (demoted to DEBUG to reduce spam)
                # Use dynamic cap for logging if equity available, else fallback to legacy
                try:
                    equity = self.equity_now()
                    max_pos = self.position_manager.get_effective_max_positions(equity=equity) if equity > 0 else self.cfg.MAX_OPEN_POSITIONS
                except Exception:
                    max_pos = self.cfg.MAX_OPEN_POSITIONS
                self.logger.debug(
                    f"[ENTRY_ATTEMPT] symbol={symbol} side={signal.side} "
                    f"score={signal.final_score:.1f} can_enter={can_enter} "
                    f"current_positions={current_positions}/{max_pos}"
                )
                
                # Signal passed all checks - proceed with entry
                try:
                    from .validators import (
                        validate_price,
                        validate_side,
                        validate_stop_loss_take_profit,
                    )
                    entry_price = validate_price(signal.entry_price, "entry_price")
                    validate_side(signal.side)
                    validate_stop_loss_take_profit(
                        entry_price,
                        signal.stop_loss,
                        signal.take_profit,
                        signal.side,
                    )
                except Exception as e:
                    self.logger.warning(
                        "Signal validation failed",
                        symbol=symbol,
                        error=str(e),
                    )
                    continue
                
                # Handle replacement if needed (score-aware replacement disabled — KISS)
                replacement_symbol = None
                if replacement_symbol:
                        # Close the weakest position to make room
                        # Close the position (will be handled by exit manager)
                        # CRITICAL: Check if replacement symbol still exists
                        if replacement_symbol in self.positions:
                            replacement_position = self.positions[replacement_symbol]
                            
                            # Force exit the weakest position
                            exit_result = await self.exit_manager.exit_position(
                                symbol=replacement_symbol,
                                position=replacement_position,
                                reason="replaced_by_better_signal",
                                funding_rate=replacement_position.get("funding_rate", 0.0),
                            )
                            
                            if exit_result.success:
                                # Update stats
                                was_win = exit_result.net_pnl > 0 if exit_result.net_pnl else False
                                pnl_pct = (exit_result.net_pnl / equity * 100) if exit_result.net_pnl else None
                                
                                # CANONICAL: Apply exit and log atomically
                                from .position_utils import apply_exit_and_log, record_loop_exit
                                
                                replacement_entry_price = replacement_position.get('entry_price', 0)
                                replacement_entry_time = replacement_position.get('entry_time', time.time())
                                replacement_side = replacement_position.get('side', '')
                                replacement_size = replacement_position.get('size', 0)
                                
                                # Calculate PnL percentage
                                replacement_pnl_pct = 0.0
                                if replacement_entry_price > 0 and exit_result.exit_price and exit_result.exit_price > 0:
                                    if replacement_side.lower() == 'long':
                                        replacement_pnl_pct = ((exit_result.exit_price - replacement_entry_price) / replacement_entry_price) * 100
                                    else:
                                        replacement_pnl_pct = ((replacement_entry_price - exit_result.exit_price) / replacement_entry_price) * 100
                                
                                replacement_now = time.time()
                                replacement_prs = replacement_position.get('recovery_score')
                                
                                success, event_created = apply_exit_and_log(
                                    positions=self.positions,
                                    positions_set=set(),
                                    symbol=replacement_symbol,
                                    new_size=0.0,  # Full exit
                                    action="EXIT",
                                    exit_price=exit_result.exit_price,
                                    entry_price=replacement_entry_price,
                                    entry_time=replacement_entry_time,
                                    exit_time=replacement_now,
                                    exit_size=exit_result.exit_size or replacement_size,
                                    size_before=replacement_size,
                                    size_after=0.0,
                                    side=replacement_side,
                                    pnl_value=exit_result.net_pnl or 0.0,
                                    pnl_pct=replacement_pnl_pct,
                                    gross_pnl=exit_result.gross_pnl or 0.0,
                                    net_pnl=exit_result.net_pnl or 0.0,
                                    total_costs=exit_result.total_costs or 0.0,
                                    reason="replaced_by_better_signal",
                                    prs=replacement_prs,
                                    was_win=was_win,
                                    is_unicorn=replacement_position.get('is_unicorn', False),
                                    bot_instance=self
                                )
                                
                                if success and event_created:
                                    record_loop_exit(replacement_symbol, "EXIT")
                                    self.position_manager.record_exit(replacement_symbol, was_win, pnl_pct=pnl_pct, exit_reason=None, profit_atr=None)
                                
                                current_positions = len(self.positions)
                            elif "Invalid position" in str(exit_result.error or ""):
                                # Position doesn't exist on exchange - clean up from dict
                                if replacement_symbol in self.positions:
                                    del self.positions[replacement_symbol]
                                current_positions = len(self.positions)
                
                leverage = self.position_manager.calculate_dynamic_leverage(
                    signal.strength,
                    is_unicorn=is_unicorn,
                )
                
                # SCALPER: Get ATR from indicators for scalper stop loss calculation
                atr_pct = None
                if indicators and indicators.get('atr_pct'):
                    atr_pct = indicators.get('atr_pct')
                else:
                    # Fallback: estimate from signal stop loss
                    if signal.signal_score:
                        stop_distance_pct = (
                            abs((signal.stop_loss - signal.entry_price) / signal.entry_price)
                            if signal.entry_price > 0
                            else 0
                        )
                        if stop_distance_pct > 0:
                            atr_pct = stop_distance_pct / 1.5
                
                # Use signal's stop loss — now calculated from real ATR in signal generator
                scalper_stop_loss = signal.stop_loss
                
                # HARD POSITION CAP — verify against Binance, not internal tracking
                # Internal tracking can have ghosts. Binance is the ONLY source of truth.
                equity_now = self.equity_now()
                max_pos = self.position_manager.get_effective_max_positions(equity=equity_now) if equity_now > 0 else self.cfg.MAX_OPEN_POSITIONS
                # Use cached exchange count if fresh (<3s old), else do a quick lightweight fetch
                exchange_pos_count = len(self.positions)  # Fallback
                if hasattr(self, '_last_exchange_pos_count') and hasattr(self, '_last_exchange_count_ts'):
                    if time.time() - self._last_exchange_count_ts < 3.0:
                        exchange_pos_count = self._last_exchange_pos_count
                    else:
                        # Stale — do a fresh lightweight fetch (fast, ~200ms)
                        try:
                            fresh = await asyncio.wait_for(
                                self._fetch_positions_lightweight(), timeout=3.0)
                            exchange_pos_count = len(fresh)
                            self._last_exchange_pos_count = exchange_pos_count
                            self._last_exchange_count_ts = time.time()
                        except Exception:
                            pass  # Use stale/fallback count on fetch failure
                elif hasattr(self, '_last_exchange_pos_count'):
                    exchange_pos_count = self._last_exchange_pos_count
                if exchange_pos_count >= max_pos:
                    self.logger.info(f"[BLOCKED] {symbol} cap:Exchange count={exchange_pos_count}/{max_pos}")
                    continue

                # POSITION MANAGER CHECK: cooldown, rate limit, loss streak, volume, latency
                pm_ok, pm_reason, _ = self.position_manager.can_enter_position(
                    symbol=symbol,
                    spread_bps=stats.spread_bps if stats else 9999.0,
                    volume_24h=getattr(stats, 'vol_quote', 0.0),
                    latency_ms=0,  # already checked by scanner; skip double-check
                    signal_strength=signal.strength,
                    current_positions=len(self.positions),
                    is_unicorn=is_unicorn,
                    signal_score=signal.final_score,
                    open_positions=self.positions,
                    side=signal.side,
                )
                if not pm_ok:
                    self.logger.info(f"[BLOCKED] {symbol} cmp={signal.final_score:.0f} pm:{pm_reason}")
                    if "max positions" in pm_reason or "capacity" in pm_reason:
                        rejected_capacity += 1
                    elif "cooldown" in pm_reason or "rate" in pm_reason:
                        rejected_risk += 1
                    else:
                        rejected_risk += 1
                    continue

                # RISK-BASED SIZING: Calculate position size using risk budget approach
                # Use scalper stop loss for accurate risk calculation
                (
                    position_size,
                    risk_fraction,
                    sizing_reason,
                ) = self.position_manager.calculate_position_size(
                    equity=equity,
                    entry_price=signal.entry_price,
                    stop_loss_price=scalper_stop_loss,
                    signal_strength=signal.strength,
                    side=signal.side,
                    is_unicorn=is_unicorn,
                    open_positions=self.positions,
                    leverage=leverage,
                    signal_score=signal.final_score,  # Pass signal score for RPA
                )
                
                if position_size <= 0 or sizing_reason:
                    # DEBUG: Log position sizing rejection (important for debugging)
                    self.logger.warning(
                        f"[SIZING_REJECTED] symbol={symbol} size={position_size} "
                        f"reason={sizing_reason or 'size<=0'} risk_fraction={risk_fraction*100:.2f}%"
                    )
                    continue
                
                final_leverage = leverage
                
                entry_delay = self.position_manager.get_entry_delay_ms(
                    signal_strength=signal.strength
                )
                
                use_limit = self.order_manager.should_use_limit_order(
                    spread_bps=stats.spread_bps,
                    signal_strength=signal.strength,
                )
                
                # BLOCKED SYMBOLS: permanently skip symbols that need account agreements
                if hasattr(self, '_blocked_symbols') and symbol in self._blocked_symbols:
                    continue
                # FAIL COOLDOWN: skip symbols that recently failed entry
                if hasattr(self, '_entry_fail_cooldowns') and symbol in self._entry_fail_cooldowns:
                    if time.time() < self._entry_fail_cooldowns[symbol]:
                        continue
                    else:
                        del self._entry_fail_cooldowns[symbol]  # Cooldown expired

                # ENTRY BUDGET: max 2 attempts per scan (each takes 2-4s). Prevents timeout spiral.
                if entries_attempted >= 2:
                    continue
                if time.time() - scan_start_time > 24:
                    continue

                entries_attempted += 1

                entry_symbol = symbol
                entry_side = signal.side

                # HARD CAP CHECK: simple pre-check, post-entry cleanup handles bursts
                if len(self.positions) >= MAX_OPEN_POSITIONS:
                    self.logger.info(
                        f"[BLOCKED] {entry_symbol} pm:Hard cap "
                        f"({len(self.positions)}/{MAX_OPEN_POSITIONS})"
                    )
                    continue

                # EXCHANGE CAP: Enforced by soft cap (positions_set) + naked audit (every 120s).
                # The SLTP batch fetch (every 15s) + audit covers reconciliation. No per-entry fetch needed.

                # 15M MOMENTUM TREND FILTER: only check for signals that pass all other gates.
                # Runs here (not earlier) so we only OHLCV-fetch the 1-3 candidates per scan.
                from .config import MOMENTUM_15M_ENABLED, MOMENTUM_15M_THRESHOLD_PCT
                if MOMENTUM_15M_ENABLED and not DRY_RUN:
                    try:
                        denorm = self.exchange_wrapper.denormalize_symbol(symbol)
                        ohlcv_15m = await asyncio.wait_for(
                            self.exchange_wrapper.exchange.fetch_ohlcv(denorm, '15m', limit=3),
                            timeout=3.0
                        )
                        if ohlcv_15m and len(ohlcv_15m) >= 2:
                            closes = [c[4] for c in ohlcv_15m]
                            pct_15m = ((closes[-1] - closes[-2]) / closes[-2]) * 100.0
                            if signal.side == 'long' and pct_15m < MOMENTUM_15M_THRESHOLD_PCT:
                                self.logger.info(f"[15M_BLOCK] {symbol} long blocked: 15m Δ={pct_15m:.2f}% < {MOMENTUM_15M_THRESHOLD_PCT}%")
                                continue
                            if signal.side == 'short' and pct_15m > -MOMENTUM_15M_THRESHOLD_PCT:
                                self.logger.info(f"[15M_BLOCK] {symbol} short blocked: 15m Δ={pct_15m:.2f}% > {-MOMENTUM_15M_THRESHOLD_PCT}%")
                                continue
                    except asyncio.TimeoutError:
                        pass  # Don't block entry on timeout
                    except Exception:
                        pass  # Non-critical

                # OPEN INTEREST + TAKER FLOW + POSITIONING: fetch for entry candidates
                # Fetch OI, taker buy/sell ratio, and long/short ratio in parallel
                oi_task = asyncio.create_task(
                    self.exchange_wrapper.fetch_open_interest(symbol))
                taker_task = asyncio.create_task(
                    self.exchange_wrapper.fetch_taker_buy_ratio(symbol))
                ls_task = asyncio.create_task(
                    self.exchange_wrapper.fetch_long_short_ratio(symbol))

                try:
                    oi_data = await asyncio.wait_for(oi_task, timeout=2.0)
                    if oi_data and hasattr(stats, 'open_interest'):
                        new_oi = float(oi_data.get('openInterestAmount', 0) or 0)
                        old_oi = getattr(stats, 'open_interest', 0.0) or 0.0
                        stats.open_interest = new_oi
                        if old_oi > 0:
                            stats.oi_change_pct = ((new_oi - old_oi) / old_oi) * 100.0
                except Exception:
                    pass

                try:
                    stats.taker_buy_ratio = await asyncio.wait_for(taker_task, timeout=2.0)
                except Exception:
                    stats.taker_buy_ratio = 0.5

                try:
                    stats.long_short_ratio = await asyncio.wait_for(ls_task, timeout=2.0)
                except Exception:
                    stats.long_short_ratio = 1.0

                self.logger.info(
                    f"[CALLING_ENTER] symbol={entry_symbol} side={entry_side} "
                    f"size={position_size:.4f} leverage={final_leverage}x "
                    f"entry_price={signal.entry_price:.6f} stop_loss={scalper_stop_loss:.6f}"
                )

                # PRE-ENTRY PROTECTION CUSHION CHECK (Deep Research #7)
                # Binance validates SL/TP against current MARK_PRICE. If the stop is too
                # close, -2021 rejects it. Pre-validate locally to avoid wasted attempts.
                # Research: no fixed minimum distance published — must compute our own buffer.
                _ref_price = getattr(stats, 'mark', 0.0) or getattr(stats, 'last', 0.0)
                if _ref_price > 0 and signal.stop_loss > 0:
                    if entry_side == 'long':
                        _cushion_pct = (_ref_price - signal.stop_loss) / _ref_price
                    else:
                        _cushion_pct = (signal.stop_loss - _ref_price) / _ref_price
                    _min_cushion = 0.005  # 0.5% minimum — covers tick size + spread + transport jitter
                    if _cushion_pct < _min_cushion:
                        self.logger.warning(
                            f"[PRE-CHECK] {entry_symbol} stop too close to market "
                            f"({_cushion_pct*100:.2f}% < {_min_cushion*100:.1f}%) — skipping cycle"
                        )
                        continue  # Don't even try — would get -2021

                result = await self.order_manager.enter_position(
                    symbol=entry_symbol,
                    side=entry_side,
                    size=position_size,
                    entry_price=signal.entry_price,
                    delay_ms=entry_delay,
                    use_limit=use_limit,
                    leverage=final_leverage,
                    stop_loss_price=signal.stop_loss,      # Atomic SL/TP — never enter naked
                    take_profit_price=signal.take_profit,  # If -2021, entire order rejected, no fee paid
                )
                
                # REMOVED: Individual entry result log (tracked via DecisionEvent)
                
                if result.success:
                    # METRICS
                    self.metrics['entries_attempted'] += 1
                    self.metrics['entries_opened'] += 1
                    entries_opened_this_scan += 1  # Track entries opened this scan
                    self.position_manager.record_entry(entry_symbol)
                    
                    entry_price = result.filled_price or signal.entry_price
                    entry_time = time.time()
                    filled_size = result.filled_size or position_size
                    
                    atr_pct = None
                    stop_distance_pct = (
                        abs((signal.stop_loss - entry_price) / entry_price)
                        if entry_price > 0
                        else 0
                    )
                    if stop_distance_pct > 0:
                        atr_pct = stop_distance_pct / 1.5
                    
                    # Get funding rate for position (if available)
                    position_funding_rate = None
                    if entry_symbol in self.funding_rates:
                        position_funding_rate = self.funding_rates[entry_symbol].get("rate", 0.0)
                    
                    # Use signal's stop loss — now calculated from real ATR in signal generator
                    initial_stop_price = signal.stop_loss
                    
                    # Use initial_stop_price for stop_loss (scalper uses tighter stops)
                    stop_loss_price = initial_stop_price
                    
                    # Calculate initial R (distance from entry to stop-loss)
                    if entry_side == "long":
                        initial_r = abs(entry_price - stop_loss_price)
                    else:  # short
                        initial_r = abs(stop_loss_price - entry_price)
                    
                    # Select exit profile based on signal score
                    exit_profile = self.exit_manager.select_exit_profile(signal.final_score)

                    # Build position dict but DON'T track yet — must verify SL/TP first
                    _new_position = {
                        "side": entry_side,
                        "size": filled_size,
                        "entry_price": entry_price,
                        "stop_loss": stop_loss_price,
                        "initial_stop_price": initial_stop_price,
                        "take_profit": signal.take_profit,
                        "entry_time": entry_time,
                        "signal_strength": signal.strength,
                        "signal_score": signal.final_score,
                        "signal_type": signal.signal_type,
                        "atr_pct": atr_pct,
                        "leverage": final_leverage,
                        "peak_pnl": 0.0,
                        "rescue_flag": False,
                        "rescue_start_time": 0,
                        "is_unicorn": is_unicorn,
                        "funding_rate": position_funding_rate,
                        "initial_r": initial_r,
                        "exit_profile": exit_profile,
                        "max_r_reached": 0.0,
                        "bars_in_trade": 0,
                        "partial_exit_done": False,
                        "sl_moved_to_be": False,
                        "last_bar_update_time": entry_time,
                        "stage": 1,
                        "survived_msx1": False,
                        "peak_price": entry_price,
                        "trough_price": entry_price,
                    }
                    # === SL/TP PLACEMENT — EXCHANGE-VERIFIED ===
                    # Position MUST have both SL and TP confirmed on exchange before tracking.
                    # Uses -4130 "order already exists" as verification: if we try to place
                    # and get -4130, the order is definitely live. No trust, only proof.
                    sl_ok = False
                    tp_ok = False
                    sl_id = None
                    tp_id = None
                    close_side = "sell" if entry_side == "long" else "buy"

                    for attempt in range(3):  # 3 attempts over 1.5s — was 15 over 7.5s (excessive per research)
                        await asyncio.sleep(0.5)
                        # Try placing SL (or verify existing via -4130)
                        if not sl_ok:
                            try:
                                result = await self.order_manager.place_sl_order(
                                    entry_symbol, entry_side, filled_size, stop_loss_price)
                                if result == "EXISTS":
                                    sl_ok = True  # -4130: order definitely on exchange
                                    sl_id = "EXISTS"
                                elif result:
                                    sl_ok = True  # Real order ID returned
                                    sl_id = result
                            except Exception:
                                pass
                        # Try placing TP (or verify existing via -4130)
                        if not tp_ok:
                            try:
                                result = await self.order_manager.place_tp_order(
                                    entry_symbol, entry_side, filled_size, signal.take_profit)
                                if result == "EXISTS":
                                    tp_ok = True
                                    tp_id = "EXISTS"
                                elif result:
                                    tp_ok = True
                                    tp_id = result
                            except Exception:
                                pass
                        if sl_ok and tp_ok:
                            self.logger.info(f"[VERIFIED] {entry_symbol} SL={'EXISTS' if sl_id=='EXISTS' else sl_id} TP={'EXISTS' if tp_id=='EXISTS' else tp_id}")
                            break

                    if not (sl_ok and tp_ok):
                        # Failed to protect — close position. DO NOT TRACK (position never added).
                        self.logger.critical(f"[UNPROTECTABLE] {entry_symbol} SL={sl_ok} TP={tp_ok} after 3 attempts — CLOSING")
                        for close_i in range(5):
                            try:
                                await self.exchange_wrapper.exchange.create_order(
                                    self.exchange_wrapper.denormalize_symbol(entry_symbol),
                                    "market", close_side, filled_size, None,
                                    {"reduceOnly": True})
                                self.logger.info(f"[UNPROTECTABLE_CLOSED] {entry_symbol}")
                                break
                            except Exception as e:
                                if close_i == 4:
                                    self.logger.critical(f"[UNPROTECTABLE_FATAL] {entry_symbol} cannot close: {e}")
                                await asyncio.sleep(1)
                        continue  # DO NOT TRACK — position is closed, _new_position not added

                    # ONLY track AFTER verified SL+TP on exchange
                    _new_position["sl_order_id"] = sl_id
                    _new_position["tp_order_id"] = tp_id
                    self.positions[entry_symbol] = _new_position

                    # LOGGING V2: Entry logging now handled by DecisionEvent (single concise line)
                    # Create canonical decision event for entry
                    decision = DecisionEvent(
                        timestamp=entry_time,
                        action="ENTRY",
                        symbol=entry_symbol,
                        side=entry_side.upper(),
                        price=entry_price,
                        entry_price=entry_price,
                        size=filled_size,
                        reason="approved",
                        score=signal.final_score,  # Continuous composite mapped to 0-100
                        signal_type=signal.signal_type,
                        signal_strength=signal.strength,
                        stop_loss=stop_loss_price,  # SCALPER: Use scalper stop loss
                        take_profit=signal.take_profit,
                        is_unicorn=is_unicorn
                    )
                    # COMPOSITE: Attach pillar scores for logging
                    if hasattr(signal, 'composite_score') and signal.composite_score != 0:
                        decision.composite = signal.composite_score
                    if hasattr(signal, 'score_components_capped') and signal.score_components_capped:
                        decision.score_components_capped = signal.score_components_capped
                    # SCALPER UPGRADE: Attach filter details for logging
                    if filter_details:
                        decision.filter_details = filter_details
                    
                    # Log using unified system (writes to decisions.jsonl, recent_trades, and Recent Activity buffer)
                    # This also logs the standardized ENTRY line
                    log_trade_decision(decision, bot_instance=self)
                    
                    current_positions += 1
                    # POST-ENTRY CAP: if burst pushed us over, close worst
                    if len(self.positions) > MAX_OPEN_POSITIONS:
                        excess = len(self.positions) - MAX_OPEN_POSITIONS
                        self.logger.warning(
                            f"[CAP_CULL] {excess} over cap — closing worst performer"
                        )
                        sorted_pos = sorted(
                            self.positions.items(),
                            key=lambda x: x[1].get('unrealized_pnl', x[1].get('peak_pnl', 0) or 0)
                        )
                        for sym, pos in sorted_pos[:excess]:
                            close_side = 'sell' if pos.get('side') == 'long' else 'buy'
                            size = pos.get('size', 0)
                            await self.order_manager.close_position_immediately(
                                sym, close_side, size)

                            if sym in self.positions:
                                del self.positions[sym]
                            current_positions -= 1
                else:
                    # METRICS
                    self.metrics['entries_attempted'] += 1
                    error_msg = (
                        result.error if hasattr(result, "error") and result.error else "Unknown error"
                    )
                    self.logger.warning(
                        f"[E] entry_fail sym={entry_symbol} side={entry_side} msg={error_msg[:80]}"
                    )
                    # Cooldown failed symbols to prevent retry spam
                    if not hasattr(self, '_entry_fail_cooldowns'):
                        self._entry_fail_cooldowns = {}
                    # Use error catalog for cooldown strategy
                    from .error_catalog import should_block_symbol, classify, RetryStrategy
                    _, strategy, _ = classify(error_msg)
                    if strategy == RetryStrategy.PERMANENT_SKIP:
                        if not hasattr(self, '_blocked_symbols'):
                            self._blocked_symbols = set()
                        self._blocked_symbols.add(entry_symbol)
                        self._entry_fail_cooldowns[entry_symbol] = float('inf')  # Permanent
                        self.logger.warning(f"[BLOCKED_PERM] {entry_symbol} — {error_msg[:80]}")
                    else:
                        cooldown_sec = 120
                        self._entry_fail_cooldowns[entry_symbol] = time.time() + cooldown_sec
        
        # Track scan completion
        scan_end_time = time.time()
        scan_duration = scan_end_time - scan_start_time
        self.last_scan_end = scan_end_time
        self.is_scanning = False
        
        # LOGGING V2: Detailed scan summary moved to DEBUG (too verbose for INFO)
        # Counts at each filtering stage
        total_candidates = total_symbols  # Symbols scanned
        signals_after_generator = signals_found  # Signals that passed signal generator filters
        signals_after_circuit_breaker = signals_found - signals_blocked_circuit_breaker  # Signals after circuit breaker check
        signals_after_position_manager = signals_pass_position_manager  # Signals that passed position_manager.can_enter_position()
        final_entries_attempted = entries_attempted  # Actually attempted entries
        
        # DIAGNOSTIC: Get signal generator filter stats if available
        signal_filter_stats = {}
        if hasattr(self.signal_generator, '_filter_stats'):
            signal_filter_stats = self.signal_generator._filter_stats.copy()
        
        # DEBUG only: Detailed scan summary (moved from INFO to reduce log volume)
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(
                f"SCAN_DETAIL candidates={total_candidates} | "
                f"after_generator={signals_after_generator} | "
                f"after_circuit_breaker={signals_after_circuit_breaker} | "
                f"after_position_manager={signals_after_position_manager} | "
                f"entries_attempted={final_entries_attempted} | "
                f"blocked_circuit_breaker={signals_blocked_circuit_breaker} | "
                f"blocked_position_manager={signals_blocked_position_manager} | "
                f"skip_reasons={dict(symbols_skipped)} | "
                f"current_positions={current_positions}/{MAX_CONCURRENT_POS} | "
                f"signal_filters={signal_filter_stats}"
            )
        
        # Calculate cache hit rate
        total_cache_accesses = cache_hits + cache_misses
        cache_hit_rate = (cache_hits / total_cache_accesses * 100.0) if total_cache_accesses > 0 else 0.0
        
        # Calculate orderbook success rate
        total_orderbook_attempts = orderbook_success + orderbook_failures
        orderbook_success_rate = (orderbook_success / total_orderbook_attempts * 100.0) if total_orderbook_attempts > 0 else 0.0
        
        # GENERATE SINGLE SUMMARY LINE (aggregated from all signals in this loop)
        from datetime import datetime
        from .logger import get_log_buffer
        
        # PURE_SCALPER: Enhanced summary with filter breakdown
        scan_num = self.debug_scan_counter
        if signals_found > 0 or passed_filters > 0 or rejected_score > 0 or rejected_percentile > 0 or rejected_micro > 0 or rejected_risk > 0 or rejected_capacity > 0:
            # Calculate best and average score
            best_score = max(loop_stats['scores']) if loop_stats['scores'] else 0.0
            avg_score = sum(loop_stats['scores']) / len(loop_stats['scores']) if loop_stats['scores'] else 0.0
            
            # Compact scan summary: [S]#N sig=N pass=N rj=N top=reason
            # Find top rejection reason
            top_reject_reason = "none"
            reject_counts = {
                'score': rejected_score,
                'pct': rejected_percentile,
                'micro': rejected_micro,
                'risk': rejected_risk,
                'cap': rejected_capacity
            }
            if any(reject_counts.values()):
                top_reject_reason = max(reject_counts.items(), key=lambda x: x[1])[0]
            
            total_rejected = rejected_score + rejected_percentile + rejected_micro + rejected_risk + rejected_capacity
            
            # Compact summary line (< 100 chars)
            summary = (
                f"[S]#{scan_num} sig={signals_found} pass={passed_filters} "
                f"rj={total_rejected} top={top_reject_reason}"
            )
            
            # Log compact summary to file (INFO level)
            self.logger.info(summary)
            
            # NEWS SENTIMENT PREFETCH: every 90 scans (~15 min), background-scan ALL tokens
            if not hasattr(self, '_news_prefetch_counter'):
                self._news_prefetch_counter = 0
            self._news_prefetch_counter += 1
            if self._news_prefetch_counter % 90 == 0:
                try:
                    from .news_sentiment import get_news_sentiment
                    _ns = get_news_sentiment()
                    # Wide net for news discovery ($0.01+, $10M+ vol)
                    _to_scan = []
                    for _s, _st in self.universe.stats.items():
                        _px = getattr(_st, 'mark', 0) or getattr(_st, 'last', 0)
                        _vol = getattr(_st, 'vol_quote', 0) or 0
                        if _px >= 0.01 and _vol >= 10_000_000:
                            _to_scan.append(_s)
                    asyncio.create_task(self._prefetch_news(_ns, _to_scan))
                except Exception:
                    pass

            # Also add to UI buffer (for UI LOG panel)
            log_buffer = get_log_buffer()
            log_buffer.append(
                datetime.now(),
                'INFO',
                summary
            )
        elif loop_stats['signals'] > 0:
            # Fallback: Original summary format if no filter breakdown available
            best_score = max(loop_stats['scores']) if loop_stats['scores'] else 0.0
            avg_score = sum(loop_stats['scores']) / len(loop_stats['scores']) if loop_stats['scores'] else 0.0
            time_str = datetime.now().strftime("%H:%M:%S")
            summary = (
                f"{time_str}  SIG={loop_stats['signals']} "
                f"UNI={loop_stats['unicorns']} "
                f"L={loop_stats['longs']} "
                f"S={loop_stats['shorts']} "
                f"best={best_score:.1f} "
                f"avg={avg_score:.1f}"
            )
            if loop_stats['rejected_by_filters'] > 0:
                summary += f"  RJ={loop_stats['rejected_by_filters']}"
            log_buffer = get_log_buffer()
            log_buffer.append(
                datetime.now(),
                'INFO',
                summary
            )
        
        # Store scan record
        scan_record = {
            'timestamp': scan_start_time,
            'duration': scan_duration,
            'symbols_processed': symbols_processed,
            'symbols_skipped': symbols_skipped.copy(),
            'total_symbols': total_symbols,
            'active_symbols': len(active_symbols),
            'discovery_symbols': len(discovery_symbols),
            'cache_hits': cache_hits,
            'cache_misses': cache_misses,
            'cache_hit_rate': cache_hit_rate,
            'orderbook_success': orderbook_success,
            'orderbook_failures': orderbook_failures,
            'orderbook_success_rate': orderbook_success_rate,
            'signals_found': signals_found,
            'entries_attempted': entries_attempted
        }
        self.scan_history.append(scan_record)
        self.scan_times.append(scan_duration)
        
        # Update aggregate statistics
        self.scan_stats['total_scans'] += 1
        self.scan_stats['total_symbols_processed'] += symbols_processed
        self.scan_stats['total_cache_hits'] += cache_hits
        self.scan_stats['total_cache_misses'] += cache_misses
        self.scan_stats['total_orderbook_success'] += orderbook_success
        self.scan_stats['total_orderbook_failures'] += orderbook_failures
        
        # FIX: Update cumulative filter stats for Signal Health panel
        # Wire the real filter stats from [FILTER] scan#N log to Signal Health
        # CRITICAL: These stats feed the Signal Health panel and [FILTER] log
        # - signals_found: Total signals generated (passed signal generator checks)
        # - passed_filters: Signals that passed filter pipeline AND position manager check (ready for entry)
        # - signals_rejected: Signals that were rejected (signals_found - passed_filters)
        self.filter_stats_cumulative['signals_total'] += signals_found
        self.filter_stats_cumulative['signals_passed'] += passed_filters
        self.filter_stats_cumulative['signals_rejected'] += (signals_found - passed_filters)
        
        # Update running average score
        if loop_stats['scores']:
            for score in loop_stats['scores']:
                self.filter_stats_cumulative['avg_score_sum'] += score
                self.filter_stats_cumulative['avg_score_count'] += 1
        
        # DEBUG: Single aggregated log per scan showing signal flow
        self.logger.debug(
            f"[DEBUG] entries_for_scan raw_signals={signals_found} filtered={passed_filters} "
            f"entry_candidates={entry_candidates} opened={entries_opened_this_scan}"
        )
    
    async def monitor_and_exit_positions(self) -> None:
        """Monitor open positions and exit when stop-loss or take-profit is hit."""
        if not self.exit_manager:
            return

        if not self.positions:
            return

        # OPTIMIZATION: Cache equity calculation (used in exit logic)
        equity = self.equity_now()
        positions_to_exit = []
        
        # OPTIMIZATION: Cache time once for all position checks
        monitor_now = time.time()

        # PRIORITY 0: Place SL/TP for any PENDING entries IMMEDIATELY.
        # These positions were entered without atomic SL/TP (-2021 retry) and are naked.
        # Must protect before anything else — monitor may timeout later.
        for symbol, position in list(self.positions.items()):
            sl_id = position.get('sl_order_id')
            tp_id = position.get('tp_order_id')
            if sl_id == "PENDING" or tp_id == "PENDING":
                side = position.get('side', 'long')
                size = position.get('size', 0)
                sl_price = position.get('stop_loss')
                tp_price = position.get('take_profit')
                try:
                    if sl_id == "PENDING" and sl_price:
                        new_sl = await asyncio.wait_for(
                            self.order_manager.place_sl_order(symbol, side, size, sl_price),
                            timeout=5.0)
                        if new_sl:
                            position['sl_order_id'] = new_sl
                            self.logger.info(f"[PROTECT] {symbol} SL placed id={new_sl} @ {sl_price:.6f}")
                    if tp_id == "PENDING" and tp_price:
                        new_tp = await asyncio.wait_for(
                            self.order_manager.place_tp_order(symbol, side, size, tp_price),
                            timeout=5.0)
                        if new_tp:
                            position['tp_order_id'] = new_tp
                            self.logger.info(f"[PROTECT] {symbol} TP placed id={new_tp} @ {tp_price:.6f}")
                except Exception as e:
                    self.logger.error(f"[PROTECT_FAIL] {symbol}: {type(e).__name__}: {e}")

        # SL/TP ORDER FILL DETECTION — monitor position, NOT orders
        # Binance closePosition orders are invisible to fetch_order (-2013).
        # Instead: check if position still exists on exchange every 5s.
        # If gone → SL/TP filled. If still open → protection is active.
        if not hasattr(self, '_last_sltp_check'):
            self._last_sltp_check = 0
        if monitor_now - self._last_sltp_check > 10:  # Every 10s (matches monitor gate)
            self._last_sltp_check = monitor_now
            all_current_positions = {}
            try:
                # Use lightweight REST call — just position amounts, no ccxt overhead
                t_fetch_start = time.time()
                raw = await asyncio.wait_for(
                    self._fetch_positions_lightweight(), timeout=10.0)
                t_fetch = time.time() - t_fetch_start
                if t_fetch > 2.0:
                    self.logger.warning(f"[SLTP_SLOW] position fetch took {t_fetch:.1f}s (unusually slow)")
                for sym, info in raw.items():
                    all_current_positions[sym] = {'symbol': sym, 'contracts': info['contracts']}
                self._last_exchange_pos_count = len(all_current_positions)
                self._last_exchange_count_ts = time.time()
            except Exception as e:
                self.logger.warning(f"[SLTP_FETCH_FAIL] batch position fetch failed after {time.time()-t_fetch_start:.1f}s: {type(e).__name__}: {e} — monitor blind this cycle")

            if all_current_positions:
                # Only process if batch fetch succeeded (avoids false exit cascade)
                pass  # fall through to for loop
            else:
                # Batch fetch failed — skip position processing, try next cycle
                self._sltp_pending_doublecheck = []

            # Position processing only runs if all_current_positions is populated
            _skip_sltp = not all_current_positions
            for symbol, position in list(self.positions.items()):
                if _skip_sltp:
                    break
                sl_id = position.get('sl_order_id')
                tp_id = position.get('tp_order_id')
                if not sl_id and not tp_id:
                    continue
                # Handle deferred SL/TP — entry filled but position wasn't visible yet
                if sl_id == "PENDING" or tp_id == "PENDING":
                    side = position.get('side', 'long')
                    size = position.get('size', 0)
                    stop_loss_price = position.get('stop_loss')
                    take_profit_price = position.get('take_profit')
                    # Batch-fetched data now uses internal format (normalized in fetch_positions)
                    matched = all_current_positions.get(symbol)
                    still_open = matched is not None
                    if still_open:
                        # Position now visible — place SL/TP
                        new_sl = await self.order_manager.place_sl_order(
                            symbol, side, size, stop_loss_price)
                        new_tp = await self.order_manager.place_tp_order(
                            symbol, side, size, take_profit_price)
                        if new_sl:
                            position['sl_order_id'] = new_sl
                            self.logger.info(f"[DEFERRED_OK] {symbol} SL placed id={new_sl}")
                        if new_tp:
                            position['tp_order_id'] = new_tp
                            self.logger.info(f"[DEFERRED_OK] {symbol} TP placed id={new_tp}")
                    else:
                        # Position not on exchange yet — keep waiting. Entry filled.
                        deferred_sec = time.time() - position.get('entry_time', time.time())
                        if deferred_sec > 600:  # 10 min — only orphan if truly failed
                            self.logger.warning(
                                f"[ORPHAN] {symbol} not on exchange after {deferred_sec:.0f}s — removing")
                            # positions dict already handles removal
                            if symbol in self.positions:
                                del self.positions[symbol]
                    continue  # Skip normal SL/TP check for pending positions

                if not sl_id and not tp_id:
                    continue
                try:
                    self.logger.debug(f"[SLTP_MONITOR] fetching positions for {symbol}...")
                    # Use batch-fetched data (now normalized to internal format)
                    still_open = symbol in all_current_positions
                    if not still_open:
                        # Collect for batch double-check below
                        if not hasattr(self, '_sltp_pending_doublecheck'):
                            self._sltp_pending_doublecheck = []
                        self._sltp_pending_doublecheck.append((symbol, position, sl_id, tp_id, symbol))
                    # else: position still open — SL/TP protection active, nothing to do
                except Exception as e:
                    self.logger.error(f"[SLTP_ERROR] {symbol}: {type(e).__name__}: {e}")

            # BATCH DOUBLE-CHECK: one re-fetch for all suspicious positions
            if hasattr(self, '_sltp_pending_doublecheck') and self._sltp_pending_doublecheck:
                await asyncio.sleep(1.0)
                doublecheck_positions = {}
                try:
                    raw2 = await asyncio.wait_for(
                        self._fetch_positions_lightweight(), timeout=6.0)
                    for sym, info in raw2.items():
                        doublecheck_positions[sym] = {'symbol': sym, 'contracts': info['contracts']}
                except Exception as e:
                    self.logger.warning(f"[SLTP_DBLCHK_FAIL] double-check fetch failed: {type(e).__name__}: {e} — deferring exits")
                    doublecheck_positions = None  # On error, don't process any exits

                for (symbol, position, sl_id, tp_id, denorm_sym) in self._sltp_pending_doublecheck:
                    still_open2 = doublecheck_positions is None or symbol in doublecheck_positions
                    if still_open2:
                        self.logger.warning(f"[SLTP_GHOST] {symbol} false exit — position still open. Ignoring.")
                        continue
                    # CONFIRMED GONE — process exit with Binance-verified price
                    entry_px = position.get('entry_price', 0)
                    side = position.get('side', 'long')
                    size = position.get('size', 0)
                    sl_price = position.get('stop_loss', 0)
                    tp_price = position.get('take_profit', 0)
                    # Determine exit price: use SL/TP trigger level (far more accurate than stale ticker)
                    # STOP_MARKET fills near stop, TAKE_PROFIT_MARKET fills near TP.
                    # Use universe price only to determine direction, then use trigger level.
                    exit_stats = self.universe.stats.get(symbol)
                    ref_price = exit_stats.last if exit_stats else entry_px
                    if side == 'long':
                        if sl_price and ref_price <= sl_price:
                            exit_px = sl_price
                            reason = "stop_loss"
                        elif tp_price and ref_price >= tp_price:
                            exit_px = tp_price
                            reason = "take_profit"
                        else:
                            exit_px = ref_price
                            reason = "take_profit" if ref_price > entry_px else "stop_loss"
                    else:
                        if sl_price and ref_price >= sl_price:
                            exit_px = sl_price
                            reason = "stop_loss"
                        elif tp_price and ref_price <= tp_price:
                            exit_px = tp_price
                            reason = "take_profit"
                        else:
                            exit_px = ref_price
                            reason = "take_profit" if ref_price < entry_px else "stop_loss"
                    if tp_id and tp_id not in ("RECONCILED", "EXISTS", "PENDING", "ATOMIC"):
                        try: await self.order_manager.cancel_order(symbol, tp_id)
                        except Exception: pass
                    if sl_id and sl_id not in ("RECONCILED", "EXISTS", "PENDING", "ATOMIC"):
                        try: await self.order_manager.cancel_order(symbol, sl_id)
                        except Exception: pass
                    initial_sl = position.get('initial_stop_price', position.get('stop_loss', 0))
                    hold_sec = time.time() - position.get('entry_time', time.time())
                    if side == 'long':
                        pnl = (exit_px - entry_px) * size
                        r_multiple = (exit_px - entry_px) / (entry_px - initial_sl) if initial_sl and entry_px != initial_sl else 0
                    else:
                        pnl = (entry_px - exit_px) * size
                        r_multiple = (entry_px - exit_px) / (initial_sl - entry_px) if initial_sl and initial_sl != entry_px else 0
                    self.logger.info(f"[SLTP_FILL] {symbol} {reason} @ {exit_px:.6f} PnL=${pnl:.4f} R={r_multiple:.2f} hold={hold_sec:.0f}s")
                    self.realized_pnl_total += pnl
                    if pnl > 0:
                        self.win_count += 1; self.gross_win += pnl
                    else:
                        self.loss_count += 1; self.gross_loss += abs(pnl)
                    if not hasattr(self, '_recent_exit_pnls'): self._recent_exit_pnls = []
                    self._recent_exit_pnls.append(pnl)
                    if len(self._recent_exit_pnls) > 10: self._recent_exit_pnls.pop(0)
                    from .decision_event import DecisionEvent, log_trade_decision
                    decision = DecisionEvent(
                        timestamp=time.time(), action="EXIT", symbol=symbol,
                        side=side.upper(), price=exit_px, entry_price=entry_px,
                        size=size, reason=reason, net_pnl=pnl, duration_sec=hold_sec, was_win=(pnl > 0))
                    log_trade_decision(decision, bot_instance=self)
                    self.logger.info(f"EXIT: {symbol} | {reason} | size={size} | price={exit_px:.2f} | pnl={pnl:.2f} | R={r_multiple:.2f} | hold={hold_sec:.0f}s")
                    self.metrics['exits_by_reason'][reason] = self.metrics['exits_by_reason'].get(reason, 0) + 1
                    # positions dict already handles removal
                    if symbol in self.positions: del self.positions[symbol]
                self._sltp_pending_doublecheck = []  # Clear for next cycle

        for symbol, position in list(self.positions.items()):
            # Skip already-queued SL/TP fills
            if any(s == symbol for s, _, _, _, _ in positions_to_exit):
                continue

            # LATENCY OPTIMIZATION: Use cached data (no API calls)
            # Note: local exit checks now run ALONGSIDE exchange SL/TP.
            # Exchange orders = safety floor. Local trailing = earlier trigger.
            # If local exit fires, it cancels exchange orders via exit pipeline.
            # Get current market price from universe stats (already cached)
            stats = self.universe.stats.get(symbol)
            if not stats:
                continue
            
            # OPTIMIZATION: Cache position attributes once to avoid repeated dict lookups
            side = position.get('side', '').lower()
            entry_price = position.get('entry_price', 0)
            entry_time = position.get('entry_time', monitor_now)
            size = position.get('size', 0)
            
            # Try cache first for ultra-fast access
            cached_ticker = self.ticker_cache.get(symbol, max_age=2.0)
            if cached_ticker:
                current_price = cached_ticker.mark or cached_ticker.last
                spread_bps = cached_ticker.spread_bps
            else:
                # Fallback to universe stats (still fast, no API call)
                current_price = stats.mark or stats.last
                spread_bps = stats.spread_bps
            
            if current_price <= 0:
                continue
            
            # OPTIMIZATION: Update peak/trough prices for ATR trailing stops
            # Use cached position attributes
            if side == 'long':
                peak_price = position.get('peak_price', entry_price)
                position['peak_price'] = max(peak_price, current_price)
                # Update peak PnL for recovery score
                peak_pnl = position.get('peak_pnl', 0.0)
                current_pnl_pct = ((current_price - entry_price) / entry_price) * 100
                if current_pnl_pct > peak_pnl:
                    position['peak_pnl'] = current_pnl_pct
            else:  # short
                trough_price = position.get('trough_price', entry_price)
                position['trough_price'] = min(trough_price, current_price)
                # Update peak PnL for recovery score
                peak_pnl = position.get('peak_pnl', 0.0)
                current_pnl_pct = ((entry_price - current_price) / entry_price) * 100
                if current_pnl_pct > peak_pnl:
                    position['peak_pnl'] = current_pnl_pct
            
            # DYNAMIC POSITION RECOVERY SCORE (PRS) EVALUATION
            # Compute recovery score and decide on actions (close, scale out, tighten stop)
            age_seconds = monitor_now - entry_time if entry_time > 0 else 0
            age_minutes = age_seconds / 60.0

            # HARD MAX AGE: kill positions stuck > 2 hours — no exceptions
            if age_seconds > 7200:
                reason = f"max_age_{int(age_minutes)}min"
                positions_to_exit.append((symbol, position, reason, current_price, 1.0))
                continue
            
            # Get trend and volatility data (simplified - can be enhanced with full indicators)
            # For now, use price action and stored ATR
            atr_pct = position.get('atr_pct', None)
            # REFACTOR: Added defensive check for stats existence before getattr
            if atr_pct is None and stats is not None:
                # Try to get from stats if available
                atr_pct = getattr(stats, 'atr_pct', None)
            
            # Determine volatility regime
            vol_regime = self.exit_manager.get_volatility_regime(atr_pct=atr_pct)
            
            # PRS ENHANCEMENT: Calculate trend direction from recent price data
            trend5 = 0  # Default neutral
            trend15 = 0  # Default neutral
            
            try:
                # Try to get recent OHLCV data for trend calculation (replay feed or fast_storage)
                ohlcv_5m = None
                ohlcv_15m = None
                
                # REPLAY MODE: Get OHLCV from replay feed
                if self.replay_mode and self.replay_feed:
                    ohlcv_5m = self.replay_feed.get_ohlcv(symbol, timeframe='5m', limit=20)
                    ohlcv_15m = self.replay_feed.get_ohlcv(symbol, timeframe='15m', limit=20)
                # LIVE MODE: Try fast_storage (if it has get_ohlcv method)
                elif hasattr(self.fast_storage, 'get_ohlcv'):
                    # Get 5-minute prices (last 20 bars = ~1.5 hours)
                    ohlcv_5m = self.fast_storage.get_ohlcv(symbol, timeframe='5m', limit=20)
                    # Get 15-minute prices (last 20 bars = ~5 hours)
                    ohlcv_15m = self.fast_storage.get_ohlcv(symbol, timeframe='15m', limit=20)
                
                if ohlcv_5m and len(ohlcv_5m) >= 10:
                    prices_5m = [bar[4] for bar in ohlcv_5m]  # Close prices
                    from .indicators import calculate_trend_direction_from_prices
                    trend5 = calculate_trend_direction_from_prices(prices_5m, ema_short=3, ema_long=8)
                else:
                    trend5 = 0
                
                if ohlcv_15m and len(ohlcv_15m) >= 10:
                    prices_15m = [bar[4] for bar in ohlcv_15m]  # Close prices
                    trend15 = calculate_trend_direction_from_prices(prices_15m, ema_short=5, ema_long=10)
                else:
                    trend15 = 0
            except Exception as e:
                # If trend calculation fails, use neutral (no impact on PRS)
                self.logger.debug(f"Trend calculation failed for {symbol}: {e}")
                trend5 = 0
                trend15 = 0
            
            # Calculate recovery score
            recovery_score = self.exit_manager.compute_recovery_score(
                position, current_price, trend5=trend5, trend15=trend15, vol_regime=vol_regime
            )
            
            # Store recovery score and age metadata for UI/logging
            position['recovery_score'] = recovery_score
            position['age_minutes'] = age_minutes
            
            # PRS DEBUG LOGGING: Log detailed PRS information
            if self.logger.isEnabledFor(logging.DEBUG):
                pnl_pct = self._calculate_pnl_pct(entry_price, current_price, side)
                self.logger.debug(
                    f"[PRS] {symbol}: score={recovery_score:.1f}, "
                    f"age={age_minutes:.1f}m, pnl={pnl_pct:.2f}%, "
                    f"peak_pnl={position.get('peak_pnl', 0):.2f}%, "
                    f"trend5={trend5}, trend15={trend15}, vol={vol_regime}"
                )
            
            # NEW ARCHITECTURE: Use RecoveryModule for PRS evaluation
            if not hasattr(self, 'recovery_module'):
                from .core.recovery import RecoveryModule
                self.recovery_module = RecoveryModule(self.exit_manager)
            
            # Evaluate PRS and get action
            prs_action = self.recovery_module.evaluate_position(
                symbol=symbol,
                position=position,
                current_price=current_price,
                now=monitor_now,
                trend5=trend5,
                trend15=trend15,
                vol_regime=vol_regime
            )
            
            # Queue PRS action if needed
            if prs_action:
                positions_to_exit.append((
                    symbol,
                    position,
                    prs_action.reason,
                    None,  # target_price (market order)
                    prs_action.exit_size_ratio
                ))

            # SCALPER UPGRADE: Use scalper-specific trailing exit logic
            from .engine.scalper_exits import evaluate_scalper_trailing
            
            # Get advanced_features for structural exit checks
            advanced_features = None
            if hasattr(self, '_advanced_features_cache'):
                advanced_features = self._advanced_features_cache.get(symbol)
            
            # Get indicators for ATR
            indicators = None
            if hasattr(self, 'indicators_cache'):
                indicators = self.indicators_cache.get(symbol)
            
            # Update bars_in_trade tracking for scalper exits
            last_bar_update = position.get('last_bar_update_time', entry_time)
            from .config import R_BAR_SCAN_CYCLE_SEC
            bar_closed = (monitor_now - last_bar_update) >= R_BAR_SCAN_CYCLE_SEC
            if bar_closed:
                position['last_bar_update_time'] = monitor_now
                position['bars_in_trade'] = position.get('bars_in_trade', 0) + 1
            
            bars_in_trade = position.get('bars_in_trade', 0)
            
            # SCALPER: Set initial stop loss if not set (0.35 * ATR)
            if not position.get('initial_stop_price'):
                if side == "long":
                    initial_stop = entry_price * (1.0 - 1.5 * (atr_pct or 0.01))
                else:
                    initial_stop = entry_price * (1.0 + 1.5 * (atr_pct or 0.01))
                position['initial_stop_price'] = initial_stop
                if not position.get('stop_loss'):
                    position['stop_loss'] = initial_stop
            
            # Evaluate scalper trailing — only if profit exceeds regime activation threshold
            scalper_action = None
            _trail_ok = False
            try:
                _rc = getattr(self, 'regime_config', None)
                if _rc:
                    _activation = getattr(_rc, 'trailing_stop_activation_pct', 1.0)
                    # Calculate current profit %
                    _profit = 0.0
                    if side == 'long':
                        _profit = (current_price - entry_price) / entry_price * 100
                    else:
                        _profit = (entry_price - current_price) / entry_price * 100
                    if _activation < 50 and _profit >= _activation:
                        _trail_ok = True
            except Exception:
                pass
            if _trail_ok:
                scalper_action = evaluate_scalper_trailing(
                    position=position,
                    current_price=current_price,
                    atr_pct=atr_pct,
                    advanced_features=advanced_features,
                    indicators=indicators,
                    side=side,
                    entry_price=entry_price,
                    entry_time=entry_time,
                    bars_in_trade=bars_in_trade
                )
            
            if scalper_action:
                if scalper_action.action == "exit":
                    positions_to_exit.append((
                        symbol,
                        position,
                        scalper_action.exit_reason or "scalper_trailing",
                        current_price,
                        scalper_action.exit_size_ratio
                    ))
                elif scalper_action.action in ("update_sl", "update_both"):
                    # Update SL in memory AND on exchange (cancel old + place new)
                    old_sl_id = position.get('sl_order_id')
                    if scalper_action.new_stop:
                        position['stop_loss'] = scalper_action.new_stop
                        # Cancel old exchange SL order if it has a real ID
                        if old_sl_id and old_sl_id not in ("PENDING", "EXISTS", "RECONCILED", "ATOMIC"):
                            try:
                                await self.order_manager.cancel_order(symbol, old_sl_id)
                            except Exception:
                                pass
                        # Place new SL on exchange
                        new_sl = await self.order_manager.place_sl_order(
                            symbol, side, size, scalper_action.new_stop)
                        if new_sl:
                            position['sl_order_id'] = new_sl
                        if self.logger.isEnabledFor(logging.INFO):
                            self.logger.info(
                                f"TRAIL_SL_UPDATE sym={symbol} SL->{scalper_action.new_stop:.4f} "
                                f"price={current_price:.4f} side={side.upper()}"
                            )
                if scalper_action.action in ("update_tp", "update_both") and scalper_action.new_tp:
                    # Update TP in memory AND on exchange (cancel old + place new)
                    old_tp_id = position.get('tp_order_id')
                    position['take_profit'] = scalper_action.new_tp
                    if old_tp_id and old_tp_id not in ("PENDING", "EXISTS", "RECONCILED", "ATOMIC"):
                        try:
                            await self.order_manager.cancel_order(symbol, old_tp_id)
                        except Exception:
                            pass
                    new_tp = await self.order_manager.place_tp_order(
                        symbol, side, size, scalper_action.new_tp)
                    if new_tp:
                        position['tp_order_id'] = new_tp
                    if self.logger.isEnabledFor(logging.INFO):
                        self.logger.info(
                            f"RATCHET_TP sym={symbol} TP->{scalper_action.new_tp:.4f} "
                            f"price={current_price:.4f} side={side.upper()}"
                        )
            
            # Dynamic trailing stop evaluation (centralized in ExitPipeline) - fallback
            # Skip in DRY simple exits mode (no partials/trailing)
            trailing_action = None
            from .config import DRY_SIMPLE_EXITS
            if (hasattr(self, 'exit_pipeline') and self.exit_pipeline and not scalper_action and 
                not (DRY_RUN and DRY_SIMPLE_EXITS)):
                trailing_action = self.exit_pipeline.evaluate_trailing(
                    symbol=symbol,
                    position=position,
                    current_price=current_price,
                    now=monitor_now
                )
            
            if trailing_action and trailing_action.partial_actions:
                for ratio, reason in trailing_action.partial_actions:
                    # For trailing stop hits, pass current_price as target_price for PnL calculation
                    # (market order, but we need price for PnL)
                    target_price_for_exit = current_price if "trailing_stop" in reason else None
                    positions_to_exit.append((symbol, position, reason, target_price_for_exit, ratio))
            
            # Check if position should be exited (existing logic continues)
            # Use R-based exit engine if enabled, otherwise fall back to legacy
            from .config import USE_R_BASED_EXITS, R_BAR_SCAN_CYCLE_SEC
            
            if USE_R_BASED_EXITS:
                # R-based exit engine
                # Check if a bar has closed (scan cycle completed)
                last_bar_update = position.get('last_bar_update_time', entry_time)
                bar_closed = (monitor_now - last_bar_update) >= R_BAR_SCAN_CYCLE_SEC
                if bar_closed:
                    position['last_bar_update_time'] = monitor_now
                
                # Get ATR for volatility-aware trailing
                atr_pct = position.get('atr_pct', None)
                
                # Determine if high volatility (for runner profile)
                # Use regime config or fallback to ATR-based heuristic
                is_high_volatility = False
                if hasattr(self, 'regime_config') and self.regime_config:
                    from .regime import TradingRegime
                    is_high_volatility = (self.regime_config.regime_type == TradingRegime.SCALPING and 
                                         getattr(self.regime_config, 'volatility_regime', '') == 'high')
                elif atr_pct and atr_pct > 0.02:  # > 2% ATR suggests high volatility
                    is_high_volatility = True
                
                should_exit, reason, target_price, exit_size_pct = self.exit_manager.should_exit_position_r_based(
                    position,
                    current_price,
                    atr_pct=atr_pct,
                    is_high_volatility=is_high_volatility,
                    bar_closed=bar_closed,
                    now_ts=monitor_now
                )
                
                if should_exit:
                    self.logger.info(f"[EXIT_SIGNAL] {symbol} reason={reason} target={target_price:.4f} price={current_price:.4f}")
                    # Spawn as background task — survives monitor timeout
                    asyncio.create_task(self._execute_single_exit(symbol, position, reason, target_price, exit_size_pct))
            else:
                # Legacy exit logic
                # OPTIMIZATION: Cache regime config lookup (used for all positions in this loop)
                if not hasattr(self, '_monitor_regime_config'):
                    self._monitor_regime_config = getattr(self, 'regime_config', None)
                regime_config = self._monitor_regime_config
                
                should_exit, reason, target_price = self.exit_manager.should_exit_position(
                    position, current_price, spread_bps, regime_config, symbol=symbol
                )

                if should_exit:
                    # EXECUTE EXIT IMMEDIATELY — don't wait for batch processing at end of monitor.
                    # Monitor can timeout before reaching batch processing, losing exits.
                    self.logger.info(f"[EXIT_SIGNAL] {symbol} reason={reason} target={target_price:.4f} price={current_price:.4f}")
                    asyncio.create_task(self._execute_single_exit(symbol, position, reason, target_price, 1.0))
        
        # OPTIMIZATION: Use cached time from position monitoring loop
        exit_now = monitor_now
        # Clear cached regime config after loop
        if hasattr(self, '_monitor_regime_config'):
            delattr(self, '_monitor_regime_config')
        
        # CANONICAL POSITION UPDATE TRACKER (prevents double-deletes and missing position errors)
        # Track which positions have been processed in this loop
        if not hasattr(self, '_exits_processed_this_loop'):
            self._exits_processed_this_loop = set()
        if not hasattr(self, '_exit_failures_logged_this_loop'):
            self._exit_failures_logged_this_loop = set()
        self._exits_processed_this_loop.clear()  # Reset for each loop
        self._exit_failures_logged_this_loop.clear()  # Reset for each loop
        
        # CANONICAL EXIT TRACKER: Reset per-loop exit event tracker
        from .position_utils import reset_loop_exit_tracker
        reset_loop_exit_tracker()
        
        # NEW ARCHITECTURE: Route all exits through ExitPipeline
        # Queue exits to pipeline instead of executing directly
        if hasattr(self, 'exit_pipeline') and self.exit_pipeline:
            for exit_data in positions_to_exit:
                # Handle both R-based (5-tuple) and legacy (4-tuple) exit data
                if len(exit_data) == 5:
                    symbol, position, reason, target_price, exit_size_pct = exit_data
                else:
                    symbol, position, reason, target_price = exit_data
                    exit_size_pct = 1.0  # Full exit for legacy
                
                # Determine priority (higher priority = exits first)
                priority = 100  # Default priority
                if "stop_loss" in reason or "circuit_breaker" in reason:
                    priority = 200  # Highest priority for risk exits
                elif "prs_full_exit" in reason:
                    priority = 150  # High priority for PRS full exits
                elif "prs_scale_out" in reason:
                    priority = 120  # Medium-high priority for PRS scale-outs
                elif "take_profit" in reason or "tp" in reason:
                    priority = 80  # Medium priority for take-profits
                else:
                    priority = 50  # Lower priority for other exits
                
                # Determine if limit order should be used
                # Trailing stops always use market orders to avoid price validation issues
                use_limit = (reason in ["take_profit", "scalp_tp_1r", "standard_partial_1r", "runner_partial_1r"]) and "trailing" not in reason.lower()
                
                # Create exit request
                exit_request = ExitRequest(
                    symbol=symbol,
                    position=position,
                    reason=reason,
                    target_price=target_price,
                    exit_size_ratio=exit_size_pct,
                    use_limit=use_limit,
                    priority=priority
                )
                
                # Cancel exchange SL/TP before local exit
                sl_id = position.get('sl_order_id')
                tp_id = position.get('tp_order_id')
                if sl_id and sl_id != "PENDING" and sl_id != "EXISTS":
                    await self.order_manager.cancel_order(symbol, sl_id)
                if tp_id and tp_id != "PENDING" and tp_id != "EXISTS":
                    await self.order_manager.cancel_order(symbol, tp_id)

                # Queue exit request
                self.exit_pipeline.queue_exit(exit_request)

            # Process all queued exits
            processed = await self.exit_pipeline.process_exits(bot_instance=self)
            # Circuit breaker: track all exits from pipeline
            if processed > 0 and not hasattr(self, "_recent_exit_pnls"): self._recent_exit_pnls = []
            if processed > 0:
                self.logger.debug(f"ExitPipeline processed {processed} exits")
        else:
            # FALLBACK: Use old exit logic if ExitPipeline not available
            # Exit positions (legacy path - will be removed after testing)
            for exit_data in positions_to_exit:
                # Handle both R-based (5-tuple) and legacy (4-tuple) exit data
                if len(exit_data) == 5:
                    symbol, position, reason, target_price, exit_size_pct = exit_data
                else:
                    symbol, position, reason, target_price = exit_data
                    exit_size_pct = 1.0  # Full exit for legacy
                
                # CRITICAL: Check if position still exists BEFORE attempting exit
                if symbol not in self.positions:
                    # Position already deleted (possibly by another exit in same loop)
                    # Log warning ONCE per symbol per loop
                    if symbol not in self._exits_processed_this_loop:
                        self.logger.warning(
                            f"Exit skipped: {symbol} not in positions dict (already closed?)"
                        )
                        self._exits_processed_this_loop.add(symbol)
                    continue  # Skip this exit in legacy fallback loop
                
                # CRITICAL: Verify position data matches
                actual_position = self.positions[symbol]
                if actual_position.get('size', 0) <= 0:
                    # Position size is 0 (already closed or invalid)
                    if symbol not in self._exits_processed_this_loop:
                        self.logger.warning(
                            f"Exit skipped: {symbol} has zero size (already closed?)"
                        )
                        self._exits_processed_this_loop.add(symbol)
                    # Clean up invalid position
                    del self.positions[symbol]
                    continue
                
                # CRITICAL: Prevent double-processing same symbol in one loop
                if symbol in self._exits_processed_this_loop:
                    # Already processed in this loop
                    continue
                
                # Mark as processed
                self._exits_processed_this_loop.add(symbol)
                
                # Use actual position from dict (not the one from exit_data, may be stale)
                position = actual_position
                
                # OPTIMIZATION: Cache position attributes to avoid repeated dict lookups
                position_side = position.get('side', '')
                entry_price = position.get('entry_price', 0)
                entry_time = position.get('entry_time', exit_now)
                position_size = position.get('size', 0)
                
                # Calculate exit size (for partial exits)
                # NOTE: Do NOT update position size here - done atomically in update_position_after_exit()
                if exit_size_pct < 1.0:
                    # Partial exit
                    exit_size = position_size * exit_size_pct
                else:
                    # Full exit
                    exit_size = position_size
                
                # Determine order type (use limit for take-profit/partial, market for stop-loss)
                use_limit = (reason in ["take_profit", "scalp_tp_1r", "standard_partial_1r", "runner_partial_1r"])
                
                # Execute exit (create temporary position dict for exit calculation)
                exit_position_dict = position.copy()
                exit_position_dict['size'] = exit_size  # Use exit size for this exit
                
                exit_result = await self.exit_manager.exit_position(
                    symbol=symbol,
                    position=exit_position_dict,
                    reason=reason,
                    target_price=target_price,
                    use_limit=use_limit
                )
                
                if exit_result.success:
                    # Calculate PnL
                    was_win = exit_result.net_pnl > 0
                    # Circuit breaker tracking
                    if not hasattr(self, '_recent_exit_pnls'):
                        self._recent_exit_pnls = []
                    self._recent_exit_pnls.append(exit_result.net_pnl)
                    if len(self._recent_exit_pnls) > 20:
                        self._recent_exit_pnls = self._recent_exit_pnls[-20:]

                    # Update statistics
                    if was_win:
                        self.win_count += 1
                        self.gross_win += exit_result.gross_pnl
                    else:
                        self.loss_count += 1
                        self.gross_loss += abs(exit_result.gross_pnl)
                    
                    self.realized_pnl_total += exit_result.net_pnl
                    self.realized_fees_total += exit_result.total_costs
                    
                    # Track fee breakdown
                    if exit_result.entry_fee is not None:
                        self.realized_entry_fees_total += exit_result.entry_fee
                    if exit_result.exit_fee is not None:
                        self.realized_exit_fees_total += exit_result.exit_fee
                    if exit_result.slippage is not None:
                        self.realized_slippage_total += exit_result.slippage
                    if exit_result.funding_cost is not None:
                        self.realized_funding_total += exit_result.funding_cost
                    
                    # CANONICAL EXIT AND LOG: Single function that both updates position AND logs DecisionEvent
                    # This ensures every successful exit/partial-exit produces exactly one DecisionEvent
                    from .position_utils import apply_exit_and_log, record_loop_exit
                    
                    # Calculate new position size
                    if exit_size_pct < 1.0:
                        # Partial exit: update size
                        new_size = position_size - exit_result.exit_size
                    else:
                        # Full exit: remove position
                        new_size = 0.0
                    
                    # Calculate PnL percentage
                    pnl_pct = 0.0
                    if entry_price > 0 and exit_result.exit_price and exit_result.exit_price > 0:
                        if position_side.lower() == 'long':  # OPTIMIZATION: Use cached value
                            pnl_pct = ((exit_result.exit_price - entry_price) / entry_price) * 100
                        else:
                            pnl_pct = ((entry_price - exit_result.exit_price) / entry_price) * 100
                    
                    # Determine action type
                    if exit_size_pct < 1.0:
                        action = "PARTIAL_EXIT" if "prs_" in reason or "scale" in reason.lower() else "SCALE_OUT"
                    else:
                        action = "EXIT"
                    
                    # Get size before and after for partial exits
                    size_before = position_size
                    size_after = new_size if exit_size_pct < 1.0 else 0.0
                    
                    # Get recovery score (PRS) if available
                    prs = position.get('recovery_score')
                    
                    # CANONICAL: Apply exit and log atomically
                    success, event_created = apply_exit_and_log(
                        positions=self.positions,
                        positions_set=set(),
                        symbol=symbol,
                        new_size=new_size,
                        action=action,
                        exit_price=exit_result.exit_price,
                        entry_price=entry_price,
                        entry_time=entry_time,
                        exit_time=exit_now,
                        exit_size=exit_result.exit_size,
                        size_before=size_before,
                        size_after=size_after,
                        side=position_side,
                        pnl_value=exit_result.net_pnl,
                        pnl_pct=pnl_pct,
                        gross_pnl=exit_result.gross_pnl,
                        net_pnl=exit_result.net_pnl,
                        total_costs=exit_result.total_costs,
                        reason=reason,
                        prs=prs,
                        was_win=was_win,
                        is_unicorn=position.get('is_unicorn', False),
                        bot_instance=self
                    )
                    
                    if not success:
                        # Position update failed (already deleted or invalid)
                        self.logger.warning(f"Position update failed: {symbol}")
                        continue
                    
                    # Track successful exit for self-check
                    if event_created:
                        record_loop_exit(symbol, action)
                    
                    # Record exit in position manager (only for full exits)
                    if exit_size_pct >= 1.0:
                        pnl_pct_for_manager = (exit_result.net_pnl / self.equity_now() * 100) if exit_result.net_pnl is not None else None
                        # Calculate profit_atr for churn tracking (if time_exit)
                        profit_atr = None
                        if reason and reason.startswith("time_exit"):
                            # Try to extract from reason string: "time_exit: 4 bars, profit=0.00ATR"
                            import re
                            match = re.search(r'profit=([\d\.-]+)ATR', reason)
                            if match:
                                try:
                                    profit_atr = float(match.group(1))
                                except (ValueError, AttributeError):
                                    pass
                            # Fallback: calculate from position data if available
                            if profit_atr is None and entry_price > 0 and exit_result.exit_price > 0:
                                atr_pct = position.get('atr_pct', None)
                                if atr_pct and atr_pct > 0:
                                    if position_side.lower() == 'long':
                                        profit_pct = ((exit_result.exit_price - entry_price) / entry_price)
                                    else:
                                        profit_pct = ((entry_price - exit_result.exit_price) / entry_price)
                                    profit_atr = profit_pct / atr_pct
                        self.position_manager.record_exit(symbol, was_win, pnl_pct=pnl_pct_for_manager, exit_reason=reason, profit_atr=profit_atr)
                    
                    # METRICS: exits by reason
                    try:
                        self.metrics['exits_by_reason'][reason] = self.metrics['exits_by_reason'].get(reason, 0) + 1
                    except Exception:
                        pass
                else:
                    # Exit failed - handle gracefully and log compact error
                    error_msg = str(exit_result.error) if exit_result.error else "Unknown error"
                    from datetime import datetime
                    time_str = datetime.now().strftime("%H:%M:%S")
                    symbol_short = symbol.replace("/USDT", "")
                    
                    # Detect specific error types and log compact messages
                    if "Invalid position" in error_msg:
                        # Position doesn't exist on exchange (likely already closed elsewhere)
                        # Only log once per symbol per loop (not spammy)
                        if symbol not in self._exit_failures_logged_this_loop:
                            self.logger.warning(
                                f"{time_str}  EXIT_FAILED {symbol_short} Invalid pos"
                            )
                            self._exit_failures_logged_this_loop.add(symbol)
                        
                        # CRITICAL: Clean up position from dict if exchange says it doesn't exist
                        # This prevents future "Invalid pos" errors for the same symbol
                        if symbol in self.positions:
                            del self.positions[symbol]
                    elif "invalid_limit_price" in error_msg or "invalid_price" in error_msg or "Invalid target price" in error_msg:
                        # Price validation errors - log compact message and debug details
                        self.logger.warning(
                            f"{time_str}  EXIT_SKIPPED {symbol_short} invalid_price"
                        )
                        # Log detailed debug info (not INFO level to keep LOG panel clean)
                        self.logger.debug(
                            f"[EXIT] {symbol} skipped: {error_msg} | "
                            f"reason={reason} | target_price={target_price} | "
                            f"use_limit={use_limit} | side={position_side} | "
                            f"entry_price={entry_price}"
                        )
                    elif "market_price_unavailable" in error_msg:
                        # Market price fetch failed - log compact message
                        self.logger.warning(
                            f"{time_str}  EXIT_SKIPPED {symbol_short} no_market_price"
                        )
                        self.logger.debug(
                            f"[EXIT] {symbol} skipped: market price unavailable | reason={reason}"
                        )
                    else:
                        # Other errors (API timeout, connection issues, etc.) - log compact format
                        error_type = type(exit_result.error).__name__ if exit_result.error else "Unknown"
                        error_msg_short = str(exit_result.error)[:80] if exit_result.error else "Unknown error"
                        self.logger.error(
                            f"[E] exit_fail sym={symbol_short} type={error_type} msg={error_msg_short}"
                        )
        
        # SELF-CHECK: Verify that every successful exit/partial-exit produced exactly one DecisionEvent
        # Use per-loop tracker instead of time-based matching for accuracy
        try:
            from .position_utils import get_loop_exit_events
            
            # Get exit events recorded in THIS loop (via apply_exit_and_log -> record_loop_exit)
            loop_exit_events = get_loop_exit_events()
            events_logged_count = len(loop_exit_events)
            
            # Count how many exits were attempted (not skipped)
            attempted_symbols = [e[0] for e in positions_to_exit]
            skipped_symbols = [s for s in self._exits_processed_this_loop if s not in [e[0] for e in loop_exit_events]]
            attempted_but_skipped = [s for s in attempted_symbols if s in skipped_symbols]
            attempted_not_skipped = [s for s in attempted_symbols if s not in attempted_but_skipped]
            
            # Count successful exits (those that created DecisionEvents)
            logged_symbols = [e[0] for e in loop_exit_events]
            successful_count = len(logged_symbols)
            
            # Expected: Every exit that was attempted (not skipped) should have succeeded and created a DecisionEvent
            # Exception: If exit_result.success == False, no DecisionEvent is created (expected behavior)
            # So we can't compare attempted vs logged directly - we need to track which ones actually succeeded
            
            # For now, just verify that logged events match what we expect
            # If we have logged events, they should all be from attempted exits
            if loop_exit_events:
                extra_symbols = [s for s in logged_symbols if s not in attempted_symbols]
                
                if extra_symbols:
                    # Extra DecisionEvents that weren't in positions_to_exit (shouldn't happen)
                    # Single-line format for LOG panel
                    extra_symbols_str = ','.join(extra_symbols[:5])  # Limit to first 5
                    if len(extra_symbols) > 5:
                        extra_symbols_str += f" (+{len(extra_symbols)-5} more)"
                    self.logger.warning(
                        f"[SELF-CHECK] Extra DecisionEvents: {extra_symbols_str}"
                    )
                
                # Log success (downgrade to DEBUG to avoid spam once stable)
                # Only log if there are actual exits to verify
                if successful_count > 0:
                    self.logger.debug(
                        f"[SELF-CHECK] {successful_count} exit events logged"
                    )
        except Exception as e:
            # Non-critical, don't break execution
            self.logger.debug(f"[SELF-CHECK] Failed to verify exit events: {e}")

        # ======== NAKED POSITION AUDIT ========
        # Every 60s, cross-reference Binance positions against internal tracking.
        # Binance is the ONLY source of truth — if a position exists on exchange
        # but not in self.positions, it's either an orphan from a crashed session
        # or a reconciliation failure. Either way: verify protection or add it.
        if not hasattr(self, '_last_naked_audit'):
            self._last_naked_audit = 0
        if monitor_now - self._last_naked_audit > 10:  # Every 10s (was 30s) — minimize naked windows
            self._last_naked_audit = monitor_now
            try:
                raw_audit = await asyncio.wait_for(
                    self._fetch_positions_lightweight(), timeout=6.0)
                # Convert lightweight format to match expected dict keys
                exchange_open = {}
                for sym, info in raw_audit.items():
                    exchange_open[sym] = {
                        'symbol': sym,
                        'contracts': info['contracts'],
                        'side': info['side'],
                        'entryPrice': info['entryPrice'],
                    }
                tracked_symbols = set(self.positions.keys())

                # STEP 1: Remove ghosts — positions in tracking but NOT on exchange
                ghosts_found = []
                for tsym in list(tracked_symbols):
                    if tsym not in exchange_open:
                        ghosts_found.append(tsym)
                for gsym in ghosts_found:
                    self.logger.warning(f"[RECONCILE] {gsym} in tracking but NOT on Binance — removing ghost")
                    # positions dict already handles removal
                    if gsym in self.positions:
                        del self.positions[gsym]
                if ghosts_found:
                    self._last_exchange_pos_count = len(exchange_open)
                    self._last_exchange_count_ts = time.time()
                    tracked_symbols = set(self.positions.keys())
                # Tracked positions with suspect SL/TP state — verify with -4130 test
                _SUSPECT_ORDER_IDS = frozenset({"RECONCILED", "PENDING", None, ""})
                _DRY_PREFIX = "DRY_"

                for normalized, pos in exchange_open.items():
                    side = pos.get('side', 'long')
                    qty = float(pos.get('contracts', 0))
                    entry = float(pos.get('entryPrice', 0))
                    close_side = 'sell' if side == 'long' else 'buy'
                    from .config import MIN_STOP_DISTANCE_PCT; _d = MIN_STOP_DISTANCE_PCT / 100.0; real_sl = round(entry * ((1 - _d) if side == 'long' else (1 + _d)), 4)
                    real_tp = round(entry * ((1 + _d) if side == 'long' else (1 - _d)), 4)

                    if normalized in tracked_symbols:
                        # Bot tracks this position — verify its protection is real, not just a sentinel
                        tracked = self.positions.get(normalized, {})
                        sl_id = str(tracked.get('sl_order_id', ''))
                        tp_id = str(tracked.get('tp_order_id', ''))
                        needs_verify = (
                            sl_id in _SUSPECT_ORDER_IDS or sl_id.startswith(_DRY_PREFIX) or
                            tp_id in _SUSPECT_ORDER_IDS or tp_id.startswith(_DRY_PREFIX)
                        )
                        if not needs_verify:
                            continue  # Has real order IDs — trust them

                        # Verify with -4130 test
                        has_sl = has_tp = False
                        try:
                            await self.exchange_wrapper.create_order(normalized, 'STOP_MARKET',
                                close_side, qty, None, {'stopPrice': real_sl, 'closePosition': 'true'})
                        except Exception as e:
                            if '4130' in str(e):
                                has_sl = True
                        try:
                            await self.exchange_wrapper.create_order(normalized, 'TAKE_PROFIT_MARKET',
                                close_side, qty, None, {'stopPrice': real_tp, 'closePosition': 'true'})
                        except Exception as e:
                            if '4130' in str(e):
                                has_tp = True

                        if has_sl and has_tp:
                            # Protection confirmed — update tracking with verified sentinel
                            tracked['sl_order_id'] = 'EXISTS'
                            tracked['tp_order_id'] = 'EXISTS'
                            self.logger.info(
                                f"[NAKED_AUDIT] {normalized} tracked + verified protected via -4130")
                        else:
                            # Tracked but naked — place real protection
                            self.logger.error(
                                f"[NAKED_AUDIT] {normalized} TRACKED BUT NAKED! Placing SL/TP.")
                            try:
                                new_sl = await self.order_manager.place_sl_order(
                                    normalized, side, qty, real_sl)
                                new_tp = await self.order_manager.place_tp_order(
                                    normalized, side, qty, real_tp)
                                tracked['sl_order_id'] = new_sl or 'RECONCILED'
                                tracked['tp_order_id'] = new_tp or 'RECONCILED'
                                self.logger.info(
                                    f"[NAKED_AUDIT] {normalized} PROTECTED: SL={new_sl} TP={new_tp}")
                            except Exception as e:
                                self.logger.critical(
                                    f"[NAKED_AUDIT] {normalized} PROTECTION FAILED: {e}")
                        continue

                    has_sl = False
                    has_tp = False
                    try:
                        await self.exchange_wrapper.create_order(normalized, 'STOP_MARKET', close_side, qty, None,
                            {'stopPrice': real_sl, 'closePosition': 'true'})
                    except Exception as e:
                        if '4130' in str(e):
                            has_sl = True

                    try:
                        await self.exchange_wrapper.create_order(normalized, 'TAKE_PROFIT_MARKET', close_side, qty, None,
                            {'stopPrice': real_tp, 'closePosition': 'true'})
                    except Exception as e:
                        if '4130' in str(e):
                            has_tp = True

                    if has_sl and has_tp:
                        # Protected but untracked — reconcile
                        self.logger.warning(f"[NAKED_AUDIT] {normalized} exists on exchange with SL+TP but not tracked. Reconciling.")
                        est_sl2 = round(entry * (0.982 if side == 'long' else 1.018), 4)
                        self.positions[normalized] = {
                            'symbol': normalized, 'side': side, 'size': qty,
                            'entry_price': entry, 'entry_time': time.time(),
                            'sl_order_id': 'RECONCILED', 'tp_order_id': 'RECONCILED',
                            'initial_stop_price': est_sl2, 'stop_loss': est_sl2,
                            'take_profit': round(entry * (1.018 if side == 'long' else 0.982), 4),
                            'signal_score': 50,  # Default — prevents instant replacement
                            'leverage': LEVERAGE_BASE
                        }
                        # already tracked in self.positions
                    else:
                        # NAKED! Place protection immediately
                        self.logger.error(f"[NAKED_AUDIT] {normalized} NAKED on exchange! Placing SL/TP NOW.")
                        real_sl = round(entry * (1 - 0.018) if side == 'long' else entry * (1 + 0.018), 4)
                        real_tp = round(entry * (1 + 0.018) if side == 'long' else entry * (1 - 0.018), 4)
                        try:
                            sl_result = await self.order_manager.place_sl_order(normalized, side, qty, real_sl)
                            tp_result = await self.order_manager.place_tp_order(normalized, side, qty, real_tp)
                            self.logger.info(f"[NAKED_AUDIT] {normalized} PROTECTED: SL={sl_result} TP={tp_result}")
                            self.positions[normalized] = {
                                'symbol': normalized, 'side': side, 'size': qty,
                                'entry_price': entry, 'entry_time': time.time(),
                                'sl_order_id': sl_result, 'tp_order_id': tp_result,
                                'initial_stop_price': real_sl, 'stop_loss': real_sl,
                                'leverage': LEVERAGE_BASE
                            }
                            # already tracked in self.positions
                        except Exception as e:
                            self.logger.error(f"[NAKED_AUDIT] FAILED to protect {normalized}: {e}")
            except Exception as e:
                self.logger.error(f"[NAKED_AUDIT] Audit failed: {type(e).__name__}: {e}")

    def _log_signal_decision(self, symbol: str, signal, action: str, reason: Optional[str] = None):
        """
        Log signal decision with full score breakdown to decisions.jsonl.
        
        RECOMMENDATION #5: Filters applied to reduce log volume:
        - Always logs approved signals
        - Only logs rejections above LOG_REJECTION_MIN_SCORE or sampled at LOG_REJECTION_SAMPLE_RATE
        
        Args:
            symbol: Trading symbol
            signal: TradingSignal object
            action: "approved" or "rejected"
            reason: Rejection reason (if rejected)
        """
        from .config import LOG_ALL_APPROVALS, LOG_REJECTION_MIN_SCORE, LOG_REJECTION_SAMPLE_RATE
        import random
        
        # Apply filters to reduce log volume
        should_log = False
        
        if action == "approved":
            # Always log approved signals (default behavior)
            should_log = LOG_ALL_APPROVALS
        elif action == "rejected":
            # For rejections, apply score filter or sampling
            if signal.final_score >= LOG_REJECTION_MIN_SCORE:
                # High-score rejection - always log (interesting case)
                should_log = True
            elif random.random() < LOG_REJECTION_SAMPLE_RATE:
                # Low-score rejection - sample at configured rate
                should_log = True
            # else: skip logging low-score rejection
        
        if not should_log:
            return
        
        log_entry = {
            'timestamp': time.time(),
            'symbol': symbol,
            'side': signal.side,
            'action': action,
            'reason': reason,
            'final_score': signal.final_score,
            'strength': signal.strength,
            'signal_type': signal.signal_type,
            'entry_price': signal.entry_price,
            'stop_loss': signal.stop_loss,
            'take_profit': signal.take_profit
        }
        
        # Add full score breakdown if available
        if signal.signal_score:
            log_entry.update(signal.signal_score.to_dict())
        
        # Write asynchronously to rotating decision log
        try:
            decision_logger = get_decision_logger()
            decision_logger.log(log_entry)
        except Exception:
            # Non-critical, don't break execution
            pass

    async def run(self):
        # CRITICAL: Disable all console logging from external libraries FIRST
        from .logger import disable_external_loggers
        disable_external_loggers()
        
        # Enable print monitor in debug mode (optional)
        debug_mode = os.getenv("DEBUG_PRINT_MONITOR", "false").lower() == "true"
        if debug_mode:
            try:
                from .debug_monitor import enable_print_monitor
                enable_print_monitor()
                self.logger.debug("[DEBUG] Print monitor enabled")
            except ImportError:
                pass
        
        await self.init_exchange()

        # Pre-initialize lightweight exchange for position monitoring.
        # Done at startup to avoid network calls inside the critical monitor path.
        self._lightweight_ex_created = 0  # Set BEFORE try — prevents AttributeError on failure
        try:
            import ccxt.async_support as ccxt_async
            self._lightweight_ex = ccxt_async.binanceusdm({
                'apiKey': self.exchange_wrapper.api_key,
                'secret': self.exchange_wrapper.api_secret,
                'enableRateLimit': True,
                'timeout': 10000,
            })
            self._lightweight_ex_created = time.time()
            self.logger.info("[STARTUP] Lightweight exchange initialized")
        except Exception as e:
            self.logger.warning(f"[STARTUP] Lightweight exchange init failed: {e}")
            self._lightweight_ex = None

        # START CLEAN: reconcile stale positions on exchange BEFORE main loop.
        # The bot starts with empty position tracking — anything on exchange is a leftover.
        # Protection orders (SL/TP) are ALWAYS real regardless of DRY_RUN mode,
        # so this block runs even in DRY_RUN to verify/place protection on stale positions.
        if self.exchange_wrapper:
            try:
                pos_list = await self.exchange_wrapper.fetch_positions()
                stale = [p for p in pos_list if float(p.get('contracts', 0)) > 0]
                if stale:
                    self.logger.warning(
                        f"[STARTUP] Found {len(stale)} stale positions on exchange — checking protection"
                    )
                    for p in stale:
                        sym = p['symbol']
                        contracts = float(p['contracts'])
                        side = p['side']
                        entry = float(p.get('entryPrice', 0))
                        close_side = 'sell' if side == 'long' else 'buy'

                        # ALWAYS place fresh SL/TP at config distance — never trust stale orders.
                        # Old closePosition orders from previous sessions may be at wrong prices.
                        # Placing new ones at correct distance replaces any stale orders atomically.
                        from .config import MIN_STOP_DISTANCE_PCT
                        _dist = MIN_STOP_DISTANCE_PCT / 100.0  # Convert pct to decimal
                        sl_price = entry * (1 - _dist) if side == 'long' else entry * (1 + _dist)
                        tp_price = entry * (1 + _dist) if side == 'long' else entry * (1 - _dist)
                        has_sl = False
                        has_tp = False
                        try:
                            await self.exchange_wrapper.create_order(sym, 'STOP_MARKET', close_side, contracts, None,
                                {'stopPrice': sl_price, 'closePosition': 'true'})
                            has_sl = True
                        except Exception as e:
                            if '4130' in str(e):
                                has_sl = True  # Same price already exists
                            else:
                                self.logger.error(f"[STARTUP] SL placement failed for {sym}: {e}")
                        try:
                            await self.exchange_wrapper.create_order(sym, 'TAKE_PROFIT_MARKET', close_side, contracts, None,
                                {'stopPrice': tp_price, 'closePosition': 'true'})
                            has_tp = True
                        except Exception as e:
                            if '4130' in str(e):
                                has_tp = True  # Same price already exists
                            else:
                                self.logger.error(f"[STARTUP] TP placement failed for {sym}: {e}")

                        # If protection failed, close the position — can't trade naked
                        # Sub-dollar tokens: skip SL/TP, force-close immediately
                        if not (has_sl and has_tp) or entry < 0.50:
                            self.logger.critical(
                                f"[STARTUP] {sym} UNPROTECTABLE (entry=\${entry:.4f}) — closing position")
                            # Try up to 3 times with increasing aggression
                            for close_attempt in range(3):
                                try:
                                    await self.exchange_wrapper.create_order(
                                        sym, 'market', close_side, contracts, None,
                                        {'reduceOnly': 'true'})
                                    self.logger.info(f"[STARTUP] {sym} closed on attempt {close_attempt+1}")
                                    break
                                except Exception as ce:
                                    if close_attempt == 2:
                                        self.logger.critical(
                                            f"[STARTUP] {sym} CANNOT CLOSE after 3 attempts: {ce}. "
                                            f"Position UNPROTECTED on exchange!")
                                    await asyncio.sleep(1)
                            continue

                        status = "SL+TP placed"
                        normalized = self.exchange_wrapper.normalize_symbol(sym) if hasattr(self.exchange_wrapper, 'normalize_symbol') else sym
                        self.logger.warning(
                            f"[STARTUP] {normalized} {side} {contracts} — {status}, reconciling to tracking")
                        self.positions[normalized] = {
                            'symbol': normalized, 'side': side, 'size': contracts,
                            'entry_price': entry, 'entry_time': time.time(),
                            'sl_order_id': 'EXISTS' if has_sl else 'PENDING',
                            'tp_order_id': 'EXISTS' if has_tp else 'PENDING',
                            'initial_stop_price': sl_price, 'stop_loss': sl_price,
                            'take_profit': tp_price,
                            'signal_score': 50,
                            'leverage': LEVERAGE_BASE
                        }
                        # already tracked in self.positions
                else:
                    self.logger.info("[STARTUP] No stale positions — clean slate")
            except Exception as e:
                self.logger.warning(f"[STARTUP] Stale position check failed: {e}")

        # STARTUP ALGO RECONCILIATION: cross-check Algo Service orders (post-Dec 2025)
        if self.exchange_wrapper and self.exchange_wrapper.exchange:
            try:
                from .exchanges.binance_algo import startup_algo_reconciliation, AlgoStatus
                tracked_symbols = list(self.positions.keys())
                algo_state = await startup_algo_reconciliation(
                    self.exchange_wrapper.exchange, tracked_symbols)
                for sym, algos in algo_state.items():
                    has_sl = any(a.purpose == "SL" and a.status == AlgoStatus.NEW for a in algos)
                    has_tp = any(a.purpose == "TP" and a.status == AlgoStatus.NEW for a in algos)
                    if sym in self.positions:
                        if has_sl: self.positions[sym]['sl_order_id'] = 'EXISTS'
                        if has_tp: self.positions[sym]['tp_order_id'] = 'EXISTS'
                    self.logger.info(
                        f"[STARTUP_ALGO] {sym}: SL={'✓' if has_sl else '✗'} TP={'✓' if has_tp else '✗'} "
                        f"({len(algos)} algo orders)")
                if algo_state:
                    self.logger.info(f"[STARTUP_ALGO] Reconciled {len(algo_state)} symbols with Algo Service")
            except Exception as e:
                self.logger.warning(f"[STARTUP_ALGO] Algo reconciliation failed (non-fatal): {e}")

        # NEWS SENTIMENT INITIAL PREFETCH: scan only tradeable tokens
        try:
            from .news_sentiment import get_news_sentiment
            _ns = get_news_sentiment()
            # News scan: broader set than trading floor — find the gems
            _to_scan = []
            for _s, _st in self.universe.stats.items():
                _px = getattr(_st, 'mark', 0) or getattr(_st, 'last', 0)
                _vol = getattr(_st, 'vol_quote', 0) or 0
                if _px >= 0.01 and _vol >= 10_000_000:  # Wide net for discovery
                    _to_scan.append(_s)
            asyncio.create_task(self._prefetch_news(_ns, _to_scan))
            self.logger.info(f"[STARTUP] News scanning {len(_to_scan)} tokens (wide net)")
        except Exception as e:
            self.logger.warning(f"[STARTUP] News sentiment init skipped: {e}")

        # PRE-WARM FEATURE CACHE: compute AdvancedFeatures for top symbols before first scan
        if hasattr(self, 'feature_engine') and self.feature_engine:
            try:
                from .prewarmer import prewarm_feature_cache
                warmed = await prewarm_feature_cache(
                    self.feature_engine, self.exchange_wrapper, top_n=20, max_concurrent=3)
                self.logger.info(f"[STARTUP] Feature cache pre-warmed: {warmed} symbols")
            except Exception as e:
                self.logger.warning(f"[STARTUP] Feature pre-warm skipped: {e}")

        # Initialize Rich UI based on UI_MODE
        from .config import UI_MODE
        rich_ui_runtime = None
        ui_v2_instance = None
        
        # CRITICAL: Store original stdout/stderr BEFORE UI initialization
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        
        # NEW ARCHITECTURE: Initialize UIAdapter for print interception
        from .ui.adapter import UIAdapter
        ui_adapter = None
        if USE_RICH_UI:
            ui_adapter = UIAdapter(enable_print_interception=True)
            ui_adapter.start()
        
        # CRITICAL: Remove ALL console handlers from logger BEFORE UI starts
        # This prevents any logger messages from appearing in console
        if USE_RICH_UI:
            import logging
            # Remove console handlers from all loggers
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                if isinstance(handler, logging.StreamHandler) and handler.stream in (sys.stdout, sys.stderr):
                    root_logger.removeHandler(handler)
            
            # Remove console handlers from bot's logger
            for handler in self.logger.logger.handlers[:]:
                if isinstance(handler, logging.StreamHandler) and handler.stream in (sys.stdout, sys.stderr):
                    self.logger.logger.removeHandler(handler)
        
        if USE_RICH_UI:
            if UI_MODE == "v2":
                # Use new UI v2 with snapshot builder (FULLY STATIC, NO SCROLLING)
                try:
                    from .ui_v2 import UIv2
                    from .snapshot_builder import build_engine_snapshot
                    
                    # CRITICAL: Suppress stdout/stderr IMMEDIATELY before UI starts
                    # Redirect to /dev/null (or NUL on Windows) to prevent any output
                    import io
                    if os.name == 'nt':
                        # Windows: Use NUL device
                        null_stream = io.open(os.devnull, 'w', encoding='utf-8')
                    else:
                        # Unix: Use /dev/null
                        null_stream = io.open(os.devnull, 'w', encoding='utf-8')
                    
                    # Redirect stdout/stderr BEFORE UI initialization
                    sys.stdout = null_stream
                    sys.stderr = null_stream
                    
                    # Start UI v2 (this enters screen mode and redirects stdout/stderr)
                    ui_v2_instance = UIv2()
                    ui_v2_instance.__enter__()
                    
                    # Initial render (this will be the first thing visible)
                    initial_snapshot = build_engine_snapshot(self, debug=False)
                    ui_v2_instance.render(initial_snapshot)
                    
                    # From this point on, NO prints/logs will appear in terminal
                    # They all go to file logger
                    
                except (ImportError, Exception) as e:
                    # Restore stdout/stderr if UI failed
                    sys.stdout = original_stdout
                    sys.stderr = original_stderr
                    self.logger.warning(f"[UI] UI v2 failed, using plain UI: {type(e).__name__}: {e}", exc_info=True)
                    ui_v2_instance = None
            else:
                # Use legacy UI (ui_rich.py + ui_runtime.py)
                try:
                    from .ui_runtime import RichUIRuntime, create_initial_state
                    
                    # CRITICAL: Suppress stdout/stderr IMMEDIATELY before UI starts
                    import io
                    if os.name == 'nt':
                        null_stream = io.open(os.devnull, 'w', encoding='utf-8')
                    else:
                        null_stream = io.open(os.devnull, 'w', encoding='utf-8')
                    
                    sys.stdout = null_stream
                    sys.stderr = null_stream

                    initial_state = create_initial_state(self)
                    rich_ui_runtime = RichUIRuntime()
                    rich_ui_runtime.start(initial_state)
                except (ImportError, Exception) as e:
                    sys.stdout = original_stdout
                    sys.stderr = original_stderr
                    self.logger.warning(f"[UI] Rich UI (legacy) failed, using plain UI: {type(e).__name__}: {e}", exc_info=True)
                    rich_ui_runtime = None
        else:
            # Plain UI mode - no suppression needed
            pass
        
        try:
            while True:
                # Check drawdown circuit breaker (before any trading activity)
                if self.check_drawdown_circuit_breaker():
                    self.logger.critical(
                        "TRADING HALTED: Drawdown circuit breaker active. Manual intervention required.",
                        drawdown_pct=self.get_drawdown_pct(),
                        max_drawdown_pct=MAX_DRAWDOWN_PCT
                    )
                    # Continue monitoring positions but don't enter new ones
                    # Exit existing positions will still be processed
                    await self.monitor_and_exit_positions()
                    await asyncio.sleep(1.0)  # Wait before next check
                    continue  # Skip signal scanning and new entries
                
                # OPTIMIZATION: Only run LLM controller operations periodically (not every loop)
                # These operations can be expensive and don't need to run every 100ms
                llm_check_interval = 5.0  # Check every 5 seconds
                if not hasattr(self, '_last_llm_check'):
                    self._last_llm_check = 0.0
                
                if self._get_current_time() - self._last_llm_check >= llm_check_interval:
                    await self.ctrl.check_control_patch(self)
                    self.ctrl.generate_advisor_suggestions(self)
                    self.ctrl.check_and_switch_regime(self)  # Check and switch regime if needed
                    self._last_llm_check = self._get_current_time()

                # Refresh universe every loop iteration (loop time ~50s is the effective interval)
                await self.refresh_universe()

                # Scan for signals every loop iteration
                try:
                    await asyncio.wait_for(self.scan_and_enter_signals(), timeout=30.0)
                except asyncio.TimeoutError:
                    self.logger.error("[TIMEOUT] scan_and_enter_signals() hung — skipping this cycle")
                except Exception as e:
                    self.logger.error(f"[SCAN_ERROR] {type(e).__name__}: {e}")

                # Signal confirmation cleanup (every 5 minutes)
                if hasattr(self, 'signal_confirmation'):
                    if not hasattr(self, '_last_scw_cleanup'):
                        self._last_scw_cleanup = time.time()
                    elif time.time() - self._last_scw_cleanup >= 300.0:
                        try:
                            self.signal_confirmation.cleanup_stale_signals(max_age_sec=300.0)
                            self._last_scw_cleanup = time.time()
                        except Exception:
                            pass
                
                # Monitor and exit positions — gate to every 10s (was every loop iter)
                if not hasattr(self, '_last_monitor_run'):
                    self._last_monitor_run = 0
                if time.time() - self._last_monitor_run >= 10.0:
                    self._last_monitor_run = time.time()
                    try:
                        await asyncio.wait_for(self.monitor_and_exit_positions(), timeout=30.0)
                    except asyncio.TimeoutError:
                        self.logger.error("[TIMEOUT] monitor_and_exit_positions() hung (30s limit)")
                    except Exception as e:
                        self.logger.error(f"[MONITOR_ERROR] {type(e).__name__}: {e}")
                
                # Refresh orderbooks for open positions (uses separate timer to avoid starving top-symbols refresh)
                if self.positions and time.time() - self._last_positions_ob_refresh >= self._positions_ob_interval:
                    await self._refresh_orderbooks_for_positions()
                    self._last_positions_ob_refresh = time.time()

                # UI UPDATE: Refresh every 2 seconds for better visibility
                # OPTIMIZATION: Use cached time to avoid extra time.time() call
                loop_now = time.time()
                ui_update_interval = 2.0  # Update UI every 2 seconds
                if loop_now - self.last_draw >= ui_update_interval:
                    try:
                        if ui_v2_instance is not None:
                            # UI v2: Build snapshot and render
                            from .snapshot_builder import build_engine_snapshot
                            snapshot = build_engine_snapshot(self, debug=False)
                            ui_v2_instance.render(snapshot)
                        elif rich_ui_runtime is not None:
                            # Legacy UI
                            from .ui_runtime import create_initial_state
                            state = create_initial_state(self)
                            rich_ui_runtime.update(state)
                        else:
                            # Plain UI fallback runs in thread to avoid blocking event loop
                            loop = asyncio.get_event_loop()
                            await loop.run_in_executor(None, draw_panel, self)
                    except Exception as e:
                        self.logger.error(f"[UI] UI update failed: {type(e).__name__}: {e}", exc_info=True)
                    finally:
                        self.last_draw = loop_now

                # LATENCY OPTIMIZATION: Reduce sleep to 0.1s for faster loop (was 0.25s)
                await asyncio.sleep(0.1)

                # HEARTBEAT: Log every ~30s so we know the loop is alive
                if not hasattr(self, '_heartbeat_count'):
                    self._heartbeat_count = 0
                self._heartbeat_count += 1
                if self._heartbeat_count % 300 == 0:  # Every 300 loops = ~30s
                    self.logger.debug(f"[HEARTBEAT] loop alive | scans={self._heartbeat_count//300} | pos={len(self.positions)}")
                
                # SNAPSHOT VALIDATION: Periodic check (every ~60s)
                if time.time() - self._last_metrics_log >= 60.0:
                    try:
                        # Use canonical snapshot validation
                        from .engine_snapshot import collect_engine_snapshot, validate_snapshot_consistency
                        
                        snapshot = collect_engine_snapshot(self)
                        warnings = validate_snapshot_consistency(snapshot, logger=self.logger)
                        
                        if warnings:
                            self.logger.warning(f"[SNAPSHOT VALIDATION] Found {len(warnings)} consistency issues")
                        else:
                            self.logger.debug(
                                f"[SNAPSHOT VALIDATION] All checks passed | "
                                f"Positions: {len(snapshot.open_positions)} | "
                                f"Equity: ${snapshot.performance.equity:.2f} | "
                                f"Unrealized: ${snapshot.performance.unrealized_pnl:.2f}"
                            )
                        
                        # Metrics logging
                        total_attempted = self.metrics['entries_attempted']
                        total_opened = self.metrics['entries_opened']
                        winrate = (self.win_count / (self.win_count + self.loss_count) * 100.0) if (self.win_count + self.loss_count) > 0 else 0.0
                        # DEBUG ONLY: Metrics are tracked in Performance panel (too verbose for LOG panel)
                        self.logger.debug(
                            f"[METRICS] entries_attempted={total_attempted} entries_opened={total_opened} "
                            f"winrate={winrate:.1f}% rejections={self.metrics['rejections_by_reason']} "
                            f"exits={self.metrics['exits_by_reason']}"
                        )
                    except Exception as e:
                        self.logger.debug(f"[SNAPSHOT VALIDATION] Validation failed: {e}")
                    self._last_metrics_log = time.time()
        except KeyboardInterrupt:
            # Restore stdout/stderr BEFORE logging
            if ui_v2_instance is not None or rich_ui_runtime is not None:
                sys.stdout = original_stdout
                sys.stderr = original_stderr
            self.logger.info("[EXIT] KeyboardInterrupt - shutting down gracefully")
        finally:
            # Cleanup Rich UI (both versions)
            if ui_v2_instance is not None:
                try:
                    ui_v2_instance.__exit__(None, None, None)
                except Exception:
                    pass
                finally:
                    # Restore stdout/stderr after UI cleanup
                    sys.stdout = original_stdout
                    sys.stderr = original_stderr
            if rich_ui_runtime is not None:
                try:
                    rich_ui_runtime.stop()
                except Exception:
                    pass
            try:
                # Cleanup: Close storage connection
                if hasattr(self, 'fast_storage'):
                    self.fast_storage.close()
                # Close exchange wrapper
                if self.exchange_wrapper:
                    await self.exchange_wrapper.close()
                elif self.exchange:
                    # Fallback to direct exchange close (backward compatibility)
                    await self.exchange.close()
            except Exception:
                pass
