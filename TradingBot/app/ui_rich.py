"""
Rich-based terminal UI for Binance Futures Scalper Bot.
Provides structured dashboard with panels for all bot state.
Falls back to plain-text UI if Rich is not available.
"""

from __future__ import annotations
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
import math

try:
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.live import Live
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    # Fallback will be handled in main UI

from .config import DRY_RUN, LEVERAGE_BASE
from .engine_snapshot import collect_engine_snapshot, validate_snapshot_consistency


# ────────────────────────────────────────────────────────────
# Data models
# ────────────────────────────────────────────────────────────

@dataclass
class PositionView:
    symbol: str
    side: str               # "LONG" / "SHORT"
    qty: float
    entry_price: float
    last_price: float
    pnl_abs: float          # in quote currency (e.g. USDT)
    pnl_pct: float          # %
    leverage: float
    age_s: int              # seconds open
    score: Optional[float] = None  # final signal score at entry
    exit_strategy: Optional[str] = None  # "Trailing", "ATR", "Fixed", etc.
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    size_pct: Optional[float] = None  # Position size as % of account
    rr: Optional[float] = None  # Risk-reward ratio
    max_pct: Optional[float] = None  # Maximum profit % reached
    sl_pct: Optional[float] = None  # Stop loss % from entry
    corr: Optional[float] = None  # BTC correlation
    dd_time: Optional[int] = None  # Drawdown time in seconds (time since peak)
    is_unicorn: Optional[bool] = False  # Whether this is a unicorn signal (score ≥ threshold)
    recovery_score: Optional[float] = None  # Position Recovery Score (0-100)
    current_r: Optional[float] = None  # Current R multiple


@dataclass
class EventView:
    timestamp: datetime
    type: str               # "ENTRY", "EXIT", "REJECT", "INFO", "WARN", ...
    message: str
    is_unicorn: Optional[bool] = False  # Whether this is a unicorn signal (for ENTRY events)


@dataclass
class SignalQueueItem:
    symbol: str
    score: float
    side: str
    status: str  # "Pending", "Watching", "Rejected", etc.
    reason: str  # Why pending/rejected
    price: Optional[float] = None

@dataclass
class LLMMessage:
    message: str
    action: Optional[str] = None  # Execution details
    confidence: Optional[float] = None

@dataclass
class BotState:
    # meta
    now: datetime
    runtime_s: int
    mode: str               # "DRY_RUN", "LIVE", "TESTNET"
    exchange: str
    version: str

    # loop & infra
    scan_number: int
    universe_size: int
    loop_latency_ms: float
    api_calls_last_min: int
    api_limit_per_min: int

    # account / performance
    balance: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    win_rate: float         # 0–1
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: float     # Sharpe ratio
    entry_fees_total: float
    exit_fees_total: float
    slippage_total: float
    funding_total: float
    total_costs: float
    avg_win: float          # Average win amount
    avg_loss: float         # Average loss amount
    total_trades: int       # Total completed trades
    avg_hold_time: float    # Average hold time in seconds

    # signal health
    signal_flow: float      # Signals per minute
    pass_rate: float        # Pass rate as percentage
    avg_signal_score: float # Average signal score (0-100)
    pnl_momentum: float     # PnL momentum (+/-)
    stop_dist: float        # Stop distance as percentage

    # risk / regime
    btc_trend: str          # e.g. "UP", "DOWN", "FLAT"
    volatility_regime: str  # "LOW", "NORMAL", "HIGH"
    loss_streak: int
    max_positions: int
    current_regime: str     # "scalping", "day", "swing"
    exit_strategy: str      # "Trailing", "ATR", "Fixed"

    # positions & activity
    open_positions: List[PositionView] = field(default_factory=list)
    recent_events: List[EventView] = field(default_factory=list)
    signal_queue: List[SignalQueueItem] = field(default_factory=list)

    # LLM advisory
    llm_messages: List[LLMMessage] = field(default_factory=list)
    llm_last_messages: List[str] = field(default_factory=list)  # Keep for compatibility

    # warnings / errors
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# ────────────────────────────────────────────────────────────
# Panel builders
# ────────────────────────────────────────────────────────────

def _panel_header(title: str) -> Text:
    return Text(title, style="bold white")


