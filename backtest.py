"""
Backtesting engine for Mr Bera's_Bot.

Usage:
    from backtest import Backtest
    from strategy import moving_average_crossover
    import pandas as pd

    df = pd.read_csv("data/EURUSD_H1.csv", parse_dates=["time"], index_col="time")
    bt = Backtest(df, moving_average_crossover, initial_capital=10_000, lot_size=1000)
    results = bt.run()
    bt.print_summary(results)
    bt.plot_equity(results)
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Trade:
    entry_time: pd.Timestamp
    entry_price: float
    direction: int          # +1 long, -1 short
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    pnl: float = 0.0


@dataclass
class BacktestResults:
    equity_curve: pd.Series
    trades: list[Trade] = field(default_factory=list)
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    total_trades: int = 0


class Backtest:
    def __init__(
        self,
        df: pd.DataFrame,
        strategy_fn: Callable,
        initial_capital: float = 10_000.0,
        lot_size: float = 1_000.0,
        spread_pips: float = 1.5,
        pip_value: float = 0.0001,
        strategy_kwargs: dict | None = None,
    ):
        self.df = df.copy()
        self.strategy_fn = strategy_fn
        self.capital = initial_capital
        self.lot_size = lot_size
        self.spread = spread_pips * pip_value
        self.strategy_kwargs = strategy_kwargs or {}

    # ------------------------------------------------------------------
    def run(self) -> BacktestResults:
        df = self.df.copy()
        kwargs = self.strategy_kwargs
        df["signal"] = self.strategy_fn(df, **kwargs)

        capital = self.capital
        equity = [capital]
        trades: list[Trade] = []
        position: Trade | None = None

        for i in range(1, len(df)):
            row = df.iloc[i]
            sig = df["signal"].iloc[i]

            # Close existing position on opposite signal
            if position is not None and sig != 0 and sig != position.direction:
                exit_px = row["close"] - self.spread if position.direction == 1 else row["close"] + self.spread
                raw_pnl = (exit_px - position.entry_price) * position.direction * self.lot_size
                position.exit_time = row.name
                position.exit_price = exit_px
                position.pnl = raw_pnl
                capital += raw_pnl
                trades.append(position)
                position = None

            # Open new position
            if position is None and sig in (1, -1):
                entry_px = row["close"] + self.spread if sig == 1 else row["close"] - self.spread
                position = Trade(entry_time=row.name, entry_price=entry_px, direction=int(sig))

            equity.append(capital)

        # Close any open position at the end
        if position is not None:
            last = df.iloc[-1]
            exit_px = last["close"] - self.spread if position.direction == 1 else last["close"] + self.spread
            position.exit_time = last.name
            position.exit_price = exit_px
            position.pnl = (exit_px - position.entry_price) * position.direction * self.lot_size
            capital += position.pnl
            trades.append(position)
            equity[-1] = capital

        equity_series = pd.Series(equity, index=df.index)

        # Metrics
        total_return = (capital - self.capital) / self.capital * 100
        max_dd = self._max_drawdown(equity_series)
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        win_rate = len(wins) / len(trades) * 100 if trades else 0
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss else float("inf")
        returns = equity_series.pct_change().dropna()
        sharpe = (returns.mean() / returns.std() * np.sqrt(252 * 24)) if returns.std() > 0 else 0

        return BacktestResults(
            equity_curve=equity_series,
            trades=trades,
            total_return_pct=round(total_return, 2),
            max_drawdown_pct=round(max_dd, 2),
            win_rate_pct=round(win_rate, 2),
            profit_factor=round(profit_factor, 3),
            sharpe_ratio=round(sharpe, 3),
            total_trades=len(trades),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _max_drawdown(equity: pd.Series) -> float:
        roll_max = equity.cummax()
        drawdown = (equity - roll_max) / roll_max * 100
        return abs(drawdown.min())

    # ------------------------------------------------------------------
    def print_summary(self, results: BacktestResults) -> None:
        sep = "─" * 40
        print(f"\n{'Mr Bera\\'s_Bot — Backtest Results':^40}")
        print(sep)
        print(f"  Initial Capital  : ${self.capital:>12,.2f}")
        print(f"  Final Capital    : ${results.equity_curve.iloc[-1]:>12,.2f}")
        print(f"  Total Return     : {results.total_return_pct:>+10.2f} %")
        print(f"  Max Drawdown     : {results.max_drawdown_pct:>10.2f} %")
        print(sep)
        print(f"  Total Trades     : {results.total_trades:>10}")
        print(f"  Win Rate         : {results.win_rate_pct:>10.2f} %")
        print(f"  Profit Factor    : {results.profit_factor:>10.3f}")
        print(f"  Sharpe Ratio     : {results.sharpe_ratio:>10.3f}")
        print(sep)

    # ------------------------------------------------------------------
    def plot_equity(self, results: BacktestResults) -> None:
        try:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(12, 4))
            results.equity_curve.plot(ax=ax, color="#0779e4", linewidth=1.5)
            ax.set_title("Mr Bera's_Bot — Equity Curve", fontsize=13, fontweight="bold")
            ax.set_ylabel("Capital ($)")
            ax.grid(alpha=0.3)
            plt.tight_layout()
            plt.savefig("results/equity_curve.png", dpi=150)
            print("  Equity curve saved → results/equity_curve.png")
            plt.show()
        except ImportError:
            print("  matplotlib not installed — skipping plot.")
