<!-- bcd26082-9556-4eb2-8bc0-d1616f90e64f e4d4aa6f-29ea-4bb3-84d1-525f2de69c1d -->
# Tighten Scoring System to Reduce Generosity

## Problem Analysis

The scoring system has several generous components that allow too many signals to pass:

1. **Momentum Score**: Starts at 0.3 for 0% change (flat prices get 30% credit)
2. **RR Score**: Minimum 0.2 even for RR < 0.8 (poor risk-reward still gets points)
3. **Spread Score**: Minimum 0.2 even for wide spreads (poor execution still gets points)
4. **Final Score Formula**: Pure weighted sum allows components to compensate (poor execution can be offset by good setup)
5. **Execution Penalty**: Only applies at < 0.3, so scores 0.3-0.5 escape penalty
6. **Trend Score**: Flat trends get 0.5 (reasonable but could be lower)

## Proposed Changes

### 1. Tighten Momentum Score (`app/signal_scorer.py`)

- Remove base 0.3 floor - start at 0.0 for 0% change
- Require minimum 0.5% change to get any momentum credit
- Stricter mapping: 0.5-1% = 0.2-0.4, 1-2% = 0.4-0.7, 2-4% = 0.7-1.0
- Remove mean reversion credit for small negative changes (was 0.2)

### 2. Tighten RR Score (`app/signal_scorer.py`)

- Remove minimum 0.2 floor for RR < 0.8
- RR < 1.0 → 0.0 (no credit for poor risk-reward)
- RR 1.0-1.5 → 0.2-0.5 (stricter mapping)
- RR 1.5-2.0 → 0.5-0.8 (stricter mapping)
- RR >= 2.0 → 1.0 (unchanged)

### 3. Tighten Spread Score (`app/signal_scorer.py`)

- Remove minimum 0.2 floor for wide spreads
- Spreads at max threshold → 0.0 (hard reject)
- Stricter interpolation: ideal → 1.0, max → 0.0 (no minimum floor)

### 4. Tighten Trend Score (`app/signal_scorer.py`)

- Flat/mixed trends: 0.5 → 0.3 (lower baseline)
- Weak trends: 0.6 → 0.4
- Moderate trends: 0.7 → 0.6
- Strong trends: 0.8 → 0.8 (unchanged)

### 5. Make Final Score Formula Less Forgiving (`app/signal_scorer.py`)

- Add multiplicative penalty for moderate execution issues
- If execution_score < 0.5 (not just < 0.3), apply penalty
- Penalty: `final_score *= (0.5 + execution_score)` for execution 0.3-0.5
- This prevents poor execution from being fully compensated by good setup

### 6. Tighten Volatility Score (`app/signal_scorer.py`)

- Lower minimums: Too low vol → 0.0 (was 0.2), Too high vol → 0.2 (was 0.3)
- Stricter sweet spot: Only 0.7-1.0 ATR range gets high scores

## Expected Impact

**Before (Example):**

- Setup: 0.7, Execution: 0.4, Risk: 0.6
- Final: 100  *(0.6*0.7 + 0.25*0.4 + 0.15*0.6) = 100 * 0.61 = 61.0 ✅ (passes 70 threshold? No, but close)

**After (Same Example):**

- Setup: 0.5 (tighter trend), Execution: 0.4, Risk: 0.5 (tighter RR)
- Base: 100  *(0.6*0.5 + 0.25*0.4 + 0.15*0.5) = 100 * 0.475 = 47.5
- Execution penalty (0.4 < 0.5): 47.5 * (0.5 + 0.4) = 47.5 * 0.9 = 42.75 ❌ (fails)

## Files to Modify

1. `app/signal_scorer.py` - Tighten all component scoring functions
2. `app/config.py` - Consider if thresholds need adjustment (may need to lower if scoring becomes too strict)

## Implementation Notes

- Remove all minimum score floors (0.2, 0.3) that give credit for poor conditions
- Make scoring more punitive: poor components should hurt, not just be neutral
- Keep regime-aware adjustments but make them less lenient overall
- Test that we still get some trades (don't want zero trades)

### To-dos

- [ ] Update config.py: Increase MIN_SIGNAL_SCORE to 58, MIN_SIGNAL_STRENGTH to 0.79, add SIGNAL_PERCENTILE_THRESHOLD and SIGNAL_HISTORY_SIZE
- [ ] Add percentile filtering method to SignalGenerator in signals.py
- [ ] Integrate percentile check into generate_signal() method in signals.py
- [ ] Update regime config thresholds in regime.py proportionally
- [ ] Tighten momentum_score: Remove 0.3 base floor, require 0.5% change minimum, stricter mapping
- [ ] Tighten rr_score: Remove 0.2 minimum, RR < 1.0 → 0.0, stricter mapping for 1.0-2.0
- [ ] Tighten spread_score: Remove 0.2 minimum floor, max spread → 0.0
- [ ] Tighten trend_score: Lower flat/weak trend scores (0.5→0.3, 0.6→0.4)
- [ ] Add execution penalty for moderate issues: Apply penalty if execution < 0.5 (not just < 0.3)
- [ ] Tighten volatility_score: Lower minimums (0.2→0.0, 0.3→0.2)