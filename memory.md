# Project Memory: AI-Powered Forex Trading Bot

## 📌 Project Vision
A robust, multi-timeframe trading system for MetaTrader 5 that uses technical analysis for signal generation and a Random Forest Machine Learning model for high-confidence filtering.

## 🏗️ Technical Architecture
- **Data Pipeline**: Logic to fetch from Binance (Crypto) and MT5 (Forex), with automated cleaning and multi-timeframe resampling (5m -> 15m, 30m, 1h, 4h).
- **Strategy**: Trend alignment across three timeframes (5m, 1h, 4h) using SMAs, RSI, and ATR.
- **ML Engine**: Random Forest Classifier trained to predict Trade Success (hitting TP before SL). Features include trend strength, momentum, and volatility.
- **Persistence**: Models are saved in `/models/` using `joblib` to prevent unnecessary retraining.
- **Dynamic Scaling**: Uses `symbol_info.point` to handle different decimal places for JPY, BTC, and standard Forex pairs automatically.
- **Analytics**: `visualize_equity.py` provides performance tracking by reading the `trade_history.csv` log.

## 🛠️ Operational Manual (How to run)
1. **Setup Environment**: `pip install -r requirements.txt`
2. **Data Prep**: Run `python data_collection.py` or `python core/data_collector.py` to populate the `data/` folder.
3. **Validation**: Run `python test_multi_pair.py` to verify the strategy and ML filter performance across multiple assets.
4. **Live Execution**: Ensure MT5 Terminal is open, then run `python run_live.py`.

## ✅ Development Checklist

### Phase 1: Core Stability (Current)
- [x] Implement Multi-Timeframe resampling logic.
- [x] Integrate ML Model (Random Forest) as a secondary filter.
- [x] Add Model Save/Load functionality (`joblib`).
- [x] Implement dynamic Pip/Point handling for JPY and BTC.
- [x] Implement a `.env` file for MT5 credentials to improve security.

### Phase 2: Execution Quality
- [x] Add "Slippage Control": Check if the current price has moved too far from the signal price before entering.
- [x] Implement Global Circuit Breaker: Stop trading if daily loss exceeds 5%.
- [x] Implement Trade Result Updater: Script to sync CSV with MT5 closed trade data.
- [ ] Add Telegram/Discord notifications for live trade executions.

### Phase 3: ML Optimization
- [ ] Feature Engineering: Add MACD and Bollinger Band width as ML features.
- [ ] Implement "Walk-Forward Validation" in backtesting.
- [x] Create an automated retraining script (retrain every Sunday or after 50 trades).

## ⚠️ Critical Notes
- **Symbol Matching**: Ensure MT5 symbols match the script (e.g., "EURUSD" vs "EURUSD.m").
- **Filling Mode**: The `live_trader.py` now dynamically detects filling modes (IOC/FOK) to prevent "Invalid Filling" errors common with different brokers.