def _build_global_panel(state: BotState) -> Panel:
    mode_color = {
        "LIVE": "bold red",
        "DRY_RUN": "bold cyan",
        "TESTNET": "bold yellow",
    }.get(state.mode.upper(), "bold white")

    t = Table.grid(expand=True)
    t.add_column(justify="left")
    t.add_column(justify="right")

    t.add_row(
        Text(f"{state.exchange} | v{state.version}", style="bold"),
        Text(state.now.strftime("%Y-%m-%d %H:%M:%S"), style="dim"),
    )
    t.add_row(
        Text("Mode: ", style="dim") + Text(state.mode, style=mode_color),
        Text(
            f"Runtime: {state.runtime_s//3600:02d}:{(state.runtime_s%3600)//60:02d}:{state.runtime_s%60:02d}",
            style="dim",
        ),
    )
    t.add_row(
        f"Universe: {state.universe_size} symbols | Scan #{state.scan_number}",
        f"Loop: {state.loop_latency_ms:.1f} ms",
    )
    t.add_row(
        f"API: {state.api_calls_last_min}/{state.api_limit_per_min} calls/min",
        ""
    )

    return Panel(
        t,
        title=_panel_header("GLOBAL STATUS"),
        border_style="cyan",
        box=box.ROUNDED,
    )


