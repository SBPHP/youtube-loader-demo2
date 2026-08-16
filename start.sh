#!/bin/sh
set -eu

echo "[youtube-loader] starting BgUtils POT provider on 127.0.0.1:4416"
node /opt/bgutil/server/build/main.js --host 127.0.0.1 --port 4416 &
BGUTIL_PID=$!

cleanup() {
  kill "$BGUTIL_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Give the provider a short moment to bind. The app runtime center performs the real check.
sleep 2

echo "[youtube-loader] starting FastAPI on :${PORT:-10000}"
exec uvicorn backend.app:app --host 0.0.0.0 --port "${PORT:-10000}"
