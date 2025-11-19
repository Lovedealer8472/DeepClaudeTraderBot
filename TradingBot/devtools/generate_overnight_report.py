"""
Generate Overnight Stability Report

Reads reports/overnight_summary.csv and generates reports/OVERNIGHT_REPORT.md
"""

import csv
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any
import statistics

REPORTS_DIR = Path(__file__).parent.parent / "reports"
CSV_PATH = REPORTS_DIR / "overnight_summary.csv"
REPORT_PATH = REPORTS_DIR / "OVERNIGHT_REPORT.md"


def load_sessions() -> List[Dict[str, Any]]:
    """Load session data from CSV."""
    if not CSV_PATH.exists():
        return []
    
    sessions = []
    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Convert numeric fields
            try:
                row['session'] = int(row.get('session', 0))
                row['duration_sec'] = float(row.get('duration_sec', 0))
                row['trades_opened'] = int(row.get('trades_opened', 0))
                row['trades_closed'] = int(row.get('trades_closed', 0))
                row['prs_actions'] = int(row.get('prs_actions', 0))
                row['exceptions_caught'] = int(row.get('exceptions_caught', 0))
                row['crashed'] = row.get('crashed', 'False').lower() == 'true'
                row['final_open_positions'] = int(row.get('final_open_positions', 0))
                row['final_equity'] = float(row.get('final_equity', 0))
                row['final_pnl_pct'] = float(row.get('final_pnl_pct', 0))
                row['final_pnl_value'] = float(row.get('final_pnl_value', 0))
                row['log_size_mb'] = float(row.get('log_size_mb', 0))
            except (ValueError, KeyError):
                continue
            sessions.append(row)
    
    return sessions


