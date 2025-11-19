# Scalper_MEXC v5.4 (LLM-Adaptive, Modular)

- DRY-on-Live (MEXC Futures data)
- LLM control bridge: edits `control_patch.json` to switch modes/params
- CMD panel: mode, PF, WR, EqΔ, API budget, Top Movers
- Modular layout under `app/`

## Quickstart
```bash
pip install ccxt python-dotenv
cd Scalper_MEXC_v54_modular
python run.py
```

## API Smoke Test (Validate Binance Futures Connection)

Before running the bot, validate your Binance Futures API credentials and permissions:

```powershell
# Set API keys (or load from .env)
$env:BINANCE_API_KEY = "<your_key_here>"
$env:BINANCE_SECRET = "<your_secret_here>"

# Run smoke test
python binance_futures_api_smoketest.py
```

**Expected Success Output:**
- Loaded X markets
- Futures USDT balance: ...
- TEST ORDER endpoint call succeeded
- RESULT: Binance USDT-M Futures API accepts authenticated order requests

This confirms: keys valid, region not blocked, and trading permissions enabled.

## LLM Council (multi-model assistants)

The repo now ships with an opinionated "council of experts" helper that can query several LLM providers in parallel (OpenAI, Claude, Groq, DeepSeek, Perplexity, etc.) and aggregate the feedback.

1. Collect API keys and export them (only the providers with keys will activate):
   ```powershell
   $env:OPENAI_API_KEY="..."
   $env:ANTHROPIC_API_KEY="..."
   $env:GROQ_API_KEY="..."
   $env:DEEPSEEK_API_KEY="..."
   $env:PERPLEXITY_API_KEY="..."
   ```

2. Install the HTTP dependency:
   ```bash
   pip install httpx
   ```

3. Copy `council_config.example.json` to `council_config.json` if you want persistent customisation (otherwise the baked-in defaults are used whenever the file is missing).

4. Run the council CLI:
   ```bash
   python council_cli.py "Help me refactor the signal generator" --context "app/universe.py" --topic "engineering review"
   ```

The CLI prints each model’s analysis plus a synthesized consensus (moderated by the first active provider by default). Use `--json` to emit the raw payload for downstream tooling.

### Live Autopilot (experimental)

Set `COUNCIL_AUTOPILOT=1` to allow the council to tune bot parameters while `run.py` is live. You can control the cadence with `COUNCIL_INTERVAL_SEC` (default 180 seconds) and limit which env vars get touched through `COUNCIL_ALLOWED_PARAMS` (comma-separated list; defaults provided in `app/config.py`). Bounds are taken from `envelopes.json`, so keep that file up to date with safe min/max values before enabling autopilot.

### Strategy Layer

The bot now exposes a basic strategy engine that produces baseline recommendations for the council:

- `momentum` raises leverage and speeds up entries when average heat across top symbols is strong.
- `mean_reversion` cuts risk and extends cooldowns when drawdown or loss streaks spike.
- `breakout` increases rotation when spreads are tight and movers show high heat.

Control them via environment variables (or `.env`):

```
STRATEGY_ACTIVE=momentum,mean_reversion,breakout
MOMENTUM_HEAT_THRESHOLD=3.2
MOMENTUM_LOOKBACK_MIN=5
MEANR_DRAWDOWN_TRIGGER=0.05
MEANR_COOLDOWN_BOOST=45
BREAKOUT_SPREAD_TRIGGER=1.2
BREAKOUT_HEAT_TRIGGER=4.5
```

Only STRATEGY_ACTIVE members are evaluated. The council prompt includes these baseline ideas so it can refine them on the fly, and all knobs are whitelisted in `COUNCIL_ALLOWED_PARAMS` for LLM tuning. Update `envelopes.json` with safe bounds for any new parameters you enable.

### Live Autopilot Mode

To let the council tune the running bot in real time, set the flag and restart:

```bash
setx COUNCIL_AUTOPILOT "1"
setx COUNCIL_INTERVAL_SEC "240"  # optional cadence override (seconds)
setx COUNCIL_ALLOWED_PARAMS "RISK_PCT,LEVERAGE,MAX_CONCURRENT_POS,ENTRY_DELAY_MS,COOLDOWN_SEC,MAX_ENTRIES_PER_MIN,SLIPPAGE_BPS,CLOSE_FEECHURN_BPS"
```

Only the listed parameters can be edited; envelope bounds (`envelopes.json`) still govern min/max values. The bot periodically snapshots its performance, asks the council for structured JSON adjustments, and applies allowed changes without blocking the trading loop. Keep the council disabled (`COUNCIL_AUTOPILOT=0`) until you’re comfortable with its behaviour.