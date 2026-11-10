#!/usr/bin/env bash
# Prints the agentbox skill (SKILL.md) for the coding agents, generated from the actual config,
# so it only describes what this box really has.
#
#   agentbox-skill <config.json> [box-name]
#
# env: DOCKER_HOST (emulator control on), AGENTBOX_DESKTOP=1 (desktop running)
set -euo pipefail

cfg=$(mktemp)
trap 'rm -f "$cfg"' EXIT
cat "${1:?config.json missing}" > "$cfg"   # also works for /dev/stdin, which can only be read once
box=${2:-$(hostname)}
j() { jq -r "$@" "$cfg"; }

workdir=$(j '.workdir // "/src"')
layout=$(j '.layout // "per-project"')
sudo_ok=$(j '.user.sudo // false')
mapfile -t repos < <(j '.repos // [] | .[] | (if type == "string" then . else .url end)
  | sub("\\.git$"; "") | split("/") | last | split(":") | last')
mapfile -t emus < <(j '.container.android_emulators // [] | .[] | (if type == "string" then . else .name end) // empty')
mapfile -t devices < <(j '.android.devices // [] | .[]')
android=$(j 'if (.android // .container.android_emulators) then "1" else "0" end')
mem=$(j '.container.resources.memory // empty')
cpus=$(j '.container.resources.cpus // empty')

toolchains() {
  j '. as $root | [
      (.packages.tools // [] | map(select(. != "android"
          and (. != "dotnet" or ($root.packages.dotnet_version // null) == null)
          and (. != "python" or ($root.packages.python.version // null) == null))) | .[]),
      (if .packages.dotnet_version then ".NET SDK \(.packages.dotnet_version)" else empty end),
      (if .packages.dotnet_workloads then "dotnet workloads: \(.packages.dotnet_workloads | join(", "))" else empty end),
      (if .packages.python.version then "Python \(.packages.python.version) (venv /opt/tools/venv, first in PATH)" else empty end),
      (if (.android // .container.android_emulators) then "JDK \(.android.jdk // 21), Android SDK (\(.android.sdk_packages // ["platform-tools"] | join(", ")))" else empty end),
      (if (.packages.apt // [] | length) > 0 then "apt: \(.packages.apt | join(", "))" else empty end)
    ] | .[] | "- \(.)"'
  echo "- Node 22, git, gh, jq, yq, ripgrep (always)"
}

cat <<EOF
---
name: agentbox
description: Use when working inside this agentbox dev container – before installing system packages or tools, when something must survive a restart, when touching Docker, the Android emulators (adb, logs, restarts), the desktop/GUI apps, GitHub access, ports or resource limits.
---

# agentbox: the environment you are running in

You run inside an agentbox container named \`$box\`, as user \`dev\`. The person you work for
manages it from the host with the \`agentbox\` tool and a YAML config. You can't change that
config or the container setup yourself – when something needs to change there, say exactly
what and why, and they apply it.

## Layout

- Repos (side by side in \`$workdir\`): $( ((${#repos[@]})) && printf '%s, ' "${repos[@]}" | sed 's/, $//' || echo none)
- Workspaces: $([[ "$layout" == root ]] && echo "one herdr workspace in $workdir that sees all repos" || echo "one herdr workspace per repo")
- sudo: $([[ "$sudo_ok" == true ]] && echo "yes, without password" || echo "no")
- The config is mounted read-only at \`/config/workspace.yaml\` – read it to see how this box is set up.

## What survives and what doesn't

The box is recreated on every \`agentbox up\` and on image updates. These survive:

- \`$workdir\` – the repos, including uncommitted work
- \`/home/dev\` – your sessions and history, logins (gh, agents), SSH key, caches
- \`/opt/tools\` – the toolchains below, npm globals, the Python venv

Everything else is lost: packages installed with \`apt\`, files in \`/tmp\`, changes in \`/etc\`.
Installing something by hand is fine for trying it out. If it's needed permanently, tell the user
which entry to add to the config (\`packages.apt\`, \`packages.tools\`, \`packages.npm\`,
\`packages.python.packages\` or a repo's \`setup\`).

## Installed toolchains

$(toolchains)

## Network and access

- Outbound internet works. Inbound, only SSH into this box is open; nothing else is published.
- GitHub: use \`gh\` (it is also git's credential helper). If \`gh auth status\` fails, ask the user
  to run \`./agentbox login gh\` on the host.
- No Docker and no access to the host$( [[ -n "${DOCKER_HOST:-}" && ${#emus[@]} -gt 0 ]] && echo ", except the emulator containers (see below)").

## Resources

$( [[ -n "$mem$cpus" ]] && echo "Limits for this box: memory ${mem:-unlimited}, CPUs ${cpus:-unlimited}." || echo "No explicit limits are configured for this box.")
\`free\` and \`nproc\` show the host's values. The real memory limit is in
\`/sys/fs/cgroup/memory.max\`, current use in \`/sys/fs/cgroup/memory.current\`. Large builds
(Gradle, .NET Android) can hit it: an OOM kill shows up as a process killed with signal 9.
EOF

if [[ "$android" == 1 ]]; then
  cat <<EOF

## Android

EOF
  if ((${#emus[@]})); then
    list=$(printf '`%s`, ' "${emus[@]}")
    echo "Emulators run in their own containers: ${list%, }."
  fi
  if ((${#devices[@]})); then
    list=$(printf '`%s`, ' "${devices[@]}")
    echo "Additional adb devices: ${list%, }."
  fi
  cat <<'EOF'
They are connected over TCP: `adb devices` lists them, the connection is re-established
automatically within ~10 s when an emulator restarts (log: `~/.android-adb-watch.log`).

- Pick a device with `adb -s <serial>`; serials are `<ip>:5555` and can change after a restart.
- A dev server in this box is NOT reachable as 10.0.2.2 from the emulator (that's the emulator's
  own container). Use `adb -s <serial> reverse tcp:<port> tcp:<port>` and `localhost:<port>` in the app.
- The MCP server `mobile` (mobile-mcp) lets you look at the screen and tap through the app.
- The user can watch the emulators in a browser via noVNC on the host.
EOF
  if [[ -n "${DOCKER_HOST:-}" ]] && ((${#emus[@]})); then
    cat <<'EOF'

### Inspecting and restarting the emulator containers

You have limited Docker access to the emulator containers only (no exec, no other containers),
wrapped in `emu`:

- `emu status` – running?, restart count, OOM-killed?, exit code, memory use vs. limit, adb state
- `emu logs <phone|tablet> [lines] [-f]` – container log with timestamps
- `emu files <phone|tablet> [path]` – copy log files out to `/tmp/emu/<container>/`
- `emu restart <phone|tablet>`, then `emu wait <phone|tablet>` until adb sees it again

When an emulator crashes: start with `emu status` (`oom_killed=true` means its memory limit is too
low – suggest a higher `memory` for it in the config), then the container log and `emu files`,
and `adb logcat` once it is back. Docker restarts crashed emulators on its own; a growing
restart count means it keeps dying.
EOF
  elif ((${#emus[@]})); then
    cat <<'EOF'

You can't restart or inspect the emulator containers yourself. If one hangs, ask the user to run
`./agentbox restart <container>` on the host – or to enable `android.docker_control: true`,
which gives you `emu` for logs and restarts.
EOF
  fi
fi

if [[ "${AGENTBOX_DESKTOP:-}" == 1 ]]; then
  cat <<'EOF'

## Desktop

A virtual desktop runs on `DISPLAY=:0` (Openbox). GUI apps you start appear there, and the user
can watch and click along via RustDesk. There is no GPU; rendering is software only.
EOF
fi
