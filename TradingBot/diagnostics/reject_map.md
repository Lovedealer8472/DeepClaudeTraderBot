# UniRabbit Rejection Gate Map

## Overview
This document maps all rejection paths in the UniRabbit trading bot that can cause signals to be rejected before entry. Each rejection gate is documented with its location, condition, return value, scope, and DRY_RUN/LIVE parity.

---

## Rejection Paths by File

### 1. `app/scanner.py` - Early Rejection (Before Scoring)

#### 1.1 No Replay Ticker
- **File**: `app/scanner.py`
- **Line**: 97
- **Condition**: `ticker_data is None` (in replay mode)
- **Return**: `('skipped', 'no_replay_ticker', None, None, was_cache_hit, False)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same (replay mode only)

#### 1.2 Ticker Timeout
- **File**: `app/scanner.py`
- **Line**: 111
- **Condition**: `asyncio.TimeoutError` during ticker fetch
- **Return**: `('skipped', 'ticker_timeout', None, None, was_cache_hit, False)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

#### 1.3 Ticker Error
- **File**: `app/scanner.py`
- **Line**: 114
- **Condition**: Exception during ticker fetch
- **Return**: `('skipped', 'ticker_error', None, None, was_cache_hit, False)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

#### 1.4 No Stats
- **File**: `app/scanner.py`
- **Line**: 117
- **Condition**: `stats is None`
- **Return**: `('skipped', 'no_stats', None, None, was_cache_hit, False)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

#### 1.5 No Signal Generated
- **File**: `app/scanner.py`
- **Line**: 258
- **Condition**: `signal is None` (signal generator returned None)
- **Return**: `('skipped', 'no_signal', None, None, was_cache_hit, orderbook_fetched)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

#### 1.6 Low Score (Early Filter)
- **File**: `app/scanner.py`
- **Line**: 261
- **Condition**: `signal.final_score < MIN_SIGNAL_SCORE`
- **Return**: `('skipped', 'low_score', None, None, was_cache_hit, orderbook_fetched)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same (MIN_SIGNAL_SCORE = 72)

#### 1.7 Low Strength (Early Filter)
- **File**: `app/scanner.py`
- **Line**: 264
- **Condition**: `signal.strength < MIN_SIGNAL_STRENGTH`
- **Return**: `('skipped', 'low_strength', None, None, was_cache_hit, orderbook_fetched)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same (MIN_SIGNAL_STRENGTH = 0.60)

#### 1.8 Processing Error
- **File**: `app/scanner.py`
- **Line**: 277
- **Condition**: Exception during symbol processing
- **Return**: `('error', symbol, None, None, False, False)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

---

### 2. `app/signals.py` - Signal Generation & Scoring Rejection

#### 2.1 Hard Minimum Score
- **File**: `app/signals.py`
- **Line**: 423
- **Condition**: `score_to_check < HARD_MIN_SCORE` (HARD_MIN_SCORE = 70)
- **Return**: `(best_signal, f"RJ – score below minimum ({score_to_check:.1f} < {HARD_MIN_SCORE})")`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

#### 2.2 Entry Gate Score Threshold
- **File**: `app/signals.py`
- **Line**: 431
- **Condition**: `score_to_check < effective_min_score` (effective_min_score = 70.0)
- **Return**: `(best_signal, f"Score too low ({score_to_check:.1f} < {effective_min_score})")`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same (hardcoded to 70.0)

#### 2.3 Signal Strength Too Low
- **File**: `app/signals.py`
- **Line**: 438
- **Condition**: `best_signal.strength < min_strength` (min_strength from config/regime, default MIN_SIGNAL_STRENGTH = 0.60)
- **Return**: `(best_signal, f"Strength too low ({best_signal.strength:.3f} < {min_strength})")`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

---

### 3. `app/engine/scalper_filters.py` - Three-Stage Filter Pipeline

