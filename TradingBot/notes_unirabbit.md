# UniRabbit Trading Bot - Execution Pipeline & Context

## Execution Pipeline

### 1. Entry Point
- **File**: `run.py`
- **Function**: `main()` (line 111)
- **Flow**: 
  - Validates config → Creates `ScalperBot()` → Calls `await bot.run()`

### 2. Main Loop & Scan Start
- **File**: `app/bot.py`
- **Function**: `ScalperBot.run()` (line 3015)
- **Scan Function**: `ScalperBot.scan_and_enter_signals()` (line 998)
- **Called from**: Main loop at line 3189
- **Interval**: Regime-specific `scan_interval_seconds` (default ~1.0s)

### 3. Signal Generation
- **File**: `app/scanner.py`
- **Class**: `SymbolScanner`
- **Method**: `process_symbol()` (line 37)
- **Signal Generator**: `app/signals.py` → `SignalGenerator.generate_signal()` (line 244)
- **Signal Representation**: 
  - Class: `TradingSignal` (dataclass with `__slots__`)
  - Fields: `symbol`, `side`, `entry_price`, `stop_loss`, `take_profit`, `strength` (0-1), `final_score` (0-100), `signal_score` (SignalScore object)
  - Location: `app/signals.py` lines 22-47

### 4. Scoring
- **Primary Scorer**: `app/signal_scorer.py` → `SignalScorer.score_signal()` (line 456)
- **Scoring v2**: `app/scoring/engine.py` → `compute_final_score()` (line 39)
- **Scoring v2 Adapter**: `app/scoring/adapter.py` → `build_signal_context()` 
- **Score Components**: 
  - Setup: trend_score, momentum_score, volatility_score
  - Execution: spread_score, depth_score, latency_score
  - Risk: rr_score, exposure_score, streak_score
  - Final: 0-100 scale
- **Entry Gate Threshold**: `app/signals.py` line 430 → `effective_min_score = 70.0`
- **Scoring Config**: `app/scoring/config.py` → `MIN_SIGNAL_SCORE = 82.0` (used by v2 system)

### 5. Filtering (Before Scoring)
- **File**: `app/engine/scalper_filters.py`
- **Function**: `evaluate_three_stage_filter()` (line 414)
- **Stages**:
  1. Microstructure Gate (line 29): Spread/depth checks
  2. Structure Gate (line 114): Exhaustion/divergence/SFP signals
  3. Direction Prediction (line 276): Candle direction score
- **Called from**: `app/bot.py` line 1664
- **Note**: Filters run AFTER scoring in current architecture

### 6. Risk/Sizing
- **File**: `app/position_manager.py`
- **Function**: `PositionManager.calculate_position_size()` (line 64)
- **Risk Manager**: `app/core/risk.py` → `RiskManager.can_open_new_position()` (line 33)
- **Sizing Logic**:
  - Risk budget: `TOTAL_RISK_BUDGET` (2% of equity)
  - Per-trade risk: `MIN_RISK_PER_TRADE` (0.2%) to `MAX_RISK_PER_TRADE` (1.0%)
  - Stop distance check: `MIN_STOP_DISTANCE_PCT` (0.5%)
  - DRY_RUN special case: If `stop_too_tight`, uses minimal size `max(MIN_POSITION_SIZE, equity * 0.002)`
- **Validation**: `PositionManager.can_enter_position()` (line 225)

### 7. DRY_RUN vs LIVE Decision
- **Config Flag**: `app/config.py` line 55 → `DRY_RUN = env("DRY_RUN", "1") in ("1","true","TRUE")`
- **Order Placement**: `app/order_manager.py` line 90 → `if DRY_RUN:` simulates order
- **Exchange Wrapper**: `app/exchanges/binance_futures.py` line 41 → `self.dry_run = DRY_RUN`
- **Sizing Special Case**: `app/position_manager.py` line 116 → DRY_RUN allows minimal size for `stop_too_tight`

