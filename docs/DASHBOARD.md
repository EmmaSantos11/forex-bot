# Dashboard Guide

The dashboard runs at `http://localhost:8501` and is built with Streamlit. It refreshes automatically every 30 seconds without you needing to do anything. If you've set up ngrok, you can access the exact same dashboard from your phone using the URL printed in the launcher console.

There are five pages. Here's what each one shows and why it's there.

---

## Home

The home page is the one you'll spend most of your time on. Everything important about the bot's current state is here.

**Bot mood** sits at the top. It's a single card that changes based on what the bot is doing: scanning, watching a setup build, confirming a signal, placed a trade, won, lost, retraining. It also shows the detail — which pair, what confidence level, what the ICT score was. Instead of digging through console logs to understand why the bot did something, the mood card gives you a plain-language summary in real time.

**Account metrics** sit below that: balance, equity, open profit, total drawdown percentage, and today's running P&L. These pull directly from MT5 every refresh cycle.

**Signal scanner** is the main table. It shows every active pair and what the bot is currently thinking about each one. The status column tells you exactly why the bot isn't trading a pair right now — WATCHING means it's scanning, PENDING means a signal appeared and it's waiting for the second confirmation cycle, LOW_CONF means the setup scored but AI confidence wasn't high enough, NEWS means a high-impact event is blocking it, DAILY_LIMIT means it already traded that pair enough today, NOT ACTIVE TODAY means it's not on today's schedule. The confidence bar shows the combined AI confidence as a visual percentage. If a pair is in a kill zone and showing a PENDING status, that's worth paying attention to.

**Open positions** shows all currently open trades with their entry price, current P&L, and trailing SL status.

**News countdown** pulls from the ForexFactory calendar and shows any HIGH-impact events coming up in the next 8 hours, with a live countdown timer.

**Active session** shows which trading session is currently running (Sydney, Tokyo, London, New York, or an overlap) and which pairs are most active in it.

---

## Performance

This page is for reviewing how the bot is actually doing over time.

At the top are four fixed P&L cards that never change regardless of what date range you select: today's P&L, the last 3 days, the last 7 days, and the last 30 days. These give you a constant reference point.

Below that is a period selector (7 days to 180 days) that controls everything else on the page. The six summary metrics — total trades, win rate, profit factor, net P&L, average win, average loss — all respond to the selected period.

The **equity curve** plots your running balance over the period with a drawdown percentage overlaid on a second axis. If the equity line and drawdown line are both moving in bad directions at the same time, that's a signal something needs reviewing.

**Win rate by pair** and **net P&L by pair** are side-by-side horizontal bar charts. These quickly show which pairs are contributing and which ones are dragging. If a pair consistently shows red in P&L and low win rate, it might be worth removing it from the active list or waiting for more data.

**Daily trade activity** is a stacked bar chart showing wins and losses per day, with a summary table below. You can filter it from 1 day up to 30 days.

**P&L heatmap** shows average P&L by hour of day and day of week. After a few weeks of trading, patterns start to emerge — certain hours on certain days tend to be better or worse. This chart helps you see those patterns without manually going through trade records.

---

## AI Training

This page tracks how the machine learning models are evolving over time.

The top section shows the most recent retrain's results for each pair: XGBoost recall, Random Forest recall, and how many samples were in the training set. Recall measures how well the model identified actual winning trades — a recall of 0.65 means it caught 65% of the real winners. As the training set grows with real trade data, these numbers should trend upward.

The bottom chart plots recall history across every Sunday retrain. Week 1's models will look rough. By week 6 or 8, if the data is accumulating properly, you should see the trend line improving. This chart is why the patience advice in the README matters — it takes a few weeks to see meaningful model improvement.

---

## Signals

A detailed view of the current signal scanner state, showing ICT scores, confidence levels, regime status, and which confluence factors fired for each pair. Useful if you want to understand exactly why a trade did or didn't trigger.

---

## About

Project overview, the list of active pairs and why each was chosen, and the four-phase development roadmap showing what's been built and what's coming next. Useful if someone else is looking at the dashboard and wants context.

---

## Accessing from your phone

If ngrok is configured in your `.env`, the launcher console prints a URL that looks like `https://abc123.ngrok.io` during startup. Open that on any device, anywhere. It's a live tunnel to your local dashboard. The URL changes every time you restart the bot unless you're on a paid ngrok plan with reserved domains.

The dashboard is read-only — you can't click a button to place a trade or close a position from it. Those actions go through Telegram (see [Telegram Commands](TELEGRAM.md)).
