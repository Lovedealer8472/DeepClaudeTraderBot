"""
Short Boot Test - Very short bot run to verify initialization and basic operation.
Runs bot for 30-60 seconds to ensure all components initialize correctly.
"""

import os
import sys
import asyncio
import time
from pathlib import Path
from typing import Dict, Any

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Set environment for short boot test
os.environ['DRY_RUN'] = '1'
os.environ['LOG_LEVEL'] = 'WARNING'  # Reduce log noise
os.environ['UI_MODE'] = 'legacy'  # Use legacy UI
os.environ['USE_RICH_UI'] = '0'  # Disable Rich UI

# Very small universe for fast testing
os.environ['SYMBOLS_TO_SCAN'] = '5'  # Only scan 5 symbols
os.environ['MAX_ACTIVE_SYMBOLS'] = '10'  # Small universe

# Reduce scan intervals for faster testing
os.environ['UNIVERSE_REFRESH_INTERVAL_SEC'] = '3'  # Refresh every 3s
os.environ['SCAN_INTERVAL_SEC'] = '1'  # Scan every 1s

# Test duration: 30-60 seconds or fixed loops
TEST_DURATION_SEC = int(os.getenv("SHORT_BOOT_DURATION", "60"))  # 60 seconds default
MAX_LOOPS = int(os.getenv("SHORT_BOOT_MAX_LOOPS", "50"))  # Max 50 loops

# Output file
SUMMARY_FILE = Path("reports/short_boot_summary.txt")
SUMMARY_FILE.parent.mkdir(exist_ok=True)


async def run_short_boot_test() -> Dict[str, Any]:
    """
    Run short boot test and collect metrics.
    
    Returns:
        Test results dictionary
    """
    results = {
        'exit_code': 0,
        'errors': 0,
        'tracebacks': 0,
        'open_positions': 0,
        'duration_sec': 0,
        'status': 'UNKNOWN'
    }
    
    start_time = time.time()
    loop_count = 0
    
    try:
        # Import bot
        from app.bot import ScalperBot
        from app.logger import get_logger
        
        logger = get_logger("ShortBootTest")
        logger.info("Starting short boot test...")
        
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
        
        # Verify key components initialized
        if not hasattr(bot, 'exit_pipeline') or bot.exit_pipeline is None:
            raise Exception("ExitPipeline not initialized")
        
        if not hasattr(bot, 'position_registry') or bot.position_registry is None:
            raise Exception("PositionRegistry not initialized")
        
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
            
            results['duration_sec'] = int(time.time() - start_time)
        
        results['status'] = 'OK'
        results['exit_code'] = 0
        
    except Exception as e:
        results['status'] = 'FAILED'
        results['exit_code'] = 1
        results['errors'] = 1
        # Log error but don't print to stdout
        try:
            from app.logger import get_logger
            get_logger("ShortBootTest").error(f"Short boot test failed: {type(e).__name__}: {e}")
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
                    # Filter out expected errors
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
    duration = results.get('duration_sec', 0)
    
    # Determine final status
    if status == 'OK' and errors == 0 and tracebacks == 0:
        final_status = 'OK'
    else:
        final_status = 'FAILED'
    
    # Write one-line summary
    summary_line = (
        f"{final_status} | duration={duration}s | "
        f"exit_code={exit_code} | errors={errors} | tracebacks={tracebacks} | "
        f"open_positions={open_positions}"
    )
    
    with open(SUMMARY_FILE, 'w', encoding='utf-8') as f:
        f.write(summary_line + '\n')


async def main():
    """Main short boot test entry point."""
    # Minimal stdout
    print("Running short boot test...", end='', flush=True)
    
    results = await run_short_boot_test()
    
    # Write summary
    write_summary(results)
    
    # Minimal output
    status_icon = "[OK]" if results['status'] == 'OK' and results['errors'] == 0 and results['tracebacks'] == 0 else "[FAIL]"
    print(f" {status_icon}")
    print(f"Summary: {results['status']} | Duration: {results.get('duration_sec', 0)}s | Errors: {results['errors']} | Tracebacks: {results['tracebacks']}")
    print(f"Results written to: {SUMMARY_FILE}")
    
    # Exit with appropriate code
    sys.exit(0 if results['status'] == 'OK' and results['errors'] == 0 and results['tracebacks'] == 0 else 1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShort boot test interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\nShort boot test failed: {e}")
        # Write failure summary
        write_summary({
            'status': 'FAILED',
            'exit_code': 1,
            'errors': 1,
            'tracebacks': 0,
            'open_positions': 0,
            'duration_sec': 0
        })
        sys.exit(1)

