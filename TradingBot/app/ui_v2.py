"""
UI v2 - Fully Static, Full-Screen, Non-Scrolling Rich TUI Dashboard

HTOP-style interface: Fixed layout, no scrolling, no output below UI.
All content clipped to fit terminal size.
"""

import sys
import os
import shutil
import threading
import queue
from typing import Optional

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box
from rich.live import Live

from .snapshot_builder import EngineSnapshot


class KeyboardHandler:
    """Non-blocking keyboard input handler."""
    
    def __init__(self):
        self.input_queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.thread = None
    
    def start(self):
        """Start keyboard listener thread."""
        if sys.platform == 'win32':
            # Windows: use msvcrt
            self.thread = threading.Thread(target=self._listen_windows, daemon=True)
        else:
            # Unix: use select/tty
            self.thread = threading.Thread(target=self._listen_unix, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop keyboard listener."""
        self.stop_flag.set()
    
    def get_key(self) -> Optional[str]:
        """Get pressed key (non-blocking)."""
        try:
            return self.input_queue.get_nowait()
        except queue.Empty:
            return None
    
    def _listen_windows(self):
        """Windows keyboard listener."""
        try:
            import msvcrt
            while not self.stop_flag.is_set():
                if msvcrt.kbhit():
                    key = msvcrt.getch().decode('utf-8', errors='ignore').lower()
                    self.input_queue.put(key)
                self.stop_flag.wait(0.1)
        except Exception:
            pass
    
    def _listen_unix(self):
        """Unix keyboard listener."""
        try:
            import select
            import tty
            import termios
            
            old_settings = termios.tcgetattr(sys.stdin)
            try:
                tty.setcbreak(sys.stdin.fileno())
                while not self.stop_flag.is_set():
                    if select.select([sys.stdin], [], [], 0.1)[0]:
                        key = sys.stdin.read(1).lower()
                        self.input_queue.put(key)
            finally:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        except Exception:
            pass


class UIv2:
    """
    UI v2 - Fully static, full-screen TUI dashboard.
    
    NO scrolling, NO output below UI, fixed layout like htop.
    """
    
    def __init__(self):
        # Use sys.__stdout__ to bypass any redirects
        self.console = Console(
            file=sys.__stdout__,
            force_terminal=True,
            legacy_windows=False,
            width=None,  # Auto-detect
            height=None,  # Auto-detect
        )
        self._live = None
        self._keyboard = KeyboardHandler()
        self._debug_mode = False
        self._last_snapshot = None
        
        # Detect terminal size
        self._update_terminal_size()
    
    def _update_terminal_size(self):
        """Detect and cache terminal size."""
        try:
            term_size = shutil.get_terminal_size()
            self.term_width = term_size.columns
            self.term_height = term_size.lines
        except Exception:
            # Fallback to default
            self.term_width = 120
            self.term_height = 40
        
        # Calculate panel heights based on terminal size
        self.top_height = 7
        self.bottom_height = 11
        # Positions table gets the remaining space
        self.positions_height = max(10, self.term_height - self.top_height - self.bottom_height - 4)
    
    def __enter__(self):
        """Start Live display with screen mode."""
        self._update_terminal_size()
        
        # Start keyboard handler
        self._keyboard.start()
        
        # Create Live display with screen=True for full-screen takeover
        self._live = Live(
            console=self.console,
            screen=True,  # Full-screen mode (clears screen, no scrolling)
            auto_refresh=False,  # Manual refresh only
            redirect_stdout=True,  # Redirect stdout to prevent leaks
            redirect_stderr=True,  # Redirect stderr to prevent leaks
        )
        self._live.__enter__()
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop Live display and keyboard handler."""
        self._keyboard.stop()
        
        if self._live:
            self._live.__exit__(exc_type, exc_val, exc_tb)
            self._live = None
    
    def render(self, snapshot: EngineSnapshot):
        """
        Render the UI from snapshot.
        
        This is the ONLY way the UI updates. No prints, no logs outside.
        """
        if not self._live:
            return
        
        # Handle keyboard input
        key = self._keyboard.get_key()
        if key == 'q':
            # Signal quit (caller should handle)
            raise KeyboardInterrupt("User pressed 'Q' to quit")
        elif key == 'r':
            # Force refresh (just re-render, snapshot is already fresh)
            pass
        elif key == 'd':
            # Toggle debug mode
            self._debug_mode = not self._debug_mode
        
        # Store snapshot for potential debug display
        self._last_snapshot = snapshot
        
        # OPTIMIZATION: Update terminal size only occasionally (every 5 seconds) to reduce jitter
        # Check terminal size only if we haven't checked recently
        import time
        if not hasattr(self, '_last_terminal_check') or (time.time() - self._last_terminal_check) > 5.0:
            self._update_terminal_size()
            self._last_terminal_check = time.time()
        
        # Build layout
        layout = self._build_layout(snapshot)
        
        # Update Live display (this overwrites the screen)
        # CRITICAL: Single update call per render to prevent flicker
        self._live.update(layout, refresh=True)
    
    def _build_layout(self, snapshot: EngineSnapshot) -> Layout:
        """
        Build fixed, non-scrolling layout.
        
        Structure:
        ┌─────────────────────────────────────────────────────────┐
        │ [ GLOBAL ] [ PERFORMANCE ] [ SIGNAL HEALTH ] [ MARKET ] │ (top_height)
        ├─────────────────────────────────────────────────────────┤
        │                   OPEN POSITIONS TABLE                   │ (positions_height)
        ├─────────────────────────────────────────────────────────┤
        │ [ RECENT ACTIVITY ] [ SIGNAL QUEUE ] [ LLM LOG ]        │ (bottom_height)
        └─────────────────────────────────────────────────────────┘
        """
        root = Layout()
        
        # Split into 3 rows with FIXED heights
        root.split_column(
            Layout(name="top", size=self.top_height),
            Layout(name="middle", size=self.positions_height),
            Layout(name="bottom", size=self.bottom_height),
        )
        
        # Top row: 4 boxes
        root["top"].split_row(
            Layout(self._build_global_panel(snapshot), name="global"),
            Layout(self._build_performance_panel(snapshot), name="performance"),
            Layout(self._build_signal_health_panel(snapshot), name="signal_health"),
            Layout(self._build_market_panel(snapshot), name="market"),
        )
        
        # Middle: Positions table (full width)
        root["middle"].update(self._build_positions_panel(snapshot))
        
        # Bottom row: 3 boxes
        root["bottom"].split_row(
            Layout(self._build_activity_panel(snapshot), name="activity"),
            Layout(self._build_signals_panel(snapshot), name="signals"),
            Layout(self._build_llm_panel(snapshot), name="llm"),
        )
        
        return root
    
    def _build_global_panel(self, snapshot: EngineSnapshot) -> Panel:
        """Build GLOBAL STATUS panel (compact)."""
        gs = snapshot.global_status
        
        # Format runtime
        runtime_h = gs.runtime_seconds // 3600
        runtime_m = (gs.runtime_seconds % 3600) // 60
        runtime_str = f"{runtime_h}h{runtime_m:02d}m" if runtime_h > 0 else f"{runtime_m}m"
        
        # Mode with color - PURE SCALPER MODE
        mode_style = "bold green" if gs.mode == "LIVE" else "bold yellow"
        
        content = Text()
        content.append(f"{gs.mode}  ", style=mode_style)
        content.append(f"[PURE SCALPER]  ", style="bold magenta")
        content.append(f"v{gs.version}  ", style="dim")
        content.append(f"{gs.exchange}\n", style="cyan")
        content.append(f"⏱ {runtime_str}  ", style="white")
        content.append(f"#", style="dim")
        content.append(f"{gs.scan_number}  ", style="cyan")
        content.append(f"🌐", style="dim")
        content.append(f"{gs.universe_size}\n", style="magenta")
        content.append(f"Loop: ", style="dim")
        content.append(f"{gs.loop_time_ms:.0f}ms  ", style="yellow")
        content.append(f"API: ", style="dim")
        content.append(f"{gs.api_calls_per_min}/{gs.api_limit_per_min}", style="yellow")
        
        return Panel(
            content,
            title="[bold cyan]⚙ GLOBAL[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    
    def _build_performance_panel(self, snapshot: EngineSnapshot) -> Panel:
        """Build PERFORMANCE panel (compact)."""
        perf = snapshot.performance
        
        # PnL colors
        total_pnl = perf.realized_pnl + perf.unrealized_pnl
        pnl_color = "green" if total_pnl >= 0 else "red"
        
        content = Text()
        content.append(f"Equity: ", style="dim")
        content.append(f"${perf.equity:.2f}  ", style="bold cyan")
        content.append(f"Bal: ", style="dim")
        content.append(f"${perf.balance:.2f}\n", style="white")
        
        content.append(f"PnL: ", style="dim")
        content.append(f"${total_pnl:+.2f}  ", style=f"bold {pnl_color}")
        content.append(f"(R:", style="dim")
        content.append(f"${perf.realized_pnl:+.2f} ", style=pnl_color)
        content.append(f"U:", style="dim")
        content.append(f"${perf.unrealized_pnl:+.2f})\n", style=pnl_color)
        
        wr_color = "green" if perf.win_rate >= 50 else "yellow" if perf.win_rate >= 40 else "red"
        pf_color = "green" if perf.profit_factor >= 1.5 else "yellow" if perf.profit_factor >= 1.0 else "red"
        content.append(f"WR: ", style="dim")
        content.append(f"{perf.win_rate:.1f}%  ", style=wr_color)
        content.append(f"PF: ", style="dim")
        content.append(f"{perf.profit_factor:.2f}  ", style=pf_color)
        content.append(f"{perf.win_count}W/{perf.loss_count}L", style="white")
        
        return Panel(
            content,
            title="[bold green]💰 PERFORMANCE[/bold green]",
            border_style="green",
            box=box.ROUNDED,
        )
    
    def _build_signal_health_panel(self, snapshot: EngineSnapshot) -> Panel:
        """
        Build SIGNAL HEALTH panel.
        
        Data source: snapshot.signal_health (SignalHealth dataclass)
        - signals_generated: Total signals generated (cumulative, from bot.filter_stats_cumulative)
        - signals_approved: Signals that passed filter pipeline AND position manager (cumulative)
        - signals_rejected: Rejected signals (signals_generated - signals_approved)
        - pass_rate: Percentage of signals approved (0-100%)
        - avg_signal_score: Running average of all signal scores (0-100)
        
        Updated in: bot.py scan_and_enter_signals() after each scan
        """
        # Defensive: Handle missing signal_health gracefully
        if not hasattr(snapshot, 'signal_health') or snapshot.signal_health is None:
            # Fallback to zero values if signal_health is missing
            total_signals = 0
            signals_approved = 0
            signals_rejected = 0
            pass_rate = 0.0
            avg_score = 0.0
        else:
            sig_health = snapshot.signal_health
            total_signals = sig_health.signals_generated or 0
            signals_approved = sig_health.signals_approved or 0
            signals_rejected = sig_health.signals_rejected or 0
            pass_rate = sig_health.pass_rate or 0.0
            avg_score = sig_health.avg_signal_score or 0.0
        
        content = Text()
        content.append(f"Signals: ", style="dim")
        content.append(f"{total_signals}  ", style="white")
        content.append(f"Pass: ", style="dim")
        pass_color = "green" if pass_rate >= 20 else "yellow" if pass_rate >= 10 else "red"
        content.append(f"{pass_rate:.0f}%\n", style=pass_color)
        
        content.append(f"Passed: ", style="dim")
        content.append(f"{signals_approved}  ", style="green")
        content.append(f"Rejects: ", style="dim")
        content.append(f"{signals_rejected}\n", style="red")
        
        content.append(f"Avg Score: ", style="dim")
        score_color = "green" if avg_score >= 70 else "yellow" if avg_score >= 60 else "white"
        content.append(f"{avg_score:.0f}", style=score_color)
        
        return Panel(
            content,
            title="[bold green]📊 SIGNAL HEALTH[/bold green]",
            border_style="green",
            box=box.ROUNDED,
        )
    
    def _build_market_panel(self, snapshot: EngineSnapshot) -> Panel:
        """Build MARKET SNAPSHOT panel."""
        risk = snapshot.risk
        
        # BTC trend formatting
        btc_arrow = "⬆" if risk.btc_trend == "UP" else "⬇" if risk.btc_trend == "DOWN" else "➡"
        btc_color = "green" if risk.btc_trend == "UP" else "red" if risk.btc_trend == "DOWN" else "white"
        
        # Position utilization
        pos_pct = (risk.open_positions / risk.max_positions * 100) if risk.max_positions > 0 else 0
        pos_color = "red" if pos_pct >= 90 else "yellow" if pos_pct >= 70 else "green"
        
        # PURE SCALPER MODE: Always show scalper, no regime switching
        regime_display = "PURE SCALPER"  # Hard-locked to scalper
        
        content = Text()
        content.append(f"BTC: ", style="dim")
        content.append(f"{btc_arrow} {risk.btc_trend}  ", style=btc_color)
        content.append(f"({risk.btc_trend_pct:+.2f}%)\n", style=btc_color)
        
        content.append(f"Vol: ", style="dim")
        content.append(f"{risk.volatility_regime}  ", style="yellow")
        content.append(f"Pos: ", style="dim")
        content.append(f"{risk.open_positions}/{risk.max_positions}\n", style=pos_color)
        
        content.append(f"Mode: ", style="dim")
        content.append(f"{regime_display}  ", style="bold magenta")
        content.append(f"Exit: ", style="dim")
        content.append(f"{risk.exit_strategy}", style="magenta")
        
        return Panel(
            content,
            title="[bold yellow]📈 MARKET[/bold yellow]",
            border_style="yellow",
            box=box.ROUNDED,
        )
    
    def _build_positions_panel(self, snapshot: EngineSnapshot) -> Panel:
        """
        Build OPEN POSITIONS table with FIXED height.
        
        CRITICAL: Table rows are clipped to fit panel height.
        NO overflow into terminal.
        
        Data source: snapshot.open_positions (List[PositionSnapshot])
        - Reads directly from bot.positions (position_registry._positions)
        - Updated in real-time when positions are opened/closed
        - Source: snapshot_builder.py line 424-536
        """
        table = Table(
            expand=True,
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold white on blue",
            border_style="blue",
            padding=(0, 1),
        )
        
        # 14 columns (compact widths)
        table.add_column("Symbol", style="bold", width=7, no_wrap=True)
        table.add_column("Side", width=4, justify="center", no_wrap=True)
        table.add_column("Entry", justify="right", width=8, no_wrap=True)
        table.add_column("PnL", justify="right", width=7, no_wrap=True)
        table.add_column("PnL%", justify="right", width=6, no_wrap=True)
        table.add_column("Max%", justify="right", width=5, no_wrap=True)
        table.add_column("Scr", justify="center", width=3, no_wrap=True)
        table.add_column("PRS", justify="center", width=3, no_wrap=True)
        table.add_column("Age", justify="center", width=6, no_wrap=True)
        table.add_column("SL%", justify="right", width=4, no_wrap=True)
        table.add_column("Lev", justify="center", width=3, no_wrap=True)
        table.add_column("Sz%", justify="right", width=5, no_wrap=True)
        table.add_column("Cor", justify="right", width=4, no_wrap=True)
        table.add_column("DD", justify="center", width=6, no_wrap=True)
        
        # Calculate max rows based on panel height
        # Panel height includes border (2 lines) + title (1 line) + header (1 line)
        # Remaining lines for data rows
        max_rows = max(1, self.positions_height - 4)
        
        # Defensive: Handle missing open_positions gracefully
        positions_list = getattr(snapshot, 'open_positions', None) or []
        if not positions_list:
            # Empty state
            table.add_row("—", "—", "—", "—", "—", "—", "—", "—", "—", "—", "—", "—", "—", "—", style="dim")
        else:
            # Clip to max_rows (most important positions first, already sorted by snapshot_builder)
            for pos in positions_list[:max_rows]:
                # Symbol with unicorn emoji
                if pos.is_unicorn:
                    symbol_display = f"🦄{pos.symbol[:5]}"
                    symbol_style = "bold bright_magenta"
                else:
                    symbol_display = pos.symbol[:7]
                    symbol_style = "white"
                
                # Side
                side_style = "green" if pos.side == "LONG" else "red"
                side_display = pos.side[:1]
                
                # PnL
                pnl_color = "green" if pos.pnl_value >= 0 else "red"
                
                # PnL%
                pnl_pct_color = "bold green" if pos.pnl_pct >= 1.0 else "green" if pos.pnl_pct >= 0 else "bold red" if pos.pnl_pct <= -1.0 else "red"
                
                # Score
                score_color = "bold green" if pos.score >= 80 else "green" if pos.score >= 70 else "yellow" if pos.score >= 60 else "red"
                
                # PRS
                if pos.prs is not None:
                    prs_color = "green" if pos.prs >= 70 else "yellow" if pos.prs >= 50 else "red"
                    prs_display = f"{pos.prs:.0f}"
                else:
                    prs_color = "dim"
                    prs_display = "—"
                
                # Size%
                size_color = "red" if pos.size_pct >= 25 else "yellow" if pos.size_pct >= 15 else "white"
                
                # Corr
                if pos.corr is not None:
                    corr_color = "green" if pos.corr > 0.5 else "red" if pos.corr < -0.5 else "white"
                    corr_display = f"{pos.corr:+.2f}"
                else:
                    corr_color = "dim"
                    corr_display = "—"
                
                table.add_row(
                    Text(symbol_display, style=symbol_style),
                    Text(side_display, style=f"bold {side_style}"),
                    Text(f"${pos.entry_price:.4f}", style="white"),
                    Text(f"${pos.pnl_value:+.2f}", style=pnl_color),
                    Text(f"{pos.pnl_pct:+.2f}%", style=pnl_pct_color),
                    Text(f"{pos.max_pct:+.2f}%" if pos.max_pct is not None else "—", style="cyan"),
                    Text(f"{pos.score:.0f}", style=score_color),
                    Text(prs_display, style=prs_color),
                    Text(pos.age_str, style="white"),
                    Text(f"{pos.sl_pct:.1f}%" if pos.sl_pct is not None else "—", style="yellow"),
                    Text(f"{pos.leverage}x", style="magenta"),
                    Text(f"{pos.size_pct:.1f}%", style=size_color),
                    Text(corr_display, style=corr_color),
                    Text(pos.dd_time if pos.dd_time else "—", style="cyan"),
                )
        
        # Title with count
        pos_count = len(positions_list)
        count_display = f"({pos_count})" if pos_count > 0 else "(0)"
        
        return Panel(
            table,
            title=f"[bold white on blue] OPEN POSITIONS {count_display} [/bold white on blue]",
            border_style="blue",
            box=box.ROUNDED,
        )
    
    def _build_activity_panel(self, snapshot: EngineSnapshot) -> Panel:
        """
        Build RECENT ACTIVITY panel with FIXED height.
        
        Shows last N events that fit in panel.
        """
        content = Text()
        
        # Calculate max events based on bottom panel height
        # Panel includes border (2) + title (1)
        max_events = max(1, self.bottom_height - 3)
        
        if not snapshot.recent_activity:
            content.append("No recent activity", style="dim")
        else:
            for event in snapshot.recent_activity[:max_events]:
                # Time
                time_str = event.timestamp.strftime("%H:%M:%S")
                content.append(f"{time_str} ", style="dim")
                
                # Message based on action
                if event.action == "ENTRY":
                    if event.is_unicorn:
                        content.append("🦄 ", style="bold bright_magenta")
                    score_str = f"{event.score:.0f}" if event.score else "?"
                    content.append(f"{event.side or '?'} {event.symbol}", style="bold")
                    content.append(f" @ ${event.price:.2f} ", style="white")
                    content.append(f"({score_str})", style="cyan")
                
                elif event.action in ("EXIT", "PARTIAL_EXIT", "SCALE_OUT"):
                    action_label = "EXIT" if event.action == "EXIT" else ("PART" if event.action == "PARTIAL_EXIT" else "SCAL")
                    action_style = "bold yellow" if event.action in ("PARTIAL_EXIT", "SCALE_OUT") else "bold white"
                    
                    content.append(f"{action_label} ", style=action_style)
                    content.append(f"{event.symbol}", style="bold")
                    
                    if event.pnl_value is not None:
                        pnl_color = "green" if event.pnl_value >= 0 else "red"
                        content.append(f" ${event.pnl_value:+.2f}", style=pnl_color)
                        if event.pnl_pct is not None:
                            content.append(f" ({event.pnl_pct:+.2f}%)", style=pnl_color)
                    
                    if event.reason and len(event.reason) < 20:
                        content.append(f" [{event.reason[:15]}]", style="dim")
                
                elif event.action == "REJECT":
                    content.append(f"REJ {event.symbol}", style="red")
                    if event.reason and len(event.reason) < 20:
                        content.append(f" [{event.reason[:15]}]", style="dim")
                
                content.append("\n")
        
        return Panel(
            content,
            title="[bold cyan]📊 RECENT ACTIVITY[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    
    def _build_signals_panel(self, snapshot: EngineSnapshot) -> Panel:
        """
        Build SIGNAL QUEUE panel with FIXED height.
        
        Data source: snapshot.signal_queue (List[SignalItem])
        - Shows recent rejected signals from bot.signal_history
        - Updated in snapshot_builder.py line 620-632
        - Source: bot.signal_history (deque, maxlen=20)
        """
        content = Text()
        
        max_signals = max(1, self.bottom_height - 3)
        
        # Defensive: Handle missing signal_queue gracefully
        signal_queue_list = getattr(snapshot, 'signal_queue', None) or []
        if not signal_queue_list:
            content.append("No signals in queue", style="dim")
        else:
            for sig in signal_queue_list[:max_signals]:
                # Symbol (7 chars) | Score | Status | Reason (truncated)
                symbol_display = sig.symbol[:7].ljust(7)
                score_color = "green" if sig.score >= 70 else "yellow" if sig.score >= 60 else "red"
                score_display = f"{sig.score:.0f}".rjust(3)
                
                status_display = "RJ" if sig.status == "REJECTED" else "PD"
                status_color = "red" if sig.status == "REJECTED" else "yellow"
                
                reason_display = sig.reason[:25] if sig.reason else "—"
                
                content.append(f"{symbol_display} ", style="white")
                content.append(f"{score_display} ", style=score_color)
                content.append(f"{status_display} ", style=status_color)
                content.append(f"{reason_display}\n", style="dim")
        
        return Panel(
            content,
            title="[bold yellow]🔔 SIGNAL QUEUE[/bold yellow]",
            border_style="yellow",
            box=box.ROUNDED,
        )
    
    def _build_llm_panel(self, snapshot: EngineSnapshot) -> Panel:
        """
        Build LOG panel with FIXED height.
        
        Displays recent log messages from in-memory buffer + LLM messages.
        """
        content = Text()
        
        max_msgs = max(1, self.bottom_height - 3)
        
        # Get recent logs from in-memory buffer
        try:
            from .logger import get_log_buffer
            log_buffer = get_log_buffer()
            recent_logs = log_buffer.get_recent(limit=max_msgs)
            
            if recent_logs:
                for log_entry in recent_logs[-max_msgs:]:
                    time_str = log_entry['timestamp'].strftime("%H:%M:%S")
                    level = log_entry['level']
                    message = log_entry['message']
                    
                    # Color by level
                    level_color = {
                        'DEBUG': 'dim',
                        'INFO': 'cyan',
                        'WARNING': 'yellow',
                        'ERROR': 'red',
                        'CRITICAL': 'bold red'
                    }.get(level, 'white')
                    
                    # Calculate available space: panel width - timestamp - level - padding
                    # Panel is ~33% of screen width, timestamp ~9 chars, level ~3 chars
                    # Use terminal width to calculate properly
                    prefix_len = len(time_str) + len(f"[{level[0]}]") + 2  # +2 for spaces
                    max_msg_len = max(60, (self.term_width // 3) - prefix_len - 2)  # -2 for padding
                    
                    # Truncate message intelligently (preserve word boundaries when possible)
                    if len(message) > max_msg_len:
                        # Try to truncate at word boundary (space or |)
                        truncated = message[:max_msg_len]
                        # Find last space or pipe in truncated string
                        last_space = max(
                            truncated.rfind(' '),
                            truncated.rfind('|')
                        )
                        # If found within last 20% of length, use it; otherwise hard truncate
                        if last_space > max_msg_len * 0.8:
                            msg_text = truncated[:last_space] + "..."
                        else:
                            msg_text = truncated.rstrip() + "..."
                    else:
                        msg_text = message
                    
                    content.append(f"{time_str} ", style="dim")
                    content.append(f"[{level[0]}] ", style=level_color)
                    # Use Text with no_wrap=True to prevent wrapping
                    msg_line = Text(f"{msg_text}\n", style="white", no_wrap=True, overflow="ellipsis")
                    content.append(msg_line)
            else:
                # Show LLM messages if no logs available
                if snapshot.llm_log:
                    for msg in snapshot.llm_log[:max_msgs]:
                        time_str = msg.timestamp.strftime("%H:%M:%S")
                        msg_style = "bold green" if msg.priority >= 70 else "yellow" if msg.priority >= 50 else "white"
                        msg_text = msg.message[:60] if len(msg.message) > 60 else msg.message
                        content.append(f"{time_str} ", style="dim")
                        content.append(f"{msg_text}\n", style=msg_style)
                else:
                    content.append("No log messages", style="dim")
        except Exception:
            # Fallback to LLM messages
            if snapshot.llm_log:
                for msg in snapshot.llm_log[:max_msgs]:
                    time_str = msg.timestamp.strftime("%H:%M:%S")
                    msg_style = "bold green" if msg.priority >= 70 else "yellow" if msg.priority >= 50 else "white"
                    msg_text = msg.message[:60] if len(msg.message) > 60 else msg.message
                    content.append(f"{time_str} ", style="dim")
                    content.append(f"{msg_text}\n", style=msg_style)
            else:
                content.append("No log messages", style="dim")
        
        return Panel(
            content,
            title="[bold magenta]📋 LOG[/bold magenta]",
            border_style="magenta",
            box=box.ROUNDED,
        )


def render_ui_v2(snapshot: EngineSnapshot) -> None:
    """
    Render UI v2 from engine snapshot (one-time render).
    
    For continuous display, use UIv2 class with context manager.
    
    NOTE: This function is deprecated. Use UIv2 class with context manager instead.
    This function may output to console, so it should NOT be used when UI is active.
    
    Args:
        snapshot: EngineSnapshot from build_engine_snapshot()
    """
    # DEPRECATED: This function should not be used in production
    # Use UIv2 class with context manager instead for proper stdout/stderr redirection
    ui = UIv2()
    console = ui.console
    layout = ui._build_layout(snapshot)
    # NOTE: This console.print will output to console - only use if UI is not active
    console.print(layout)
