#!/bin/sh
# Start the Reachy Mini daemon and the reachy-fps UI together, from any
# terminal (tmux and SSH included). Ctrl-C, closing the terminal, or
# ./stop.sh from another shell stops both.
#
#   ./start.sh [reachy-fps flags...]
#
# REACHY_DAEMON overrides the daemon executable (default: the venv one level up).
# REACHY_DAEMON_ARGS passes extra daemon flags.
# REACHY_WAKE=0 leaves the motors off after startup (wake from the UI later).
# REACHY_PORT sets the UI port (default 8080).
# REACHY_NOSLEEP=0 skips disabling macOS sleep (which asks for your sudo password).
# REACHY_TAILSCALE=0 skips publishing the UI over HTTPS with `tailscale serve`
# (which is what lets browsers on other tailnet devices use the microphone).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
DAEMON=${REACHY_DAEMON:-"$HERE/../.venv/bin/reachy-mini-daemon"}
LOG="$HOME/.reachy/daemon.log"
APP="$HERE/dist/Reachy Mini Daemon.app"
PORT=${REACHY_PORT:-8080}

if curl -s -m 1 -o /dev/null http://localhost:8000/api/daemon/status; then
  echo "A daemon is already running on :8000. Stop it first (or run ./reachy-fps on its own)." >&2
  exit 1
fi
[ -x "$DAEMON" ] || { echo "daemon not found: $DAEMON (set REACHY_DAEMON)" >&2; exit 1; }

cd "$HERE"
go build -o reachy-fps .
mkdir -p "$(dirname "$LOG")"
: > "$LOG"

daemon_pids() { pgrep -f "Reachy Mini Daemon.app/Contents/MacOS/daemon-launcher" || true; }

cleanup() {
  trap - INT TERM HUP EXIT
  echo "stopping…"
  [ -n "${TS_PID:-}" ] && kill "$TS_PID" 2>/dev/null || true
  [ -n "${UI_PID:-}" ] && kill "$UI_PID" 2>/dev/null || true
  [ -n "${TAIL_PID:-}" ] && kill "$TAIL_PID" 2>/dev/null || true
  [ -n "${AWAKE_PID:-}" ] && { pkill -P "$AWAKE_PID" 2>/dev/null; kill "$AWAKE_PID" 2>/dev/null; } || true
  for pid in $(daemon_pids); do kill -TERM "$pid" 2>/dev/null || true; done
  # Give the daemon time to park the robot.
  i=0
  while [ -n "$(daemon_pids)" ] && [ $i -lt 30 ]; do sleep 0.5; i=$((i + 1)); done
}
trap cleanup INT TERM HUP EXIT

if [ "$(uname)" = Darwin ]; then
  # With the lid closed, macOS drops to DarkWake ("Clamshell Sleep"): SSH keeps
  # working, but CoreAudio stops delivering data, and the daemon won't publish
  # video without audio. Power assertions can't prevent clamshell sleep; only
  # `pmset disablesleep` (or an attached display) does.
  if pmset -g | grep -q "SleepDisabled[[:space:]]*1"; then
    echo "system sleep is already disabled; leaving it that way"
  elif [ "${REACHY_NOSLEEP:-1}" != 0 ] && echo "disabling system sleep while running (sudo pmset)…" && sudo -v; then
    # A root guard turns sleep back on once this script is gone, however it
    # exits (Ctrl-C, ./stop.sh, closed terminal, even kill -9).
    sudo sh -c "trap '' HUP INT TERM
      pmset -a disablesleep 1
      while kill -0 $$ 2>/dev/null; do sleep 2; done
      pmset -a disablesleep 0" </dev/null >/dev/null 2>&1 &
  else
    echo "note: system sleep stays enabled. With the lid closed, video will freeze whenever"
    echo "      macOS enters DarkWake; caffeinate wakes it back up in ~30s."
    sh -c 'while :; do caffeinate -u -d -i -t 60 & sleep 20; done' &
    AWAKE_PID=$!
  fi
  [ -d "$APP" ] || ./macos/build-app.sh
  # Launch through LaunchServices so the app, not tmux, owns camera access.
  # shellcheck disable=SC2086
  open -n -g -a "$APP" --args -daemon "$DAEMON" -log "$LOG" -- --no-wake-up-on-start ${REACHY_DAEMON_ARGS:-}
else
  # shellcheck disable=SC2086
  "$DAEMON" --no-wake-up-on-start ${REACHY_DAEMON_ARGS:-} >> "$LOG" 2>&1 &
