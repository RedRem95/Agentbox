"""Config laden/speichern (Kommentare bleiben erhalten) und Host-Infos."""
from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 120
    return y


def load(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return _yaml().load(f) or {}


def save(data: Any, path: Path, header: str = "") -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        if header:
            f.write(header.rstrip() + "\n\n")
        _yaml().dump(data, f)
    tmp.replace(path)


def get(d: Any, dotted: str, default: Any = None) -> Any:
    """Verschachtelter Zugriff; None/fehlend -> default, False bleibt False."""
    cur = d
    for key in dotted.split("."):
        if isinstance(cur, dict) and key in cur and cur[key] is not None:
            cur = cur[key]
        else:
            return default
    return cur


def as_list(v: Any) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return list(v)


@dataclass
class Host:
    """Sicht auf den Docker-Host. Im CLI-Container kommen die Werte vom Wrapper (HOST_*)."""

    home: Path
    uid: int
    gid: int
    kvm: bool
    sudo_uid: int | None
    sudo_gid: int | None
    kvm_gid: int | None = None  # Gruppe von /dev/kvm auf dem Host (Emulatoren brauchen sie)

    @classmethod
    def detect(cls) -> Host:
        env = os.environ
        uid = int(env.get("HOST_UID", os.getuid()))
        gid = int(env.get("HOST_GID", os.getgid()))
        home = Path(env.get("HOST_HOME", str(Path.home())))
        kvm = env.get("HOST_KVM", "1" if Path("/dev/kvm").exists() else "0") == "1"
        su = env.get("SUDO_UID")
        sg = env.get("SUDO_GID")
        kg = env.get("HOST_KVM_GID", "")
        if not kg and Path("/dev/kvm").exists():
            kg = str(Path("/dev/kvm").stat().st_gid)
        return cls(home, uid, gid, kvm, int(su) if su else None, int(sg) if sg else None,
                   int(kg) if kg.isdigit() else None)

    def expand(self, p: str) -> str:
        if p == "~" or p.startswith("~/"):
            return str(self.home) + p[1:]
        return p

    def owner(self, ref: Path | None = None) -> tuple[int, int]:
        """UID/GID für dev in der Box und für erzeugte Dateien – nie 0."""
        if self.uid != 0:
            return self.uid, self.gid
        if self.sudo_uid:
            return self.sudo_uid, self.sudo_gid or self.sudo_uid
        if ref is not None and ref.exists():
            st = ref.stat()
            if st.st_uid != 0:
                return st.st_uid, st.st_gid
        return 1000, 1000

    def chown(self, path: Path, ref: Path | None = None) -> None:
        """Als root im CLI-Container erzeugte Dateien dem Host-User geben."""
        if os.getuid() != 0:
            return
        uid, gid = self.owner(ref)
        try:
            os.chown(path, uid, gid)
        except OSError:
            pass


def is_linux() -> bool:
    return platform.system() == "Linux"
