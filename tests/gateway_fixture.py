"""A real three-process test chain, confined to a temporary loopback directory."""
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave

PROJECT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def until(check, seconds=12):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            last = check()
            if last:
                return last
        except Exception as exc:
            last = exc
        time.sleep(.05)
    raise AssertionError("Test condition timed out: " + str(last))


def synthetic_voice():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        # Audible synthetic tone, no voice recording.
        import math
        import struct
        wav.writeframes(b"".join(struct.pack("<h", int(2000 * math.sin(i * .35))) for i in range(1600)))
    return output.getvalue()


class SyntheticStack:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="onebot-gateway-test-")
        self.root = Path(self.temp.name)
        self.processes = {}
        self.logs = []
        self.token = secrets.token_urlsafe(32)
        self.ob_port, self.ws_port, self.port = free_port(), free_port(), free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        config = {
            "data_dir": str(self.root / "gateway"), "port": self.port, "token": self.token,
            "onebot_url": f"http://127.0.0.1:{self.ob_port}", "test_controls": True,
            "synthetic_profiles": {"peer": {"display_name": "Synthetic Companion",
                                           "signature": "Synthetic fixture", "role": "test-framework",
                                           "avatar": "assets/her.svg"},
                                   "me": {"display_name": "Synthetic User", "avatar": "assets/me.svg"}},
        }
        self.write("gateway-config", config)
        self.write("onebot-config", {"httpPort": self.ob_port, "wsPort": self.ws_port,
                   "windowUrl": self.base + "/say", "token": self.token,
                   "selfName": "Synthetic Robot", "peerName": "Synthetic Human",
                   "selfId": 20000, "peerId": 20001})
        self.write("mock-config", {"ws_url": f"ws://127.0.0.1:{self.ws_port}", "token": self.token})
        self.commands = {
            "onebot": [shutil.which("node"), str(PROJECT / "server/onebot-local.mjs"),
                       "--config", str(self.root / "onebot-config.json"),
                       "--data-dir", str(self.root / "onebot")],
            "gateway": [sys.executable, "-B", str(PROJECT / "server/gateway.py"),
                        "--config", str(self.root / "gateway-config.json")],
            "mock": [shutil.which("node"), str(PROJECT / "tests/mock-framework.mjs"),
                     str(self.root / "mock-config.json")],
        }
        try:
            self.start("onebot")
            until(lambda: self.request("/__state", onebot=True))
            self.start("gateway")
            until(lambda: self.request("/v1/handshake", {"protocol_version": "dsh-client/1"}))
            self.start("mock")
            until(lambda: self.request("/__state", onebot=True)["connections"] == 1)
        except Exception:
            self.close()
            raise

    def write(self, name, value):
        (self.root / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")

    def start(self, name):
        handle = (self.root / (name + ".output")).open("ab")
        self.logs.append(handle)
        self.processes[name] = subprocess.Popen(
            self.commands[name], cwd=self.root, stdout=handle, stderr=handle,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})

    def stop_process(self, name):
        proc = self.processes.get(name)
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)

    def restart(self, name):
        self.stop_process(name)
        self.start(name)
        if name == "gateway":
            until(lambda: self.request("/v1/handshake", {"protocol_version": "dsh-client/1"}))
        if name == "onebot":
            until(lambda: self.request("/__state", onebot=True)["connections"] == 1)

    def request(self, route, body=None, onebot=False, token=None):
        req = urllib.request.Request(
            (f"http://127.0.0.1:{self.ob_port}" if onebot else self.base) + route,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + (self.token if token is None else token),
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)

    def panel_config(self):
        from settings import default_config
        config = default_config()
        config["mode"] = "dsh_local"
        config["dsh_local"].update(enabled=True, base_url=self.base, token=self.token,
                                   endpoint_id="test-gateway", poll_seconds=.1,
                                   heartbeat_seconds=.2, reconnect_seconds=.2, timeout=2)
        return config

    def close(self):
        for name in list(self.processes):
            self.stop_process(name)
        for handle in self.logs:
            handle.close()
        self.temp.cleanup()
