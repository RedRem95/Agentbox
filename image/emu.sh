#!/usr/bin/env bash
# emu – look at and restart the Android emulator containers from inside the box.
# Goes through the restricted Docker proxy (android.docker_control: true): only these containers,
# only reading, copying files out and start/stop/restart. No exec.
set -euo pipefail

LIST=/etc/agentbox/emulators
OUT=${EMU_OUT:-/tmp/emu}

usage() {
  cat <<'EOF'
usage: emu <command> [emulator] [options]

  status                     state of all emulators: running?, restarts, OOM-killed?, exit code,
                             memory use vs. limit, adb connection
  logs <emu> [lines] [-f]    container log (default: last 300 lines), -f to follow
  files <emu> [path]         copy log files out of the container to /tmp/emu/<emu>/
                             (default: the usual docker-android log/status locations)
  restart <emu>              restart the container (adb reconnects by itself)
  stop|start <emu>
  wait <emu> [seconds]       wait until the emulator is back in `adb devices` (default 300 s)

<emu> is the container name or its short form (phone, tablet).
EOF
}

case "${1:-}" in -h|--help|help) usage; exit 0 ;; esac
[[ -n "${DOCKER_HOST:-}" ]] || { echo "No Docker access – set android.docker_control: true in the agentbox config." >&2; exit 1; }
[[ -s "$LIST" ]] || { echo "No emulators configured." >&2; exit 1; }
mapfile -t EMUS < "$LIST"

resolve() {
  local e
  for e in "${EMUS[@]}"; do
    if [[ "$e" == "$1" || "$e" == *-"$1" ]]; then echo "$e"; return; fi
  done
  echo "unknown emulator '$1' (known: ${EMUS[*]})" >&2
  exit 1
}

api() { curl -fsS --max-time 30 "http://${DOCKER_HOST#tcp://}$1"; }

adb_state() {  # adb state of an emulator container (via its current IP)
  local ip
  ip=$(getent hosts "$1" | awk '{print $1; exit}')
  [[ -n "$ip" ]] || { echo "no-ip"; return; }
  adb devices 2>/dev/null | awk -v s="$ip:5555" '$1 == s {print $2; f=1} END {if (!f) print "not-connected"}'
}

human() { awk -v b="${1:-0}" 'BEGIN { printf "%.1fG", b / 1024^3 }'; }

cmd_status() {
  local e state mem
  for e in "${EMUS[@]}"; do
    state=$(docker inspect -f 'status={{.State.Status}} restarts={{.RestartCount}} oom_killed={{.State.OOMKilled}} exit_code={{.State.ExitCode}} started={{.State.StartedAt}} finished={{.State.FinishedAt}}{{if .State.Error}} error="{{.State.Error}}"{{end}}' "$e" 2>&1) || true
    mem=$(api "/containers/$e/stats?stream=false" 2>/dev/null \
      | jq -r '"\(.memory_stats.usage // 0) \(.memory_stats.limit // 0)"' 2>/dev/null) || mem=""
    echo "$e"
    echo "  $state"
    if [[ -n "$mem" ]]; then
      read -r used limit <<<"$mem"
      echo "  memory=$(human "$used")/$(human "$limit") adb=$(adb_state "$e")"
    else
      echo "  adb=$(adb_state "$e")"
    fi
  done
}

cmd_logs() {
  local e lines=300 follow=()
  e=$(resolve "${1:?emulator missing}"); shift
  for a in "$@"; do
    if [[ "$a" == -f ]]; then follow=(-f); elif [[ "$a" =~ ^[0-9]+$ ]]; then lines=$a; fi
  done
  docker logs --timestamps --tail "$lines" "${follow[@]}" "$e" 2>&1
}

cmd_files() {
  local e dest paths p got=0
  e=$(resolve "${1:?emulator missing}")
  dest="$OUT/${e}"
  mkdir -p "$dest"
  if [[ -n "${2:-}" ]]; then
    paths=("$2")
  else
    # typical docker-android locations (log dirs, supervisor logs, device status)
    paths=(/home/androidusr/logs /var/log/supervisor /home/androidusr/device_status /home/androidusr/emulator/config.ini)
  fi
  for p in "${paths[@]}"; do
    if docker cp "$e:$p" - 2>/dev/null | tar -x -C "$dest" 2>/dev/null; then
      echo "copied $p"; got=1
    fi
  done
  ((got)) || { echo "nothing found at: ${paths[*]}" >&2; exit 1; }
  echo "-> $dest"
  find "$dest" -type f -printf '   %P (%s bytes)\n' | head -50
}

cmd_wait() {
  local e secs=${2:-300} i
  e=$(resolve "${1:?emulator missing}")
  for ((i = 0; i < secs; i += 5)); do
    if [[ "$(adb_state "$e")" == device ]]; then echo "$e is back ($((i))s)"; return; fi
    sleep 5
  done
  echo "$e not in adb devices after ${secs}s" >&2
  exit 1
}

case "${1:-status}" in
  status) cmd_status ;;
  logs) shift; cmd_logs "$@" ;;
  files) shift; cmd_files "$@" ;;
  restart|stop|start)
    c=$1; e=$(resolve "${2:?emulator missing}")
    docker "$c" "$e" >/dev/null && echo "$c: $e"
    [[ "$c" == stop ]] || echo "adb reconnects automatically; 'emu wait ${2}' blocks until it's back" ;;
  wait) shift; cmd_wait "$@" ;;
  -h|--help|help) usage ;;
  *) usage >&2; exit 1 ;;
esac
