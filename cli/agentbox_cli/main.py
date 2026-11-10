"""agentbox – Kommandozeile."""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from . import __version__, compose, docker, envfile, resources, wizard
from . import config as C
from .stack import Stack

TTY = sys.stdin.isatty() and sys.stdout.isatty()


def _stack(args) -> Stack:
    p = Path(args.config)
    if not p.exists():
        sys.exit(f"Config {p} nicht gefunden – erst `agentbox init` (oder -c <datei>)")
    return Stack.from_config(p, C.Host.detect())


def _existing(stack: Stack) -> set[str]:
    return {e.name for e in stack.emulators if docker.exists(e.name)}


def _ensure_compose(stack: Stack) -> Path:
    if not stack.compose_path.exists():
        doc, _, _ = compose.build(stack, _existing(stack))
        compose.write(stack, doc)
    return stack.compose_path


def _box_running(stack: Stack) -> None:
    if not docker.exists(stack.name):
        sys.exit(f"Container {stack.name} läuft nicht – erst `agentbox up`")


def _ensure_rustdesk_password(stack: Stack) -> None:
    env = stack.env_file_or_default
    if not stack.rustdesk or envfile.has(env, "RUSTDESK_PASSWORD"):
        return
    if os.environ.get("RUSTDESK_PASSWORD"):
        envfile.set_var(env, "RUSTDESK_PASSWORD", os.environ["RUSTDESK_PASSWORD"])
    elif TTY:
        print("RustDesk ist aktiviert, aber noch kein Passwort gesetzt.")
        envfile.set_var(env, "RUSTDESK_PASSWORD", envfile.prompt_secret("RustDesk-Passwort"))
    else:
        sys.exit("RUSTDESK_PASSWORD fehlt – `agentbox set rustdesk-password`")
    stack.host.chown(env, stack.cfg_path)


# ---------------------------------------------------------------- Befehle
def cmd_init(args) -> int:
    host = C.Host.detect()
    try:
        res = resources.HostRes.query()
    except docker.DockerError:
        res = None
    path = Path(args.config)
    if not wizard.run(path, host, res):
        return 1
    stack = Stack.from_config(path, host)
    if res:
        print()
        resources.check(stack, res, _existing(stack)).print()
    if TTY and wizard.yes("\nJetzt Image prüfen und Stack starten?", True):
        return cmd_up(args)
    print(f"Starten mit: ./agentbox up{'' if path.name == 'agentbox.yaml' else f' -c {path}'}")
    return 0


def cmd_check(args) -> int:
    stack = _stack(args)
    rep = resources.check(stack, resources.HostRes.query(stack.name), _existing(stack))
    rep.print()
    return 1 if rep.errors else 0


def cmd_generate(args) -> int:
    stack = _stack(args)
    doc, _, notes = compose.build(stack, _existing(stack))
    for n in notes:
        print(f"Hinweis: {n}")
    print(f"Compose-Datei: {compose.write(stack, doc)}")
    return 0


def cmd_up(args) -> int:
    stack = _stack(args)
    existing = _existing(stack)
    rep = resources.check(stack, resources.HostRes.query(stack.name), existing)
    rep.print()
    if rep.errors and not args.force:
        print("Abbruch. Limits anpassen oder --force.")
        return 1
    if rep.warnings and not args.force and TTY and not wizard.yes("Trotzdem starten?", False):
        return 1
    if docker.run("image", "inspect", stack.image, check=False, quiet=True).returncode != 0:
        if "/" not in stack.image.split(":")[0]:
            sys.exit(f"Lokales Image {stack.image} fehlt – im Repo-Checkout `./agentbox build`")
        print(f"Lade Image {stack.image} …")
        docker.run("pull", stack.image)
    _ensure_rustdesk_password(stack)

    doc, side_services, notes = compose.build(stack, existing)
    for n in notes:
        print(f"Hinweis: {n}")
    path = compose.write(stack, doc)
    print(f"Compose-Datei: {path}")

    # Container aus früheren run.sh/docker-run-Starts übernehmen (gleiche Namen/Volumes)
    for c in [stack.name, *side_services]:
        if docker.exists(c) and not docker.compose_project(c):
            print(f"Ersetze alten Container {c} durch Compose-Version (Volumes bleiben)")
            docker.run("rm", "-f", c, quiet=True)

    # Emulatoren/Proxy nur bei geänderter Definition neu, Box immer neu (Entrypoint liest Config neu)
    if side_services:
        docker.compose(path, "up", "-d", "--remove-orphans", *side_services)
    docker.compose(path, "up", "-d", "--remove-orphans", "--no-deps", "--force-recreate", "agentbox")
    _hints(stack)
    return 0


