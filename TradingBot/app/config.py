"""
Unified Configuration Module - Centralized configuration management.
All configuration values are organized into logical sections for better maintainability.
"""

import os
import json
from typing import Any, Optional, Union, Callable

# ────────────────────────────────────────────────────────────────
# Environment Variable Helper
# ────────────────────────────────────────────────────────────────

def env(name: str, default: Any = None, cast: Optional[Callable] = None) -> Any:
    """
    Get environment variable with optional type casting.
    
    Args:
        name: Environment variable name
        default: Default value if not set
        cast: Optional type cast function (int, float, str, etc.)
    
    Returns:
        Environment variable value (casted if cast provided) or default
    """
    v = os.getenv(name, default)
    if cast is None:
        return v
    try:
        return cast(v) if v is not None else default
    except Exception:
        return default


# ────────────────────────────────────────────────────────────────
# DEBUG MODE TOGGLES
# ────────────────────────────────────────────────────────────────

# Debug flags for development and troubleshooting
DEBUG_MODE = env("DEBUG_MODE", "0") in ("1", "true", "TRUE")
DEBUG_SCANNING = env("DEBUG_SCANNING", "0") in ("1", "true", "TRUE")  # Debug symbol scanning
DEBUG_SIGNALS = env("DEBUG_SIGNALS", "0") in ("1", "true", "TRUE")  # Debug signal generation
DEBUG_ORDERS = env("DEBUG_ORDERS", "0") in ("1", "true", "TRUE")  # Debug order execution
DEBUG_EXITS = env("DEBUG_EXITS", "0") in ("1", "true", "TRUE")  # Debug exit logic
DEBUG_PERFORMANCE = env("DEBUG_PERFORMANCE", "0") in ("1", "true", "TRUE")  # Debug performance metrics
DEBUG_CACHE = env("DEBUG_CACHE", "0") in ("1", "true", "TRUE")  # Debug cache operations
DEBUG_API = env("DEBUG_API", "0") in ("1", "true", "TRUE")  # Debug API calls


# ────────────────────────────────────────────────────────────────
# CORE FLAGS
# ────────────────────────────────────────────────────────────────

# Core flags
DRY_RUN = env("DRY_RUN", "1") in ("1","true","TRUE")
# OPTIMIZATION: Removed USE_WS (WebSocket support not implemented, unused)
USE_RICH_UI = env("USE_RICH_UI", "true") in ("1","true","TRUE")  # Use Rich UI (requires rich library)
# UI_MODE: "legacy" (old ui_rich.py) or "v2" (new ui_v2.py with snapshot_builder)
UI_MODE = env("UI_MODE", "v2")  # Default to v2 for clean, reliable UI

# Exchange Selection (Primary - for single exchange mode)
EXCHANGE = env("EXCHANGE", "binance_futures").lower()  # Only "binance_futures" supported

# Credentials - Binance Futures
BINANCE_API_KEY = env("BINANCE_API_KEY", "")
BINANCE_API_SECRET = env("BINANCE_API_SECRET", "")  # Use BINANCE_API_SECRET for consistency
BINANCE_SECRET = BINANCE_API_SECRET  # Alias for backward compatibility
BINANCE_TESTNET = env("BINANCE_TESTNET", "0") in ("1","true","TRUE")  # Use Binance testnet

# Validation warnings (non-blocking)
if not DRY_RUN:
    # Binance Futures only
    if EXCHANGE == "binance_futures" and (not BINANCE_API_KEY or not BINANCE_API_SECRET):
        import warnings
        warnings.warn("BINANCE_API_KEY or BINANCE_API_SECRET not set for Binance Futures", UserWarning)

# Trading params (legacy - used if not using hybrid bot)
ACCOUNT_BAL        = env("ACCOUNT_BAL","250", float)
RISK_PCT           = env("RISK_PCT","0.5", float)/100.0  # Tightened to 0.5% for realistic test run (conservative)
LEVERAGE_BASE      = env("LEVERAGE","5", int)  # Legacy base leverage (used if dynamic disabled)

# Dynamic Leverage and Position Sizing (Phase 1: External Review Implementation)
USE_DYNAMIC_LEVERAGE = env("USE_DYNAMIC_LEVERAGE", "1") in ("1","true","TRUE")  # Enable dynamic leverage based on signal strength
MIN_LEVERAGE = env("MIN_LEVERAGE", "2", int)  # Minimum leverage (2x)
MAX_LEVERAGE = env("MAX_LEVERAGE", "5", int)  # Maximum leverage (5x) - reduced for live trading safety

USE_DYNAMIC_POSITION_SIZE = env("USE_DYNAMIC_POSITION_SIZE", "1") in ("1","true","TRUE")  # Enable dynamic position sizing
BASE_POSITION_PCT = env("BASE_POSITION_PCT", "1.2", float)  # Base position size as % of capital (reduced from 1.5%)
MAX_POSITION_PCT = env("MAX_POSITION_PCT", "3.0", float)  # Maximum position size as % of capital (3.0%)
MIN_POSITION_PCT = env("MIN_POSITION_PCT", "0.5", float)  # Minimum position size as % of capital (0.5%)

# Risk Management - PURE SCALPER MODE: Simple, tight risk limits
TOTAL_RISK_BUDGET = env("TOTAL_RISK_BUDGET", "2.0", float) / 100.0  # Total risk budget as % of equity (2% = 0.02) - scalper-friendly
MAX_OPEN_POSITIONS = env("MAX_OPEN_POSITIONS", "5", int)  # Hard maximum positions for LIVE balanced scalper (3-5 typical)
MAX_CONCURRENT_POS = MAX_OPEN_POSITIONS  # Alias for backward compatibility
MAX_CONCURRENT_POS_MIN = env("MAX_CONCURRENT_POS_MIN","1", int)  # Minimum positions (scalper: 1-3)
MAX_CONCURRENT_POS_MAX = env("MAX_CONCURRENT_POS_MAX","5", int)  # LIVE MODE: Cap at 5 for balanced scalper (was 3)

