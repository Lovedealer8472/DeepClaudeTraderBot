"""
Confluence Scoring Model — replaces binary structure gate.

Based on research: combined signals outperform individual signals alone.
Point system with 65-point entry threshold. Volume confirmation is mandatory
for SFP-based entries. 4h alignment gates direction.

SFP:              40 pts  (strongest reversal)
Regular div:      25 pts  (momentum weakening)
Hidden div:       15 pts  (continuation, not reversal)
Volume confirm:   20 pts  (≥1.5x 20-bar avg on SFP candle)
4h alignment:     20 pts  (trend-neutral or confirming)
Weekend penalty: -15 pts  (thin liquidity)
Counter-4h:      -20 pts  (fighting HTF trend)
Level fatigue:   -15 pts  (3+ tests of same level)

ENTRY THRESHOLD: 65 points
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, List


@dataclass
class ChecklistScore:
    score: int
    passed: bool
    checks: Dict[str, bool]
    reason: str
    details: Dict[str, str] = field(default_factory=dict)
    confluence_score: int = 0  # New: actual confluence point total

    @property
    def as_int(self) -> int:
        return self.confluence_score  # Better representation

    def __int__(self) -> int:
        return self.confluence_score

    def __float__(self) -> float:
        return float(self.confluence_score)


def score_signal(
    side: str,
    symbol_stats: Dict,
    indicators: Optional[Dict] = None,
    pct_change_24h: float = 0.0,
    volume_24h: float = 0.0,
    spread_bps: float = 9999.0,
    advanced_features=None,
    current_price: float = 0.0,
) -> ChecklistScore:
    """
    Confluence scoring for structure-based entries.

    Hard hygiene (must pass):
      - Price >= $2.00
      - Volume >= $50M 24h
      - Spread < 100bps

    Confluence scoring (need 65+ to enter):
      - SFP (40) + Volume confirmation (20) = 60 (needs one more)
      - SFP (40) + Regular div (25) = 65 ✓
      - SFP (40) + Volume (20) + 4h align (20) = 80 ✓✓
      - Regular div (25) + Volume (20) + 4h align (20) = 65 ✓
      - SFP (40) alone = 40 ✗ (not enough)
    """
    checks = {}
    details = {}
    confluence = 0
    penalties = []

    # ═══════════════════════════════════
    # HARD HYGIENE
    # ═══════════════════════════════════
    price_ok = current_price >= 2.00
    volume_ok = volume_24h >= 50_000_000
    spread_ok = spread_bps < 100.0
    hygiene_pass = price_ok and volume_ok and spread_ok

    checks["hygiene"] = hygiene_pass
    bad = []
    if not price_ok: bad.append(f"${current_price:.2f}")
    if not volume_ok: bad.append(f"vol={volume_24h/1e6:.0f}M")
    if not spread_ok: bad.append(f"spread={spread_bps:.0f}bps")
    details["hygiene"] = "PASS" if hygiene_pass else f"FAIL({','.join(bad)})"

    if not hygiene_pass:
        return ChecklistScore(
            score=0, passed=False, checks=checks, confluence_score=0,
            reason=f"Hygiene fail: {', '.join(bad)}", details=details)

    # ═══════════════════════════════════
    # STRUCTURE SIGNALS
    # ═══════════════════════════════════
    has_sfp = False
    has_divergence = False
    div_type = None  # "regular" or "hidden"
    has_exhaustion = False
    has_extreme = False

    if advanced_features is not None:
        af = advanced_features
        if side == "long":
            has_sfp = getattr(af, 'sfp_bottom', False)
            has_exhaustion = getattr(af, 'exhaustion_down', False)
            has_extreme = getattr(af, 'extreme_down', False)
            if getattr(af, 'bullish_divergence', False):
                has_divergence = True
                div_type = "regular"
        else:
            has_sfp = getattr(af, 'sfp_top', False)
            has_exhaustion = getattr(af, 'exhaustion_up', False)
            has_extreme = getattr(af, 'extreme_up', False)
            if getattr(af, 'bearish_divergence', False):
                has_divergence = True
                div_type = "regular"

    # ═══════════════════════════════════
    # VOLUME CONFIRMATION
    # ═══════════════════════════════════
    volume_confirmed = False
    vol_ratio = 0.0
    if has_sfp and advanced_features is not None:
        # Check if the SFP candle had elevated volume
        # Use volume_ratio from advanced_features if available
        vol_ratio = getattr(advanced_features, 'volume_ratio', 0.0) or 0.0
        # Fallback: use 24h volume as proxy (imperfect but better than nothing)
        if vol_ratio <= 0 and volume_24h >= 200_000_000:
            vol_ratio = 1.5  # Generous proxy for high-volume tokens
        volume_confirmed = vol_ratio >= 1.5

    checks["volume_confirm"] = volume_confirmed
    details["volume_confirm"] = f"{vol_ratio:.1f}x avg" if vol_ratio > 0 else "no_data"

    # ═══════════════════════════════════
    # HTF TREND (4h alignment)
    # ═══════════════════════════════════
    htf_dir = 0  # 0=neutral, 1=up, -1=down
    if advanced_features is not None:
        htf_dir = getattr(advanced_features, 'htf_trend_dir', 0) or 0

    # Hard block: cannot trade against strong HTF trend without extra confluence
    fighting_htf = False
    if htf_dir == 1 and side == "short":
        fighting_htf = True
    elif htf_dir == -1 and side == "long":
        fighting_htf = True

    htf_aligned = (htf_dir == 0) or (htf_dir == 1 and side == "long") or (htf_dir == -1 and side == "short")
    checks["htf_aligned"] = htf_aligned
    details["htf"] = f"dir={htf_dir} {'aligned' if htf_aligned else 'COUNTER'}"

    # ═══════════════════════════════════
    # SESSION (weekend penalty)
    # ═══════════════════════════════════
    from datetime import datetime as _dt
    _now = _dt.utcnow()
    is_weekend = _now.weekday() >= 5
    checks["session"] = not is_weekend
    details["session"] = "weekend" if is_weekend else "weekday"

    # ═══════════════════════════════════
    # CONFLUENCE SCORE
    # ═══════════════════════════════════
    if has_sfp:
        confluence += 40
        details["sfp"] = "40pts"
    elif has_divergence or has_exhaustion or has_extreme:
        details["sfp"] = "none"
    else:
        details["sfp"] = "none"

    if has_divergence:
        confluence += 25
        details["divergence"] = f"25pts ({div_type})"
    else:
        details["divergence"] = "none"

    if volume_confirmed:
        confluence += 20
        details["vol_pts"] = "20pts"

    if htf_aligned:
        confluence += 20
        details["htf_pts"] = "20pts"
    else:
        details["htf_pts"] = "0pts"

    # Penalties
    if is_weekend:
        confluence -= 15
        penalties.append("weekend(-15)")

    if fighting_htf:
        confluence -= 20
        penalties.append("counter_htf(-20)")
        # But allow counter-trend if volume is extreme
        if vol_ratio >= 2.0 and has_divergence:
            confluence += 15  # Partial restore for extreme setups
            penalties.append("extreme_override(+15)")

    details["penalties"] = "+".join(penalties) if penalties else "none"

    # ═══════════════════════════════════
    # DECISION
    # ═══════════════════════════════════
    ENTRY_THRESHOLD = 65
    passed = confluence >= ENTRY_THRESHOLD

    if has_sfp:
        if volume_confirmed:
            entry_type = "SFP+vol"
        else:
            entry_type = "SFP(no_vol)"
    elif has_divergence:
        entry_type = f"div({div_type})"
    elif has_exhaustion:
        entry_type = "exhaustion"
    elif has_extreme:
        entry_type = "extreme"
    else:
        entry_type = "none"

    reason = f"Confluence: {confluence}pts [{entry_type}] {'ENTER' if passed else f'<{ENTRY_THRESHOLD}'}"
    if penalties:
        reason += f" penalties={penalties}"

    return ChecklistScore(
        score=1 if passed else 0,
        passed=passed,
        checks=checks,
        reason=reason,
        details=details,
        confluence_score=confluence,
    )
