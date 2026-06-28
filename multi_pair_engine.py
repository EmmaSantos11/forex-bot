import pandas as pd

def merge_pairs_data(pairs_data):
    all_data = []

    for pair_name, df in pairs_data.items():
        df = df.copy()
        df["pair"] = pair_name
        all_data.append(df)

    combined = pd.concat(all_data)
    combined = combined.sort_index()

    return combined


def run_multi_pair_backtest(combined_df, initial_balance=400):

    balance = initial_balance
    open_positions = []

    equity_curve = []
    trades = []

    MAX_OPEN_TRADES = 2
    MAX_RISK_TOTAL = 0.03
    RISK_PER_TRADE = 0.01
    COOLDOWN = 20

    last_trade_index = {}

    for i in range(len(combined_df)):

        row = combined_df.iloc[i]

        signal = row["Signal"]
        price = row["Close"]
        high = row["High"]
        low = row["Low"]
        atr = row["ATR"]
        pair = row["pair"]

        # ======================
        # CLOSE TRADES
        # ======================
        new_positions = []

        for pos in open_positions:

            if pos["pair"] != pair:
                new_positions.append(pos)
                continue

            if pos["type"] == "BUY":

                if low <= pos["sl"]:
                    balance -= pos["risk"]
                    trades.append(-pos["risk"])

                elif high >= pos["tp"]:
                    profit = pos["risk"] * pos["rrr"]
                    balance += profit
                    trades.append(profit)

                else:
                    new_positions.append(pos)

            elif pos["type"] == "SELL":

                if high >= pos["sl"]:
                    balance -= pos["risk"]
                    trades.append(-pos["risk"])

                elif low <= pos["tp"]:
                    profit = pos["risk"] * pos["rrr"]
                    balance += profit
                    trades.append(profit)

                else:
                    new_positions.append(pos)

        open_positions = new_positions

        # ======================
        # RISK CONTROL
        # ======================
        total_risk = sum(p["risk"] for p in open_positions)

        pair_has_trade = any(p["pair"] == pair for p in open_positions)
        last_idx = last_trade_index.get(pair, -1000)

        # ======================
        # OPEN TRADE
        # ======================
        if (
            signal != 0
            and not pd.isna(atr)
            and not pair_has_trade
            and len(open_positions) < MAX_OPEN_TRADES
            and total_risk < balance * MAX_RISK_TOTAL
            and (i - last_idx) > COOLDOWN
        ):

            risk = balance * RISK_PER_TRADE

            if signal == 1:
                sl = price - atr * 1.2
                tp = price + atr * 3

                open_positions.append({
                    "type": "BUY",
                    "sl": sl,
                    "tp": tp,
                    "risk": risk,
                    "rrr": 3,
                    "pair": pair
                })

            elif signal == -1:
                sl = price + atr * 1.2
                tp = price - atr * 3

                open_positions.append({
                    "type": "SELL",
                    "sl": sl,
                    "tp": tp,
                    "risk": risk,
                    "rrr": 3,
                    "pair": pair
                })

            last_trade_index[pair] = i

        equity_curve.append(balance)

    return balance, trades, equity_curve