"""Compose-Datei aus dem Stack erzeugen."""
from __future__ import annotations

import io
import os
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString as Q

from . import config as C
from .stack import Stack


def _limits(svc: dict, memory: str | None, cpus: float | None) -> None:
    if memory:
        svc["mem_limit"] = Q(memory)
        svc["memswap_limit"] = Q(memory)  # Swap = Limit -> hartes RAM-Limit
    if cpus:
        svc["cpus"] = int(cpus) if float(cpus).is_integer() else cpus


def _quote(doc: dict) -> dict:
    """Listen-Strings (Ports "2222:22", Env, Volumes) immer quoten – YAML-1.1-Parser lesen 2222:22 sonst als Zahl."""
    for svc in doc["services"].values():
        for key in ("ports", "environment", "volumes", "devices", "extra_hosts"):
            if key in svc:
                svc[key] = [Q(v) if isinstance(v, str) else v for v in svc[key]]
    return doc


def build(stack: Stack, existing: set[str]) -> tuple[dict, list[str], list[str]]:
    """-> (compose-dict, Emulator-Services, Hinweise)"""
    notes: list[str] = []
    host = stack.host
    uid, gid = host.owner(stack.cfg_path)
    services: dict = {}
    volumes = [f"{stack.name}-home", f"{stack.name}-tools", f"{stack.name}-sshd"]
    emu_services: list[str] = []

    for e in stack.emulators:
        if not host.kvm and e.name not in existing:
            notes.append(f"/dev/kvm fehlt – Emulator {e.name} wird ausgelassen")
            continue
        svc: dict = {
            "image": e.image, "container_name": e.name, "hostname": e.name,
            "restart": "unless-stopped", "devices": ["/dev/kvm"], "networks": ["stack"],
            "environment": [f"EMULATOR_DEVICE={e.device}"]
            + (["WEB_VNC=true"] if e.vnc_port else [])
            + [f"{k}={v}" for k, v in e.env.items()],
            "volumes": [f"{e.name}-data:/home/androidusr"],
        }
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

    doc = {
        "name": stack.name,
        "services": services,
        "networks": {"stack": {"name": stack.network}},
        "volumes": {v: {"name": v} for v in volumes},
    }
    return doc, emu_services, notes


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
