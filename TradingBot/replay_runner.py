"""
Replay Runner - Backtest the trading bot on historical data.

Usage:
    python replay_runner.py --start 2024-01-01 --hours 24 --timeframe 1m --symbols BTC/USDT:USDT ETH/USDT:USDT
    python replay_runner.py --start 2024-01-01T00:00:00 --end 2024-01-02T00:00:00 --timeframe 5m
"""

import asyncio
import argparse
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

from app.bot import ScalperBot
from app.config import DRY_RUN
from app.data.replay_feed import ReplayDataFeed
from app.data.load_history import load_history
import ccxt


def parse_datetime(s: str) -> int:
    """Parse datetime string to Unix timestamp."""
    formats = [
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            return int(dt.timestamp())
        except ValueError:
            continue
    raise ValueError(f"Invalid datetime format: {s}")


async def main():
    parser = argparse.ArgumentParser(description="Replay/backtest trading bot on historical data")
    parser.add_argument("--start", required=True, help="Start datetime (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)")
    parser.add_argument("--end", help="End datetime (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)")
    parser.add_argument("--hours", type=float, help="Number of hours from start (alternative to --end)")
    parser.add_argument("--timeframe", default="1m", help="Timeframe (1m, 5m, 15m, 1h, etc.)")
    parser.add_argument("--symbols", nargs="+", default=["BTC/USDT:USDT"], help="Trading symbols")
    parser.add_argument("--data-dir", default="data/history", help="Directory for CSV data files")
    parser.add_argument("--exchange", default="binance", help="Exchange name (for API fallback)")
    
    args = parser.parse_args()
    
    # Parse start time
    start_ts = parse_datetime(args.start)
    
    # Parse end time
    if args.end:
        end_ts = parse_datetime(args.end)
    elif args.hours:
        end_ts = start_ts + int(args.hours * 3600)
    else:
        end_ts = start_ts + 24 * 3600  # Default: 24 hours
    
    if end_ts <= start_ts:
        print("Error: End time must be after start time")
        return
    
    print(f"Replay Mode: {datetime.fromtimestamp(start_ts)} to {datetime.fromtimestamp(end_ts)}")
    print(f"Timeframe: {args.timeframe}")
    print(f"Symbols: {args.symbols}")
    print()
    
    # Create exchange instance for loading data (if needed)
    exchange = None
    try:
        exchange_class = getattr(ccxt, args.exchange)
        exchange = exchange_class({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}  # For futures
        })
    except Exception as e:
        print(f"Warning: Could not create exchange instance: {e}")
        print("Will only use CSV files if available")
    
    # Load historical candles
    print("Loading historical data...")
    candles_by_symbol = load_history(
        symbols=args.symbols,
        timeframe=args.timeframe,
        start_ts=start_ts,
        end_ts=end_ts,
        exchange=exchange,
        data_dir=args.data_dir
    )
    
    if not candles_by_symbol:
        print("Error: No historical data loaded")
        return
    
    # Create replay feed
    feed = ReplayDataFeed(candles_by_symbol)
    total_steps = feed.get_total_steps()
    print(f"Loaded {total_steps} candles total")
    print()
    
    # Create bot in REPLAY mode
    print("Initializing bot in REPLAY mode...")
    bot = ScalperBot()
    
    # Set replay mode
    bot.replay_mode = True
    bot.replay_feed = feed
    bot.replay_start_time = start_ts
    
    # Force DRY_RUN mode
    import app.config as config_module
    config_module.DRY_RUN = True
    
    # Initialize bot (but skip exchange connection)
    await bot.init_exchange()  # This will detect replay mode and skip real exchange
    
    print("Starting replay...")
    print()
    
    # Track stats
    initial_equity = bot.equity_now()
    signals_generated = 0
    entries = 0
    exits = 0
    
    # Replay loop
    step = 0
    while not feed.is_finished():
        step += 1
        
        # Update bot's current time from replay feed
        bot._replay_current_time = feed.get_current_time()
        
        # Run one scan cycle
        try:
            await bot.scan_and_enter_signals()
            await bot.monitor_and_exit_positions()
            
            # Track stats
            signals_generated = bot.signal_stats.get('signals_generated', 0)
            entries = len(bot.positions)
            
        except Exception as e:
            print(f"Error at step {step}: {e}")
            import traceback
            traceback.print_exc()
            break
        
        # Advance to next candle
        if not feed.step():
            break
        
        # Progress update every 100 steps
        if step % 100 == 0:
            current_time = datetime.fromtimestamp(feed.get_current_time())
            print(f"Step {step}/{total_steps} | Time: {current_time} | Positions: {len(bot.positions)}")
    
    # Final stats
    final_equity = bot.equity_now()
    pnl = final_equity - initial_equity
    pnl_pct = (pnl / initial_equity * 100) if initial_equity > 0 else 0.0
    
    print()
    print("=" * 60)
    print("REPLAY SUMMARY")
    print("=" * 60)
    print(f"Period: {datetime.fromtimestamp(start_ts)} to {datetime.fromtimestamp(end_ts)}")
    print(f"Timeframe: {args.timeframe}")
    print(f"Total steps: {step}")
    print()
    print(f"Signals generated: {signals_generated}")
    print(f"Entries: {entries}")
    print(f"Exits: {exits}")
    print()
    print(f"Initial equity: ${initial_equity:,.2f}")
    print(f"Final equity: ${final_equity:,.2f}")
    print(f"PnL: ${pnl:,.2f} ({pnl_pct:+.2f}%)")
    print()
    print(f"Wins: {bot.win_count}")
    print(f"Losses: {bot.loss_count - bot.win_count if bot.loss_count > bot.win_count else 0}")
    print(f"Win rate: {(bot.win_count / (bot.win_count + bot.loss_count) * 100) if (bot.win_count + bot.loss_count) > 0 else 0:.1f}%")
    print("=" * 60)
    
    # Cleanup
    await bot.cleanup()


if __name__ == "__main__":
    asyncio.run(main())