# Per-trade risk bounds
MIN_RISK_PER_TRADE = env("MIN_RISK_PER_TRADE", "0.2", float) / 100.0  # 0.2% minimum per trade (below this, trades are noise)
MAX_RISK_PER_TRADE = env("MAX_RISK_PER_TRADE", "1.0", float) / 100.0  # 1% max per trade (hard upper bound)

# Calculate RISK_PCT from TOTAL_RISK_BUDGET / MAX_OPEN_POSITIONS (for backward compatibility)
# This is a default target, but actual sizing uses risk budget dynamically
RISK_PCT = TOTAL_RISK_BUDGET / MAX_OPEN_POSITIONS if MAX_OPEN_POSITIONS > 0 else 0.005  # Default 0.5% if division by zero

# Legacy/fallback risk settings (kept for backward compatibility)
MAX_RISK_PER_TRADE_PCT = env("MAX_RISK_PER_TRADE_PCT", None, float)  # If set, overrides calculated RISK_PCT
if MAX_RISK_PER_TRADE_PCT is not None:
    RISK_PCT = MAX_RISK_PER_TRADE_PCT / 100.0  # Override with explicit setting

CORRELATION_PENALTY_PCT = env("CORRELATION_PENALTY_PCT", "40.0", float) / 100.0  # Reduce size by 40% if 2+ correlated positions
STREAK_PENALTY_PCT = env("STREAK_PENALTY_PCT", "40.0", float) / 100.0  # Reduce size/leverage by 40% if 3+ losses
CORRELATION_THRESHOLD = env("CORRELATION_THRESHOLD", "2", int)  # Number of correlated positions before penalty applies
CORRELATION_BLOCK_THRESHOLD = env("CORRELATION_BLOCK_THRESHOLD", "0.90", float)  # Block entry if correlation > 0.90 with any open position (relaxed from 0.85)
STREAK_THRESHOLD = env("STREAK_THRESHOLD", "3", int)  # Number of consecutive losses before penalty applies
TREND_ALIGNMENT_REQUIRED = env("TREND_ALIGNMENT_REQUIRED", "0") in ("1","true","TRUE")  # Require 5m and 15m trend alignment with trade direction (disabled by default - too restrictive)
MAX_CAPITAL_PER_POS = env("MAX_CAPITAL_PER_POS","0.15", float)  # Max 15% of account per position - reduced for live trading safety

# Score-Aware Replacement Logic
SCORE_AWARE_REPLACEMENT_ENABLED = env("SCORE_AWARE_REPLACEMENT_ENABLED", "1") in ("1","true","TRUE")  # Enable score-aware replacement
SCORE_REPLACEMENT_MARGIN = env("SCORE_REPLACEMENT_MARGIN", "5", float)  # Reduced margin from 10→5 to free slots for higher scores

# Friction
TAKER_FEE_RATE     = env("TAKER_FEE_RATE","0.0004", float)
ENTRY_FEE_RATE     = env("ENTRY_FEE_RATE","0.0004", float)
FUNDING_RATE_PER8H = env("FUNDING_RATE_PER8H","0.0", float)
SLIPPAGE_BPS       = env("SLIPPAGE_BPS","2", float)

# Behavior - Optimized for ultra-low latency
CLOSE_FEECHURN_BPS = env("CLOSE_FEECHURN_BPS","4", float)
ENTRY_DELAY_MS     = env("ENTRY_DELAY_MS","100", int)  # Ultra-low latency: 100ms (was 300ms)
STARTUP_DELAY_SEC  = env("STARTUP_DELAY_SEC","0", int)  # Wait 0 seconds by default (entries enabled immediately) - can be overridden via env
COOLDOWN_SEC       = env("COOLDOWN_SEC","30", int)  # Reduced to 30s for faster re-entry on Binance Futures
COOLDOWN_SAME_SYMBOL = env("COOLDOWN_SAME_SYMBOL","300", int)  # 5 minute cooldown for same symbol (reduced from 30min for futures)
COOLDOWN_AFTER_EXIT = env("COOLDOWN_AFTER_EXIT","120", int)  # 2 minutes cooldown after exit (softened to prevent stale loss lockout)
COOLDOWN_DIFF_SYMBOL = env("COOLDOWN_DIFF_SYMBOL","10", int)  # Shorter cooldown for different symbols (reduced for futures)
STATE_SAVE_SEC     = env("STATE_SAVE_SEC","10", int)
MAX_ENTRIES_PER_MIN= env("MAX_ENTRIES_PER_MIN","10", int)  # Increased to 10 for Binance Futures (more aggressive, appropriate for futures)

