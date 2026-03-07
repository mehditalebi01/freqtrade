#!/bin/bash
set -e

# ============================================================
# Heroku Startup Script for Freqtrade
# ============================================================
# Heroku dynamically assigns $PORT — Freqtrade must bind to it.
# Heroku Postgres provides $DATABASE_URL — we convert it for
# SQLAlchemy compatibility.
# ============================================================

# Default port fallback for local testing
PORT="${PORT:-8080}"

# Convert Heroku Postgres URL for SQLAlchemy compatibility
# Heroku uses "postgres://" but SQLAlchemy 2.x requires "postgresql://"
if [ -n "$DATABASE_URL" ]; then
    DB_URL=$(echo "$DATABASE_URL" | sed 's|^postgres://|postgresql://|')
else
    # Fallback to SQLite for local development / dry-run testing
    DB_URL="sqlite:///user_data/tradesv3.sqlite"
    echo "WARNING: DATABASE_URL not set. Using SQLite (data will be lost on Heroku dyno restart)."
fi

echo "Starting Freqtrade on port ${PORT}..."
echo "Database: ${DB_URL%%@*}@***"

# Override API server port via environment variable
# Freqtrade reads FREQTRADE__<section>__<key> env vars as config overrides
export FREQTRADE__API_SERVER__LISTEN_PORT="${PORT}"
export FREQTRADE__API_SERVER__LISTEN_IP_ADDRESS="0.0.0.0"
export FREQTRADE__API_SERVER__ENABLED="true"

# Launch Freqtrade in trade mode
exec freqtrade trade \
    --logfile /freqtrade/user_data/logs/freqtrade.log \
    --db-url "${DB_URL}" \
    --config /freqtrade/user_data/config_gpt.json \
    --strategy MTFTrendATRStrategy
