"""
Health check — run every 5 minutes via cron on the Oracle VM.
Alerts Telegram if the trading agent process is not running.

Crontab entry (run as ubuntu):
    */5 * * * * /home/ubuntu/trading-agent/.venv/bin/python \
        /home/ubuntu/trading-agent/scripts/health_check.py \
        >> /home/ubuntu/trading-agent/logs/health.log 2>&1

Install:
    crontab -e   # paste the line above
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Bootstrap path so config/settings loads correctly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from reporting.reporter import send_alert


SERVICE = "trading-agent"


def _service_active() -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", SERVICE],
        capture_output=True, text=True,
    )
    return result.stdout.strip() == "active"


def _recent_log_ok() -> bool:
    """True if the log file was written to in the last 30 minutes."""
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    logs = sorted(log_dir.glob("agent_*.log"), reverse=True)
    if not logs:
        return False
    import time
    age_min = (time.time() - logs[0].stat().st_mtime) / 60
    return age_min < 30


def main() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not _service_active():
        print(f"[{now}] UNHEALTHY — service '{SERVICE}' is not active. Alerting Telegram.")
        send_alert(
            f"<b>Agent DOWN</b> — {now}\n"
            f"Service <code>{SERVICE}</code> is not running on the VM.\n"
            f"SSH in and check: <code>sudo systemctl status {SERVICE}</code>",
            emoji="🚨",
        )
        # Attempt auto-restart
        subprocess.run(["sudo", "systemctl", "restart", SERVICE])
        print(f"[{now}] Restart attempted.")
        return

    if not _recent_log_ok():
        print(f"[{now}] WARNING — service active but log file is stale (>30 min).")
        send_alert(
            f"⚠️ <b>Agent Warning</b> — {now}\n"
            f"Service is running but no log activity in 30+ minutes.",
            emoji="",
        )
        return

    print(f"[{now}] OK — {SERVICE} is healthy.")


if __name__ == "__main__":
    main()
