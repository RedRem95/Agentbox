#!/usr/bin/env bash
# agentbox entrypoint: Config lesen -> Pakete -> SSH -> Repos -> herdr + Agenten -> sshd
set -euo pipefail

log()  { echo "[agentbox] $*" >&2; }
warn() { echo "[agentbox] WARN: $*" >&2; }

DEV_USER=dev
DEV_HOME=/home/dev
PERSIST=/persist                  # sshd-Hostkeys (Volume)
CONFIG_FILE=${CONFIG_FILE:-/config/workspace.yaml}
CFG_JSON=/etc/agentbox.json

# ---------------------------------------------------------------- Config
# Config als Datei (YAML/JSON/TOML) oder inline via $AGENTBOX_CONFIG
if [[ -n "${AGENTBOX_CONFIG:-}" ]]; then
  printf '%s\n' "$AGENTBOX_CONFIG" > /tmp/agentbox-config
  CONFIG_FILE=/tmp/agentbox-config
fi
[[ -f "$CONFIG_FILE" ]] || { log "Keine Config unter $CONFIG_FILE gefunden"; exit 1; }
yq -o=json '.' "$CONFIG_FILE" > "$CFG_JSON"
cfg() { jq -r "$@" "$CFG_JSON"; }

as_dev() {
  runuser -u "$DEV_USER" -- env HOME="$DEV_HOME" USER="$DEV_USER" LOGNAME="$DEV_USER" "$@"
}

# ---------------------------------------------------------------- UID/GID an Host anpassen
# UID 0 nie übernehmen: dev wäre sonst root (und sshd würde ihn mit PermitRootLogin no ablehnen)
if [[ "${HOST_UID:-}" == 0 ]]; then
  warn "HOST_UID=0 (als root gestartet?) – ignoriert, dev bleibt UID 1000"
  HOST_UID=1000; HOST_GID=1000
fi
if [[ "${HOST_GID:-}" == 0 ]]; then HOST_GID=$HOST_UID; fi
if [[ -n "${HOST_UID:-}" && "$HOST_UID" != "$(id -u $DEV_USER)" ]]; then
  log "Setze UID/GID von $DEV_USER auf ${HOST_UID}:${HOST_GID:-$HOST_UID}"
  groupmod -o -g "${HOST_GID:-$HOST_UID}" "$DEV_USER"
  usermod  -o -u "$HOST_UID" "$DEV_USER"
fi
# Besitzrechte reparieren (-xdev: Host-Bind-Mounts werden nicht angefasst)
for d in "$DEV_HOME" /opt/tools; do
  find "$d" -xdev \( ! -user "$DEV_USER" -o ! -group "$DEV_USER" \) -exec chown -h "$DEV_USER:$DEV_USER" {} + 2>/dev/null || true
done

if [[ "$(cfg '.user.sudo // false')" == "true" ]]; then
  echo "$DEV_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/dev && chmod 440 /etc/sudoers.d/dev
else
  rm -f /etc/sudoers.d/dev
fi

# Docker-Env (API-Keys etc.) auch in SSH-Shells verfügbar machen
{
  while IFS= read -r name; do
    if [[ -n "${!name:-}" ]]; then printf 'export %s=%q\n' "$name" "${!name}"; fi
  done < <(cfg '.env_passthrough // [] | .[]')
} > /etc/agentbox-env.sh
chmod 640 /etc/agentbox-env.sh; chgrp "$DEV_USER" /etc/agentbox-env.sh

# ---------------------------------------------------------------- SSH (persistent)
mkdir -p "$PERSIST/ssh_host"
for t in ed25519 rsa; do
  f="$PERSIST/ssh_host/ssh_host_${t}_key"
  [[ -f "$f" ]] || ssh-keygen -q -t "$t" -N '' -f "$f"
