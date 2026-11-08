"""Modell des Stacks (Box + Emulatoren), abgeleitet aus der Config."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config as C
from . import default_image

DEFAULT_EMU_IMAGE = "budtmo/docker-android:emulator_14.0"
DEFAULT_EMU_DEVICE = "Samsung Galaxy S10"


@dataclass
class Emulator:
    name: str
    image: str = DEFAULT_EMU_IMAGE
    device: str = DEFAULT_EMU_DEVICE
    vnc_port: int | None = None
    memory: str | None = None
    cpus: float | None = None
    env: dict = field(default_factory=dict)


@dataclass
class Stack:
    cfg: Any
    cfg_path: Path
    host: C.Host
    name: str
    image: str
    ssh_port: int
    ssh_bind: str
    src: str
    env_file: Path | None
    network: str
    box_memory: str | None
    box_cpus: float | None
    emulators: list[Emulator]
    desktop: bool
    rustdesk: bool
    rd_direct: bool
    rd_port: int
    rd_bind: str
    agent_count: int
    android: bool
    errors: list[str] = field(default_factory=list)

    @property
    def compose_path(self) -> Path:
        return self.cfg_path.parent / f"{self.name}.compose.yml"

    @property
    def env_file_or_default(self) -> Path:
        return self.env_file or (self.cfg_path.parent / ".env")

    @classmethod
    def from_config(cls, cfg_path: Path, host: C.Host) -> Stack:
        cfg_path = cfg_path.resolve()
        cfg = C.load(cfg_path)
        g = lambda k, d=None: C.get(cfg, k, d)  # noqa: E731

        name = str(g("container.name", "agentbox"))
        env_file = g("container.env_file", "")
        env_path = None
        if env_file:
            p = Path(host.expand(str(env_file)))
            env_path = p if p.is_absolute() else (cfg_path.parent / p)

        errors: list[str] = []
        emus: list[Emulator] = []
        for i, e in enumerate(C.as_list(g("container.android_emulators"))):
            if isinstance(e, str):
                e = {"name": e}
            if not e.get("name"):
                errors.append(f"android_emulators[{i}] braucht einen name")
                continue
            emus.append(Emulator(
                name=str(e["name"]),
                image=str(e.get("image") or DEFAULT_EMU_IMAGE),
                device=str(e.get("device") or DEFAULT_EMU_DEVICE),
                vnc_port=int(e["vnc_port"]) if e.get("vnc_port") else None,
                memory=str(e["memory"]) if e.get("memory") else None,
                cpus=float(e["cpus"]) if e.get("cpus") else None,
                env={str(k): str(v) for k, v in (e.get("env") or {}).items()},
            ))

        default_agents = C.as_list(g("agents"))
        if g("layout", "per-project") == "root":
            agent_count = len(default_agents)
        else:
            agent_count = 0
            for r in C.as_list(g("repos")):
                if isinstance(r, dict) and "agents" in r:
                    agent_count += len(C.as_list(r["agents"]))
                else:
                    agent_count += len(default_agents)

        desktop = g("desktop.enabled", False) is True
        rustdesk = desktop and g("desktop.rustdesk.enabled", True) is not False
        rd_direct = rustdesk and g("desktop.rustdesk.direct.enabled", False) is True

        box_cpus = g("container.resources.cpus")
        return cls(
            cfg=cfg, cfg_path=cfg_path, host=host, name=name,
            image=str(g("container.image", "") or default_image()),
            ssh_port=int(g("container.ssh_port", 2222)),
            ssh_bind=str(g("container.ssh_bind", "")),
            src=str(g("container.src", "")),
            env_file=env_path,
            network=str(g("container.network", "") or f"{name}-net"),
            box_memory=str(g("container.resources.memory")) if g("container.resources.memory") else None,
            box_cpus=float(box_cpus) if box_cpus else None,
            emulators=emus,
            desktop=desktop, rustdesk=rustdesk, rd_direct=rd_direct,
            rd_port=int(g("desktop.rustdesk.direct.port", 21118)),
            rd_bind=str(g("desktop.rustdesk.direct.bind", "")),
            agent_count=agent_count,
            # wie jq im Entrypoint: aktiv, sobald der Schlüssel existiert (auch "android: {}")
            android=(isinstance(cfg, dict) and cfg.get("android") not in (None, False)) or bool(emus),
            errors=errors,
        )
