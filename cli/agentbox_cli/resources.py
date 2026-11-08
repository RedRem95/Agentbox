"""RAM/CPU-Prüfung des Stacks gegen den Docker-Host."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import docker
from .config import is_linux
from .stack import Stack

GiB = 1024 ** 3
EMU_EST = 4 * GiB
EMU_MIN = 3 * GiB


def to_bytes(s: str | None) -> int:
    """512m, 4g, 4608m, 1.5GiB, 800MB, 1.2kB -> Bytes"""
    if not s:
        return 0
    m = re.match(r"^\s*([\d.]+)\s*([kmgtp]?)i?b?\s*$", str(s), re.I)
    if not m:
        raise ValueError(f"Ungültige Größe: {s}")
    mult = {"": 1, "k": 1024, "m": 1024 ** 2, "g": GiB, "t": 1024 ** 4, "p": 1024 ** 5}[m.group(2).lower()]
    return int(float(m.group(1)) * mult)


def fmt(b: int) -> str:
    return f"{b / GiB:.1f} GiB"


def box_estimate(stack: Stack) -> int:
    """1 GiB Basis + 0.75 GiB pro Agent + 2 GiB Gradle/.NET-Android + 1 GiB Desktop"""
    return GiB + stack.agent_count * 3 * GiB // 4 + (2 * GiB if stack.android else 0) + (GiB if stack.desktop else 0)


@dataclass
class HostRes:
    mem_total: int
    cpus: int
    mem_available: int

    @classmethod
    def query(cls, box_name: str | None = None) -> HostRes:
        info = docker.info()
        total = int(info.get("MemTotal", 0))
        cpus = int(info.get("NCPU", 1))
        avail = None
        # Im CLI-Container (und nativ unter Linux) zeigt /proc/meminfo den Docker-Host
        if is_linux() and not os.environ.get("DOCKER_HOST") and Path("/proc/meminfo").exists():
            m = re.search(r"^MemAvailable:\s+(\d+) kB", Path("/proc/meminfo").read_text(), re.M)
            if m:
                avail = int(m.group(1)) * 1024
        if avail is None:
            avail = total - sum(to_bytes(u) for u in docker.mem_usage())
        if box_name:  # Box wird neu erstellt -> ihr Verbrauch wird frei
            avail += sum(to_bytes(u) for u in docker.mem_usage(box_name))
        return cls(total, cpus, avail)


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def print(self) -> None:
        for line in self.lines:
            print(line)
        for w in self.warnings:
            print(f"WARN:   {w}")
        for e in self.errors:
            print(f"FEHLER: {e}")


def check(stack: Stack, host: HostRes, existing: set[str]) -> Report:
    r = Report(errors=list(stack.errors))
    est = box_estimate(stack)
    box_mem = to_bytes(stack.box_memory) if stack.box_memory else est
    if stack.box_memory and box_mem < est:
        r.warnings.append(f"Box-Limit {stack.box_memory} ist knapp: geschätzt {fmt(est)} "
                          f"für {stack.agent_count} Agent(en)"
                          + (" + Android-Builds" if stack.android else "") + (" + Desktop" if stack.desktop else ""))
    if stack.box_cpus and stack.box_cpus > host.cpus:
        r.errors.append(f"Box: cpus={stack.box_cpus:g} > {host.cpus} verfügbare CPUs")

    total, need, cpus = box_mem, box_mem, stack.box_cpus or 0.0
    r.lines.append(f"Ressourcen: Docker-Host {fmt(host.mem_total)} / {host.cpus} CPUs, frei ~{fmt(host.mem_available)}")
    r.lines.append(f"  {stack.name}: {stack.box_memory or f'kein Limit (Schätzung {fmt(est)})'}, "
                   f"cpus {f'{stack.box_cpus:g}' if stack.box_cpus else 'unbegrenzt'}")
    for e in stack.emulators:
        m = to_bytes(e.memory) if e.memory else EMU_EST
        if e.memory and m < EMU_MIN:
            r.warnings.append(f"Emulator {e.name}: memory={e.memory} ist zu wenig (mind. 3g, besser 4g)")
        if e.cpus:
            if e.cpus > host.cpus:
                r.errors.append(f"Emulator {e.name}: cpus={e.cpus:g} > {host.cpus} verfügbare CPUs")
            cpus += e.cpus
        total += m
        running = e.name in existing
        if not running:
            need += m
        r.lines.append(f"  {e.name}: {e.memory or f'kein Limit (Schätzung {fmt(EMU_EST)})'}, "
                       f"cpus {f'{e.cpus:g}' if e.cpus else 'unbegrenzt'}" + (" (läuft bereits)" if running else ""))

    r.lines.append(f"  gesamt: {fmt(total)}, {cpus:g} CPUs" if cpus else f"  gesamt: {fmt(total)}")
    if total > host.mem_total:
        r.errors.append(f"Stack braucht ~{fmt(total)}, Docker hat insgesamt nur {fmt(host.mem_total)}")
    elif total * 10 > host.mem_total * 9:
        r.warnings.append(f"Stack braucht ~{fmt(total)} von {fmt(host.mem_total)} – kaum Luft für Host/andere Container")
    if need > host.mem_available:
        r.warnings.append(f"Zum Starten werden ~{fmt(need)} gebraucht, frei sind aktuell ~{fmt(host.mem_available)}")
    if cpus > host.cpus:
        r.warnings.append(f"Summe der CPU-Limits ({cpus:g}) > {host.cpus} CPUs – Container bremsen sich gegenseitig")
    return r
