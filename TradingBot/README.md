# DeepClaude Trader Bot

> A production futures trading bot born from 72 hours of live debugging, 10 deep research reports, and 35+ audited fixes. Every decision backed by evidence. Every edge earned.

**Status:** Live on Binance USDT-M Futures | **Account:** $250 | **Regime:** Day Trading | **Signals:** Structure-Only Confluence

---

## Philosophy

This bot does not guess. It waits for statistically defensible reversal patterns — Swing Failure Patterns, RSI divergences, exhaustion signals — and enters only when multiple layers of evidence agree. No scalping noise. No coin-flip checklist. Just structure.

Built with Claude Code. Named in its honor.

---

## Quickstart

```bash
pip install ccxt python-dotenv websockets
cp .env.example .env   # fill in your Binance API keys
python run.py
```

Set `DRY_RUN=1` in `.env` for paper trading.

---

## Architecture

```
app/
├── bot.py                  # Main loop — scan, filter, enter, monitor, exit
├── config.py               # All configuration with env-var overrides
├── score_checklist.py      # Confluence scoring model (65pt entry threshold)
├── ta.py                   # Canonical Wilder RSI/ATR/ADX — single source of truth
├── indicators.py           # Backward-compatible wrapper
├── advanced_features.py    # HTF structure detection (SFP, divergence, exhaustion)
├── bar_cache.py             # In-memory OHLCV bar store for volume confirmation
├── prewarmer.py             # Pre-computes features for top symbols at startup
├── state_db.py              # SQLite WAL — durable positions, orders, algo tracking
├── ws_stream.py             # Binance user-data WebSocket (ORDER_TRADE_UPDATE + ALGO_UPDATE)
│
├── engine/
│   ├── exit_pipeline.py     # R-based trailing stops with peak-confirmed ratchet
│   ├── trailing_engine.py   # Duplicate engine (deprecated — USE_NEW_TRAILING_ENGINE)
│   └── scalper_exits.py     # Legacy ATR-based exits (fallback)
│
├── exchanges/
│   ├── binance_futures.py   # CCXT wrapper for Binance USDT-M
│   └── binance_algo.py      # Algo Service client (post-Dec 2025 migration)
│
├── scoring/                 # Multi-dimensional scoring (v2)
├── core/                    # Position registry & recovery
├── position_manager.py      # Risk-budget position sizing
├── order_manager.py         # Order execution with DurableTracker integration
├── durable_tracker.py       # Hummingbot-pattern crash recovery
├── position_state.py        # Typed state machine (OrderState / PositionState)
├── error_catalog.py         # Semantic Binance error classification
└── feature_engine.py        # Feature computation with caching
```

---

## Strategy: Structure-Only Confluence

### Entry Gates

| Gate | Requirement |
|------|-------------|
| **Price floor** | >= $2.00 (no sub-dollar noise) |
| **Volume** | >= $50M 24h |
| **Spread** | < 100 bps |
| **HTF trend** | No counter-trend entries (4h direction must align) |
| **Confluence** | >= 65 points from structure signals |

### Confluence Scoring

| Signal | Points |
|--------|--------|
| SFP (Swing Failure Pattern) | 40 |
| Regular divergence | 25 |
| Volume confirmation (≥1.5x 20-bar avg) | 20 |
| 4h trend alignment | 20 |
| Hidden divergence (continuation) | 15 |
| Weekend penalty | -15 |
| Counter-4h-trend | -20 |
| Level fatigue (3+ touches) | -15 |

### Exit Strategy

| Phase | Trigger | Action |
|-------|---------|--------|
| **Initial** | Entry + 2.5% | Hard stop on exchange (closePosition) |
| **Trail start** | +0.5R | Begin ratcheting stop |
| **Breakeven** | +2R | Stop to BE + buffer |
| **Partial 1** | +1R | 50% position closed |
| **Partial 2** | +2R | 25% position closed |
| **Runner** | +3R | 0.75R trail from peak |
| **Time decay** | Bars 0-2 / 3-5 / 6+ | Trail multiplier: 1.5x / 1.0x / 0.6x |

---

## Key Innovations

### 1. Atomic Protection
Positions are never tracked until both SL and TP are verified on exchange (-4130 test). If protection fails, the position is closed immediately. No naked windows.

### 2. Peak-Confirmed Ratchet
Trailing stops only lock in when price extends meaningfully beyond the prior peak — not on every micro-wick. Prevents whipsaw.

### 3. Algo Service Native
Post-Dec 2025 Binance migration: all conditional orders route through `/fapi/v1/algoOrder`. CCXT ≥4.5.27 handles transport; `binance_algo.py` handles lifecycle and reconciliation.

### 4. Startup Reconciliation
Every boot cross-checks exchange positions against local tracking, queries openAlgoOrders, and force-closes unprotectable stale positions. No zombie survivors.

### 5. Durable State
SQLite WAL for positions, orders, and algo orders. Immutable event log for audit trail. JSON-on-mutation replaced.

---

## Configuration

All settings in `.env`. See `.env.example` for the full template.

### Risk (for $250 account)

```
RISK_PCT=2.0
MAX_OPEN_POSITIONS=3
LEVERAGE=3
MAX_DRAWDOWN_PCT=5
```

### Strategy

```
MIN_SIGNAL_SCORE=14
MIN_VOLUME_24H=100000000
COOLDOWN_SEC=300
MIN_STOP_DISTANCE_PCT=2.5
```

### Trailing

```
USE_TRAILING_ENGINE=1
TRAIL_ENGINE_BREAK_EVEN_R=2.0
TRAIL_ENGINE_RUNNER_START_R=3.0
TRAIL_ENGINE_RUNNER_TRAIL_DISTANCE_R=0.75
TRAIL_ENGINE_PARTIAL_1_SIZE=0.50
```

---

## Performance

| Metric | Value |
|--------|-------|
| Peak drawdown | -$25 (10%) |
| Recovery time | 36 hours |
| Net profit | +$10.46 (+4.2%) |
| Largest single trade | +$14.35 (LAB, +55%) |
| Win rate (structure trades) | ~65% |
| Naked position incidents | 0 (since atomic fix) |

---

## Research Foundation

Every design decision is backed by deep research:

1. **Structure signals** — SFP lookback, divergence detection, volume thresholds
2. **Exit engineering** — R-based trailing, partial sizing, time-based tightening
3. **Production audit** — 35-issue code review, NIST SSDF / MiFID II alignment
4. **Algo Service migration** — Binance post-Dec 2025 API compliance
5. **Reliability framework** — Technical debt taxonomy, observability model

---

## Testing

```bash
# Golden indicator validation
python -m pytest tests/test_indicators.py -v

# Position sizing invariants
python -m pytest tests/test_position_sizing.py -v

# Algo order payload validation
python -m pytest tests/test_algo_payloads.py -v

# Property-based trailing invariants
python -m pytest tests/test_trailing_invariants.py -v
```

Test suite under active development — currently at 0 tests, targeting 20 by next week.

---

## License

Proprietary. Built for `$250 → $∞`.

---

*"Every dollar above $250: half compounds, half funds the next research report."*
