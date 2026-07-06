"""
Confluence Scoring Model — dual-tier entry gating.

Tier A: Structure-based (large caps, OHLCV available, 65pt threshold)
Tier B: Momentum/scalp (low/mid caps, ticker-only, 45pt threshold)

Research: 100% rejection rate was caused by scoring low-cap momentum tokens
with a large-cap structure checklist. Tier B unblocks entries on tokens that
actually move, while Tier A preserves the proven structure model for deep-data
tokens.
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
    confluence_score: int = 0
    tier: str = "none"  # "A" (structure), "B" (momentum), or "none" (rejected)

    @property
    def as_int(self) -> int:
        return self.confluence_score

    def __int__(self) -> int:
        return self.confluence_score

    def __float__(self) -> float:
        return float(self.confluence_score)


# ── Tier A: Structure (large caps, OHLCV available) ──────────────────────
TIER_A_MIN_VOL = 20_000_000   # $20M 24h (was $50M)
TIER_A_MIN_PRICE = 1.00       # $1 minimum (was $2)
TIER_A_MAX_SPREAD = 40        # bps
TIER_A_THRESHOLD = 65

# ── Tier B: Momentum/scalp (low/mid caps, ticker-only) ───────────────────
TIER_B_MIN_VOL = 2_000_000    # $2M 24h
TIER_B_MIN_PRICE = 0.001      # micro-cap OK if liquid
TIER_B_MAX_SPREAD = 60        # bps — tighter than old 100 for $250 account
TIER_B_THRESHOLD = 45


def _fail(checks, details, reason):
    return ChecklistScore(
        score=0, passed=False, checks=checks, confluence_score=0,
        reason=reason, details=details, tier="none")


def score_signal(
    side: str,
    symbol_stats: Dict,
    indicators: Optional[Dict] = None,
    pct_change_24h: float = 0.0,
    volume_24h: float = 0.0,
    spread_bps: float = 9999.0,
    advanced_features=None,
    current_price: float = 0.0,
    news_modifier: int = 0,
    signal_type: str = "unknown",
) -> ChecklistScore:
    """
    Dual-tier confluence scoring.

    Tier A: Structure-based for large caps with OHLCV data (65pt threshold).
    Tier B: Ticker-only momentum/scalp for low/mid caps (45pt threshold).

    Routing: if volume >= $20M AND advanced_features available → Tier A,
             else if volume >= $2M and spread ok → Tier B,
             else rejected.
    """
    checks = {}
    details = {}

    # ── Route to tier ──────────────────────────────────────────────────
    has_structure = advanced_features is not None
    tier_a_eligible = volume_24h >= TIER_A_MIN_VOL and has_structure
    tier_b_eligible = (
        volume_24h >= TIER_B_MIN_VOL
        and current_price >= TIER_B_MIN_PRICE
        and spread_bps < TIER_B_MAX_SPREAD
    )

    if not tier_a_eligible and not tier_b_eligible:
        bad = []
        if volume_24h < TIER_B_MIN_VOL: bad.append(f"vol={volume_24h/1e6:.0f}M")
        if current_price < TIER_B_MIN_PRICE: bad.append(f"${current_price:.4f}")
        if spread_bps >= TIER_B_MAX_SPREAD: bad.append(f"spread={spread_bps:.0f}bps")
        return _fail(checks, details, f"Hygiene fail: {', '.join(bad)}")

    # ── Tier A: Structure path ──────────────────────────────────────────
    if tier_a_eligible:
        return _score_tier_a(
            side, volume_24h, spread_bps, current_price,
            advanced_features, news_modifier, checks, details)

    # ── Tier B: Momentum/scalp path ─────────────────────────────────────
    return _score_tier_b(
        side, pct_change_24h, volume_24h, spread_bps, current_price,
        signal_type, news_modifier, checks, details)


def _score_tier_a(
    side, volume_24h, spread_bps, current_price,
    advanced_features, news_modifier, checks, details,
) -> ChecklistScore:
    """Structure-based scoring — original 65pt model with relaxed hygiene."""

    # Hygiene
    price_ok = current_price >= TIER_A_MIN_PRICE
    vol_ok = volume_24h >= TIER_A_MIN_VOL
    spread_ok = spread_bps < TIER_A_MAX_SPREAD
    hygiene_pass = price_ok and vol_ok and spread_ok

    checks["hygiene"] = hygiene_pass
    bad = []
    if not price_ok: bad.append(f"${current_price:.2f}")
    if not vol_ok: bad.append(f"vol={volume_24h/1e6:.0f}M")
    if not spread_ok: bad.append(f"spread={spread_bps:.0f}bps")
    details["hygiene"] = "PASS" if hygiene_pass else f"FAIL({','.join(bad)})"

    if not hygiene_pass:
        return _fail(checks, details, f"Hygiene fail: {', '.join(bad)}")

    # Structure signals
    has_sfp = False
    has_divergence = False
    div_type = None
    has_exhaustion = False
    has_extreme = False

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

    # Volume confirmation
    volume_confirmed = False
    vol_ratio = 0.0
    if has_sfp:
        vol_ratio = getattr(af, 'volume_ratio', 0.0) or 0.0
        if vol_ratio <= 0 and volume_24h >= 100_000_000:
            vol_ratio = 1.5
        volume_confirmed = vol_ratio >= 1.5

    checks["volume_confirm"] = volume_confirmed
    details["volume_confirm"] = f"{vol_ratio:.1f}x avg" if vol_ratio > 0 else "no_data"

    # HTF trend
    htf_dir = getattr(af, 'htf_trend_dir', 0) or 0
    fighting_htf = (htf_dir == 1 and side == "short") or (htf_dir == -1 and side == "long")
    htf_aligned = (htf_dir == 0) or (htf_dir == 1 and side == "long") or (htf_dir == -1 and side == "short")
    checks["htf_aligned"] = htf_aligned
    details["htf"] = f"dir={htf_dir} {'aligned' if htf_aligned else 'COUNTER'}"

    # Session
    from datetime import datetime as _dt
    is_weekend = _dt.utcnow().weekday() >= 5
    checks["session"] = not is_weekend
    details["session"] = "weekend" if is_weekend else "weekday"

    # Confluence
    confluence = 0
    penalties = []
    if has_sfp: confluence += 40; details["sfp"] = "40pts"
    else: details["sfp"] = "none"
    if has_divergence: confluence += 25; details["divergence"] = f"25pts ({div_type})"
    else: details["divergence"] = "none"
    if volume_confirmed: confluence += 20; details["vol_pts"] = "20pts"
    if htf_aligned: confluence += 20; details["htf_pts"] = "20pts"
    else: details["htf_pts"] = "0pts"

    if is_weekend: confluence -= 5; penalties.append("weekend(-5)")
    if fighting_htf:
        confluence -= 20; penalties.append("counter_htf(-20)")
        if vol_ratio >= 2.0 and has_divergence:
            confluence += 15; penalties.append("extreme_override(+15)")
    if news_modifier != 0:
        confluence += news_modifier; penalties.append(f"news({news_modifier:+d})")

    details["penalties"] = "+".join(penalties) if penalties else "none"

    passed = confluence >= TIER_A_THRESHOLD

    if has_sfp: entry_type = "SFP+vol" if volume_confirmed else "SFP(no_vol)"
    elif has_divergence: entry_type = f"div({div_type})"
    elif has_exhaustion: entry_type = "exhaustion"
    elif has_extreme: entry_type = "extreme"
    else: entry_type = "none"

    reason = f"[TierA] Confluence: {confluence}pts [{entry_type}] {'ENTER' if passed else f'<{TIER_A_THRESHOLD}'}"
    if penalties: reason += f" penalties={penalties}"

    return ChecklistScore(
        score=1 if passed else 0, passed=passed, checks=checks,
        reason=reason, details=details, confluence_score=confluence, tier="A")


def _score_tier_b(
    side, pct_change_24h, volume_24h, spread_bps, current_price,
    signal_type, news_modifier, checks, details,
) -> ChecklistScore:
    """Ticker-only momentum/scalp scoring — no structure data required."""

    from datetime import datetime as _dt
    is_weekend = _dt.utcnow().weekday() >= 5

    # Hygiene (must all pass)
    vol_ok = volume_24h >= TIER_B_MIN_VOL
    spread_ok = spread_bps < TIER_B_MAX_SPREAD
    price_ok = current_price >= TIER_B_MIN_PRICE
    hygiene_pass = vol_ok and spread_ok and price_ok

    checks["hygiene"] = hygiene_pass
    bad = []
    if not vol_ok: bad.append(f"vol={volume_24h/1e6:.0f}M")
    if not spread_ok: bad.append(f"spread={spread_bps:.0f}bps")
    if not price_ok: bad.append(f"${current_price:.4f}")
    details["hygiene"] = "PASS" if hygiene_pass else f"FAIL({','.join(bad)})"

    if not hygiene_pass:
        return _fail(checks, details, f"Hygiene fail: {', '.join(bad)}")

    # Confluence scoring (need TIER_B_THRESHOLD=45 to enter)
    pts = 0
    modifiers = []

    # 1. Price move strength
    abs_pct = abs(pct_change_24h)
    if abs_pct >= 15:       pts += 25; modifiers.append("big_move(+25)")
    elif abs_pct >= 8:      pts += 20; modifiers.append("strong(+20)")
    elif abs_pct >= 4:      pts += 12; modifiers.append("moderate(+12)")
    elif abs_pct >= 1.5:    pts += 6;  modifiers.append("mild(+6)")

    # 2. Volume bonus
    if volume_24h >= 10_000_000:    pts += 15; modifiers.append("vol10M(+15)")
    elif volume_24h >= 5_000_000:   pts += 10; modifiers.append("vol5M(+10)")
    elif volume_24h >= 2_000_000:   pts += 5;  modifiers.append("vol2M(+5)")

    # 3. Spread quality
    if spread_bps <= 20:        pts += 12; modifiers.append("tight(+12)")
    elif spread_bps <= 35:      pts += 8;  modifiers.append("good(+8)")
    elif spread_bps <= 50:      pts += 4;  modifiers.append("ok(+4)")

    # 4. Signal type bonus
    if signal_type == "momentum":
        pts += 8; modifiers.append("momentum(+8)")
    elif signal_type == "mean_reversion" and abs_pct >= 8:
        pts += 6; modifiers.append("reversal(+6)")

    # 5. Direction alignment with move
    move_up = pct_change_24h > 0
    if side == "long" and move_up:      pts += 5; modifiers.append("with_trend(+5)")
    elif side == "short" and not move_up: pts += 5; modifiers.append("with_trend(+5)")

    checks["session"] = not is_weekend
    details["session"] = "weekend" if is_weekend else "weekday"

    # Penalties
    if is_weekend: pts -= 5; modifiers.append("weekend(-5)")
    # Inverse: shorting +60% runners is suicide
    if side == "short" and pct_change_24h > 25:  pts -= 20; modifiers.append("parabolic(-20)")
    if side == "long" and pct_change_24h < -25:   pts -= 20; modifiers.append("crater(-20)")
    if news_modifier != 0: pts += news_modifier; modifiers.append(f"news({news_modifier:+d})")

    details["modifiers"] = "+".join(modifiers) if modifiers else "none"

    passed = pts >= TIER_B_THRESHOLD
    reason = (
        f"[TierB] Confluence: {pts}pts "
        f"pct={pct_change_24h:+.1f}% vol={volume_24h/1e6:.1f}M "
        f"spread={spread_bps:.0f}bps "
        f"{'ENTER' if passed else f'<{TIER_B_THRESHOLD}'}"
    )

    return ChecklistScore(
        score=1 if passed else 0, passed=passed, checks=checks,
        reason=reason, details=details, confluence_score=pts, tier="B")