# Entry filters
MIN_SPREAD_BPS     = env("MIN_SPREAD_BPS","5", float)  # Minimum spread for entry (5 bps) - relaxed for Binance Futures
MAX_SPREAD_BPS     = env("MAX_SPREAD_BPS","100", float)  # Absolute maximum spread (100 bps) - relaxed for Binance Futures
MIN_VOLUME_24H     = env("MIN_VOLUME_24H","1000000", float)  # Minimum $1M 24h volume - relaxed for Binance Futures
MAX_LATENCY_MS     = env("MAX_LATENCY_MS","50", int)  # Ultra-low latency: 50ms (cached data only, no API calls)
MIN_SIGNAL_STRENGTH = env("MIN_SIGNAL_STRENGTH","0.60", float)  # Minimum signal strength (0-1 scale) - relaxed for Binance Futures
# BALANCED SCALPER MODE: Moderate minimum scores (72-75 for balanced setups)
MIN_SIGNAL_SCORE = env("MIN_SIGNAL_SCORE","72", int)  # Minimum signal score for balanced scalper (72-73 range, loosened from 75)
HARD_MIN_SCORE = env("HARD_MIN_SCORE", "70", int)  # Hard minimum score threshold - signals below this are immediately rejected
SIGNAL_PERCENTILE_THRESHOLD = env("SIGNAL_PERCENTILE_THRESHOLD","0.0", float)  # LIVE MODE: Disabled (0.0 = no percentile filtering) - allows more signals
USE_SIGNAL_PERCENTILE_FILTER = env("USE_SIGNAL_PERCENTILE_FILTER", "0") in ("1","true","TRUE")  # PURE_SCALPER: Disabled by default - percentile filter not used
USE_THREE_STAGE_FILTER = env("USE_THREE_STAGE_FILTER", "1") in ("1","true","TRUE")  # PURE_SCALPER: Enabled by default but softened (microstructure only rejects garbage)
SIGNAL_HISTORY_SIZE = env("SIGNAL_HISTORY_SIZE","100", int)  # Track last 100 signals for percentile calculation

# Dynamic Threshold Adjustment (Phase 1: External Review Implementation)
DYNAMIC_THRESHOLDS_ENABLED = env("DYNAMIC_THRESHOLDS_ENABLED", "1") in ("1","true","TRUE")  # Enabled - auto-adjusts based on win rate and market conditions
THRESHOLD_ADJUSTMENT_WINDOW = env("THRESHOLD_ADJUSTMENT_WINDOW", "50", int)  # Last N trades for win rate calculation
THRESHOLD_ADJUSTMENT_STEP = env("THRESHOLD_ADJUSTMENT_STEP", "2", int)  # Points to adjust (relax/tighten) per adjustment
WIN_RATE_RELAX_THRESHOLD = env("WIN_RATE_RELAX_THRESHOLD", "60.0", float)  # Relax thresholds if win rate > 60%
WIN_RATE_TIGHTEN_THRESHOLD = env("WIN_RATE_TIGHTEN_THRESHOLD", "40.0", float)  # Tighten thresholds if win rate < 40%
MIN_SCORE_RANGE = (55, 85)  # Min and max score thresholds (hard limits) - lowered from (70,80) to match new baseline
MIN_STRENGTH_RANGE = (0.60, 0.92)  # Min and max strength thresholds (hard limits) - lowered min from 0.82 to 0.60

# Extended Multi-Dimensional Scoring (New Clean Architecture)
USE_EXTENDED_SCORING = env("USE_EXTENDED_SCORING", "1") in ("1","true","TRUE")  # Enable extended scoring system
USE_NEW_SCORING_SYSTEM = env("USE_NEW_SCORING_SYSTEM", "1") in ("1","true","TRUE")  # Use new clean scoring architecture

# Scoring v2 - Percentile-based scoring with enforced rarity
USE_SCORING_V2 = env("USE_SCORING_V2", "0") in ("1","true","TRUE")  # Enable Scoring v2 (percentile-based, enforces rarity)

# Unicorn Found Priority Protocol
UNICORN_SCORE_THRESHOLD = env("UNICORN_SCORE_THRESHOLD", "90", int)  # Score threshold for unicorn signals (≥ 90)
UNICORN_PROTOCOL_ENABLED = env("UNICORN_PROTOCOL_ENABLED", "1") in ("1","true","TRUE")  # Enable unicorn priority protocol
UNICORN_POSITION_SIZE_MULTIPLIER = env("UNICORN_POSITION_SIZE_MULTIPLIER", "1.5", float)  # Increase position size by 50% for unicorns
UNICORN_LEVERAGE_MULTIPLIER = env("UNICORN_LEVERAGE_MULTIPLIER", "1.2", float)  # Increase leverage by 20% for unicorns (capped at MAX_LEVERAGE)
UNICORN_BYPASS_COOLDOWN = env("UNICORN_BYPASS_COOLDOWN", "1") in ("1","true","TRUE")  # Bypass cooldowns for unicorns
UNICORN_BYPASS_MAX_POSITIONS = env("UNICORN_BYPASS_MAX_POSITIONS", "1") in ("1","true","TRUE")  # Allow 1-2 extra positions for unicorns
UNICORN_EXTRA_POSITION_SLOTS = env("UNICORN_EXTRA_POSITION_SLOTS", "2", int)  # Allow +2 slots for unicorn signals (always get priority)
UNICORN_BYPASS_RATE_LIMIT = env("UNICORN_BYPASS_RATE_LIMIT", "1") in ("1","true","TRUE")  # Bypass rate limits for unicorns
UNICORN_BYPASS_LOSS_STREAK = env("UNICORN_BYPASS_LOSS_STREAK", "1") in ("1","true","TRUE")  # Bypass loss streak protection for unicorns
UNICORN_BYPASS_CORRELATION = env("UNICORN_BYPASS_CORRELATION", "1") in ("1","true","TRUE")  # Allow correlated positions for unicorns

# Symbol scanning and rotation
SYMBOLS_TO_SCAN = env("SYMBOLS_TO_SCAN","200", int)  # Number of symbols to scan per cycle (aggressive, uses cached data)
MAX_ACTIVE_SYMBOLS = env("MAX_ACTIVE_SYMBOLS","500", int)  # Maximum symbols in active list (increased from 250 to 500 for better target discovery)
STALE_SYMBOL_THRESHOLD_SEC = env("STALE_SYMBOL_THRESHOLD_SEC","300", float)  # Time before symbol considered stale (5 min)
STALE_ROTATION_PCT = env("STALE_ROTATION_PCT","0.20", float)  # Percentage of active list to rotate per cycle (20%)
ROTATION_CHECK_INTERVAL_SEC = env("ROTATION_CHECK_INTERVAL_SEC","60", float)  # How often to check for rotation (1 min)

