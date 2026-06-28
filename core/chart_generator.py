"""
chart_generator.py — Generates candlestick chart images for trade signals.

Called at:
  • Trade entry   → shows setup (candles + entry/SL/TP levels)
  • TP hit        → shows full trade trajectory + TP marker
  • SL hit        → shows full trade trajectory + SL marker

Uses matplotlib (already installed) with a TradingView-style dark theme.
Returns PNG bytes that can be sent directly via Telegram sendPhoto API.
"""

import io
import matplotlib
matplotlib.use("Agg")   # non-interactive backend — no display/GUI needed
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch
import pandas as pd
import numpy as np
from datetime import datetime


# ── Colour palette (TradingView dark) ────────────────────────────────────────
_BG       = "#131722"
_PANEL    = "#1e222d"
_TEXT     = "#d1d4dc"
_GREEN    = "#26a69a"
_RED      = "#ef5350"
_BLUE     = "#2196f3"
_YELLOW   = "#f9a825"
_PURPLE   = "#9c27b0"


def _draw_candles(ax, df: pd.DataFrame):
    """Renders OHLC data as coloured candlestick bars on the given Axes."""
    for i, (_, row) in enumerate(df.iterrows()):
        up = row["Close"] >= row["Open"]
        colour = _GREEN if up else _RED

        body_lo = min(row["Open"], row["Close"])
        body_hi = max(row["Open"], row["Close"])
        body_h  = max(body_hi - body_lo, 1e-10)

        # Candle body
        rect = mpatches.Rectangle(
            (i - 0.35, body_lo), 0.70, body_h,
            facecolor=colour, edgecolor=colour, linewidth=0,
        )
        ax.add_patch(rect)

        # Upper and lower wicks
        ax.plot([i, i], [row["Low"],  body_lo], color=colour, linewidth=0.9, solid_capstyle="round")
        ax.plot([i, i], [body_hi, row["High"]], color=colour, linewidth=0.9, solid_capstyle="round")


def _price_label(ax, x_end: float, price: float, text: str, colour: str, fontsize: int = 8):
    """Draws a right-edge price label box."""
    ax.annotate(
        text,
        xy=(x_end, price),
        xytext=(x_end + 0.4, price),
        fontsize=fontsize,
        color=colour,
        fontweight="bold",
        va="center",
        ha="left",
        bbox=dict(boxstyle="round,pad=0.25", facecolor=_PANEL, edgecolor=colour, linewidth=1.0),
    )


