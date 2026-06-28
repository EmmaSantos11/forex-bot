# Trading Strategy

The strategy is built on ICT (Inner Circle Trader) and SMC (Smart Money Concepts) principles, with a layer of machine learning on top to filter out the weak setups from the strong ones. The core idea is that retail traders consistently lose because they chase price, while institutions move price to wherever retail liquidity is sitting and then go the other direction. The bot is designed to trade with institutions, not against them.

---

## The three gates

Before any signal is considered, the bar has to pass three gates in sequence. Fail any one of them and the bot moves on.

### Gate 0 — Market Regime

The bot checks the 4-hour ADX (Average Directional Index) to understand what kind of market it's in. If ADX is below 20, the market is ranging — moving sideways with no real direction. In that environment, every break of structure is a fake-out, not a real move. The bot simply skips trading entirely during ranging conditions.

ADX between 20 and 25 means the market is transitioning — picking a direction but not fully committed yet. The bot still trades here but requires one extra confluence factor as insurance.

ADX at 25 or above means the market is trending. This is the ideal environment for the strategy, and the bot gives a free +1 score bonus just for the trend being in place.

### Gate 1 — 4H Market Structure

On the 4-hour chart, the bot checks three things simultaneously: price must be above (or below) the 50-period SMA, the 7-period SMA must be on the correct side of the 50, and price must be on the correct side of the 70-period moving average. All three have to agree on direction. This triple-confirmation filters out the mixed, choppy periods where indicators disagree with each other.

### Gate 2 — 5M Break of Structure

On the 5-minute chart, the bot looks for a candle that closes beyond the high (for buys) or low (for sells) of the previous 10 bars. This is the Break of Structure — the moment where the market has actually committed to moving in the 4H direction on a short-term basis. Without a BOS, there's no trigger, no matter how good everything else looks.

---

## Confluence scoring

Once all three gates pass, the bot scores the setup out of 20 factors. Each one that confirms adds a point. A setup needs at least 5 points to fire (4 during ICT kill zones — see below). Here's what it checks:

**Fibonacci OTE** — The Optimal Trade Entry zone sits between the 50% and 78.6% retracement of the most recent 4-hour swing. This is where price typically pauses before continuing in the trend direction. Being inside the OTE means you're not chasing the move.

**Golden Zone** — A tighter version of the OTE: specifically the 61.8% ±5% retracement level. ICT practitioners treat the 0.618 as the most precise entry within the OTE window. Being in the golden zone gets its own point on top of the general OTE score.

**RSI confirmation** — The bot checks RSI on both the 1-hour and 5-minute charts. For a buy, it wants to see momentum building (RSI > 50 on 5M) but not yet overbought (RSI < 60 on 1H). This stops the bot from buying into an exhausted move.

**MACD histogram** — The histogram must be pointing in the trade direction. A positive histogram on a buy setup means the short-term momentum is actually supporting the thesis.

**Liquidity sweep** — This is one of the most important factors. Before a real institutional move, smart money needs to grab the stop orders sitting above recent highs (for sells) or below recent lows (for buys). A wick that spikes past the swing level then closes back inside it is exactly that. If this happened right before the BOS, the setup quality goes up significantly.

**Turtle Body Soup** — A stronger version of the liquidity sweep. Instead of just the wick, the candle's actual body moves through the swing level before closing back inside. More traders got trapped, which means a more aggressive institutional push back in the other direction.

**Order Block** — The last bearish candle before a bullish move (or last bullish candle before a bearish move) becomes a Point of Interest. When price comes back to test that zone, institutions are often waiting with limit orders there. The bot identifies these zones and adds a point if current price is retesting one.

**Breaker Block** — When an order block gets fully broken through, it flips roles. A former supply zone becomes demand, and vice versa. Price retesting a breaker block from the new side is one of the cleaner ICT setups.

**Fair Value Gap** — A three-candle imbalance where the first candle's high doesn't overlap with the third candle's low (or vice versa). These gaps represent inefficient price delivery, and institutions tend to fill them on the way back. The bot detects FVGs in the recent lookback and checks if current price is inside one.

**Inversion FVG** — A regular FVG that was subsequently violated. Once violated, the gap zone flips: a former bearish imbalance (supply) becomes bullish demand after price trades above it. Price retesting from the new side gets a point.

