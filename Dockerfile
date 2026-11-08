# agentbox image: herdr server, coding agents, repos and toolchains from one config.
# Also contains the agentbox CLI (started by the ./agentbox wrapper on the host).
FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TERM=xterm-256color

# Basis-Tools, sshd, git, jq, tini
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-server git curl ca-certificates gnupg jq tini sudo less nano \
        procps unzip xz-utils ripgrep python3 python3-venv \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Docker-CLI + Compose-Plugin (nur für das agentbox-Tool, das den Host-Docker über den Socket steuert)
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# yq (liest YAML/JSON/TOML-Config)
ARG YQ_VERSION=v4.44.3
RUN curl -fsSL "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_$(dpkg --print-architecture)" \
        -o /usr/local/bin/yq && chmod +x /usr/local/bin/yq && yq --version

# herdr – Reihenfolge:
#   1. vendor/herdr im Build-Kontext (eigenes Binary, z.B. cp "$(command -v herdr)" vendor/)
#   2. GitHub Releases (mit Retries)
#   3. Installer von releases.herdr.dev
# Version pinnen: docker build --build-arg HERDR_VERSION=v0.9.5 ...
COPY image/vendor/ /tmp/vendor/
ARG HERDR_VERSION=latest
RUN set -e; \
    case "$(dpkg --print-architecture)" in amd64) a=x86_64 ;; arm64) a=aarch64 ;; esac; \
    if [ -f /tmp/vendor/herdr ]; then \
      echo "herdr aus vendor/"; install -m 755 /tmp/vendor/herdr /usr/local/bin/herdr; \
    else \
      if [ "$HERDR_VERSION" = latest ]; then \
        url="https://github.com/herdrdev/herdr/releases/latest/download/herdr-linux-$a"; \
      else \
        url="https://github.com/herdrdev/herdr/releases/download/$HERDR_VERSION/herdr-linux-$a"; \
      fi; \
      if curl -fsSL --retry 4 --retry-all-errors --retry-delay 5 -o /usr/local/bin/herdr "$url"; then \
        chmod +x /usr/local/bin/herdr; \
      else \
        echo "GitHub-Download fehlgeschlagen, versuche releases.herdr.dev"; rm -f /usr/local/bin/herdr; \
        curl -fsSL --retry 4 --retry-all-errors https://releases.herdr.dev/core/install.sh | sh; \
        for p in /root/.local/bin/herdr /root/.herdr/bin/herdr /root/bin/herdr; do \
          if [ -x "$p" ]; then install -m 755 "$p" /usr/local/bin/herdr; break; fi; \
        done; \
      fi; \
    fi; \
    rm -rf /tmp/vendor; \
    herdr --version

# User "dev" (UID wird beim Start per HOST_UID an den Host angepasst)
RUN userdel -r node \
    && useradd -m -u 1000 -s /bin/bash dev \
    && mkdir -p /src /opt/tools/bin /opt/tools/npm /persist /run/sshd /config \
    && chown -R dev:dev /src /opt/tools \
    && rm -f /etc/ssh/ssh_host_*

# Alles, was zur Laufzeit installiert wird, landet in /opt/tools (als Volume cachebar)
ENV NPM_CONFIG_PREFIX=/opt/tools/npm \
    DOTNET_ROOT=/opt/tools/dotnet \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    RUSTUP_HOME=/opt/tools/rustup \
    CARGO_HOME=/opt/tools/cargo \
    BUN_INSTALL=/opt/tools/bun \
    ANDROID_HOME=/opt/tools/android-sdk \
    ANDROID_SDK_ROOT=/opt/tools/android-sdk \
    JAVA_HOME=/opt/tools/jdk \
    PATH=/opt/tools/venv/bin:/opt/tools/jdk/bin:/opt/tools/npm/bin:/opt/tools/bin:/opt/tools/dotnet:/opt/tools/cargo/bin:/opt/tools/go/bin:/opt/tools/bun/bin:/opt/tools/android-sdk/cmdline-tools/latest/bin:/opt/tools/android-sdk/platform-tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Gleiche Umgebung für SSH-Logins (sshd setzt PATH sonst zurück)
RUN printf '%s\n' \
      'export NPM_CONFIG_PREFIX=/opt/tools/npm DOTNET_ROOT=/opt/tools/dotnet DOTNET_CLI_TELEMETRY_OPTOUT=1' \
      'export RUSTUP_HOME=/opt/tools/rustup CARGO_HOME=/opt/tools/cargo BUN_INSTALL=/opt/tools/bun' \
      'export ANDROID_HOME=/opt/tools/android-sdk ANDROID_SDK_ROOT=/opt/tools/android-sdk JAVA_HOME=/opt/tools/jdk' \
      'export PATH=/opt/tools/venv/bin:/opt/tools/jdk/bin:/opt/tools/npm/bin:/opt/tools/bin:/opt/tools/dotnet:/opt/tools/cargo/bin:/opt/tools/go/bin:/opt/tools/bun/bin:/opt/tools/android-sdk/cmdline-tools/latest/bin:/opt/tools/android-sdk/platform-tools:$PATH' \
      '[ -f /etc/agentbox-env.sh ] && . /etc/agentbox-env.sh' \
      > /etc/profile.d/agentbox.sh \
    && printf '\n[ -f /etc/profile.d/agentbox.sh ] && . /etc/profile.d/agentbox.sh\n' >> /etc/bash.bashrc

COPY image/entrypoint.sh /usr/local/bin/agentbox-entrypoint
COPY image/adb-watch.sh /usr/local/bin/adb-watch
COPY image/desktop.sh /usr/local/bin/agentbox-desktop
RUN chmod +x /usr/local/bin/agentbox-entrypoint /usr/local/bin/adb-watch /usr/local/bin/agentbox-desktop

# agentbox-Tool (Python) – wird über den Wrapper ./agentbox auf dem Host gestartet
COPY cli/pyproject.toml /opt/agentbox-cli/src/pyproject.toml
COPY cli/agentbox_cli/ /opt/agentbox-cli/src/agentbox_cli/
RUN python3 -m venv /opt/agentbox-cli/venv \
    && /opt/agentbox-cli/venv/bin/pip install --no-cache-dir -q /opt/agentbox-cli/src \
    && ln -s /opt/agentbox-cli/venv/bin/agentbox /usr/local/bin/agentbox-cli \
    && agentbox-cli --version

EXPOSE 22
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/agentbox-entrypoint"]