done
chmod 600 "$PERSIST"/ssh_host/*_key

cat > /etc/ssh/sshd_config.d/agentbox.conf <<EOF
HostKey $PERSIST/ssh_host/ssh_host_ed25519_key
HostKey $PERSIST/ssh_host/ssh_host_rsa_key
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers $DEV_USER
ClientAliveInterval 30
ClientAliveCountMax 6
EOF

# User-SSH-Verzeichnis liegt im Home-Volume -> überlebt Recreations
SSH_DIR="$DEV_HOME/.ssh"
as_dev mkdir -p "$SSH_DIR"; chmod 700 "$SSH_DIR"
if [[ ! -f "$SSH_DIR/id_ed25519" ]]; then
  as_dev ssh-keygen -q -t ed25519 -N '' -C "agentbox@$(hostname)" -f "$SSH_DIR/id_ed25519"
fi
if [[ ! -f "$SSH_DIR/config" ]]; then
  as_dev bash -c "printf 'Host *\n  StrictHostKeyChecking accept-new\n  ServerAliveInterval 30\n' > '$SSH_DIR/config'"
fi
AK="$SSH_DIR/authorized_keys"
as_dev touch "$AK"
{
  cfg '.ssh.authorized_keys // [] | .[]'
  if [[ -n "${AUTHORIZED_KEYS:-}" ]]; then printf '%s\n' "$AUTHORIZED_KEYS"; fi
  if [[ -f /config/authorized_keys ]]; then cat /config/authorized_keys; fi
} | while IFS= read -r key; do
  [[ -z "$key" ]] && continue
  grep -qxF "$key" "$AK" || echo "$key" >> "$AK"
done
chmod 600 "$AK"; chown "$DEV_USER:$DEV_USER" "$AK"
[[ -s "$AK" ]] || warn "authorized_keys ist leer – Login per SSH nicht möglich"

log "Public Key für GitHub (Deploy-/User-Key):"
cat "$SSH_DIR/id_ed25519.pub" >&2

# ---------------------------------------------------------------- Pakete
APT_UPDATED=0
apt_install() {
  local missing=()
  for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
  ((${#missing[@]})) || return 0
  if ((APT_UPDATED == 0)); then apt-get update -qq; APT_UPDATED=1; fi
  log "apt install ${missing[*]}"
  apt-get install -y -qq --no-install-recommends "${missing[@]}"
}

install_tool() {
  case "$1" in
    python)
      # packages.python.version gesetzt -> genaue Version per uv als venv (/opt/tools/venv, vorne im PATH),
      # sonst Debian-Python
      local pyv; pyv=$(cfg '.packages.python.version // ""')
      if [[ -n "$pyv" ]]; then
        install_tool uv
        if [[ "$(/opt/tools/venv/bin/python -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)" != "$pyv" ]]; then
          log "Python $pyv (uv venv)"
          as_dev env UV_PYTHON_INSTALL_DIR=/opt/tools/python uv venv --clear --seed --python "$pyv" /opt/tools/venv
        fi
        local pyp=(); mapfile -t pyp < <(cfg '.packages.python.packages // [] | .[]')
        if ((${#pyp[@]})); then
          as_dev uv pip install --quiet --python /opt/tools/venv/bin/python "${pyp[@]}" || warn "pip-Pakete fehlgeschlagen"
        fi
      else
        apt_install python3 python3-pip python3-venv python-is-python3
      fi ;;
    uv)
      command -v uv >/dev/null || as_dev bash -c 'curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/opt/tools/bin UV_NO_MODIFY_PATH=1 sh' ;;
    dotnet)
      apt_install libicu72 libssl3 zlib1g
      local d=/opt/tools/dotnet/dotnet ch v
      # Exakte Versionen (z.B. aus global.json/eng/sdk.json) haben Vorrang vor Channels
      for v in $(cfg '.packages.dotnet_version // ""'); do
        if ! "$d" --list-sdks 2>/dev/null | grep -q "^$v "; then
          log ".NET SDK $v"
          as_dev bash -c "curl -fsSL https://dot.net/v1/dotnet-install.sh | bash -s -- --version '$v' --install-dir /opt/tools/dotnet --no-path"
        fi
      done
      for ch in $(cfg 'if .packages.dotnet_version then (.packages.dotnet_channel // "") else (.packages.dotnet_channel // "LTS") end'); do
        if [[ "$ch" =~ ^[0-9] ]] && "$d" --list-sdks 2>/dev/null | grep -q "^${ch}\."; then continue; fi
        if [[ ! "$ch" =~ ^[0-9] ]] && [[ -n "$("$d" --list-sdks 2>/dev/null)" ]]; then continue; fi
        log ".NET SDK Channel $ch"
        as_dev bash -c "curl -fsSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel '$ch' --install-dir /opt/tools/dotnet --no-path"
      done
      # Workloads (z.B. android); im Home ausführen, damit keine global.json greift
      local wl
      for wl in $(cfg '.packages.dotnet_workloads // [] | .[]'); do
        if ! (cd "$DEV_HOME" && as_dev "$d" workload list 2>/dev/null | grep -q "^$wl "); then
          log ".NET Workload $wl"
          (cd "$DEV_HOME" && as_dev "$d" workload install "$wl") || warn "Workload $wl fehlgeschlagen"
        fi
      done ;;
    go)
      if [[ ! -x /opt/tools/go/bin/go ]]; then
        local v arch; v=$(curl -fsSL 'https://go.dev/VERSION?m=text' | head -1); arch=$(dpkg --print-architecture)
        as_dev bash -c "curl -fsSL https://go.dev/dl/${v}.linux-${arch}.tar.gz | tar -xz -C /opt/tools"
      fi ;;
    rust)
      command -v cargo >/dev/null || as_dev bash -c 'curl -fsSL https://sh.rustup.rs | sh -s -- -y --no-modify-path' ;;
    java)
      install_jdk ;;
    bun)
      command -v bun >/dev/null || as_dev bash -c 'curl -fsSL https://bun.sh/install | bash' ;;
    android)
      install_android ;;
    *)
      warn "Unbekanntes Tool '$1' – nutze packages.apt oder packages.setup" ;;
  esac
}

# Android SDK (ohne Emulator – der läuft in eigenem Container)
# Temurin-JDK nach /opt/tools/jdk (JAVA_HOME); Version aus android.jdk bzw. packages.jdk, Default 21
install_jdk() {
  local v arch cur
  v=$(cfg '.android.jdk // .packages.jdk // 21')
  cur=$(/opt/tools/jdk/bin/java -version 2>&1 | awk -F'"' '/version/ {split($2, a, "."); print a[1]; exit}')
  [[ "$cur" == "$v" ]] && return 0
  case "$(dpkg --print-architecture)" in amd64) arch=x64 ;; arm64) arch=aarch64 ;; *) warn "JDK: Architektur nicht unterstützt"; return 1 ;; esac
  log "Temurin JDK $v"
  as_dev bash -c "set -e; rm -rf /opt/tools/jdk; mkdir -p /opt/tools/jdk
    curl -fsSL 'https://api.adoptium.net/v3/binary/latest/$v/ga/linux/$arch/jdk/hotspot/normal/eclipse' \
      | tar -xz -C /opt/tools/jdk --strip-components=1"
}

install_android() {
  local sdk=/opt/tools/android-sdk ver tmp
  apt_install unzip
  install_jdk
  ver=$(cfg '.android.cmdline_tools_version // "13114758"')
  if [[ ! -x "$sdk/cmdline-tools/latest/bin/sdkmanager" ]]; then
    log "Installiere Android cmdline-tools ($ver)"
    tmp=$(mktemp -d); chown "$DEV_USER:$DEV_USER" "$tmp"
    as_dev bash -c "set -e; mkdir -p '$sdk/cmdline-tools'; cd '$tmp'
      curl -fsSLo clt.zip 'https://dl.google.com/android/repository/commandlinetools-linux-${ver}_latest.zip'
      unzip -q clt.zip; rm -rf '$sdk/cmdline-tools/latest'; mv cmdline-tools '$sdk/cmdline-tools/latest'"
    rm -rf "$tmp"
  fi
  local pk=()
  mapfile -t pk < <(cfg '.android.sdk_packages // ["platform-tools","platforms;android-35","build-tools;35.0.0"] | .[]')
  as_dev bash -c 'yes | sdkmanager --licenses >/dev/null 2>&1' || true
  # Einzeln installieren: vorhandene überspringen, Fehler sichtbar machen
  local p out
  for p in "${pk[@]}"; do
    [[ -d "$sdk/${p//;//}" ]] && continue
    log "sdkmanager: $p"
    if ! out=$(as_dev sdkmanager --install "$p" 2>&1); then
      warn "sdkmanager '$p' fehlgeschlagen:"; tail -5 <<<"$out" >&2
    elif [[ ! -d "$sdk/${p//;//}" ]]; then
      warn "sdkmanager '$p' meldet Erfolg, aber $sdk/${p//;//} fehlt:"; tail -5 <<<"$out" >&2
    fi
  done
}

# Android ist aktiv, sobald android.* oder container.android_emulators konfiguriert ist
ANDROID_ENABLED=$(cfg 'if (.android // .container.android_emulators) then "true" else "false" end')
mapfile -t ADB_DEVICES < <(cfg '[(.android.devices // [])[],
  ((.container.android_emulators // [])[] | (if type=="string" then . else .name end) | select(. != null) | . + ":5555")]
  | unique | .[]')

mapfile -t APT_PKGS < <(cfg '.packages.apt // [] | .[]')
if ((${#APT_PKGS[@]})); then apt_install "${APT_PKGS[@]}"; fi

while IFS= read -r t; do
  [[ -z "$t" ]] && continue
  install_tool "$t" || warn "Installation von $t fehlgeschlagen"
done < <(jq -r --arg a "$ANDROID_ENABLED" \
  '(.packages.tools // []) + (if $a == "true" then ["android"] else [] end) | unique | .[]' "$CFG_JSON")

mapfile -t NPM_PKGS < <(cfg '.packages.npm // [] | .[]')
if ((${#NPM_PKGS[@]})); then as_dev npm install -g --no-fund --no-audit "${NPM_PKGS[@]}" || warn "npm install fehlgeschlagen"; fi

SETUP=$(cfg '.packages.setup // ""')
if [[ -n "$SETUP" ]]; then
  log "Führe packages.setup aus"
  bash -c "$SETUP" || warn "packages.setup fehlgeschlagen"
fi

# ---------------------------------------------------------------- Agenten
declare -A AGENT_NPM=(
  [claude]=@anthropic-ai/claude-code
  [codex]=@openai/codex
  [opencode]=opencode-ai
  [cline]=cline
  [gemini]=@google/gemini-cli
  [qwen]=@qwen-code/qwen-code
)
declare -A AGENT_BIN=( [claude]=claude [codex]=codex [opencode]=opencode [cline]=cline [gemini]=gemini [qwen]=qwen )
# Resume-Befehl: setzt letzte Session im aktuellen Verzeichnis fort, sonst Neustart
declare -A AGENT_RESUME=(
  [claude]='claude --continue || claude'
  [codex]='codex resume --last || codex'
  [opencode]='opencode --continue || opencode'
  [gemini]='gemini --resume latest || gemini'
)

norm_list='if type=="string" then [.] elif type=="array" then . else [] end'
mapfile -t ALL_AGENTS < <(jq -r "[(.agents // [] | $norm_list), (.repos // [] | .[] | objects | (.agents // [] | $norm_list))] | add // [] | unique | .[]" "$CFG_JSON")

for a in "${ALL_AGENTS[@]}"; do
  [[ "$a" == shell || "$a" == none ]] && continue
  bin=${AGENT_BIN[$a]:-$a}
  if ! command -v "$bin" >/dev/null && [[ -n "${AGENT_NPM[$a]:-}" ]]; then
    log "Installiere Agent $a (${AGENT_NPM[$a]})"
    as_dev npm install -g --no-fund --no-audit "${AGENT_NPM[$a]}" || warn "Installation von $a fehlgeschlagen"
  fi
done

# Kommando für Agent bestimmen: agent_commands > Resume-Default > Binary
agent_cmd() {
  local a=$1 c
  c=$(jq -r --arg a "$a" '.agent_commands[$a] // empty' "$CFG_JSON")
  if [[ -n "$c" ]]; then echo "$c"; return; fi
  if [[ "$(cfg '.resume != false')" == "true" && -n "${AGENT_RESUME[$a]:-}" ]]; then
    echo "${AGENT_RESUME[$a]}"
  else
    echo "${AGENT_BIN[$a]:-$a}"
  fi
}

# ---------------------------------------------------------------- MCP-Server
# mcp_servers: { name: {command, args, env} } – wird bei allen konfigurierten Agenten eingetragen.
# android.mcp (Default true) ergänzt "mobile" (mobile-mcp, nutzt das lokale adb).
MCP_JSON=$(jq -c --arg a "$ANDROID_ENABLED" '
  (.mcp_servers // {})
  + (if $a == "true" and (.android.mcp != false) then
       {mobile: {command: "npx", args: ["-y", "@mobilenext/mobile-mcp@latest"]}} else {} end)' "$CFG_JSON")

has_agent() { local x; for x in "${ALL_AGENTS[@]}"; do [[ "$x" == "$1" ]] && return 0; done; return 1; }

register_mcp() {
  local n=$1 spec=$2 cmd args=() envs=() f
  cmd=$(jq -r '.command' <<<"$spec")
  mapfile -t args < <(jq -r '.args // [] | .[]' <<<"$spec")
  mapfile -t envs < <(jq -r '.env // {} | to_entries[] | "\(.key)=\(.value)"' <<<"$spec")

  if has_agent claude && command -v claude >/dev/null; then
    if ! as_dev claude mcp get "$n" >/dev/null 2>&1; then
      local eargs=(); for e in "${envs[@]}"; do eargs+=(-e "$e"); done
      as_dev claude mcp add -s user "${eargs[@]}" "$n" -- "$cmd" "${args[@]}" >/dev/null \
        && log "MCP $n -> claude" || warn "MCP $n für claude fehlgeschlagen"
    fi
  fi
  if has_agent codex; then
    f="$DEV_HOME/.codex/config.toml"; as_dev mkdir -p "$DEV_HOME/.codex"; as_dev touch "$f"
    if ! grep -q "^\[mcp_servers\.$n\]" "$f"; then
      jq -r --arg n "$n" '"\n[mcp_servers.\($n)]\ncommand = \(.command|@json)\nargs = \((.args // [])|@json)"
        + (if .env then "\nenv = { " + (.env|to_entries|map("\(.key) = \(.value|tostring|@json)")|join(", ")) + " }" else "" end)' \
        <<<"$spec" >> "$f"
      log "MCP $n -> codex"
    fi
  fi
  if has_agent opencode; then
    f="$DEV_HOME/.config/opencode/opencode.json"; as_dev mkdir -p "$(dirname "$f")"
    [[ -s "$f" ]] || echo '{"$schema":"https://opencode.ai/config.json"}' > "$f"
    jq --arg n "$n" --argjson s "$spec" \
      '.mcp[$n] //= ({type: "local", command: ([$s.command] + ($s.args // [])), enabled: true}
                     + (if $s.env then {environment: $s.env} else {} end))' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
    chown "$DEV_USER:$DEV_USER" "$f"
  fi
  if has_agent gemini; then
    f="$DEV_HOME/.gemini/settings.json"; as_dev mkdir -p "$(dirname "$f")"
    [[ -s "$f" ]] || echo '{}' > "$f"
    jq --arg n "$n" --argjson s "$spec" '.mcpServers[$n] //= $s' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
    chown "$DEV_USER:$DEV_USER" "$f"
  fi
}

while IFS= read -r entry; do
  [[ -z "$entry" ]] && continue
  register_mcp "$(jq -r '.key' <<<"$entry")" "$(jq -c '.value' <<<"$entry")" || warn "MCP-Registrierung fehlgeschlagen"
done < <(jq -c 'to_entries[]' <<<"$MCP_JSON")

# ---------------------------------------------------------------- Git
GIT_NAME=$(cfg '.git.name // empty');  if [[ -n "$GIT_NAME" ]];  then as_dev git config --global user.name "$GIT_NAME"; fi
GIT_EMAIL=$(cfg '.git.email // empty'); if [[ -n "$GIT_EMAIL" ]]; then as_dev git config --global user.email "$GIT_EMAIL"; fi

if as_dev gh auth status >/dev/null 2>&1; then
  as_dev gh auth setup-git || true
  if [[ "$(cfg '.git.register_ssh_key_with_gh // false')" == "true" ]]; then
    as_dev gh ssh-key add "$SSH_DIR/id_ed25519.pub" --title "agentbox-$(hostname)" 2>/dev/null \
      || log "SSH-Key evtl. schon registriert (oder Scope admin:public_key fehlt)"
  fi
else
  warn "gh ist nicht eingeloggt. Einmalig auf dem Host: ./agentbox login gh  (oder in der Box: gh auth login)"
  warn "Alternativ GH_TOKEN in der env_file setzen."
fi

# ---------------------------------------------------------------- Repos
WORKDIR=$(cfg '.workdir // "/src"')
mkdir -p "$WORKDIR"; chown "$DEV_USER:$DEV_USER" "$WORKDIR"

repo_name() { local n; n=$(basename "${1%.git}"); echo "${n##*:}"; }

mapfile -t REPOS < <(jq -c '.repos // [] | .[] | if type=="string" then {url: .} else . end' "$CFG_JSON")
for r in "${REPOS[@]}"; do
  url=$(jq -r '.url' <<<"$r")
  name=$(jq -r '.name // empty' <<<"$r"); [[ -z "$name" ]] && name=$(repo_name "$url")
  branch=$(jq -r '.branch // empty' <<<"$r")
  dir="$WORKDIR/$name"
  if [[ -d "$dir/.git" ]]; then
    if [[ -z "$(as_dev git -C "$dir" status --porcelain)" ]]; then
      log "Pull $name"
      as_dev git -C "$dir" pull --ff-only || warn "Pull von $name fehlgeschlagen"
    else
      log "$name hat lokale Änderungen – kein Pull"
    fi
  else
    log "Clone $url -> $dir"
    as_dev git clone ${branch:+--branch "$branch"} "$url" "$dir" || { warn "Clone von $url fehlgeschlagen"; continue; }
  fi
  # Repo-Setup läuft, bis es einmal erfolgreich war (Marker in .git, wird von git ignoriert)
  repo_setup=$(jq -r '.setup // empty' <<<"$r")
  if [[ -n "$repo_setup" && ! -f "$dir/.git/agentbox-setup-done" ]]; then
    log "Setup $name"
    if (cd "$dir" && as_dev bash -ec "$repo_setup"); then
      as_dev touch "$dir/.git/agentbox-setup-done"
    else
      warn "Setup von $name fehlgeschlagen – läuft beim nächsten Start erneut"
    fi
  fi
done

# ---------------------------------------------------------------- Dateien im Workspace
# files: { "/src/CLAUDE.md": "inhalt" } – wird als dev angelegt, aber nie überschrieben
while IFS= read -r entry; do
  [[ -z "$entry" ]] && continue
  fpath=$(jq -r '.key' <<<"$entry")
  [[ "$fpath" = /* ]] || fpath="$WORKDIR/$fpath"
  if [[ ! -e "$fpath" ]]; then
    as_dev mkdir -p "$(dirname "$fpath")"
    jq -r '.value' <<<"$entry" | as_dev tee "$fpath" >/dev/null
    log "Datei angelegt: $fpath"
  fi
done < <(jq -c '.files // {} | to_entries[]' "$CFG_JSON")

# ---------------------------------------------------------------- adb -> externe Emulatoren
HERDR_DIR="$DEV_HOME/.config/herdr"
as_dev mkdir -p "$HERDR_DIR"

if ((${#ADB_DEVICES[@]})); then
  if command -v adb >/dev/null; then
    log "adb-watch: ${ADB_DEVICES[*]}"
    as_dev touch "$DEV_HOME/.android-adb-watch.log"
    # gleicher User wie herdr -> Agenten in den Panes teilen sich diesen adb-Server
    runuser -u "$DEV_USER" -- env HOME="$DEV_HOME" USER="$DEV_USER" \
      adb-watch "${ADB_DEVICES[@]}" >> "$DEV_HOME/.android-adb-watch.log" 2>&1 &
  else
    warn "adb nicht gefunden – Android-Devices werden nicht verbunden"
  fi
fi

# ---------------------------------------------------------------- Emulator-Kontrolle (android.docker_control)
# DOCKER_HOST zeigt auf den Socket-Proxy, der nur die Emulator-Container durchlässt (siehe `emu --help`).
mkdir -p /etc/agentbox
jq -r '.container.android_emulators // [] | .[] | (if type == "string" then . else .name end) // empty' \
  "$CFG_JSON" > /etc/agentbox/emulators

if [[ -n "${DOCKER_HOST:-}" ]]; then
  echo "export DOCKER_HOST=$DOCKER_HOST" >> /etc/agentbox-env.sh
  log "Emulator-Kontrolle aktiv (emu status | logs | files | restart)"
fi

# ---------------------------------------------------------------- Desktop + RustDesk (optional)
# desktop.enabled: true -> Xvfb/Openbox auf DISPLAY=:0; Agenten/Apps aus herdr erscheinen dort.
toml_str() { local x=${1//\\/\\\\}; x=${x//\"/\\\"}; printf '"%s"' "$x"; }

install_rustdesk() {
  command -v rustdesk >/dev/null && return 0
  local ver arch deb
  ver=$(cfg '.desktop.rustdesk.version // "latest"')
  if [[ "$ver" == latest ]]; then
    ver=$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/rustdesk/rustdesk/releases/latest)
    ver=${ver##*/}
  fi
  case "$(dpkg --print-architecture)" in amd64) arch=x86_64 ;; arm64) arch=aarch64 ;; esac
  deb="/opt/tools/cache/rustdesk-$ver-$arch.deb"
  as_dev mkdir -p /opt/tools/cache
  if [[ ! -s "$deb" ]]; then
    log "Lade RustDesk $ver"
    as_dev curl -fsSL --retry 4 --retry-all-errors -o "$deb.part" \
      "https://github.com/rustdesk/rustdesk/releases/download/$ver/rustdesk-$ver-$arch.deb" \
      && as_dev mv "$deb.part" "$deb" || { warn "RustDesk-Download fehlgeschlagen"; return 1; }
  fi
  # Kein systemd im Container: Stub, damit die Paket-Skripte nicht abbrechen
  if ! command -v systemctl >/dev/null; then printf '#!/bin/sh\nexit 0\n' > /usr/local/bin/systemctl; chmod +x /usr/local/bin/systemctl; fi
  if ((APT_UPDATED == 0)); then apt-get update -qq; APT_UPDATED=1; fi
  apt-get install -y -qq --no-install-recommends "$deb" || { warn "RustDesk-Installation fehlgeschlagen"; return 1; }
}

