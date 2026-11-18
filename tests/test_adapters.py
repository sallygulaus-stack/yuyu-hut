"""Exercise optional model and generation services using loopback test servers."""
import base64
import http.server
import io
import json
from pathlib import Path
import secrets
import sys
import threading
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "panel"))
from PIL import Image
from tests.support import IsolatedPanel
from settings import default_config


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = IsolatedPanel(PROJECT)
        self.addCleanup(self.fixture.close)
        self.panel = self.fixture.panel
        self.panel.初始化媒体库()
        self.received = []
        self.secret = secrets.token_urlsafe(24)
        test = self
        output = io.BytesIO()
        Image.new("RGB", (100, 100), "red").save(output, "PNG")
        self.image = output.getvalue()

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                test.received.append((self.path, body, self.headers.get("Authorization")))
                if self.path == "/v1/chat/completions":
                    result = {"choices": [{"message": {"content": "模拟模型回复"}}]}
                else:
                    result = {"prompt_id": "test-job"}
                raw = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                test.received.append((self.path, None, self.headers.get("Authorization")))
                if self.path.startswith("/view?"):
                    raw = test.image
                else:
                    raw = json.dumps({"test-job": {
                        "outputs": {"1": {"images": [{"filename": "generated.png", "type": "output"}]}},
                        "status": {"completed": True},
                    }}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.config = default_config()
        self.config["mode"] = "model"
        self.config["model"].update(
            enabled=True, base_url=self.base_url + "/v1", model="test-model", api_key=self.secret)
        self.config["comfyui"].update(enabled=True, base_url=self.base_url, token=self.secret)
        self.api = self.panel.Api(self.config)

    def test_model_sends_only_current_text_and_persists_reply(self):
        self.panel.记一条("我", [{"type": "text", "text": "不应发送的旧记录"}])
        self.assertTrue(self.api.chat_send("本次输入")["ok"])
        route, body, authorization = self.received[0]
        self.assertEqual(route, "/v1/chat/completions")
        self.assertEqual(body["messages"], [{"role": "user", "content": "本次输入"}])
        self.assertEqual(authorization, "Bearer " + self.secret)
        self.assertEqual(self.api.chat_history()["总"], 3)
        self.assertFalse(self.api.chat_send_image(base64.b64encode(self.image).decode())["ok"])
        self.assertEqual(len(self.received), 1)

    def test_generation_collects_media_that_survives_adapter_shutdown(self):
        self.assertEqual(self.api.comfy_submit({"1": {"class_type": "Example"}})["prompt_id"], "test-job")
        result = self.api.comfy_collect("test-job")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["完成"])
        media_id = result["作品"][0]["媒体ID"]
        self.assertTrue(all(row[2] == "Bearer " + self.secret for row in self.received))
        self.api.comfy.config["enabled"] = False
        self.assertFalse(self.api.comfy_submit({"1": {}})["ok"])
        self.assertTrue(self.panel.媒体库.resolve_path(media_id).is_file())
        self.assertEqual(self.api.gallery_list()["总"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
