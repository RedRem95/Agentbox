import io

import pytest
from ruamel.yaml import YAML

from agentbox_cli import compose
from agentbox_cli import config as C
from agentbox_cli.stack import Stack

CFG = """
agents: claude
repos:
  - git@github.com:org/a.git
  - url: https://github.com/org/b.git
    agents: [claude, codex]
desktop:
  enabled: true
  rustdesk:
    direct: {enabled: true, bind: 192.168.1.10}
github: {config_dir: ~/.agentbox/gh}
container:
  name: box
  image: example/agentbox:1.2.3
  ssh_bind: 127.0.0.1
  resources: {memory: 8g, cpus: 4}
  mounts:
    - ~/exists:/home/dev/exists:ro
    - ~/missing:/home/dev/missing
  android_emulators:
    - {name: box-phone, vnc_port: 6080, memory: 4g, cpus: 2, env: {A: 1}}
"""


def _build(tmp_path, kvm=True, existing=frozenset(), cfg=CFG, kvm_gid=103):
    (tmp_path / "exists").mkdir()
    p = tmp_path / "agentbox.yaml"
    p.write_text(cfg)
    host = C.Host(home=tmp_path, uid=1234, gid=1234, kvm=kvm, sudo_uid=None, sudo_gid=None, kvm_gid=kvm_gid)
    stack = Stack.from_config(p, host)
    return stack, *compose.build(stack, set(existing))


def test_box_service(tmp_path):
    stack, doc, emus, notes = _build(tmp_path)
    box = doc["services"]["agentbox"]
    assert box["image"] == "example/agentbox:1.2.3"
    assert box["ports"] == ["127.0.0.1:2222:22", "192.168.1.10:21118:21118"]
    assert box["mem_limit"] == box["memswap_limit"] == "8g"
    assert box["cpus"] == 4
    assert "HOST_UID=1234" in box["environment"]
    assert box["env_file"][0]["path"].endswith("/.env")  # RustDesk an -> .env immer
    vols = box["volumes"]
    assert "box-home:/home/dev" in vols and "box-src:/src" in vols
    assert f"{tmp_path}/exists:/home/dev/exists:ro" in vols
    assert not any("missing" in v for v in vols)
    assert any("missing" in n for n in notes)
    assert f"{tmp_path}/.agentbox/gh:/home/dev/.config/gh" in vols
    assert (tmp_path / ".agentbox" / "gh").is_dir()


def test_emulator_service(tmp_path):
    _, doc, emus, _ = _build(tmp_path)
    assert emus == ["box-phone"]
    emu = doc["services"]["box-phone"]
    assert emu["devices"] == ["/dev/kvm"]
    assert emu["ports"] == ["127.0.0.1:6080:6080"]
    assert "A=1" in emu["environment"] and "WEB_VNC=true" in emu["environment"]
    assert doc["volumes"]["box-phone-data"] == {"name": "box-phone-data"}
    assert doc["networks"]["stack"]["name"] == "box-net"


def test_emulator_skipped_without_kvm(tmp_path):
    _, doc, emus, notes = _build(tmp_path, kvm=False)
    assert emus == [] and "box-phone" not in doc["services"]
    assert any("/dev/kvm" in n for n in notes)


def test_existing_emulator_kept_without_kvm(tmp_path):
    _, doc, emus, _ = _build(tmp_path, kvm=False, existing={"box-phone"})
    assert emus == ["box-phone"]


def test_written_file_quotes_ports(tmp_path):
    stack, doc, _, _ = _build(tmp_path)
    text = compose.write(stack, doc).read_text()
    assert '- "127.0.0.1:2222:22"' in text  # sonst lesen YAML-1.1-Parser 2222:22 als Zahl
    reread = YAML(typ="safe").load(io.StringIO(text))
    assert reread["services"]["agentbox"]["ports"][0] == "127.0.0.1:2222:22"


def test_agent_count_per_project(tmp_path):
    stack, *_ = _build(tmp_path)
    assert stack.agent_count == 3  # a: claude, b: claude+codex


def test_emulator_gets_kvm_group_of_the_host(tmp_path):
    _, doc, _, _ = _build(tmp_path)
    assert doc["services"]["box-phone"]["group_add"] == ["103"]
    assert "group_add" not in doc["services"]["agentbox"]


def test_kvm_group_from_config_wins(tmp_path):
    cfg = CFG.replace("  name: box\n", "  name: box\n  kvm_gid: 108\n")
    _, doc, _, _ = _build(tmp_path, cfg=cfg)
    assert doc["services"]["box-phone"]["group_add"] == ["108"]


def test_no_group_add_when_unknown(tmp_path):
    _, doc, _, _ = _build(tmp_path, kvm_gid=None)
    assert "group_add" not in doc["services"]["box-phone"]


def test_docker_control_adds_restricted_proxy(tmp_path):
    cfg = CFG.replace("container:\n", "android: {docker_control: true}\ncontainer:\n")
    stack, doc, side, _ = _build(tmp_path, cfg=cfg)
    proxy = doc["services"]["box-dockerproxy"]
    assert side == ["box-phone", "box-dockerproxy"]  # wird vor der Box gestartet
    assert any("containers/(box\\-phone)/" in c for c in proxy["command"])
    box = doc["services"]["agentbox"]
    assert "DOCKER_HOST=tcp://box-dockerproxy:2375" in box["environment"]
    assert box["networks"] == ["stack", "ctl"] and proxy["networks"] == ["ctl"]
    assert doc["networks"]["ctl"] == {"name": "box-ctl"}
    assert "ctl" not in doc["services"]["box-phone"]["networks"]  # Emulator sieht den Proxy nicht


