"""
Error Catalog — Semantic categories for Binance Futures error codes.

Pattern copied from Hummingbot + Binance official error docs.
Every exchange error maps to a semantic category + recommended action.
Ad-hoc string matching replaced with centralized, testable dispatch.
"""

from enum import Enum
from typing import Optional, Tuple


class ErrorCategory(Enum):
    """Semantic error categories — what the error MEANS, not just the code."""
    ORDER_NOT_FOUND = "order_not_found"        # -2013, -2011: order doesn't exist on venue
    ALREADY_PROTECTED = "already_protected"     # -4130: closePosition order already active
    IMMEDIATE_TRIGGER = "immediate_trigger"     # -2021: SL/TP would trigger immediately
    NO_POSITION = "no_position"                 # -4509: TIF GTE requires open position
    UNSUPPORTED_ENDPOINT = "unsupported_endpoint"  # -4120: use algo-order endpoint
    LIMIT_ONLY = "limit_only"                   # -5025: only limit orders supported
    HARD_REJECT = "hard_reject"                 # -4411, -4061: account restriction
    RATE_LIMIT = "rate_limit"                   # -1015, -1016: too many requests
    EXCHANGE_UNAVAILABLE = "exchange_unavailable"  # -1001, -1003: exchange error
    NETWORK_ERROR = "network_error"             # Timeout, connection errors
    UNKNOWN = "unknown"                         # Unrecognized error


class RetryStrategy(Enum):
    """What to do when this error is encountered."""
    NONE = "none"             # Don't retry — terminal
    IMMEDIATE = "immediate"   # Retry immediately (<1s)
    BACKOFF = "backoff"       # Retry with increasing delay
    STATE_REFRESH = "state_refresh"  # Refresh state from exchange first, then retry
    PERMANENT_SKIP = "permanent_skip"  # Block symbol permanently


# Master error map: Binance error code → (category, retry_strategy, description)
ERROR_MAP: dict = {}

def _register(code: int, category: ErrorCategory, strategy: RetryStrategy, description: str):
    """Register an error code in the catalog."""
    ERROR_MAP[str(code)] = (category, strategy, description)
    # Also register the negative version (Binance uses negative codes)
    ERROR_MAP[str(-code)] = (category, strategy, description)

# --- ORDER LIFECYCLE ERRORS ---
_register(2013, ErrorCategory.ORDER_NOT_FOUND, RetryStrategy.STATE_REFRESH,
          "Order does not exist on exchange — may have been filled, cancelled, or expired")
_register(2011, ErrorCategory.ORDER_NOT_FOUND, RetryStrategy.STATE_REFRESH,
          "Unknown order sent — cancel request failed, order not in orderbook")

# --- PROTECTION ERRORS ---
_register(4130, ErrorCategory.ALREADY_PROTECTED, RetryStrategy.NONE,
          "closePosition order already active — position IS protected")

# --- ENTRY ERRORS ---
_register(2021, ErrorCategory.IMMEDIATE_TRIGGER, RetryStrategy.BACKOFF,
          "Order would immediately trigger — SL/TP price too close to market. Widen stop.")
_register(4509, ErrorCategory.NO_POSITION, RetryStrategy.IMMEDIATE,
          "TIF GTE requires open position — position not yet visible after fill. Retry.")

# --- ENDPOINT ERRORS ---
_register(4120, ErrorCategory.UNSUPPORTED_ENDPOINT, RetryStrategy.NONE,
          "Order type not supported on this endpoint — use algo-order API")

# --- ORDER TYPE ERRORS ---
_register(5025, ErrorCategory.LIMIT_ONLY, RetryStrategy.NONE,
          "Only limit order is supported for this operation")

# --- ACCOUNT RESTRICTIONS ---
_register(4411, ErrorCategory.HARD_REJECT, RetryStrategy.PERMANENT_SKIP,
          "TradFi-Perps agreement not signed — account cannot trade stock tokens")
_register(4061, ErrorCategory.HARD_REJECT, RetryStrategy.PERMANENT_SKIP,
          "Order rejected by exchange — possible account restriction")

# --- RATE LIMITS ---
_register(1015, ErrorCategory.RATE_LIMIT, RetryStrategy.BACKOFF,
          "Too many requests — rate limit exceeded")
_register(1016, ErrorCategory.RATE_LIMIT, RetryStrategy.BACKOFF,
          "Too many orders — order rate limit exceeded")

# --- EXCHANGE ERRORS ---
_register(1001, ErrorCategory.EXCHANGE_UNAVAILABLE, RetryStrategy.BACKOFF,
          "Exchange internal error — retry with backoff")
_register(1003, ErrorCategory.EXCHANGE_UNAVAILABLE, RetryStrategy.BACKOFF,
          "Exchange busy — retry with backoff")


def classify(error_msg: str) -> Tuple[ErrorCategory, RetryStrategy, str]:
    """
    Classify an error message string into semantic category + retry strategy.

    Args:
        error_msg: The error string from Binance/ccxt (e.g., "binanceusdm {\"code\":-2013,...}")

    Returns:
        (ErrorCategory, RetryStrategy, description)
    """
    # Try to extract numeric error code from the message
    import re
    match = re.search(r'"code"\s*:\s*(-?\d+)', error_msg)
    if match:
        code = match.group(1)
        if code in ERROR_MAP:
            return ERROR_MAP[code]

    # Network/timeout errors
    if 'timeout' in error_msg.lower() or 'timed out' in error_msg.lower():
        return (ErrorCategory.NETWORK_ERROR, RetryStrategy.BACKOFF,
                "Network timeout — retry with backoff")
    if 'connection' in error_msg.lower() or 'closed' in error_msg.lower():
        return (ErrorCategory.NETWORK_ERROR, RetryStrategy.BACKOFF,
                "Connection error — retry with backoff")

    # CCXT-specific errors
    if 'InvalidOrder' in error_msg or 'invalid' in error_msg.lower():
        return (ErrorCategory.HARD_REJECT, RetryStrategy.NONE,
                "Invalid order parameters — check price/quantity")
    if 'ExchangeNotAvailable' in error_msg or 'maintenance' in error_msg.lower():
        return (ErrorCategory.EXCHANGE_UNAVAILABLE, RetryStrategy.BACKOFF,
                "Exchange unavailable — retry with backoff")

    return (ErrorCategory.UNKNOWN, RetryStrategy.NONE,
            f"Unrecognized error: {error_msg[:120]}")


def is_retryable(error_msg: str) -> bool:
    """Quick check: should this error be retried?"""
    _, strategy, _ = classify(error_msg)
    return strategy != RetryStrategy.NONE and strategy != RetryStrategy.PERMANENT_SKIP


def should_block_symbol(error_msg: str) -> bool:
    """Should this symbol be permanently blocked?"""
    _, strategy, _ = classify(error_msg)
    return strategy == RetryStrategy.PERMANENT_SKIP


def is_already_protected(error_msg: str) -> bool:
    """Does this error mean the order already exists? (-4130)"""
    cat, _, _ = classify(error_msg)
    return cat == ErrorCategory.ALREADY_PROTECTED
