# MAX_OPEN_POSITIONS Fix - Diff Summary

## Changes Made to `app/bot.py`

### 1. Added `cfg` attribute to `ScalperBot.__init__()`

**Location:** Line ~110

**Change:**
```python
# Added after self.logger initialization
self.cfg = config_module
```

This allows accessing config values via `self.cfg.MAX_OPEN_POSITIONS` as requested.

### 2. Fixed `MAX_OPEN_POSITIONS` reference in `[ENTRY_REJECTED]` log

**Location:** Line ~1821

**Before:**
```python
self.logger.warning(
    f"[ENTRY_REJECTED] symbol={symbol} side={signal.side} "
    f"score={signal.final_score:.1f} reason={reason_str} "
    f"current_positions={current_positions}/{MAX_OPEN_POSITIONS}"
)
```

**After:**
```python
max_pos = self.cfg.MAX_OPEN_POSITIONS
self.logger.warning(
    f"[ENTRY_REJECTED] symbol={symbol} side={signal.side} "
    f"score={signal.final_score:.1f} reason={reason_str} "
    f"current_positions={current_positions}/{max_pos}"
)
```

### 3. Fixed `MAX_OPEN_POSITIONS` reference in `[ENTRY_ATTEMPT]` log

**Location:** Line ~1841

**Before:**
```python
self.logger.info(
    f"[ENTRY_ATTEMPT] symbol={symbol} side={signal.side} "
    f"score={signal.final_score:.1f} can_enter={can_enter} "
    f"current_positions={current_positions}/{MAX_OPEN_POSITIONS}"
)
```

**After:**
```python
max_pos = self.cfg.MAX_OPEN_POSITIONS
self.logger.info(
    f"[ENTRY_ATTEMPT] symbol={symbol} side={signal.side} "
    f"score={signal.final_score:.1f} can_enter={can_enter} "
    f"current_positions={current_positions}/{max_pos}"
)
```

## Summary

- ✅ Added `self.cfg = config_module` in `__init__()` to enable config access via `self.cfg`
- ✅ Replaced 2 occurrences of bare `MAX_OPEN_POSITIONS` with `self.cfg.MAX_OPEN_POSITIONS`
- ✅ Used local variable `max_pos` for consistency in `scan_and_enter_signals()`
- ✅ All references now use `self.cfg.MAX_OPEN_POSITIONS` as requested

## Files Modified

- `app/bot.py` - Fixed NameError by properly accessing config via `self.cfg`