fi

tail -n +1 -f "$LOG" | sed -u 's/^/[daemon] /' &
TAIL_PID=$!

echo "waiting for daemon…"
i=0
until curl -s -m 1 -o /dev/null http://localhost:8000/api/daemon/status; do
  i=$((i + 1))
  if [ $i -eq 8 ] && [ "$(uname)" = Darwin ] && pgrep -x UserNotificationCenter >/dev/null; then
    echo "  macOS is showing a permission prompt on the Mac's screen (Camera/Microphone"
    echo "  for \"Reachy Mini Daemon\"). The daemon is blocked until someone clicks Allow;"
    echo "  over SSH, use Screen Sharing. This only happens once."
  fi
  if [ $i -gt 180 ]; then
    echo "timed out after 3 minutes; see $LOG" >&2
    exit 1
  fi
  sleep 1
done
echo "daemon up"

# ---- Staged bring-up -----------------------------------------------------------
# Everything used to start at once: camera + USB audio + the daemon's fast
# wake-up move. That combined surge is the prime suspect for the motor bus
# dropping out and the USB camera hanging. So: media first, then motors, then
# one slow move.
API=http://localhost:8000/api
PY="$(dirname "$DAEMON")/python"

video_ready() {
  "$PY" -c "from reachy_mini.media.webrtc_utils import get_producer_list as g; import sys; sys.exit(0 if g('127.0.0.1', 8443) else 1)" 2>/dev/null
}
echo "waiting for camera stream…"
i=0
until video_ready; do
  i=$((i + 1))
  if [ $i -ge 20 ]; then
    echo "  no video stream after 20s. Motion will still work."
    echo "  The stream needs both camera and audio. If the Mac is in DarkWake (lid closed),"
    echo "  CoreAudio delivers nothing: check pmset -g log, or open the lid."
    break
  fi
  sleep 1
done
[ $i -lt 20 ] && echo "camera stream up"

if [ "${REACHY_WAKE:-1}" != 0 ]; then
  echo "enabling motors (holding current pose)…"
  curl -s -m 10 -X POST "$API/motors/set_mode/enabled" >/dev/null
  sleep 1.5
  status=$(curl -s -m 3 "$API/daemon/status")
  case "$status" in
    *'"error":null'*) ;;
    *) echo "  daemon reports an error after enabling motors; not moving. $status" ;;
  esac
  case "$status" in
    *'"error":null'*)
      echo "gliding to neutral over 4s…"
      curl -s -m 10 -X POST "$API/move/goto" -H 'Content-Type: application/json' \
        -d '{"head_pose":{"x":0,"y":0,"z":0,"roll":0,"pitch":0,"yaw":0},"antennas":[-0.1745,0.1745],"body_yaw":0,"duration":4.0,"interpolation":"minjerk"}' >/dev/null
      sleep 4.5
      ;;
  esac
fi

"$HERE/reachy-fps" -listen "0.0.0.0:$PORT" "$@" &
UI_PID=$!

# Publish the UI as https://<machine>.<tailnet>.ts.net so the mic works from
# other devices. Foreground `tailscale serve` removes its config when it exits.
start_tailscale() {
  [ "${REACHY_TAILSCALE:-1}" = 0 ] && return
  command -v tailscale >/dev/null 2>&1 || return
  status_json=$(tailscale status --json 2>/dev/null) || { echo "[tailscale] not running; skipping HTTPS"; return; }
  host=$(printf '%s' "$status_json" | tr -d '\n' | sed -n 's/.*"Self": *{[^}]*"DNSName": *"\([^"]*\)\.".*/\1/p')
  if printf '%s' "$status_json" | tr -d ' \n' | grep -q '"CertDomains":null'; then
    echo "[tailscale] HTTPS certificates are off for your tailnet, so voice only works on localhost."
    echo "[tailscale] Enable once at https://login.tailscale.com/admin/dns (HTTPS Certificates), then restart."
    return
  fi
  if ! tailscale serve status 2>/dev/null | grep -q "No serve config"; then
    echo "[tailscale] an existing serve config is present; not touching it. Voice needs HTTPS (see README)."
    return
  fi
  tailscale serve --https=443 "http://127.0.0.1:$PORT" 2>&1 | sed -u 's/^/[tailscale] /' &
  TS_PID=$!
  echo "[tailscale] https://$host/"
}
start_tailscale

wait "$UI_PID"
