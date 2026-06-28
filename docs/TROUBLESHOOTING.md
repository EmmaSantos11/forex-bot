# Troubleshooting

A collection of the issues that have actually come up during development and live testing, with what caused them and how to fix them.

---

## The bot starts but doesn't trade

This is usually one of a few things.

**Check the signal scanner on the dashboard.** If everything shows DAILY_LIMIT, the 6-trade cap for the day was already hit and the bot is done for today. If everything shows NOT ACTIVE TODAY, it's a weekend or none of the scheduled pairs are available on your broker.

**Check the session.** The dead zone runs from 21:00 to 23:00 UTC every day. During that window the bot deliberately doesn't trade — liquidity is too thin and spreads are too wide. If you start the bot in that window and wonder why nothing happens, wait until Sydney opens.

**Check ADX.** If the 4-hour ADX is below 20 across all active pairs, the market is ranging and the bot is intentionally sitting out. This can last hours. It's not broken — it's doing exactly what it should.

**The DAILY_LIMIT shows even though it's a new day.** The scanner state in `logs/signal_scan.json` persists between sessions. When the bot restarts, it takes one full cycle (30 seconds) to clear the stale state and write fresh values. Just wait a cycle.

---

## MT5 disconnects frequently

This tends to happen when the machine goes to sleep, or when MT5's connection to the broker drops during volatile periods. The bot has a reconnection watchdog built in — every cycle it checks if the terminal is still connected and tries to reconnect if not.

If disconnects are happening constantly, check your MT5 terminal directly. If the chart data looks frozen or the login indicator at the bottom is red, the broker connection itself has an issue. Usually closing MT5 and reopening it fixes it.

On Windows, make sure power settings aren't putting the machine to sleep. Go to Power Options and set the "Turn off display" and "Put the computer to sleep" options to Never when you're running the bot.

---

## "Can't start new thread" crash in the bot window

This was a real issue caused by Python 3.14's changes to the internal thread pool, which broke joblib (the library that manages parallel computation in scikit-learn). The fix is already in the code — Random Forest's `n_jobs` is forced to 1 in both training and inference so it never tries to spin up a thread pool. If you're seeing this error, check that your `ml_model.py` has `n_jobs=1` in the `RandomForestClassifier` constructor and `if hasattr(model, "n_jobs"): model.n_jobs = 1` in `_predict_one()`.

---

## Sunday retrain runs but says "no training data"

The most common cause is that the broker symbol for EURUSD on your account isn't just "EURUSD" — Exness uses "EURUSDm", for example. The retrain pipeline tries to resolve the correct symbol automatically by checking `active_pairs` first, then scanning all MT5 symbols. If that still fails, it tries three times with 60-second gaps before giving up.

If it's failing consistently, open MT5, go to Market Watch, and check what the actual symbol name is for EURUSD on your broker. If it doesn't match the pattern the resolver expects, you can add it manually as the first entry in `PREFERRED_PAIRS` at the top of `main.py`.

---

## The dashboard shows stale data

The dashboard reads from `logs/signal_scan.json` and `logs/bot_mood.json`. If those files aren't being updated, it means either the bot isn't running or the bot is in a state where it's sleeping (market closed, dead zone, etc.) and only updates the file every 5 minutes instead of every 30 seconds.

If the bot's console window looks active but the dashboard is stuck, try refreshing the browser manually. Streamlit's auto-refresh occasionally gets stuck in some browser configurations. A hard refresh (Ctrl+Shift+R) fixes it.

---

## Streamlit throws errors about module imports

If the dashboard crashes on startup with an import error, it's usually because the virtual environment isn't activated. Make sure you always run through `start_bot.bat` which handles the venv activation. Running `streamlit run streamlit_app.py` directly from a plain terminal won't have the venv's packages available.

---

## Trades open but Telegram notifications aren't arriving

First check that `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` are correct. The chat ID needs to be your personal user ID, not a group ID. You can verify it by messaging `@userinfobot` on Telegram.

If the credentials look right, check whether the bot's Telegram listener thread is running. In the console, you should see `📱 Telegram listener active.` near startup. If that line is missing, the thread might have crashed silently. Restarting the bot brings it back.

Also check that you've done `/start` in the bot's chat. Without that, your user ID isn't in the registry and you won't receive broadcast messages (trade entries, closures, etc.).

---

## Models aren't loading after the Sunday retrain

After the retrain completes, the bot immediately calls `load_model()` and `load_rf_model()` to hot-swap the fresh models into the running process. If the console shows the retrain completing but the AI confidence values look unchanged, check the `models/` folder to confirm the `.pkl` files have a fresh modification timestamp.

If the files are there but the bot isn't picking them up, a manual restart after the retrain will definitely load the new models. The hot-swap is a convenience — the bot always loads fresh on restart.

---

## Cloud sync fails with "401 Unauthorized"

Your Hugging Face token is either wrong, expired, or was revoked. Go to huggingface.co/settings/tokens, create a new Write token, and update `HF_TOKEN` in `.env`. Restart the bot.

---

## The bot opened a trade on a pair that wasn't on today's schedule

This shouldn't happen after the schedule filter fix, but if it does: check what weekday it was in UTC (not local time — the bot uses UTC throughout). If it was Wednesday UTC, all pairs are active. If the bot traded on Monday but the pair wasn't in Monday's schedule, check that `_strip_suffix()` is correctly removing the broker suffix from the symbol name so it matches the base name in `DAILY_PAIR_SCHEDULE`.

---

## Performance looks wrong or missing on the Performance page

The Performance page pulls deal history from MT5 using `mt5.history_deals_get()`. If you recently reset your demo account or switched accounts, the history might not be there. MT5 stores deal history per account, so a new demo account starts with an empty history tab.

Also check that trades are actually closing. If a position is open but hasn't been closed yet, it won't appear in the deal history and won't count toward any of the metrics.