def _hints(stack: Stack, rustdesk_id: str | None = None) -> None:
    host = "localhost" if stack.ssh_bind in ("", "127.0.0.1") else stack.ssh_bind
    print("\nLogs:       ./agentbox logs")
    print(f"Verbinden:  herdr --remote ssh://dev@{host}:{stack.ssh_port}")
    for e in stack.emulators:
        if e.vnc_port:
            print(f"Emulator:   {e.name} -> http://localhost:{e.vnc_port}")
    if stack.rustdesk:
        direct = f", direkt: <host-ip>:{stack.rd_port}" if stack.rd_direct else ""
        print(f"RustDesk:   ID {rustdesk_id}{direct}" if rustdesk_id else f"RustDesk:   ID mit `./agentbox status`{direct}")


def _rustdesk_id(stack: Stack, logs: str) -> str | None:
    found = [i for i in re.findall(r"RustDesk-ID: (\S+)", logs) if i.isdigit()]
    if found:
        return found[-1]
    # nicht (mehr) im Log, z.B. weil RustDesk beim Start noch nicht bereit war: live fragen
    out = docker.out("exec", "-u", "dev", "-e", "DISPLAY=:0", stack.name, "rustdesk", "--get-id")
    digits = "".join(c for c in out if c.isdigit())
    return digits or None


def _hints(stack: Stack, rustdesk_id: str | None = None) -> None:
    host = "localhost" if stack.ssh_bind in ("", "127.0.0.1") else stack.ssh_bind
    print("\nLogs:       ./agentbox logs")
    print(f"Verbinden:  herdr --remote ssh://dev@{host}:{stack.ssh_port}")
    for e in stack.emulators:
        if e.vnc_port:
            print(f"Emulator:   {e.name} -> http://localhost:{e.vnc_port}")
    if stack.rustdesk:
        direct = f", direkt: <host-ip>:{stack.rd_port}" if stack.rd_direct else ""
        print(f"RustDesk:   ID {rustdesk_id}{direct}" if rustdesk_id else f"RustDesk:   ID mit `./agentbox status`{direct}")


def _rustdesk_id(stack: Stack, logs: str) -> str | None:
    found = [i for i in re.findall(r"RustDesk-ID: (\S+)", logs) if i.isdigit()]
    if found:
        return found[-1]
    # nicht (mehr) im Log, z.B. weil RustDesk beim Start noch nicht bereit war: live fragen
    out = docker.out("exec", "-u", "dev", "-e", "DISPLAY=:0", stack.name, "rustdesk", "--get-id")
    digits = "".join(c for c in out if c.isdigit())
    return digits or None


def cmd_status(args) -> int:
    stack = _stack(args)
    docker.compose(_ensure_compose(stack), "ps", check=False)
    rd_id = None
    if docker.exists(stack.name):
        logs = docker.logs(stack.name)
        if stack.rustdesk:
            rd_id = _rustdesk_id(stack, logs)
            if not rd_id:
                print("\nRustDesk-ID: noch keine – Desktop-Log: "
                      f"docker exec -u dev {stack.name} tail -40 ~/.agentbox-desktop.log")
        warns = [line for line in logs.splitlines() if "WARN" in line][-5:]
        if warns:
            print("\nLetzte Warnungen:")
            print("\n".join(f"  {w}" for w in warns))
    _hints(stack, rd_id)
    return 0


def cmd_simple(action: str):
    def f(args) -> int:
        stack = _stack(args)
        extra = ["-v"] if action == "down" and getattr(args, "volumes", False) else []
        if extra and not (TTY and wizard.yes("ALLE Volumes löschen (Repos, Logins, Sessions, SDKs, Emulator-Daten)?", False)):
            return 1
        return docker.compose(_ensure_compose(stack), action, *extra, *getattr(args, "services", []), check=False)
    return f


def cmd_logs(args) -> int:
    stack = _stack(args)
    svc = args.service or "agentbox"
    a = ["logs", "--tail", str(args.tail)] + (["-f"] if not args.no_follow else []) + [svc]
    return docker.compose(_ensure_compose(stack), *a, check=False)


