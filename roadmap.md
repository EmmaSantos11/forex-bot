# 🗺️ Mr Bera's_Bot — Project Roadmap
*Last Updated: 12 May 2026*

---

## ✅ Phase 1: Core Infrastructure
- [x] MT5 integration — stable connection, data streaming, auto-reconnect watchdog
- [x] Multi-timeframe engine — 5M, 1H, 4H sync via `resample_timeframes()`
- [x] Full indicator suite — SMA, RSI, ATR, MACD, Bollinger Bands, ADX, DI+/DI-
- [x] Portfolio manager — dynamic lot sizing, balance tiers, drawdown tracking
- [x] Trade execution — `live_trader.py` with dynamic fill mode detection
- [x] Security — credentials in `.env`, never hardcoded
- [x] Spread filter — per-instrument thresholds (forex 3 pips, gold 0.04%, BTC 0.20%)

---

## ✅ Phase 2: Strategy — ICT / SMC / Market Mechanics
- [x] 4H market structure gate — SMA7 + SMA50 + MA70 triple confirmation
- [x] 5M Break of Structure (BOS) — swing high/low detection
- [x] ADX market regime — RANGING (skip), TRANSITIONING (tight filter), TRENDING (full)
- [x] Fibonacci OTE zones — 50–78.6% retracement on 4H swing
- [x] Liquidity sweep detection — stop hunt before BOS (highest quality signal)
- [x] Order block detection — price retesting last opposing candle (POI)
- [x] Fair Value Gap (FVG) retest — price inside imbalance zone
- [x] Change of Character (CHoCH) — early structural reversal signal
- [x] Turtle Body Soup (TBS) — body-level liquidity sweep (stronger than wick)
- [x] Inversion FVG (IFVG) — violated gap zone that flips its POI role
- [x] Breaker Block — failed order block flipped to opposite POI
- [x] Key level proximity — round numbers + previous-day high/low
- [x] Confluence scoring — 20-factor system, trade fires above min score
- [x] Displacement — 3+ consecutive large-bodied institutional delivery candles
- [x] CISD — Change in State of Delivery (single dominant candle breaks swing extreme)
- [x] Golden Zone — price within 0.618 ± 5% Fibonacci band on 4H swing
- [x] ICT Kill Zones — London (07–09 UTC), NY (13–15 UTC), Asian (01–03 UTC)
- [x] Master Candle Range Break — closes beyond widest-range candle in lookback
- [x] Rejection Candle — pin bar (wick ≥ 55%) or engulfing candle at POI
- [x] Daily Bias (PO3) — entry in discount for buys, premium for sells
- [x] SMT Divergence — cross-pair divergence (EURUSD/GBPUSD, AUD/NZD, XAU/AUD etc.)
- [x] Pairs removed after backtest: GBPUSD (PF 0.84), USDJPY (PF 1.0), AUDUSD (PF 1.05)

---

## ✅ Phase 3: ML / AI Engine
- [x] XGBoost + Random Forest dual ensemble — both models must agree before entry
- [x] Per-pair models — 8 individual model pairs (XGBoost + RF each)
- [x] Global fallback model — used when per-pair model not available
- [x] Class imbalance fix — `scale_pos_weight` (XGBoost), `class_weight=balanced` (RF)
- [x] Win recall metric — real measure of model quality (not just accuracy)
- [x] Live trade data feedback — Sunday retrain blends live trades at 3× weight
- [x] Sunday auto-retrain pipeline — runs before market open automatically
- [x] Confidence gate — 75% minimum standard hours, 80% evening (18–22 UTC)
- [x] ICT features → ML input — 20-feature set (7 indicators + 12 ICT binary patterns
      + normalized score). Models now learn WHICH ICT combinations predict wins,
      not just whether a threshold was crossed.
- [x] Training timeframe fix — retrain now fetches 5M data (~60K bars per pair)
      instead of H1 (~5K bars). Training and live inference now operate at the
      same scale — ICT lookbacks, BOS windows, and ATR values are consistent.
- [x] RRR alignment — training labels and live execution both use 1:3 base RRR;
      lookforward window extended 60 → 100 bars to capture slow-moving outcomes
- [x] ICT score always computed — previously only ran when AI signal != 0,
      meaning score was 0 whenever the model had no conviction; now computed
      every cycle regardless of signal, so diagnostic logging and alerts are accurate
- [x] Training crash fix (SMA_7 KeyError) — `add_features()` was called before
      `add_indicators()` in both retrain and live inference; ordering corrected
