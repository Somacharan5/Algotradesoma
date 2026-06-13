"""
Trading analytics — pure functions over the agent's trade history.

No Streamlit here on purpose: everything takes plain DataFrames / lists and
returns plain dicts / DataFrames, so it can be unit-tested and reused. The
dashboard imports these and only handles presentation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd


# ── Trade-level enrichment ──────────────────────────────────────────────────

def prepare_trades(trades: list[dict], starting_capital: float) -> pd.DataFrame:
    """
    Normalise raw trade rows into a tidy, analysis-ready frame of CLOSED trades,
    sorted oldest→newest, with derived columns (return %, holding period, equity).
    """
    if not trades:
        return pd.DataFrame()

    df = pd.DataFrame(trades)
    df = df[df.get("status") == "CLOSED"].copy()
    if df.empty:
        return df

    for col in ("entry_price", "exit_price", "pnl", "quantity"):
        df[col] = pd.to_numeric(df.get(col), errors="coerce")

    df["opened_at"] = pd.to_datetime(df.get("created_at"), errors="coerce", utc=True)
    df["closed_at_dt"] = pd.to_datetime(df.get("closed_at"), errors="coerce", utc=True)
    df = df.sort_values("closed_at_dt").reset_index(drop=True)

    df["cost"] = (df["entry_price"] * df["quantity"]).replace(0, np.nan)
    df["return_pct"] = df["pnl"] / df["cost"] * 100
    df["holding_days"] = (df["closed_at_dt"] - df["opened_at"]).dt.total_seconds() / 86400
    df["win"] = df["pnl"] > 0

    # Per-trade equity curve from starting capital
    df["equity"] = starting_capital + df["pnl"].fillna(0).cumsum()
    df["cum_pnl"] = df["pnl"].fillna(0).cumsum()
    running_max = df["equity"].cummax()
    df["drawdown_pct"] = (df["equity"] - running_max) / running_max * 100
    return df


# ── Headline metrics ────────────────────────────────────────────────────────

@dataclass
class Metrics:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    net_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float | None = None
    avg_win: float = 0.0
    avg_loss: float = 0.0
    payoff_ratio: float | None = None
    expectancy: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    avg_holding_days: float = 0.0
    avg_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    total_return_pct: float = 0.0
    current_streak: int = 0          # +n winning / -n losing
    sharpe: float | None = None
    extras: dict = field(default_factory=dict)


def compute_metrics(df: pd.DataFrame, starting_capital: float) -> Metrics:
    m = Metrics()
    if df is None or df.empty:
        return m

    pnl = df["pnl"].dropna()
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    m.total_trades = int(len(pnl))
    m.wins = int(len(wins))
    m.losses = int(len(losses))
    m.win_rate = round(m.wins / m.total_trades * 100, 1) if m.total_trades else 0.0

    m.net_pnl = round(float(pnl.sum()), 2)
    m.gross_profit = round(float(wins.sum()), 2)
    m.gross_loss = round(float(abs(losses.sum())), 2)
    m.profit_factor = round(m.gross_profit / m.gross_loss, 2) if m.gross_loss > 0 else None

    m.avg_win = round(float(wins.mean()), 2) if len(wins) else 0.0
    m.avg_loss = round(float(losses.mean()), 2) if len(losses) else 0.0
    m.payoff_ratio = round(abs(m.avg_win / m.avg_loss), 2) if m.avg_loss else None
    m.expectancy = round(float(pnl.mean()), 2) if len(pnl) else 0.0

    m.largest_win = round(float(pnl.max()), 2) if len(pnl) else 0.0
    m.largest_loss = round(float(pnl.min()), 2) if len(pnl) else 0.0

    m.avg_holding_days = round(float(df["holding_days"].dropna().mean()), 1) if df["holding_days"].notna().any() else 0.0
    m.avg_return_pct = round(float(df["return_pct"].dropna().mean()), 2) if df["return_pct"].notna().any() else 0.0
    m.max_drawdown_pct = round(float(df["drawdown_pct"].min()), 2) if df["drawdown_pct"].notna().any() else 0.0
    m.total_return_pct = round(m.net_pnl / starting_capital * 100, 2) if starting_capital else 0.0

    # Current win/lose streak (from most recent trade backwards)
    streak = 0
    for w in reversed(df["win"].tolist()):
        if streak == 0:
            streak = 1 if w else -1
        elif (streak > 0) == bool(w):
            streak += 1 if w else -1
        else:
            break
    m.current_streak = streak

    # Rough Sharpe from per-trade returns (not annualised — comparative only)
    rets = df["return_pct"].dropna()
    if len(rets) > 2 and rets.std(ddof=1) > 0:
        m.sharpe = round(float(rets.mean() / rets.std(ddof=1)), 2)

    return m


# ── Breakdowns for charts ───────────────────────────────────────────────────

def pnl_by_symbol(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["symbol", "pnl", "trades", "win_rate"])
    g = df.groupby("symbol").agg(
        pnl=("pnl", "sum"),
        trades=("pnl", "size"),
        wins=("win", "sum"),
    ).reset_index()
    g["win_rate"] = (g["wins"] / g["trades"] * 100).round(0)
    return g.sort_values("pnl", ascending=False)


def monthly_returns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["month", "pnl", "trades"])
    d = df.dropna(subset=["closed_at_dt"]).copy()
    if d.empty:
        return pd.DataFrame(columns=["month", "pnl", "trades"])
    d["month"] = d["closed_at_dt"].dt.tz_convert("Asia/Kolkata").dt.strftime("%Y-%m")
    g = d.groupby("month").agg(pnl=("pnl", "sum"), trades=("pnl", "size")).reset_index()
    return g.sort_values("month")


def conviction_vs_outcome(df: pd.DataFrame, audit: list[dict]) -> pd.DataFrame:
    """
    Join each closed trade to the conviction score the agent gave it, so we can
    see whether higher conviction actually produced better returns.
    Matches the latest conviction_assessed event for that symbol at/just before
    the trade opened.
    """
    if df is None or df.empty or not audit:
        return pd.DataFrame(columns=["symbol", "composite", "verdict", "return_pct", "pnl"])

    convs = []
    for ev in audit:
        if ev.get("event") != "conviction_assessed":
            continue
        p = ev.get("payload") or {}
        ts = pd.to_datetime(ev.get("created_at"), errors="coerce", utc=True)
        if pd.isna(ts) or "composite" not in p:
            continue
        convs.append({"symbol": p.get("symbol"), "ts": ts,
                      "composite": p.get("composite"), "verdict": p.get("verdict")})
    if not convs:
        return pd.DataFrame(columns=["symbol", "composite", "verdict", "return_pct", "pnl"])
    cdf = pd.DataFrame(convs).sort_values("ts")

    rows = []
    for _, t in df.iterrows():
        cand = cdf[(cdf["symbol"] == t["symbol"]) & (cdf["ts"] <= t["opened_at"] + pd.Timedelta(minutes=5))]
        if cand.empty:
            cand = cdf[cdf["symbol"] == t["symbol"]]
        if cand.empty:
            continue
        c = cand.iloc[-1]
        rows.append({"symbol": t["symbol"], "composite": c["composite"],
                     "verdict": c["verdict"], "return_pct": t["return_pct"], "pnl": t["pnl"]})
    return pd.DataFrame(rows)


def regime_history(audit: list[dict]) -> pd.DataFrame:
    rows = []
    for ev in audit:
        if ev.get("event") != "market_regime":
            continue
        p = ev.get("payload") or {}
        ts = pd.to_datetime(ev.get("created_at"), errors="coerce", utc=True)
        if pd.isna(ts):
            continue
        rows.append({"ts": ts, "label": p.get("label"), "score": p.get("score"),
                     **(p.get("data") or {})})
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("ts")
