"""
Trading Agent — live monitoring dashboard.

Reads the same Supabase tables the agent writes to (read-only) and explains
what the agent is doing in plain language. Runs locally:

    streamlit run dashboard/app.py
"""
from __future__ import annotations

import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import pandas as pd
import pytz
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402  (loads .env)
from db.client import db_fetch, get_client  # noqa: E402
from strategy.universe import WATCHLIST  # noqa: E402

IST = pytz.timezone("Asia/Kolkata")
SCAN_TIME = time(9, 20)
EOD_TIME = time(15, 35)

st.set_page_config(page_title="Trading Agent Monitor", page_icon="📈", layout="wide")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _now_ist() -> datetime:
    return datetime.now(IST)


def _to_ist(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        return dt.astimezone(IST)
    except ValueError:
        return None


def _ago(dt: datetime | None) -> str:
    if dt is None:
        return "never"
    secs = (_now_ist() - dt).total_seconds()
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{secs / 3600:.1f} h ago"
    return f"{secs / 86400:.1f} days ago"


def _next_scan() -> datetime:
    now = _now_ist()
    nxt = now.replace(hour=SCAN_TIME.hour, minute=SCAN_TIME.minute, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    while nxt.weekday() >= 5:  # skip weekends (holidays shown approximately)
        nxt += timedelta(days=1)
    return nxt


def _rupees(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "—"
    sign = "+" if signed and x > 0 else ""
    return f"{sign}₹{x:,.0f}" if abs(x) >= 100 else f"{sign}₹{x:,.2f}"


def _pnl_color(df: pd.DataFrame, col: str):
    return df.style.map(
        lambda v: "color: #09ab3b" if isinstance(v, (int, float)) and v > 0
        else ("color: #ff2b2b" if isinstance(v, (int, float)) and v < 0 else ""),
        subset=[col],
    )


# ── Data access (fresh on every refresh) ────────────────────────────────────

def _fetch_latest(table: str, ts_col: str, limit: int) -> list[dict]:
    """Newest rows first — db_fetch has no ordering, which matters once tables grow."""
    try:
        q = get_client().table(table).select("*").order(ts_col, desc=True).limit(limit)
        return q.execute().data or []
    except Exception:
        return db_fetch(table, limit=limit)  # SQLite fallback path


def load_data() -> dict:
    return {
        "trades": _fetch_latest("trades", "created_at", 500),
        "positions": db_fetch("positions", limit=50),
        "daily": _fetch_latest("daily_pnl", "trade_date", 365),
        "audit": _fetch_latest("audit_log", "created_at", 500),
    }


# ── Plain-language decision feed ────────────────────────────────────────────

STRATEGY_NAMES = {
    "ema_crossover": "EMA crossover (short-term trend turned up through long-term trend)",
}

def _explain(event: dict) -> str | None:
    """Turn one audit_log row into a sentence a non-trader can read."""
    p = event.get("payload") or {}
    sym = p.get("symbol", "")
    kind = event.get("event")

    if kind == "signal_generated":
        strat = STRATEGY_NAMES.get(p.get("strategy", ""), p.get("strategy", ""))
        conf = p.get("confidence")
        conf_txt = f" (confidence {conf:.0%})" if isinstance(conf, (int, float)) else ""
        return f"🔍 Spotted a **{p.get('direction', '?')}** opportunity on **{sym}** — {strat}{conf_txt}"

    if kind == "compliance_checked":
        if p.get("passed"):
            return f"✅ Compliance check passed for **{sym}** (market open, stock liquid, no restrictions)"
        return f"🚫 Compliance **blocked {sym}**: {p.get('reason', 'no reason recorded')}"

    if kind == "risk_evaluated":
        if p.get("approved"):
            return f"🛡️ Risk officer approved **{sym}** — position sized at **{p.get('quantity')} shares** to keep potential loss small"
        return f"🛑 Risk officer **rejected {sym}**: {p.get('reason', 'no reason recorded')}"

    if kind == "market_regime":
        emoji = {"BULLISH": "🌤", "NEUTRAL": "⛅", "BEARISH": "🌧"}.get(p.get("label", ""), "⛅")
        why = "; ".join(p.get("reasons", [])[:3])
        return f"{emoji} Market check: **{p.get('label', '?')}** ({p.get('score', 0):.0%} favourable) — {why}"

    if kind == "conviction_assessed":
        s = p.get("scores", {})
        detail = (f"chart {s.get('technical', 0):.0%} · company health {s.get('fundamental', 0):.0%} · "
                  f"news {s.get('news', 0):.0%} · market {s.get('regime', 0):.0%}")
        why = "; ".join(p.get("reasons", [])[1:4])
        if p.get("verdict") == "STRONG":
            return f"💪 High conviction on **{sym}** ({p.get('composite', 0):.0%}) — full position. {detail}. {why}"
        if p.get("verdict") == "MODERATE":
            return f"🤔 Moderate conviction on **{sym}** ({p.get('composite', 0):.0%}) — buying a smaller position. {detail}. {why}"
        return f"🙅 Passed on **{sym}** ({p.get('composite', 0):.0%} conviction) — {why}"

    if kind == "circuit_breaker_tripped":
        return (f"⚡ **CIRCUIT BREAKER** — the day's loss hit {p.get('daily_loss_pct')}% "
                f"(limit {p.get('limit_pct')}%). All positions were closed and trading stopped for the day.")

    if kind == "end_of_day_summary":
        return (f"🌙 Day closed: {p.get('total_trades', 0)} trade(s), "
                f"{p.get('winners', 0)} winner(s) / {p.get('losers', 0)} loser(s), "
                f"realised P&L {_rupees(p.get('realised_pnl'), signed=True)}")

    return None


# ── Live page (auto-refreshes) ──────────────────────────────────────────────

@st.fragment(run_every="30s")
def live_view() -> None:
    data = load_data()
    trades = pd.DataFrame(data["trades"])
    positions = pd.DataFrame(data["positions"])
    daily = pd.DataFrame(data["daily"])
    if not daily.empty:
        daily = daily.sort_values("trade_date").reset_index(drop=True)
    audit = data["audit"]

    for ev in audit:
        ev["_ts"] = _to_ist(ev.get("created_at"))
    audit.sort(key=lambda e: e["_ts"] or _now_ist(), reverse=True)
    last_activity = audit[0]["_ts"] if audit else None

    open_trades = trades[trades["status"] == "OPEN"] if not trades.empty else pd.DataFrame()
    closed = trades[trades["status"] == "CLOSED"].copy() if not trades.empty else pd.DataFrame()

    # ── Status banner ──
    nxt = _next_scan()
    hours_to_scan = (nxt - _now_ist()).total_seconds() / 3600
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Mode", settings.TRADING_MODE.upper(),
              help="PAPER = practice trades with virtual money. No real money is at risk.")
    c2.metric("Agent last active", _ago(last_activity),
              help="Time of the last event the agent wrote to the database.")
    c3.metric("Next stock scan", nxt.strftime("%a %H:%M IST"), f"in {hours_to_scan:.1f} h",
              delta_color="off",
              help="Every market day at 09:20 IST the agent scans its watchlist for buy signals.")
    circuit = bool(daily.iloc[-1]["circuit_tripped"]) if not daily.empty else False
    c4.metric("Safety circuit", "TRIPPED ⚡" if circuit else "Armed ✓",
              help="If the agent loses 3.5% of capital in one day it stops itself until tomorrow.")

    st.divider()

    # ── Money row ──
    realised_total = float(closed["pnl"].fillna(0).sum()) if not closed.empty else 0.0
    unrealised = float(positions["unrealised_pnl"].fillna(0).sum()) if not positions.empty else 0.0
    today = _now_ist().date().isoformat()
    today_row = daily[daily["trade_date"] == today] if not daily.empty else pd.DataFrame()
    today_pnl = float(today_row.iloc[0]["realised_pnl"] + today_row.iloc[0]["unrealised_pnl"]) if not today_row.empty else 0.0
    wins = int((closed["pnl"] > 0).sum()) if not closed.empty else 0
    win_rate = (wins / len(closed) * 100) if len(closed) else 0.0

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Capital", _rupees(settings.PAPER_CAPITAL + realised_total),
              help="Starting capital plus all profits/losses from closed trades.")
    m2.metric("Today's P&L", _rupees(today_pnl, signed=True), delta=f"{today_pnl:+,.0f}")
    m3.metric("All-time P&L (closed)", _rupees(realised_total, signed=True), delta=f"{realised_total:+,.0f}")
    m4.metric("Open positions P&L", _rupees(unrealised, signed=True),
              help="Profit/loss on stocks the agent currently holds, at last checked prices.")
    m5.metric("Win rate", f"{win_rate:.0f}%", f"{wins} of {len(closed)} trades", delta_color="off")

    st.divider()
    left, right = st.columns([3, 2], gap="large")

    # ── Left: positions + history + equity ──
    with left:
        st.subheader("📦 Stocks the agent holds right now")
        live_pos = positions[positions.get("quantity", pd.Series(dtype=int)) > 0] if not positions.empty else pd.DataFrame()
        if not open_trades.empty:
            view = open_trades[["symbol", "direction", "quantity", "entry_price", "stop_loss", "target"]].copy()
            if not live_pos.empty:
                ltp = live_pos.set_index("symbol")[["current_price", "unrealised_pnl"]]
                view = view.join(ltp, on="symbol")
            view.columns = ["Stock", "Side", "Shares", "Bought at", "Stop loss (max pain)", "Target (goal)",
                            "Price now", "P&L now"][: len(view.columns)]
            st.dataframe(_pnl_color(view, "P&L now") if "P&L now" in view else view,
                         hide_index=True, use_container_width=True)
            st.caption("The agent automatically sells if price falls to the stop loss or rises to the target. "
                       "Prices update every 15 minutes during market hours.")
        else:
            st.info("No open positions — the agent is in cash, waiting for a good setup.")

        st.subheader("📜 Completed trades")
        if not closed.empty:
            closed["_closed"] = closed["closed_at"].map(_to_ist)
            closed = closed.sort_values("_closed", ascending=False)
            hist = closed[["symbol", "direction", "quantity", "entry_price", "exit_price", "pnl"]].copy()
            hist.insert(0, "Closed on", closed["_closed"].map(
                lambda d: d.strftime("%d %b %Y %H:%M") if d else "—"))
            hist.columns = ["Closed on", "Stock", "Side", "Shares", "Bought at", "Sold at", "Profit/Loss ₹"]
            st.dataframe(_pnl_color(hist, "Profit/Loss ₹"), hide_index=True, use_container_width=True)
        else:
            st.info("No completed trades yet.")

        st.subheader("📈 Capital over time")
        if not daily.empty and len(daily) > 1:
            eq = daily.sort_values("trade_date").set_index("trade_date")["capital_end"]
            st.line_chart(eq, height=260)
        else:
            st.caption("The chart appears once there are a few days of trading history.")

    # ── Right: decision diary ──
    with right:
        st.subheader("🧠 Decision diary")
        st.caption("Every decision the agent makes, newest first, in plain words.")
        shown = 0
        current_day = None
        for ev in audit:
            line = _explain(ev)
            if not line:
                continue
            day = ev["_ts"].strftime("%A, %d %B %Y") if ev["_ts"] else "Unknown date"
            if day != current_day:
                st.markdown(f"**{day}**")
                current_day = day
            ts = ev["_ts"].strftime("%H:%M") if ev["_ts"] else "--:--"
            st.markdown(f"<small>{ts}</small> &nbsp;{line}", unsafe_allow_html=True)
            shown += 1
            if shown >= 60:
                break
        if shown == 0:
            st.info("No decisions recorded yet. The diary fills up after the next market scan (09:20 IST).")

    st.caption(f"Auto-refreshes every 30 s · last refreshed {_now_ist().strftime('%H:%M:%S IST')} · data: Supabase (read-only)")


# ── Sidebar: how it works ───────────────────────────────────────────────────

with st.sidebar:
    st.title("📈 Trading Agent")
    st.markdown(
        f"""
**What is this?** A live window into your autonomous trading agent
running on Oracle Cloud. It currently trades **paper money**
(₹{settings.PAPER_CAPITAL:,.0f} virtual capital) — no real money is at risk.

**Its daily routine (IST):**
- **09:20** — scans the **Nifty 100** ({len(WATCHLIST)} largest NSE stocks) for buy signals
- **every 15 min** — re-checks prices on stocks it holds
- **15:35** — writes the day's report and goes to sleep

**How it decides, in 5 steps:**
1. **Strategy** spots a chart pattern (e.g. price trend turning up)
2. **Compliance** checks the trade is allowed (market open, stock liquid)
3. **Conviction** weighs the company's financial health, recent news
   (Indian + global) and overall market mood — weak cases are dropped,
   borderline ones get a smaller bet
4. **Risk officer** sizes the position so a single loss stays small
5. **Executor** places the trade with a stop-loss and a target

**Watchlist:** {", ".join(WATCHLIST[:12])}… ({len(WATCHLIST)} stocks total)
"""
    )
    st.divider()
    st.caption("Tip: keep this page open during market hours (09:15–15:30 IST) to watch it work.")

live_view()
