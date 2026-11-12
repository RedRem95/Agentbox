"""Compose-Datei aus dem Stack erzeugen."""
from __future__ import annotations

import io
import math
import os
import re
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString as Q

from . import config as C
from .stack import Emulator, Stack


def _limits(svc: dict, memory: str | None, cpus: float | None) -> None:
    if memory:
        svc["mem_limit"] = Q(memory)
        svc["memswap_limit"] = Q(memory)  # Swap = Limit -> hartes RAM-Limit
    if cpus:
        svc["cpus"] = int(cpus) if float(cpus).is_integer() else cpus


def _quote(doc: dict) -> dict:
    """Listen-Strings (Ports "2222:22", Env, Volumes) immer quoten – YAML-1.1-Parser lesen 2222:22 sonst als Zahl."""
    for svc in doc["services"].values():
        if isinstance(svc.get("user"), str):
            svc["user"] = Q(svc["user"])  # "0:0" / "65534:998" – sonst Zahlen in YAML 1.1
        for key in ("ports", "environment", "volumes", "devices", "extra_hosts", "group_add", "command"):
            if key in svc:
                svc[key] = [Q(v) if isinstance(v, str) else v for v in svc[key]]
    return doc


# docker-android löscht bei jedem Start mit `sudo sed -i '1d' /etc/passwd` den root-Eintrag
# (cli/src/device/emulator.py, change_permission). Beim nächsten Start desselben Containers
# scheitert dann `sudo` – docker restart, stop/start und Dockers eigener Neustart nach einem
# Absturz brechen ab. Dieser Vorspann läuft als root, stellt die Zeile wieder her und startet das
# normale Startscript ohne root-Rechte als androidusr. ($$ = $ für Compose)
def budtmo_start(kvm_gid: int | None) -> str:
    """Vorspann für docker-android. Gruppen: die von androidusr plus die KVM-Gruppe des Hosts
    (group_add allein reicht nicht, setpriv setzt die Gruppen neu)."""
    extra = f",{kvm_gid}" if kvm_gid is not None else ""
    return (
        "grep -q '^root:' /etc/passwd || sed -i '1i root:x:0:0:root:/root:/bin/bash' /etc/passwd; "
        "u=$$(id -u androidusr 2>/dev/null || echo 1300); g=$$(id -g androidusr 2>/dev/null || echo 1301); "
        "G=$$(id -G androidusr 2>/dev/null | tr ' ' ',' || echo $$g); "
        f"exec setpriv --reuid=$$u --regid=$$g --groups=$$G{extra} "
        "env HOME=/home/androidusr USER=androidusr LOGNAME=androidusr \"$$APP_PATH/mixins/scripts/run.sh\""
    )


def is_docker_android(image: str) -> bool:
    return image.split(":")[0].split("@")[0] in ("budtmo/docker-android", "docker.io/budtmo/docker-android")


def emulator_cores(e: Emulator) -> int | None:
    """vCPUs für den Android-Gast. Ohne Angabe: CPU-Limit des Containers, aufgerundet
    (die AVD steht sonst auf hw.cpu.ncore=1 und nutzt nur einen Kern)."""
    if e.cores is not None:
        return e.cores or None
    return max(1, math.ceil(e.cpus)) if e.cpus else None


def emulator_env(e: Emulator) -> dict:
    """Eigene env-Einträge plus -cores in EMULATOR_ADDITIONAL_ARGS (gilt bei jedem Start,
    vor hw.cpu.ncore aus der AVD). Steht dort schon ein -cores, bleibt es unangetastet."""
    env = dict(e.env)
    cores = emulator_cores(e)
    args = env.get("EMULATOR_ADDITIONAL_ARGS", "")
    if cores and not re.search(r"(^|\s)-cores(\s|$)", args):
        env["EMULATOR_ADDITIONAL_ARGS"] = f"{args} -cores {cores}".strip()
    return env


