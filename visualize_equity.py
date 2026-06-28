import pandas as pd
import matplotlib.pyplot as plt
import os

def plot_equity_curve(csv_path="data/trade_history.csv", initial_balance=1000):
    """
    Reads the trade history CSV and plots the cumulative equity over time.
    """
    if not os.path.exists(csv_path):
        print(f"❌ {csv_path} not found. Make sure you have trades logged and results synced.")
        return

    # Load the trade history
    df = pd.read_csv(csv_path)
    
    # Ensure the profit column exists and filter out incomplete/open trades
    if 'profit' not in df.columns:
        print("❌ 'profit' column not found in CSV. Run update_results.py to sync data from MT5.")
        return
        
    df = df.dropna(subset=['profit']).copy()
    
    if df.empty:
        print("⚠️ No closed trades with profit data available to plot.")
        return

    # Sort by timestamp to ensure chronological order
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp')

    # Calculate cumulative equity
    df['equity'] = initial_balance + df['profit'].cumsum()

    # Visualize
    plt.figure(figsize=(12, 6))
    
    x_axis = df['timestamp'] if 'timestamp' in df.columns else range(len(df))
    plt.plot(x_axis, df['equity'], marker='o', markersize=4, linewidth=2, color='#2ecc71', label='Equity Curve')
    plt.axhline(y=initial_balance, color='#e74c3c', linestyle='--', alpha=0.7, label='Starting Balance')
    
    plt.title(f"AI Trading Bot Performance: {len(df)} Trades", fontsize=14, fontweight='bold')
    plt.xlabel("Date/Time" if 'timestamp' in df.columns else "Trade #", fontsize=12)
    plt.ylabel("Account Balance ($)", fontsize=12)
    plt.legend(frameon=True, loc='upper left')
    plt.grid(True, linestyle=':', alpha=0.6)
    
    if 'timestamp' in df.columns:
        plt.gcf().autofmt_xdate()

    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    # Match the starting balance from your PortfolioManager or MT5 account
    plot_equity_curve(initial_balance=1000)