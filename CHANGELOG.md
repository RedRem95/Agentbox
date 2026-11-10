# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.1] - 2026-11-11

### Fixed

- `emu logs` was rejected by the Docker proxy: `docker logs` looks up the container ID and
  asks for the logs by ID, but the proxy only allows the emulators by name. `emu logs` now talks
  to the Docker API by name and turns the log stream into plain text itself (also with `-f`).
- The proxy rules also accept a query string (`?tail=…`, `?t=10`), whichever way the proxy
  matches requests.
- The agentbox skill tells agents to use `emu` instead of plain `docker` commands.

## [0.2.0] - 2026-11-11

Agents can now debug the Android emulators themselves, and Claude knows what kind of box it runs in.

To use the emulator control, add `docker_control: true` under `android:` in your config, get the
new wrapper from this release and run `./agentbox up`. The skill needs no setup.

### Added

- `android.docker_control: true` lets the agents inspect and restart the emulator containers with
  `emu status | logs | files | restart | wait` – status shows restarts, OOM kills, exit codes and
  memory use. Docker access goes through a socket proxy that only allows these containers and
  these actions (no exec). `agentbox init` asks for it.
- Claude gets an `agentbox` skill generated from the config (what the box has, what persists, what
  the agent may and may not do), plus a short pointer to it in `~/.claude/CLAUDE.md`.

## [0.1.1] - 2026-11-09

Two fixes for things that didn't work in 0.1.0. Get the new wrapper from this release and run
`./agentbox up`; the emulators are recreated with the right group, their data stays.

### Fixed

- Android emulators couldn't use KVM: they run as a non-root user and lacked the group that owns
  `/dev/kvm` on the host. The emulator containers now get that group (detected on the host,
  overridable with `container.kvm_gid`).
- `agentbox status` now shows the RustDesk ID and the box's warnings. It only read stdout of the
  container log, but the box writes its messages to stderr. If the ID isn't in the log, it is
  asked from RustDesk directly.

## [0.1.0] - 2026-11-09

The first public version. agentbox turns one YAML file into a dev box for coding agents:
your repos side by side, the toolchains they need, agents running in a herdr server you can
attach to over SSH, and optionally Android emulators and a desktop for GUI testing.

### Added

- **Docker is the only host requirement.** The `agentbox` wrapper starts the tool from the image
  and talks to Docker through the socket. `uvx` works as well if you prefer a native install.
- **`agentbox init`** asks a handful of questions and writes the config. It clones your repos
  shallowly to look at them and detects .NET SDKs and workloads, JDK, Android SDK packages
  (including NDK and CMake versions from CI workflows), Python, Node, Rust and Go. It also orders
  repos that reference each other and proposes a RAM/CPU split for the whole stack.
- **Everyday commands:** `up`, `status`, `logs`, `stop`, `start`, `restart`, `down`, `shell`,
  `check`, `pubkey`, `login gh|claude`, `set rustdesk-password|gh-token|claude-token`.
- **The box:** herdr server with one workspace per repo or one for all, agents that resume their
  last session after restarts, MCP servers registered for Claude Code, Codex, opencode and Gemini,
  key-only SSH, toolchains installed from the config and cached in a volume.
- **Android:** phone and tablet emulators in their own containers, kept connected over `adb`,
  with mobile-mcp so agents can see and tap through the app.
- **Desktop:** optional Xvfb/Openbox desktop with RustDesk, reachable from the LAN and/or through
  your own relay server. The password is asked for once and kept in `.env`.
- **Limits and pre-flight check:** memory and CPU limits per container, and a check that warns or
  stops before the stack would overload the Docker host.
- **Persistence:** sessions, logins, repos, SDKs and emulator data live in named volumes and
  survive `up`, image updates and `down`.

### Known limitations

- Android emulators need a Linux x86_64 host with `/dev/kvm`.
- The image is published for `linux/amd64` only.
- The CLI talks German for now.

[Unreleased]: https://github.com/RedRem95/Agentbox/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/RedRem95/Agentbox/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/RedRem95/Agentbox/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/RedRem95/Agentbox/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/RedRem95/Agentbox/releases/tag/v0.1.0
