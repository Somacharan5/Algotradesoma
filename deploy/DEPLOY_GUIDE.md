# Deployment Guide — Oracle Cloud + GitHub Actions

## One-time VM setup

### 1. Create Oracle Cloud VM
- Sign in at cloud.oracle.com
- Compute → Instances → Create Instance
- Shape: VM.Standard.A1.Flex (ARM, Always Free) — 1 OCPU, 6 GB RAM
- OS: Ubuntu 22.04
- Add your SSH public key (`~/.ssh/oracle_trading_agent.pub`)
- Note the **PUBLIC** IP address (shown on the instance page).
  ⚠️ Do NOT use the private IP (`10.x.x.x`) — GitHub Actions cannot reach it
  and every deploy will time out at the SSH step.

### 2. Open firewall port 22
- VCN → Security Lists → Ingress Rules
- Source: 0.0.0.0/0, Protocol: TCP, Port: 22 (SSH)

### 3. First deploy (bootstraps the VM automatically)
No manual bootstrap needed. Once the GitHub secrets below are set, run the
"Deploy to Oracle Cloud" workflow (or push to `main`). The workflow rsyncs
the code to the VM (the repo is private — the VM never talks to GitHub),
installs Python + dependencies, and installs the systemd service.

### 4. Fill in your .env on the VM
```bash
ssh -i ~/.ssh/oracle_trading_agent ubuntu@<PUBLIC_IP>
nano /home/ubuntu/trading-agent/.env
# Add all keys from .env.example
sudo systemctl start trading-agent
sudo systemctl status trading-agent
```

### 5. Set up health check cron (on the VM)
```bash
crontab -e
# Add this line:
*/5 * * * * /home/ubuntu/trading-agent/.venv/bin/python /home/ubuntu/trading-agent/scripts/health_check.py >> /home/ubuntu/trading-agent/logs/health.log 2>&1
```

---

## GitHub Actions secrets

Go to: https://github.com/Somacharan5/Algotradesoma/settings/secrets/actions

Add these 4 secrets:

| Secret | Value |
|---|---|
| `ORACLE_HOST` | Your VM **PUBLIC** IP (e.g. `140.238.x.x` — never `10.x.x.x`) |
| `ORACLE_USER` | `ubuntu` |
| `ORACLE_SSH_KEY` | Your **private** SSH key (contents of `~/.ssh/oracle_trading_agent`) |
| `TELEGRAM_BOT_TOKEN` | Your bot token |
| `TELEGRAM_CHAT_ID` | Your chat ID |

---

## How deploys work

1. Push any commit to `main`
2. GitHub Actions SSHs into your Oracle VM
3. Pulls latest code from git
4. Updates pip dependencies
5. Restarts the `trading-agent` systemd service
6. Verifies service is active
7. Agent sends "Trading Agent Online" to Telegram on startup

---

## Useful VM commands

```bash
# Live logs
journalctl -u trading-agent -f

# Restart manually
sudo systemctl restart trading-agent

# Check status
sudo systemctl status trading-agent

# View log files
tail -f /home/ubuntu/trading-agent/logs/agent_$(date +%Y-%m-%d).log
```
