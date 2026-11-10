import re

import pytest

from agentbox_cli import compose
from agentbox_cli import config as C

HOST = C.Host(home=None, uid=1000, gid=1000, kvm=True, sudo_uid=None, sudo_gid=None, docker_gid=998)
EMUS = ["travelbox-phone", "travelbox-tablet"]


def _rules():
    cmd = compose.docker_proxy("travelbox", EMUS, HOST)["command"]
    rules = {}
    for c in cmd:
        m = re.match(r"-allow(GET|POST|HEAD)=(.*)", c)
        if m:
            rules[m.group(1)] = re.compile(m.group(2))
    return rules


def _allowed(method, uri):
    """Must hold whether the proxy matches the bare path or the path including the query."""
    r = _rules().get(method)
    if not r:
        return False
    path_only, full = bool(r.match(uri.split("?")[0])), bool(r.match(uri))
    assert path_only == full or "?" not in uri, f"path/query disagree for {uri}"
    return full


@pytest.mark.parametrize("method,path", [
    ("HEAD", "/_ping"),
    ("GET", "/_ping"),
    ("GET", "/v1.47/version"),
    ("GET", "/v1.47/containers/travelbox-phone/json"),                     # docker inspect
    ("GET", "/v1.47/containers/travelbox-phone/logs?stdout=1&tail=200"),   # docker logs
    ("GET", "/v1.47/containers/travelbox-tablet/stats?stream=false"),
    ("GET", "/v1.47/containers/travelbox-tablet/top"),
    ("HEAD", "/v1.47/containers/travelbox-phone/archive?path=%2Fvar%2Flog"),  # docker cp (Go encodes /)
    ("GET", "/v1.47/containers/travelbox-phone/archive?path=%2Fvar%2Flog"),
    ("GET", "/containers/travelbox-phone/logs?stdout=1&stderr=1&timestamps=1&tail=300"),  # emu logs
    ("POST", "/v1.47/containers/travelbox-phone/restart?t=10"),
    ("POST", "/v1.47/containers/travelbox-tablet/stop"),
    ("POST", "/v1.47/containers/travelbox-tablet/start"),
])
def test_allowed(method, path):
    assert _allowed(method, path)


@pytest.mark.parametrize("method,path", [
    ("POST", "/v1.47/containers/travelbox-phone/exec"),           # exec: bewusst nicht
    ("POST", "/v1.47/exec/abc/start"),
    ("POST", "/v1.47/containers/create"),
    ("POST", "/v1.47/containers/travelbox/restart"),               # die Box selbst
    ("POST", "/v1.47/containers/travelbox-dockerproxy/stop"),      # der Proxy selbst
    ("POST", "/v1.47/containers/0123456789ab/restart"),            # andere Container per ID
    ("GET", "/v1.47/containers/other/logs"),
    ("GET", "/v1.47/containers/json"),                             # Liste aller Container
    ("GET", "/v1.47/containers/travelbox-phone-x/json"),           # Präfix-Trick
    ("GET", "/v1.47/containers/travelbox-phone/json/../../other/json"),
    ("GET", "/v1.47/containers/0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef/logs?tail=10"),
    ("PUT", "/v1.47/containers/travelbox-phone/archive"),          # Dateien hineinschreiben
    ("DELETE", "/v1.47/containers/travelbox-phone"),
    ("POST", "/v1.47/containers/travelbox-phone/update"),
    ("GET", "/v1.47/images/json"),
    ("POST", "/v1.47/build"),
])
def test_denied(method, path):
    assert not _allowed(method, path)


def test_proxy_runs_unprivileged():
    svc = compose.docker_proxy("travelbox", EMUS, HOST)
    assert svc["user"] == "65534:998"
    assert svc["read_only"] and svc["cap_drop"] == ["ALL"]
    assert svc["volumes"] == ["/var/run/docker.sock:/var/run/docker.sock:ro"]
    assert "-allowfrom=travelbox" in svc["command"]
