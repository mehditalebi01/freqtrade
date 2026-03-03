# 🚀 Deploying Freqtrade on Heroku

A complete guide to deploying the Freqtrade crypto trading bot on Heroku using Docker containers.

---

## Prerequisites

- [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli) installed
- [Docker](https://www.docker.com/products/docker-desktop/) installed
- [Git](https://git-scm.com/) installed
- A Heroku account (credit card required for add-ons)

---

## Step 1: Create a Heroku App

```bash
# Login to Heroku
heroku login

# Create a new app (choose a unique name)
heroku create your-freqtrade-bot

# Set the stack to container (required for Docker deployments)
heroku stack:set container -a your-freqtrade-bot
```

## Step 2: Add Heroku Postgres

Heroku's filesystem is **ephemeral** — SQLite data is lost on dyno restart. Use Postgres instead:

```bash
# Add the free mini Postgres plan
heroku addons:create heroku-postgresql:essential-0 -a your-freqtrade-bot
```

This automatically sets the `DATABASE_URL` config var. The `heroku_start.sh` script handles format conversion.

## Step 3: Copy Configuration

```bash
# Copy the Heroku config template to user_data
cp config_examples/config_heroku.json user_data/config.json
```

Edit `user_data/config.json` to customize:

- `exchange.name` — your exchange (binance, bybit, okx, etc.)
- `exchange.pair_whitelist` — pairs you want to trade
- `strategy` — your strategy class name

> **Note**: Exchange keys and Telegram tokens are set via environment variables (Step 4), NOT in the config file.

## Step 4: Set Config Vars (Secrets)

Use Heroku config vars to inject sensitive data via Freqtrade's `FREQTRADE__` env var support:

```bash
# Exchange credentials
heroku config:set FREQTRADE__EXCHANGE__KEY="your_exchange_api_key" -a your-freqtrade-bot
heroku config:set FREQTRADE__EXCHANGE__SECRET="your_exchange_api_secret" -a your-freqtrade-bot

# API server credentials (change these!)
heroku config:set FREQTRADE__API_SERVER__USERNAME="your_username" -a your-freqtrade-bot
heroku config:set FREQTRADE__API_SERVER__PASSWORD="YourStr0ngP@ssword" -a your-freqtrade-bot
heroku config:set FREQTRADE__API_SERVER__JWT_SECRET_KEY="$(openssl rand -hex 32)" -a your-freqtrade-bot

# Optional: Telegram integration
heroku config:set FREQTRADE__TELEGRAM__ENABLED=true -a your-freqtrade-bot
heroku config:set FREQTRADE__TELEGRAM__TOKEN="your_telegram_bot_token" -a your-freqtrade-bot
heroku config:set FREQTRADE__TELEGRAM__CHAT_ID="your_telegram_chat_id" -a your-freqtrade-bot

# Optional: Switch to live trading (default is dry_run)
# heroku config:set FREQTRADE__DRY_RUN=false -a your-freqtrade-bot
```

## Step 5: Add Your Strategy

Place your strategy file in `user_data/strategies/`:

```bash
# Example: copy your custom strategy
cp /path/to/MyStrategy.py user_data/strategies/

# Update config.json to use your strategy
# "strategy": "MyStrategy"
```

Make sure to commit the strategy file:

```bash
git add user_data/strategies/MyStrategy.py
git commit -m "Add custom trading strategy"
```

## Step 6: Deploy

```bash
# Add Heroku remote (if not done automatically)
heroku git:remote -a your-freqtrade-bot

# Deploy
git push heroku main
```

> If your branch is not `main`:
>
> ```bash
> git push heroku your-branch:main
> ```

## Step 7: Verify Deployment

```bash
# Check logs
heroku logs --tail -a your-freqtrade-bot

# Check if the API is responding
curl https://your-freqtrade-bot-xxxx.herokuapp.com/api/v1/ping
# Expected: {"status":"pong"}
```

Access the **FreqUI** web interface at your app's URL (e.g., `https://your-freqtrade-bot-xxxx.herokuapp.com`).

## Step 8: Scale the Dyno

```bash
# Ensure at least 1 web dyno is running
heroku ps:scale web=1 -a your-freqtrade-bot

# Check dyno status
heroku ps -a your-freqtrade-bot

# Upgrade dyno for 24/7 uptime (recommended)
heroku ps:type web=basic -a your-freqtrade-bot
# Or for better performance:
# heroku ps:type web=standard-1x -a your-freqtrade-bot
```

---

## 📋 Config Vars Quick Reference

| Variable | Description | Example |
|---|---|---|
| `DATABASE_URL` | Auto-set by Heroku Postgres | `postgres://...` |
| `FREQTRADE__EXCHANGE__KEY` | Exchange API key | `abc123...` |
| `FREQTRADE__EXCHANGE__SECRET` | Exchange API secret | `xyz789...` |
| `FREQTRADE__EXCHANGE__PASSWORD` | Exchange passphrase (if required) | `mypassphrase` |
| `FREQTRADE__API_SERVER__USERNAME` | FreqUI/API login username | `admin` |
| `FREQTRADE__API_SERVER__PASSWORD` | FreqUI/API login password | `Str0ngP@ss!` |
| `FREQTRADE__API_SERVER__JWT_SECRET_KEY` | JWT signing secret | `random-hex-string` |
| `FREQTRADE__TELEGRAM__ENABLED` | Enable Telegram bot | `true` |
| `FREQTRADE__TELEGRAM__TOKEN` | Telegram bot token | `123456:ABC-DEF...` |
| `FREQTRADE__TELEGRAM__CHAT_ID` | Telegram chat ID | `123456789` |
| `FREQTRADE__DRY_RUN` | Paper trading mode | `true` / `false` |
| `FREQTRADE__STAKE_AMOUNT` | Amount per trade | `100` or `unlimited` |

---

## 🔧 Troubleshooting

### Build fails with "TA-Lib" error

TA-Lib is compiled from source in the Dockerfile. If the build runs out of memory, you may need to use a `performance-m` dyno for the build:

```bash
heroku ps:type web=performance-m -a your-freqtrade-bot && git push heroku main
# then scale back down after build:
heroku ps:type web=basic -a your-freqtrade-bot
```

### App crashes on startup

Check the logs:

```bash
heroku logs --tail -a your-freqtrade-bot
```

Common causes:

- Missing `DATABASE_URL` — ensure Postgres add-on is attached
- Invalid config — validate your JSON config file
- Missing strategy file — ensure the strategy class matches `config.json`

### Database connection errors

Heroku rotates database credentials periodically. The `DATABASE_URL` is automatically updated, but if you see connection errors:

```bash
heroku pg:credentials:rotate -a your-freqtrade-bot
heroku restart -a your-freqtrade-bot
```

### Dyno sleeps / bot stops trading

Eco and free dynos sleep after 30 minutes of inactivity. Upgrade to **Basic** ($7/mo) or higher:

```bash
heroku ps:type web=basic -a your-freqtrade-bot
```

---

## 🔄 Updating Freqtrade

To update to the latest version:

```bash
# Pull latest changes
git pull origin main

# Push to Heroku
git push heroku main
```

---

## ⚠️ Important Notes

1. **Start with dry-run mode** (`dry_run: true`) to test your setup before risking real money
2. **Use strong passwords** for the API server — it's publicly accessible
3. **Monitor your bot** via Telegram or the FreqUI web interface
4. **Heroku Postgres** has row limits on the free/mini plan — monitor usage for long-running bots
5. **Backtest locally** before deploying strategies — Heroku is for live/dry-run trading only
