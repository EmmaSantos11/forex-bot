import pandas as pd
import os

def log_trade(trade, filename="logs/trade_log.csv"):
    os.makedirs("logs", exist_ok=True)
    df = pd.DataFrame([trade])

    if not os.path.exists(filename):
        df.to_csv(filename, index=False)
    else:
        df.to_csv(filename, mode='a', header=False, index=False)