### 8. Order Placement
- **File**: `app/order_manager.py`
- **Function**: `OrderManager.enter_position()` (line 35)
- **DRY_RUN Path**: Lines 90-98 → Returns simulated `OrderResult` with fake order_id
- **LIVE Path**: Lines 99-111 → Calls `_place_limit_order()` or `_place_market_order_fast()`
- **Exchange Adapter**: `app/exchanges/binance_futures.py` → `BinanceFuturesExchange`
- **Called from**: `app/bot.py` line 2035

### 9. UI Entry Point
- **File**: `app/ui_v2.py` (primary UI)
- **Class**: `UIv2` (line 87)
- **Initialization**: `app/bot.py` line 3085 → `ui_v2_instance = UIv2()`
- **State Source**: `app/snapshot_builder.py` → `build_engine_snapshot()` (line 168)
- **Snapshot Class**: `EngineSnapshot` (line 141 in snapshot_builder.py)
- **UI Reads From**: 
  - `bot.positions` (position registry)
  - `bot.metrics` (performance stats)
  - `bot.signal_history` (recent signals)
  - `bot.filter_stats_cumulative` (filter statistics)
  - `DecisionEvent` objects (recent decisions)

## Configuration & Constants

### DRY_RUN Control
- **Primary Flag**: `app/config.py` line 55 → `DRY_RUN = env("DRY_RUN", "1")`
- **Default**: `"1"` (DRY_RUN enabled by default)
- **Usage Locations**:
  - `app/order_manager.py` line 69, 90, 173
  - `app/position_manager.py` line 116 (stop_too_tight special case)
  - `app/exchanges/binance_futures.py` line 41, 46
  - `app/scanner.py` line 20

### Leverage Settings
- **Base Leverage**: `app/config.py` line 80 → `LEVERAGE_BASE = env("LEVERAGE","5", int)`
- **Dynamic Leverage**: Lines 83-85
  - `USE_DYNAMIC_LEVERAGE` (default: enabled)
  - `MIN_LEVERAGE = 2`
  - `MAX_LEVERAGE = 5`
- **Unicorn Leverage**: Line 175 → `UNICORN_LEVERAGE_MULTIPLIER = 1.2`

### Risk Limits
- **Total Risk Budget**: `app/config.py` line 93 → `TOTAL_RISK_BUDGET = 2.0%` of equity
- **Per-Trade Risk**: Lines 100-101
  - `MIN_RISK_PER_TRADE = 0.2%`
  - `MAX_RISK_PER_TRADE = 1.0%`
- **Stop Distance**: Line 231 → `MIN_STOP_DISTANCE_PCT = 0.5%`
- **Position Size**: Lines 215, 384
  - `MIN_POSITION_SIZE = 10 USDT`
  - `RPA_MIN_SIZE_USD = 5 USDT` (if RPA enabled)

### Global Filters (Can Block Entries)
- **Score Threshold**: `app/signals.py` line 430 → `effective_min_score = 70.0` (entry gate)
- **Hard Min Score**: `app/config.py` line 149 → `HARD_MIN_SCORE = 70` (immediate reject)
- **Strength Threshold**: Line 146 → `MIN_SIGNAL_STRENGTH = 0.60` (0-1 scale)
- **Percentile Filter**: Line 150 → `SIGNAL_PERCENTILE_THRESHOLD = 0.0` (disabled by default)
- **Microstructure Filter**: `app/engine/scalper_filters.py` line 79-106
  - Spread > 0.25% OR depth < $800 USD
- **Structure Filter**: Lines 255-264 (conflicting signals only)
- **Direction Filter**: Lines 400-405 (direction_score < 0.45)

## Key File Locations

