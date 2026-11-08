#!/usr/bin/env bash
# Hält adb-Verbindungen zu Emulatoren in anderen Containern aufrecht.
# Aufruf: adb-watch host[:port] ...   (Port-Default 5555)
# Hostnamen werden bei jedem Durchlauf neu aufgelöst -> überlebt IP-Wechsel nach Emulator-Neustart.
set -u
INTERVAL=${ADB_WATCH_INTERVAL:-10}

adb start-server >/dev/null 2>&1

while true; do
  state=$(adb devices 2>/dev/null)
  for d in "$@"; do
    host=${d%:*}; port=${d##*:}
    [[ "$host" == "$port" ]] && port=5555
    ip=$(getent hosts "$host" | awk '{print $1; exit}')
    [[ -z "$ip" ]] && continue
    serial="$ip:$port"
    st=$(awk -v s="$serial" '$1==s {print $2}' <<<"$state")
    case "$st" in
      device) ;;
      "")
        if adb connect "$serial" 2>/dev/null | grep -q '^connected'; then
          echo "$(date +%T) verbunden: $d ($serial)"
        fi ;;
      *)  # offline / unauthorized -> neu verbinden
        adb disconnect "$serial" >/dev/null 2>&1
        adb connect "$serial" >/dev/null 2>&1 ;;
    esac
  done
  sleep "$INTERVAL"
done
