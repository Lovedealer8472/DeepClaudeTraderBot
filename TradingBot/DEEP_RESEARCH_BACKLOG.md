# DeepClaudeTraderBot — Deep Research Backlog

Everything that still needs answers. Prioritized by blast radius.

---

## PRIORITY A: Binance API Migration (breaks if not done)

### A1. Old /fapi/v1/order with closePosition:true — is it still working?
Our order_manager.py still uses the old endpoint. Binance's Dec 2025 changelog says STOP_MARKET/TAKE_PROFIT_MARKET return -4120 STOP_ORDER_SWITCH_ALGO. We need to verify: is this enforced yet, or is there a grace period? Test on testnet with both old and new endpoints and confirm behavior.

### A2. Algo Service reconciliation pattern
We built `binance_algo.py` with typed payloads but haven't wired it into the hot paths. Questions:
- Does `POST /fapi/v1/algoOrder` return the algoId in the same response format as a regular order, or does it use a different structure?
- What fields does the `ALGO_UPDATE` WebSocket event payload contain? We need the exact JSON schema to implement the event handler.
- Does `cancel_algo_order` work on orders that have already started TRIGGERING but haven't filled yet?

### A3. Native trailing stop behavior
- What's the minimum callbackRate that actually works in practice? Docs say 0.1% but what's the effective minimum given tick size rounding?
- If we set a native trailing stop and then place a second algo order (e.g., a tighter TP), does the trailing stop get cancelled or do they coexist?
- Does native trailing stop work with activatePrice? If we want the trail to start only after +1R, can we set activatePrice at that level and the trail only begins then?

---

## PRIORITY B: Indicator & Signal Correctness

### B1. Divergence detection rewrite
Our current divergence detection splits a 20-bar window into halves and compares max/min. This produces ghost divergences. We need:
- Pivot-based divergence detection using `find_pivots()` (built in ta.py but not yet wired)
- Minimum RSI separation threshold for valid divergence: what's the right value? 3 points? 5? 8?
- Should we require price separation too? E.g., "price higher high must exceed prior high by at least 0.25 ATR"

### B2. Golden dataset values
We need reference values for RSI(14), ADX(14), ATR(14), EMA(20) on a fixed BTCUSDT 1d dataset (100+ candles) computed by TA-Lib. This validates our Wilder implementations.
- Generate the dataset once: fetch BTCUSDT 1d klines, save as CSV fixture
- Compute golden values with TA-Lib, save as JSON
- Add regression test that our ta.py matches within 1e-6 tolerance

### B3. SFP detection parameters
- Current lookback: 50 bars on 1h. Research suggests 20-30 bars. What's optimal?
- Close-back-inside threshold: currently "any close." Should require at least 0.25% or 0.15 ATR inside the range.
- Should SFP detection use Wick, Close, or Body? What gets the best hit rate?

### B4. Volume confirmation that actually works
- We proxy volume confirmation with 24h volume > $200M. We need per-candle volume bars.
- What's the optimal storage pattern for 200 bars of 1h OHLCV per symbol? `deque(maxlen=200)` keyed by symbol+timeframe?
- Memory cost: 500 symbols × 3 timeframes × 200 bars × 8 fields = ~19 MB. Is this performant?
- Should we use quote_volume or base_volume for volume confirmation? (quote_volume adjusts for price)

---

## PRIORITY C: Exit Architecture

### C1. Duplicate trailing engines — which to kill?
- `exit_pipeline.py:77` TrailingStopEngine — controlled by `USE_TRAILING_ENGINE` — active default
- `trailing_engine.py:30` TrailingStopEngine — controlled by `USE_NEW_TRAILING_ENGINE` — disabled default
- Which should be canonical? What's different between them? Do we merge or delete one?

### C2. Should we use native trailing stops server-side?
- Server-side means the exchange handles trail updates even when bot is disconnected
- But native trailing doesn't understand our 1R/2R/3R partials, breakeven locks, or structure invalidation
- Can we use native trailing as a "catastrophe floor" and our R-based engine as the "alpha optimizer"?
- What callbackRate to use per tier? Tier A (BTC/ETH): 0.3%? Tier B: 0.5%? Tier C: 0.7%?

### C3. Partial sizing optimization
- We take 50% at 1R. Research says 75% for volatile crypto, 25% for stable majors.
- What's the optimal partial size by volatility regime? Should we vary by ATR percentile?
- Does taking 75% at 1R really improve expectancy, or does it kill the runner too early?

### C4. Time-based trail tightening calibration
- Current: bars 0-2 wide (1.5x), bars 3-5 active (1.0x), bars 6+ aggressive (0.6x)
- We need backtest data on whether this schedule improves expectancy
- Alternative schedules: 2/5/8, 4/8/12, or ATR-only with no time decay

---

## PRIORITY D: Data & Edge

