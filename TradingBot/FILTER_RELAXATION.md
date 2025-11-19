# Filter Relaxation - Adjustments Made

**Date**: 2025-11-17  
**Reason**: Filters were too restrictive - 0% pass rate, all signals rejected

---

## Changes Made

### 1. ✅ Hard Minimum Score: 85 → 80
- **Before**: `HARD_MIN_SCORE = 85` (too strict)
- **After**: `HARD_MIN_SCORE = 80` (more reasonable)
- **Impact**: Signals with scores 80-84 will now pass the hard minimum check

### 2. ✅ Percentile Filter: Top 10% → Top 20%
- **Before**: `SIGNAL_PERCENTILE_THRESHOLD = 0.10` (top 10% only)
- **After**: `SIGNAL_PERCENTILE_THRESHOLD = 0.20` (top 20%)
- **Impact**: More signals will pass the percentile filter

### 3. ✅ Trend Alignment: Disabled by Default
- **Before**: `TREND_ALIGNMENT_REQUIRED = True` (required 5m & 15m alignment)
- **After**: `TREND_ALIGNMENT_REQUIRED = False` (disabled by default)
- **Impact**: Trend alignment check is now optional (can be enabled if needed)
- **Note**: This was too restrictive for scalping strategies

### 4. ✅ Correlation Block: 0.85 → 0.90
- **Before**: `CORRELATION_BLOCK_THRESHOLD = 0.85` (block if correlation > 0.85)
- **After**: `CORRELATION_BLOCK_THRESHOLD = 0.90` (block if correlation > 0.90)
- **Impact**: Slightly less restrictive correlation blocking

---

## Expected Impact

### Before (Too Restrictive):
- 0% pass rate
- All signals rejected (even score 86)
- 0 open positions
- Signal flow: 0.5/min

### After (Balanced):
- Higher pass rate (estimated 10-30%)
- Signals 80+ will pass hard minimum
- Top 20% percentile (more reasonable)
- Trend alignment optional (less restrictive)
- Should see some positions opening

---

## Configuration Summary

```bash
HARD_MIN_SCORE=80                    # Lowered from 85
SIGNAL_PERCENTILE_THRESHOLD=0.20     # Increased from 0.10 (top 20%)
TREND_ALIGNMENT_REQUIRED=0           # Disabled (was 1)
CORRELATION_BLOCK_THRESHOLD=0.90     # Relaxed from 0.85
MAX_OPEN_POSITIONS=12                # Unchanged (hard cap)
```

---

## Monitoring

Watch for:
1. **Pass rate** should increase from 0%
2. **Signal flow** should increase
3. **Open positions** should start appearing
4. **Rejection reasons** should show more variety

If still too restrictive, can further adjust:
- Lower `HARD_MIN_SCORE` to 78 or 75
- Increase `SIGNAL_PERCENTILE_THRESHOLD` to 0.25 or 0.30
- Keep `TREND_ALIGNMENT_REQUIRED=0` (disabled)

---

## Status

✅ **Filters relaxed**  
✅ **Ready for testing**  
✅ **Should see improved pass rate**