def test_no_proxy_without_flag_or_emulators(tmp_path):
    _, doc, side, _ = _build(tmp_path)
    assert "box-dockerproxy" not in doc["services"] and side == ["box-phone"]
    cfg = CFG.replace("container:\n", "android: {docker_control: true}\ncontainer:\n")
    (tmp_path / "x").mkdir()
    _, doc, side, _ = _build(tmp_path / "x", cfg=cfg, kvm=False)  # Emulator ausgelassen -> kein Proxy
    assert "box-dockerproxy" not in doc["services"] and side == []


@pytest.mark.parametrize("emu,expected", [
    ({"cpus": 3}, "-cores 3"),
    ({"cpus": 1.5}, "-cores 2"),                  # aufrunden: 2 vCPUs teilen sich 1.5 CPUs Quota
    ({"cpus": 0.5}, "-cores 1"),
    ({}, None),                                    # kein Limit -> nicht anfassen
    ({"cpus": 3, "cores": 4}, "-cores 4"),         # explizit
    ({"cpus": 3, "cores": "auto"}, "-cores 3"),
    ({"cpus": 3, "cores": 0}, None),               # 0 = AVD-Wert lassen
    ({"cpus": 3, "env": {"EMULATOR_ADDITIONAL_ARGS": "-memory 3072"}}, "-memory 3072 -cores 3"),
    ({"cpus": 3, "env": {"EMULATOR_ADDITIONAL_ARGS": "-cores 2 -gpu off"}}, "-cores 2 -gpu off"),
])
def test_emulator_cores(emu, expected):
    from agentbox_cli.stack import Emulator
    e = Emulator(name="x", cpus=emu.get("cpus"), env=emu.get("env", {}),
                 cores=None if emu.get("cores") in (None, "auto") else int(emu["cores"]))
    assert compose.emulator_env(e).get("EMULATOR_ADDITIONAL_ARGS") == expected


def test_cores_in_generated_service(tmp_path):
    _, doc, _, _ = _build(tmp_path)  # box-phone hat cpus: 2
    assert "EMULATOR_ADDITIONAL_ARGS=-cores 2" in doc["services"]["box-phone"]["environment"]


def test_docker_android_gets_restartable_start(tmp_path):
    from agentbox_cli.compose import budtmo_start
    _, doc, _, _ = _build(tmp_path)
    emu = doc["services"]["box-phone"]
    assert emu["user"] == "0:0"
    assert emu["entrypoint"] == ["/bin/sh", "-c", budtmo_start(103)]
    assert "--groups=$$G,103" in emu["entrypoint"][2]   # $$ = $ für Compose


def test_restart_fix_only_for_docker_android_and_can_be_disabled(tmp_path):
    cfg = CFG.replace("{name: box-phone,", "{name: box-phone, image: example/other:1,")
    _, doc, _, _ = _build(tmp_path, cfg=cfg)
    assert "entrypoint" not in doc["services"]["box-phone"]
    (tmp_path / "off").mkdir()
    cfg = CFG.replace("{name: box-phone,", "{name: box-phone, restart_fix: false,")
    _, doc, _, _ = _build(tmp_path / "off", cfg=cfg)
    assert "entrypoint" not in doc["services"]["box-phone"] and "user" not in doc["services"]["box-phone"]


def test_budtmo_start_survives_repeated_starts(tmp_path):
    """Simulated: our prelude, then docker-android deletes line 1 of /etc/passwd – three times."""
    import subprocess

    from agentbox_cli.compose import budtmo_start
    passwd = tmp_path / "passwd"
    passwd.write_text("root:x:0:0:root:/root:/bin/bash\ndaemon:x:1:1::/:/bin/false\n"
                      "androidusr:x:1300:1301::/home/androidusr:/bin/bash\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "setpriv").write_text(f'#!/bin/sh\necho "$*" > {tmp_path}/setpriv\n')
    (bin_dir / "id").write_text('#!/bin/sh\ncase "$1" in -u) echo 1300;; -g) echo 1301;; -G) echo "1301 27";; esac\n')
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    script = budtmo_start(103).replace("$$", "$").replace("/etc/passwd", str(passwd))
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "APP_PATH": "/app"}
    for _ in range(3):
        subprocess.run(["sh", "-c", script], env=env, check=True)
        lines = passwd.read_text().splitlines()
        assert lines[0].startswith("root:")
        passwd.write_text("\n".join(lines[1:]) + "\n")  # what docker-android does on every start
        assert [line.split(":")[0] for line in passwd.read_text().splitlines()] == ["daemon", "androidusr"]
    assert (tmp_path / "setpriv").read_text().split()[:3] == ["--reuid=1300", "--regid=1301", "--groups=1301,27,103"]


def test_written_file_quotes_user(tmp_path):
    cfg = CFG.replace("container:\n", "android: {docker_control: true}\ncontainer:\n")
    stack, doc, _, _ = _build(tmp_path, cfg=cfg)
    text = compose.write(stack, doc).read_text()
    assert 'user: "0:0"' in text
    reread = YAML(typ="safe", pure=True).load(io.StringIO(text))
    assert reread["services"]["box-phone"]["user"] == "0:0"
