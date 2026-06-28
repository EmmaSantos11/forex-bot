# System Architecture

This is a breakdown of how all the pieces fit together. If you're trying to understand the codebase, modify something, or debug an issue, this is the right place to start.

---

## Bird's eye view

```
start_bot.bat
    └── launch.py  (master orchestrator)
            ├── run_bot.py  (crash-recovery wrapper)
            │       └── main.py  (trading brain — main loop)
            │               ├── strategy.py       (signal generation)
            │               ├── ml_model.py       (XGBoost + Random Forest)
            │               ├── mt5_connector.py  (broker connection)
            │               ├── portfolio_manager.py (position sizing, drawdown)
            │               ├── live_trader.py    (order execution)
            │               ├── cloud_sync.py     (Hugging Face backup)
            │               └── core/
            │                     ├── multi_timeframe.py   (5M/1H/4H/1D resampling)
            │                     ├── notifications.py     (Telegram messaging)
            │                     ├── trade_logger.py      (trade records)
            │                     └── execution_logger.py  (execution notes)
            │
            ├── streamlit_app.py  (dashboard home page)
            │       └── pages/
            │               ├── 0_About.py
            │               ├── 1_Performance.py
            │               ├── 2_AI_Training.py
            │               └── 3_Signals.py
            │
            └── ngrok tunnel (optional — remote phone access)
```

---

## How the main loop works

`main.py` runs an infinite loop on a 30-second interval. Each cycle does the following, in order:

**1. Health checks**
Reconnects to MT5 if the connection dropped. Resets daily counters at midnight. Checks if it's Sunday and runs the auto-retrain if needed.

**2. Circuit breakers**
Checks account balance against daily loss limit and total drawdown cap. If either is breached, it skips the trading section and waits.

**3. Position management**
Looks at all open trades and advances the trailing stop-loss if the trade has moved far enough in profit. Also checks for smart reversal partials — if a high-confidence signal fires in the opposite direction of an open trade that's sitting in profit, it closes 50% to lock gains.

**4. News check**
Pulls the ForexFactory calendar and notifies all Telegram users about upcoming or just-released HIGH-impact events. Flags affected pairs so they get skipped this cycle.

**5. Signal scan**
For each pair on today's schedule, runs through the full gate checklist, generates signals, checks AI confidence, waits for two-cycle confirmation, and places the trade if everything passes.

**6. State write**
Dumps the current scanner state to `logs/signal_scan.json` so the dashboard can display it without querying MT5 directly.

---

## The data flow for a single signal

```
MT5 (live prices)
    → _fetch_bars()          pulls 5M OHLCV data (500 bars)
    → resample_timeframes()  creates 1H, 4H, 1D from the 5M data
    → add_indicators()       RSI, ATR, MACD, BB, ADX, SMAs on all timeframes
    → add_features()         derived features: momentum, trend_strength, volatility
    → generate_signals_ict() ICT/SMC signal + 20-factor confluence score
    → generate_ensemble_signals()  XGBoost + RF both evaluate the features
    → confidence gate        both models must agree, both must exceed 80%
    → two-cycle confirmation signal must appear on 2 consecutive 30s scans
    → spread check           ask-bid spread must be within per-pair threshold
    → place_trade()          order sent to MT5 with ATR-based SL and 3:1 TP
```

---

## The files and what they own

**`main.py`** — the brain. Everything passes through here. It owns the main loop, the daily pair schedule, all the gate logic, the Sunday retrain pipeline, the Telegram command listener, and the trade entry flow.

**`strategy.py`** — pure signal logic. No MT5 calls, no model calls. Takes a DataFrame of OHLCV data and returns signals. This is where all the ICT/SMC indicators live: BOS detection, order blocks, FVGs, liquidity sweeps, CHoCH, displacement, kill zones, master candle detection, rejection candles, fibonacci zones.

**`ml_model.py`** — model training and inference. Handles label generation (which historical bars would have been profitable), training XGBoost and Random Forest, saving/loading `.pkl` files, and generating predictions. Per-pair models and global fallback models both live here.

**`mt5_connector.py`** — thin wrapper around the MetaTrader5 Python library. Handles connection, reconnection, and shutdown.

**`portfolio_manager.py`** — position sizing and risk accounting. Calculates lot sizes based on account balance and stop distance, tracks open risk, monitors daily and total drawdown.

**`live_trader.py`** — the actual order execution. Takes a symbol, direction, lot size, SL points, and TP points and sends the order to MT5.

**`cloud_sync.py`** — Hugging Face integration. Uploads model files after retraining, downloads missing files at startup.

**`core/multi_timeframe.py`** — resamples a 5M DataFrame into 1H, 4H, and 1D DataFrames using pandas resample. This is what makes the multi-timeframe analysis possible without fetching separate data for each timeframe.

**`core/notifications.py`** — all Telegram messaging. Send to the admin, send to all registered users, fetch incoming updates.

**`dashboard_utils.py`** — shared utilities for the Streamlit pages: CSS injection, sidebar render, colour palette, `get_deal_history()` which pulls closed trades from MT5.

**`streamlit_app.py`** — the dashboard home page. Shows bot mood, account state, signal scanner, open positions, news countdown.

---

## Shared state between threads

The bot runs two threads: the main trading loop and a Telegram listener. They share a few global variables declared at the top of `main.py`:

- `active_pairs` — the list of broker-resolved symbol names currently being traded. Both threads read and write this (add/removepair commands from Telegram).
- `pair_models` — dictionary mapping each symbol to its `(xgb_model, rf_model)` tuple. Updated after Sunday retrain.
- `_scan_state` — the current signal scanner state written to `logs/signal_scan.json` each cycle. The dashboard reads this file — it never queries the bot directly.

The Telegram thread is a daemon thread, so it shuts down automatically when the main process exits.

---

## Log files

Everything the bot produces during runtime lands in the `logs/` folder:

| File | What it contains |
|------|-----------------|
| `signal_scan.json` | Current per-pair scanner state — read by the dashboard every 30s |
| `bot_mood.json` | Current bot "mood" (scanning, watching, high_conf, won, lost, etc.) |
| `news_events.json` | Upcoming HIGH-impact news events for the dashboard countdown |
| `ath.json` | All-time high balance — used to trigger ATH notifications |
| `model_recall.csv` | Latest retrain recall scores per pair |
| `model_recall_history.csv` | Cumulative history of all retrains — drives the AI training trend chart |
| `crash.log` | Full tracebacks from any unexpected crashes |

These are small files. The only one that grows meaningfully over time is `model_recall_history.csv`, and even that will be a few kilobytes after a year.

---

## Models folder

```
models/
    xgb_trading_model.pkl    ← global XGBoost fallback
    rf_trading_model.pkl     ← global Random Forest fallback
    xgb_EURUSD.pkl           ← per-pair XGBoost
    rf_EURUSD.pkl            ← per-pair Random Forest
    xgb_USDCHF.pkl
    rf_USDCHF.pkl
    ... (one pair per file)
```

The bot always tries the per-pair model first. If it's missing (pair was just added, or first run), it falls back to the global model. Both get retrained every Sunday.
