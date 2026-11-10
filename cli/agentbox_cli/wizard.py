"""agentbox init – Frage-Antwort-Assistent, der die Config baut."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from ruamel.yaml.comments import CommentedMap as M
from ruamel.yaml.scalarstring import LiteralScalarString as Lit

from . import config as C
from . import envfile
from .detect import Detected, clone, repo_name, scan
from .resources import GiB, HostRes

AGENTS = ["claude", "codex", "opencode", "gemini", "cline", "qwen"]
EMU_PRESETS = {"phone": ("Samsung Galaxy S10", "Phone"), "tablet": ("Pixel C", "Tablet")}


# ---------------------------------------------------------------- Eingabe-Helfer
def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    v = input(f"{prompt}{suffix}: ").strip()
    return v if v else (default or "")


def yes(prompt: str, default: bool = True) -> bool:
    v = input(f"{prompt} [{'J/n' if default else 'j/N'}]: ").strip().lower()
    return default if not v else v in ("j", "ja", "y", "yes")


def choice(prompt: str, options: list[tuple[str, str]], default: str) -> str:
    print(prompt)
    for i, (key, label) in enumerate(options, 1):
        print(f"  {i}) {label}" + ("  (Standard)" if key == default else ""))
    while True:
        v = input("Auswahl: ").strip()
        if not v:
            return default
        if v.isdigit() and 1 <= int(v) <= len(options):
            return options[int(v) - 1][0]
        for key, _ in options:
            if v == key:
                return key
        print("Bitte Nummer eingeben.")


def multi(prompt: str, options: list[str], default: list[str]) -> list[str]:
    v = ask(f"{prompt} ({', '.join(options)}; mehrere mit Komma)", ",".join(default))
    picked = [x.strip() for x in v.split(",") if x.strip()]
    unknown = [x for x in picked if x not in options]
    if unknown:
        print(f"  Hinweis: unbekannt {unknown} – wird trotzdem übernommen (eigener Befehl via agent_commands)")
    return picked


def number(prompt: str, default: float, minimum: float = 0) -> float:
    while True:
        v = ask(prompt, f"{default:g}")
        try:
            f = float(v.replace(",", "."))
            if f >= minimum:
                return f
        except ValueError:
            pass
        print(f"Bitte Zahl ≥ {minimum:g} eingeben.")


def header(t: str) -> None:
    print(f"\n── {t} " + "─" * max(0, 60 - len(t)))


def _gitconfig(host: C.Host, key: str) -> str:
    gc = host.home / ".gitconfig"
    if not gc.exists():
        return ""
    r = subprocess.run(["git", "config", "--file", str(gc), key], capture_output=True, text=True)
    return r.stdout.strip()


# ---------------------------------------------------------------- Assistent
def run(cfg_path: Path, host: C.Host, res: HostRes | None) -> bool:
    print("agentbox init – baut eine Config. Enter übernimmt den Vorschlag in [Klammern].")
    if cfg_path.exists() and not yes(f"{cfg_path.name} existiert bereits. Überschreiben?", False):
        return False
    env_path = cfg_path.parent / ".env"
    secrets: dict[str, str] = {}

    header("Box")
    name = ask("Name der Box (Container-Name, Präfix für Volumes)", "agentbox")

    # ---- Repos + Erkennung
    header("Repos")
    print("Repo-URLs (git@github.com:org/repo.git oder https://…), leer = fertig.")
    repos: list[tuple[str, Detected | None]] = []
    total = Detected(name="gesamt")
    tmp = Path(tempfile.mkdtemp(prefix="agentbox-scan-"))
    try:
        while True:
            url = ask(f"Repo {len(repos) + 1}")
            if not url:
                break
            rn = repo_name(url)
            print(f"  analysiere {rn} …", end="", flush=True)
            ok, err = clone(url, tmp / rn)
            if not ok:
                print(" nicht lesbar (Erkennung übersprungen)")
                if err:
                    print(f"    {err.splitlines()[-1]}")
                repos.append((url, None))
                continue
            d = scan(tmp / rn, rn)
            total.merge(d)
            found = d.summary()
            print(" " + ("; ".join(found) if found else "nichts Besonderes erkannt"))
            repos.append((url, d))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Reihenfolge: Repos, auf die andere per ProjectReference zeigen, zuerst
    names = {repo_name(u): (u, d) for u, d in repos}
    ordered: list[tuple[str, Detected | None]] = []

    def add(n: str, seen: set) -> None:
        if n in seen or n not in names:
            return
        seen.add(n)
        for s in sorted(names[n][1].siblings if names[n][1] else []):
            add(s, seen)
        ordered.append(names[n])

    seen: set = set()
    for u, _ in repos:
        add(repo_name(u), seen)
    for _url, d in ordered:
        if d and d.siblings:
            missing = d.siblings - set(names)
            if missing:
                print(f"  Hinweis: {d.name} referenziert {', '.join(sorted(missing))} als Nachbar-Ordner – fehlt in der Liste")

    header("Toolchains")
    if total.summary():
        print("Erkannt:")
        for line in total.summary():
            print(f"  • {line}")
        for n in total.notes:
            print(f"  ! {n}")
        if not yes("Übernehmen? (Feinschliff später direkt in der YAML)", True):
            total = Detected(name="gesamt")
    else:
        print("Nichts erkannt.")
    extra = ask("Zusätzliche Tools (python, uv, dotnet, go, rust, java, bun; Komma)", "")
    total.tools |= {t.strip() for t in extra.split(",") if t.strip()}

    # ---- Agenten
    header("Agenten")
    layout = choice("Wo sollen Agenten starten?", [
        ("root", "Ein Workspace im Basisordner /src (sieht alle Repos)"),
        ("per-project", "Ein Workspace pro Repo"),
    ], "root" if len(repos) > 1 else "per-project")
    agents = multi("Agenten", AGENTS, ["claude"])

    # ---- Android
    emulators: list[tuple[str, str]] = []
    docker_control = False
    if total.android or yes("Android-Emulatoren einrichten?", False):
        header("Android-Emulatoren")
        print("Emulatoren laufen in eigenen Containern (brauchen /dev/kvm) und hängen per adb an der Box.")
        if not host.kvm:
            print("  ! /dev/kvm fehlt auf diesem Host – Emulator-Container würden nicht starten.")
        sel = choice("Welche?", [("both", "Phone + Tablet"), ("phone", "Phone"), ("tablet", "Tablet"), ("none", "Keine")],
                     "both" if host.kvm else "none")
        for key in (["phone", "tablet"] if sel == "both" else [] if sel == "none" else [sel]):
            dev = ask(f"  Gerät für {EMU_PRESETS[key][1]}", EMU_PRESETS[key][0])
            emulators.append((key, dev))
        if emulators:
            docker_control = yes("Dürfen die Agenten die Emulator-Container ansehen und neu starten? "
                                 "(Logs, Status, Restart – nur diese Container, kein exec)", True)

    # ---- Desktop / RustDesk
    header("Desktop (GUI-Tests per RustDesk)")
    desktop = yes("Virtuellen Desktop mit RustDesk einrichten?", total.gui)
    rd = M()
    if desktop:
        rd["enabled"] = True
        rd["version"] = "latest"
        direct = yes("Direkt-IP-Zugriff aus dem LAN?", True)
        bind = ask("  Nur auf dieser Host-IP lauschen (z.B. LAN-IP 192.168.1.10; leer = alle)", "") if direct else ""
        rd["direct"] = M(enabled=direct, port=21118, bind=bind)
        relay = M(id_server="", relay_server="", api_server="", key="")
        if yes("Eigenen RustDesk-Server (hbbs/hbbr) nutzen?", False):
            relay["id_server"] = ask("  ID-Server (z.B. rd.example.com)")
            relay["relay_server"] = ask("  Relay-Server (leer = vom ID-Server abgeleitet)", "")
            relay["key"] = ask("  Public Key des Servers (id_ed25519.pub)")
        rd["relay"] = relay
        secrets["RUSTDESK_PASSWORD"] = envfile.prompt_secret("  RustDesk-Passwort")

    # ---- GitHub / Claude
    header("Zugänge")
    gh = choice("GitHub-Zugang für gh und Agenten:", [
        ("container", "Login im Container (später: ./agentbox login gh)"),
        ("hostdir", "Login-Daten in einem Host-Ordner (mehrere Boxen teilen sich den Login)"),
        ("token", "Token (GH_TOKEN) jetzt eingeben"),
    ], "container")
    gh_dir = ""
    if gh == "hostdir":
        gh_dir = ask("  Ordner", "~/.agentbox/gh")
    elif gh == "token":
        secrets["GH_TOKEN"] = envfile.prompt_secret("  GitHub-Token", min_len=10, confirm=False)
    if "claude" in agents and yes("Claude-Token (aus `claude setup-token`) jetzt eintragen? Sonst Login im herdr-Pane", False):
        secrets["CLAUDE_CODE_OAUTH_TOKEN"] = envfile.prompt_secret("  Token", min_len=10, confirm=False)

    header("SSH")
    keys: list[str] = []
    for pub in sorted((host.home / ".ssh").glob("*.pub")) if (host.home / ".ssh").is_dir() else []:
        line = pub.read_text().strip()
        if line and yes(f"Key {pub.name} ({line.split()[-1] if len(line.split()) > 2 else ''}) erlauben?", True):
            keys.append(line)
    while True:
        k = ask("Weiteren Public Key einfügen (z.B. vom Laptop; leer = fertig)", "")
        if not k:
            break
        keys.append(k)
    if not keys:
        print("  ! Kein Key – Login per SSH/herdr ist erst möglich, wenn du einen in die Config einträgst.")
    port = int(number("SSH-Port auf dem Host", 2222, 1))
    ssh_bind = "" if yes("SSH-Port aus dem Netz erreichbar machen? (Nein = nur lokal/per SSH-Jump)", True) else "127.0.0.1"
    git_name = ask("Git-Name", _gitconfig(host, "user.name") or "")
    git_email = ask("Git-E-Mail", _gitconfig(host, "user.email") or "")

    # ---- Ressourcen
    header("Ressourcen")
    n_emu = len(emulators)
    box_need = 1 + 0.75 * max(1, len(agents)) + (2 if total.android else 0) + (1 if desktop else 0)
    if res:
        host_gb, host_cpu = res.mem_total / GiB, res.cpus
        print(f"Docker-Host: {host_gb:.1f} GB RAM, {host_cpu} CPUs (frei ~{res.mem_available / GiB:.1f} GB)")
    else:
        host_gb, host_cpu = 16.0, 4
    want = box_need + 2 + 4.5 * n_emu
    budget_gb = number("RAM-Budget für den ganzen Stack (GB)", round(min(host_gb * 0.7, max(want, 8))), 2)
    budget_cpu = number("CPU-Budget (Kerne)", max(2, round(host_cpu * 0.6)), 1)
    emu_mem = 4.5 if budget_gb - 4.5 * n_emu >= box_need else 3.5
    emu_cpu = 1.5 if budget_cpu - 1.5 * n_emu >= 2 else 1.0
    box_mem = round(budget_gb - emu_mem * n_emu, 1)
    box_cpu = max(1.0, round(budget_cpu - emu_cpu * n_emu, 1))
    print(f"  Aufteilung: Box {box_mem:g} GB / {box_cpu:g} CPU"
          + (f", je Emulator {emu_mem:g} GB / {emu_cpu:g} CPU" if n_emu else ""))
    if box_mem < box_need:
        print(f"  ! Box hat weniger als geschätzt nötig ({box_need:g} GB) – Budget erhöhen oder Emulatoren reduzieren")

    # ---------------------------------------------------------------- Config zusammenbauen
    cfg = M()
    cfg["workdir"] = "/src"
    cfg["layout"] = layout
    cfg["agents"] = agents
    cfg["resume"] = True
    rl = []
    for u, d in ordered:
        if d and d.setup:
            rl.append(M(url=u, setup=Lit("\n".join(d.setup) + "\n")))
        else:
            rl.append(u)
    cfg["repos"] = rl
    docs = [(d.name, doc) for _, d in ordered if d for doc in d.agent_docs]
    if layout == "root" and docs:
        body = ["# Workspace", "", "Repos nebeneinander in /src.", ""] + [f"@{n}/{doc}" for n, doc in docs]
        cfg["files"] = M({"CLAUDE.md": Lit("\n".join(body) + "\n")})

    pk = M()
    tools = set(total.tools)
    if total.python:
        tools |= {"python", "uv"}
    pk["tools"] = sorted(tools)
    if total.dotnet_version:
        pk["dotnet_version"] = total.dotnet_version
    elif total.dotnet_majors:
        pk["dotnet_channel"] = f"{max(total.dotnet_majors)}.0"
    if total.workloads:
        pk["dotnet_workloads"] = sorted(total.workloads)
    if total.python:
        pk["python"] = M(version=total.python, packages=sorted(total.python_pkgs))
    pk["apt"] = sorted(total.apt)
    cfg["packages"] = pk

    if total.android or emulators:
        cfg["android"] = M(jdk=total.jdk or 21,
                           sdk_packages=sorted(total.sdk_packages or {"platform-tools"}),
                           mcp=True, docker_control=docker_control)
    if desktop:
        cfg["desktop"] = M(enabled=True, resolution="1600x900", rustdesk=rd)
    cfg["user"] = M(sudo=True)
    cfg["git"] = M(name=git_name, email=git_email)
    cfg["github"] = M(config_dir=gh_dir)
    cfg["ssh"] = M(authorized_keys=keys)
    cfg["env_passthrough"] = ["GH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]

    ct = M()
    ct["name"] = name
    ct["ssh_port"] = port
    ct["ssh_bind"] = ssh_bind
    ct["env_file"] = ".env"
    ct["resources"] = M(memory=f"{int(box_mem * 1024)}m", cpus=box_cpu)
    emus = []
    for i, (key, dev) in enumerate(emulators):
        emus.append(M(name=f"{name}-{key}", image="budtmo/docker-android:emulator_14.0", device=dev,
                      vnc_port=6080 + i, memory=f"{int(emu_mem * 1024)}m", cpus=emu_cpu))
    if emus:
        ct["android_emulators"] = emus
    ct["mounts"] = []
    cfg["container"] = ct

    C.save(cfg, cfg_path, header=(
        "# agentbox-Config – erzeugt mit `agentbox init`, darf frei bearbeitet werden.\n"
        "# Secrets (Tokens, Passwörter) stehen in .env, nicht hier."))
    host.chown(cfg_path)
    for k, v in secrets.items():
        envfile.set_var(env_path, k, v)
    if secrets:
        host.chown(env_path, cfg_path)

    header("Fertig")
    print(f"Config: {cfg_path}")
    if secrets:
        print(f"Secrets: {env_path} ({', '.join(secrets)})")
    return True