| Component | File | Line Range |
|-----------|------|------------|
| Scan Start | `app/bot.py` | 998-3189 |
| Signal Generation | `app/signals.py` | 244-444 |
| Scoring Entry | `app/signal_scorer.py` | 456-650 |
| Scoring v2 Entry | `app/scoring/engine.py` | 39-57 |
| Sizing/Risk | `app/position_manager.py` | 64-223 |
| DRY_RUN/LIVE Branch | `app/order_manager.py` | 90-111 |
| Order Placement | `app/order_manager.py` | 35-385 |
| UI Entry Point | `app/ui_v2.py` | 87-721 |
| UI State Builder | `app/snapshot_builder.py` | 168-715 |

## TODOs (Existing)

### app/position_manager.py
- Line 148: `# REFACTOR: Handle loss streak adjustment errors gracefully`
- Line 314: `# REFACTOR: Handle invalid score/bonus values`

### app/order_manager.py
- Line 86: `# REFACTOR: Handle leverage setting errors (may already be set)`

### app/bot.py
- Line 633: `# OPTIMIZATION: Removed debug log (not critical)`
- Line 916: `# OPTIMIZATION: Removed debug log (fallback is expected behavior)`

### app/engine/scalper_filters.py
- Line 443: `# DEBUG: Log to file only (no console output)`
- Line 447: `# DEBUG: Log to file only (no console output)`

### app/exchanges/binance_futures.py
- Line 32: `# OPTIMIZATION: Cache symbol normalization/denormalization (hot path)`

## Dead/Unused Code Paths

### Potentially Unused
- **Legacy UI**: `app/ui_rich.py` and `app/ui_runtime.py` - Only used if `UI_MODE != "v2"` (default is "v2")
- **Legacy Scoring**: `app/extended_scoring.py` - Only used if `USE_EXTENDED_SCORING=1` and `USE_NEW_SCORING_SYSTEM=0`
- **Old Signal Scorer**: `app/signal_scorer.py` - Still used as fallback, but primary is Scoring v2
- **Risk Manager**: `app/core/risk.py` - `RiskManager` class exists but may not be fully integrated (PositionManager has its own risk logic)

### Confirmed Active
- **Scanner**: `app/scanner.py` - Active, used in `bot.py` line 132
- **Scoring v2**: `app/scoring/*` - Active, used via `app/scoring/adapter.py`
- **UI v2**: `app/ui_v2.py` - Active, default UI mode
- **Exit Pipeline**: `app/engine/exit_pipeline.py` - Active, used in bot.py line 39

## Pipeline Flow Summary

```
run.py:main()
  └─> ScalperBot.__init__()
      └─> ScalperBot.run() [bot.py:3015]
          ├─> init_exchange()
          ├─> UI initialization (ui_v2.py)
          └─> Main loop [bot.py:3136]
              ├─> refresh_universe() [periodic]
              ├─> scan_and_enter_signals() [bot.py:998]
              │   ├─> scanner.process_symbol() [scanner.py:37]
              │   │   ├─> Fetch ticker/orderbook
              │   │   └─> signal_generator.generate_signal() [signals.py:244]
              │   │       ├─> signal_scorer.score_signal() [signal_scorer.py:456]
              │   │       └─> scoring.engine.compute_final_score() [scoring/engine.py:39]
              │   ├─> evaluate_three_stage_filter() [scalper_filters.py:414]
              │   ├─> position_manager.can_enter_position() [position_manager.py:225]
              │   ├─> position_manager.calculate_position_size() [position_manager.py:64]
              │   └─> order_manager.enter_position() [order_manager.py:35]
              │       └─> exchange_wrapper.create_order() [if LIVE]
              └─> monitor_and_exit_positions() [bot.py:2320]
                  └─> exit_manager.exit_position() [exit_manager.py]
```

## DRY_RUN Safety Check

✅ **DRY_RUN is safe**:
- Order placement: `order_manager.py:90` returns simulated result
- Exchange wrapper: `binance_futures.py:41` stores DRY_RUN flag
- No real API calls in DRY_RUN mode
- Sizing special case: Allows minimal size for testing without weakening LIVE safety

