#!/bin/sh
D="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$D/launcher/logs"
echo "=== $(date) DISPLAY=$DISPLAY ===" >> "$D/launcher/logs/start.log"
exec "$D/python-linux/cpython-3.11-linux-x86_64-gnu/bin/python3" "$D/launcher/launcher.py" >> "$D/launcher/logs/start.log" 2>&1
