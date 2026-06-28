# Mr Bera's Bot — Forex AI Trading System

This started as a side project. The idea was simple: build something that could watch the markets so I didn't have to sit in front of charts all day. What it turned into is a full trading system that combines institutional-grade price action concepts with machine learning — running live, 24/5, on a real MetaTrader 5 account.

The bot doesn't just blindly fire orders. It reads market structure across multiple timeframes, checks for confluence across 20 different factors, runs two separate AI models and makes sure they agree, filters out bad timing and high-impact news, and only then — if everything lines up — does it actually place a trade. Most of the time it does nothing. That's by design.

---

## What it trades

Seven pairs, each chosen after backtesting:

- **EURUSD** — the flagship. Tightest spreads, most liquid, most predictable structure.
- **USDCHF** — safe-haven pair that moves with EUR but gives independent setups.
- **USDCAD** — oil-correlated, solid Wednesday/Thursday momentum.
- **NZDUSD** — commodity/resource currency, strong mid-week range setups.
- **EURJPY** — high volatility, best for Monday and Thursday carry-flow moves.
- **XAUUSD** — gold. Reacts to everything. Traded Tuesday/Wednesday/Friday only.
- **BTCUSD** — added for mid-week expansion moves. Not traded Friday.

GBPUSD, USDJPY, and AUDUSD were removed after backtesting showed insufficient profit factors on this strategy. Better to run fewer pairs well than trade everything poorly.

---

## How it works — the short version

Every 30 seconds the bot scans each pair on its schedule. Before it even looks at a signal, it runs through a checklist: is the market open? Is this pair on today's schedule? Is there a news event coming? Is the spread reasonable? Is there already an open position on a correlated pair? If any of those fail, it moves on.

If the pair passes all the gates, it looks at the 4-hour chart to figure out the overall direction, then drops to the 5-minute chart to find a Break of Structure. If both timeframes agree, it checks 20 confluence factors — things like fibonacci retracement zones, order blocks, fair value gaps, kill zone timing, and rejection candles. If enough factors confirm the setup, the AI runs two separate models (XGBoost and Random Forest). If both models agree and both are above the confidence threshold, the trade fires.

One thing worth saying upfront: the bot is selective. It will go hours without trading. That's not a bug. It's the whole point — only take the setups that actually make sense.

---

## Quick links

- [Setup & Installation](SETUP.md)
- [System Architecture](ARCHITECTURE.md)
- [Trading Strategy](STRATEGY.md)
- [Risk Management](RISK_MANAGEMENT.md)
- [Dashboard Guide](DASHBOARD.md)
- [Telegram Commands](TELEGRAM.md)
- [Cloud Storage](CLOUD_STORAGE.md)
- [Troubleshooting](TROUBLESHOOTING.md)

---

## Current status

The bot went live in late April 2026 on an Exness demo account with $5,000. The first week was rough — which was expected, because the AI models had no real trade data to learn from yet. They were running on historical simulations only. Every Sunday the models retrain on the previous week's actual closed trades, so they improve over time. Week one is always the hardest. By week four or five, the models start developing a proper understanding of how these pairs actually behave on this broker.

---

## A note on expectations

This isn't a get-rich-quick system. It targets a 3:1 reward-to-risk on every trade and only needs to be right about 35% of the time to be profitable. The goal is consistency over time, not massive wins in a single session. If you're looking for a bot that trades every five minutes and goes 10× in a month, this isn't it. If you want something that behaves like a disciplined trader who knows when to sit on their hands, you're in the right place.
