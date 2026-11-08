# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-09

First public version.

### Added

- `agentbox` wrapper: Docker is the only host requirement; the tool runs inside the image.
- `agentbox init`: interactive setup that clones the given repos, detects toolchains
  (.NET SDK and workloads, JDK, Android SDK packages incl. NDK/CMake from CI workflows,
  Python, Node, Rust, Go) and suggests a resource split.
- `up`, `check`, `status`, `logs`, `stop`, `start`, `down`, `shell`, `login gh|claude`,
  `set <secret>`, `pubkey`, `generate`.
- Box image with herdr server, key-only SSH, per-config toolchains cached in a volume,
  agent resume across restarts, MCP registration for claude/codex/opencode/gemini.
- Android emulators in separate containers with automatic `adb` reconnects and mobile-mcp.
- Optional desktop (Xvfb + Openbox) with RustDesk via LAN and/or own relay server.
- RAM/CPU limits per container and a pre-flight check against the Docker host.