def generate_trade_chart(
    symbol:       str,
    direction:    str,            # "BUY" or "SELL"
    entry_price:  float,
    sl_price:     float,
    tp_price:     float,
    confidence:   float,          # 0.0 – 1.0
    ict_score:    int,            # /20
    df_5m:        pd.DataFrame,
    outcome:      str  = None,    # None (entry) | "TP" | "SL" | "BE"
    exit_price:   float = None,
    rr_ratio:     int  = 3,
) -> bytes | None:
    """
    Generates a candlestick chart PNG.

    Entry chart  (outcome=None): last 80 bars + levels.
    Outcome chart (outcome='TP'/'SL'/'BE'): same + exit marker.

    Returns PNG bytes or None on error.
    """
    if df_5m is None or df_5m.empty:
        return None

    try:
        df = df_5m.tail(80).copy().reset_index()

        # Normalise column names to Title Case so _draw_candles can use them safely
        _required = {"open", "high", "low", "close"}
        _col_map  = {c: c.title() for c in df.columns if c.lower() in _required}
        if len(_col_map) < 4:
            return None   # missing OHLC data — can't draw chart
        df = df.rename(columns=_col_map)

        n = len(df)

        # ── Figure layout ─────────────────────────────────────────────────────
        fig, ax = plt.subplots(figsize=(13, 6.5), dpi=130)
        fig.patch.set_facecolor(_BG)
        ax.set_facecolor(_BG)

        # Candles
        _draw_candles(ax, df)

        # ── Price range + padding ─────────────────────────────────────────────
        all_prices = [df["High"].max(), df["Low"].min(), entry_price, sl_price, tp_price]
        if exit_price:
            all_prices.append(exit_price)
        p_min = min(all_prices)
        p_max = max(all_prices)
        pad   = (p_max - p_min) * 0.20

        ax.set_xlim(-1, n + 3)
        ax.set_ylim(p_min - pad, p_max + pad)

        x_end = n - 0.5   # right edge for horizontal lines

        # ── Zone shading ──────────────────────────────────────────────────────
        if direction == "BUY":
            profit_lo, profit_hi = entry_price, tp_price
            risk_lo,   risk_hi   = sl_price,    entry_price
        else:
            profit_lo, profit_hi = tp_price,    entry_price
            risk_lo,   risk_hi   = entry_price, sl_price

        ax.axhspan(profit_lo, profit_hi, xmin=0, xmax=(n + 1) / (n + 3),
                   color=_GREEN, alpha=0.07, zorder=0)
        ax.axhspan(risk_lo, risk_hi, xmin=0, xmax=(n + 1) / (n + 3),
                   color=_RED, alpha=0.07, zorder=0)

        # ── Key price lines ───────────────────────────────────────────────────
        # Entry
        ax.axhline(entry_price, color=_BLUE, linewidth=1.2, linestyle="--", alpha=0.85)
        _price_label(ax, x_end, entry_price,
                     f"ENTRY  {entry_price:.5f}", _BLUE)

        # Take Profit
        ax.axhline(tp_price, color=_GREEN, linewidth=1.2, linestyle="--", alpha=0.85)
        _price_label(ax, x_end, tp_price,
                     f"TP  {tp_price:.5f}", _GREEN)

        # Stop Loss
        ax.axhline(sl_price, color=_RED, linewidth=1.2, linestyle="--", alpha=0.85)
        _price_label(ax, x_end, sl_price,
                     f"SL  {sl_price:.5f}", _RED)

        # ── Entry arrow marker on the last candle ─────────────────────────────
        arrow_y  = entry_price
        arrow_dx = 0
        arrow_dy = (p_max - p_min) * 0.06 * (1 if direction == "BUY" else -1)
        ax.annotate(
            "▲ BUY" if direction == "BUY" else "▼ SELL",
            xy=(n - 1, arrow_y),
            xytext=(n - 1, arrow_y - arrow_dy),
            fontsize=9, fontweight="bold",
            color=_GREEN if direction == "BUY" else _RED,
            ha="center", va="center",
            arrowprops=dict(arrowstyle="->", color=_GREEN if direction == "BUY" else _RED, lw=1.5),
            bbox=dict(boxstyle="round,pad=0.3",
                      facecolor=_GREEN if direction == "BUY" else _RED,
                      edgecolor="none", alpha=0.85),
        )

        # ── Outcome marker (TP / SL hit) ──────────────────────────────────────
        if outcome and exit_price:
            outcome_colour = _GREEN if outcome == "TP" else (_RED if outcome == "SL" else _YELLOW)
            marker         = "★" if outcome == "TP" else ("✖" if outcome == "SL" else "⬥")
            label          = f"{marker} {outcome} HIT  {exit_price:.5f}"
            ax.axhline(exit_price, color=outcome_colour, linewidth=1.5, linestyle="-", alpha=0.9)
            ax.scatter([n - 1], [exit_price], marker="*" if outcome == "TP" else "X",
                       s=220, color=outcome_colour, zorder=5)
            _price_label(ax, x_end, exit_price, label, outcome_colour, fontsize=9)

        # ── Axes styling ──────────────────────────────────────────────────────
        ax.tick_params(colors=_TEXT, labelsize=7.5)
        for spine in ax.spines.values():
            spine.set_edgecolor("#2a2e39")

        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda v, _: f"{v:.4f}" if abs(v) < 100 else f"{v:.2f}")
        )

        # Remove x-tick labels (bar numbers are meaningless)
        ax.set_xticklabels([])

        # ── Grid ─────────────────────────────────────────────────────────────
        ax.yaxis.set_tick_params(labelright=True, labelleft=False)
        ax.yaxis.set_label_position("right")
        ax.grid(axis="y", color="#2a2e39", linewidth=0.5, alpha=0.7)

        # ── Title block ───────────────────────────────────────────────────────
        pair_base  = symbol.upper().replace(".M", "").replace("M", "").replace(".C", "")
        conf_stars = "★★★" if confidence >= 0.85 else ("★★" if confidence >= 0.75 else "★")
        dir_icon   = "🟢 BUY" if direction == "BUY" else "🔴 SELL"

        if outcome is None:
            status_line = f"ENTRY SIGNAL  |  AI {confidence:.0%} {conf_stars}  |  ICT {ict_score}/20  |  RR 1:{rr_ratio}"
        elif outcome == "TP":
            pnl_str = f"+${abs((tp_price - entry_price) / entry_price * 100):.2f}%" if exit_price else ""
            status_line = f"✅ TAKE PROFIT HIT  {pnl_str}  |  AI {confidence:.0%}  |  ICT {ict_score}/20"
        elif outcome == "SL":
            status_line = f"❌ STOP LOSS HIT  |  AI {confidence:.0%}  |  ICT {ict_score}/20"
        else:
            status_line = f"⚖️ BREAKEVEN  |  AI {confidence:.0%}  |  ICT {ict_score}/20"

        ax.set_title(
            f"{pair_base}  ·  5M  ·  {dir_icon}  ·  {status_line}",
            color=_TEXT, fontsize=10, fontweight="bold", pad=10,
            loc="left",
        )

        # ── RR annotation bottom-right ────────────────────────────────────────
        sl_pips = abs(entry_price - sl_price)
        tp_pips = abs(tp_price   - entry_price)
        if sl_pips > 0:
            actual_rr = tp_pips / sl_pips
            fig.text(0.99, 0.01,
                     f"Generated by Mr Bera's Bot  |  Risk:{sl_pips*10000:.1f}p  TP:{tp_pips*10000:.1f}p  RR 1:{actual_rr:.1f}",
                     ha="right", va="bottom", fontsize=6.5, color="#555f6e",
                     transform=fig.transFigure)

        plt.tight_layout(pad=1.2)

        # ── Export to bytes ───────────────────────────────────────────────────
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=_BG, dpi=130, bbox_inches="tight")
        buf.seek(0)
        image_bytes = buf.read()
        plt.close(fig)
        return image_bytes

    except Exception as e:
        print(f"⚠️  Chart generation failed: {e}")
        try:
            plt.close("all")
        except Exception:
            pass
        return None