# Universe Configuration
WHITELIST_SYMBOLS = env("WHITELIST_SYMBOLS", "").split(",") if env("WHITELIST_SYMBOLS", "") else []  # Whitelist symbols (empty = all)
WHITELIST_SYMBOLS = [s.strip().upper() for s in WHITELIST_SYMBOLS if s.strip()]  # Clean and normalize
# CRITICAL FIX: Use MIN_VOLUME_24H value, don't override it
# For Binance Futures, we use the configured MIN_VOLUME_24H (5M by default)
MIN_24H_VOLUME_USDT = env("MIN_24H_VOLUME_USDT", None, float)
# Only use MIN_24H_VOLUME_USDT if explicitly set via env var, otherwise use MIN_VOLUME_24H
if MIN_24H_VOLUME_USDT is None:
    MIN_24H_VOLUME_USDT = MIN_VOLUME_24H  # Use MIN_VOLUME_24H value (5M)
# Don't override MIN_VOLUME_24H - use it as the source of truth

# Discovery scanning (Option 3: Smart Discovery)
DISCOVERY_SCAN_INTERVAL_SEC = env("DISCOVERY_SCAN_INTERVAL_SEC","45", float)  # How often to run discovery scan (45 seconds)
DISCOVERY_SYMBOLS_PER_CYCLE = env("DISCOVERY_SYMBOLS_PER_CYCLE","75", int)  # Number of discovery symbols to scan per cycle
DISCOVERY_MIN_VOLUME_24H = env("DISCOVERY_MIN_VOLUME_24H","1000000", float)  # Relaxed volume threshold for discovery ($1M vs $2M)
DISCOVERY_MAX_SPREAD_BPS = env("DISCOVERY_MAX_SPREAD_BPS","80", float)  # Relaxed spread threshold for discovery (80 bps vs 60 bps)
DISCOVERY_MIN_MOMENTUM_PCT = env("DISCOVERY_MIN_MOMENTUM_PCT","1.0", float)  # Minimum momentum for discovery candidates (1.0%)

# Signal scoring parameters
IDEAL_SPREAD_BPS = env("IDEAL_SPREAD_BPS","20", float)  # Ideal spread for SpreadScore calculation
IDEAL_LATENCY_MS = env("IDEAL_LATENCY_MS","10", int)  # Ideal latency for LatencyScore calculation
MIN_ORDERBOOK_DEPTH_PCT = env("MIN_ORDERBOOK_DEPTH_PCT","0.05", float)  # Minimum orderbook depth (5% of order size) for DepthScore

# Position sizing
USE_KELLY_SIZING   = env("USE_KELLY_SIZING","1") in ("1","true","TRUE")  # Use Kelly-adjusted sizing
MIN_POSITION_SIZE  = env("MIN_POSITION_SIZE","10", float)  # Minimum position size in USDT
MAX_POSITION_SIZE  = env("MAX_POSITION_SIZE","50", float)  # Maximum position size in USDT

# Paths
AUDIT_PATH   = env("AUDIT_PATH","trades_audit.csv")
STATE_PATH   = env("STATE_PATH","state.json")
TUNING_LOG   = env("TUNING_LOG","llm_tuning_log.csv")
CONTROL_PATH = env("CONTROL_PATH","control_patch.json")
MODES_PATH   = env("MODES_PATH","modes.json")
ENVELOPE_PATH= env("ENVELOPE_PATH","envelopes.json")

# HTTP
EXCHANGE_TIMEOUT_MS = env("EXCHANGE_TIMEOUT_MS","20000", int)
EXCHANGE_RETRIES    = env("EXCHANGE_RETRIES","3", int)

# Magic numbers extracted to constants
MIN_STOP_DISTANCE_PCT = env("MIN_STOP_DISTANCE_PCT", "0.5", float) / 100.0  # 0.5% minimum stop distance
TRAILING_STOP_ACTIVATION_PCT = env("TRAILING_STOP_ACTIVATION_PCT", "0.7", float)  # Activate trailing stop at 0.7% profit (lowered for faster activation)
TRAILING_STOP_PCT = env("TRAILING_STOP_PCT", "50.0", float) / 100.0  # Trail stop to 50% of profit

# ATR-Based Trailing Stops (Phase 1: External Review Implementation)
USE_ATR_TRAILING_STOP = env("USE_ATR_TRAILING_STOP", "1") in ("1","true","TRUE")  # Enable ATR-based trailing stops
ATR_TRAILING_MULTIPLIER = env("ATR_TRAILING_MULTIPLIER", "2.0", float)  # Default 2× ATR behind peak
ATR_TRAILING_MIN_DISTANCE_PCT = env("ATR_TRAILING_MIN_DISTANCE_PCT", "0.5", float) / 100.0  # Minimum 0.5% distance
ATR_TRAILING_SCALPING_MULTIPLIER = env("ATR_TRAILING_SCALPING_MULTIPLIER", "1.5", float)  # Tighter for scalping
ATR_TRAILING_DAY_MULTIPLIER = env("ATR_TRAILING_DAY_MULTIPLIER", "2.0", float)  # Balanced for day trading
ATR_TRAILING_SWING_MULTIPLIER = env("ATR_TRAILING_SWING_MULTIPLIER", "2.5", float)  # Wider for swing trading