def _build_account_panel(state: BotState) -> Panel:
    t = Table.grid(expand=True)
    t.add_column(justify="left")
    t.add_column(justify="right")

    # Top-line summary (explicit net PnL + total fees)
    pnl_summary = Text("PnL (net): ", style="bold")
    pnl_summary.append(f"{state.realized_pnl:+.2f}", style="green" if state.realized_pnl >= 0 else "red")
    pnl_summary.append("   |   Fees: ", style="dim")
    pnl_summary.append(f"${state.total_costs:.2f}", style="yellow")
    t.add_row(pnl_summary, "")

    eq_delta = state.equity - state.balance
    eq_delta_pct = (eq_delta / state.balance * 100) if state.balance else 0.0

    pnl_color = "green" if state.realized_pnl >= 0 else "red"
    upnl_color = "green" if state.unrealized_pnl >= 0 else "red"
    eq_color = "green" if eq_delta >= 0 else "red"

    t.add_row("Balance", f"{state.balance:,.2f}")
    t.add_row("Equity", Text(f"{state.equity:,.2f} (+{eq_delta:+.2f})", style=eq_color))
    t.add_row("Realized PnL (net)", Text(f"{state.realized_pnl:+.2f}", style=pnl_color))
    t.add_row("Unrealized", Text(f"{state.unrealized_pnl:+.2f}", style=upnl_color))
    t.add_row("Profit Factor", f"{state.profit_factor:.2f}")
    t.add_row("Total Fees / Costs", Text(f"${state.total_costs:.2f}", style="yellow"))
    t.add_row("Entry Fees", f"${state.entry_fees_total:.2f}")
    t.add_row("Exit Fees", f"${state.exit_fees_total:.2f}")
    t.add_row("Funding", f"${state.funding_total:.2f}")
    t.add_row("Slippage", f"${state.slippage_total:.2f}")
    
    # Format avg hold time
    avg_hold_m = int(state.avg_hold_time // 60)
    avg_hold_s = int(state.avg_hold_time % 60)
    avg_hold_str = f"{avg_hold_m}m{avg_hold_s}s" if avg_hold_m > 0 else f"{avg_hold_s}s"
    t.add_row("Avg Hold", avg_hold_str)

    return Panel(
        t,
        title=_panel_header("PERFORMANCE"),
        border_style="magenta",
        box=box.ROUNDED,
    )


def _build_signal_health_panel(state: BotState) -> Panel:
    t = Table.grid(expand=True)
    t.add_column(justify="left")
    t.add_column(justify="right")

    t.add_row("Signal Flow", f"{state.signal_flow:.1f}/min")
    t.add_row("Pass Rate", f"{state.pass_rate:.1f}%")
    t.add_row("Avg Score", f"{state.avg_signal_score:.1f}")
    t.add_row("Latency", f"{state.loop_latency_ms:.0f}ms")

    return Panel(
        t,
        title=_panel_header("REAL-TIME SIGNAL HEALTH"),
        border_style="green",
        box=box.ROUNDED,
    )


def _build_market_panel(state: BotState) -> Panel:
    t = Table.grid(expand=True)
    t.add_column(justify="left")
    t.add_column(justify="right")

    # One-line regime indicator (Scalper/Daytrade/Swing) with exit strategy
    try:
        regime_label = (state.current_regime or "scalping").replace("_", " ").title()
    except Exception:
        regime_label = "Scalping"
    try:
        exit_label = (state.exit_strategy or "Trailing").title()
    except Exception:
        exit_label = "Trailing"
    t.add_row("Regime", Text(f"{regime_label}  ·  Exit: {exit_label}", style="bold"))

    # Format BTC trend with arrow
    btc_display = f"⬆ {state.btc_trend}" if state.btc_trend == "UP" else (f"⬇ {state.btc_trend}" if state.btc_trend == "DOWN" else f"➡ {state.btc_trend}")
    t.add_row("BTC Trend", btc_display)
    t.add_row("Volatility", f"{state.volatility_regime}")
    t.add_row("PnL Momentum", Text(f"{state.pnl_momentum:+.2f}", style="green" if state.pnl_momentum >= 0 else "red"))
    t.add_row("Stop Dist", f"{state.stop_dist:.1f}%")

    return Panel(
        t,
        title=_panel_header("MARKET SNAPSHOT"),
        border_style="yellow",
        box=box.ROUNDED,
    )


def _build_positions_panel(state: BotState, max_rows: int = 25) -> Panel:
    table = Table(
        expand=True,
        box=box.SIMPLE_HEAVY,
        show_lines=False,
    )
    # 14 columns: Symbol, Side, Entry, PnL, PnL%, Max%, Score, PRS, Age, SL%, Lev, Size%, Corr, DD Time
    table.add_column("Symbol", justify="left", style="bold", width=8)
    table.add_column("Side", justify="center", width=5)
    table.add_column("Entry", justify="right", width=8)
    table.add_column("PnL", justify="right", width=7)
    table.add_column("PnL%", justify="right", width=6)
    table.add_column("R", justify="right", width=5)
    table.add_column("Max%", justify="right", width=6)
    table.add_column("Score", justify="right", width=5)
    table.add_column("PRS", justify="right", width=4)  # Position Recovery Score
    table.add_column("Age", justify="right", width=6)
    table.add_column("SL%", justify="right", width=5)
    table.add_column("Lev", justify="right", width=4)
    table.add_column("Size%", justify="right", width=6)
    table.add_column("Corr", justify="right", width=6)
    table.add_column("DD Time", justify="right", width=7)

    if not state.open_positions:
        table.add_row("–", "", "", "", "", "", "", "", "", "", "", "", "", "")
    else:
        for pos in state.open_positions[:max_rows]:
            side_style = "green" if pos.side.upper() == "LONG" else "red"
            pnl_style = "green" if pos.pnl_abs >= 0 else "red"

            # Format duration more compactly
            age_m = pos.age_s // 60
            age_s_remainder = pos.age_s % 60
            if age_m > 0:
                age_str = f"{age_m}m{age_s_remainder}s"
            else:
                age_str = f"{age_s_remainder}s"

            # Format score properly (0-1 to 0-100, or use as-is if already 0-100)
            if pos.score is not None:
                score_display = f"{pos.score * 100:.0f}" if pos.score <= 1.0 else f"{pos.score:.0f}"
            else:
                score_display = "–"

            # Format size %
            size_pct_display = f"{pos.size_pct:.1f}%" if pos.size_pct is not None else "–"
            
            # Format leverage
            lev_display = f"{pos.leverage:.0f}x" if pos.leverage else "–"
            
            # Format Max%
            max_pct_display = f"{pos.max_pct:+.1f}%" if pos.max_pct is not None else "–"
            
            # Format SL%
            sl_pct_display = f"{pos.sl_pct:.1f}%" if pos.sl_pct is not None else "–"
            
            # Format Corr (correlation)
            if pos.corr is not None:
                corr_display = f"{pos.corr:+.2f}"
            else:
                corr_display = "–"
            
            # Format DD Time (drawdown time)
            dd_time_display = "–"
            if pos.dd_time is not None:
                dd_m = pos.dd_time // 60
                dd_s = pos.dd_time % 60
                if dd_m > 0:
                    dd_time_display = f"{dd_m}m{dd_s}s"
                else:
                    dd_time_display = f"{dd_s}s"
            
            # Format PRS (Position Recovery Score) with color coding
            if pos.recovery_score is not None:
                prs_score = pos.recovery_score
                if prs_score >= 70:
                    prs_style = "green"
                elif prs_score >= 50:
                    prs_style = "yellow"
                else:
                    prs_style = "red"
                prs_display = Text(f"{prs_score:.0f}", style=prs_style)
            else:
                prs_display = "–"

            # Add unicorn emoji prefix to symbol if this is a unicorn signal
            symbol_base = pos.symbol.replace('/USDT', '')[:8]
            if pos.is_unicorn:
                # Make unicorn signals stand out with emoji and bold styling
                symbol_display = Text("🦄 ", style="bold bright_magenta") + Text(symbol_base, style="bold bright_magenta")
            else:
                symbol_display = symbol_base

            r_display = f"{pos.current_r:.1f}" if pos.current_r is not None else "–"
            
            table.add_row(
                symbol_display,
                Text(pos.side[:4], style=side_style),
                f"{pos.entry_price:.3f}",
                Text(f"{pos.pnl_abs:+.2f}", style=pnl_style),
                Text(f"{pos.pnl_pct:+.1f}%", style=pnl_style),
                r_display,
                max_pct_display,
                score_display,
                prs_display,
                age_str,
                sl_pct_display,
                lev_display,
                size_pct_display,
                corr_display,
                dd_time_display,
            )

    # Show "displayed / total" if we hit the display limit
    total_positions = len(state.open_positions)
    displayed_positions = min(total_positions, max_rows)
    
    if total_positions > max_rows:
        title_text = f"OPEN POSITIONS ({displayed_positions}/{total_positions} shown)"
    else:
        title_text = f"OPEN POSITIONS ({total_positions})"
    
    return Panel(
        table,
        title=_panel_header(title_text),
        border_style="green",
        box=box.ROUNDED,
    )


def _build_events_panel(state: BotState, max_rows: int = 9) -> Panel:
    # Compact format matching web UI - just text lines
    # Show up to 9 entries (user requested 4-5 more than previous 5)
    lines = []
    events = state.recent_events[:max_rows]  # Already sorted newest first
    
    if not events:
        lines.append(Text("No recent activity.", style="dim"))
    else:
        for ev in events:
            ts = ev.timestamp.strftime("%H:%M:%S")
            type_style = {
                "ENTRY": "green",
                "EXIT": "cyan",
                "REJECT": "yellow",
                "ERROR": "red",
                "WARN": "yellow",
            }.get(ev.type.upper(), "white")
            
            event_line = Text(f"{ts} — ", style="dim")
            event_line.append(ev.type.upper(), style=type_style)
            
            # Make unicorn entries stand out visually with emoji and special styling
            if ev.is_unicorn and ev.type.upper() == "ENTRY":
                event_line.append(" 🦄 ", style="bold bright_magenta")
                event_line.append(f" — {ev.message}", style="bold")
            else:
                event_line.append(f" — {ev.message}", style="")
            
            lines.append(event_line)

    body = Group(*lines) if lines else Text("No recent activity.", style="dim")

    return Panel(
        body,
        title=_panel_header("RECENT ACTIVITY"),
        border_style="blue",
        box=box.ROUNDED,
    )


def _build_llm_panel(state: BotState) -> Panel:
    if not state.llm_messages and not state.llm_last_messages:
        body = Text("No recent LLM guidance.", style="dim")
    else:
        lines = []
        # Use enhanced LLM messages if available, otherwise fall back to simple messages
        if state.llm_messages:
            for msg in state.llm_messages[-2:]:
                advisor_text = Text("🧠 Advisor: ", style="bold magenta")
                advisor_text.append(msg.message, style="")
                lines.append(advisor_text)
        else:
            # Fallback to simple messages
            for msg in state.llm_last_messages[-2:]:
                advisor_text = Text("🧠 Advisor: ", style="bold magenta")
                advisor_text.append(msg, style="")
                lines.append(advisor_text)
        
        body = Group(*lines) if lines else Text("No recent LLM guidance.", style="dim")

    return Panel(
        body,
        title=_panel_header("LLM COMMAND LOG"),
        border_style="bright_magenta",
        box=box.ROUNDED,
    )


def _build_signal_queue_panel(state: BotState, max_rows: int = 5) -> Panel:
    table = Table(
        expand=True,
        box=box.MINIMAL,
        show_header=True,
    )
    # Matching web UI: Symbol, Score, Status, Reason (no Side column)
    table.add_column("Symbol", justify="left", style="bold", width=8)
    table.add_column("Score", justify="right", width=5)
    table.add_column("Status", justify="left", width=6)
    table.add_column("Reason", justify="left")

    if not state.signal_queue:
        table.add_row("–", "", "", "No signals in queue.")
    else:
        for sig in state.signal_queue[:max_rows]:
            # Map status to compact display
            status_upper = sig.status.upper()
            if "REJECTED" in status_upper:
                status_display = Text("RJ", style="red")
            elif "PENDING" in status_upper:
                status_display = Text("PD", style="yellow")
            elif "WATCHING" in status_upper:
                status_display = Text("WT", style="cyan")
            else:
                status_display = Text(sig.status[:2], style="white")
            
            # Format score properly
            score_display = f"{sig.score:.0f}" if sig.score >= 1.0 else f"{sig.score * 100:.0f}"
            
            # Format reason (compact)
            reason_display = sig.reason[:20] if sig.reason else "–"
            
            table.add_row(
                sig.symbol.replace('/USDT', '')[:8],
                score_display,
                status_display,
                reason_display,
            )

    return Panel(
        table,
        title=_panel_header("SIGNAL QUEUE"),
        border_style="blue",
        box=box.ROUNDED,
    )


def _build_alerts_panel(state: BotState) -> Panel:
    items = []

    for w in state.warnings[-3:]:
        items.append(Text(f"[WARN] {w}", style="yellow"))

    for e in state.errors[-3:]:
        items.append(Text(f"[ERROR] {e}", style="bold red"))

    if not items:
        items.append(Text("System healthy. No alerts.", style="green"))

    body = Group(*items)
    return Panel(
        body,
        title=_panel_header("ALERTS"),
        border_style="red",
        box=box.ROUNDED,
    )


# ────────────────────────────────────────────────────────────
# Layout & render
# ────────────────────────────────────────────────────────────

def build_layout(state: BotState) -> Layout:
    """Construct the Rich layout for the current bot state."""
    from .logger import get_logger
    log = get_logger("UI")
    
    try:
        log.debug(f"[UI] build_layout() called | positions={len(state.open_positions)} | events={len(state.recent_events)}")
        layout = Layout()
    except Exception as e:
        log.error(f"[UI] build_layout() failed: {type(e).__name__}: {e}", exc_info=True)
        raise

    layout.split(
        Layout(name="top", size=7),
        Layout(name="middle", ratio=2),
        Layout(name="bottom", size=12),
    )

    # Top: global + account + signal health + market in 4 columns
    top = Layout()
    top.split_row(
        Layout(_build_global_panel(state), name="global"),
        Layout(_build_account_panel(state), name="account"),
        Layout(_build_signal_health_panel(state), name="signal_health"),
        Layout(_build_market_panel(state), name="market"),
    )
    layout["top"].update(top)

    # Middle: positions full width
    layout["middle"].update(_build_positions_panel(state))

    # Bottom: three columns: events | signal queue | LLM
    bottom = Layout()
    bottom.split_row(
        Layout(_build_events_panel(state), name="events"),
        Layout(_build_signal_queue_panel(state), name="signals"),
        Layout(_build_llm_panel(state), name="llm"),
    )
    layout["bottom"].update(bottom)

    return layout


# ────────────────────────────────────────────────────────────
# Live dashboard helper
# ────────────────────────────────────────────────────────────

class _FilteredWriter:
    """A writer that discards all output except Rich console output."""
    def __init__(self, original_stream, rich_console):
        self._original = original_stream
        self._rich_console = rich_console
        self._buffer = []
        # Track if we're in a Rich rendering context
        self._in_rich_context = False
    
    def write(self, s):
        # Discard all writes - Rich Console writes directly to original stream
        # This prevents print statements and logging from appearing below UI
        pass
    
    def flush(self):
        pass
    
    def isatty(self):
        return getattr(self._original, 'isatty', lambda: False)()


class Dashboard:
    """
    Simple wrapper around Rich Live.
    You call dashboard.update(state) each loop with a fresh BotState snapshot.
    Prevents any output from appearing below the UI frame by redirecting stdout/stderr.
    """

    def __init__(self, console: Console | None = None, refresh_per_second: float = 4.0):
        if not RICH_AVAILABLE:
            raise ImportError("Rich library is not installed. Install with: pip install rich")
        # Store original stdout before any redirects
        self._original_stdout = sys.__stdout__ if hasattr(sys, '__stdout__') else sys.stdout
        self._original_stderr = sys.__stderr__ if hasattr(sys, '__stderr__') else sys.stderr
        # Use a Console that writes to original stdout (bypasses any redirects)
        # CRITICAL: Check if we're in a real terminal (not redirected)
        is_terminal = hasattr(self._original_stdout, 'isatty') and self._original_stdout.isatty()
        # Even if isatty() returns False, try to use terminal features (Windows terminals sometimes report False incorrectly)
        self.console = console or Console(
            file=self._original_stdout,
            force_terminal=True,  # Force terminal mode to enable Rich features
            width=None,
            height=None,
            legacy_windows=False  # Use modern Windows terminal features if available
        )
        self.refresh_per_second = refresh_per_second
        self._live: Optional[Live] = None
        self._console_handlers = []  # Track logging handlers to restore on exit
        self._filtered_stdout = None
        self._filtered_stderr = None

    def __enter__(self):
        # IMPORTANT: Capture current stdout/stderr BEFORE redirecting
        # These are the wrapped versions (like TextIOWrapper from run.py)
        current_stdout = sys.stdout
        current_stderr = sys.stderr
        
        # Suppress console logging output FIRST (before redirecting)
        # Logs will still go to file, but won't pollute the UI frame
        import logging
        self._console_handlers = []
        
        # Helper to check if a stream writes to stdout/stderr
        def is_stdout_stderr_stream(stream):
            """Check if stream writes to stdout/stderr, including wrapped versions."""
            # Direct comparison with current stdout/stderr (wrapped versions)
            if stream == current_stdout or stream == current_stderr:
                return True
            # Direct comparison with original stdout/stderr
            if stream == self._original_stdout or stream == self._original_stderr:
                return True
            # Check if it's a wrapped stdout/stderr (like TextIOWrapper in run.py)
            # Compare underlying buffers
            if hasattr(stream, 'buffer'):
                try:
                    stream_buffer = stream.buffer
                    if hasattr(current_stdout, 'buffer') and stream_buffer == current_stdout.buffer:
                        return True
                    if hasattr(current_stderr, 'buffer') and stream_buffer == current_stderr.buffer:
                        return True
                    if hasattr(self._original_stdout, 'buffer') and stream_buffer == getattr(self._original_stdout, 'buffer', None):
                        return True
                    if hasattr(self._original_stderr, 'buffer') and stream_buffer == getattr(self._original_stderr, 'buffer', None):
                        return True
                except (AttributeError, OSError):
                    pass
            # Check if it has the same underlying file descriptor
            try:
                if hasattr(stream, 'fileno'):
                    stream_fd = stream.fileno()
                    if hasattr(current_stdout, 'fileno'):
                        try:
                            if stream_fd == current_stdout.fileno():
                                return True
                        except (OSError, AttributeError):
                            pass
                    if hasattr(current_stderr, 'fileno'):
                        try:
                            if stream_fd == current_stderr.fileno():
                                return True
                        except (OSError, AttributeError):
                            pass
                    if hasattr(self._original_stdout, 'fileno'):
                        try:
                            if stream_fd == self._original_stdout.fileno():
                                return True
                        except (OSError, AttributeError):
                            pass
                    if hasattr(self._original_stderr, 'fileno'):
                        try:
                            if stream_fd == self._original_stderr.fileno():
                                return True
                        except (OSError, AttributeError):
                            pass
            except (OSError, AttributeError):
                pass
            return False
        
        # Find all console handlers across all loggers
        root_logger = logging.getLogger()
        all_loggers = [root_logger] + [logging.getLogger(name) for name in logging.Logger.manager.loggerDict]
        
        for logger in all_loggers:
            for handler in logger.handlers[:]:
                if isinstance(handler, logging.StreamHandler):
                    if is_stdout_stderr_stream(handler.stream):
                        # Avoid duplicates
                        is_duplicate = any(h == handler for h, _ in self._console_handlers)
                        if not is_duplicate:
                            original_level = handler.level
                            self._console_handlers.append((handler, original_level))
                            # Suppress ALL levels (set to highest possible)
                            handler.setLevel(logging.CRITICAL + 1)
        
        # NOW enable alternate screen buffer and redirect stdout/stderr
        # CRITICAL: Check if terminal supports alternate screen buffer
        # On Windows, some terminals don't support it, so we'll try with screen=True first
        try:
            self._live = Live(
                auto_refresh=False,
                console=self.console,
                screen=True,  # This enables alternate screen buffer
                refresh_per_second=self.refresh_per_second,
                redirect_stderr=True,  # Redirect stderr to prevent output
            )
            self._live.__enter__()
        except Exception as e:
            # Fallback: Try without alternate screen buffer if it fails
            from .logger import get_logger
            log = get_logger("UI")
            log.warning(f"[UI] Alternate screen buffer failed, trying without: {e}")
            try:
                self._live = Live(
                    auto_refresh=False,
                    console=self.console,
                    screen=False,  # Disable alternate screen buffer
                    refresh_per_second=self.refresh_per_second,
                    redirect_stderr=True,
                )
                self._live.__enter__()
            except Exception as e2:
                log.error(f"[UI] Live context creation failed: {e2}")
                raise
        
        # Redirect stdout/stderr to filtered writers to block ALL output (print statements, etc.)
        # Rich Console uses original stdout directly, so it bypasses this redirect
        self._filtered_stdout = _FilteredWriter(current_stdout, self.console)
        self._filtered_stderr = _FilteredWriter(current_stderr, self.console)
        sys.stdout = self._filtered_stdout
        sys.stderr = self._filtered_stderr
        
        return self

    def __exit__(self, exc_type, exc, tb):
        # Restore stdout/stderr FIRST before restoring logging (logging might write to them)
        # Check if they were redirected (they should be)
        if self._filtered_stdout is not None and sys.stdout is self._filtered_stdout:
            sys.stdout = self._original_stdout
        if self._filtered_stderr is not None and sys.stderr is self._filtered_stderr:
            sys.stderr = self._original_stderr
        
        # Restore console logging when Rich UI exits
        if hasattr(self, '_console_handlers'):
            for handler, original_level in self._console_handlers:
                handler.setLevel(original_level)
            self._console_handlers = []
        
        if self._live:
            self._live.__exit__(exc_type, exc, tb)
            self._live = None

    def update(self, state: BotState):
        """Update the dashboard with new state."""
        if not self._live:
            return
        
        try:
            layout = build_layout(state)
            # CRITICAL: Single update call per render to prevent flicker
            # Ensure we're updating the Live context
            if self._live:
                self._live.update(layout, refresh=True)
        except Exception as e:
            # Log UI update errors so we can diagnose issues (but only errors, not debug)
            from .logger import get_logger
            log = get_logger("UI")
            log.error(f"[UI] update() failed: {type(e).__name__}: {e}", exc_info=True)
            # Don't re-raise to avoid breaking the UI loop, but log it


# ────────────────────────────────────────────────────────────
# Data mapping from bot to BotState
# ────────────────────────────────────────────────────────────

def map_bot_to_state(bot) -> BotState:
    """
    Map bot's internal state to BotState dataclass for Rich UI.
    
    Uses canonical EngineSnapshot as single source of truth.
    """
    from .logger import get_logger
    log = get_logger("UI")
    
    try:
        log.debug("[UI] map_bot_to_state() called - using canonical EngineSnapshot")
        
        # CANONICAL SNAPSHOT: Single source of truth
        engine_snapshot = collect_engine_snapshot(bot)
        
        # Validate snapshot consistency
        warnings = validate_snapshot_consistency(engine_snapshot, logger=log)
        if warnings:
            log.warning(f"[UI] Snapshot validation found {len(warnings)} issues")
        
        now = engine_snapshot.timestamp
        log.debug(f"[UI] EngineSnapshot collected | positions={len(engine_snapshot.open_positions)} | signals={len(engine_snapshot.signal_queue)}")
    except Exception as e:
        log.error(f"[UI] map_bot_to_state() failed in snapshot collection: {type(e).__name__}: {e}", exc_info=True)
        raise
    
    # ═══════════════════════════════════════════════════════════
    # MAP EngineSnapshot → BotState
    # All values come from canonical snapshot, no recomputation
    # ═══════════════════════════════════════════════════════════
    
    gs = engine_snapshot.global_status
    perf = engine_snapshot.performance
    sig_health = engine_snapshot.signal_health
    market = engine_snapshot.market
    
    # Calculate Sharpe ratio (approximation)
    sharpe_ratio = 0.0
    if perf.win_count + perf.loss_count > 0 and perf.balance > 0:
        runtime_days = gs.runtime_seconds / 86400.0
        if runtime_days > 0:
            avg_return = (perf.realized_pnl / perf.balance) / runtime_days
            if perf.win_count > 0 and perf.loss_count > 0:
                avg_win_pct = (perf.gross_win / perf.win_count * 100.0 / perf.balance) if perf.win_count > 0 else 0.0
                avg_loss_pct = abs(perf.gross_loss / perf.loss_count * 100.0 / perf.balance) if perf.loss_count > 0 else 0.0
                return_std = ((avg_win_pct + avg_loss_pct) / 2.0) * 0.5
                sharpe_ratio = (avg_return * 252) / (return_std * (252 ** 0.5)) if return_std > 0 else 0.0
    
    # Average win/loss amounts
    avg_win = (perf.gross_win / perf.win_count) if perf.win_count > 0 else 0.0
    avg_loss = abs(perf.gross_loss / perf.loss_count) if perf.loss_count > 0 else 0.0
    
    # Calculate average hold time (from recent trades if available)
    avg_hold_time = 0.0
    # Note: This would need to be tracked in bot.recent_trades with duration_sec
    # For now, use a placeholder
    
    # Signal flow (signals per minute)
    signal_flow = sig_health.signals_generated / max(gs.runtime_seconds / 60.0, 1.0) if gs.runtime_seconds > 0 else 0.0
    
    # PnL momentum from market snapshot
    pnl_momentum = market.pnl_momentum
    
    # Stop distance from market snapshot
    stop_dist = market.avg_stop_distance_pct
    
    # Map positions from EngineSnapshot
    open_positions = []
    for pos in engine_snapshot.open_positions:
        # Map PositionView from EngineSnapshot to PositionView for Rich UI
        # Calculate risk-reward ratio
        rr = None
        if pos.stop_loss and pos.take_profit and pos.entry_price > 0:
            if pos.side == "LONG":
                stop_distance = abs(pos.entry_price - pos.stop_loss) / pos.entry_price
                profit_distance = abs(pos.take_profit - pos.entry_price) / pos.entry_price
            else:  # SHORT
                stop_distance = abs(pos.stop_loss - pos.entry_price) / pos.entry_price
                profit_distance = abs(pos.entry_price - pos.take_profit) / pos.entry_price
            rr = profit_distance / stop_distance if stop_distance > 0 else None
        
        # Determine exit strategy
        exit_strategy_pos = "ATR" if pos.stop_loss and pos.pnl_pct > 1.0 else "Trailing" if pos.stop_loss else "Fixed"
        
        open_positions.append(PositionView(
            symbol=pos.symbol,
            side=pos.side,
            qty=pos.size,
            entry_price=pos.entry_price,
            last_price=pos.current_price,
            pnl_abs=pos.pnl_abs,
            pnl_pct=pos.pnl_pct,
            leverage=float(pos.leverage),
            age_s=pos.age_seconds,
            score=pos.signal_score,
            exit_strategy=exit_strategy_pos,
            stop_loss=pos.stop_loss,
            take_profit=pos.take_profit,
            size_pct=pos.size_pct,
            rr=rr,
            max_pct=pos.max_pct,
            sl_pct=pos.stop_loss_pct,
            corr=pos.correlation,
            dd_time=pos.drawdown_time_seconds,
            is_unicorn=pos.is_unicorn,
            recovery_score=pos.recovery_score,
            current_r=pos.current_r,
        ))
    
    # Map events from EngineSnapshot
    recent_events = []
    for event in engine_snapshot.recent_events:
        # Format message based on event type
        if event.event_type == 'ENTRY':
            score_str = f"{event.score:.0f}" if event.score else "?"
            unicorn_prefix = "🦄 " if event.is_unicorn else ""
            message = f"{unicorn_prefix}{event.side or '?'} {event.symbol} @ ${event.price:.2f} (Score: {score_str})"
        elif event.event_type == 'EXIT':
            pnl_str = f"${event.pnl:+.2f}" if event.pnl is not None else "$0.00"
            reason = event.reason or 'closed'
            
            # Format exit message with clear action labels
            if event.is_partial:
                action_label = "PARTIAL EXIT" if "prs_" in reason or "scale" not in reason.lower() else "SCALE OUT"
                message = f"{action_label} – {event.side or '?'} {event.symbol} @ ${event.price:.2f} PnL: {pnl_str}"
                if event.pnl_pct is not None:
                    message += f" ({event.pnl_pct:+.2f}%)"
                message += f" [{reason}]"
            else:
                # Full exit
                message = f"EXIT – {event.side or '?'} {event.symbol} @ ${event.price:.2f} PnL: {pnl_str}"
                if event.pnl_pct is not None:
                    message += f" ({event.pnl_pct:+.2f}%)"
                message += f" [{reason}]"
        else:
            message = f"{event.symbol} @ ${event.price:.2f}"
        
        recent_events.append(EventView(
            timestamp=event.timestamp,
            type=event.event_type,
            message=message,
            is_unicorn=event.is_unicorn,
        ))
    
    # Map signal queue from EngineSnapshot
    signal_queue = []
    for sig in engine_snapshot.signal_queue:
        signal_queue.append(SignalQueueItem(
            symbol=sig.symbol,
            score=sig.score,
            side=sig.side,
            status=sig.status,
            reason=sig.rejection_reason or "Unknown",
            price=None,  # Not stored in EngineSnapshot
        ))
    
    # Map LLM messages from EngineSnapshot
    llm_messages = []
    llm_last_messages = []
    for msg in engine_snapshot.llm_messages:
        llm_last_messages.append(msg.message)
        llm_messages.append(LLMMessage(
            message=msg.message,
            action=None,
            confidence=msg.priority / 100.0 if msg.priority else 0.75,
        ))
    
    # Warnings/errors (not in EngineSnapshot, keep empty for now)
    warnings = []
    errors = []
    
    # Build BotState from EngineSnapshot
    state = BotState(
        now=now,
        runtime_s=gs.runtime_seconds,
        mode=gs.mode,
        exchange=gs.exchange,
        version=gs.version,
        scan_number=gs.scan_number,
        universe_size=gs.universe_size,
        loop_latency_ms=gs.loop_latency_ms,
        api_calls_last_min=gs.api_calls_last_min,
        api_limit_per_min=gs.api_limit_per_min,
        balance=perf.balance,
        equity=perf.equity,
        realized_pnl=perf.realized_pnl,
        unrealized_pnl=perf.unrealized_pnl,
        win_rate=perf.win_rate / 100.0,  # Convert % to 0-1
        profit_factor=perf.profit_factor,
        max_drawdown_pct=perf.max_drawdown_pct,
        sharpe_ratio=sharpe_ratio,
        entry_fees_total=perf.realized_entry_fees,
        exit_fees_total=perf.realized_exit_fees,
        slippage_total=perf.realized_slippage,
        funding_total=perf.realized_funding,
        total_costs=perf.total_costs,
        avg_win=avg_win,
        avg_loss=avg_loss,
        total_trades=perf.win_count + perf.loss_count,
        avg_hold_time=avg_hold_time,
        signal_flow=signal_flow,
        pass_rate=sig_health.pass_rate,
        avg_signal_score=sig_health.avg_signal_score,
        pnl_momentum=pnl_momentum,
        stop_dist=stop_dist,
        btc_trend=market.btc_trend_direction,
        volatility_regime=market.volatility_regime.upper(),
        loss_streak=engine_snapshot.loss_streak,
        max_positions=engine_snapshot.max_positions,
        current_regime=engine_snapshot.current_regime,
        exit_strategy=engine_snapshot.exit_strategy,
        open_positions=open_positions,
        recent_events=recent_events,
        signal_queue=signal_queue,
        llm_messages=llm_messages,
        llm_last_messages=llm_last_messages,
        warnings=warnings,
        errors=errors,
    )

    try:
        log.debug(
            "[UI] BotState created",
            positions=len(state.open_positions),
            events=len(state.recent_events),
            signals=len(state.signal_queue),
            equity=state.equity,
            balance=state.balance,
        )
    except Exception as e:
        log.error(f"[UI] BotState inspection failed: {type(e).__name__}: {e}", exc_info=True)

    return state

