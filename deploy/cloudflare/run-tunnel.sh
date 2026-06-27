#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Deploy the India Procurement Watch dashboard on Cloudflare's network, free.
#
# Runs the Flask app with a production WSGI server (gunicorn) and exposes it
# through a free Cloudflare *quick tunnel* — you get a public
# https://<random>.trycloudflare.com URL with NO Cloudflare account, NO domain,
# and NO code changes. The whole app works: charts, search, Contract Network,
# and the Sector map.
#
# Prereqs (one-time):
#   pip install -r requirements.txt          # includes gunicorn
#   # cloudflared:
#   #   macOS:  brew install cloudflared
#   #   linux:  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
#
# Usage:
#   bash deploy/cloudflare/run-tunnel.sh
#   PORT=8080 bash deploy/cloudflare/run-tunnel.sh
# ---------------------------------------------------------------------------
set -euo pipefail

# repo root (this script lives in deploy/cloudflare/)
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

PORT="${PORT:-5055}"

command -v gunicorn   >/dev/null || { echo "✖ gunicorn not found — pip install -r requirements.txt"; exit 1; }
command -v cloudflared >/dev/null || { echo "✖ cloudflared not found — install it (see header)"; exit 1; }

[ -f summary.db ] || echo "⚠️  summary.db missing — run: python build_summary.py   (dashboard charts will be empty)"
[ -f network.db ] || echo "ℹ️  network.db missing — Contract Network tab shows a build note (python build_network.py …)"

echo "▶ starting app on 127.0.0.1:${PORT} (gunicorn) …"
# threaded: the app uses thread-local SQLite connections (check_same_thread=False)
gunicorn -w 2 --threads 8 --timeout 120 -b "127.0.0.1:${PORT}" app:app &
APP_PID=$!
trap 'echo; echo "stopping…"; kill $APP_PID 2>/dev/null || true' EXIT INT TERM
sleep 2

echo "▶ opening free Cloudflare quick tunnel — public URL appears below:"
echo "  (Ctrl-C to stop both the tunnel and the app)"
cloudflared tunnel --url "http://127.0.0.1:${PORT}"