# R-Based Trailing Stop Engine (canonical trailing)
USE_TRAILING_ENGINE = env("USE_TRAILING_ENGINE", "1") in ("1", "true", "TRUE")
USE_NEW_TRAILING_ENGINE = env("USE_NEW_TRAILING_ENGINE", "1") in ("1", "true", "TRUE")
TRAIL_ENGINE_START_BUFFER_R = env("TRAIL_ENGINE_START_BUFFER_R", "0.5", float)  # No trailing before 0.5R
TRAIL_ENGINE_PARTIAL_1_R = env("TRAIL_ENGINE_PARTIAL_1_R", "1.0", float)  # First partial at +1R
TRAIL_ENGINE_PARTIAL_1_SIZE = env("TRAIL_ENGINE_PARTIAL_1_SIZE", "0.25", float)  # Default 25% clip
TRAIL_ENGINE_PARTIAL_1_SL_OFFSET_R = env("TRAIL_ENGINE_PARTIAL_1_SL_OFFSET_R", "0.2", float)  # Keep SL slightly negative until proven
TRAIL_ENGINE_BREAK_EVEN_R = env("TRAIL_ENGINE_BREAK_EVEN_R", "1.5", float)  # Move to BE + buffer at 1.5R
TRAIL_ENGINE_BE_BUFFER_R = env("TRAIL_ENGINE_BE_BUFFER_R", "0.25", float)
TRAIL_ENGINE_PARTIAL_2_R = env("TRAIL_ENGINE_PARTIAL_2_R", "2.0", float)
TRAIL_ENGINE_PARTIAL_2_SIZE = env("TRAIL_ENGINE_PARTIAL_2_SIZE", "0.25", float)
TRAIL_ENGINE_LOCK_R_LEVEL = env("TRAIL_ENGINE_LOCK_R_LEVEL", "2.0", float)
TRAIL_ENGINE_LOCK_AMOUNT_R = env("TRAIL_ENGINE_LOCK_AMOUNT_R", "1.0", float)
TRAIL_ENGINE_RUNNER_START_R = env("TRAIL_ENGINE_RUNNER_START_R", "3.0", float)
TRAIL_ENGINE_RUNNER_TRAIL_DISTANCE_R = env("TRAIL_ENGINE_RUNNER_TRAIL_DISTANCE_R", "1.25", float)
TRAIL_ENGINE_MIN_R_INCREMENT = env("TRAIL_ENGINE_MIN_R_INCREMENT", "0.5", float)
TRAIL_ENGINE_MIN_UPDATE_SECONDS = env("TRAIL_ENGINE_MIN_UPDATE_SECONDS", "20", float)
BTC_TREND_THRESHOLD = env("BTC_TREND_THRESHOLD", "0.3", float)  # BTC trend threshold for correlation
WIDE_SPREAD_EXIT_THRESHOLD_BPS = env("WIDE_SPREAD_EXIT_THRESHOLD_BPS", "50", float)  # Exit if spread > 50 bps
EARLY_STOP_LOSS_PCT = env("EARLY_STOP_LOSS_PCT", "0.8", float)  # Early exit at -0.8% loss
EARLY_STOP_LOSS_MAX_PCT = env("EARLY_STOP_LOSS_MAX_PCT", "1.2", float)  # Maximum early stop loss
EARLY_STOP_MIN_TIME_SEC = env("EARLY_STOP_MIN_TIME_SEC", "60", float)  # Minimum time in position before early stop (60s)
EARLY_STOP_CONFIRMATION_COUNT = env("EARLY_STOP_CONFIRMATION_COUNT", "2", int)  # Require 2 consecutive checks before early stop

# Stale Position Kill-Switch (Prevent Dead Weight)
MAX_POSITION_AGE_SEC = env("MAX_POSITION_AGE_SEC", "1800", int)  # Maximum position age: 30 minutes (1800s)
STALE_POSITION_PNL_THRESHOLD = env("STALE_POSITION_PNL_THRESHOLD", "0.5", float)  # Auto-close if |PnL| < 0.5% after max age
STALE_POSITION_CHECK_INTERVAL_SEC = env("STALE_POSITION_CHECK_INTERVAL_SEC", "60", int)  # Check every 60 seconds
# Position Recovery Score (PRS) guardrail – minimum age (minutes) before PRS can affect exits
PRS_MIN_AGE_MIN = env("PRS_MIN_AGE_MIN", "15", float)

# Decision log rotation
DECISION_LOG_MAX_MB = env("DECISION_LOG_MAX_MB", "50", float)
DECISION_LOG_MAX_FILES = env("DECISION_LOG_MAX_FILES", "20", int)

# Decision Logging Filters (RECOMMENDATION #5: Reduce logging volume)
LOG_ALL_APPROVALS = env("LOG_ALL_APPROVALS", "1") in ("1","true","TRUE")  # Always log approved signals
LOG_REJECTION_MIN_SCORE = env("LOG_REJECTION_MIN_SCORE", "50", float)  # Only log rejections above this score
LOG_REJECTION_SAMPLE_RATE = env("LOG_REJECTION_SAMPLE_RATE", "0.1", float)  # Sample rate for low-score rejections (0.1 = 10%)

# Extended Stale Position Rules
STALE_90MIN_AGE_SEC = env("STALE_90MIN_AGE_SEC", "5400", int)  # 90 minutes (5400s) - consider closing
STALE_90MIN_PNL_THRESHOLD = env("STALE_90MIN_PNL_THRESHOLD", "0.5", float)  # Close if PnL < 0.5% at 90m
EXTENDED_LEASH_PNL_THRESHOLD = env("EXTENDED_LEASH_PNL_THRESHOLD", "0.8", float)  # If PnL > 0.8%, extend to 120m
EXTENDED_LEASH_AGE_SEC = env("EXTENDED_LEASH_AGE_SEC", "7200", int)  # 120 minutes (7200s) extended leash
STALE_DRAWDOWN_RESUME_THRESHOLD = env("STALE_DRAWDOWN_RESUME_THRESHOLD", "-0.2", float)  # If PnL drops to -0.2% while stale, cut earlier

