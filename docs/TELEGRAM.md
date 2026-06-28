# Telegram Bot

The Telegram integration is how you interact with the bot when you're away from your machine. It works two ways: the bot sends you alerts automatically, and you can send it commands to get information or take action.

Every user who sends `/start` to the bot gets registered and receives all automated alerts going forward. The admin account (set via `TELEGRAM_CHAT_ID` in `.env`) gets a few extra commands that regular users don't have access to.

---

## Commands available to everyone

**`/start`**
Registers you and shows the full command list. If you've used the bot before, it welcomes you back. New users get a slightly different greeting.

**`/status`**
Returns a quick snapshot: current balance, equity, floating profit, and the maximum drawdown percentage since the bot started. Good for a quick check when you don't want to open the full dashboard.

**`/pairs`**
Lists all the pairs currently being traded. Useful to confirm a pair was added or removed correctly after using the admin commands.

**`/trades`**
Shows all currently open positions with their direction, lot size, and current P&L. If nothing is open, it tells you that too.

**`/history`**
Shows all closed trades from today: which pair, win or loss, how much. Totals at the bottom with win/loss count and net P&L for the day.

**`/market`**
Tells you whether the market is currently open or closed, which session is active (London, New York, overlap, etc.), and how long until the next open or close. Also shows which active pairs are best suited to the current session.

**`/today`**
Shows today's scheduled pairs and explains why those specific pairs were chosen for this day of the week. The reasoning is built into the schedule — Monday is different from Wednesday for good reasons, and this command explains them.

---

## Admin-only commands

These are only accessible to the account whose chat ID is set in `.env`.

**`/addpair SYMBOL`**
Adds a pair to the active list immediately. The bot picks it up on the next 30-second cycle — no restart needed. The symbol must match exactly what MT5 uses on your broker. Example: `/addpair EURAUD` or `/addpair EURAUDm` depending on your broker's naming.

**`/removepair SYMBOL`**
Removes a pair from the active list. Any open position on that pair is not affected — the bot just stops looking for new entries on it.

**`/close`**
Emergency command. Closes all open positions immediately at market price. Use this when you need to exit everything fast — before a news event you weren't expecting, or if something is going wrong that you want to stop before the bot figures it out on its own.

---

## Automatic alerts

These happen without you asking.

**Trade entered** — Sent to all registered users the moment an order is filled. Includes the pair, direction, lot size, entry price, stop loss, take profit, R:R ratio, confidence level, and the active trading session.

**Trailing stop moved** — When the trailing SL advances to a new step (+1R, +2R breakeven, +3R, etc.), you get a notification with the current profit level and where the SL moved to.

**Smart partial close** — If the reversal partial fires (high-confidence opposite signal detected while a trade is in profit), you get a notification explaining what triggered it, how much was locked in, and what lot size remains open.

**Trade closed** — Sent when any position closes for any reason: TP hit, SL hit, or breakeven. Includes the exit price, full P&L, and the reason for closure.

**High-impact news upcoming** — About 30 minutes before any HIGH-impact ForexFactory event affecting your active pairs, you get an advance warning with the event details, forecast vs previous, expected direction, and which of your pairs will be paused.

**News released** — When the actual data comes out, you get the actual figure alongside the forecast, the confirmed direction, and a list of setups to watch once the post-news buffer expires.

**Weekly report** — Every Monday morning, a full performance summary for the previous 7 days: trade count, win rate, net P&L, best and worst trade, and a per-pair breakdown.

**All-time high** — When the account balance hits a new peak, you get a message. Small celebration, keeps things motivating.

**Profit milestones** — At 10%, 15%, 25%, 50%, and 100% account growth, the bot flags it. In prop firm mode, hitting the 8% target triggers a hard stop and tells you to submit the account for evaluation.

**Bot started / stopped** — Startup and shutdown notifications go to all registered users so everyone knows when the bot is online and when it's not.

**Sunday retrain** — When the weekly retrain starts and when it finishes, with a full summary of which pairs trained successfully, their recall scores, and the total sample count.

**Cloud backup** — After each retrain, a confirmation that models were successfully uploaded to Hugging Face.

---

## Setting it up from scratch

If you haven't set up the Telegram bot yet:

1. Open Telegram and search for `@BotFather`
2. Send `/newbot`
3. Follow the prompts to name your bot and give it a username
4. Copy the token BotFather gives you and paste it as `TELEGRAM_BOT_TOKEN` in `.env`
5. To get your chat ID, message `@userinfobot` — it'll reply with your ID
6. Paste that as `TELEGRAM_CHAT_ID` in `.env`

That's it. The bot connects to Telegram on startup and immediately sends a launch notification.
