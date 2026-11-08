"""env_file lesen/schreiben (Secrets, nie in der Config)."""
from __future__ import annotations

import getpass
import os
from pathlib import Path


def has(path: Path, name: str) -> bool:
    if not path.exists():
        return False
    return any(line.startswith(f"{name}=") for line in path.read_text().splitlines())


def set_var(path: Path, name: str, value: str) -> None:
    """Setzt/ersetzt NAME='value'. In '…' ersetzt Compose keine $-Variablen."""
    if "'" in value:
        raise ValueError("Wert darf kein ' enthalten")
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [line for line in lines if not line.startswith(f"{name}=")]
    lines.append(f"{name}='{value}'")
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def prompt_secret(label: str, min_len: int = 8, confirm: bool = True) -> str:
    while True:
        p1 = getpass.getpass(f"{label} (mind. {min_len} Zeichen): ")
        if len(p1) < min_len:
            print("Zu kurz.")
            continue
        if "'" in p1:
            print("Bitte ohne ' (Hochkomma).")
            continue
        if confirm and getpass.getpass("Wiederholen: ") != p1:
            print("Stimmt nicht überein.")
            continue
        return p1
