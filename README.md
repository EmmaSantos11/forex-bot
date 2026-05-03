# Mr Bera's_Bot — Forex AI Trading Bot

> **Smarter Trades. Better Results.**

An AI-powered Forex trading bot with a full backtesting engine, multiple strategy implementations, and risk management. Built with Python.

---

## Features

| Module | Description |
|---|---|
| `backtest.py` | Full backtesting engine — equity curve, drawdown, Sharpe ratio, win rate |
| `strategy.py` | MA Crossover, RSI, and Bollinger Band strategies |
| `data_loader.py` | CSV loader + synthetic GBM data generator for testing |
| `main.py` | Run all three strategies and compare results |

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run backtest on synthetic EURUSD data
python main.py
```

Sample output:
```
  Mr Bera's_Bot — Forex AI Backtesting Engine
  ──────────────────────────────────────────

  Strategy: MA Crossover (10/30)
  ────────────────────────────────────────
  Initial Capital  :    $10,000.00
  Final Capital    :    $11,243.80
  Total Return     :      +12.44 %
  Max Drawdown     :        8.21 %
  ────────────────────────────────────────
  Total Trades     :           87
  Win Rate         :       54.02 %
  Profit Factor    :        1.342
  Sharpe Ratio     :        0.871
```

---

## Using Your Own Data

```python
from data_loader import load_csv
from backtest import Backtest
from strategy import moving_average_crossover

# CSV must have columns: time, open, high, low, close, volume
df = load_csv("data/EURUSD_H1.csv")

bt = Backtest(df, moving_average_crossover, initial_capital=10_000, lot_size=1_000)
results = bt.run()
bt.print_summary(results)
bt.plot_equity(results)
```

---

## Strategies

### 1. Moving Average Crossover
Classic dual-MA system. Buys when the fast MA crosses above the slow MA, sells on the reverse crossover.

### 2. RSI Strategy
Mean-reversion approach. Buys when RSI dips below the oversold threshold (default 30), sells when it exceeds overbought (default 70).

### 3. Bollinger Band Strategy
Buys when price touches the lower band (mean-reversion entry), sells when it touches the upper band.

---

## Roadmap

- [ ] Live trading via MetaTrader 5 API
- [ ] Machine learning signal filter (Random Forest)
- [ ] Telegram/Discord alert integration
- [ ] Web dashboard (Streamlit)
- [ ] Portfolio-level risk management

---

## Author

**Ohamadike Emmanuel Chidera** (Bera Santos)  
AI Developer · Prompt Engineer · Data Scientist  
[Portfolio](https://emmasantos11.github.io/my-portfolio) · [LinkedIn](https://www.linkedin.com/in/emmanuel-ohamadike-767195196/) · [GitHub](https://github.com/EmmaSantos11)
