#!/usr/bin/env bash
# Virtueller Desktop für die agentbox: Xvfb + Openbox + tint2 (+ RustDesk)
# Aufruf (als dev): agentbox-desktop <AUFLÖSUNG> <rustdesk 0|1>
set -u
RES=${1:-1600x900}
RD=${2:-1}
export DISPLAY=:0

rm -f /tmp/.X0-lock /tmp/.X11-unix/X0
Xvfb :0 -screen 0 "${RES}x24" -nolisten tcp -ac +extension RANDR &
for _ in $(seq 1 50); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.2; done

eval "$(dbus-launch --sh-syntax)"
export DBUS_SESSION_BUS_ADDRESS

xsetroot -solid '#2b2f36' 2>/dev/null || true
openbox &
tint2 >/dev/null 2>&1 &
xterm -geometry 120x35+40+40 &

if [[ "$RD" == 1 ]]; then
  # Server-Prozess (nimmt Verbindungen an) mit Auto-Neustart, dazu die GUI (zeigt ID/Status)
  ( while true; do rustdesk --server; sleep 3; done ) &
  sleep 3
  ( while true; do rustdesk; sleep 5; done ) &
fi

wait