def build(stack: Stack, existing: set[str]) -> tuple[dict, list[str], list[str]]:
    """-> (compose-dict, Emulator-Services, Hinweise)"""
    notes: list[str] = []
    host = stack.host
    uid, gid = host.owner(stack.cfg_path)
    services: dict = {}
    volumes = [f"{stack.name}-home", f"{stack.name}-tools", f"{stack.name}-sshd"]
    emu_services: list[str] = []

    kvm_gid = C.get(stack.cfg, "container.kvm_gid", host.kvm_gid)
    for e in stack.emulators:
        if not host.kvm and e.name not in existing:
            notes.append(f"/dev/kvm fehlt – Emulator {e.name} wird ausgelassen")
            continue
        svc: dict = {
            "image": e.image, "container_name": e.name, "hostname": e.name,
            "restart": "unless-stopped", "devices": ["/dev/kvm"], "networks": ["stack"],
            "environment": [f"EMULATOR_DEVICE={e.device}"]
            + (["WEB_VNC=true"] if e.vnc_port else [])
            + [f"{k}={v}" for k, v in emulator_env(e).items()],
            "volumes": [f"{e.name}-data:/home/androidusr"],
        }
        if e.restart_fix and is_docker_android(e.image):
            svc["user"] = "0:0"
            svc["entrypoint"] = ["/bin/sh", "-c", budtmo_start(kvm_gid)]
        # Der Emulator läuft im Container nicht als root: er braucht die Gruppe, der /dev/kvm auf dem Host gehört
        if kvm_gid is not None:
            svc["group_add"] = [str(kvm_gid)]
        if e.vnc_port:
            svc["ports"] = [f"127.0.0.1:{e.vnc_port}:6080"]  # noVNC nur lokal; adb bleibt intern
        _limits(svc, e.memory, e.cpus)
        services[e.name] = svc
        volumes.append(f"{e.name}-data")
        emu_services.append(e.name)

    ports = [f"{stack.ssh_bind + ':' if stack.ssh_bind else ''}{stack.ssh_port}:22"]
    if stack.rd_direct:
        ports.append(f"{stack.rd_bind + ':' if stack.rd_bind else ''}{stack.rd_port}:21118")

    vols = [
        f"{stack.cfg_path}:/config/workspace.yaml:ro",
        f"{stack.name}-home:/home/dev",
        f"{stack.name}-tools:/opt/tools",
        f"{stack.name}-sshd:/persist",
    ]
    if stack.src:
        src = Path(host.expand(stack.src))
        src.mkdir(parents=True, exist_ok=True)
        vols.append(f"{src}:/src")
    else:
        volumes.append(f"{stack.name}-src")
        vols.append(f"{stack.name}-src:/src")

    for m in C.as_list(C.get(stack.cfg, "container.mounts")):
        m = host.expand(str(m))
        src = m.split(":", 1)[0]
        if os.path.exists(src):
            vols.append(m)
        elif src.startswith(str(host.home)) or src.startswith(str(stack.cfg_path.parent)):
            notes.append(f"Überspringe Mount (fehlt): {src}")
        else:
            vols.append(m)  # außerhalb der sichtbaren Ordner: nicht prüfbar, Docker meckert ggf.
            notes.append(f"Mount nicht prüfbar (außerhalb von Home/Projekt): {src}")

    gh_dir = C.get(stack.cfg, "github.config_dir", "")
    if gh_dir:
        p = Path(host.expand(str(gh_dir)))
        p.mkdir(parents=True, exist_ok=True)
        os.chmod(p, 0o700)
        host.chown(p, stack.cfg_path)
        vols.append(f"{p}:/home/dev/.config/gh")

    box: dict = {
        "image": stack.image, "container_name": stack.name, "hostname": stack.name,
        "restart": "unless-stopped", "networks": ["stack"],
        "extra_hosts": ["host.docker.internal:host-gateway"],
        "ports": ports,
        "environment": [f"HOST_UID={uid}", f"HOST_GID={gid}"],
        "volumes": vols,
    }
    if stack.env_file or stack.rustdesk:
        box["env_file"] = [{"path": str(stack.env_file_or_default), "required": False}]
    _limits(box, stack.box_memory, stack.box_cpus)
    services["agentbox"] = box
    networks = {"stack": {"name": stack.network}}

    # android.docker_control: Agenten dürfen die Emulator-Container ansehen und neu starten –
    # über einen Socket-Proxy, der nur diese Container-Namen und nur diese Aktionen durchlässt.
    side = list(emu_services)
    if emu_services and C.get(stack.cfg, "android.docker_control", False) is True:
        proxy = f"{stack.name}-dockerproxy"
        services[proxy] = docker_proxy(stack.name, emu_services, host)
        networks["ctl"] = {"name": f"{stack.name}-ctl"}
        box["networks"].append("ctl")
        box["environment"].append(f"DOCKER_HOST=tcp://{proxy}:2375")
        side.append(proxy)

    doc = {
        "name": stack.name,
        "services": services,
        "networks": networks,
        "volumes": {v: {"name": v} for v in volumes},
    }
    return doc, side, notes


def docker_proxy(box_name: str, emulators: list[str], host: C.Host) -> dict:
    """wollomatic/socket-proxy mit Allowlist: nur die Emulatoren, nur lesen + start/stop/restart/kill.
    Bewusst ohne exec (ließe sich "privileged" anfordern – der Proxy sieht nur Pfade, keine Inhalte)."""
    names = "|".join(re.escape(n) for n in emulators)
    v = r"(/v1\.[0-9]+)?"
    q = r"(\?[^/]*)?"  # optional query (?tail=…, ?t=10) – no "/" so it can't smuggle in a second path
    svc: dict = {
        "image": "wollomatic/socket-proxy:1",
        "container_name": f"{box_name}-dockerproxy",
        "hostname": f"{box_name}-dockerproxy",
        "restart": "unless-stopped",
        "command": [
            f"-allowfrom={box_name}",
            "-listenip=0.0.0.0",
            f"-allowHEAD=^{v}/(_ping|containers/({names})/archive){q}$",
            f"-allowGET=^{v}/(_ping|version|containers/({names})/(json|logs|stats|top|archive)){q}$",
            f"-allowPOST=^{v}/containers/({names})/(start|stop|restart|kill){q}$",
        ],
        "volumes": [f"{host.docker_sock}:/var/run/docker.sock:ro"],
        "networks": ["ctl"],
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": "64m",
    }
    if host.docker_gid is not None:
        svc["user"] = f"65534:{host.docker_gid}"  # nobody + Docker-Gruppe statt root
    return svc


def write(stack: Stack, doc: dict) -> Path:
    y = YAML()
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 200
    buf = io.StringIO()
    y.dump(_quote(doc), buf)
    path = stack.compose_path
    path.write_text(f"# Generiert von agentbox aus {stack.cfg_path.name} – nicht von Hand ändern\n" + buf.getvalue())
    stack.host.chown(path, stack.cfg_path)
    return path
