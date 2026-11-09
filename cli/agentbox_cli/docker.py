"""Dünne Hülle um die docker-CLI."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


class DockerError(RuntimeError):
    pass


def run(*args: str, check: bool = True, capture: bool = False, quiet: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["docker", *args], check=check, text=True,
            stdout=subprocess.PIPE if capture else (subprocess.DEVNULL if quiet else None),
            stderr=subprocess.PIPE if capture else (subprocess.DEVNULL if quiet else None),
        )
    except FileNotFoundError as e:
        raise DockerError("docker-CLI nicht gefunden") from e
    except subprocess.CalledProcessError as e:
        if capture and e.stderr:
            sys.stderr.write(e.stderr)
        raise DockerError(f"docker {' '.join(args[:3])} … fehlgeschlagen") from e


def out(*args: str) -> str:
    r = run(*args, check=False, capture=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def logs(container: str, tail: int | None = None) -> str:
    """Container-Log inkl. stderr (die Box schreibt ihre Meldungen dorthin)."""
    args = ["docker", "logs"] + (["--tail", str(tail)] if tail else []) + [container]
    try:
        r = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except FileNotFoundError as e:
        raise DockerError("docker-CLI nicht gefunden") from e
    return r.stdout if r.returncode == 0 else ""


def exists(container: str) -> bool:
    return run("container", "inspect", container, check=False, quiet=True).returncode == 0


def compose_project(container: str) -> str:
    return out("container", "inspect", "-f", '{{ index .Config.Labels "com.docker.compose.project" }}', container)


def info() -> dict:
    raw = out("info", "--format", "{{json .}}")
    if not raw:
        raise DockerError("docker info fehlgeschlagen – läuft der Docker-Daemon, ist der Socket gemountet?")
    return json.loads(raw)


def mem_usage(container: str | None = None) -> list[str]:
    args = ["stats", "--no-stream", "--format", "{{.MemUsage}}"]
    if container:
        args.append(container)
    return [line.split(" / ")[0] for line in out(*args).splitlines() if line.strip()]


def compose(compose_file: Path, *args: str, check: bool = True) -> int:
    return run("compose", "-f", str(compose_file), *args, check=check).returncode


def exec_(container: str, *cmd: str, user: str = "dev", tty: bool = False, workdir: str | None = None,
          check: bool = True) -> int:
    args = ["exec"]
    if tty:
        args.append("-it")
    args += ["-u", user]
    if workdir:
        args += ["-w", workdir]
    return run(*args, container, *cmd, check=check).returncode