#### 3.1 Stage 1: Microstructure Fail
- **File**: `app/engine/scalper_filters.py`
- **Line**: 96
- **Condition**: `spread_pct > 0.0025` (0.25%) OR `depth_top_usd < 800.0` (if orderbook available)
- **Return**: `FilterResult(passed=False, reason=f"microstructure_fail: {reason_detail}", ...)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same
- **Note**: Missing orderbook data is NOT a rejection (passes by default)

#### 3.2 Stage 2: Structure Fail (Conflicting Signals)
- **File**: `app/engine/scalper_filters.py`
- **Line**: 255
- **Condition**: 
  - Long side: `has_bearish and not has_bullish` (conflicting bearish signals)
  - Short side: `has_bullish and not has_bearish` (conflicting bullish signals)
- **Return**: `FilterResult(passed=False, reason=f"structure_fail: conflicting_signals (side={side}, ...)", ...)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same
- **Note**: Missing structure data is NOT a rejection (passes by default)

#### 3.3 Stage 3: Direction Fail
- **File**: `app/engine/scalper_filters.py`
- **Line**: 400
- **Condition**: `direction_score < 0.45` (when indicators available)
- **Return**: `FilterResult(passed=False, reason=f"direction_fail: score_too_low ({direction_score:.3f} < {min_direction_score})", ...)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same
- **Note**: Missing indicators is NOT a rejection (passes by default with neutral score)

---

### 4. `app/position_manager.py` - Entry Validation & Risk Gates

#### 4.1 Max Positions Reached (Hard Cap)
- **File**: `app/position_manager.py`
- **Line**: 324
- **Condition**: `current_positions >= MAX_OPEN_POSITIONS` (MAX_OPEN_POSITIONS = 5)
- **Return**: `(False, "RJ – max positions reached", None)`
- **Scope**: Portfolio-based (global)
- **DRY_RUN/LIVE Parity**: Same
- **Note**: No unicorn bypass for hard cap

#### 4.2 Loss Streak Pause
- **File**: `app/position_manager.py`
- **Line**: 336
- **Condition**: `not adj_ok` from `loss_streak_adjustments()` when `loss_streak_state == "pause"` and `now_ts < loss_streak_unlock_ts`
- **Return**: `(False, pause_reason or "loss_streak_pause", None)`
- **Scope**: Portfolio-based (global)
- **DRY_RUN/LIVE Parity**: Same
- **Note**: 
  - Pause triggered at `loss_streak >= LOSS_STREAK_HARD_LEVEL` (7 losses)
  - Pause duration: `LOSS_STREAK_PAUSE_SEC` (900 seconds = 15 minutes)
  - Unicorn bypass: `score >= HIGH_SCORE_BYPASS` (90.0) bypasses pause

#### 4.3 Min Score Guard (Loss Streak Adjusted)
- **File**: `app/position_manager.py`
- **Line**: 342
- **Condition**: `signal_score < effective_min` where `effective_min = MIN_SIGNAL_SCORE + score_bonus`
- **Return**: `(False, f"min_score_guard:{signal_score:.1f}<{effective_min:.1f}", None)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same
- **Note**: `score_bonus` from loss streak adjustments (5.0 in defense/pause states)

#### 4.4 DD-Aware Loss Streak Block
- **File**: `app/position_manager.py`
- **Line**: 351
- **Condition**: `dd_block == True` from `check_dd_aware_loss_streak_block()` when:
  - `loss_streak >= DD_AWARE_STREAK_THRESHOLD` (3) AND
  - `drawdown_pct <= -DD_AWARE_DD_THRESHOLD` (-5.0%)
- **Return**: `(False, dd_reason, None)`
- **Scope**: Portfolio-based (global)
- **DRY_RUN/LIVE Parity**: Same
- **Note**: Only active if `DD_AWARE_LOSS_STREAK_ENABLED = True` (default: False)

