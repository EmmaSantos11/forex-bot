# 🤖 AI-Powered Forex Trading Bot

A robust, multi-timeframe trading system built for **MetaTrader 5**. This bot combines traditional technical analysis with a **Random Forest Machine Learning** model to filter high-probability trades.

## 📌 Project Vision
The goal is to eliminate emotional trading by using trend alignment across multiple timeframes (5m, 1h, 4h) and validating every signal through an AI classifier trained on historical market behavior.

## 🧠 How It Works
- **Multi-Timeframe Engine**: Automatically resamples data to ensure trades are only taken when the short-term trend (5m) aligns with the long-term structure (1h, 4h).
- **ML Filtering**: Uses a Random Forest Classifier to predict the likelihood of a trade hitting its Take Profit (TP) before its Stop Loss (SL).
- **Risk Management**: Features a built-in `PortfolioManager` that handles dynamic lot sizing, ATR-based stops, and a global 5% daily loss circuit breaker.
- **Safety First**: Includes filters for market spread and slippage to prevent poor entries during high volatility.

## 🛠️ Tech Stack
- **Language**: Python 3.10+
- **Trading Platform**: MetaTrader 5
- **ML Library**: Scikit-Learn (Random Forest)
- **Data Handling**: Pandas, Numpy
- **Notifications**: Telegram Bot API

## 🚀 Getting Started (Testing)
To test the strategy and the ML model without running live:

1.  **Clone the Repo**:
    ```bash
    git clone <your-repo-url>
    cd forex_ai_bot
    ```
2.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
3.  **Prepare Data**:
    Place your historical CSV data in the `data/` folder or run the collection script:
    ```bash
    python data_collection.py
    ```
4.  **Run Backtest**:
    Execute the backtesting suite to see how the AI performs:
    ```bash
    python test_strategy.py
    ```

## 📈 Roadmap
- [x] Multi-Timeframe Trend Alignment
- [x] Random Forest Signal Filtering
- [x] Daily Circuit Breakers & Portfolio Risk Management
- [ ] Advanced Feature Engineering (MACD, Bollinger Bands)
- [ ] Live Dashboard with Streamlit

## ⚠️ Disclaimer
Trading Forex involves significant risk. This bot is for educational and research purposes. Always test thoroughly on a demo account before considering live deployment.

---
*Last Updated: October 2023*