#!/usr/bin/env bash
# ==============================================================================
# LeadMap Pro Dashboard Launcher
# ==============================================================================
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "=================================================================="
echo "🚀 Starting LeadMap Pro (Google Maps Scraper & Lead Intelligence)"
echo "=================================================================="

# Check Docker container
if ! docker ps --format '{{.Names}}' | grep -q "^gmaps-scraper$"; then
  echo "▶ Scraper container not running. Starting via docker compose..."
  docker compose up -d
  echo "  waiting for scraper to initialize..."
  sleep 4
fi

echo "✔ Scraper backend is active on http://localhost:8085"
echo "✔ Starting interactive Web Dashboard on http://localhost:3000 ..."
echo ""
echo "Open your browser at:"
echo "👉  http://localhost:3000"
echo "=================================================================="

python3 dashboard.py
