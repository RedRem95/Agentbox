import os
import stat

from agentbox_cli.main import main

FAKE_DOCKER = """#!/bin/sh
case "$1" in
  logs)
    # the box writes its messages to stderr
    echo "[agentbox] Starte Desktop (DISPLAY=:0, RustDesk=1)" >&2
    echo "[agentbox] RustDesk-ID: ${FAKE_LOG_ID:-unbekannt (siehe ~/.agentbox-desktop.log)}" >&2
    echo "[agentbox] WARN: something to look at" >&2 ;;
  container) exit 0 ;;
  exec) echo "${FAKE_LIVE_ID:-}" ;;
  *) exit 0 ;;
esac
"""

CFG = """
desktop: {enabled: true}
container: {name: box}
"""


def _setup(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("HOST_HOME", str(tmp_path))
    monkeypatch.setenv("HOST_KVM", "0")
    cfg = tmp_path / "agentbox.yaml"
    cfg.write_text(CFG)
    return cfg


def test_status_shows_id_and_warnings_from_stderr(tmp_path, monkeypatch, capsys):
    cfg = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_LOG_ID", "123456789")
    assert main(["-c", str(cfg), "status"]) == 0
    out = capsys.readouterr().out
    assert "RustDesk:   ID 123456789" in out
    assert "WARN: something to look at" in out


def test_status_asks_rustdesk_live_when_log_has_no_id(tmp_path, monkeypatch, capsys):
    cfg = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_LIVE_ID", "987 654 321")
    main(["-c", str(cfg), "status"])
    assert "RustDesk:   ID 987654321" in capsys.readouterr().out


def test_status_points_to_desktop_log_without_id(tmp_path, monkeypatch, capsys):
    cfg = _setup(tmp_path, monkeypatch)
    main(["-c", str(cfg), "status"])
    out = capsys.readouterr().out
    assert "noch keine" in out and ".agentbox-desktop.log" in out