- [x] **ICT score source fix** — `get_signal()` was reading `generate_signals_ict()`
      which only writes a non-zero `ICT_Score` on bars with a live BOS (~2–5% of bars).
      The last bar almost never has a live BOS → score was always 0 → ICT gate
      blocked every single trade. Fixed: now reads `ict_score_norm × 20` from
      `add_ict_features()` output, which is computed on every bar based on 11 binary
      factors. ICT gate now functions correctly.
- [x] **4H data undersampling fix** — resampling 500 5M bars produces only ~10 4H bars,
      making SMA50 and MA70 meaningless and causing a training/inference mismatch
      (retrain uses ~520 4H bars; live was using 10). Fixed: `get_signal()` now fetches
      500 H4 + 400 H1 bars directly from MT5, matching the 85-day retrain window.
      Features `ict_structure_bull`, `ict_ote`, and `ict_golden_zone` are now
      consistent between training and live inference.

---

## ✅ Phase 4: Risk Management
- [x] ATR-based SL — 1.5× ATR stop loss per trade
- [x] RRR — 1:3 default, 1:5 at ≥87% confidence + ICT ≥ 8, 1:10 at ≥92% + ICT ≥ 10
- [x] Partial close at 1R — closes 50% of position, moves SL to breakeven
- [x] 3-hour SL cooldown per pair — no re-entry after a stop-loss
- [x] Max 2 trades per pair per day
- [x] Daily loss limit — 5% (demo/personal), 4% (prop firm)
- [x] Total drawdown cap — 12% (demo), 10% (personal), 7% (prop firm)
- [x] Max 3 daily losses guard — stops trading after 3 SL hits in one day
- [x] Session kill-zone budget — max 3 trades in London KZ (07–12 UTC),
      max 3 trades in NY KZ (13–18 UTC), max 1 outside prime windows.
      Prevents front-loading all trades before noon.
- [x] 90-min global inter-trade cooldown — replaced 60 min, spreads trades naturally
- [x] Correlation filter — blocks opening if correlated pair already open
- [x] Monday gap guard — no new trades during market open hour
- [x] Friday auto-close — prop firm mode closes all positions before weekend
- [x] Dead zone — no trading 21:00–23:00 UTC (post-NY, pre-Sydney)
- [x] Evening caution — higher confidence required 18:00–22:00 UTC
- [x] Power outage recovery — position cache synced from MT5 on every restart

---

## ✅ Phase 5: Intelligence & Filtering
- [x] ForexFactory news calendar — 30-min blackout around HIGH-impact events
- [x] News direction inference — forecast vs actual → bullish/bearish verdict
- [x] News pair signal — maps currency direction to BUY/SELL on each active pair
- [x] Session awareness — London, NY, Tokyo, Sydney with overlap detection
- [x] Daily pair schedule — different pairs suited to each weekday
- [x] Prop firm / Demo / Personal mode — auto-detected from MT5 account type

---

## ✅ Phase 6: Notifications & Logging
- [x] Telegram bot — trade entry, exit, P&L, confidence, session in every alert
- [x] Admin vs user message split — `start_bot.bat` only shown to admin
- [x] Weekly performance report — every Monday to all users
- [x] News alerts — upcoming (≤30 min) and released events sent to users **and channel**
- [x] User registry — anyone who `/start`s the bot gets registered
- [x] Admin command scope — `/addpair`, `/removepair`, `/close` hidden from users
- [x] Crash notifications — `run_bot.py` alerts all users on crash + auto-restart
- [x] Trade logging — CSV log of every trade with AI confidence and spread
- [x] ATH and milestone alerts — posted to channel when balance hits new highs
- [x] Telegram channel automation:
      · Daily London open briefing (07:00 UTC weekdays) — focus pairs + reasoning
      · ICT setup alert (≥ 10/20 factors, 4h cooldown) — "setup forming" before entry
      · Sunday prep post (20:00 UTC) — week preview, Monday pairs, retrain status
      · Weekly ICT/SMC lesson (Wednesday 10:00 UTC) — 6-topic rotation
      · Trade autopsy (every SL hit) — entry conditions, AI score, honest post-mortem
- [x] **Trade setup charts** — on every trade entry a TradingView-style dark-theme
      candlestick chart is auto-generated (last 80 5M bars, entry/SL/TP dashed lines,
      shaded profit/risk zones, direction arrow, AI confidence + ICT score in title)
      and sent to the Telegram channel as a PNG image so followers can visually
      review the exact setup the bot acted on.
- [x] **Trade outcome charts** — when a position closes (TP / SL / BE), an outcome
      chart is generated showing the same 80-bar window with the exit marker overlaid
      (★ for TP, ✖ for SL, ⬥ for breakeven) and sent to the channel alongside the
      P&L result message. Built with `matplotlib` (Agg backend, headless).