**Change of Character (CHoCH)** — In a downtrend, price is making lower highs and lower lows. When it makes a higher low and then breaks above the last lower high, the character of the move has changed. This is often the earliest warning that a reversal is starting, before a full Break of Structure confirms it.

**Daily Bias (Power of Three)** — The bot checks whether the current price is in a discount relative to the daily candle's open. For buys, price should be below the daily open (in the lower half of the day's range). For sells, price should be above the daily open. This is ICT's Power of Three concept: accumulate in discount, expand through the open, distribute in premium.

**Displacement** — After a liquidity sweep, institutions need to show their hand through a series of large-bodied delivery candles. The bot looks for three or more consecutive candles where the body covers at least 55% of the full range and is 70% the size of the average candle. This is the "displacement" — the actual institutional delivery after the sweep.

**CISD (Change in State of Delivery)** — A single dominant candle whose body covers 70% or more of its full range and closes beyond a recent swing extreme. Brad Goh's concept: one candle that announces the shift in who controls price delivery.

**ICT Kill Zones** — The London open (07:00–09:00 UTC), New York open (13:00–15:00 UTC), and Asian open (01:00–03:00 UTC) are when institutional algorithms are most active. A sweep and BOS that happen during these windows carry significantly more weight than the same pattern at 16:00 UTC when volume is thin. During kill zones, the minimum confluence requirement drops from 5 to 4 — the timing itself is worth something.

**Master Candle Range Break** — The widest-range candle in the recent lookback period is the master candle. It represents peak institutional delivery. When subsequent price action breaks beyond its high or low in the trade direction, institutions have committed to carrying price further. This is expansion — the market is not consolidating anymore.

**Rejection Candle** — A pin bar (wick covers 55% or more of the total candle range) or a fully engulfing candle at the Point of Interest is physical evidence that one side tried to push price the wrong way and failed completely. The failure is the signal.

**Key Level Proximity** — Round numbers (00 and 50 levels) and the previous day's high and low attract disproportionate order flow. Being near one of these adds confluence.

---

## The AI layer

After the ICT scoring, the bot runs two separate machine learning models on the same data:

**XGBoost** is a gradient-boosted decision tree ensemble. It trained on 5,000+ historical hours of EURUSD data, then fine-tuned per pair on each pair's individual history. It's strong at identifying non-linear patterns in the feature set — things like "when RSI is here AND MACD is doing this AND the BB is positioned like that, the outcome tends to be X."

**Random Forest** is an independent ensemble of decision trees. It uses completely different internal logic from XGBoost, which is the whole point. Having two different algorithms evaluate the same setup and independently reach the same conclusion is much more meaningful than one very sophisticated algorithm making a guess.

A trade only fires when both models agree on direction AND both are above the 80% confidence threshold (85% during the London/NY evening overlap, 18:00–22:00 UTC). There's a secondary tier: if AI confidence is between 75% and 80% but the ICT confluence score is 6 or higher, the trade can still fire — the ICT analysis is backing up the AI's softer read.

---

## What the bot doesn't trade

Some of these are worth knowing because they explain why the bot sits quiet for long stretches:

The bot won't trade during **ranging markets**. When ADX says the market has no direction, every break of structure is a fake-out waiting to happen. It's better to sit out.

It won't trade within **30 minutes of a high-impact news event**. News moves are often violent, gap-prone, and completely divorced from technical structure. The reward isn't worth the risk.

It won't trade when **spreads are too wide**. This typically happens during the dead zone (21:00–23:00 UTC) or right before major news. Wide spreads eat into the target from the moment the trade opens.

It won't trade **correlated pairs simultaneously**. If EURUSD is already open, GBPUSD won't trade — they move too closely together, and having both open doubles the effective risk without doubling the opportunity.

It won't take a pair that **already hit a stop-loss today**. After an SL, the pair sits in a 3-hour cooldown. The market that just stopped you out is usually not ready to give you a clean setup again immediately.

---

## Why 3:1 reward-to-risk

The stop-loss is placed at 1.5× ATR from entry. The take-profit is at 4.5× ATR — three times the stop distance. At 3:1, the bot only needs to be right about 35% of the time to be profitable over a large sample. The trailing stop system means even a trade that doesn't hit the full TP often locks in partial gains along the way.

The previous version used 5:1, which sounds more impressive but was harder to hit in practice. Trades would get to +2R or +3R, then reverse before hitting the full TP, end up breakeven or worse. Reducing to 3:1 means taking real profit on trades that would have previously scratched out.
