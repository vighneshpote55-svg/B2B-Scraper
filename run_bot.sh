#!/usr/bin/env bash
# ==============================================================================
# LeadMap Pro Telegram Bot Launcher
# ==============================================================================
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "=================================================================="
echo "🤖 Starting LeadMap Pro Telegram Bot"
echo "=================================================================="

# Check if .env exists
if [ ! -f ".env" ]; then
  echo "⚠️ .env file not found. Copying from .env.example..."
  cp .env.example .env
fi

# Verify TELEGRAM_BOT_TOKEN is set
if ! grep -q "^TELEGRAM_BOT_TOKEN=.\+" .env; then
  echo "❌ Error: TELEGRAM_BOT_TOKEN is not configured in your .env file."
  echo "Please edit .env and provide your bot token from @BotFather:"
  echo "TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
  echo "TELEGRAM_ALLOWED_USER_IDS=your_telegram_user_id"
  exit 1
fi

echo "✔ Configuration verified."
echo "✔ Starting bot long-polling service..."
echo "=================================================================="

python3 scripts/telegram_bot.py
