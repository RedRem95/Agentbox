import io

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
