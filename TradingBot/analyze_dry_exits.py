import re
import sys
from pathlib import Path
from statistics import mean, median


# Match exit lines with R values: [EXIT] symbol side R=X.XX pnl=... hold=... reason=...
# Format: [EXIT] BTC/USDT LONG R=1.87 pnl=0.7400 hold=132s reason=hard_tp_dry_simple
EXIT_PAT = re.compile(
    r"\[EXIT\].*?R=([\-0-9\.]+).*?reason=(hard_sl_dry_simple|hard_tp_dry_simple|stop_loss_hit)"
)


def find_latest_log(log_dir: Path) -> Path:
    candidates = sorted(log_dir.glob("bot_*.log"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        raise SystemExit(f"No bot_*.log files in {log_dir}")
    return candidates[-1]


def load_r_values(log_path: Path):
    rs = []
    with log_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = EXIT_PAT.search(line)
            if not m:
                continue
            try:
                r = float(m.group(1))
                reason = m.group(2)
            except (ValueError, IndexError):
                continue
            rs.append((reason, r))
    return rs


def summarize(rs):
    if not rs:
        print("No DRY_SIMPLE exits found.")
        return

    total = len(rs)
    wins = [r for _, r in rs if r > 0]
    losses = [r for _, r in rs if r <= 0]

    win_count = len(wins)
    loss_count = len(losses)

    sum_win = sum(wins) if wins else 0.0
    sum_loss = sum(losses) if losses else 0.0

    pf = (sum_win / abs(sum_loss)) if sum_loss < 0 else float("inf") if sum_win > 0 else 0.0
    wr = (win_count / total) * 100.0

    all_r = [r for _, r in rs]

    print(f"Trades     : {total}")
    print(f"Wins       : {win_count} ({wr:.1f}%)")
    print(f"Avg R      : {mean(all_r):.3f}")
    print(f"Median R   : {median(all_r):.3f}")
    print(f"Sum R win  : {sum_win:.3f}")
    print(f"Sum R loss : {sum_loss:.3f}")
    print(f"PF         : {pf:.2f}")
    print()

    buckets = {
        "<= -1.0": [r for r in all_r if r <= -1.0],
        "-1.0..0": [r for r in all_r if -1.0 < r < 0.0],
        "0..+1.8": [r for r in all_r if 0.0 <= r < 1.8],
        ">= +1.8": [r for r in all_r if r >= 1.8],
    }
    print("R buckets:")
    for name, vals in buckets.items():
        if not vals:
            print(f"  {name:8}: 0")
        else:
            print(f"  {name:8}: {len(vals)} (avg={mean(vals):.3f})")

    reasons = {}
    for reason, r in rs:
        reasons.setdefault(reason, []).append(r)
    print("\nBy reason:")
    for reason, vals in reasons.items():
        print(f"  {reason:18}: {len(vals)} trades, avgR={mean(vals):.3f}")


def main():
    if len(sys.argv) > 1:
        log_path = Path(sys.argv[1])
    else:
        log_dir = Path("logs")
        log_path = find_latest_log(log_dir)

    print(f"Analyzing log: {log_path}")
    rs = load_r_values(log_path)
    summarize(rs)


if __name__ == "__main__":
    main()