# Drawdown Circuit Breaker (Live Trading Safety)
MAX_DRAWDOWN_PCT = env("MAX_DRAWDOWN_PCT", "5.0", float)  # Maximum allowed drawdown (5%) - circuit breaker threshold
DRAWDOWN_CIRCUIT_BREAKER_ENABLED = env("DRAWDOWN_CIRCUIT_BREAKER_ENABLED", "1") in ("1","true","TRUE")  # Enable drawdown circuit breaker

# Loss streak and risk controls (calibration overrides)
MAX_LOSS_STREAK_HARD = env("MAX_LOSS_STREAK_HARD", "999", int)  # Hard block disabled for calibration - no permanent lockout
LOSS_STREAK_CAUTION_LEVEL = env("LOSS_STREAK_CAUTION_LEVEL", "3", int)  # Log caution from 3 losses onwards (no block, just logging)
LOSS_STREAK_AUTO_RESET_SEC = env("LOSS_STREAK_AUTO_RESET_SEC", "600", int)  # Auto-reset after 10 minutes without new loss (600s)

# Tiered/DD-aware loss streak protections (disabled for calibration run)
TIERED_LOSS_STREAK_ENABLED = env("TIERED_LOSS_STREAK_ENABLED", "0") in ("1","true","TRUE")
LOSS_STREAK_TIER1 = env("LOSS_STREAK_TIER1", "3", int)
LOSS_STREAK_TIER2 = env("LOSS_STREAK_TIER2", "5", int)
LOSS_STREAK_TIER1_RISK_MULTIPLIER = env("LOSS_STREAK_TIER1_RISK_MULTIPLIER", "0.7", float)
LOSS_STREAK_TIER2_RISK_MULTIPLIER = env("LOSS_STREAK_TIER2_RISK_MULTIPLIER", "0.5", float)
LOSS_STREAK_TIER1_POSITION_MULTIPLIER = env("LOSS_STREAK_TIER1_POSITION_MULTIPLIER", "0.8", float)
LOSS_STREAK_TIER2_POSITION_MULTIPLIER = env("LOSS_STREAK_TIER2_POSITION_MULTIPLIER", "0.6", float)
LOSS_STREAK_TIER1_SCORE_BOOST = env("LOSS_STREAK_TIER1_SCORE_BOOST", "0", int)
LOSS_STREAK_TIER2_SCORE_BOOST = env("LOSS_STREAK_TIER2_SCORE_BOOST", "0", int)

DD_AWARE_LOSS_STREAK_ENABLED = env("DD_AWARE_LOSS_STREAK_ENABLED", "0") in ("1","true","TRUE")
DD_AWARE_STREAK_THRESHOLD = env("DD_AWARE_STREAK_THRESHOLD", "3", int)
DD_AWARE_DD_THRESHOLD = env("DD_AWARE_DD_THRESHOLD", "5.0", float)
AUTO_RESET_LOSS_STREAK_ENABLED = env("AUTO_RESET_LOSS_STREAK_ENABLED", "1") in ("1","true","TRUE")
AUTO_RESET_TIME_SEC = env("AUTO_RESET_TIME_SEC", "600", int)
AUTO_RESET_WINNER_PNL_THRESHOLD = env("AUTO_RESET_WINNER_PNL_THRESHOLD", "0", float)
AUTO_RESET_DD_IMPROVEMENT_THRESHOLD = env("AUTO_RESET_DD_IMPROVEMENT_THRESHOLD", "0", float)

# Loss streak state machine (explicit thresholds)
LOSS_STREAK_DEFENSE_LEVEL = env("LOSS_STREAK_DEFENSE_LEVEL", "5", int)  # 5–6 → defense
LOSS_STREAK_HARD_LEVEL = env("LOSS_STREAK_HARD_LEVEL", "7", int)        # ≥7 → pause
LOSS_STREAK_PAUSE_SEC = env("LOSS_STREAK_PAUSE_SEC", "900", int)        # 15 minutes pause
LOSS_STREAK_DECAY_SECONDS = env("LOSS_STREAK_DECAY_SECONDS", "900", int) # Auto-decay by 1 after 15 minutes
HIGH_SCORE_BYPASS = env("HIGH_SCORE_BYPASS", "90.0", float)              # Unicorn threshold for bypass

# Break-Even Rescue Protocol (BERP)
BERP_ENABLED = env("BERP_ENABLED", "1") in ("1","true","TRUE")  # Enable Break-Even Rescue Protocol
BERP_TRIGGER_AGE_SEC = env("BERP_TRIGGER_AGE_SEC", "3600", int)  # Trigger rescue at 60 minutes (3600s)
BERP_TRIGGER_PNL_THRESHOLD = env("BERP_TRIGGER_PNL_THRESHOLD", "0.3", float)  # Trigger if PnL < +0.3% (soft noise margin)
BERP_RESCUE_DURATION_SEC = env("BERP_RESCUE_DURATION_SEC", "3600", int)  # Rescue duration: 60 minutes (3600s)

# R-Based Exit Engine (Score-Aware Exit Management)
USE_R_BASED_EXITS = env("USE_R_BASED_EXITS", "1") in ("1","true","TRUE")  # Enable R-based exit engine
R_EXIT_SCALP_SCORE_MIN = env("R_EXIT_SCALP_SCORE_MIN", "60", int)  # Minimum score for scalp profile (60-69)
R_EXIT_SCALP_SCORE_MAX = env("R_EXIT_SCALP_SCORE_MAX", "69", int)  # Maximum score for scalp profile
R_EXIT_STANDARD_SCORE_MIN = env("R_EXIT_STANDARD_SCORE_MIN", "70", int)  # Minimum score for standard profile (70-85)
R_EXIT_STANDARD_SCORE_MAX = env("R_EXIT_STANDARD_SCORE_MAX", "85", int)  # Maximum score for standard profile
R_EXIT_RUNNER_SCORE_MIN = env("R_EXIT_RUNNER_SCORE_MIN", "90", int)  # Minimum score for runner profile (90+)