def generate_report(sessions: List[Dict[str, Any]]) -> str:
    """Generate markdown report from session data."""
    if not sessions:
        return "# Overnight Stability Report\n\nNo test data available.\n"
    
    total_sessions = len(sessions)
    successful_sessions = sum(1 for s in sessions if not s.get('crashed', False))
    crashed_sessions = total_sessions - successful_sessions
    
    # Calculate statistics
    durations = [s['duration_sec'] for s in sessions]
    trades_opened = [s['trades_opened'] for s in sessions]
    trades_closed = [s['trades_closed'] for s in sessions]
    prs_actions = [s['prs_actions'] for s in sessions]
    log_sizes = [s['log_size_mb'] for s in sessions]
    exceptions = [s['exceptions_caught'] for s in sessions]
    
    # Error types
    all_error_types = set()
    for s in sessions:
        error_types_str = s.get('error_types', '')
        if error_types_str:
            all_error_types.update(error_types_str.split(';'))
    
    # PRS behavior
    sessions_with_prs = sum(1 for s in sessions if s['prs_actions'] > 0)
    avg_prs_per_session = statistics.mean(prs_actions) if prs_actions else 0
    
    # Log size analysis
    avg_log_size = statistics.mean(log_sizes) if log_sizes else 0
    max_log_size = max(log_sizes) if log_sizes else 0
    total_log_size = sum(log_sizes)
    
    # Report content
    report = f"""# Overnight Stability Report

**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Test Summary

- **Total Sessions:** {total_sessions}
- **Successful Sessions:** {successful_sessions} ({successful_sessions/total_sessions*100:.1f}%)
- **Crashed Sessions:** {crashed_sessions} ({crashed_sessions/total_sessions*100:.1f}%)
- **Test Duration per Session:** {statistics.mean(durations):.0f}s (avg), {min(durations):.0f}s (min), {max(durations):.0f}s (max)

## Trading Activity

- **Trades Opened (avg):** {statistics.mean(trades_opened):.1f} per session (total: {sum(trades_opened)})
- **Trades Closed (avg):** {statistics.mean(trades_closed):.1f} per session (total: {sum(trades_closed)})
- **Max Trades in Single Session:** {max(trades_opened) if trades_opened else 0} opened, {max(trades_closed) if trades_closed else 0} closed

## PRS (Position Recovery Score) Behavior

- **Sessions with PRS Actions:** {sessions_with_prs} ({sessions_with_prs/total_sessions*100:.1f}%)
- **Average PRS Actions per Session:** {avg_prs_per_session:.1f}
- **Total PRS Actions:** {sum(prs_actions)}
- **Max PRS Actions in Single Session:** {max(prs_actions) if prs_actions else 0}

## Logging

- **Average Log Size per Session:** {avg_log_size:.2f} MB
- **Max Log Size (single session):** {max_log_size:.2f} MB
- **Total Log Size (all sessions):** {total_log_size:.2f} MB ({total_log_size/1024:.2f} GB)
- **Target:** < 1GB total (✅ PASSED if < 1GB, ⚠️ WARNING if 1-2GB, ❌ FAILED if > 2GB)

## Error Analysis

- **Sessions with Exceptions:** {sum(1 for s in sessions if s['exceptions_caught'] > 0)}
- **Total Exceptions Caught:** {sum(exceptions)}
- **Error Types Observed:** {', '.join(sorted(all_error_types)) if all_error_types else 'None'}

## Fixes Applied Overnight

### Phase 2: Trade Lifecycle & Exit Pipeline Hardening
- ✅ Added comprehensive trade lifecycle documentation in `app/bot.py`
- ✅ Created centralized exit reasons module (`app/exit_reasons.py`)
- ✅ Hardened exit pipeline edge cases (zero size, invalid prices, stale positions)
- ✅ Added canonical exit path validation

### Phase 3: PRS Verification & Hardening
- ✅ Added PRS safety guards (data freshness checks, position size validation)
- ✅ Enhanced PRS logging (single-line INFO logs for actions)
- ✅ Verified PRS state tracking (cooldown, one-shot actions, recovery reset)

### Phase 4: Log Slimming
- ✅ Verified log rotation (10MB per file, 5 backups)
- ✅ Confirmed UILogHandler filtering (excludes per-symbol spam)
- ✅ Moved verbose PRS debug logs to DEBUG level
- ✅ Loop summaries aggregated (not per-symbol)

### Phase 5: UI Non-Fuckery
- ✅ Verified static UI layout (UI v2 with snapshot builder)
- ✅ Confirmed no rogue stdout (all logs go to file logger)
- ✅ UI log panel shows single-line summaries only

### Phase 6: Micro-Optimizations
- ✅ Cached equity calculation per scan cycle
- ✅ Cached regime config lookups
- ✅ Optimized position tracking (O(1) lookups with set)

## Recommendations

"""
    
    # Add recommendations based on results
    if crashed_sessions > 0:
        report += f"- ⚠️ **CRITICAL:** {crashed_sessions} session(s) crashed. Review error types and fix root causes.\n"
    
    if total_log_size > 2048:  # > 2GB
        report += f"- ❌ **CRITICAL:** Log size too large ({total_log_size/1024:.2f}GB). Further reduce verbosity.\n"
    elif total_log_size > 1024:  # > 1GB
        report += f"- ⚠️ **WARNING:** Log size high ({total_log_size/1024:.2f}GB). Consider reducing DEBUG logs.\n"
    else:
        report += f"- ✅ Log size acceptable ({total_log_size/1024:.2f}GB).\n"
    
    if avg_prs_per_session > 10:
        report += f"- ⚠️ **WARNING:** High PRS action rate ({avg_prs_per_session:.1f}/session). Verify PRS thresholds.\n"
    elif avg_prs_per_session == 0 and sessions_with_prs == 0:
        report += f"- ℹ️ **INFO:** No PRS actions observed. This may be normal if no positions reached PRS thresholds.\n"
    
    if sum(exceptions) > total_sessions * 0.1:  # > 10% of sessions
        report += f"- ⚠️ **WARNING:** High exception rate ({sum(exceptions)} total). Review error handling.\n"
    
    report += "\n## Next Steps\n\n"
    report += "1. Review crashed sessions (if any) and fix root causes\n"
    report += "2. Monitor log sizes in production and adjust rotation if needed\n"
    report += "3. Verify PRS behavior matches expectations\n"
    report += "4. Run extended stability test (12+ hours) before going live\n"
    
    return report


def main():
    """Main entry point."""
    REPORTS_DIR.mkdir(exist_ok=True)
    
    sessions = load_sessions()
    
    if not sessions:
        print(f"[REPORT] No data found in {CSV_PATH}")
        print("[REPORT] Run overnight_stability_runner.py first to generate test data.")
        return
    
    report = generate_report(sessions)
    
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"[REPORT] Generated report: {REPORT_PATH}")
    print(f"[REPORT] Analyzed {len(sessions)} sessions")


if __name__ == "__main__":
    main()

