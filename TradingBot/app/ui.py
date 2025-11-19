import os
import sys
import json
import time
import socket
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Iterable

from .config import DRY_RUN, LEVERAGE_BASE


class C:
    H = '\033[97m'; G = '\033[92m'; R = '\033[91m'; Y = '\033[93m'
    Cc = '\033[96m'; B = '\033[94m'; D = '\033[90m'; X = '\033[0m'; BOX = '\033[38;5;45m'


# Unicode box drawing characters with ASCII fallback for Windows
def _get_box_chars():
    """Get box drawing characters, with ASCII fallback for Windows console."""
    try:
        # Test if console supports Unicode
        encoding = sys.stdout.encoding or 'utf-8'
        if encoding.lower() in ('cp1252', 'cp437', 'ascii'):
            # Windows console - use ASCII
            return {'top': '=', 'mid': '-', 'side': '|'}
        else:
            # Unicode-capable console
            return {'top': '═', 'mid': '─', 'side': '│'}
    except (UnicodeEncodeError, LookupError):
        # REFACTOR: Handle encoding errors (e.g., cp1252 on Windows)
        # Fallback to ASCII
        return {'top': '=', 'mid': '-', 'side': '|'}

BOX_CHARS = _get_box_chars()

ANSI_ESCAPE_RE = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')


def _visible_length(text: str) -> int:
    return len(ANSI_ESCAPE_RE.sub('', text or ''))


def _pad_to_width(text: str, width: int) -> str:
    text = text or ""
    visible = _visible_length(text)
    if visible >= width:
        return text
    return text + " " * (width - visible)


def _make_panel(title: str, lines: Iterable[str], width: int) -> list[str]:
    panel_lines = [_pad_to_width(f"{C.H}{title}{C.X}", width)]
    has_body = False
    for line in lines:
        has_body = True
        panel_lines.append(_pad_to_width(f"  {line}", width))
    if not has_body:
        panel_lines.append(_pad_to_width("", width))
    return panel_lines


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def _humanize_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m or (h and s):
        parts.append(f"{m:02d}m" if h else f"{m}m")
    parts.append(f"{s:02d}s" if (h or m) else f"{s}s")
    return " ".join(parts)


def _extract_field(obj: Any, keys: Iterable[str]) -> Any:
    if isinstance(obj, dict):
        for key in keys:
            if key in obj:
                return obj[key]
    else:
        for key in keys:
            if hasattr(obj, key):
                try:
                    return getattr(obj, key)
                except AttributeError:
                    # REFACTOR: Continue to next key if attribute doesn't exist
                    continue
    return None


def _normalize_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except Exception:
            return None
    return None


# Helper formatting functions
def format_compact_number(value: float, decimals: int = 2) -> str:
    """Format number compactly (e.g., 1.2K, 3.4M)."""
    if value is None or value == 0:
        return "0"
    abs_val = abs(value)
    sign = "-" if value < 0 else ""
    
    if abs_val >= 1e9:
        return f"{sign}{abs_val/1e9:.{decimals}f}B"
    elif abs_val >= 1e6:
        return f"{sign}{abs_val/1e6:.{decimals}f}M"
    elif abs_val >= 1e3:
        return f"{sign}{abs_val/1e3:.{decimals}f}K"
    else:
        return f"{sign}{abs_val:.{decimals}f}"


