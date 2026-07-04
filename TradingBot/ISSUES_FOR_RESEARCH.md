# DeepClaudeTraderBot — Issues for Deep Research

## Critical (live trading impact)

### 1. Monitor timeout causes trailing engine misses
**Symptom:** `[TIMEOUT] monitor_and_exit_positions() hung` — trailing stops don't update during spikes.
**Impact:** LAB spike wasn't fully captured because the trail never ratcheted up during the peak. Runner gave back $5.88 that a live trail would have protected.
**Current fix:** Timeout raised 15s→30s, lightweight exchange pre-initialized at startup. Mitigates but doesn't solve root cause.
**Research needed:** Why does CCXT `fetch_positions()` intermittently hang for >10s even with timeout set? Is this a connection pool exhaustion issue? Should we use a dedicated WebSocket for position updates instead of REST polling?

### 2. closePosition orders invisible to fetch_open_orders
**Symptom:** `fetch_open_orders()` returns empty for positions that DO have SL/TP on exchange. Forces reliance on -4130 "test by trying to place" hack.
**Impact:** Cannot programmatically determine if a position is protected without side effects (placing/canceling orders). Naked audit is inherently destructive.
**Research needed:** Is there a Binance API endpoint that lists closePosition conditional orders? Does `/fapi/v1/openOrders` with `type=STOP_MARKET` work? Alternative: use user-data WebSocket stream which DOES emit ORDER_TRADE_UPDATE for closePosition orders.

### 3. Trailing stop loss becomes stale when monitor is blocked
**Symptom:** On-exchange stop stays at old level while price rises, then retraces through the stale stop.
**Impact:** Runner portion of winning trades gives back significantly more than configured trail distance.
**Research needed:** Can Binance's native trailing stop order (`/fapi/v1/algoOrder` with `type=TRAILING_STOP_MARKET`) replace our manual ratcheting? Native trailing stops update server-side without bot intervention. What callback rate works best for crypto?

## High (edge quality)

### 4. AdvancedFeatures cache empty for most tokens
**Symptom:** 90% of signals reach the checklist with `advanced_features=None`. SFP/divergence/exhaustion never fire for most tokens.
**Impact:** Confluence model falls back to 24h-change-only scoring. Real structure signals only work on tokens with cached features.
**Research needed:** What's the optimal pre-computation strategy? Pre-compute features for top-50 volume tokens on startup? Use a background worker? Reduce feature compute cost?

### 5. Non-canonical indicators (RSI, ATR, ADX) — PARTIALLY FIXED
**Current state:** RSI/ATR/ADX now use Wilder smoothing. But no regression tests against reference implementations.
**Research needed:** What's the expected deviation between our Wilder implementations and TA-Lib? At what thresholds does the deviation matter for divergence detection?

### 6. Volume confirmation uses 24h volume proxy
**Symptom:** When AdvancedFeatures doesn't have candle-level volume data, we fall back to 24h volume > $200M as a proxy for volume confirmation.
**Impact:** Volume-confirmed SFP may not actually have elevated volume on the reversal candle. False positives.
**Research needed:** What's the correlation between 24h volume rank and reversal-candle volume? Can we use `fetch_trades()` or WebSocket trade stream to compute real-time volume bars?

### 7. Position sizing invariant is contradictory
**Symptom:** `position_manager.py` says "leverage does not affect contract count" then divides by leverage later to get "actual" risk.
**Impact:** Risk may be misstated. Sizing may be wrong for higher leverage.
**Research needed:** Audit the sizing math end-to-end. Should be: `contracts = notional_risk / price_risk` where `notional_risk = equity * risk_pct`, independent of leverage. Leverage only affects margin usage.

## Medium (reliability)

### 8. Dual state system — sentinel strings vs TrackedPosition
**Symptom:** `position_state.py` defines proper enums and transitions, but hot paths still use `'PENDING'`, `'EXISTS'`, `'RECONCILED'`, `'ATOMIC'` sentinel strings.
**Impact:** Two competing state models = duplicate positions, orphaned stops, bad restart behavior.
**Research needed:** Migration strategy. Should we use a single TrackedPosition object everywhere? How do production frameworks (Freqtrade, Hummingbot) handle the transition from legacy dicts to typed state?

### 9. Entry position is naked for 7.5 seconds (15 attempts × 0.5s)
**Symptom:** SL/TP placement loop runs AFTER entry order fills. Position exists unprotected during this window.
**Impact:** If the bot crashes or network fails during this 7.5s window, position is permanently naked.
**Research needed:** Can Binance OCO orders attach SL/TP atomically at entry? What's the correct `create_order` params for atomic entry+protection? Does Binance support `stopLossPrice`/`takeProfitPrice` on MARKET orders?

### 10. Ghost exit verification uses wrong scope
**Symptom:** `_execute_exit()` in `exit_pipeline.py` referenced bare `exchange` (not `self.exchange` or `bot_instance.exchange_wrapper`). FIXED but untested in production.
**Impact:** Recovery path would crash with NameError during live exit failure, leaving position in unknown state.
**Research needed:** Unit test the ghost exit path with mock exchange. What other recovery paths have similar scope bugs?

## Low (nice to have)

### 11. No end-of-day position liquidation
**Day trading should not hold through illiquid overnight sessions.**
**Research needed:** What time UTC is optimal for forced liquidation? Should we close all positions at 23:00 UTC? Only close losers? Reduce size?

### 12. No test coverage
**Zero tests in repo.** Every change is deployed blind to live.
**Research needed:** How do production crypto bots structure their test suites? What mocking strategy for CCXT? Should we use Binance testnet for integration tests?

### 13. No WebSocket market data
**All market data comes from REST polling.** Config expects sub-100ms latency but REST takes 200-500ms.
**Research needed:** Migration plan for Binance WebSocket streams (`@bookTicker`, `@kline_1h`, user-data stream). Impact on signal quality vs REST?

## Fixed (for reference)

- [x] OrderManager logger crash (AttributeError on leverage-setting exception)
- [x] ExitPipeline ghost exit scope (NameError on bare `exchange`)
- [x] Wilder RSI/ATR/ADX implementations (were SMA approximations)
- [x] Monitor timeout (15s→30s + pre-initialized lightweight exchange)
- [x] Startup reconciliation uses hardcoded 1.8% instead of config distance
- [x] Naked audit skips tracked positions
- [x] DRY_RUN fakes SL/TP orders