# Scalp Profile (60-69): Fast exits, no trailing
R_SCALP_TP_R = env("R_SCALP_TP_R", "1.0", float)  # Take profit at 1.0R
R_SCALP_TIME_STOP_BARS = env("R_SCALP_TIME_STOP_BARS", "35", int)  # Time-stop after 35 bars if not hit ±0.5R
R_SCALP_BOREDOM_RANGE = env("R_SCALP_BOREDOM_RANGE", "0.5", float)  # Exit if stays within ±0.5R for time-stop period

# Standard Profile (70-85): Partial + trailing
R_STANDARD_PARTIAL_TP_R = env("R_STANDARD_PARTIAL_TP_R", "1.0", float)  # Partial TP at 1.0R
R_STANDARD_PARTIAL_PCT = env("R_STANDARD_PARTIAL_PCT", "50", float) / 100.0  # Close 50% at partial TP
R_STANDARD_TRAIL_START_R = env("R_STANDARD_TRAIL_START_R", "1.5", float)  # Start trailing at 1.5R
R_STANDARD_TRAIL_ATR_MULT = env("R_STANDARD_TRAIL_ATR_MULT", "0.75", float)  # Trail at 0.75× ATR
R_STANDARD_MAX_R = env("R_STANDARD_MAX_R", "3.0", float)  # Hard cap target around 3R
R_STANDARD_TIME_STOP_BARS = env("R_STANDARD_TIME_STOP_BARS", "50", int)  # Time-stop after 50 bars if not hit ±0.5R
R_STANDARD_BOREDOM_RANGE = env("R_STANDARD_BOREDOM_RANGE", "0.5", float)  # Exit if stays within ±0.5R for time-stop period

# Runner Profile (90+): Small partial + aggressive trailing
R_RUNNER_PARTIAL_TP_R = env("R_RUNNER_PARTIAL_TP_R", "1.0", float)  # Small partial at 1.0R
R_RUNNER_PARTIAL_PCT = env("R_RUNNER_PARTIAL_PCT", "25", float) / 100.0  # Close 25% at partial TP
R_RUNNER_BE_MOVE_R = env("R_RUNNER_BE_MOVE_R", "0.25", float)  # Move SL to +0.25R at 1R (or breakeven)
R_RUNNER_TRAIL_START_R = env("R_RUNNER_TRAIL_START_R", "1.5", float)  # Start trailing at 1.5R
R_RUNNER_TRAIL_ATR_MULT_NORMAL = env("R_RUNNER_TRAIL_ATR_MULT_NORMAL", "1.0", float)  # Trail at 1.0× ATR (normal vol)
R_RUNNER_TRAIL_ATR_MULT_HIGH = env("R_RUNNER_TRAIL_ATR_MULT_HIGH", "1.2", float)  # Trail at 1.2× ATR (high vol)
R_RUNNER_MAX_R_NORMAL = env("R_RUNNER_MAX_R_NORMAL", "4.0", float)  # Target 3-4R in normal volatility
R_RUNNER_MAX_R_HIGH = env("R_RUNNER_MAX_R_HIGH", "5.0", float)  # Target 4-5R in high volatility
R_RUNNER_TIME_STOP_BARS = env("R_RUNNER_TIME_STOP_BARS", "100", int)  # Much laxer time-stop (100 bars)
R_RUNNER_BOREDOM_RANGE = env("R_RUNNER_BOREDOM_RANGE", "0.3", float)  # Exit if stays below +0.3R after entry phase

# Bar tracking (for time-stop calculations)
# Since this is a scalping bot, we'll use scan cycles as "bars"
# Each scan cycle counts as 1 bar
R_BAR_SCAN_CYCLE_SEC = env("R_BAR_SCAN_CYCLE_SEC", "30", int)  # Approximate scan cycle time (30 seconds)

# Signal Confirmation Window (SCW)
USE_SIGNAL_CONFIRMATION = env("USE_SIGNAL_CONFIRMATION", "0") in ("1","true","TRUE")  # Disabled for calibration run
SCW_UNICORN_CONFIRM = env("SCW_UNICORN_CONFIRM", "0", int)  # Unicorn (90+): 0 bars confirmation
SCW_STANDARD_CONFIRM = env("SCW_STANDARD_CONFIRM", "2", int)  # Standard (70-89): 2 bars confirmation
SCW_SCALP_CONFIRM = env("SCW_SCALP_CONFIRM", "3", int)  # Scalp (60-69): 3 bars confirmation
SCW_MAX_SPREAD_PCT = env("SCW_MAX_SPREAD_PCT", "0.08", float)  # Max spread for confirmation (0.08%)
SCW_MAX_BODY_ATR_MULT = env("SCW_MAX_BODY_ATR_MULT", "2.0", float)  # Max candle body = 2× ATR
SCW_VOLUME_STABLE_MULT = env("SCW_VOLUME_STABLE_MULT", "1.5", float)  # Volume <= 1.5× volume_ma
SCW_VALIDATION_BARS = env("SCW_VALIDATION_BARS", "10", int)  # Bars to check for validation

# Rank-Based Position Allocation (RPA)
USE_RANK_BASED_ALLOCATION = env("USE_RANK_BASED_ALLOCATION", "1") in ("1","true","TRUE")  # Enable RPA
RPA_MIN_SIZE_MULT = env("RPA_MIN_SIZE_MULT", "0.50", float)  # Minimum size multiplier (0.50 = 50%)
RPA_MAX_SIZE_MULT = env("RPA_MAX_SIZE_MULT", "1.00", float)  # Maximum size multiplier (1.00 = 100%)
RPA_MIN_SIZE_USD = env("RPA_MIN_SIZE_USD", "5", float)  # Minimum position size in USD
RPA_MAX_RISK_BUDGET_PCT = env("RPA_MAX_RISK_BUDGET_PCT", "0.5", float)  # Max position = 50% of total risk budget