#### 4.5 Risk Budget Exhausted (No Replacement)
- **File**: `app/position_manager.py`
- **Line**: 380
- **Condition**: 
  - `current_total_risk >= TOTAL_RISK_BUDGET` (2.0%) AND
  - `SCORE_AWARE_REPLACEMENT_ENABLED = False` OR no replacement candidate found
- **Return**: `(False, f"Risk budget reached: {current_total_risk*100:.2f}% >= {TOTAL_RISK_BUDGET*100:.2f}%", None)`
- **Scope**: Portfolio-based (global)
- **DRY_RUN/LIVE Parity**: Same

#### 4.6 Risk Budget Exhausted (Replacement Failed)
- **File**: `app/position_manager.py`
- **Line**: 385
- **Condition**: 
  - `current_total_risk >= TOTAL_RISK_BUDGET` AND
  - `SCORE_AWARE_REPLACEMENT_ENABLED = True` BUT
  - `signal_score < weakest_score + SCORE_REPLACEMENT_MARGIN` (5.0)
- **Return**: `(False, f"Risk budget reached: {current_total_risk*100:.2f}% >= {TOTAL_RISK_BUDGET*100:.2f}%", None)`
- **Scope**: Portfolio-based (global)
- **DRY_RUN/LIVE Parity**: Same

#### 4.7 Max Positions Reached (Soft Cap, No Replacement)
- **File**: `app/position_manager.py`
- **Line**: 410
- **Condition**: 
  - `current_positions >= max_pos` (dynamic limit) AND
  - `SCORE_AWARE_REPLACEMENT_ENABLED = False` OR no replacement candidate
- **Return**: `(False, f"Max positions reached ({max_pos})", None)`
- **Scope**: Portfolio-based (global)
- **DRY_RUN/LIVE Parity**: Same

#### 4.8 Max Positions Reached (Replacement Failed)
- **File**: `app/position_manager.py`
- **Line**: 412
- **Condition**: 
  - `current_positions >= max_pos` AND
  - `SCORE_AWARE_REPLACEMENT_ENABLED = True` BUT
  - `signal_score < weakest_score + SCORE_REPLACEMENT_MARGIN` (5.0)
- **Return**: `(False, f"Max positions reached ({max_pos})", None)`
- **Scope**: Portfolio-based (global)
- **DRY_RUN/LIVE Parity**: Same

#### 4.9 Signal Strength Too Low
- **File**: `app/position_manager.py`
- **Line**: 416
- **Condition**: `signal_strength < MIN_SIGNAL_STRENGTH` (0.60)
- **Return**: `(False, f"Signal strength too low ({signal_strength:.2f} < {MIN_SIGNAL_STRENGTH})", None)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

#### 4.10 Spread Too Wide
- **File**: `app/position_manager.py`
- **Line**: 422
- **Condition**: `spread_bps > MAX_SPREAD_BPS` (100 bps)
- **Return**: `(False, f"Spread too wide ({spread_bps:.1f}bps > {MAX_SPREAD_BPS}bps)", None)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

#### 4.11 Volume Too Low
- **File**: `app/position_manager.py`
- **Line**: 438
- **Condition**: `volume_24h < MIN_VOLUME_24H` (1,000,000 USDT)
- **Return**: `(False, f"Volume too low (${volume_m:.2f}M < ${min_volume_m:.0f}M)", None)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

#### 4.12 Latency Too High
- **File**: `app/position_manager.py`
- **Line**: 442
- **Condition**: `latency_ms > MAX_LATENCY_MS` (50 ms)
- **Return**: `(False, f"Latency too high ({latency_ms:.0f}ms > {MAX_LATENCY_MS}ms)", None)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

#### 4.13 Cooldown Active
- **File**: `app/position_manager.py`
- **Line**: 449
- **Condition**: `cooldown_reason is not None` from `_check_cooldown()`:
  - Same symbol cooldown: `now < cooldown_until[symbol]` (COOLDOWN_SEC = 30s base, adaptive)
  - Same symbol time check: `time_since < COOLDOWN_SAME_SYMBOL` (300s = 5 min)
