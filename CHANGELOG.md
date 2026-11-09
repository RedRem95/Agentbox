# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

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

[Unreleased]: https://github.com/RedRem95/Agentbox/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/RedRem95/Agentbox/releases/tag/v0.1.0
