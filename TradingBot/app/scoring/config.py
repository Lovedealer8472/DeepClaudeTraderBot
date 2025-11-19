"""Scoring Configuration - Hard caps and thresholds."""

from typing import Dict, Tuple

# Per-module hard caps (SCALPER: tuned for scalper sustainability)
SCORE_CAPS: Dict[str, Tuple[float, float]] = {
    "liquidity":     (-5.0, 5.0),    # Modest range
    "regime":        (-4.0, 4.0),    # Small but meaningful
    "structure":     (-20.0, 20.0),  # Most influential (keep wide)
    "portfolio":     (-15.0, 5.0),   # Keep as-is
    "time_of_day":   (-3.0, 3.0),   # Small range
    "symbol_rating": (-3.0, 3.0),   # Modest effect
}

# Global score bounds
SCORE_MIN = 0.0
SCORE_MAX = 100.0

# Base score now lives on the full 0–100 range.
# The primary scoring engine is trusted to output 0–100 directly.
# Modules add small adjustments (±5-20 points) around the base.
BASE_SCORE_MIN = 0.0
BASE_SCORE_MAX = 100.0

# Percentile + score thresholds for final filtering
# LIVE MODE: Moderate minimum score (72-73 for balanced scalper setups)
MIN_SIGNAL_SCORE = 72.0        # LIVE MODE: minimum absolute score (72-73 range, allows more signals)
SIGNAL_PERCENTILE_THRESHOLD = 0.0  # LIVE MODE: Disabled (0.0 = no percentile filtering) - allows more signals