- **Return**: `(False, cooldown_reason, None)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same
- **Note**: Unicorn bypass: `UNICORN_BYPASS_COOLDOWN = True` bypasses cooldown

#### 4.14 Symbol Churn Pause (After Micro Time Exit)
- **File**: `app/position_manager.py`
- **Line**: 301
- **Condition**: `is_symbol_churn_paused(symbol, now, SYMBOL_CHURN_COOLDOWN_SEC)` returns `True`:
  - Symbol previously exited by `time_exit` with `|profit_atr| <= 0.25`
  - Cooldown period: `SYMBOL_CHURN_COOLDOWN_SEC` (900 seconds = 15 minutes)
- **Return**: `(False, f"churn_pause_after_time_exit (symbol={symbol} cooloff={SYMBOL_CHURN_COOLDOWN_SEC}s)", None)`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same
- **Note**: Prevents repeated micro-cuts on symbols that are not working. Triggered when a symbol exits via time_exit with flat/small profit (|R| <= 0.25).

#### 4.15 Rate Limit Exceeded
- **File**: `app/position_manager.py`
- **Line**: 456
- **Condition**: `rate_limit_reason is not None` from `_check_rate_limit()` when `len(entry_times) >= MAX_ENTRIES_PER_MIN` (10 entries/min)
- **Return**: `(False, rate_limit_reason, None)`
- **Scope**: Portfolio-based (global)
- **DRY_RUN/LIVE Parity**: Same
- **Note**: Unicorn bypass: `UNICORN_BYPASS_RATE_LIMIT = True` bypasses rate limit

---

### 5. `app/position_manager.py` - Position Sizing Rejection

#### 5.1 Invalid Inputs
- **File**: `app/position_manager.py`
- **Line**: 96
- **Condition**: `entry_price <= 0 or stop_loss_price <= 0 or equity <= 0`
- **Return**: `(0.0, 0.0, "invalid_inputs")`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

#### 5.2 Invalid Stop Loss (Long)
- **File**: `app/position_manager.py`
- **Line**: 101
- **Condition**: `entry_price <= stop_loss_price` (long position)
- **Return**: `(0.0, 0.0, "invalid_stop_loss")`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

#### 5.3 Invalid Stop Loss (Short)
- **File**: `app/position_manager.py`
- **Line**: 105
- **Condition**: `stop_loss_price <= entry_price` (short position)
- **Return**: `(0.0, 0.0, "invalid_stop_loss")`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

#### 5.4 Invalid Stop Distance
- **File**: `app/position_manager.py`
- **Line**: 109
- **Condition**: `price_risk <= 0`
- **Return**: `(0.0, 0.0, "invalid_stop_distance")`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

#### 5.5 Stop Too Tight (LIVE Mode Only)
- **File**: `app/position_manager.py`
- **Line**: 145
- **Condition**: `stop_distance_pct < MIN_STOP_DISTANCE_PCT` (0.5%) AND `DRY_RUN = False`
- **Return**: `(0.0, 0.0, "stop_too_tight")`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: **DIFFERENT** - DRY_RUN allows minimal size, LIVE rejects
- **Note**: DRY_RUN mode uses minimal size instead of rejecting (line 116-142)

#### 5.6 Risk Budget Exhausted (Sizing)
- **File**: `app/position_manager.py`
- **Line**: 156
- **Condition**: `remaining_risk <= MIN_RISK_PER_TRADE` (0.2%)
- **Return**: `(0.0, 0.0, "risk_budget_exhausted")`
- **Scope**: Portfolio-based (global)
- **DRY_RUN/LIVE Parity**: Same

#### 5.7 Size Too Small
- **File**: `app/position_manager.py`
- **Line**: 212
- **Condition**: `base_size <= 0`
- **Return**: `(0.0, 0.0, "size_too_small")`
- **Scope**: Symbol-based
- **DRY_RUN/LIVE Parity**: Same

---

### 6. `app/core/risk.py` - Risk Manager (Not Currently Used)

**Note**: `RiskManager` class exists but is not actively used in the entry pipeline. PositionManager handles risk checks directly.

---

## Summary: Which Function Returns pass=0 for All Signals?

The function that ultimately returns `pass=0` for all signals is:

**`app/position_manager.py::can_enter_position()`** (line 255)

This is the final gate before entry. It checks:
1. Max positions (hard cap)
2. Loss streak pause state
3. Min score guard (with loss streak bonus)
4. DD-aware loss streak block
5. Risk budget
6. Max positions (soft cap with replacement)
7. Signal strength
8. Spread
9. Volume
10. Latency
11. Cooldown
12. Rate limit

If any of these checks fail, the signal is rejected and `can_enter_position()` returns `(False, reason, None)`.

---

## Cooldown Status: Is Cooldown Chronically Activated?

### Cooldown Types:

1. **Same Symbol Cooldown** (`COOLDOWN_SAME_SYMBOL = 300s = 5 min`)
   - Set on entry: `cooldown_until[symbol] = now + COOLDOWN_SEC` (30s base, adaptive)
   - Also checked: `time_since < COOLDOWN_SAME_SYMBOL` from `last_entry_by_symbol[symbol]`

2. **Exit-to-Entry Cooldown** (`COOLDOWN_AFTER_EXIT = 120s = 2 min`)
   - Set on exit: `cooldown_until[symbol] = now + COOLDOWN_AFTER_EXIT`
   - Applied to the symbol that just exited

3. **Loss Streak Pause** (`LOSS_STREAK_PAUSE_SEC = 900s = 15 min`)
   - Triggered when `loss_streak >= LOSS_STREAK_HARD_LEVEL` (7 losses)
   - Global pause: `loss_streak_unlock_ts = now + LOSS_STREAK_PAUSE_SEC`
   - Blocks ALL entries until pause expires

### Potential Chronic Activation:

**YES** - If the bot has:
- Recent losses (loss_streak >= 7) → 15-minute pause
- Recent exits → 2-minute cooldown per symbol
- Recent entries → 5-minute cooldown per symbol

**Check**: Look for logs containing:
- `"loss_streak_pause_*s"` (pause active)
- `"Cooldown active (*s remaining)"` (symbol cooldown)
- `"Same symbol cooldown (*s remaining)"` (5-min cooldown)

---

## DRY_RUN vs LIVE Parity Summary

| Rejection Gate | DRY_RUN | LIVE | Parity |
|----------------|---------|------|--------|
| Stop Too Tight | Allows minimal size | Rejects | **DIFFERENT** |
| All Other Gates | Same thresholds | Same thresholds | **SAME** |

**Key Finding**: Only `stop_too_tight` has different behavior. All other rejection gates use identical thresholds and logic in both DRY_RUN and LIVE modes.

---

## Rejection Flow Diagram

```
Signal Generated (scanner.py)
  ↓
