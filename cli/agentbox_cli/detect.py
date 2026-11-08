"""Toolchain-Erkennung aus Repo-Inhalten (Projektdateien + CI-Workflows)."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "bin", "obj", "build", "dist", "target", ".venv", "venv", "artifacts", ".gradle"}
# .NET-Version -> Android-API, die der android-Workload als Ziel nutzt
NET_ANDROID_API = {8: 34, 9: 35, 10: 36, 11: 37}


def _walk(root: Path, max_depth: int = 5):
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if len(Path(dirpath).parts) - root_depth >= max_depth:
            dirnames[:] = []
        for f in filenames:
            yield Path(dirpath) / f


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _vkey(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", v))


@dataclass
class Detected:
    name: str = ""
    dotnet_versions: set[str] = field(default_factory=set)
    dotnet_majors: set[int] = field(default_factory=set)
    workloads: set[str] = field(default_factory=set)
    android: bool = False
    sdk_packages: set[str] = field(default_factory=set)
    jdk: int | None = None
    python: str | None = None
    python_pkgs: set[str] = field(default_factory=set)
    node: bool = False
    tools: set[str] = field(default_factory=set)
    apt: set[str] = field(default_factory=set)
    gui: bool = False
    setup: list[str] = field(default_factory=list)
    siblings: set[str] = field(default_factory=set)
    agent_docs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def merge(self, o: Detected) -> None:
        for attr in ("dotnet_versions", "dotnet_majors", "workloads", "sdk_packages", "python_pkgs", "tools", "apt"):
            getattr(self, attr).update(getattr(o, attr))
        self.android |= o.android
        self.node |= o.node
        self.gui |= o.gui
        if o.jdk and (not self.jdk or o.jdk > self.jdk):
            self.jdk = o.jdk
        if o.python and (not self.python or _vkey(o.python) > _vkey(self.python)):
            self.python = o.python
        self.notes += [f"{o.name}: {n}" for n in o.notes]

    @property
    def dotnet_version(self) -> str | None:
        exact = [v for v in self.dotnet_versions if re.fullmatch(r"\d+\.\d+\.\d+", v)]
        return max(exact, key=_vkey) if exact else None

    def summary(self) -> list[str]:
        s = []
        if self.dotnet_version or self.dotnet_majors:
            s.append(".NET SDK " + (self.dotnet_version or f"{max(self.dotnet_majors)}.0 (Channel)")
                     + (f" + Workloads {', '.join(sorted(self.workloads))}" if self.workloads else ""))
        if self.jdk:
            s.append(f"JDK {self.jdk}")
        if self.android:
            s.append("Android SDK: " + ", ".join(sorted(self.sdk_packages)))
        if self.python:
            s.append(f"Python {self.python}" + (f" + {', '.join(sorted(self.python_pkgs))}" if self.python_pkgs else ""))
        for t in sorted(self.tools - {"dotnet", "python", "uv"}):
            s.append(t)
        if self.node:
            s.append("Node (im Image: 22)")
        if self.apt:
            s.append("apt: " + ", ".join(sorted(self.apt)))
        return s


def scan(root: Path, name: str) -> Detected:
    d = Detected(name=name)
    files = list(_walk(root))
    by_name: dict[str, list[Path]] = {}
    for f in files:
        by_name.setdefault(f.name.lower(), []).append(f)

    # ---- .NET
    for f in by_name.get("global.json", []) + [root / "eng" / "sdk.json"]:
        m = re.search(r'"version"\s*:\s*"([\d.]+[\w.\-]*)"', _read(f))
        if m:
            d.dotnet_versions.add(m.group(1))
    projs = [f for f in files if f.suffix in (".csproj", ".fsproj", ".vbproj")]
    for p in projs:
        txt = _read(p)
        for tfm in re.findall(r"<TargetFrameworks?>([^<]+)</TargetFrameworks?>", txt):
            for t in tfm.split(";"):
                t = t.strip()
                m = re.match(r"net(\d+)\.\d+(?:-(\w+))?", t)
                if not m:
                    continue
                major, plat = int(m.group(1)), m.group(2)
                d.dotnet_majors.add(major)
                if plat == "android":
                    d.android = True
                    d.workloads.add("android")
                    api = NET_ANDROID_API.get(major)
                    if api:
                        d.sdk_packages |= {f"platforms;android-{api}", f"build-tools;{api}.0.0"}
                elif plat in ("ios", "maccatalyst"):
                    d.notes.append(f"{p.name}: {plat} lässt sich unter Linux nicht bauen")
        if re.search(r"<UseMaui>\s*true", txt, re.I):
            d.workloads.add("maui-android")
        if re.search(r'Include="Avalonia', txt):
            d.gui = True
            d.apt |= {"libfontconfig1", "libice6", "libsm6", "fonts-dejavu-core"}
        for ref in re.findall(r'<ProjectReference\s+Include="([^"]+)"', txt):
            target = (p.parent / ref.replace("\\", "/")).resolve()
            try:
                target.relative_to(root.resolve())
            except ValueError:
                rel = os.path.relpath(target, root.resolve().parent).split(os.sep)
                if rel and rel[0] not in ("..", ""):
                    d.siblings.add(rel[0])
    if d.dotnet_versions or d.dotnet_majors:
        d.tools.add("dotnet")
    sln = sorted([f for f in root.iterdir() if f.suffix in (".sln", ".slnx")], key=lambda f: f.name)
    if sln:
        d.setup.append(f"dotnet restore {sln[0].name}")

    # ---- Gradle/Android
    for f in [f for f in files if f.name in ("build.gradle", "build.gradle.kts")]:
        txt = _read(f)
        if re.search(r"com\.android\.(application|library)", txt):
            d.android = True
            m = re.search(r"compileSdk(?:Version)?\s*[=(]?\s*(\d+)", txt)
            if m:
                api = int(m.group(1))
                d.sdk_packages |= {f"platforms;android-{api}", f"build-tools;{api}.0.0"}
            d.jdk = d.jdk or 17
    if (root / "gradlew").exists():
        d.setup.append("./gradlew --version >/dev/null")

    # ---- CI-Workflows: genaueste Quelle für Versionen
    wf = root / ".github" / "workflows"
    if wf.is_dir():
        for f in sorted(wf.glob("*.y*ml")):
            txt = _read(f)
            for m in re.finditer(r"java-version:\s*['\"]?(\d+)", txt):
                d.jdk = max(d.jdk or 0, int(m.group(1)))
            for m in re.finditer(r"python-version:\s*['\"]?(\d+\.\d+)", txt):
                if not d.python or _vkey(m.group(1)) > _vkey(d.python):
                    d.python = m.group(1)
            for m in re.finditer(r"dotnet-version:\s*['\"]?(\d+\.\d+\.\d+)", txt):
                d.dotnet_versions.add(m.group(1))
            if re.search(r"go-version:\s*['\"]?\d", txt):
                d.tools.add("go")
            # env-Variablen des Workflows (z.B. NDK_VERSION: 28.2…) für $VAR in sdkmanager-Aufrufen
            wenv = dict(re.findall(r"^\s+([A-Z][A-Z0-9_]*):\s*['\"]?([\w.\-]+)['\"]?\s*$", txt, re.M))
            for m in re.finditer(r"((?:ndk|cmake|build-tools|platforms);[\w.\-${}]+)", txt):
                pkg = re.sub(r"\$\{?(\w+)\}?", lambda v, e=wenv: e.get(v.group(1), v.group(0)), m.group(1))
                if "$" not in pkg:
                    d.sdk_packages.add(pkg)
                    d.android = True
            for m in re.finditer(r"dotnet workload install ([\w\- ]+)", txt):
                d.workloads |= {w for w in m.group(1).split() if not w.startswith("-")}
            for m in re.finditer(r"pip install ([^\n#&|;]+)", txt):
                for tok in m.group(1).split():
                    if tok.startswith("-") or tok in (".", "pip") or "/" in tok or "$" in tok:
                        if tok in ("-r", "-e"):
                            break
                        continue
                    d.python_pkgs.add(tok.strip("'\""))
            for m in re.finditer(r"apt-get install ([^\n#&|;]+)", txt):
                d.apt |= {t for t in m.group(1).split() if not t.startswith("-") and "$" not in t}
            if re.search(r"(build-libgit2|ndk-build|ANDROID_NDK)", txt):
                d.notes.append(f"{f.name}: baut native Teile (NDK) – evtl. eigenes 'setup' nötig")

    # ---- Python
    pyproj = root / "pyproject.toml"
    if pyproj.exists() or (root / "uv.lock").exists():
        d.tools |= {"python", "uv"}
        m = re.search(r'requires-python\s*=\s*["\'][^\d]*(\d+\.\d+)', _read(pyproj))
        if m and (not d.python or _vkey(m.group(1)) > _vkey(d.python)):
            d.python = m.group(1)
        if (root / "uv.lock").exists():
            d.setup.append("uv sync")
    if (root / "requirements.txt").exists():
        d.tools |= {"python", "uv"}
        d.setup.append("uv pip install --python /opt/tools/venv/bin/python -r requirements.txt")
    if d.python or d.python_pkgs:
        d.tools.add("python")
        d.python = d.python or "3.12"

    # ---- Node/Bun/Rust/Go
    if (root / "package.json").exists():
        d.node = True
        if (root / "pnpm-lock.yaml").exists():
            d.setup.append("corepack enable --install-directory /opt/tools/bin pnpm && pnpm install")
        elif (root / "yarn.lock").exists():
            d.setup.append("corepack enable --install-directory /opt/tools/bin yarn && yarn install")
        elif (root / "bun.lockb").exists() or (root / "bun.lock").exists():
            d.tools.add("bun")
            d.setup.append("bun install")
        else:
            d.setup.append("npm ci" if (root / "package-lock.json").exists() else "npm install")
    if (root / "Cargo.toml").exists():
        d.tools.add("rust")
        d.setup.append("cargo fetch")
    if (root / "go.mod").exists():
        d.tools.add("go")
        d.setup.append("go mod download")

    if d.android:
        d.sdk_packages.add("platform-tools")
        d.jdk = d.jdk or 17
    for doc in ("AGENTS.md", "CLAUDE.md"):
        if (root / doc).exists():
            d.agent_docs.append(doc)
    return d


def repo_name(url: str) -> str:
    n = url.rstrip("/").rsplit("/", 1)[-1]
    n = n.rsplit(":", 1)[-1]
    return n[:-4] if n.endswith(".git") else n


def clone(url: str, dest: Path) -> tuple[bool, str]:
    """Flacher Clone zur Analyse. SSH nutzt den Key/Agent des Hosts (Home ist gemountet)."""
    env = dict(os.environ)
    home = os.environ.get("HOST_HOME")
    ssh = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={tempfile.gettempdir()}/kh"]
    if home:
        for k in ("id_ed25519", "id_ecdsa", "id_rsa"):
            if (Path(home) / ".ssh" / k).exists():
                ssh += ["-i", str(Path(home) / ".ssh" / k)]
    env["GIT_SSH_COMMAND"] = " ".join(ssh)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    r = subprocess.run(["git", "clone", "-q", "--depth", "1", url, str(dest)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    return r.returncode == 0, r.stderr.strip()
