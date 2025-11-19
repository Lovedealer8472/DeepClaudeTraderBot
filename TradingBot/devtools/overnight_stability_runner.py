"""
Overnight Stability Test Harness

Runs the bot N times in DRY_RUN mode to verify stability.
Collects metrics per session and writes to reports/overnight_summary.csv.

IMPORTANT: Run this in terminal ONLY. Do NOT stream output to LLM.
"""

import asyncio
import os
import sys
import time
import csv
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.bot import ScalperBot
from app.config import DRY_RUN, MAX_CONCURRENT_POS
from app.logger import get_logger

# Configuration
NUM_SESSIONS = int(os.getenv("OVERNIGHT_SESSIONS", "20"))
SESSION_DURATION_SEC = int(os.getenv("OVERNIGHT_SESSION_DURATION", "600"))  # 10 minutes default
UNIVERSE_LIMIT = int(os.getenv("OVERNIGHT_UNIVERSE_LIMIT", "15"))  # Limit to 15 symbols for speed
REPORTS_DIR = Path(__file__).parent.parent / "reports"
CSV_PATH = REPORTS_DIR / "overnight_summary.csv"

# Ensure DRY_RUN is enabled
os.environ["DRY_RUN"] = "1"


class SessionMetrics:
    """Collects metrics for a single bot session."""
    
    def __init__(self, session_idx: int):
        self.session_idx = session_idx
        self.start_time = None
        self.end_time = None
        self.dry_run = True
        self.trades_opened = 0
        self.trades_closed = 0
        self.prs_actions = 0
        self.exceptions_caught = 0
        self.error_types = set()
        self.crashed = False
        self.final_open_positions = 0
        self.final_equity = 0.0
        self.final_pnl_pct = 0.0
        self.final_pnl_value = 0.0
        self.log_dir = None
        self.log_size_mb = 0.0
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for CSV export."""
        return {
            "session": self.session_idx,
            "start_time": self.start_time.isoformat() if self.start_time else "",
            "end_time": self.end_time.isoformat() if self.end_time else "",
            "duration_sec": (self.end_time - self.start_time).total_seconds() if self.end_time and self.start_time else 0,
            "dry_run": self.dry_run,
            "trades_opened": self.trades_opened,
            "trades_closed": self.trades_closed,
            "prs_actions": self.prs_actions,
            "exceptions_caught": self.exceptions_caught,
            "error_types": ";".join(sorted(self.error_types)),
            "crashed": self.crashed,
            "final_open_positions": self.final_open_positions,
            "final_equity": self.final_equity,
            "final_pnl_pct": self.final_pnl_pct,
            "final_pnl_value": self.final_pnl_value,
            "log_dir": str(self.log_dir) if self.log_dir else "",
            "log_size_mb": self.log_size_mb
        }


async def run_bot_session(session_idx: int, duration_sec: int) -> SessionMetrics:
    """
    Run bot for a fixed duration and collect metrics.
    
    Args:
        session_idx: Session number (1..N)
        duration_sec: How long to run (seconds)
    
    Returns:
        SessionMetrics object
    """
    metrics = SessionMetrics(session_idx)
    metrics.start_time = datetime.now()
    
    bot = None
    logger = get_logger("OvernightRunner")
    
    try:
        logger.info(f"[SESSION {session_idx}] Starting bot session (duration={duration_sec}s)")
        
        # Create bot instance
        bot = ScalperBot()
        
        # Limit universe for faster testing
        if hasattr(bot.universe, 'max_symbols'):
            bot.universe.max_symbols = UNIVERSE_LIMIT
        elif hasattr(bot.universe, '_max_symbols'):
            bot.universe._max_symbols = UNIVERSE_LIMIT
        
        # Initialize exchange
        await bot.init_exchange()
        
        # Track initial state
        initial_positions = len(bot.positions)
        initial_equity = bot.start_equity
        
        # Track PRS actions (from exit_manager or bot monitoring)
        initial_prs_actions = 0
        if hasattr(bot, 'metrics') and 'exits_by_reason' in bot.metrics:
            exits = bot.metrics.get('exits_by_reason', {})
            initial_prs_actions = sum(
                count for reason, count in exits.items() 
                if 'prs' in reason.lower()
            )
        
        # Run bot for fixed duration
        start_run = time.time()
        end_run = start_run + duration_sec
        
        # Create a task that runs the bot
        bot_task = asyncio.create_task(bot.run())
        
        # Wait for duration or until bot stops
        try:
            await asyncio.wait_for(
                asyncio.shield(bot_task),
                timeout=duration_sec
            )
        except asyncio.TimeoutError:
            # Expected - stop bot after duration
            logger.info(f"[SESSION {session_idx}] Duration reached, stopping bot...")
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass
        
        # Collect final metrics
        metrics.end_time = datetime.now()
        metrics.final_open_positions = len(bot.positions)
        metrics.trades_opened = bot.metrics.get('entries_opened', 0)
        metrics.trades_closed = bot.metrics.get('exits_by_reason', {})
        if isinstance(metrics.trades_closed, dict):
            metrics.trades_closed = sum(metrics.trades_closed.values())
        
        # Calculate final equity and PnL
        metrics.final_equity = bot.start_equity + bot.realized_pnl_total
        if bot.start_equity > 0:
            metrics.final_pnl_pct = (bot.realized_pnl_total / bot.start_equity) * 100
        metrics.final_pnl_value = bot.realized_pnl_total
        
        # Count PRS actions from exit reasons
        if hasattr(bot, 'metrics') and 'exits_by_reason' in bot.metrics:
            exits = bot.metrics.get('exits_by_reason', {})
            final_prs_actions = sum(
                count for reason, count in exits.items() 
                if 'prs' in reason.lower()
            )
            metrics.prs_actions = final_prs_actions - initial_prs_actions
        else:
            metrics.prs_actions = 0
        
        # Get log directory and size
        log_dir = Path("logs")
        if log_dir.exists():
            metrics.log_dir = log_dir
            total_size = sum(f.stat().st_size for f in log_dir.glob("*.log") if f.is_file())
            metrics.log_size_mb = total_size / (1024 * 1024)
        
        logger.info(f"[SESSION {session_idx}] Completed successfully")
        
    except Exception as e:
        metrics.crashed = True
        metrics.error_types.add(type(e).__name__)
        metrics.exceptions_caught += 1
        logger.error(f"[SESSION {session_idx}] CRASHED: {type(e).__name__}: {e}", exc_info=True)
        
        # Try to collect partial metrics
        if bot:
            try:
                metrics.final_open_positions = len(bot.positions)
                metrics.final_equity = getattr(bot, 'start_equity', 0) + getattr(bot, 'realized_pnl_total', 0)
            except Exception:
                pass
    
    finally:
        # Cleanup
        if bot:
            try:
                if hasattr(bot, 'fast_storage') and bot.fast_storage:
                    bot.fast_storage.close()
                if hasattr(bot, 'exchange_wrapper') and bot.exchange_wrapper:
                    await bot.exchange_wrapper.close()
            except Exception:
                pass
        
        if not metrics.end_time:
            metrics.end_time = datetime.now()
    
    return metrics


def write_csv_row(metrics: SessionMetrics, csv_path: Path):
    """Write a single session's metrics to CSV."""
    file_exists = csv_path.exists()
    
    with open(csv_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            "session", "start_time", "end_time", "duration_sec",
            "dry_run", "trades_opened", "trades_closed", "prs_actions",
            "exceptions_caught", "error_types", "crashed",
            "final_open_positions", "final_equity", "final_pnl_pct", "final_pnl_value",
            "log_dir", "log_size_mb"
        ])
        
        if not file_exists:
            writer.writeheader()
        
        writer.writerow(metrics.to_dict())