### D1. Taker buy/sell ratio integration
Binance exposes `/futures/data/takerlongshortRatio` with periods 5m to 1d.
- At what thresholds does taker ratio predict reversals? >1.25 = crowded long? <0.80 = crowded short?
- Should we use it as a confluence modifier (+5 to +10 points) or as a hard filter?
- What period is most predictive for 1h structure trades? 5m? 15m? 1h?

### D2. Open interest interpretation
- Rising OI during a bullish SFP = new positioning (good) vs short covering (less durable)
- Should we compute OI change over 1h, 4h, or 24h for structure confirmations?
- Is OI z-score more useful than raw change? What's the right lookback?

### D3. Funding rate as exit/tighten trigger
- At what funding rate does crowding become predictive? >0.05% per 8h? >0.10%?
- Should extreme funding tighten stops or force an exit entirely?
- Does funding work better as a contrarian indicator (extreme long = short signal)?

### D4. Token tiering by liquidity
Current $2 price floor is crude. We need real tiering:
- Tier A: 24h vol > $500M, spread < 5bps, depth > $2M → score threshold 62-65
- Tier B: 24h vol $100M-$500M, spread 5-15bps, depth $500K-$2M → score threshold 68-72
- Tier C: 24h vol $25M-$100M, spread 15-35bps → score threshold 75-80, half size
- Exclude: <$25M vol, spread >35bps
- What confluence thresholds per tier have statistical backing?

### D5. WebSocket migration for market data
- All market data currently REST polling. Config targets sub-100ms but REST is 200-500ms.
- Binance `@bookTicker` is real-time, `@kline_1h` streams candles, user-data stream for order updates.
- What's the migration plan? Phase it by priority: positions stream first, then candles, then book ticker?
- How do production frameworks handle WebSocket reconnection and missed messages?

---

## PRIORITY E: Testing & CI

### E1. Minimum viable test suite
- 5 indicator golden tests (validate ta.py against TA-Lib)
- 5 Binance Algo payload tests (validate payload structure matches API docs)
- 5 trailing invariant tests (stop never moves backward, etc.)
- 3 position sizing tests (risk fraction matches target)
- 2 reconciliation idempotency tests (applying same event twice is safe)

### E2. Mocking strategy
- Unit tests: FakeExchange class, no CCXT network calls
- Integration tests: Binance testnet only, behind `ALLOW_TESTNET=1` flag
- Should we create an `ExchangePort` protocol/interface so tests mock the port, not CCXT internals?

### E3. CI pipeline
- `ruff` + `black` for formatting
- `mypy --strict` on trading core
- `pytest -q` with `pytest-asyncio`
- Property tests with Hypothesis for invariants
- Optional Binance testnet smoke test

---

## PRIORITY F: Architecture & Code Quality

### F1. State machine migration from sentinel strings
Current: `"PENDING"`, `"EXISTS"`, `"RECONCILED"`, `"ATOMIC"` sentinel strings everywhere.
Target: Typed `AlgoOrder`/`ProtectionOrder` with proper AlgoStatus enum.
- How to migrate without breaking the running system? Dual-write? Feature flag? Parallel compare?
- The `position_state.py` TrackedPosition class already exists — wire it or replace it?

### F2. DurableTracker wiring
`durable_tracker.py` exists but isn't wired into the order lifecycle. No `track()` on submit, no `confirm()` on ack, no `resolve()` on terminal.
- Should we replace DurableTracker's JSON persistence with SQLite WAL (state_db.py)?
- What events trigger a write? Order submitted, order confirmed, fill received, algo updated, position closed?

### F3. Global asyncio hack
`global asyncio` at bot.py:1124 — fragile workaround for Python 3.14 closure scoping.
Should pass the event loop explicitly or use `functools.partial`.

### F4. Hardcoded magic numbers audit
~80 hardcoded values across 8 files. Which should become configurable?
Most critical: confluence point values (40, 25, 20, 15), entry threshold (65), SFP lookback (50), close-back-inside threshold (any), volume confirmation threshold (1.5x).

### F5. Replay mode clock mixing
Replay mode mixes `time.time()` with `_get_current_time()`, producing nonsensical deltas for time_since_start and heartbeat timing. Replay mode should use one clock throughout.

---

## PRIORITY G: Operational

### G1. Monitoring and alerting
- Unprotected position age >5s → alert
- Lost order age >60s → alert
- State divergence (venue position exists but local registry flat) → alert
- WebSocket staleness >2s → alert
- Equity below $240 → notification

### G2. End-of-day liquidation
Day trading should not hold through illiquid overnight sessions. Close all positions at 23:00 UTC? Only close losers? Reduce size by 50% at 22:00?

### G3. Kill switch testing
- Does our circuit breaker actually fire on 3 consecutive losses?
- Is the drawdown circuit breaker tested?
- Can we manually trigger a kill switch from the CLI?

---

Total: 7 priority areas, 28 research questions. The A-tier items (Binance API migration verification) are the most urgent — those could break us overnight if the old endpoint gets shut off.
