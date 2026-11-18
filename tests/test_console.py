"""Exercise console settings against isolated protocol and media endpoints."""

import base64
from contextlib import contextmanager
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "panel"))

from service_console import SETTINGS_FILE, ServiceConsole, address
from settings import load_config
from tests.gateway_fixture import SyntheticStack, synthetic_voice, until
from tests.support import IsolatedPanel


@contextmanager
def sample_server(replace_gateway=None):
    prompt_id = "c2e85710-6f47-4634-ad26-1379f39d7627"
    voice = synthetic_voice()
    buffer = io.BytesIO()
    Image.new("RGB", (128, 96), (124, 40, 90)).save(buffer, format="PNG")
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def result(self, data, mime="application/json", status=200):
            if not isinstance(data, bytes):
                data = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                self.result({"ok": True, "model_loaded": True, "private": "not-for-console"})
            elif self.path == "/system_stats":
                self.result({"system": {}, "devices": []})
            elif self.path == "/object_info/CheckpointLoaderSimple":
                self.result({"CheckpointLoaderSimple": {"input": {"required": {
                    "ckpt_name": [["test-checkpoint.safetensors"]]}}}})
            elif self.path == "/history/" + prompt_id:
                self.result({prompt_id: {"outputs": {"7": {
                    "images": [{"filename": "preview.png", "type": "temp", "subfolder": ""}]}}}})
            elif self.path == "/view?filename=preview.png&type=temp":
                self.result(buffer.getvalue(), "image/png")
            else:
                self.result({"ok": False}, status=404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            if self.path == "/replace-gateway" and replace_gateway is not None:
                self.result(replace_gateway())
            elif self.path == "/tts/audio":
                self.result(voice, "audio/wav")
            elif self.path == "/prompt":
                self.result({"prompt_id": prompt_id})
            else:
                self.result({"ok": False}, status=404)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, voice
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


class ConsoleTests(unittest.TestCase):
    def test_addresses_are_bounded_to_supported_transports(self):
        self.assertEqual(address("http://127.0.0.1:1234/", local=True, root=True), "http://127.0.0.1:1234")
        self.assertEqual(address("https://example.invalid/gateway/", https=True), "https://example.invalid/gateway")
        for value in ("http://localhost:5678", "http://192.0.2.10:22", "https://127.0.0.1",
                      "http://127.0.0.1:0", "http://127.0.0.1/user", "http://127.0.0.1?token=x",
                      "http://127.0.0.1%2frouter", "http://127.0.0.1/#a", "http://user@127.0.0.1",
                      "http://127.0.0.1/%0d", "http://127.0.0.1\\example.invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                address(value, local=True, root=True)

    def test_connect_save_restart_and_reject_failed_changes(self):
        stack = SyntheticStack()
        self.addCleanup(stack.close)
        with IsolatedPanel(ROOT) as fixture:
            panel = fixture.panel
            panel.初始化媒体库()
            api = panel.Api(load_config(data_dir=fixture.root))
            try:
                self.assertEqual(api.chat_history()["总"], 0)
                self.assertEqual(api.console_view()["billing"]["phase"], "unavailable")
                endpoint = {"mode": "dsh_local", "base_url": stack.base, "token": stack.token}
                self.assertTrue(api.console_chat(endpoint, "test")["ok"])
                self.assertEqual(api.config["mode"], "demo")
                self.assertFalse((fixture.root / SETTINGS_FILE).exists())
                incorrect = "synthetic-incorrect"
                wrong = api.console_chat({**endpoint, "token": incorrect}, "save")
                self.assertFalse(wrong["ok"])
                saved = api.console_chat(endpoint, "save")
                self.assertTrue(saved["ok"], saved)
                self.assertTrue(saved["chat"]["has_token"])
                self.assertNotIn(stack.token, json.dumps(api.console_view()))
                self.assertNotIn(stack.base, json.dumps(api.console_diagnostics()))
                sent = api.chat_send("synthetic-console-sample")
                self.assertTrue(sent["ok"], sent)
                until(lambda: api.chat_history()["总"] >= 2)
                history = json.dumps(api.chat_history(), ensure_ascii=False)
                self.assertIn("synthetic reply: synthetic-console-sample", history)
            finally:
                api.close()
            restarted = panel.Api(load_config(data_dir=fixture.root))
            try:
                restarted.start()
                until(lambda: restarted.chat_ready()["connected"])
                self.assertEqual(restarted.chat_history()["总"], 2)
                old = restarted.adapter
                self.assertFalse(restarted.console_chat(
                    {**endpoint, "base_url": "http://127.0.0.1:1", "token": None}, "save")["ok"])
                self.assertIs(restarted.adapter, old)
                self.assertFalse(restarted.console_chat(
                    {**endpoint, "token": incorrect}, "save")["ok"])
                self.assertIs(restarted.adapter, old)
                self.assertTrue(restarted.console_chat({"mode": "demo"}, "save")["ok"])
                self.assertFalse(restarted.chat_retry(sent["message_id"]).get("ok"))
                self.assertEqual(restarted.chat_history()["总"], 2)
                self.assertTrue(restarted.console_chat(endpoint, "save")["ok"])
                self.assertEqual(restarted.adapter.endpoint_id, old.endpoint_id)
                until(lambda: restarted.adapter._resume_from >= 1)
                self.assertEqual(restarted.chat_history()["总"], 2)
            finally:
                restarted.close()

    def test_samples_save_only_checked_loopback_and_return_real_bytes(self):
        with sample_server() as (base, requests, voice), tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = ServiceConsole(root=root)
            config = {"enabled": True, "base_url": base}
            self.assertFalse(api.configure_chat({"mode": "demo"}, save=True)["ok"])
            self.assertFalse(api.configure_bridge(base)["ok"])
            self.assertFalse(api.configure_service("voice", {**config, "base_url": "http://example.invalid"}, True)["ok"])
            self.assertTrue(api.configure_service("voice", config)["ok"])
            self.assertFalse(api.sample("voice", "first try")["ok"])
            self.assertEqual(requests, [])
            for kind in ("voice", "image"):
                self.assertTrue(api.configure_service(kind, config, True)["ok"])
            self.assertFalse(api.sample("voice", "a" * 81)["ok"])
            generated = api.sample("voice", "试听")
            self.assertEqual(base64.b64decode(generated["media"]), voice)
            image = api.sample("image")
            with Image.open(io.BytesIO(base64.b64decode(image["media"]))) as result:
                self.assertEqual(result.getpixel((0, 0)), (124, 40, 90))
            with IsolatedPanel(ROOT) as fixture:
                fixture.panel.初始化媒体库()
                gallery = fixture.panel.Api()
                self.assertEqual(gallery.gallery_list()["总"], 0)
                self.assertFalse(api.save_image("unissued", gallery.gallery_add)["ok"])
                saved = api.save_image(image["preview_id"], gallery.gallery_add)
                self.assertTrue(saved["ok"], saved)
                self.assertFalse(saved["duplicate"])
                self.assertTrue(api.save_image(image["preview_id"], gallery.gallery_add)["duplicate"])
                self.assertEqual(gallery.gallery_list()["总"], 1)
            self.assertEqual([call[0] for call in requests], ["/tts/audio", "/prompt"])
            self.assertEqual(requests[0][1], {"text": "试听"})
            self.assertEqual(set(node["class_type"] for node in requests[1][1]["prompt"].values()), {
                "CheckpointLoaderSimple", "CLIPTextEncode", "EmptyLatentImage", "KSampler", "VAEDecode",
                "PreviewImage"})
            rebuilt = ServiceConsole(root=root)
            self.assertTrue(rebuilt.service_options()["service_options"]["image"]["enabled"])
            self.assertTrue(rebuilt.configure_service("image", {**config, "enabled": False}, save=True)["ok"])
            self.assertFalse(rebuilt.sample("image")["ok"])
            self.assertFalse((root / "archive").exists())

    def test_console_rpc_auth_and_size_limits(self):
        with IsolatedPanel(ROOT) as fixture:
            panel = fixture.panel
            api = panel.Api(load_config(data_dir=fixture.root))
            port = panel.起界面服务(api, 0)
            base = "http://127.0.0.1:" + str(port)
            try:
                with urlopen(base + "/api/bootstrap", timeout=5) as response:
                    token = json.load(response)["session_token"]
                for headers, payload, code in (
                    ({}, b"[]", 403),
                    ({"X-Panel-Token": token, "Origin": "https://example.invalid"}, b"[]", 403),
                    ({"X-Panel-Token": token}, b"x" * 8193, 413),
                ):
                    with self.assertRaises(HTTPError) as denied:
                        urlopen(Request(base + "/api/console_view", data=payload, headers=headers), timeout=5)
                    self.assertEqual(denied.exception.code, code)
                    denied.exception.close()
                with urlopen(Request(base + "/api/console_view", data=b"[]",
                                     headers={"X-Panel-Token": token}), timeout=5) as response:
                    state = json.load(response)
                self.assertTrue(state["editable"])
                self.assertEqual(Path(state["data_path"]), fixture.root)
            finally:
                api.close()
                panel._server.shutdown()
                panel._server.server_close()


if __name__ == "__main__":
    if "--browser" not in sys.argv:
        unittest.main(verbosity=2)
    else:
        stack = SyntheticStack()
        try:
            def replace_gateway():
                config = json.loads((stack.root / "gateway-config.json").read_text(encoding="utf-8"))
                config["data_dir"] = tempfile.mkdtemp(prefix="gateway-replacement-", dir=stack.root)
                stack.write("gateway-config", config)
                stack.restart("gateway")
                return stack.request("/v1/handshake", {"protocol_version": "dsh-client/1"})

            with sample_server(replace_gateway) as (base, _, _):
                env = {**os.environ, "PYTHON": sys.executable,
                       "SYNTHETIC_GATEWAY_URL": stack.base, "SYNTHETIC_GATEWAY_TOKEN": stack.token,
                       "SYNTHETIC_MEDIA_URL": base}
                result = subprocess.run([shutil.which("node"), str(ROOT / "tests/console-browser.cjs")],
                                        cwd=ROOT, env=env)
                raise SystemExit(result.returncode)
        finally:
            stack.close()
