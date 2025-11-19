"""
Smoke Test - Quick stability test for bot.
Runs bot for a short time and captures basic metrics.
"""

import os
import sys
import asyncio
import time
import subprocess
from pathlib import Path
from typing import Dict, Any

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Set environment for smoke test
os.environ['DRY_RUN'] = '1'
os.environ['LOG_LEVEL'] = 'WARNING'  # Reduce log noise
os.environ['UI_MODE'] = 'legacy'  # Use legacy UI (no Rich UI for smoke test)
os.environ['USE_RICH_UI'] = '0'  # Disable Rich UI

# Small universe for fast testing
os.environ['SYMBOLS_TO_SCAN'] = '10'  # Only scan 10 symbols
os.environ['MAX_ACTIVE_SYMBOLS'] = '20'  # Small universe

# Reduce scan intervals for faster testing
os.environ['UNIVERSE_REFRESH_INTERVAL_SEC'] = '5'  # Refresh every 5s
os.environ['SCAN_INTERVAL_SEC'] = '2'  # Scan every 2s

# Test duration: 2-5 minutes or fixed loops
TEST_DURATION_SEC = int(os.getenv("SMOKE_TEST_DURATION", "120"))  # 2 minutes default
MAX_LOOPS = int(os.getenv("SMOKE_TEST_MAX_LOOPS", "100"))  # Max 100 loops

# Output file
SUMMARY_FILE = Path("reports/smoke_test_summary.txt")
SUMMARY_FILE.parent.mkdir(exist_ok=True)


async def run_smoke_test() -> Dict[str, Any]:
    """
    Run smoke test and collect metrics.
    
    Returns:
        Test results dictionary
    """
    results = {
        'exit_code': 0,
        'errors': 0,
        'tracebacks': 0,
        'open_positions': 0,
        'status': 'UNKNOWN'
    }
    
    start_time = time.time()
    loop_count = 0
    
    try:
        # Import bot
        from app.bot import ScalperBot
        from app.logger import get_logger
        
        logger = get_logger("SmokeTest")
        logger.info("Starting smoke test...")
        
        # Create bot instance
        try:
            bot = ScalperBot()
        except Exception as e:
            raise Exception(f"Bot initialization failed: {type(e).__name__}: {e}")
        
        # Initialize exchange
        try:
            await bot.init_exchange()
        except Exception as e:
            raise Exception(f"Exchange initialization failed: {type(e).__name__}: {e}")
        
        # Run bot for test duration or max loops
        bot_task = None
        try:
            bot_task = asyncio.create_task(bot.run())
        except Exception as e:
            raise Exception(f"Bot.run() task creation failed: {type(e).__name__}: {e}")
        
        try:
            while True:
                await asyncio.sleep(1.0)  # Check every second
                loop_count += 1
                elapsed = time.time() - start_time
                
                # Check if test duration exceeded
                if elapsed >= TEST_DURATION_SEC:
                    break
                
                # Check if max loops exceeded
                if loop_count >= MAX_LOOPS:
                    break
                
                # Check for errors
                if hasattr(bot, 'recent_errors') and bot.recent_errors:
                    results['errors'] = len(bot.recent_errors)
        
        except KeyboardInterrupt:
            pass
        finally:
            # Stop bot
            if bot_task:
                bot_task.cancel()
                try:
                    await asyncio.wait_for(bot_task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            
            # Cleanup
            if hasattr(bot, 'exchange_wrapper') and bot.exchange_wrapper:
                try:
                    await bot.exchange_wrapper.close()
                except Exception:
                    pass
            
            # Get final state
            if hasattr(bot, 'positions'):
                results['open_positions'] = len(bot.positions)
        
        results['status'] = 'OK'
        results['exit_code'] = 0
        
    except Exception as e:
        results['status'] = 'FAILED'
        results['exit_code'] = 1
        results['errors'] = 1
        # Log error with traceback for debugging
        import traceback
        try:
            from app.logger import get_logger
            error_msg = f"Smoke test failed: {type(e).__name__}: {e}\n{traceback.format_exc()}"
            get_logger("SmokeTest").error(error_msg)
            # Also write to a debug file
            debug_file = Path("reports/smoke_test_debug.txt")
            with open(debug_file, 'w', encoding='utf-8') as f:
                f.write(error_msg)
        except Exception:
            pass
    
    # Check log files for tracebacks and errors
    log_dir = Path("logs")
    if log_dir.exists():
        log_files = sorted(log_dir.glob("bot_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if log_files:
            latest_log = log_files[0]
            try:
                with open(latest_log, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    results['tracebacks'] = content.count('Traceback')
                    # Count ERROR lines (excluding expected ones)
                    error_lines = [line for line in content.split('\n') if 'ERROR' in line.upper()]
                    # Filter out expected errors (like "ERROR fetching ticker" for invalid symbols)
                    unexpected_errors = [e for e in error_lines if 'EXIT_FAILED' in e or 'FATAL' in e]
                    results['errors'] = max(results['errors'], len(unexpected_errors))
            except Exception:
                pass
    
    return results


def write_summary(results: Dict[str, Any]):
    """
    Write one-line summary to file.
    
    Args:
        results: Test results
    """
    status = results['status']
    exit_code = results['exit_code']
    errors = results['errors']
    tracebacks = results['tracebacks']
    open_positions = results['open_positions']
    
    # Determine final status
    if status == 'OK' and errors == 0 and tracebacks == 0:
        final_status = 'OK'
    else:
        final_status = 'FAILED'
    
    # Write one-line summary
    summary_line = (
        f"{final_status} | exit_code={exit_code} | "
        f"errors={errors} | tracebacks={tracebacks} | "
        f"open_positions={open_positions}"
    )
    
    with open(SUMMARY_FILE, 'w', encoding='utf-8') as f:
        f.write(summary_line + '\n')


async def main():
    """Main smoke test entry point."""
    # Minimal stdout
    print("Running smoke test...", end='', flush=True)
    
    results = await run_smoke_test()
    
    # Write summary
    write_summary(results)
    
    # Minimal output
    status_icon = "[OK]" if results['status'] == 'OK' and results['errors'] == 0 and results['tracebacks'] == 0 else "[FAIL]"
    print(f" {status_icon}")
    print(f"Summary: {results['status']} | Errors: {results['errors']} | Tracebacks: {results['tracebacks']}")
    print(f"Results written to: {SUMMARY_FILE}")
    
    # Exit with appropriate code
    sys.exit(0 if results['status'] == 'OK' and results['errors'] == 0 and results['tracebacks'] == 0 else 1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSmoke test interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\nSmoke test failed: {e}")
        # Write failure summary
        write_summary({
            'status': 'FAILED',
            'exit_code': 1,
            'errors': 1,
            'tracebacks': 0,
            'open_positions': 0
        })
        sys.exit(1)