configure_rustdesk() {
  local dir="$DEV_HOME/.config/rustdesk" pw o
  as_dev mkdir -p "$dir"
  pw=${RUSTDESK_PASSWORD:-}
  if [[ ${#pw} -lt 8 ]]; then
    warn "RUSTDESK_PASSWORD fehlt oder ist kürzer als 8 Zeichen – RustDesk wird nicht gestartet"
    return 1
  fi
  # Optionen werden bei jedem Start neu geschrieben (Config ist die Quelle)
  {
    echo "rendezvous_server = ''"
    echo "nat_type = 1"
    echo "serial = 0"
    echo
    echo "[options]"
    echo "verification-method = 'use-permanent-password'"
    echo "approve-mode = 'password'"
    echo "enable-lan-discovery = 'N'"
    if [[ "$(cfg '.desktop.rustdesk.direct.enabled // false')" == true ]]; then
      echo "direct-server = 'Y'"
      echo "direct-access-port = '21118'"
    fi
    o=$(cfg '.desktop.rustdesk.relay.id_server // ""');    [[ -n "$o" ]] && echo "custom-rendezvous-server = $(toml_str "$o")"
    o=$(cfg '.desktop.rustdesk.relay.relay_server // ""'); [[ -n "$o" ]] && echo "relay-server = $(toml_str "$o")"
    o=$(cfg '.desktop.rustdesk.relay.api_server // ""');   [[ -n "$o" ]] && echo "api-server = $(toml_str "$o")"
    o=$(cfg '.desktop.rustdesk.relay.key // ""');          [[ -n "$o" ]] && echo "key = $(toml_str "$o")"
    true
  } > "$dir/RustDesk2.toml"
  # RustDesk.toml enthält ID + Schlüssel (bleibt im Home-Volume -> feste ID); nur das Passwort setzen.
  # Klartext wird von RustDesk beim ersten Laden verschlüsselt.
  touch "$dir/RustDesk.toml"
  grep -v '^password = ' "$dir/RustDesk.toml" > "$dir/RustDesk.toml.tmp" || true
  { echo "password = $(toml_str "$pw")"; cat "$dir/RustDesk.toml.tmp"; } > "$dir/RustDesk.toml"
  rm -f "$dir/RustDesk.toml.tmp"
  chown -R "$DEV_USER:$DEV_USER" "$dir"; chmod 600 "$dir"/*.toml
}

if [[ "$(cfg '.desktop.enabled // false')" == true ]]; then
  RD_ON=0
  [[ "$(cfg '.desktop.rustdesk.enabled != false')" == true ]] && RD_ON=1
  apt_install xvfb x11-utils x11-xserver-utils openbox tint2 xterm dbus-x11 \
              fonts-dejavu-core libfontconfig1 libice6 libsm6 xdg-utils
  if ((RD_ON)); then
    { install_rustdesk && configure_rustdesk; } || RD_ON=0
  fi
  mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
  as_dev touch "$DEV_HOME/.agentbox-desktop.log"
  log "Starte Desktop (DISPLAY=:0, RustDesk=$RD_ON)"
  runuser -u "$DEV_USER" -- env HOME="$DEV_HOME" USER="$DEV_USER" \
    agentbox-desktop "$(cfg '.desktop.resolution // "1600x900"')" "$RD_ON" >> "$DEV_HOME/.agentbox-desktop.log" 2>&1 &
  # Alles, was später aus herdr gestartet wird, landet auf diesem Desktop
  export DISPLAY=:0
  AGENTBOX_DESKTOP=1
  echo 'export DISPLAY=:0' >> /etc/agentbox-env.sh
  if ((RD_ON)); then
    for _ in $(seq 1 20); do
      RD_ID=$(as_dev env DISPLAY=:0 rustdesk --get-id 2>/dev/null | tr -dc '0-9')
      [[ -n "$RD_ID" ]] && break; sleep 2
    done
    log "RustDesk-ID: ${RD_ID:-unbekannt (siehe ~/.agentbox-desktop.log)}"
  fi
fi
# Passwort nicht an herdr/Agenten vererben
unset RUSTDESK_PASSWORD

# ---------------------------------------------------------------- Agent-Kontext: agentbox-Skill
# Claude bekommt einen Skill, der aus dieser Config erzeugt wird (was die Box hat, was erhalten
# bleibt, was der Agent darf), plus einen kurzen, immer geladenen Hinweis darauf in ~/.claude/CLAUDE.md.
AB_BEGIN='<!-- agentbox -->'
AB_END='<!-- /agentbox -->'
managed_block() {  # <datei> [inhalt] – verwalteten Block ersetzen; ohne Inhalt nur entfernen
  local f=$1 body=${2:-} rest
  if [[ -f "$f" ]]; then
    rest=$(sed "\|^$AB_BEGIN\$|,\|^$AB_END\$|d" "$f")   # $(…) schneidet Leerzeilen am Ende ab
  else
    rest=""
  fi
  [[ -n "$rest" || -n "$body" || -f "$f" ]] || return 0
  as_dev mkdir -p "$(dirname "$f")"
  {
    [[ -n "$rest" ]] && printf '%s\n' "$rest"
    [[ -n "$rest" && -n "$body" ]] && echo
    [[ -n "$body" ]] && printf '%s\n%s\n%s\n' "$AB_BEGIN" "$body" "$AB_END"
    true
  } | as_dev tee "$f" >/dev/null
}

if has_agent claude; then
  skill_dir="$DEV_HOME/.claude/skills/agentbox"
  as_dev mkdir -p "$skill_dir"
  AGENTBOX_DESKTOP=${AGENTBOX_DESKTOP:-0} agentbox-skill "$CFG_JSON" "$(hostname)" | as_dev tee "$skill_dir/SKILL.md" >/dev/null
  managed_block "$DEV_HOME/.claude/CLAUDE.md" "You are running inside an agentbox container. Before installing system packages or tools, \
touching Docker or the Android emulators, starting GUI apps, or relying on anything outside the repos and \
your home surviving a restart, use the \`agentbox\` skill: it describes what this box has, what persists \
and what you can and can't control."
  log "agentbox-Skill für Claude geschrieben"
else
  managed_block "$DEV_HOME/.claude/CLAUDE.md"
fi

# ---------------------------------------------------------------- herdr

# Offizielle Integrationen -> bessere Statuserkennung + native Session-Restore
for a in "${ALL_AGENTS[@]}"; do
  [[ "$a" == shell || "$a" == none ]] && continue
  as_dev herdr integration install "$a" >/dev/null 2>&1 || true
done

log "Starte herdr server"
runuser -u "$DEV_USER" -- env HOME="$DEV_HOME" USER="$DEV_USER" \
  bash -c 'cd ~ && exec herdr server >> ~/.config/herdr/agentbox-server.out 2>&1' &

for _ in $(seq 1 60); do
  as_dev herdr workspace list >/dev/null 2>&1 && break
  sleep 0.5
done
as_dev herdr workspace list >/dev/null 2>&1 || { warn "herdr server antwortet nicht"; }

workspace_exists() {
  as_dev herdr workspace list 2>/dev/null \
    | jq -e --arg l "$1" '[.. | objects | select(.label? == $l)] | length > 0' >/dev/null 2>&1
}

start_workspace() {
  local label=$1 cwd=$2; shift 2
  local agents=("$@") out ws pane p first=1
  if workspace_exists "$label"; then
    log "Workspace '$label' existiert bereits (von herdr wiederhergestellt) – überspringe"
    return
  fi
  out=$(as_dev herdr workspace create --cwd "$cwd" --label "$label" --no-focus) || { warn "Workspace $label fehlgeschlagen"; return; }
  ws=$(jq -r '.result.workspace.workspace_id' <<<"$out")
  pane=$(jq -r '.result.root_pane.pane_id' <<<"$out")
  for a in "${agents[@]}"; do
    if ((first)); then
      p=$pane; first=0
    else
      out=$(as_dev herdr tab create --workspace "$ws" --cwd "$cwd" --label "$a" --no-focus) || continue
      p=$(jq -r '.result.root_pane.pane_id' <<<"$out")
    fi
    [[ "$a" == shell || "$a" == none ]] && continue
    log "Starte $a in $label"
    as_dev herdr pane run "$p" "$(agent_cmd "$a")" || warn "Start von $a in $label fehlgeschlagen"
  done
}

mapfile -t DEFAULT_AGENTS < <(jq -r ".agents // [] | $norm_list | .[]" "$CFG_JSON")
LAYOUT=$(cfg '.layout // "per-project"')

if [[ "$LAYOUT" == "root" ]]; then
  start_workspace "$(basename "$WORKDIR")" "$WORKDIR" "${DEFAULT_AGENTS[@]}"
else
  for r in "${REPOS[@]}"; do
    url=$(jq -r '.url' <<<"$r")
    name=$(jq -r '.name // empty' <<<"$r"); [[ -z "$name" ]] && name=$(repo_name "$url")
    [[ -d "$WORKDIR/$name" ]] || continue
    if jq -e 'has("agents")' <<<"$r" >/dev/null; then
      mapfile -t ag < <(jq -r ".agents | $norm_list | .[]" <<<"$r")
    else
      ag=("${DEFAULT_AGENTS[@]}")
    fi
    start_workspace "$name" "$WORKDIR/$name" "${ag[@]}"
  done
fi

# ---------------------------------------------------------------- sshd (Vordergrund)
log "Bereit. Verbinden mit: herdr --remote ssh://dev@<host>:<port>"
exec /usr/sbin/sshd -D -e
