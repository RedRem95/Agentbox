import importlib.metadata

import pytest

from agentbox_cli import config as C
from agentbox_cli import default_image, envfile
from agentbox_cli.main import build_parser


@pytest.mark.parametrize("argv,expected", [
    (["up"], "agentbox.yaml"),
    (["-c", "x.yaml", "up"], "x.yaml"),   # argparse-Falle: Subparser darf -c nicht überschreiben
    (["up", "-c", "y.yaml"], "y.yaml"),
])
def test_config_option(argv, expected, monkeypatch):
    monkeypatch.delenv("AGENTBOX_CONFIG", raising=False)
    assert build_parser().parse_args(argv).config == expected


def test_default_image(monkeypatch):
    monkeypatch.delenv("AGENTBOX_IMAGE", raising=False)
    assert default_image().startswith("ghcr.io/redrem95/agentbox:")
    monkeypatch.setenv("AGENTBOX_IMAGE", "agentbox:local")
    assert default_image() == "agentbox:local"


def test_envfile_roundtrip(tmp_path):
    env = tmp_path / ".env"
    envfile.set_var(env, "A", "eins$zwei")
    envfile.set_var(env, "B", "x")
    envfile.set_var(env, "A", "neu")
    assert env.read_text() == "B='x'\nA='neu'\n"
    assert envfile.has(env, "A") and not envfile.has(env, "C")
    assert (env.stat().st_mode & 0o777) == 0o600
    with pytest.raises(ValueError):
        envfile.set_var(env, "A", "it's")


def test_host_owner_never_root(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("")
    h = C.Host(home=tmp_path, uid=0, gid=0, kvm=False, sudo_uid=None, sudo_gid=None)
    assert h.owner(None) == (1000, 1000)
    h.sudo_uid, h.sudo_gid = 1001, 1001
    assert h.owner(cfg) == (1001, 1001)


def test_get_keeps_false():
    d = {"a": {"b": False, "c": None}}
    assert C.get(d, "a.b", True) is False
    assert C.get(d, "a.c", "x") == "x"
    assert C.get(d, "a.zz.y", 1) == 1


def test_version_single_source():
    """pyproject.toml reads the version from __version__ (PEP 440 normalises 0.2.0-rc.1 to 0.2.0rc1)."""
    from packaging.version import Version

    from agentbox_cli import __version__
    assert Version(importlib.metadata.version("agentbox")) == Version(__version__)