Early Filters (scanner.py)
  ├─ No ticker → skip
  ├─ No signal → skip
  ├─ Low score (< 72) → skip
  └─ Low strength (< 0.60) → skip
  ↓
Signal Scoring (signals.py)
  ├─ Hard min score (< 70) → reject
  ├─ Entry gate (< 70) → reject
  └─ Strength check (< 0.60) → reject
  ↓
Three-Stage Filter (scalper_filters.py)
  ├─ Stage 1: Microstructure (spread/depth) → reject
  ├─ Stage 2: Structure (conflicting signals) → reject
  └─ Stage 3: Direction (score < 0.45) → reject
  ↓
Position Manager Validation (position_manager.py)
  ├─ Max positions (hard cap) → reject
  ├─ Loss streak pause → reject
  ├─ Min score guard → reject
  ├─ DD-aware block → reject
  ├─ Risk budget → reject
  ├─ Max positions (soft cap) → reject
  ├─ Signal strength → reject
  ├─ Spread → reject
  ├─ Volume → reject
  ├─ Latency → reject
  ├─ Cooldown → reject
  └─ Rate limit → reject
  ↓
Position Sizing (position_manager.py)
  ├─ Invalid inputs → reject
  ├─ Invalid stop loss → reject
  ├─ Stop too tight (LIVE only) → reject
  ├─ Risk budget exhausted → reject
  └─ Size too small → reject
  ↓
