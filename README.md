# Trading Agent

Autonomous swing trading agent for Indian markets.
Built with Claude Code. Runs on Oracle Cloud. Trades via Angel SmartAPI.

Status: Paper trading — live on Oracle Cloud since 2026-06-12
Owner: Somacharan
Started: 2026-05-13
Built: 2026-05-14 (Days 1–10 complete)

## Monitoring dashboard

A live web interface showing P&L, open positions, completed trades and a
plain-language "decision diary" of everything the agent does:

```bash
pip install -r dashboard/requirements.txt   # first time only
streamlit run dashboard/app.py              # opens http://localhost:8501
```

Reads the same Supabase tables the agent writes to (read-only) and
auto-refreshes every 30 seconds.
