#!/usr/bin/env bash
# Restart UniRabbit — targets ONLY the bot process, never other Python processes.
set -euo pipefail
BOT_DIR="E:/UniRabbit/UniRabbitSaved/TradingBot"
PID_FILE="$BOT_DIR/bot.pid"

cd "$BOT_DIR"

# Kill previous bot instance by PID file
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if taskkill //PID "$OLD_PID" //F 2>/dev/null; then
        echo "Killed previous bot PID $OLD_PID"
    else
        echo "No process at PID $OLD_PID (already dead)"
    fi
    rm -f "$PID_FILE"
fi

sleep 3

# Clean stale lock
rm -f run.lock
rm -rf __pycache__ app/__pycache__ app/exchanges/__pycache__ app/core/__pycache__

# Start bot and capture its PID
FORCE_START=1 nohup python run.py > /tmp/unirabbit.log 2>&1 &
BOT_PID=$!
echo "$BOT_PID" > "$PID_FILE"
echo "Bot started with PID $BOT_PID"