# Score-Weighted Trailing Algorithm (SWTA)
USE_SWTA = env("USE_SWTA", "1") in ("1","true","TRUE")  # Enable SWTA
SWTA_BASE_MULTIPLIER = env("SWTA_BASE_MULTIPLIER", "1.6", float)  # More responsive trailing (was 2.0)
SWTA_START_R = env("SWTA_START_R", "1.5", float)  # Start trailing at 1.5R

# Multi-Stage Exit Framework (MSX)
USE_MSX = env("USE_MSX", "1") in ("1","true","TRUE")  # Enable MSX framework
# ──────────────────────────────────────────────
# MSX EXIT ENGINE SETTINGS
# Stage-1 = initial validation to avoid instant-regret fills.
# For scalping, waiting 3 bars is too slow and causes missed partials/trailing.
# 1 bar ensures MSX activates quickly after the first bar closes,
# which matches scalping behaviour while still filtering bad immediate entries.
MSX_STAGE1_VALIDATION_R = env("MSX_STAGE1_VALIDATION_R", "-0.3", float)  # Exit if -0.3R within validation bars
MSX_STAGE1_VALIDATION_BARS = env("MSX_STAGE1_VALIDATION_BARS", "1", int)
MSX_PARTIAL_SCALP_PCT = env("MSX_PARTIAL_SCALP_PCT", "50", float) / 100.0  # Scalp (60-69): 50% partial
MSX_PARTIAL_STANDARD_PCT = env("MSX_PARTIAL_STANDARD_PCT", "40", float) / 100.0  # Standard (70-89): 40% partial
MSX_PARTIAL_RUNNER_PCT = env("MSX_PARTIAL_RUNNER_PCT", "25", float) / 100.0  # Runner (90+): 25% partial
MSX_UNICORN_BE_R = env("MSX_UNICORN_BE_R", "0.20", float)  # Unicorn BE = entry + 0.20R (more aggressive)
MSX_TIME_STOP_SCALP_BARS = env("MSX_TIME_STOP_SCALP_BARS", "20", int)  # Scalp time-stop (was 30)
MSX_TIME_STOP_STANDARD_BARS = env("MSX_TIME_STOP_STANDARD_BARS", "40", int)  # Standard time-stop (was 60)
MSX_TIME_STOP_RUNNER_BARS = env("MSX_TIME_STOP_RUNNER_BARS", "80", int)  # Runner time-stop (was 120)
MSX_TIME_STOP_MIN_R = env("MSX_TIME_STOP_MIN_R", "0.5", float)  # Time-stop if max_r < 0.5R (was 1.0)

# MSX Stage 1 calibration controls (new)
MSX_STAGE1_ENABLED = env("MSX_STAGE1_ENABLED", "1") in ("1","true","TRUE")
MSX_EARLY_INVALIDATION_R = env("MSX_EARLY_INVALIDATION_R", "-0.5", float)  # Relaxed from -0.3R
MSX_VOL_SPIKE_MULT = env("MSX_VOL_SPIKE_MULT", "2.5", float)  # ATR spike multiplier threshold
MSX_MAX_SPREAD_STAGE1 = env("MSX_MAX_SPREAD_STAGE1", "0.0012", float)  # 12 bps

KELLY_FRACTION = env("KELLY_FRACTION", "0.5", float)  # Use half-Kelly for safety
MAX_KELLY_PCT = env("MAX_KELLY_PCT", "20.0", float) / 100.0  # Cap at 20% Kelly - reduced for live trading safety
WIN_LOSS_RATIO_ASSUMPTION = env("WIN_LOSS_RATIO_ASSUMPTION", "2.0", float)  # Assume 2:1 R:R
MIN_VOLUME_LOG_THRESHOLD = env("MIN_VOLUME_LOG_THRESHOLD", "6", float)  # log10($1M) = 6
MAX_PRICE_SANITY_CHECK = env("MAX_PRICE_SANITY_CHECK", "1e10", float)  # Maximum reasonable price
MAX_SIZE_SANITY_CHECK = env("MAX_SIZE_SANITY_CHECK", "1e6", float)  # Maximum reasonable size

# Magic number constants (extracted for clarity)
RETRY_DELAY_MULTIPLIER = env("RETRY_DELAY_MULTIPLIER", "1.5", float)  # Multiplier for retry delays
CORRELATION_NORMALIZATION_FACTOR = env("CORRELATION_NORMALIZATION_FACTOR", "2.5", float)  # Normalization factor for correlation strength
MID_PRICE_FACTOR = 0.5  # Factor for calculating mid price (0.5 = average of bid/ask)
BTC_TREND_WEAK_THRESHOLD = env("BTC_TREND_WEAK_THRESHOLD", "0.5", float)  # BTC trend threshold for weak trend detection
STRONG_MOMENTUM_THRESHOLD = env("STRONG_MOMENTUM_THRESHOLD", "2.0", float)  # Strong momentum threshold (%)
HIGH_VOLATILITY_MEDIAN_THRESHOLD = env("HIGH_VOLATILITY_MEDIAN_THRESHOLD", "2.5", float)  # High volatility median threshold (%)
HIGH_VOLATILITY_P75_THRESHOLD = env("HIGH_VOLATILITY_P75_THRESHOLD", "3.5", float)  # High volatility 75th percentile threshold (%)

def safe_read_json(path, default=None):
    if not os.path.exists(path): return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def safe_write_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        # Use logger if available, otherwise silent fail
        try:
            from .logger import get_logger
            get_logger().warning(f"JSON write failed {path}: {e}")
        except (ImportError, Exception):
            # Silently fail if logger not available (shouldn't happen)
            pass
