# 🦄 Unicorn Found Priority Protocol

## Overview

The **Unicorn Found Priority Protocol** is a special trading mode that activates when an exceptionally high-quality signal (score ≥ 90) is detected. This protocol bypasses normal restrictions and applies enhanced risk-adjusted position sizing to capitalize on these rare, high-probability opportunities.

---

## Activation Criteria

A signal is classified as a **unicorn** when:
- **Signal Score**: `≥ 90` (out of 100)
- **Protocol Enabled**: `UNICORN_PROTOCOL_ENABLED = True`

These signals represent the top 1-2% of all generated signals and have:
- Perfect setup conditions (trend, momentum, volatility)
- Flawless execution parameters (spread, depth, latency)
- Excellent risk-reward characteristics (R:R ≥ 2.0)

---

## Protocol Features

### 1. **Bypass Restrictions** ✅

When a unicorn signal is detected, the following restrictions are bypassed:

#### **Cooldowns** (`UNICORN_BYPASS_COOLDOWN = True`)
- **Same Symbol Cooldown**: Bypassed (normally 5 minutes)
- **Exit-to-Entry Cooldown**: Bypassed (normally 1 minute)
- **Global Cooldown**: Bypassed (normally 10 seconds)

**Rationale**: Unicorn signals are rare and time-sensitive. Missing one due to cooldown would be a significant opportunity cost.

#### **Max Positions** (`UNICORN_BYPASS_MAX_POSITIONS = True`)
- **Extra Slots**: `UNICORN_EXTRA_POSITION_SLOTS = 2`
- Allows 1-2 additional positions beyond normal max

**Example**: If `MAX_CONCURRENT_POS = 10`, unicorns can fill positions 11-12.

**Rationale**: Unicorn signals are so high-quality that they justify temporarily exceeding position limits.

#### **Rate Limits** (`UNICORN_BYPASS_RATE_LIMIT = True`)
- Bypasses `MAX_ENTRIES_PER_MIN` restriction
- Allows immediate entry without waiting

**Rationale**: Unicorn signals should be entered immediately, not delayed by rate limiting.

#### **Loss Streak Protection** (`UNICORN_BYPASS_LOSS_STREAK = True`)
- Bypasses loss streak blocking (normally blocks after 3+ losses)
- Allows entry even during loss streaks

**Rationale**: Unicorn signals are high-probability opportunities that should be taken regardless of recent performance.

#### **Correlation Penalties** (`UNICORN_BYPASS_CORRELATION = True`)
- Allows correlated positions (same direction as existing positions)
- Bypasses correlation size reduction

**Rationale**: If multiple unicorn signals align, they should all be taken.

---

### 2. **Enhanced Position Sizing** 📈

#### **Position Size Multiplier** (`UNICORN_POSITION_SIZE_MULTIPLIER = 1.5`)
- **Default**: 1.5x (50% increase)
- Position size is increased by 50% for unicorn signals

**Example**: 
- Normal signal: $1,000 position
- Unicorn signal: $1,500 position (50% larger)

**Rationale**: Higher conviction signals deserve larger positions.

#### **Leverage Multiplier** (`UNICORN_LEVERAGE_MULTIPLIER = 1.2`)
- **Default**: 1.2x (20% increase)
- Leverage is increased by 20% (capped at `MAX_LEVERAGE`)

**Example**:
- Normal signal: 5x leverage
- Unicorn signal: 6x leverage (20% increase)

**Rationale**: Higher-quality signals can safely use more leverage.

---

## Configuration

All unicorn protocol settings are in `app/config.py`:

```python
# Unicorn Found Priority Protocol
UNICORN_SCORE_THRESHOLD = 90  # Score threshold for unicorn signals (≥ 90)
UNICORN_PROTOCOL_ENABLED = True  # Enable unicorn priority protocol
UNICORN_POSITION_SIZE_MULTIPLIER = 1.5  # Increase position size by 50%
UNICORN_LEVERAGE_MULTIPLIER = 1.2  # Increase leverage by 20%
UNICORN_BYPASS_COOLDOWN = True  # Bypass cooldowns for unicorns
UNICORN_BYPASS_MAX_POSITIONS = True  # Allow 1-2 extra positions
UNICORN_EXTRA_POSITION_SLOTS = 2  # Number of extra position slots
UNICORN_BYPASS_RATE_LIMIT = True  # Bypass rate limits
UNICORN_BYPASS_LOSS_STREAK = True  # Bypass loss streak protection
UNICORN_BYPASS_CORRELATION = True  # Allow correlated positions
```

### Environment Variables

All settings can be overridden via environment variables:

