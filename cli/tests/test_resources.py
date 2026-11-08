import pytest

from agentbox_cli import config as C
from agentbox_cli.resources import GiB, HostRes, box_estimate, check, to_bytes
from agentbox_cli.stack import Stack


@pytest.mark.parametrize("raw,expected", [
    ("4g", 4 * GiB),
    ("4608m", 4608 * 1024 ** 2),
    ("1.5GiB", int(1.5 * GiB)),
    ("800MB", 800 * 1024 ** 2),
    ("512", 512),
    (None, 0),
])
def test_to_bytes(raw, expected):
    assert to_bytes(raw) == expected


def test_to_bytes_rejects_garbage():
    with pytest.raises(ValueError):
        to_bytes("viel")


def _stack(tmp_path, text):
    p = tmp_path / "agentbox.yaml"
    p.write_text(text)
    host = C.Host(home=tmp_path, uid=1000, gid=1000, kvm=True, sudo_uid=None, sudo_gid=None)
    return Stack.from_config(p, host)


SCENARIO = """
layout: root
agents: [claude]
android: {jdk: 21}
container:
  name: box
  resources: {memory: 11g, cpus: 2}
  android_emulators:
    - {name: box-phone, memory: 4608m, cpus: 1.5}
    - {name: box-tablet, memory: 4608m, cpus: 1.5}
"""


def test_scenario_fits_on_32g_host(tmp_path):
    rep = check(_stack(tmp_path, SCENARIO), HostRes(32 * GiB, 8, 30 * GiB), existing=set())
    assert not rep.errors
    assert not rep.warnings
    assert any("20.0 GiB, 5 CPUs" in line for line in rep.lines)


def test_too_much_ram_is_an_error(tmp_path):
    rep = check(_stack(tmp_path, SCENARIO), HostRes(16 * GiB, 8, 15 * GiB), existing=set())
    assert any("insgesamt nur" in e for e in rep.errors)


def test_running_emulators_do_not_count_as_new_demand(tmp_path):
    stack = _stack(tmp_path, SCENARIO)
    host = HostRes(32 * GiB, 8, 12 * GiB)  # nur 12 GiB frei
    assert any("Zum Starten" in w for w in check(stack, host, existing=set()).warnings)
    assert not any("Zum Starten" in w for w in check(stack, host, existing={"box-phone", "box-tablet"}).warnings)


def test_small_limits_warn(tmp_path):
    stack = _stack(tmp_path, SCENARIO.replace("11g", "2g").replace("4608m", "2g", 1))
    rep = check(stack, HostRes(32 * GiB, 8, 30 * GiB), existing=set())
    assert any("Box-Limit 2g ist knapp" in w for w in rep.warnings)
    assert any("box-phone" in w and "zu wenig" in w for w in rep.warnings)


def test_cpu_over_host_is_error(tmp_path):
    rep = check(_stack(tmp_path, SCENARIO.replace("cpus: 2}", "cpus: 16}")), HostRes(32 * GiB, 8, 30 * GiB), set())
    assert any("cpus=16" in e for e in rep.errors)


def test_box_estimate_counts_agents_android_desktop(tmp_path):
    stack = _stack(tmp_path, "agents: [claude, codex]\nrepos: [a, b]\nandroid: {}\ndesktop: {enabled: true}\n")
    assert stack.agent_count == 4  # per-project: 2 Repos x 2 Agenten
    assert box_estimate(stack) == GiB + 3 * GiB + 2 * GiB + GiB