Entry Attempt (order_manager.py)
```

---

## Configuration Thresholds Reference

| Threshold | Value | Config Key | File |
|-----------|-------|------------|------|
| Hard Min Score | 70 | `HARD_MIN_SCORE` | `config.py:149` |
| Entry Gate Score | 70.0 | Hardcoded | `signals.py:430` |
| Min Signal Score | 72 | `MIN_SIGNAL_SCORE` | `config.py:148` |
| Min Signal Strength | 0.60 | `MIN_SIGNAL_STRENGTH` | `config.py:146` |
| Max Open Positions | 5 | `MAX_OPEN_POSITIONS` | `config.py:94` |
| Loss Streak Hard Level | 7 | `LOSS_STREAK_HARD_LEVEL` | `config.py:320` |
| Loss Streak Pause Duration | 900s | `LOSS_STREAK_PAUSE_SEC` | `config.py:321` |
| High Score Bypass | 90.0 | `HIGH_SCORE_BYPASS` | `config.py:323` |
| Total Risk Budget | 2.0% | `TOTAL_RISK_BUDGET` | `config.py:93` |
| Min Risk Per Trade | 0.2% | `MIN_RISK_PER_TRADE` | `config.py:100` |
| Max Risk Per Trade | 1.0% | `MAX_RISK_PER_TRADE` | `config.py:101` |
| Min Stop Distance | 0.5% | `MIN_STOP_DISTANCE_PCT` | `config.py:231` |
| Max Spread | 100 bps | `MAX_SPREAD_BPS` | `config.py:143` |
| Min Volume 24h | 1M USDT | `MIN_VOLUME_24H` | `config.py:144` |
| Max Latency | 50 ms | `MAX_LATENCY_MS` | `config.py:145` |
| Cooldown Base | 30s | `COOLDOWN_SEC` | `config.py:134` |
| Cooldown Same Symbol | 300s | `COOLDOWN_SAME_SYMBOL` | `config.py:135` |
| Cooldown After Exit | 120s | `COOLDOWN_AFTER_EXIT` | `config.py:136` |
| Max Entries Per Min | 10 | `MAX_ENTRIES_PER_MIN` | `config.py:139` |

---

## Diagnostic Checklist

To diagnose 0% pass rate, check:

1. **Loss Streak State**: Check `loss_streak` and `loss_streak_state` in PositionManager
   - If `loss_streak >= 7` → pause active for 15 minutes
   - Check logs for `"loss_streak_pause_*s"`

2. **Cooldown Status**: Check `cooldown_until` dict in PositionManager
   - Look for `"Cooldown active (*s remaining)"` in logs

3. **Max Positions**: Check `current_positions >= MAX_OPEN_POSITIONS` (5)
   - Look for `"RJ – max positions reached"` in logs

4. **Score Thresholds**: Check signal scores vs thresholds
   - Entry gate: 70.0 (hardcoded in signals.py)
   - Min score guard: 72 + loss_streak_bonus (5.0 in defense/pause)

5. **Risk Budget**: Check `current_total_risk >= TOTAL_RISK_BUDGET` (2.0%)
   - Look for `"Risk budget reached"` in logs

6. **Filter Rejections**: Check three-stage filter results
   - Look for `"microstructure_fail"`, `"structure_fail"`, `"direction_fail"` in logs

7. **Early Rejections**: Check scanner.py early filters
   - Look for `"no_signal"`, `"low_score"`, `"low_strength"` in logs

