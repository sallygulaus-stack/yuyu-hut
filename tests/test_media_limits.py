"""Daily attachments remain bounded and preserve their original content."""
import base64
import hashlib
import io
import json
from pathlib import Path
import random
import sys
import unittest
import wave

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / "panel"), str(PROJECT)]
from connectors.remote import DshLocalConnector, MemoryRemoteTransport
from media_limits import DEFAULT_LIMITS
from tests.gateway_fixture import SyntheticStack, until
from tests.support import IsolatedPanel


def image_bytes():
    from PIL import Image
    output = io.BytesIO()
    Image.frombytes("RGB", (640, 480), random.Random(17).randbytes(640 * 480 * 3)).save(output, "PNG")
    return output.getvalue()


class MediaLimitsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = IsolatedPanel(PROJECT)
        self.addCleanup(self.fixture.close)
        self.fixture.panel.初始化媒体库()
        self.api = self.fixture.panel.Api()
        self.addCleanup(self.api.close)

    def test_demo_sends_daily_original_and_preserves_bytes(self):
        raw = image_bytes()
        self.assertGreater(len(raw), 512 * 1024)
        result = self.api.chat_send_image(base64.b64encode(raw).decode(), "daily.png")
        self.assertTrue(result["ok"], result)
        segment = self.api.chat_history()["消息"][0]["段"][0]
        stored = self.fixture.panel.媒体库.resolve_path(segment["媒体ID"])
        self.assertEqual(stored.read_bytes(), raw)
        self.assertEqual(self.api.media_limits()["limits"], DEFAULT_LIMITS)

    def test_old_gateway_falls_back_to_its_legacy_size(self):
        transport = MemoryRemoteTransport()
        self.api.adapter = DshLocalConnector({"enabled": True, "endpoint_id": "legacy"}, transport)
        self.assertEqual(self.api.media_limits()["limits"]["image"], 512 * 1024)
        result = self.api.chat_send_image(base64.b64encode(image_bytes()).decode(), "daily.png")
        self.assertFalse(result["ok"])
        self.assertIn("512 KiB", result["error"])
        self.assertEqual(transport.received, [])
        self.assertTrue(self.api.chat_send("text remains available")["ok"])

    def test_explicit_chat_upload_restores_original_after_compressed_archive(self):
        raw = image_bytes()
        archive = self.fixture.panel.媒体库
        old = archive.ingest_bytes(raw, filename="daily.png", source="test")
        ref = archive.add_reference(old["media_id"], kind="workshop", collection="gallery")
        self.assertNotEqual(archive.resolve_path(old["media_id"]).read_bytes(), raw)
        result = self.api.chat_send_image(base64.b64encode(raw).decode(), "daily.png")
        self.assertTrue(result["ok"], result)
        self.assertEqual(archive.resolve_path(old["media_id"]).read_bytes(), raw)
        self.assertTrue(ref)

    def test_audio_limit_accepts_exact_size_and_rejects_one_more_byte(self):
        self.api.config["media_limits"] = {"image": 1024, "record": 1024}
        self.assertTrue(self.api.chat_send_record(base64.b64encode(b"x" * 1024).decode(), "tone.wav")["ok"])
        self.assertFalse(self.api.chat_send_record(base64.b64encode(b"x" * 1025).decode(), "tone.wav")["ok"])
        self.assertTrue(self.api.chat_send("after refusal")["ok"])

    def test_daily_image_and_audio_roundtrip_are_byte_identical(self):
        stack = SyntheticStack()
        self.addCleanup(stack.close)
        self.api.close()
        self.api = self.fixture.panel.Api(stack.panel_config())
        self.addCleanup(self.api.close)
        self.api.start()
        raw_image = image_bytes()
        voice = io.BytesIO()
        with wave.open(voice, "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(random.Random(18).randbytes(4 * 1024 * 1024))
        raw_voice = voice.getvalue()
        for kind, raw, name in (("image", raw_image, "daily.png"), ("record", raw_voice, "long.wav")):
            send = self.api.chat_send_image if kind == "image" else self.api.chat_send_record
            result = send(base64.b64encode(raw).decode(), name)
            self.assertTrue(result["ok"], result)
            def received():
                return [segment for row in self.api.chat_history(20)["消息"] if row["谁"] == "她"
                        for segment in row["段"] if segment.get("type") == kind]
            segment = until(received, 20)[-1]
            path = self.fixture.panel.媒体库.resolve_path(segment["媒体ID"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), hashlib.sha256(raw).digest())
        self.assertGreater(stack.request("/__test/stats")["media"], 1)

    def test_configured_remote_limit_refuses_only_oversized_attachment(self):
        stack = SyntheticStack()
        self.addCleanup(stack.close)
        config = json.loads((stack.root / "gateway-config.json").read_text())
        config["media_limits"] = {"image": 512 * 1024, "record": 1024 * 1024}
        stack.write("gateway-config", config)
        stack.restart("gateway")
        self.api.close()
        self.api = self.fixture.panel.Api(stack.panel_config())
        self.addCleanup(self.api.close)
        self.api.start()
        until(lambda: self.api.chat_ready()["connected"])
        self.assertEqual(self.api.media_limits()["limits"], config["media_limits"])
        self.assertFalse(self.api.chat_send_image(base64.b64encode(image_bytes()).decode(), "daily.png")["ok"])
        self.assertTrue(self.api.chat_send("still chatting")["ok"])
        until(lambda: any(r["谁"] == "她" for r in self.api.chat_history()["消息"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
