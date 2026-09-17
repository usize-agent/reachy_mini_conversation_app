#!/bin/sh
# Stop the reachy-fps UI and the Reachy Mini daemon started by start.sh,
# from any shell. The daemon normally gets a clean shutdown (robot goes to
# sleep); if it doesn't exit within 15s it is force-killed.
set -u
LAUNCHER="Reachy Mini Daemon.app/Contents/MacOS/daemon-launcher"

pkill -INT -f "reachy-fps/reachy-fps( |$)" 2>/dev/null && echo "stopped UI"
pkill -f "tailscale serve --https=443 http://127.0.0.1:" 2>/dev/null && echo "stopped tailscale serve"
pkill -f "while :; do caffeinate -u" 2>/dev/null && echo "stopped keep-awake"

launchers=$(pgrep -f "$LAUNCHER")
if [ -z "$launchers" ]; then
  echo "daemon not running"
  exit 0
fi
daemons=$(for p in $launchers; do pgrep -P "$p"; done)

kill -TERM $launchers 2>/dev/null
echo "stopping daemon…"
i=0
while pgrep -f "$LAUNCHER" >/dev/null; do
  i=$((i + 1))
  if [ $i -gt 30 ]; then
    echo "daemon didn't exit in 15s (stuck on hardware or a permission prompt); killing it"
    kill -KILL $daemons $launchers 2>/dev/null
    sleep 0.5
    break
  fi
  sleep 0.5
done
echo "done"
