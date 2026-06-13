"""
Trading Agent — live monitoring & analytics dashboard.

Reads the same Supabase tables the agent writes to (read-only). Runs locally
or as a hosted service on the Oracle VM:

    streamlit run dashboard/app.py

A password gate is enabled when DASHBOARD_PASSWORD is set in the environment.
"""
from __future__ import annotations

import hmac
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pytz
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402  (loads .env)
from db.client import db_fetch, get_client  # noqa: E402
from strategy.universe import WATCHLIST  # noqa: E402
from dashboard import analytics  # noqa: E402

IST = pytz.timezone("Asia/Kolkata")
SCAN_TIME = time(9, 20)

GREEN, RED, MUTED = "#16c784", "#ea3943", "#8a8d98"
ACCENT = "#5b8def"

st.set_page_config(page_title="Trading Agent", page_icon="📈", layout="wide",
                   initial_sidebar_state="collapsed")


# ── Styling ─────────────────────────────────────────────────────────────────

st.markdown(
    """
    <style>
      .block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1300px;}
      #MainMenu, footer {visibility: hidden;}
      [data-testid="stMetricValue"] {font-size: 1.7rem; font-weight: 700;}
      [data-testid="stMetricLabel"] {color: #8a8d98;}
      .stTabs [data-baseweb="tab-list"] {gap: 6px;}
      .stTabs [data-baseweb="tab"] {padding: 8px 16px; border-radius: 8px;}
      .pill {display:inline-block; padding:2px 10px; border-radius:999px;
             font-size:0.78rem; font-weight:600;}
      .diary-time {color:#8a8d98; font-variant-numeric: tabular-nums; font-size:0.8rem;}
      .card {background: rgba(128,128,128,0.06); border:1px solid rgba(128,128,128,0.15);
             border-radius:12px; padding:16px 18px; margin-bottom:10px;}
      .big {font-size:2.0rem; font-weight:800; line-height:1.1;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ── Auth gate ───────────────────────────────────────────────────────────────

def _check_password() -> bool:
    expected = os.getenv("DASHBOARD_PASSWORD", "")
    if not expected:
        return True  # no password configured (local dev)
    if st.session_state.get("auth_ok"):
        return True

    st.markdown("### 🔒 Trading Agent")
    st.caption("Enter the dashboard password to continue.")
    pw = st.text_input("Password", type="password", label_visibility="collapsed")
    if pw:
        if hmac.compare_digest(pw, expected):
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


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
    except (ValueError, TypeError):
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
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def _rupees(x: float | None, signed: bool = False) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    sign = "+" if signed and x > 0 else ""
    return f"{sign}₹{x:,.0f}" if abs(x) >= 100 else f"{sign}₹{x:,.2f}"


def _color(x: float | None) -> str:
    if x is None or pd.isna(x):
        return MUTED
    return GREEN if x > 0 else RED if x < 0 else MUTED


def _pill(text: str, color: str) -> str:
    return f'<span class="pill" style="background:{color}22;color:{color}">{text}</span>'


def _style_pnl(df: pd.DataFrame, cols: list[str]):
    cols = [c for c in cols if c in df.columns]
    return df.style.map(
        lambda v: f"color: {GREEN}" if isinstance(v, (int, float)) and v > 0
        else (f"color: {RED}" if isinstance(v, (int, float)) and v < 0 else ""),
        subset=cols,
    )


def _plotly_layout(fig: go.Figure, height: int = 320, **kwargs) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=30, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(size=12), showlegend=kwargs.pop("showlegend", False),
        hovermode="x unified", **kwargs,
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,0.12)", zeroline=False)
    return fig


# ── Data ────────────────────────────────────────────────────────────────────

def _fetch_latest(table: str, ts_col: str, limit: int) -> list[dict]:
    try:
        q = get_client().table(table).select("*").order(ts_col, desc=True).limit(limit)
        return q.execute().data or []
    except Exception:
        return db_fetch(table, limit=limit)


@st.cache_data(ttl=30, show_spinner=False)
def load_data() -> dict:
    return {
        "trades": _fetch_latest("trades", "created_at", 1000),
        "positions": db_fetch("positions", limit=50),
        "daily": _fetch_latest("daily_pnl", "trade_date", 365),
        "audit": _fetch_latest("audit_log", "created_at", 1000),
    }


# ── Decision diary ──────────────────────────────────────────────────────────

STRATEGY_NAMES = {
    "ema_crossover": "EMA crossover (short-term trend turned up through long-term trend)",
}


def _explain(event: dict) -> str | None:
    p = event.get("payload") or {}
    sym = p.get("symbol", "")
    kind = event.get("event")

    if kind == "signal_generated":
        strat = STRATEGY_NAMES.get(p.get("strategy", ""), p.get("strategy", ""))
        conf = p.get("confidence")
        conf_txt = f" (confidence {conf:.0%})" if isinstance(conf, (int, float)) else ""
        return f"🔍 Spotted a **{p.get('direction', '?')}** setup on **{sym}** — {strat}{conf_txt}"

    if kind == "compliance_checked":
        if p.get("passed"):
            return f"✅ Compliance passed for **{sym}** (market open, stock liquid)"
        return f"🚫 Compliance **blocked {sym}**: {p.get('reason', '')}"

    if kind == "risk_evaluated":
        if p.get("approved"):
            return f"🛡️ Risk officer approved **{sym}** — **{p.get('quantity')} shares**"
        return f"🛑 Risk officer **rejected {sym}**: {p.get('reason', '')}"

    if kind == "market_regime":
        emoji = {"BULLISH": "🌤", "NEUTRAL": "⛅", "BEARISH": "🌧"}.get(p.get("label", ""), "⛅")
        why = "; ".join(p.get("reasons", [])[:3])
        return f"{emoji} Market check: **{p.get('label', '?')}** ({p.get('score', 0):.0%} favourable) — {why}"

    if kind == "conviction_assessed":
        s = p.get("scores", {})
        detail = (f"chart {s.get('technical', 0):.0%} · health {s.get('fundamental', 0):.0%} · "
                  f"news {s.get('news', 0):.0%} · market {s.get('regime', 0):.0%}")
        why = "; ".join(p.get("reasons", [])[1:4])
        if p.get("verdict") == "STRONG":
            return f"💪 High conviction on **{sym}** ({p.get('composite', 0):.0%}) — full position. {detail}. {why}"
        if p.get("verdict") == "MODERATE":
            return f"🤔 Moderate conviction on **{sym}** ({p.get('composite', 0):.0%}) — smaller position. {detail}. {why}"
        return f"🙅 Passed on **{sym}** ({p.get('composite', 0):.0%}) — {why}"

    if kind == "circuit_breaker_tripped":
        return (f"⚡ **CIRCUIT BREAKER** — day's loss hit {p.get('daily_loss_pct')}% "
                f"(limit {p.get('limit_pct')}%). All positions closed, trading halted for the day.")

    if kind == "end_of_day_summary":
        return (f"🌙 Day closed: {p.get('total_trades', 0)} trade(s), "
                f"{p.get('winners', 0)} win / {p.get('losers', 0)} loss, "
                f"realised {_rupees(p.get('realised_pnl'), signed=True)}")
    return None


# ── Tab renderers ───────────────────────────────────────────────────────────

def render_overview(ctx: dict) -> None:
    cap = settings.PAPER_CAPITAL + ctx["realised_total"]
    m = ctx["metrics"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Mode", settings.TRADING_MODE.upper(), help="PAPER = virtual money, no real funds at risk.")
    c2.metric("Agent last active", _ago(ctx["last_activity"]))
    nxt = _next_scan()
    c3.metric("Next scan", nxt.strftime("%a %H:%M"), f"in {(nxt - _now_ist()).total_seconds()/3600:.1f} h",
              delta_color="off")
    c4.metric("Safety circuit", "TRIPPED ⚡" if ctx["circuit"] else "Armed ✓")

    st.divider()

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Capital", _rupees(cap), f"{m.total_return_pct:+.2f}% all-time")
    k2.metric("Today's P&L", _rupees(ctx["today_pnl"], signed=True), delta=f"{ctx['today_pnl']:+,.0f}")
    k3.metric("Open positions P&L", _rupees(ctx["unrealised"], signed=True),
              help="Unrealised P&L on stocks currently held.")
    k4.metric("Win rate", f"{m.win_rate:.0f}%", f"{m.wins}W / {m.losses}L", delta_color="off")
    k5.metric("Profit factor", f"{m.profit_factor:.2f}" if m.profit_factor else "—",
              help="Gross profit ÷ gross loss. Above 1.0 means winning more than losing.")

    st.divider()
    left, right = st.columns([3, 2], gap="large")

    with left:
        st.markdown("#### 📈 Capital over time")
        df = ctx["tdf"]
        if df is not None and len(df) >= 2:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df["closed_at_dt"], y=df["equity"], mode="lines",
                line=dict(color=ACCENT, width=2.5), fill="tozeroy",
                fillcolor="rgba(91,141,239,0.10)", name="Capital",
                hovertemplate="₹%{y:,.0f}<extra></extra>"))
            fig.add_hline(y=settings.PAPER_CAPITAL, line=dict(color=MUTED, width=1, dash="dot"))
            st.plotly_chart(_plotly_layout(fig, 300), use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("The equity curve appears once a few trades have closed.")

        st.markdown("#### 📦 Holding right now")
        _render_positions_table(ctx, compact=True)

    with right:
        st.markdown("#### 🧠 Latest decisions")
        _render_diary(ctx["audit"], limit=14)


def _render_positions_table(ctx: dict, compact: bool = False) -> None:
    open_trades = ctx["open_trades"]
    positions = ctx["positions"]
    if open_trades is None or open_trades.empty:
        st.info("No open positions — the agent is in cash, waiting for a strong setup.")
        return
    view = open_trades[["symbol", "direction", "quantity", "entry_price", "stop_loss", "target"]].copy()
    if positions is not None and not positions.empty:
        ltp = positions.set_index("symbol")[["current_price", "unrealised_pnl"]]
        view = view.join(ltp, on="symbol")
    rename = {"symbol": "Stock", "direction": "Side", "quantity": "Shares",
              "entry_price": "Bought at", "stop_loss": "Stop loss", "target": "Target",
              "current_price": "Price now", "unrealised_pnl": "P&L now"}
    view = view.rename(columns=rename)
    st.dataframe(_style_pnl(view, ["P&L now"]).format(precision=2),
                 hide_index=True, use_container_width=True)
    if not compact:
        st.caption("The agent auto-sells at the stop loss (limits the loss) or the target (locks the gain). "
                   "Prices refresh every 15 minutes during market hours.")


def render_positions(ctx: dict) -> None:
    st.markdown("#### 📦 Open positions")
    _render_positions_table(ctx, compact=False)

    open_trades = ctx["open_trades"]
    positions = ctx["positions"]
    if open_trades is None or open_trades.empty or positions is None or positions.empty:
        return

    st.markdown("#### 🎯 Distance to stop / target")
    st.caption("Where each holding's current price sits between its stop loss and its target.")
    for _, t in open_trades.iterrows():
        prow = positions[positions["symbol"] == t["symbol"]]
        if prow.empty:
            continue
        ltp = prow.iloc[0].get("current_price")
        sl, tgt, entry = t["stop_loss"], t["target"], t["entry_price"]
        if not ltp or tgt == sl:
            continue
        frac = max(0.0, min(1.0, (ltp - sl) / (tgt - sl)))
        upnl = prow.iloc[0].get("unrealised_pnl", 0) or 0
        cols = st.columns([1, 4, 1])
        cols[0].markdown(f"**{t['symbol']}**")
        cols[1].progress(frac)
        cols[2].markdown(f"<span style='color:{_color(upnl)}'>{_rupees(upnl, signed=True)}</span>",
                         unsafe_allow_html=True)
        cols[1].caption(f"SL {_rupees(sl)} · now {_rupees(ltp)} · target {_rupees(tgt)} "
                        f"(entry {_rupees(entry)})")


def render_history(ctx: dict) -> None:
    df = ctx["tdf"]
    if df is None or df.empty:
        st.info("No completed trades yet.")
        return

    syms = ["All"] + sorted(df["symbol"].unique().tolist())
    c1, c2 = st.columns([1, 1])
    pick = c1.selectbox("Filter by stock", syms)
    outcome = c2.selectbox("Outcome", ["All", "Winners only", "Losers only"])
    view = df.copy()
    if pick != "All":
        view = view[view["symbol"] == pick]
    if outcome == "Winners only":
        view = view[view["win"]]
    elif outcome == "Losers only":
        view = view[~view["win"]]

    st.markdown("#### 💹 Profit / loss per trade")
    if not view.empty:
        order = view.sort_values("closed_at_dt")
        fig = go.Figure(go.Bar(
            x=order["closed_at_dt"], y=order["pnl"],
            marker_color=[GREEN if v > 0 else RED for v in order["pnl"]],
            hovertemplate="%{x|%d %b}<br>%{customdata}<br>₹%{y:,.0f}<extra></extra>",
            customdata=order["symbol"]))
        st.plotly_chart(_plotly_layout(fig, 280), use_container_width=True, config={"displayModeBar": False})

    show = view.sort_values("closed_at_dt", ascending=False).copy()
    show["Closed"] = show["closed_at_dt"].dt.tz_convert(IST).dt.strftime("%d %b %Y %H:%M")
    show["Held"] = show["holding_days"].map(lambda d: f"{d:.1f}d" if pd.notna(d) else "—")
    show["Return"] = show["return_pct"].map(lambda v: f"{v:+.1f}%" if pd.notna(v) else "—")
    table = show[["Closed", "symbol", "quantity", "entry_price", "exit_price", "Held", "pnl", "Return"]]
    table = table.rename(columns={"symbol": "Stock", "quantity": "Shares",
                                  "entry_price": "Bought", "exit_price": "Sold", "pnl": "P&L ₹"})
    st.dataframe(_style_pnl(table, ["P&L ₹"]).format(precision=2),
                 hide_index=True, use_container_width=True)
    st.caption(f"{len(view)} trade(s) shown.")


def render_analytics(ctx: dict) -> None:
    df = ctx["tdf"]
    m = ctx["metrics"]
    if df is None or df.empty:
        st.info("Analytics appear once trades have closed. Come back after the agent has traded.")
        return

    st.markdown("#### 🔑 Performance scorecard")
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Net P&L", _rupees(m.net_pnl, signed=True), f"{m.total_return_pct:+.2f}%")
    g2.metric("Expectancy / trade", _rupees(m.expectancy, signed=True),
              help="Average profit (or loss) you can expect per trade.")
    g3.metric("Payoff ratio", f"{m.payoff_ratio:.2f}" if m.payoff_ratio else "—",
              help="Average win size ÷ average loss size.")
    g4.metric("Max drawdown", f"{m.max_drawdown_pct:.1f}%",
              help="Worst peak-to-trough fall in capital. Smaller is better.")
    g5, g6, g7, g8 = st.columns(4)
    g5.metric("Avg win", _rupees(m.avg_win, signed=True))
    g6.metric("Avg loss", _rupees(m.avg_loss, signed=True))
    g7.metric("Avg hold", f"{m.avg_holding_days:.1f} days")
    streak_txt = f"{abs(m.current_streak)} {'win' if m.current_streak > 0 else 'loss'}{'s' if abs(m.current_streak) != 1 else ''}"
    g8.metric("Current streak", streak_txt if m.current_streak else "—",
              help="Consecutive wins or losses on the most recent trades.")

    st.divider()
    a, b = st.columns(2, gap="large")

    with a:
        st.markdown("#### 📉 Drawdown")
        fig = go.Figure(go.Scatter(
            x=df["closed_at_dt"], y=df["drawdown_pct"], mode="lines",
            line=dict(color=RED, width=1.5), fill="tozeroy", fillcolor="rgba(234,57,67,0.12)",
            hovertemplate="%{y:.1f}%<extra></extra>"))
        st.plotly_chart(_plotly_layout(fig, 280), use_container_width=True, config={"displayModeBar": False})

        st.markdown("#### 🏆 Win / loss split")
        fig = go.Figure(go.Pie(
            values=[m.wins, m.losses], labels=["Wins", "Losses"], hole=0.62,
            marker_colors=[GREEN, RED], textinfo="label+percent", sort=False))
        st.plotly_chart(_plotly_layout(fig, 260, showlegend=False), use_container_width=True,
                        config={"displayModeBar": False})

    with b:
        st.markdown("#### 🏷️ P&L by stock")
        by_sym = analytics.pnl_by_symbol(df)
        if not by_sym.empty:
            top = pd.concat([by_sym.head(8), by_sym.tail(4)]).drop_duplicates("symbol")
            fig = go.Figure(go.Bar(
                x=top["pnl"], y=top["symbol"], orientation="h",
                marker_color=[GREEN if v > 0 else RED for v in top["pnl"]],
                hovertemplate="%{y}: ₹%{x:,.0f}<extra></extra>"))
            fig.update_yaxes(autorange="reversed")
            st.plotly_chart(_plotly_layout(fig, 300), use_container_width=True, config={"displayModeBar": False})

        st.markdown("#### 🗓️ Monthly P&L")
        monthly = analytics.monthly_returns(df)
        if not monthly.empty:
            fig = go.Figure(go.Bar(
                x=monthly["month"], y=monthly["pnl"],
                marker_color=[GREEN if v > 0 else RED for v in monthly["pnl"]],
                hovertemplate="%{x}: ₹%{y:,.0f}<extra></extra>"))
            st.plotly_chart(_plotly_layout(fig, 260), use_container_width=True, config={"displayModeBar": False})

    st.divider()
    st.markdown("#### 🧪 Did conviction predict the outcome?")
    st.caption("Each dot is a closed trade: its conviction score (how sure the agent was) vs the return it made. "
               "If the cloud tilts up-and-right, the agent's conviction is genuinely informative.")
    cvo = analytics.conviction_vs_outcome(df, ctx["audit"])
    if len(cvo) >= 3:
        fig = go.Figure(go.Scatter(
            x=cvo["composite"] * 100, y=cvo["return_pct"], mode="markers+text",
            text=cvo["symbol"], textposition="top center",
            marker=dict(size=11, color=[GREEN if v > 0 else RED for v in cvo["return_pct"]],
                        line=dict(width=1, color="rgba(255,255,255,0.4)")),
            hovertemplate="%{text}<br>conviction %{x:.0f}%<br>return %{y:+.1f}%<extra></extra>"))
        fig.add_hline(y=0, line=dict(color=MUTED, width=1, dash="dot"))
        fig.update_xaxes(title="Conviction score (%)")
        fig.update_yaxes(title="Trade return (%)")
        st.plotly_chart(_plotly_layout(fig, 340), use_container_width=True, config={"displayModeBar": False})
    else:
        st.info("Needs at least 3 closed trades that went through the conviction engine. Building up…")


def render_decisions(ctx: dict) -> None:
    st.markdown("#### 🧠 Decision diary")
    st.caption("Every decision the agent makes, newest first, in plain words.")
    _render_diary(ctx["audit"], limit=120)


def _render_diary(audit: list[dict], limit: int) -> None:
    shown, current_day = 0, None
    for ev in audit:
        line = _explain(ev)
        if not line:
            continue
        ts = ev.get("_ts")
        day = ts.strftime("%A, %d %B %Y") if ts else "Unknown date"
        if day != current_day:
            st.markdown(f"**{day}**")
            current_day = day
        tstr = ts.strftime("%H:%M") if ts else "--:--"
        st.markdown(f"<span class='diary-time'>{tstr}</span>&nbsp;&nbsp;{line}", unsafe_allow_html=True)
        shown += 1
        if shown >= limit:
            break
    if shown == 0:
        st.info("No decisions recorded yet. The diary fills up after the next market scan (09:20 IST).")


def render_market(ctx: dict) -> None:
    st.markdown("#### 🌍 Market regime history")
    st.caption("How supportive the agent judged the overall market to be, each scan day. "
               "It buys smaller — or not at all — when this is low.")
    reg = analytics.regime_history(ctx["audit"])
    if reg.empty:
        st.info("Market-regime history appears after the next scan (09:20 IST on a market day).")
        return

    latest = reg.iloc[-1]
    label = latest.get("label", "NEUTRAL")
    col = {"BULLISH": GREEN, "NEUTRAL": ACCENT, "BEARISH": RED}.get(label, ACCENT)
    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(f"**Current**<br>{_pill(label, col)}", unsafe_allow_html=True)
    c2.metric("Favourable", f"{latest.get('score', 0):.0%}")
    if "india_vix" in reg.columns and pd.notna(latest.get("india_vix")):
        c3.metric("India VIX", f"{latest.get('india_vix'):.0f}", help="Market fear gauge. Lower = calmer.")
    if "nifty" in reg.columns and pd.notna(latest.get("nifty")):
        c4.metric("Nifty 50", f"{latest.get('nifty'):,.0f}")

    fig = go.Figure(go.Scatter(
        x=reg["ts"], y=reg["score"] * 100, mode="lines+markers",
        line=dict(color=ACCENT, width=2), marker=dict(size=7),
        hovertemplate="%{x|%d %b}: %{y:.0f}%<extra></extra>"))
    fig.add_hrect(y0=65, y1=100, fillcolor="rgba(22,199,132,0.07)", line_width=0)
    fig.add_hrect(y0=0, y1=35, fillcolor="rgba(234,57,67,0.07)", line_width=0)
    fig.update_yaxes(range=[0, 100], title="Favourable %")
    st.plotly_chart(_plotly_layout(fig, 300), use_container_width=True, config={"displayModeBar": False})

    if "india_vix" in reg.columns and reg["india_vix"].notna().any():
        st.markdown("#### 😨 India VIX (fear gauge)")
        fig = go.Figure(go.Scatter(
            x=reg["ts"], y=reg["india_vix"], mode="lines",
            line=dict(color="#f0a020", width=2), hovertemplate="VIX %{y:.1f}<extra></extra>"))
        st.plotly_chart(_plotly_layout(fig, 220), use_container_width=True, config={"displayModeBar": False})


# ── Build context once per render ───────────────────────────────────────────

def build_context() -> dict:
    data = load_data()
    trades_raw = data["trades"]
    positions = pd.DataFrame(data["positions"])
    if not positions.empty and "quantity" in positions:
        positions = positions[pd.to_numeric(positions["quantity"], errors="coerce") > 0]
    daily = pd.DataFrame(data["daily"])
    if not daily.empty:
        daily = daily.sort_values("trade_date").reset_index(drop=True)

    audit = data["audit"]
    for ev in audit:
        ev["_ts"] = _to_ist(ev.get("created_at"))
    audit.sort(key=lambda e: e["_ts"] or _now_ist(), reverse=True)

    trades_all = pd.DataFrame(trades_raw)
    open_trades = trades_all[trades_all.get("status") == "OPEN"] if not trades_all.empty else pd.DataFrame()
    tdf = analytics.prepare_trades(trades_raw, settings.PAPER_CAPITAL)
    metrics = analytics.compute_metrics(tdf, settings.PAPER_CAPITAL)

    realised_total = float(tdf["pnl"].fillna(0).sum()) if tdf is not None and not tdf.empty else 0.0
    unrealised = float(positions["unrealised_pnl"].fillna(0).sum()) if not positions.empty and "unrealised_pnl" in positions else 0.0
    today = _now_ist().date().isoformat()
    today_row = daily[daily["trade_date"] == today] if not daily.empty else pd.DataFrame()
    today_pnl = float(today_row.iloc[0]["realised_pnl"] + today_row.iloc[0]["unrealised_pnl"]) if not today_row.empty else 0.0
    circuit = bool(daily.iloc[-1]["circuit_tripped"]) if not daily.empty else False

    return {
        "tdf": tdf, "metrics": metrics, "open_trades": open_trades, "positions": positions,
        "audit": audit, "last_activity": audit[0]["_ts"] if audit else None,
        "realised_total": realised_total, "unrealised": unrealised,
        "today_pnl": today_pnl, "circuit": circuit,
    }


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    if not _check_password():
        return

    head = st.columns([3, 1])
    head[0].markdown("## 📈 Trading Agent")
    head[0].caption(f"Autonomous · {settings.TRADING_MODE.upper()} mode · "
                    f"Nifty 100 universe ({len(WATCHLIST)} stocks) · Oracle Cloud")
    if head[1].button("↻ Refresh", use_container_width=True):
        load_data.clear()
        st.rerun()

    ctx = build_context()

    tabs = st.tabs(["📊 Overview", "📦 Positions", "📜 Trades", "📈 Analytics", "🧠 Decisions", "🌍 Market"])
    with tabs[0]:
        render_overview(ctx)
    with tabs[1]:
        render_positions(ctx)
    with tabs[2]:
        render_history(ctx)
    with tabs[3]:
        render_analytics(ctx)
    with tabs[4]:
        render_decisions(ctx)
    with tabs[5]:
        render_market(ctx)

    st.caption(f"Data: Supabase (read-only) · refreshed {_now_ist().strftime('%H:%M:%S IST')} · "
               "cached 30s — hit ↻ Refresh for the latest.")

    with st.sidebar:
        st.markdown("### How it works")
        st.markdown(
            f"""
The agent trades **paper money** (₹{settings.PAPER_CAPITAL:,.0f} virtual). No real funds at risk.

**Daily routine (IST)**
- **09:20** — scans Nifty 100 for buy setups
- **every 15 min** — re-checks held stocks
- **15:35** — writes the day's report

**Decision pipeline**
1. **Strategy** — chart pattern
2. **Compliance** — allowed & liquid?
3. **Conviction** — fundamentals + news + market mood
4. **Risk officer** — position sizing (1% risk cap)
5. **Executor** — places stop-loss + target
"""
        )


main()
