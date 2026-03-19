#!/bin/bash

# ──────────────────────────────────────────────────
#  DealRadar — Start Script
#  Run this from the car-deal-finder/ folder:
#    chmod +x start.sh && ./start.sh
# ──────────────────────────────────────────────────

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$DIR/data_pipeline/.env"
VENV="$DIR/.venv"

echo ""
echo "🚗  DealRadar — AI Car Deal Finder"
echo "────────────────────────────────────"

# 1. Check Python
if ! command -v python3 &>/dev/null; then
  echo "❌ Python 3 not found. Install it from https://python.org"
  exit 1
fi

# 2. Create virtualenv if missing
if [ ! -d "$VENV" ]; then
  echo "📦 Creating virtual environment..."
  python3 -m venv "$VENV"
fi

source "$VENV/bin/activate"

# 3. Install dependencies
echo "📦 Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r "$DIR/requirements.txt"

# 4. Check .env
if [ ! -f "$ENV_FILE" ]; then
  echo ""
  echo "⚠️  No .env file found at data_pipeline/.env"
  echo "   Copy data_pipeline/.env.example → data_pipeline/.env"
  echo "   and add your AUTO_DEV_API_KEY"
  echo ""
  read -p "Press Enter to continue anyway (searches won't work)..."
fi

# 5. Open browser after short delay
(sleep 2 && open "http://localhost:8000") &

# 6. Start server
echo ""
echo "✅ Starting server at http://localhost:8000"
echo "   Press Ctrl+C to stop"
echo ""
cd "$DIR/api"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