def cmd_shell(args) -> int:
    stack = _stack(args)
    _box_running(stack)
    return docker.exec_(stack.name, "bash", "-l", tty=True, workdir="/src", check=False)


def cmd_pubkey(args) -> int:
    stack = _stack(args)
    _box_running(stack)
    return docker.exec_(stack.name, "cat", "/home/dev/.ssh/id_ed25519.pub", check=False)


def cmd_login(args) -> int:
    stack = _stack(args)
    _box_running(stack)
    if args.service == "gh":
        print("Code wird angezeigt -> auf beliebigem Gerät https://github.com/login/device öffnen.")
        docker.exec_(stack.name, "gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "ssh", tty=True)
        docker.exec_(stack.name, "gh", "auth", "setup-git")
        return docker.exec_(stack.name, "gh", "auth", "status", check=False)
    if args.service == "claude":
        print("Startet Claude in der Box: Login-Methode wählen, URL öffnen, Code einfügen, dann /exit.")
        return docker.exec_(stack.name, "claude", tty=True, workdir="/src", check=False)
    return 1


def cmd_set(args) -> int:
    stack = _stack(args)
    env = stack.env_file_or_default
    if args.what == "rustdesk-password":
        envfile.set_var(env, "RUSTDESK_PASSWORD", envfile.prompt_secret("RustDesk-Passwort"))
    elif args.what == "gh-token":
        envfile.set_var(env, "GH_TOKEN", envfile.prompt_secret("GitHub-Token", 10, False))
    elif args.what == "claude-token":
        envfile.set_var(env, "CLAUDE_CODE_OAUTH_TOKEN", envfile.prompt_secret("Claude-Token", 10, False))
    stack.host.chown(env, stack.cfg_path)
    print(f"Gespeichert in {env}. Übernehmen mit: ./agentbox up")
    return 0


# ---------------------------------------------------------------- Parser
def build_parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(add_help=False)
    top.add_argument("-c", "--config", default=os.environ.get("AGENTBOX_CONFIG", "agentbox.yaml"),
                     help="Config-Datei (Standard: agentbox.yaml)")
    # In Subcommands ohne Default, sonst überschreibt argparse ein vorher angegebenes -c
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", default=argparse.SUPPRESS, help="Config-Datei")
    p = argparse.ArgumentParser(prog="agentbox", parents=[top],
                                description="Agent-Boxen mit herdr, Repos, Toolchains und Android-Emulatoren.")
    p.add_argument("--version", action="version", version=f"agentbox {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="BEFEHL")

    def add(name, fn, help_):
        sp = sub.add_parser(name, parents=[common], help=help_)
        sp.set_defaults(fn=fn)
        return sp

    add("init", cmd_init, "Config per Frage-Antwort erstellen").add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    add("check", cmd_check, "RAM/CPU gegen den Docker-Host prüfen")
    add("generate", cmd_generate, "nur Compose-Datei schreiben")
    add("up", cmd_up, "Stack starten/aktualisieren (Box wird neu erstellt)").add_argument(
        "--force", action="store_true", help="trotz Ressourcen-Fehlern/Warnungen")
    add("status", cmd_status, "Zustand, RustDesk-ID, letzte Warnungen")
    for a, h in (("stop", "anhalten (alles bleibt erhalten)"), ("start", "angehaltenen Stack starten"),
                 ("restart", "neu starten")):
        add(a, cmd_simple(a), h).add_argument("services", nargs="*", help="nur diese Services")
    add("down", cmd_simple("down"), "Container + Netz entfernen (Volumes bleiben)").add_argument(
        "--volumes", action="store_true", help="AUCH alle Volumes löschen")
    lg = add("logs", cmd_logs, "Logs anzeigen (Standard: Box, folgt)")
    lg.add_argument("service", nargs="?")
    lg.add_argument("--tail", type=int, default=100)
    lg.add_argument("--no-follow", action="store_true")
    add("shell", cmd_shell, "Shell in der Box (als dev, in /src)")
    add("pubkey", cmd_pubkey, "SSH-Public-Key der Box (für GitHub)")
    add("login", cmd_login, "Login in der Box: gh | claude").add_argument("service", choices=["gh", "claude"])
    add("set", cmd_set, "Secret setzen (landet in .env)").add_argument(
        "what", choices=["rustdesk-password", "gh-token", "claude-token"])
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or 0
    except docker.DockerError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nAbgebrochen.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
