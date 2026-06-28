from core.multi_timeframe import resample_timeframes
from core.fetch_data import load_csv

from strategy import add_indicators, add_features, generate_signals_multi_tf
from ml_model import prepare_ml_data, train_model, generate_ai_signals

from multi_pair_engine import merge_pairs_data, run_multi_pair_backtest

import matplotlib.pyplot as plt

# Helper function to infer point value for backtesting
def infer_point_value(symbol):
    if "JPY" in symbol.upper():
        return 0.01
    elif "BTC" in symbol.upper():
        return 1.0 # Assuming 1.0 for BTCUSD, adjust if needed
    else:
        return 0.0001

pairs = {
    "EURUSD": "data/EURUSD.csv",
    "GBPUSD": "data/GBPUSD.csv",
    "BTCUSD": "data/BTCUSD.csv"
}

pairs_data = {}

for pair_name, file_path in pairs.items():

    print(f"\n🚀 Preparing {pair_name}...")

    try:
        df = load_csv(file_path)
    except Exception as e:
        print(f"❌ Skipping {pair_name}: {e}")
        continue

    # Determine point_value for backtesting for the current pair
    point_value_for_ml = infer_point_value(pair_name)

    tfs = resample_timeframes(df)

    for tf in tfs:
        tfs[tf] = add_indicators(tfs[tf])
        tfs[tf] = add_features(tfs[tf])

    df_final = generate_signals_multi_tf(tfs)

    X, y = prepare_ml_data(df_final, point_value=point_value_for_ml)
    model = train_model(X, y)

    if model:
        df_final = generate_ai_signals(df_final, model)

    pairs_data[pair_name] = df_final

if not pairs_data:
    print("❌ No valid data loaded.")
    exit()

combined_df = merge_pairs_data(pairs_data)

final_balance, trades, equity_curve = run_multi_pair_backtest(combined_df)

print("\n===== TRUE MULTI-PAIR RESULTS =====")
print(f"Final Balance: ${final_balance:.2f}")
print(f"Total Trades: {len(trades)}")

plt.plot(equity_curve)
plt.title("Equity Curve")
plt.show()