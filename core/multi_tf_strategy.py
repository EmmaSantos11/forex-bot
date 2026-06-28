def multi_timeframe_signal(tfs):

    df_5 = tfs["5min"]
    df_1h = tfs["1h"]
    df_4h = tfs["4h"]

    signals = []

    for i in range(len(df_5)):

        try:
            time = df_5.index[i]

            h1 = df_1h.loc[:time].iloc[-1]
            h4 = df_4h.loc[:time].iloc[-1]
            current = df_5.iloc[i]

            # TREND
            h1_up = h1["SMA_7"] > h1["SMA_50"]
            h4_up = h4["SMA_7"] > h4["SMA_50"]

            h1_down = h1["SMA_7"] < h1["SMA_50"]
            h4_down = h4["SMA_7"] < h4["SMA_50"]

            # RSI FILTER (ENTRY TIMING)
            rsi = current["RSI"]

            # BUY
            if h1_up and h4_up and current["SMA_7"] > current["SMA_50"] and rsi < 40:
                signals.append(1)

            # SELL
            elif h1_down and h4_down and current["SMA_7"] < current["SMA_50"] and rsi > 60:
                signals.append(-1)

            else:
                signals.append(0)

        except:
            signals.append(0)

    df_5["Signal"] = signals
    return df_5