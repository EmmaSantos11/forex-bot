"""
Mr Bera's_Bot — Entry point.

Run a full backtest on synthetic EURUSD H1 data using all three strategies
and print a comparison table.

Usage:
    python main.py

To use your own CSV data:
    df = load_csv("data/EURUSD_H1.csv")
"""
from data_loader import generate_demo_data, load_csv
from backtest import Backtest
from strategy import moving_average_crossover, rsi_strategy, bollinger_band_strategy

INITIAL_CAPITAL = 10_000.0
LOT_SIZE        = 1_000.0


def run_strategy(name, df, strategy_fn, **kwargs):
    bt = Backtest(df, strategy_fn, INITIAL_CAPITAL, LOT_SIZE, strategy_kwargs=kwargs)
    results = bt.run()
    print(f"\n{'='*44}")
    print(f"  Strategy: {name}")
    bt.print_summary(results)
    bt.plot_equity(results)
    return results


if __name__ == "__main__":
    print("\n  Mr Bera's_Bot — Forex AI Backtesting Engine")
    print("  " + "─" * 42)

    # Use synthetic data — replace with load_csv("your_file.csv") for live data
    df = generate_demo_data(n_bars=3000, start="2023-01-01", freq="1h")

    results = {}
    results["MA Crossover"]      = run_strategy("MA Crossover (10/30)",   df, moving_average_crossover, fast=10, slow=30)
    results["RSI Strategy"]      = run_strategy("RSI Strategy (14)",       df, rsi_strategy,              period=14)
    results["Bollinger Bands"]   = run_strategy("Bollinger Bands (20,2)",  df, bollinger_band_strategy,   period=20)

    # Comparison table
    print(f"\n{'STRATEGY COMPARISON':^44}")
    print("─" * 44)
    print(f"  {'Strategy':<22} {'Return':>7} {'WinRate':>8} {'PF':>6}")
    print("─" * 44)
    for name, r in results.items():
        print(f"  {name:<22} {r.total_return_pct:>+6.1f}% {r.win_rate_pct:>7.1f}% {r.profit_factor:>6.3f}")
    print("─" * 44)
