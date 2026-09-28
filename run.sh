#!/usr/bin/env bash
#
# Single-command launcher.
#
#   ./run.sh          build the frontend, then serve API + UI on one port
#   ./run.sh dev      run uvicorn --reload and the Vite dev server together
#   ./run.sh test     run the backend test suite
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
# PORT is what tooling injects for the port a browser should open. Kept
# separate from the resolved serve port so `dev` can tell "injected" from
# "defaulted" — it needs the injected one for the UI, not for the API.
INJECTED_PORT="${PORT:-}"
SERVE_PORT="${INJECTED_PORT:-${ANDON_PORT:-8000}}"
MODE="${1:-serve}"

log() { printf '\033[2m[andon]\033[0m %s\n' "$*"; }

# Ask the OS for an unused port. Dev mode needs two of them, and neither may
# collide with whatever else is already listening on this machine.
free_port() {
  "$PY" -c 'import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()'
}

ensure_python() {
  if [[ ! -x "$PY" ]]; then
    log "creating virtualenv at .venv"
    python3 -m venv "$VENV"
  fi
  if ! "$PY" -c 'import fastapi, uvicorn, httpx, pydantic_settings, PIL' 2>/dev/null; then
    log "installing backend dependencies"
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet -e "$ROOT/backend[dev]"
  fi
}

ensure_frontend_deps() {
  if ! command -v npm >/dev/null 2>&1; then
    log "npm not found — skipping the UI; the API will still serve on :$SERVE_PORT"
    return 1
  fi
  if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
    log "installing frontend dependencies"
    (cd "$ROOT/frontend" && npm install --no-fund --no-audit --silent)
  fi
  return 0
}

build_frontend() {
  ensure_frontend_deps || return 0
  if [[ ! -d "$ROOT/frontend/dist" || -n "$(find "$ROOT/frontend/src" "$ROOT/frontend/index.html" -newer "$ROOT/frontend/dist/index.html" 2>/dev/null)" ]]; then
    log "building frontend"
    (cd "$ROOT/frontend" && npm run build --silent)
  fi
}

case "$MODE" in
  serve)
    ensure_python
    build_frontend
    log "starting on http://127.0.0.1:$SERVE_PORT  (API docs at /docs)"
    cd "$ROOT/backend"
    exec "$PY" -m uvicorn andon.main:app --host "${ANDON_HOST:-127.0.0.1}" --port "$SERVE_PORT"
    ;;

  dev)
    ensure_python
    ensure_frontend_deps || { log "npm is required for dev mode"; exit 1; }

    # Two servers, two ports. PORT (injected by tooling) belongs to the UI,
    # because that is the one a browser should open; the API gets ANDON_PORT
    # or a free port picked at random. Defaulting the API to a fixed 8000 is
    # what made this collide with unrelated services.
    UI_PORT="${INJECTED_PORT:-5173}"
    API_PORT="${ANDON_PORT:-$(free_port)}"
    if [[ "$API_PORT" == "$UI_PORT" ]]; then
      API_PORT="$(free_port)"
    fi

    cd "$ROOT/backend"
    "$PY" -m uvicorn andon.main:app --host 127.0.0.1 --port "$API_PORT" --reload &
    BACKEND_PID=$!
    trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT INT TERM
    log "API on :$API_PORT (reloading), UI on :$UI_PORT"
    cd "$ROOT/frontend"
    # --strictPort so a silent fallback cannot leave the preview pointing at
    # a port nothing is serving.
    ANDON_API_URL="http://127.0.0.1:$API_PORT" \
      npm run dev -- --port "$UI_PORT" --strictPort
    ;;

  test)
    ensure_python
    cd "$ROOT/backend"
    exec "$PY" -m pytest "${@:2}"
    ;;

  *)
    echo "usage: ./run.sh [serve|dev|test]" >&2
    exit 2
    ;;
esac
