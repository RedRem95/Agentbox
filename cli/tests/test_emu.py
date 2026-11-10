"""emu (image/emu.sh) against a fake Docker API: the right paths, by name, and readable output."""
import http.server
import os
import struct
import subprocess
import threading
from pathlib import Path

import pytest

EMU = Path(__file__).resolve().parents[2] / "image" / "emu.sh"


def frame(stream: int, text: str) -> bytes:
    data = text.encode()
    return struct.pack(">BxxxI", stream, len(data)) + data


class FakeDocker(http.server.BaseHTTPRequestHandler):
    requests: list[str] = []
    body = frame(1, "2026-11-11T07:00:00Z emulator: boot completed\n") + frame(2, "2026-11-11T07:00:01Z ERROR: crash\n")

    def do_GET(self):  # noqa: N802
        FakeDocker.requests.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.docker.multiplexed-stream")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


@pytest.fixture
def docker_api(tmp_path):
    srv = http.server.HTTPServer(("127.0.0.1", 0), FakeDocker)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    FakeDocker.requests = []
    emus = tmp_path / "emulators"
    emus.write_text("travelbox-phone\ntravelbox-tablet\n")
    env = dict(os.environ, DOCKER_HOST=f"tcp://127.0.0.1:{srv.server_port}", EMU_LIST=str(emus))
    yield env
    srv.shutdown()


def test_logs_by_name_and_demuxed(docker_api):
    out = subprocess.run(["bash", str(EMU), "logs", "phone", "50"], env=docker_api,
                         capture_output=True, text=True, check=True).stdout
    assert out == "2026-11-11T07:00:00Z emulator: boot completed\n2026-11-11T07:00:01Z ERROR: crash\n"
    path = FakeDocker.requests[0]
    assert path.startswith("/containers/travelbox-phone/logs?")  # name, not the container ID
    assert "tail=50" in path and "stderr=1" in path


def test_logs_raw_stream_passthrough(docker_api):
    FakeDocker.body = b"plain tty output\n"
    try:
        out = subprocess.run(["bash", str(EMU), "logs", "tablet"], env=docker_api,
                             capture_output=True, text=True, check=True).stdout
    finally:
        FakeDocker.body = frame(1, "x\n")
    assert out == "plain tty output\n"


def test_unknown_emulator(docker_api):
    r = subprocess.run(["bash", str(EMU), "logs", "watch"], env=docker_api, capture_output=True, text=True)
    assert r.returncode != 0 and "unknown emulator" in r.stderr


def test_help_without_docker_access():
    r = subprocess.run(["bash", str(EMU), "--help"], env=dict(os.environ, DOCKER_HOST=""),
                       capture_output=True, text=True)
    assert r.returncode == 0 and "usage: emu" in r.stdout
