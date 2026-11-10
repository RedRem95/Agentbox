# agentbox

A disposable dev machine for coding agents, described in one YAML file.

You list the repos you want to work on. agentbox clones them side by side, installs the
toolchains they need, starts your agents (Claude Code, Codex, opencode, …) inside a
[herdr](https://herdr.dev) server and lets you attach from anywhere over SSH. Sessions, logins and
the agents' conversation history survive restarts and image updates, so you can tear the box
down and pick up exactly where you left off.

If you build Android apps, it can also run phone and tablet emulators next to the box. For GUI
work there's an optional desktop you can reach with RustDesk.

The only thing you need on the host is **Docker**.

```text
 you ── ssh :2222 (herdr --remote) ──┐
                                      ▼
 ┌─ docker host ──────────────────────────────────────────────┐
 │  agentbox container                                        │
 │    herdr server ─ claude / codex / … per workspace         │
 │    /src/repo-a  /src/repo-b                                │
 │    toolchains from the config (.NET, JDK, Android SDK, …)  │
 │    optional desktop + RustDesk                             │
 │        │ adb                                               │
 │        ├──────────► phone emulator  (own container)        │
 │        └──────────► tablet emulator (own container)        │
 └────────────────────────────────────────────────────────────┘
```

## Getting started

Grab the wrapper script and let it ask you a few questions:

```bash
curl -fsSLo agentbox https://github.com/RedRem95/Agentbox/releases/latest/download/agentbox
chmod +x agentbox
./agentbox init      # asks for repos, looks at them, suggests toolchains, writes agentbox.yaml
./agentbox up        # pulls the image and starts everything
```

Then connect:

```bash
herdr --remote ssh://dev@localhost:2222
```

`init` clones each repo you name (shallowly, just to look at it) and reads `global.json`,
project files, `package.json`, `pyproject.toml`, Gradle files and your CI workflows to find out
which SDKs and versions you actually use. You can tweak everything afterwards in the YAML.

Prefer [uv](https://docs.astral.sh/uv/)? The tool is a normal Python package, so this works too
(you need the Docker CLI on the host then):

```bash
uvx --from "git+https://github.com/RedRem95/Agentbox#subdirectory=cli" agentbox init
```

## Day to day

| Command | What it does |
|---|---|
| `./agentbox up` | Start or update the stack. The box is recreated so it picks up config changes; emulators are only recreated when their definition changed. |
| `./agentbox status` | Containers, the RustDesk ID and the latest warnings from the box. |
| `./agentbox logs [service]` | Follow the logs (default: the box). |
| `./agentbox stop` / `start` | Pause and resume. Nothing is lost. |
| `./agentbox down` | Remove containers and network. Your data stays in volumes. |
| `./agentbox shell` | A shell inside the box, in `/src`. |
| `./agentbox login gh` | GitHub device login inside the box. No `gh` needed on the host. |
| `./agentbox login claude` | Same for Claude, if you don't want to do it in the herdr pane. |
| `./agentbox set rustdesk-password` | Store a secret in `.env` (also `gh-token`, `claude-token`). |
| `./agentbox check` | Will this fit? Compares RAM/CPU limits with the Docker host. |
| `./agentbox pubkey` | The box's SSH key, if you want to add it to GitHub. |

Use `-c path/to/config.yaml` (or `AGENTBOX_CONFIG`) if your config isn't `./agentbox.yaml`.
Run `./agentbox pull` to update the image for the wrapper version you have.

## The config

A small one looks like this:

```yaml
layout: root            # one herdr workspace in /src that sees all repos
agents: [claude]
repos:
  - https://github.com/you/backend.git
  - url: git@github.com:you/app.git
    setup: dotnet restore App.sln   # runs once after the first clone

packages:
  tools: [dotnet, python, uv]
  dotnet_version: "10.0.401"

container:
  name: mybox
  resources: { memory: 8g, cpus: 4 }
```

[`examples/agentbox.example.yaml`](examples/agentbox.example.yaml) shows every option with
comments, and [`examples/travelrepo`](examples/travelrepo) is a complete real-world setup with two
.NET/Avalonia repos, an Android app, phone + tablet emulators and a 20 GB / 5 CPU budget.

Secrets never go into the config. They live in `.env` next to it, readable only by you.

## What survives what

Everything that matters lives in Docker volumes named after your box:

- `<name>-home`: agent sessions and history, logins, the box's SSH key, herdr layout, RustDesk ID
- `<name>-src`: your repos, including uncommitted work
- `<name>-tools`: SDKs and toolchains, so restarts are fast
- `<emulator>-data`: installed apps and emulator state

`up`, image updates and `down` keep all of it. Only `down --volumes` deletes it, and it asks first.

## Android

Emulators run in their own containers ([docker-android](https://github.com/budtmo/docker-android))
and need `/dev/kvm`, which means a Linux x86_64 host. The box keeps an `adb` connection to them, so
`adb devices` just works in every herdr pane, and the agents get the
[mobile-mcp](https://github.com/mobile-next/mobile-mcp) server to look at and tap through the app.
You can watch each emulator in your browser via noVNC (bound to `localhost` only). Each emulator
gets as many virtual cores as its container's `cpus` limit allows (rounded up); override with
`cores: N`.

When emulators keep crashing, you want the agent to look into it. With
`android.docker_control: true` the box gets `emu`: `emu status` (restarts, OOM kills, exit code,
memory), `emu logs`, `emu files` (copies the emulator's log files out) and `emu restart`. It goes
through a small socket proxy ([wollomatic/socket-proxy](https://github.com/wollomatic/socket-proxy))
that only lets through these containers and these actions – no `exec`, no other containers.

On a Mac there's no KVM inside Docker. Run the emulator natively and point the box at it with
`android.devices: ["host.docker.internal:5555"]`.

## What the agents know

Claude gets an `agentbox` skill, generated from your config on every start: which repos and
toolchains are there, what survives a rebuild and what doesn't, that the config is read-only and
changes go through you, how to reach the emulators, the real memory limit, and whether it may use
`emu`. A three-line note in `~/.claude/CLAUDE.md` points to it; your own content in that file is
left alone.

## Desktop and RustDesk

Set `desktop.enabled: true` to get a lightweight X desktop (Xvfb, Openbox) inside the box.
Anything you start from herdr shows up there, so you can click through a desktop app yourself.
RustDesk can be reached directly from your LAN, through your own hbbs/hbbr server, or both.
The password is asked for on the first `up`.

## Security notes

- SSH is key-only: no passwords, no root login, only the `dev` user. Docker publishes ports
  past ufw, so if you don't want 2222 open, set `container.ssh_bind: 127.0.0.1` and jump through
  the host's own SSH.
- `dev` has passwordless sudo by default (`user.sudo`). Anyone with an authorized key is root
  inside the box.
- The `agentbox` tool needs the Docker socket and your home directory, so it can start containers
  and read your SSH keys and git config for the wizard. That's the same trust you give `docker`
  itself.

## Requirements

- Docker with the Compose plugin
- Android emulators: Linux, x86_64, `/dev/kvm`
- The image is built for `linux/amd64`.

## Development

```bash
git clone https://github.com/RedRem95/Agentbox && cd Agentbox
./agentbox build                  # builds agentbox:local, the wrapper uses it automatically in a checkout
cd cli && uv run --with pytest pytest
```

The repo layout:

- `agentbox`: the host wrapper (bash)
- `cli/`: the Python tool (`init`, `up`, compose generation, repo detection)
- `image/`: what runs inside the box (entrypoint, adb watcher, desktop)
- `Dockerfile`: one image for both the box and the tool

### Releasing

1. Set `__version__` in `cli/agentbox_cli/__init__.py` (the only place, `pyproject.toml` reads it
   from there), written exactly like the tag without `v`: `0.2.0` or `0.2.0-rc.1`. Add a
   `## [X.Y.Z]` section to `CHANGELOG.md`.
2. Either push a tag `vX.Y.Z`, or publish a release on GitHub with a new tag `vX.Y.Z`.
   If you write text in the web UI, it stays on top; the workflow adds whatever is missing.

The release workflow first switches the release to pre-release, builds and pushes
`ghcr.io/redrem95/agentbox`, attaches the versioned wrapper and completes the notes
(changelog section, install block, list of merged PRs). Only then it becomes the latest release.
If a step fails, the release stays a pre-release, so nobody downloads a half-finished one.
Versions like `v0.2.0-rc.1` always stay pre-releases.

The CLI currently talks German. English output is on the list.

## License

[MIT](LICENSE)
