#!/bin/sh
set -eu

echo "[youtube-loader] starting BgUtils POT provider on port 4416"
node /opt/bgutil/server/build/main.js --port 4416 &
BGUTIL_PID=$!

cleanup() {
  kill "$BGUTIL_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[youtube-loader] waiting for POT provider healthcheck..."
i=0
until curl -fsS http://127.0.0.1:4416/ping >/dev/null 2>&1; do
  i=$((i+1))
  if ! kill -0 "$BGUTIL_PID" 2>/dev/null; then
    echo "[youtube-loader] ERROR: POT provider exited during startup"
    wait "$BGUTIL_PID" || true
    exit 1
  fi
  if [ "$i" -ge 20 ]; then
    echo "[youtube-loader] ERROR: POT provider did not become ready"
    exit 1
  fi
  sleep 1
done

echo "[youtube-loader] POT provider ready"
echo "[youtube-loader] starting FastAPI on :${PORT:-10000}"
exec uvicorn backend.app:app --host 0.0.0.0 --port "${PORT:-10000}"