def format_duration_short(seconds: float) -> str:
    """Format duration in short format (e.g., "3m 12s", "45s")."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    
    if h > 0:
        return f"{h}h {m:02d}m"
    elif m > 0:
        return f"{m}m {s:02d}s"
    else:
        return f"{s}s"


def format_percentage(value: float, decimals: int = 2, show_sign: bool = True) -> str:
    """Format percentage with sign."""
    if value is None:
        return "—"
    sign = "+" if value >= 0 and show_sign else ""
    return f"{sign}{value:.{decimals}f}%"


def format_currency(value: float, decimals: int = 2) -> str:
    """Format currency value."""
    if value is None:
        return "—"
    return f"${value:,.{decimals}f}"


def truncate_string(s: str, max_len: int) -> str:
    """Truncate string to max length."""
    if not s:
        return ""
    if len(s) <= max_len:
        return s
    return s[:max_len-3] + "..."


# OPTIMIZATION: Cache system resources to avoid blocking calls
_system_resources_cache = {'cpu_percent': 0.0, 'ram_percent': 0.0, 'last_update': 0.0}
_system_resources_ttl = 2.0  # Update every 2 seconds

def get_system_resources() -> Dict[str, float]:
    """Get system resource usage (CPU, RAM).
    OPTIMIZED: Uses non-blocking psutil calls and caching to avoid 100ms delay."""
    global _system_resources_cache
    
    now = time.time()
    # Return cached value if still valid
    if (now - _system_resources_cache['last_update']) < _system_resources_ttl:
        return {
            'cpu_percent': _system_resources_cache['cpu_percent'],
            'ram_percent': _system_resources_cache['ram_percent']
        }
    
    try:
        import psutil
        # OPTIMIZATION: Use interval=None for non-blocking call (uses last measurement)
        # This avoids the 100ms blocking delay
        cpu_percent = psutil.cpu_percent(interval=None)  # Non-blocking
        ram_percent = psutil.virtual_memory().percent  # Fast, non-blocking
        
        # Update cache
        _system_resources_cache = {
            'cpu_percent': cpu_percent,
            'ram_percent': ram_percent,
            'last_update': now
        }
        
        return {
            'cpu_percent': cpu_percent,
            'ram_percent': ram_percent
        }
    except ImportError:
        # Fallback if psutil not available
        return {
            'cpu_percent': 0.0,
            'ram_percent': 0.0
        }


def calculate_btc_trend(universe, bot=None) -> Dict[str, Any]:
    """Calculate BTC trend from universe, with fallback to cache/storage."""
    # Try both formats: BTC/USDT:USDT (full futures format) and BTC/USDT (short)
    btc_stats = None
    if universe:
        btc_stats = universe.stats.get("BTC/USDT:USDT") or universe.stats.get("BTC/USDT")
    
    # Fallback to ticker_cache or fast_storage if universe stats don't have BTC data
    if not btc_stats and bot:
        ticker_cache = getattr(bot, "ticker_cache", None)
        if ticker_cache:
            # Try both formats: BTC/USDT:USDT (full) and BTC/USDT (short)
            cached = ticker_cache.get("BTC/USDT:USDT", max_age=5.0) or ticker_cache.get("BTC/USDT", max_age=5.0)
            if cached:
                # Create minimal stats from cached data
                from .universe import SymbolStats
                btc_stats = SymbolStats("BTC/USDT:USDT")
                btc_stats.pct_change_24h = cached.pct_change_24h or 0.0
                btc_stats.spread_bps = cached.spread_bps or 0.0
        
        # Fallback to fast_storage
        if not btc_stats:
            fast_storage = getattr(bot, "fast_storage", None)
            if fast_storage:
                try:
                    # Try both formats: BTC/USDT:USDT (full) and BTC/USDT (short)
                    storage_data = fast_storage.get("BTC/USDT:USDT", max_age=5.0) or fast_storage.get("BTC/USDT", max_age=5.0)
                    if storage_data:
                        from .universe import SymbolStats
                        btc_stats = SymbolStats("BTC/USDT:USDT")
                        btc_stats.pct_change_24h = storage_data.pct_change_24h or 0.0
                        btc_stats.spread_bps = storage_data.spread_bps or 0.0
                except Exception:
                    pass
    
    if not btc_stats:
        return {
            'trend_pct': 0.0,
            'direction': 'Neutral',
            'volatility': 0.0,
            'spread': 0.0
        }
    
    trend_pct = getattr(btc_stats, 'pct_change_24h', 0.0)
    
    if trend_pct > 1.0:
        direction = "Bullish"
    elif trend_pct < -1.0:
        direction = "Bearish"
    else:
        direction = "Ranging"
    
    return {
        'trend_pct': trend_pct,
        'direction': direction,
        'volatility': abs(trend_pct),
        'spread': getattr(btc_stats, 'spread_bps', 0.0)
    }


def calculate_position_health(pos: Dict, current_price: float) -> Dict[str, Any]:
    """Calculate position health metrics."""
    entry = pos.get('entry_price', 0)
    side = pos.get('side', '').lower()
    stop_loss = pos.get('stop_loss', 0)
    
    if not entry or entry <= 0:
        return {
            'atr_distance': 0.0,
            'spread_pct': 0.0,
            'health_score': 0.0
        }
    
    # Calculate ATR distance to SL (simplified)
    if side == 'long':
        sl_distance_pct = abs((entry - stop_loss) / entry) * 100 if stop_loss > 0 else 1.0
        price_distance_pct = abs((current_price - entry) / entry) * 100
    else:
        sl_distance_pct = abs((stop_loss - entry) / entry) * 100 if stop_loss > 0 else 1.0
        price_distance_pct = abs((entry - current_price) / entry) * 100
    
    # Health score (0-1): how far from SL
    if sl_distance_pct > 0:
        health_score = min(price_distance_pct / sl_distance_pct, 2.0) / 2.0
    else:
        health_score = 0.5
    
    return {
        'atr_distance': sl_distance_pct,
        'spread_pct': 0.0,  # Would need current spread
        'health_score': health_score
    }


def collect_bot_snapshot(bot) -> Dict[str, Any]:
    """Collect comprehensive bot state snapshot for display."""
    eq = bot.equity_now()
    start = getattr(bot, "start_equity", eq)
    peak = max(getattr(bot, "equity_peak", eq), eq, start)
    bot.equity_peak = peak
    dd_pct = safe_div(peak - eq, peak, 0.0) * 100.0

    gross_win = float(getattr(bot, "gross_win", 0.0) or 0.0)
    gross_loss = float(getattr(bot, "gross_loss", 0.0) or 0.0)
    profit_factor = safe_div(gross_win, gross_loss, 0.0)
    wins = int(getattr(bot, "win_count", 0))
    losses = int(getattr(bot, "loss_count", 0))
    win_rate = safe_div(wins, wins + losses, 0.0) * 100.0

    realized_pnl = float(getattr(bot, "realized_pnl_total", 0.0) or 0.0)
    realized_fees = float(getattr(bot, "realized_fees_total", 0.0) or 0.0)
    realized_funding = float(getattr(bot, "realized_funding_total", 0.0) or 0.0)
    # Note: realized_pnl is already NET (after fees), so net_realized_after_fees = realized_pnl
    net_realized_after_fees = realized_pnl  # Already net, fees tracked separately
    realized_total = realized_pnl + realized_funding

    now = time.time()
    started_at = getattr(bot, "started_at", now)
    mode_since = getattr(bot, "mode_since", now)
    uptime_sec = now - started_at
    mode_duration = now - mode_since

    market = getattr(bot, "universe", None)
    top_movers = []
    active_symbols = []
    symbols_scanned_raw = getattr(bot, "symbols_scanned_last", 0)
    try:
        symbols_scanned = int(float(symbols_scanned_raw)) if symbols_scanned_raw is not None else 0
    except (ValueError, TypeError):
        symbols_scanned = 0
    
    # OPTIMIZATION: Try to get live data from multiple sources (limit processing)
    if market:
        active_symbols = list(getattr(market, "active", []) or [])
        stats = getattr(market, "stats", {}) or {}
        score_fn = getattr(market, "score", None)
        
        # OPTIMIZATION: Limit to top 10 symbols for UI (was 20) - reduces processing
        top_symbols_limit = 10
        
        # OPTIMIZATION: Skip expensive fallback lookups if stats exist (most common case)
        # Only do fallback if stats are truly empty
        if not stats and active_symbols:
            ticker_cache = getattr(bot, "ticker_cache", None)
            fast_storage = getattr(bot, "fast_storage", None)
            
            # Try ticker_cache first (fastest, in-memory)
            if ticker_cache:
                for sym in active_symbols[:top_symbols_limit]:
                    cached = ticker_cache.get(sym, max_age=5.0)
                    if cached:
                        # Create a minimal stats object from cached data
                        from .universe import SymbolStats
                        st = SymbolStats(sym)
                        st.last = cached.last or 0.0
                        st.mark = cached.mark or cached.last or 0.0
                        st.bid = cached.bid or 0.0
                        st.ask = cached.ask or 0.0
                        st.vol_quote = cached.quote_volume or 0.0
                        st.spread_bps = cached.spread_bps or 0.0
                        st.pct_change_24h = cached.pct_change_24h or 0.0
                        stats[sym] = st
            
            # Fallback to fast_storage if ticker_cache didn't have data
            if not stats and fast_storage:
                try:
                    storage_data = fast_storage.get_multi(active_symbols[:top_symbols_limit], max_age=5.0)
                    for sym, data in storage_data.items():
                        if data:
                            from .universe import SymbolStats
                            st = SymbolStats(sym)
                            st.last = data.last or 0.0
                            st.mark = data.mark or data.last or 0.0
                            st.bid = data.bid or 0.0
                            st.ask = data.ask or 0.0
                            st.vol_quote = data.volume or 0.0
                            st.spread_bps = data.spread_bps or 0.0
                            st.pct_change_24h = data.pct_change_24h or 0.0
                            stats[sym] = st
                except Exception:
                    pass
        
        # OPTIMIZATION: Build top_movers from stats (limit to top 10)
        for sym in active_symbols[:top_symbols_limit]:
            st = stats.get(sym)
            if not st:
                continue
            score = 0.0
            if callable(score_fn):
                try:
                    score = float(score_fn(st))
                except Exception:
                    score = 0.0
            top_movers.append({
                "symbol": sym,
                "heat": float(getattr(st, "heat", 0.0) or 0.0),
                "spread_bps": float(getattr(st, "spread_bps", 0.0) or 0.0),
                "volume_m": safe_div(float(getattr(st, "vol_quote", 0.0) or 0.0), 1e6, 0.0),
                "last": float(getattr(st, "last", 0.0) or 0.0),
                "mark": float(getattr(st, "mark", 0.0) or 0.0),
                "score": float(score),
            })

    ctrl = getattr(bot, "ctrl", None)
    info_tail = list(getattr(ctrl, "recent_info", []) or [])[-12:]
    err_tail = list(getattr(ctrl, "recent_errors", []) or [])[-12:]
    last_patch_ts = getattr(ctrl, "last_control_patch", 0) if ctrl else 0
    last_patch_iso = datetime.utcfromtimestamp(last_patch_ts).isoformat() + "Z" if last_patch_ts else None

    budget = getattr(bot, "budget", None)
    bucket_rows = []
    remaining = 0
    calls = 0
    max_cpm = 0
    if budget:
        try:
            max_cpm = int(float(getattr(budget, "max_cpm", 0) or 0))
        except (ValueError, TypeError):
            max_cpm = 0
        try:
            calls = int(float(getattr(budget, "calls", 0) or 0))
        except (ValueError, TypeError):
            calls = 0
        try:
            remaining = int(budget.remaining())
        except Exception:
            remaining = 0
        buckets = getattr(budget, "buckets", {}) or {}
        for name, count in buckets.items():
            try:
                count_val = int(float(count)) if isinstance(count, str) else int(count)
            except (ValueError, TypeError):
                count_val = 0
            bucket_rows.append({"name": name, "count": count_val})
        bucket_rows.sort(key=lambda x: x["name"])

    positions = getattr(bot, "positions", {}) or {}
    position_details = []
    # OPTIMIZATION: Limit position processing to actual positions (no need for [:20] if less)
    position_keys = list(positions.keys())
    for sym in position_keys:
        pos = positions.get(sym)
        if pos is None:
            continue
        side = _extract_field(pos, ("side", "positionSide", "direction"))
        size = _normalize_number(_extract_field(pos, ("size", "qty", "amount", "contracts", "positionAmt", "quantity", "position_size")))
        entry = _normalize_number(_extract_field(pos, ("entry_price", "entryPrice", "avgPrice", "price", "avg_entry")))
        leverage = _normalize_number(_extract_field(pos, ("leverage", "leverageUsed"))) or LEVERAGE_BASE
        
        # Get current market price from multiple sources (universe, cache, storage)
        current_price = entry  # Default to entry if no market data
        if market:
            stats = getattr(market, "stats", {}).get(sym)
            if stats:
                # Prefer mark price, fallback to last price
                current_price = _normalize_number(getattr(stats, "mark", None)) or _normalize_number(getattr(stats, "last", None)) or entry
        
        # Fallback to ticker_cache if universe stats don't have data
        if current_price == entry:
            ticker_cache = getattr(bot, "ticker_cache", None)
            if ticker_cache:
                cached = ticker_cache.get(sym, max_age=5.0)
                if cached:
                    current_price = _normalize_number(cached.mark) or _normalize_number(cached.last) or entry
        
        # Fallback to fast_storage if still no data
        if current_price == entry:
            fast_storage = getattr(bot, "fast_storage", None)
            if fast_storage:
                try:
                    storage_data = fast_storage.get(sym, max_age=5.0)
                    if storage_data:
                        current_price = _normalize_number(storage_data.mark) or _normalize_number(storage_data.last) or entry
                except Exception:
                    pass
        
        # CANONICAL ACCOUNTING: Calculate unrealized PnL using canonical helper
        from .accounting import calculate_position_pnl
        
        # Build temp position dict for calculation
        temp_pos = {
            'entry_price': entry,
            'size': size,
            'side': side
        }
        unrealized, unrealized_pct = calculate_position_pnl(temp_pos, current_price)
        
        note = ""
        if not any([side, size, entry]):
            try:
                note = str(pos)
            except Exception:
                note = ""
        
        # OPTIMIZATION: Extract all position data once to avoid repeated .get() calls
        # Extract additional position data for UI
        peak_price = _normalize_number(pos.get('peak_price'))
        trough_price = _normalize_number(pos.get('trough_price'))
        peak_pnl = _normalize_number(pos.get('peak_pnl'))
        entry_time = pos.get('entry_time', 0)
        stop_loss = _normalize_number(pos.get('stop_loss'))
        take_profit = _normalize_number(pos.get('take_profit'))
        
        position_details.append({
            "symbol": sym,
            "side": side or "",
            "size": size,
            "entry": entry,
            "mark": current_price,  # Use current market price
            "unrealized": unrealized,
            "leverage": leverage,
            "note": note,
            "peak_price": peak_price,
            "trough_price": trough_price,
            "peak_pnl": peak_pnl,
            "entry_time": entry_time,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "recovery_score": pos.get("recovery_score"),
        })

    # Enhanced data collection
    signal_history = list(getattr(bot, "signal_history", []) or [])
    recent_trades_list = list(getattr(bot, "recent_trades", []) or [])[-10:]
    loop_times_list = list(getattr(bot, "loop_times", []) or [])
    api_call_times_list = list(getattr(bot, "api_call_times", []) or [])
    
    # Filter rejected signals (last 20 rejected signals)
    # OPTIMIZATION: Extract all signal data once to avoid repeated .get() calls
    rejected_signals = []
    for signal in signal_history:
        # OPTIMIZATION: Extract all needed values once
        approved = signal.get('approved', True)
        rejection_reason = signal.get('rejection_reason', None)
        btc_filter_passed = signal.get('btc_filter_passed', True)
        
        # Build rejection reason
        rejection_reasons = []
        
        # Check BTC filter (can fail even if position_manager approves)
        if not btc_filter_passed:
            rejection_reasons.append("BTC filter failed")
        
        # Check if position_manager rejected it
        if not approved:
            if rejection_reason:
                rejection_reasons.append(rejection_reason)
            else:
                rejection_reasons.append("Validation failed")
        
        # Only include if we have a rejection reason
        if rejection_reasons:
            # Combine multiple reasons
            combined_reason = " | ".join(rejection_reasons)
            
            # OPTIMIZATION: Extract all signal data once
            rejected_signals.append({
                'timestamp': signal.get('timestamp', now),
                'symbol': signal.get('symbol', '?'),
                'side': signal.get('side', '?'),
                'final_score': signal.get('final_score', 0),
                'strength': signal.get('strength', 0),
                'type': signal.get('type', 'unknown'),
                'spread_bps': signal.get('spread_bps', 0),
                'volatility': signal.get('volatility', 0),
                'btc_filter_passed': btc_filter_passed,
                'rejection_reason': combined_reason
            })
    
    # OPTIMIZATION: Sort and slice in one operation, use direct key access
    rejected_signals.sort(key=lambda x: x['timestamp'], reverse=True)
    rejected_signals = rejected_signals[:20]
    
    # Calculate loop time
    avg_loop_time = sum(loop_times_list) / len(loop_times_list) if loop_times_list else 0.0
    
    # OPTIMIZATION: Calculate API call rate (filter once, reuse)
    api_rate_per_sec = 0.0
    api_rate_per_min = 0.0
    if api_call_times_list:
        # OPTIMIZATION: Use generator for filtering (reduces memory allocation)
        recent_calls = list(t for t in api_call_times_list if now - t < 60)
        if recent_calls:
            api_rate_per_min = len(recent_calls)
            if len(recent_calls) > 1:
                # OPTIMIZATION: Use min/max on filtered list (already filtered)
                time_span = max(recent_calls) - min(recent_calls)
                if time_span > 0:
                    api_rate_per_sec = len(recent_calls) / time_span
    
    # Get system resources
    sys_resources = get_system_resources()
    
    # Get BTC trend
    btc_trend_data = calculate_btc_trend(market, bot)
    
    # Get cooldown status
    cooldown_status = bot.position_manager.get_cooldown_status() if hasattr(bot, "position_manager") else {}
    
    # Calculate position health for each position
    for pos_detail in position_details:
        sym = pos_detail["symbol"]
        pos = positions.get(sym, {})
        current_price = pos_detail.get("mark") or pos_detail.get("entry") or 0.0
        health = calculate_position_health(pos, current_price)
        pos_detail["health"] = health
        pos_detail["entry_time"] = pos.get("entry_time", 0)
        pos_detail["stop_loss"] = pos.get("stop_loss", 0)
        pos_detail["take_profit"] = pos.get("take_profit", 0)
        pos_detail["signal_strength"] = pos.get("signal_strength", 0.0)
    
    # Get LLM advisor suggestions
    llm_suggestions = []
    if ctrl:
        recent_info = list(getattr(ctrl, "recent_info", []) or [])
        # Sort by priority (highest first) and timestamp (newest first)
        recent_info_sorted = sorted(
            recent_info,
            key=lambda x: (
                x.get('priority', 50) if isinstance(x, dict) else 50,
                x.get('timestamp', 0) if isinstance(x, dict) else 0
            ),
            reverse=True
        )
        # Take top 3 suggestions
        for info in recent_info_sorted[:3]:
            if isinstance(info, dict):
                llm_suggestions.append({
                    "timestamp": info.get('timestamp', now),
                    "message": info.get('message', str(info)),
                    "confidence": min(info.get('priority', 75) / 100.0, 1.0)  # Convert priority to confidence
                })
            else:
                # Old format support
                llm_suggestions.append({
                    "timestamp": now,
                    "message": str(info),
                    "confidence": 0.75
                })
    
    # Get scan statistics - flexible to capture new scan data
    scan_history = list(getattr(bot, "scan_history", []) or [])
    scan_stats = getattr(bot, "scan_stats", {}) or {}
    last_scan_end = getattr(bot, "last_scan_end", 0.0)
    last_scan_start = getattr(bot, "last_scan_start", 0.0)
    next_scan_time = getattr(bot, "next_scan_time", 0.0)
    is_scanning = getattr(bot, "is_scanning", False)
    
    # Calculate scan statistics
    total_scans = scan_stats.get('total_scans', 0)
    total_symbols_processed = scan_stats.get('total_symbols_processed', 0)
    total_cache_hits = scan_stats.get('total_cache_hits', 0)
    total_cache_misses = scan_stats.get('total_cache_misses', 0)
    total_orderbook_success = scan_stats.get('total_orderbook_success', 0)
    total_orderbook_failures = scan_stats.get('total_orderbook_failures', 0)
    
    # Calculate averages
    # OPTIMIZATION: Use generators for sum operations (reduces memory allocation)
    avg_symbols_per_scan = safe_div(total_symbols_processed, total_scans, 0.0) if total_scans > 0 else 0.0
    avg_scan_duration = safe_div(sum(s.get('duration', 0) for s in scan_history), len(scan_history), 0.0) if scan_history else 0.0
    avg_cache_hit_rate = safe_div(total_cache_hits, (total_cache_hits + total_cache_misses), 0.0) * 100.0 if (total_cache_hits + total_cache_misses) > 0 else 0.0
    total_scan_duration = sum(s.get('duration', 0) for s in scan_history) if scan_history else 0.0
    scan_throughput = safe_div(total_symbols_processed, total_scan_duration, 0.0) if total_scan_duration > 0 else 0.0
    
    # Calculate uptime for scans_per_minute
    uptime_seconds = now - getattr(bot, "started_at", now)
    scans_per_minute = safe_div(total_scans * 60, uptime_seconds, 0.0) if uptime_seconds > 0 else 0.0
    
    # Get last scan details - safely extract all available fields
    last_scan = scan_history[-1] if scan_history else {}
    current_symbols_processed = last_scan.get('symbols_processed', 0)
    current_symbols_skipped = last_scan.get('symbols_skipped', {}) or {}
    current_cache_hits = last_scan.get('cache_hits', 0)
    current_cache_misses = last_scan.get('cache_misses', 0)
    current_cache_hit_rate = last_scan.get('cache_hit_rate', 0.0)
    current_orderbook_success = last_scan.get('orderbook_success', 0)
    current_orderbook_failures = last_scan.get('orderbook_failures', 0)
    current_orderbook_success_rate = last_scan.get('orderbook_success_rate', 0.0)
    current_scan_duration = last_scan.get('duration', 0.0)
    total_symbols = last_scan.get('total_symbols', 0)
    scan_active_symbols = last_scan.get('active_symbols', 0)  # Count from last scan
    discovery_symbols = last_scan.get('discovery_symbols', 0)
    signals_found = last_scan.get('signals_found', 0)
    entries_attempted = last_scan.get('entries_attempted', 0)
    
    # Check for new scan attributes that BossCoder might add
    scan_extra = {}
    for attr in ['scan_queue_size', 'scan_priority', 'scan_batch_size', 'scan_errors', 'scan_warnings', 'scan_mode']:
        if hasattr(bot, attr):
            scan_extra[attr] = getattr(bot, attr)
    
    # Calculate time until next scan
    time_until_next = max(0.0, next_scan_time - now) if next_scan_time > 0 else 0.0
    time_since_last_scan = max(0.0, now - last_scan_end) if last_scan_end > 0 else 0.0
    
    # Calculate scan frequency (scans per second) from actual scan intervals
    # Default to 1.0 scan/s if we have scans but can't calculate from history
    scan_frequency = 1.0  # Default: 1 scan per second (from bot.py: next_signal_scan = time.time() + 1)
    if scan_history and len(scan_history) > 1:
        # Calculate average time between scans
        intervals = []
        for i in range(1, len(scan_history)):
            interval = scan_history[i].get('timestamp', 0) - scan_history[i-1].get('timestamp', 0)
            if interval > 0:
                intervals.append(interval)
        if intervals:
            avg_interval = sum(intervals) / len(intervals)
            scan_frequency = 1.0 / avg_interval if avg_interval > 0 else 1.0
    
    # Categorize errors and warnings
    # OPTIMIZATION: Use generator in any() and tuple for faster membership test
    errors_list = []
    warnings_list = []
    error_keywords = ("error", "failed", "exception", "fatal")  # Tuple for faster membership test
    for err in err_tail:
        err_lower = err.lower()
        if any(keyword in err_lower for keyword in error_keywords):
            errors_list.append(err)
        else:
            warnings_list.append(err)

    snapshot = {
        "meta": {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "host": socket.gethostname(),
        },
        "performance": {
            "equity": float(eq),
            "start_equity": float(start),
            "equity_peak": float(peak),
            "drawdown_pct": float(dd_pct),
            "realized_total": float(realized_total),
            "net_realized_after_fees": float(net_realized_after_fees),
            "fees_total": float(realized_fees),
            "funding_total": float(realized_funding),
            "profit_factor": float(profit_factor),
            "win_rate": float(win_rate),
            "gross_win": float(gross_win),
            "gross_loss": float(gross_loss),
            "wins": wins,
            "losses": losses,
            "session_pnl_pct": safe_div((eq - start), start, 0.0) * 100.0,
            "equity_delta": eq - start,
        },
        "control": {
            "mode": getattr(bot, "current_mode", "Unknown"),
            "mode_duration_seconds": float(mode_duration),
            "mode_duration_human": _humanize_duration(mode_duration),
            "last_patch": last_patch_iso,
            "last_patch_human": _humanize_duration(now - last_patch_ts) + " ago" if last_patch_ts else "Never",
        },
        "system": {
            "uptime_seconds": float(uptime_sec),
            "uptime_human": _humanize_duration(uptime_sec),
            "started_at": datetime.utcfromtimestamp(started_at).isoformat() + "Z",
            "dry_run": bool(DRY_RUN),
            "loop_time": avg_loop_time,
            "cpu_percent": sys_resources.get("cpu_percent", 0.0),
            "ram_percent": sys_resources.get("ram_percent", 0.0),
        },
        "market": {
            "symbols_scanned_last": symbols_scanned,
            "active_count": len(active_symbols),
            "top_movers": top_movers,
            "btc_trend": btc_trend_data,
            "volatility_regime": getattr(bot, "volatility_regime", "Normal"),
            "spread_regime": getattr(bot, "spread_regime", "Normal"),
        },
        "regime": {
            "current": getattr(bot, "current_regime", "scalping"),
            "since": getattr(bot, "regime_since", time.time()),
            "duration_sec": time.time() - getattr(bot, "regime_since", time.time()),
            "config": {
                "name": getattr(bot.regime_config, "name", "Unknown") if getattr(bot, "regime_config", None) else "Unknown",
                "min_signal_strength": getattr(bot.regime_config, "min_signal_strength", 0.0) if getattr(bot, "regime_config", None) else 0.0,
                "max_spread_bps": getattr(bot.regime_config, "max_spread_bps", 0.0) if getattr(bot, "regime_config", None) else 0.0,
                "scan_interval_seconds": getattr(bot.regime_config, "scan_interval_seconds", 0.0) if getattr(bot, "regime_config", None) else 0.0,
            } if getattr(bot, "regime_config", None) else None,
        },
        "api_budget": {
            "calls": calls,
            "max_cpm": max_cpm,
            "remaining": remaining,
            "buckets": bucket_rows,
            "rate_per_sec": api_rate_per_sec,
            "rate_per_min": api_rate_per_min,
        },
        "activity": {
            "info": info_tail,
            "errors": errors_list,
            "warnings": warnings_list,
        },
        "positions": {
            "count": len(positions),
            "symbols": list(positions.keys())[:20],
            "details": position_details,
        },
        "signals": {
            "history": signal_history[-20:],  # Last 20 signals for stats
            "rejected": rejected_signals,  # Last 15 rejected signals
            "stats": getattr(bot, "signal_stats", {}),
        },
        "trades": {
            "recent": recent_trades_list,
        },
        "risk": {
            "cooldown_status": cooldown_status,
        },
        "llm": {
            "suggestions": llm_suggestions,
        },
        "scanning": {
            "last_scan_time": last_scan_end,
            "next_scan_time": next_scan_time,
            "time_until_next": time_until_next,
            "time_since_last_scan": time_since_last_scan,
            "is_scanning": is_scanning,
            "current_scan_start": last_scan_start,
            "current_scan_duration": current_scan_duration,
            "symbols_processed": current_symbols_processed,
            "symbols_skipped": current_symbols_skipped,
            "total_symbols": total_symbols,
            "active_symbols": scan_active_symbols,  # Count from last scan
            "discovery_symbols": discovery_symbols,
            "signals_found": signals_found,
            "entries_attempted": entries_attempted,
            "cache_hits": current_cache_hits,
            "cache_misses": current_cache_misses,
            "cache_hit_rate": current_cache_hit_rate,
            "orderbook_success": current_orderbook_success,
            "orderbook_failures": current_orderbook_failures,
            "orderbook_success_rate": current_orderbook_success_rate,
            "history": scan_history,
            "stats": {
                "total_scans": total_scans,
                "avg_symbols_per_scan": avg_symbols_per_scan,
                "avg_scan_duration": avg_scan_duration,
                "avg_cache_hit_rate": avg_cache_hit_rate,
                "scan_throughput": scan_throughput,
                "scans_per_minute": scans_per_minute,
                "scan_frequency": scan_frequency
            },
            "extra": scan_extra  # Any new attributes BossCoder might add
        },
    }
    return snapshot


def draw_panel(bot):
    """Display static terminal UI - category-based layout with flicker-free updates."""
    # Static display: move cursor to top and clear to end of screen
    # CRITICAL: This is the legacy plain UI, should NOT be used with UI v2
    # If UI v2 is active, these prints are suppressed by stdout redirection
    if not hasattr(draw_panel, '_initialized'):
        draw_panel._initialized = True
        print("\033[2J\033[H", end="", flush=True)  # Clear screen once on first call
    else:
        print("\033[H\033[J", end="", flush=True)  # Move to top + clear to end (fixes flicker)
    
    snapshot = collect_bot_snapshot(bot)
    perf = snapshot["performance"]
    control = snapshot["control"]
    system = snapshot["system"]
    market = snapshot["market"]
    budget = snapshot["api_budget"]
    activity = snapshot["activity"]
    positions = snapshot["positions"]
    trades = snapshot.get("trades", {})
    llm = snapshot.get("llm", {})
    scanning = snapshot.get("scanning", {})
    
    # Optimize for 1920x1080 full-screen display
    width = 200
    now = time.time()
    
    def print_line(line: str = "") -> None:
        """Print a line."""
        print(line)
    
    # Header
    mode_str = "[LIVE]" if not system['dry_run'] else "[DRY RUN]"
    if bot.exchange:
        conn_status = "[OK] Connected"
    else:
        # Check if we have cached data available
        has_cache = False
        if hasattr(bot, 'ticker_cache') and bot.ticker_cache:
            # Check if cache has any data
            try:
                test_data = bot.ticker_cache.get("BTC/USDT", max_age=60.0)
                has_cache = test_data is not None
            except Exception:
                pass
        if has_cache:
            conn_status = "[CACHE] Using cached data"
        else:
            conn_status = "[ERR] Disconnected - No data"
    header = f"BINANCE FUTURES SCALPER v5.4 [PURE SCALPER MODE] {mode_str} {conn_status}"
    print_line(f"{C.H}{header}{C.X}")
    print_line("")

    # CANONICAL ACCOUNTING: Use accounting helpers for consistency
    from .config import LEVERAGE_BASE
    from .accounting import calculate_balance, validate_accounting_consistency
    
    # Calculate balance (realized only) and equity (includes unrealized)
    start_balance = perf['start_equity']
    realized_pnl = perf['net_realized_after_fees']
    realized_funding = perf.get('funding_total', 0.0)
    
    balance = calculate_balance(start_balance, realized_pnl, realized_funding)
    
    # Sum unrealized PnL from positions (already calculated by canonical helper in collect_bot_snapshot)
    unrealized_pnl = sum([p.get('unrealized', 0) or 0 for p in positions['details']])
    
    # Total equity includes unrealized
    equity = perf['equity']  # This now comes from bot.equity_now() which includes unrealized
    equity_delta = equity - start_balance
    equity_delta_pct = safe_div(equity_delta, start_balance, 0.0) * 100.0
    
    # VALIDATION: Check that table PnL matches performance unrealized
    is_consistent, diff, message = validate_accounting_consistency(unrealized_pnl, unrealized_pnl, tolerance=0.01)
    # Note: This validation is redundant here since both use same source, but kept for future cross-checks
    
    btc_trend = market.get('btc_trend', {})
    btc_trend_pct = btc_trend.get('trend_pct', 0.0)
    btc_dir = btc_trend.get('direction', 'Neutral')
    vol_regime = market.get('volatility_regime', 'Normal')
    
    loop_time = system.get('loop_time', 0.0)
    uptime = system.get('uptime_human', '0s')
    api_rate_sec = budget.get('rate_per_sec', 0.0)
    api_rate_min = budget.get('rate_per_min', 0.0)
    
    scan_stats = scanning.get('stats', {})
    total_scans = scan_stats.get('total_scans', 0)
    current_symbols_processed = scanning.get('symbols_processed', 0)
    current_total_symbols = scanning.get('total_symbols', 0)
    active_symbols = scanning.get('active_symbols', 0)
    discovery_symbols = scanning.get('discovery_symbols', 0)
    signals_found = scanning.get('signals_found', 0)
    entries_attempted = scanning.get('entries_attempted', 0)
    current_symbols_skipped = scanning.get('symbols_skipped', {}) or {}
    current_cache_hits = scanning.get('cache_hits', 0)
    current_cache_misses = scanning.get('cache_misses', 0)
    current_cache_hit_rate = scanning.get('cache_hit_rate', 0.0)
    current_orderbook_success = scanning.get('orderbook_success', 0)
    current_orderbook_failures = scanning.get('orderbook_failures', 0)
    current_orderbook_success_rate = scanning.get('orderbook_success_rate', 0.0)
    avg_scan_duration = scan_stats.get('avg_scan_duration', 0.0)
    is_scanning = scanning.get('is_scanning', False)
    scan_extra = scanning.get('extra', {})
    
    # [1] SESSION INFO
    print_line(f"{C.H}SESSION INFO{C.X}")
    print_line(f"  Mode: PURE SCALPER     DRY_RUN: {system['dry_run']:<5} Leverage: {LEVERAGE_BASE}x  Balance: {format_currency(balance)}")
    print_line("")
    
    # [2] SCAN INFO (enhanced with more data)
    print_line(f"{C.H}SCAN INFO{C.X}")
    scan_status = "Scanning" if is_scanning else "Idle"
    status_color = C.Y if is_scanning else C.D
    print_line(f"  Scan#: {total_scans:<6} Status: {status_color}{scan_status:<10}{C.X} Symbols: {current_symbols_processed}/{current_total_symbols:<5} Active: {active_symbols:<5} Discovery: {discovery_symbols}")
    
    # Cache and orderbook stats
    cache_total = current_cache_hits + current_cache_misses
    orderbook_total = current_orderbook_success + current_orderbook_failures
    cache_rate_str = f"{current_cache_hit_rate:.1f}%" if cache_total > 0 else "N/A"
    orderbook_rate_str = f"{current_orderbook_success_rate:.1f}%" if orderbook_total > 0 else "N/A"
    print_line(f"  Cache: {current_cache_hits}/{current_cache_misses} ({cache_rate_str})  Orderbook: {current_orderbook_success}/{current_orderbook_failures} ({orderbook_rate_str})  Signals: {signals_found:<5} Entries: {entries_attempted}")
    
    # Skip breakdown and other metrics
    skip_in_pos = current_symbols_skipped.get('in_position', 0)
    skip_no_stats = current_symbols_skipped.get('no_stats', 0)
    skip_no_signal = current_symbols_skipped.get('no_signal', 0)
    skip_low_score = current_symbols_skipped.get('score_below_threshold', 0)
    skip_str = f"InPos:{skip_in_pos} NoStats:{skip_no_stats} NoSig:{skip_no_signal} LowScore:{skip_low_score}"
    print_line(f"  Skipped: {skip_str}  AvgDuration: {avg_scan_duration:.3f}s  Vol: {vol_regime:<8} BTC: {format_percentage(btc_trend_pct)} ({btc_dir})  API: {api_rate_sec:.1f}/s ({api_rate_min:.0f}/min)")
    
    # Display any extra scan attributes that BossCoder might add
    if scan_extra:
        extra_strs = []
        for key, value in scan_extra.items():
            if isinstance(value, (int, float)):
                extra_strs.append(f"{key}:{value}")
            else:
                extra_strs.append(f"{key}:{str(value)[:20]}")
        if extra_strs:
            print_line(f"  Extra: {' '.join(extra_strs[:5])}")  # Show max 5 extra attributes
    
    print_line("")
    
    # [3] PERFORMANCE
    print_line(f"{C.H}PERFORMANCE{C.X}")
    print_line(f"  PnL: Real:{format_currency(realized_pnl)} + Unreal:{format_currency(unrealized_pnl)} = Total:{format_currency(realized_pnl + unrealized_pnl)}  |  WR:{perf['win_rate']:.1f}%  PF:{perf['profit_factor']:.2f}  MaxDD:{format_percentage(perf['drawdown_pct'])}  EquityDelta:{format_currency(equity_delta)} ({format_percentage(equity_delta_pct)})")
    print_line("")
    
    # [4] POSITIONS
    print_line(f"{C.H}POSITIONS{C.X}")
    if positions['count'] > 0:
        print_line(f"  {'Symbol':<10} {'Side':<5} {'Entry':<9} {'Mark':<9} {'Size':<8} {'Lev':<3} {'Delta%':<8} {'PnL':<10} {'TP':<10} {'SL':<10}")
        
        for pos_detail in positions['details']:
            sym = pos_detail['symbol']
            side = pos_detail['side'] or '?'
            entry = pos_detail['entry'] or 0.0
            mark = pos_detail['mark'] or entry
            size = pos_detail.get('size', 0.0) or 0.0
            leverage = pos_detail.get('leverage', 1) or 1
            stop_loss = pos_detail.get('stop_loss', 0)
            take_profit = pos_detail.get('take_profit', 0)
            unrealized = pos_detail.get('unrealized', 0.0) or 0.0
            
            # Calculate Delta %
            delta_pct = 0.0
            if entry > 0:
                if side.lower() == 'long':
                    delta_pct = ((mark - entry) / entry) * 100.0
                elif side.lower() == 'short':
                    delta_pct = ((entry - mark) / entry) * 100.0
            
            side_color = C.G if side.lower() == 'long' else (C.R if side.lower() == 'short' else C.D)
            delta_color = C.G if delta_pct >= 0 else C.R
            pnl_color = C.G if unrealized >= 0 else C.R
            
            sym_display = truncate_string(sym.replace('/USDT', ''), 10)
            entry_str = f"${entry:.2f}" if entry else "-"
            mark_str = f"${mark:.2f}" if mark else "-"
            size_str = f"{size:.2f}" if size else "-"
            tp_str = f"${take_profit:.2f}" if take_profit > 0 else "-"
            sl_str = f"${stop_loss:.2f}" if stop_loss > 0 else "-"
            delta_str = f"{format_percentage(delta_pct)}" if entry > 0 else "-"
            pnl_str = f"${unrealized:+,.2f}" if unrealized is not None else "-"
            
            print_line(f"  {sym_display:<10} {side_color}{side:<5}{C.X} {entry_str:<9} {mark_str:<9} {size_str:<8} {leverage:<3}x {delta_color}{delta_str:<8}{C.X} {pnl_color}{pnl_str:<10}{C.X} {tp_str:<10} {sl_str:<10}")
    else:
        print_line(f"  {C.D}No open positions{C.X}")
    print_line("")
    
    # [5] EVENTS
    print_line(f"{C.H}EVENTS{C.X}")
    events_list = []
    # Add latest trades (entries and exits) - get more to have better selection
    recent_trades = trades.get('recent', [])[-5:]
    for trade in recent_trades:
        trade_type = trade.get('type', 'unknown')
        symbol = truncate_string(trade.get('symbol', '?').replace('/USDT', ''), 10)
        side = trade.get('side', '?')
        price = trade.get('price', 0)
        size = trade.get('size', 0)
        pnl = trade.get('pnl', 0)
        reason = trade.get('reason', '')
        timestamp = trade.get('timestamp', now)
        time_ago = format_duration_short(now - timestamp) if timestamp > 0 else "now"
        
        if trade_type == 'entry':
            score = int(trade.get('final_score', trade.get('score', 0) * 100)) if trade.get('final_score') or trade.get('score') else 0
            sl_pct = trade.get('stop_loss_pct', 0)
            tp_pct = trade.get('take_profit_pct', 0)
            leverage = trade.get('leverage', 1)
            score_color = C.G if score >= 80 else (C.Y if score >= 60 else C.R)
            events_list.append((timestamp, f"{C.Cc}[ENTRY]{C.X} {side.upper()} {symbol} @ {format_currency(price)} Size:{size:.2f} Lev:{leverage}x {score_color}Sc:{score}{C.X} SL:{sl_pct:.2f}% TP:{tp_pct:.2f}% {C.D}{time_ago} ago{C.X}"))
        elif trade_type == 'exit':
            pnl_color = C.G if pnl >= 0 else C.R
            pnl_pct = trade.get('pnl_pct', 0)
            exit_size = trade.get('size', size)
            if pnl_pct:
                pnl_str = f"{pnl_color}{format_currency(pnl)} ({format_percentage(pnl_pct)}){C.X}"
            else:
                pnl_str = f"{pnl_color}{format_currency(pnl)}{C.X}"
            reason_short = truncate_string(reason, 40) if reason else "Manual"
            events_list.append((timestamp, f"{C.Y}[EXIT]{C.X} {symbol} @ {format_currency(price)} Size:{exit_size:.2f} {pnl_str} Reason:{reason_short} {C.D}{time_ago} ago{C.X}"))
    
    # Add latest errors (use slightly older timestamp so trades take priority)
    errors_list = activity.get('errors', [])[-3:]
    for err in errors_list:
        events_list.append((now - 0.01, f"{C.R}[ERROR]{C.X} {truncate_string(err, 160)}"))
    
    # Sort by timestamp (newest first) and take max 3
    events_list.sort(key=lambda x: x[0], reverse=True)
    if events_list:
        for _, event_str in events_list[:3]:
            print_line(f"  {event_str}")
    else:
        print_line(f"  {C.D}No events{C.X}")
    print_line("")
    
    # [6] HEARTBEAT
    print_line(f"{C.H}HEARTBEAT{C.X}")
    print_line(f"  Connection: {conn_status:<18} Cycle: {loop_time:.2f}s  Uptime: {uptime}")
    print_line("")
    
    # [7] LLM ADVISOR
    print_line(f"{C.H}LLM ADVISOR{C.X}")
    llm_suggestions_list = llm.get('suggestions', [])
    if llm_suggestions_list:
        # Show up to 2 most relevant suggestions (rotating based on priority/time)
        for idx, suggestion in enumerate(llm_suggestions_list[:2]):
            msg = truncate_string(suggestion.get('message', ''), 160)
            conf = suggestion.get('confidence', 0) * 100
            if idx == 0:
                # Primary suggestion (highest priority)
                print_line(f"  {msg} (Conf:{conf:.0f}%)")
            else:
                # Secondary suggestion (indented, lighter)
                print_line(f"    {C.D}{msg} (Conf:{conf:.0f}%){C.X}")
    else:
        print_line(f"  {C.D}No suggestions yet{C.X}")
    print_line("")
    
    # Footer
    print_line(f"{C.D}Press Ctrl+C to stop | Auto-refresh: 5s{C.X}")
    # Clear any remaining lines to prevent flicker
    print("\033[J", end="", flush=True)