---

## ✅ Phase 7: Dashboard & Remote Access
- [x] Streamlit dashboard — 6 pages with dark design + 4 colour themes (added Day/light)
- [x] Responsive layout — adapts cleanly to mobile, tablet, and desktop via CSS
- [x] About page — bot overview, strategy explanation, risk management, schedule,
      tech stack, go-live roadmap phases, disclaimer
- [x] Home — balance, equity, open positions, equity curve, session indicators
- [x] Performance — equity/drawdown chart, win rate by pair, P&L heatmap
- [x] AI Training — per-pair model status, win recall chart, retrain schedule,
      interactive retrain button with pair selection + live log output
- [x] Blog — auto-generated posts from trade history, news, retrain events
- [x] Trade With Us — CTA page linking to Telegram channel
- [x] Bot mood widget — 12 animated emoji states (scanning, watching, in trade,
      won, lost, retraining, weekend rest, etc.) on Home page
- [x] Market-closed detection — signal scanner goes offline on weekends,
      trading sessions show CLOSED, no stale DAILY LIMIT data shown
- [x] Bot image integrated — Mr Bera's_Bot logo across sidebar, home, CTA page
- [x] Creator credit — sidebar links to personal site
- [x] ngrok tunnel — phone URL printed at startup, accessible from anywhere
- [x] One-click launcher — `start_bot.bat` → `launch.py` starts everything

---

## ✅ Phase 8: Model Quality
- [x] `logs/model_recall.csv` — retrain writes recall scores after every pair trains
- [x] Hyperparameter tuning — XGBoost: n_estimators 400, max_depth 4, gamma,
      reg_alpha, reg_lambda, min_child_weight. RF: n_estimators 300, max_depth 5.
- [x] Recall scores in Telegram — retrain notification shows XGB/RF recall per pair

---

## 🔲 Phase 9: Validation & Live Preparation
- [ ] **Walk-forward validation** — train on months 1–5, test on month 6, shift
      forward one month at a time. Gives a realistic picture of strategy edge
      before committing real capital. More honest than a single train/test split.
- [ ] **Retrain with new 20-feature set** — first retrain since ICT features and
      5M training data were added. Models need to learn the new feature space.
      Run from the AI Training page or terminal.

---

## ✅ Phase 10: Public Launch
- [x] **Telegram channel live** — channel is active; Trade With Us page links to it.
      Subscribers receive every trade alert, daily briefings, and educational posts.
- [x] **Trade With Us page updated** — channel + website links prominent; managed
      trading via admin DM; "let bot trade for you" enrollment hidden until ready.
- [ ] **VPS hosting** — move bot off local machine to a cloud server so it runs 24/7
      without requiring a laptop to stay plugged in. (~$10–20/month)
- [ ] **Fixed public domain** — permanent URL for the dashboard (ngrok paid plan or
      Streamlit Cloud deploy). Currently restarts reset the ngrok URL.

---

## ✅ Phase 11: Managed Accounts Infrastructure
- [x] **Managed accounts storage** — `managed_accounts.json` (gitignored) holds up
      to 5 client MT5 credentials; admin is the only one who can add/edit/remove.
- [x] **Admin panel** (`pages/6_Admin.py`) — password-protected page (ADMIN_PASSWORD
      in .env). Two tabs: Managed Accounts (CRUD for 5 client slots) and Bot Users
      (view/remove registered users). Admin never counted toward user limit.
- [x] **Managed Accounts page** (`pages/5_Managed_Accounts.py`) — public page showing
      live stats for all active client accounts (balance, equity, open P&L, positions).
      Refresh button fetches live data from each client's MT5 account.
- [x] **5-user bot limit** — non-admin Telegram users capped at 5. Users beyond the
      limit are redirected to the channel and told to contact the admin for a slot.
- [ ] **Multi-account bot trading** — separate bot process per managed account so each
      client's account is traded simultaneously. (Each instance uses client MT5 creds.)
- [ ] **Client notifications** — when bot trades a managed account, send the trade
      alert to that client's Telegram ID (stored in managed_accounts.json).

---

## 🔲 Phase 11: Real Account
- [ ] **Real account migration** — after walk-forward validation + 50+ live demo
      trades with solid risk-adjusted metrics, switch to real money.
      Verify lot sizing at live spreads before committing full capital.
- [ ] **Managed account service** — Telegram-based managed accounts, custom
      strategy configuration, performance-based pricing. Phase 4 of the
      go-live roadmap shown on the About page.
