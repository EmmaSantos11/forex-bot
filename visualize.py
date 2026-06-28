import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("data/forex_data.csv", index_col=0)
df.index = pd.to_datetime(df.index)

plt.figure()
plt.plot(df["Close"])
plt.title("EUR/USD Price")
plt.xlabel("Time")
plt.ylabel("Price")
plt.show()