```bash
UNICORN_SCORE_THRESHOLD=90
UNICORN_PROTOCOL_ENABLED=1
UNICORN_POSITION_SIZE_MULTIPLIER=1.5
UNICORN_LEVERAGE_MULTIPLIER=1.2
UNICORN_BYPASS_COOLDOWN=1
UNICORN_BYPASS_MAX_POSITIONS=1
UNICORN_EXTRA_POSITION_SLOTS=2
UNICORN_BYPASS_RATE_LIMIT=1
UNICORN_BYPASS_LOSS_STREAK=1
UNICORN_BYPASS_CORRELATION=1
```

---

## Logging

### Unicorn Detection

When a unicorn signal is detected, you'll see:

```
🦄🦄🦄 UNICORN SIGNAL DETECTED 🦄🦄🦄 | 
symbol=BTCUSDT | side=LONG | 
score=92.5 | strength=0.925 | 
entry=50000.00 | sl=49750.00 | tp=50500.00
```

### Unicorn Entry

When entering a unicorn position:

```
🦄🦄🦄 ENTERING UNICORN POSITION 🦄🦄🦄 | 
symbol=BTCUSDT | side=LONG | size=0.150000 | 
entry_price=50000.00 | leverage=6 | 
score=92.5 | strength=0.925 | 
UNICORN PROTOCOL ACTIVE
```

---

## Risk Management

### Safety Limits

Even with the unicorn protocol, safety limits are still enforced:

1. **Maximum Leverage**: Still capped at `MAX_LEVERAGE`
2. **Maximum Position Size**: Still capped at `MAX_POSITION_SIZE`
3. **Maximum Capital per Position**: Still capped at `MAX_CAPITAL_PER_POS`
4. **Stop Loss**: Still enforced (no bypass)
5. **Take Profit**: Still enforced (no bypass)

### Risk Considerations

**Pros**:
- Captures rare, high-probability opportunities
- Maximizes returns on best signals
- Prevents missing opportunities due to restrictions

**Cons**:
- Higher position sizes = higher risk per trade
- Bypassing restrictions = less conservative approach
- Extra position slots = higher portfolio risk

**Mitigation**:
- Only activates for top 1-2% of signals (very rare)
- All safety limits still enforced
- Can be disabled via `UNICORN_PROTOCOL_ENABLED = False`

---

## Example Scenario

### Normal Signal (Score: 75)
- **Position Size**: $1,000
- **Leverage**: 5x
- **Cooldown**: 5 minutes (same symbol)
- **Max Positions**: 10/10 (blocked)
- **Result**: Entry blocked due to max positions

### Unicorn Signal (Score: 92)
- **Position Size**: $1,500 (50% increase)
- **Leverage**: 6x (20% increase)
- **Cooldown**: Bypassed
- **Max Positions**: 12/10 (allowed via extra slots)
- **Result**: Entry allowed immediately

---

## Monitoring

### Signal History

Unicorn signals are marked in signal history:

```python
signal_record = {
    'symbol': 'BTCUSDT',
    'final_score': 92.5,
    'strength': 0.925,
    'is_unicorn': True,  # ← Marked as unicorn
    ...
}
```

### Statistics

Track unicorn performance separately:
- Count of unicorn signals detected
- Count of unicorn positions entered
- Win rate of unicorn positions
- Average PnL of unicorn positions

---

## Disabling the Protocol

To disable the unicorn protocol:

1. **Via Config**: Set `UNICORN_PROTOCOL_ENABLED = False`
2. **Via Environment**: Set `UNICORN_PROTOCOL_ENABLED=0`

When disabled, unicorn signals are treated as normal signals (no bypasses, no size/leverage increases).

---

## Best Practices

1. **Monitor Frequency**: Track how often unicorns appear (should be rare: 1-2% of signals)
2. **Performance Tracking**: Compare unicorn win rate vs. normal signals
3. **Risk Monitoring**: Ensure unicorn positions don't exceed risk limits
4. **Adjust Thresholds**: If too many/few unicorns, adjust `UNICORN_SCORE_THRESHOLD`
5. **Review Bypasses**: Periodically review which bypasses are most useful

---

## Summary

The **Unicorn Found Priority Protocol** ensures that the highest-quality trading opportunities are:
- ✅ **Detected** immediately
- ✅ **Entered** without restrictions
- ✅ **Sized** appropriately (larger positions)
- ✅ **Leveraged** optimally (higher leverage)

This protocol maximizes returns on the best signals while maintaining safety through hard limits and careful risk management.

**Status**: ✅ **Fully Implemented and Ready for Use**

