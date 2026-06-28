from core.multi_timeframe import resample_timeframes
from strategy import add_indicators, add_features, generate_signals_multi_tf
from backtest import run_backtest
from core.fetch_data import load_csv

from ml_model import prepare_ml_data, train_model, generate_ai_signals

import matplotlib.pyplot as plt

# Helper function to infer point value for backtesting
def infer_point_value(symbol):
    if "JPY" in symbol.upper():
        return 0.01
    elif "BTC" in symbol.upper():
        return 1.0 # Assuming 1.0 for BTCUSD, adjust if needed
    else:
        return 0.0001

# LOAD
df = load_csv("data/forex_data.csv")
# Assuming "EURUSD" for this single backtest file for point value inference
symbol_for_backtest = "EURUSD" 
point_value_for_ml = infer_point_value(symbol_for_backtest)

# TIMEFRAMES
tfs = resample_timeframes(df)
# FEATURES
for tf in tfs:
    tfs[tf] = add_indicators(tfs[tf])
    tfs[tf] = add_features(tfs[tf])

# SIGNALS
df_final = generate_signals_multi_tf(tfs)

# ML
X, y = prepare_ml_data(df_final, point_value=point_value_for_ml)
model = train_model(X, y)
df_ai = generate_ai_signals(df_final, model) if model else df_final

# BACKTEST
results, trades, equity_curve = run_backtest(df_ai)

print("\n===== AI BACKTEST RESULTS =====")
for k, v in results.items():
    print(f"{k}: {v:.2f}" if isinstance(v, float) else f"{k}: {v}")

# PLOT
plt.figure()
plt.plot(equity_curve)
plt.title("Equity Curve (AI Model)")
plt.show()