async def main():
    """Main entry point for overnight stability test."""
    # Ensure reports directory exists
    REPORTS_DIR.mkdir(exist_ok=True)
    
    # Clear or append to CSV (append mode for multiple runs)
    # For fresh run, delete existing CSV
    if os.getenv("OVERNIGHT_CLEAR_CSV", "0") == "1":
        if CSV_PATH.exists():
            CSV_PATH.unlink()
    
    logger = get_logger("OvernightRunner")
    logger.info("="*80)
    logger.info("OVERNIGHT STABILITY TEST STARTING")
    logger.info(f"Sessions: {NUM_SESSIONS}, Duration per session: {SESSION_DURATION_SEC}s")
    logger.info(f"Universe limit: {UNIVERSE_LIMIT} symbols")
    logger.info(f"DRY_RUN: {DRY_RUN}")
    logger.info("="*80)
    
    # Print minimal summary to console (not full logs)
    print(f"[OVERNIGHT] Starting {NUM_SESSIONS} sessions...")
    print(f"[OVERNIGHT] Each session runs for {SESSION_DURATION_SEC}s")
    print(f"[OVERNIGHT] Results will be written to {CSV_PATH}")
    print(f"[OVERNIGHT] Detailed logs in logs/bot.log")
    print()
    
    for session_idx in range(1, NUM_SESSIONS + 1):
        print(f"[OVERNIGHT] Session {session_idx}/{NUM_SESSIONS}...", end=" ", flush=True)
        
        try:
            metrics = await run_bot_session(session_idx, SESSION_DURATION_SEC)
            write_csv_row(metrics, CSV_PATH)
            
            status = "CRASHED" if metrics.crashed else "OK"
            print(f"{status} (trades={metrics.trades_opened}, exits={metrics.trades_closed}, prs={metrics.prs_actions})")
            
        except Exception as e:
            print(f"FAILED: {e}")
            logger.error(f"Session {session_idx} failed: {e}", exc_info=True)
        
        # Small delay between sessions
        await asyncio.sleep(2.0)
    
    print()
    print(f"[OVERNIGHT] Test complete. Results in {CSV_PATH}")
    logger.info("="*80)
    logger.info("OVERNIGHT STABILITY TEST COMPLETE")
    logger.info("="*80)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[OVERNIGHT] Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n[OVERNIGHT] Fatal error: {e}")
        sys.exit(1)

