# Mr Bera's Bot — Forex AI Trading System

A live, 24/5 MetaTrader 5 trading system that combines ICT/SMC price-action concepts with a dual machine-learning ensemble (XGBoost + Random Forest). It reads market structure across three timeframes, scores each setup against 20 confluence factors, and only fires a trade when both models agree above their confidence threshold. Most cycles, it does nothing — that's by design, not a bug.

Full write-up of the philosophy and current live status: **[docs/README.md](docs/README.md)**.

## What it trades

EURUSD, USDCHF, USDCAD, NZDUSD, EURJPY, XAUUSD, BTCUSD — seven pairs kept after backtesting; GBPUSD, USDJPY, and AUDUSD were dropped for insufficient profit factor.

## What's actually in here

- **Strategy engine** — 4H/1H/5M multi-timeframe alignment, Break of Structure, liquidity sweeps, order blocks, Fair Value Gaps, CHoCH, Fibonacci OTE zones, ICT kill zones, and a 20-factor confluence score gating every entry.
- **ML layer** — per-pair XGBoost + Random Forest models (with a global fallback), both must agree before a trade fires. Sunday auto-retrain blends the week's live trades at 3× weight.
- **Risk management** — ATR-based stops, confidence-scaled reward:risk (up to 1:10), daily/drawdown loss caps, session kill-zone trade budgets, correlation and news-event filters.
- **Telegram bot** — live trade alerts with chart images, daily briefings, a weekly ICT/SMC lesson series, admin commands, and a public channel.
- **Streamlit dashboard** — 6 pages (home, performance, AI training, blog, managed accounts, admin), accessible remotely via ngrok.
- **Managed accounts** — admin-controlled slots for running the same strategy on a client's own MT5 account.

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** and **[docs/STRATEGY.md](docs/STRATEGY.md)** for how these actually fit together, and **[roadmap.md](roadmap.md)** for the full build history.

## Getting started

Full walkthrough (Python setup, MT5 credentials, Telegram bot creation, first run): **[docs/SETUP.md](docs/SETUP.md)**. Short version:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Fill in `.env` (MT5 login, Telegram bot token, optional ngrok/Hugging Face tokens — never commit this file), then double-click `start_bot.bat` to launch the bot, dashboard, and ngrok tunnel together.

## Docs

| | |
|---|---|
| [Setup & Installation](docs/SETUP.md) | [System Architecture](docs/ARCHITECTURE.md) |
| [Trading Strategy](docs/STRATEGY.md) | [Risk Management](docs/RISK_MANAGEMENT.md) |
| [Dashboard Guide](docs/DASHBOARD.md) | [Telegram Commands](docs/TELEGRAM.md) |
| [Cloud Storage](docs/CLOUD_STORAGE.md) | [Troubleshooting](docs/TROUBLESHOOTING.md) |

## Status

Live on a real MT5 demo account since late April 2026, models retraining weekly on real closed trades. See [roadmap.md](roadmap.md) for what's shipped versus still open (walk-forward validation, VPS hosting, and multi-account bot trading are the current open items).

## Disclaimer

Trading forex involves significant risk. This system is for educational and research purposes. Test thoroughly on a demo account before considering live capital.
