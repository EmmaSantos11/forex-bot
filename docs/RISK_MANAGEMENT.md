# Risk Management

Every decision in this system ultimately comes back to one question: is the account still in good shape? The trading logic can be as sophisticated as you want, but if the risk rules aren't tight, one bad week can wipe out a month of progress. These rules exist because of that.

---

## Position sizing

The bot never uses a fixed lot size. Every trade is sized based on two things: how far the stop-loss is from the entry price, and what percentage of the current account balance that risk represents.

The stop is placed at 1.5× ATR from the entry. ATR (Average True Range) measures how much a pair typically moves in a given period. Using it as the SL anchor means the stop adjusts automatically to current volatility — tighter when markets are quiet, wider when they're moving. A stop that's too tight gets hit by normal noise. A stop too wide takes too big a loss when it does get hit. 1.5× ATR sits in a reasonable middle.

Once the stop distance is known, the portfolio manager calculates the largest lot size that keeps the dollar risk on that trade within the account's risk tier. As the account grows, lot sizes grow with it — which is how compounding actually works.

---

## Daily loss limit

The bot stops opening new trades the moment the day's net loss reaches 5% of the account balance. This is the daily circuit breaker. Once it trips, the bot monitors positions but takes no new entries until midnight UTC resets the counter.

Five percent might seem conservative, but losing 5% in a single day typically means something went wrong — maybe the market had a sharp news-driven move, maybe multiple setups failed in a row. Either way, continuing to trade after that kind of day usually leads to revenge trading and makes things worse. The bot just stops.

---

## Total drawdown cap

Separate from the daily limit, there's a total drawdown cap of 12% from the account's peak balance. If the account loses 12% from its highest point, the bot halts entirely until manually reviewed.

This is the most important protection. It means that even in the worst case — a string of losing days — the maximum you can lose from the peak is 12%. The account stays alive to trade another day.

---

## The trailing stop system

Every trade that opens gets managed from the moment it becomes profitable. The trailing stop advances in five steps as the trade moves in your favour:

At **+1R profit** (the trade has moved one stop-distance in your favour), the SL moves to entry minus half the stop distance. You're no longer at full risk — you can now lose at most 0.5R.

At **+2R**, the SL moves to breakeven. The trade cannot close at a loss from here.

At **+3R**, the SL locks in half the initial risk amount as profit. Even if the trade reverses all the way back, you walk away positive.

At **+4R**, the SL locks in the full initial risk. From this point the position is free — you can only win more.

At **+5R**, the SL moves to entry plus 1.5× the stop distance, letting the trade run with a comfortable buffer behind it.

This means most winners that don't hit the full take-profit still close with some profit. The trailing system converts what would otherwise be a breakeven into a winner and a small win into a real win.

---

## Smart reversal partials

If the bot detects a high-confidence signal (≥85%) in the opposite direction of an open trade, and that trade is already sitting at $20 or more in floating profit, it closes 50% of the position immediately.

The reasoning: a strong signal in the other direction means the market is likely turning. If you've got $60 floating and the bot sees a reversal coming, waiting for TP to get hit might just mean watching that $60 go back to zero. Taking half off locks in $30 regardless of what happens next. The remaining half stays open with the trailing SL protecting it.

---

## Maximum trades per day

No more than 6 trades across all pairs in a single day. No more than 3 on any individual pair. After 6 total trades, the bot marks every pair as DAILY_LIMIT and stops scanning until the next day's reset.

This prevents over-trading. On a busy day with lots of signals, it's tempting to just keep entering. But trade number 7 after a long day is rarely as good as trade number 2 in the morning. The cap enforces discipline.

---

## Inter-trade cooldown

After any trade fires, there's a 60-minute global cooldown before the next one can open. This isn't about the individual pair — it's system-wide. It stops the bot from firing multiple trades in quick succession on different pairs when markets get choppy and signals start appearing everywhere.

---

## Loss cooldown per pair

After a stop-loss on any pair, that specific pair is locked out for three hours. The thinking here is straightforward: if a setup just failed, the market probably isn't ready to give you a clean one on the same pair immediately. Wait for conditions to reset.

---

## Daily loss guard

Separate from the percentage-based circuit breaker, if the bot hits 2 stop-losses in a single day it stops trading for the rest of that day regardless of what the balance shows. Two losses in a day is information — something isn't right with conditions today, whether that's unusual volatility, unclear structure, or just a bad day for the strategy. Stop, review, come back tomorrow.

---

## Prop firm mode

If you're running this on a prop firm challenge account, set `PROP_FIRM_MODE=true` in `.env`. This tightens everything:

- Daily loss limit drops from 5% to 4% (gives buffer before the firm's 5% hard cap)
- Total drawdown cap drops from 12% to 7% (buffer before the firm's 8% cap)
- Risk per trade becomes fixed at 0.5% instead of tier-scaled
- All positions close automatically on Friday at 20:30 UTC so nothing is held over the weekend
- Trading pauses at 8% profit (the typical prop firm target)

In personal/demo mode these rules are more relaxed because you're trading your own account and the only person affected by the risk is you.
