#!/bin/sh
# Starter for Linux: makes sure portable uv + Python 3.11 exist, then opens the launcher window.
D="$(cd "$(dirname "$0")" && pwd)"
cd "$D" || exit 1
mkdir -p launcher/logs
LOG="$D/launcher/logs/start.log"
export UV_PYTHON_INSTALL_DIR="$D/python-linux"
export UV_CACHE_DIR="$D/.uv-cache-linux"
export UV_PYTHON_PREFERENCE=only-managed
export UV_PYTHON_INSTALL_BIN=0
UV="$D/bin-linux/uv"

say() { echo "[start] $*" | tee -a "$LOG"; }

if [ ! -x "$UV" ]; then
  case "$(uname -m)" in
    x86_64) ARCH=x86_64 ;;
    aarch64|arm64) ARCH=aarch64 ;;
    *) say "unsupported CPU: $(uname -m)"; exit 1 ;;
  esac
  URL="https://github.com/astral-sh/uv/releases/latest/download/uv-$ARCH-unknown-linux-gnu.tar.gz"
  say "downloading uv ..."
  mkdir -p "$D/bin-linux"
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "$D/uv.tar.gz" "$URL" >>"$LOG" 2>&1
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$D/uv.tar.gz" "$URL" >>"$LOG" 2>&1
  else
    say "need curl or wget (sudo apt install curl)"; exit 1
  fi
  tar -xzf "$D/uv.tar.gz" --strip-components=1 -C "$D/bin-linux" >>"$LOG" 2>&1
  rm -f "$D/uv.tar.gz"
  [ -x "$UV" ] || { say "uv download failed, see $LOG"; exit 1; }
fi

PY="$("$UV" python find 3.11 2>/dev/null)"
if [ -z "$PY" ]; then
  say "installing Python 3.11 into $D/python-linux ..."
  "$UV" python install 3.11 2>&1 | tee -a "$LOG"
  PY="$("$UV" python find 3.11 2>/dev/null)"
fi
[ -n "$PY" ] || { say "Python 3.11 install failed, see $LOG"; exit 1; }

echo "=== $(date) ===" >> "$LOG"
exec "$PY" "$D/launcher/launcher.py" >> "$LOG" 2>&1
