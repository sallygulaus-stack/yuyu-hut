"""Isolated tests for the protocol-neutral client core and connectors."""

from __future__ import annotations

import copy
import base64
import io
from pathlib import Path
import sys
import unittest
import wave

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "panel"))
sys.path.insert(0, str(PROJECT))

from connectors.remote import DshLocalConnector, MemoryRemoteTransport
from connectors.demo import DemoConnector
from core.dedupe import EventDeduper
from core.models import CAPABILITIES, ConnectionStatus, UnifiedMessage
from profiles import ProfileService
from settings import default_config, load_config
from tests.support import IsolatedPanel


class CoreTests(unittest.TestCase):
    def test_unified_message_contains_required_stable_fields(self):
        message = UnifiedMessage(
            conversation_id="main", message_id="msg-1", event_id="evt-1", sequence=7,
            sender={"profile_id": "peer", "name": "合成对方"}, created_at=1.0,
            delivery_status="delivered", segments=[{"type": "text", "text": "你好"}],
        ).to_dict()
        for field in (
            "conversation_id", "message_id", "event_id", "sequence", "sender",
            "created_at", "delivery_status", "segments", "attachments",
            "capabilities", "protocol_version",
        ):
            self.assertIn(field, message)
        self.assertTrue(message["capabilities"]["media_ids"])

    def test_deduper_is_bounded_and_rejects_event_or_message_duplicates(self):
        dedupe = EventDeduper(max_size=2, ttl=60)
        self.assertFalse(dedupe.seen("evt-1", "msg-1"))
        self.assertTrue(dedupe.seen("evt-1", "msg-other"))
        self.assertTrue(dedupe.seen("", "msg-1"))
        self.assertFalse(dedupe.seen("evt-2", "msg-2"))
        self.assertFalse(dedupe.seen("evt-3", "msg-3"))
        self.assertLessEqual(len(dedupe._items), 2)

    def test_profile_source_priority_is_dsh_over_onebot_over_local(self):
        config = default_config()
        config["profiles"] = {
            "local": {"peer": {"display_name": "本地默认", "signature": "本地签名"}},
            "onebot": {"peer": {"display_name": "桥接昵称", "signature": "桥接签名"}},
            "dsh": {"peer": {"display_name": "DSH 角色", "signature": "角色签名", "role": "女儿"}},
        }
        payload = ProfileService(config).peer_profile({"phase": "connected", "connected": True})
        self.assertEqual(payload["profile"]["display_name"], "DSH 角色")
        self.assertEqual(payload["profile"]["signature"], "角色签名")
        self.assertEqual(payload["profile"]["role"], "女儿")
        self.assertNotIn("token", str(payload).lower())
        self.assertNotIn("base_url", str(payload).lower())

    def test_remote_simulator_handshake_resume_dedupe_and_ack(self):
        transport = MemoryRemoteTransport()
        connector = DshLocalConnector({"enabled": True, "endpoint_id": "test-local"}, transport)
        state = connector.connect()
        self.assertEqual(state["phase"], "connected")
        self.assertTrue(state["connected"])
        sent = connector.send("来自测试", segments=[{"type": "text", "text": "来自测试"}])
        self.assertEqual(sent["delivery_status"], "gateway_accepted")
        event = {
            "event_id": "evt-remote-1", "message_id": "msg-remote-1", "sequence": 4,
            "conversation_id": "main", "segments": [{"type": "text", "text": "远端"}],
            "protocol_version": "dsh-client/1",
        }
        transport.push(event)
        transport.push(copy.deepcopy(event))
        events = connector.poll()
        self.assertEqual(len(events), 2)
        self.assertEqual(transport.acks, [])
        self.assertEqual(connector._resume_from, 0)
        self.assertEqual(connector.ack(events[0]), 4)
        self.assertTrue(connector.heartbeat()["ok"])
        self.assertTrue(connector.reconnect()["connected"])

    def test_remote_disconnect_reports_error_without_retrying_production(self):
        transport = MemoryRemoteTransport()
        connector = DshLocalConnector({"enabled": True}, transport)
        self.assertTrue(connector.connect()["connected"])
        transport.close()
        with self.assertRaises(Exception):
            connector.connect()
        state = connector.status()
        self.assertEqual(state["phase"], "error")
        self.assertFalse(state["connected"])
        self.assertTrue(state["error"])

    def test_panel_receive_dedupes_replayed_event_and_persists_unified_fields(self):
        fixture = IsolatedPanel(PROJECT)
        self.addCleanup(fixture.close)
        fixture.panel.初始化媒体库()
        api = fixture.panel.Api()
        first = api.receive({
            "text": "一次", "event_id": "evt-dup", "message_id": "msg-dup",
            "conversation_id": "main", "endpoint_id": "test",
        })
        second = api.receive({
            "text": "一次", "event_id": "evt-dup", "message_id": "msg-dup",
            "conversation_id": "main", "endpoint_id": "test",
        })
        self.assertTrue(first["ok"])
        self.assertTrue(second["duplicate"])
        page = api.chat_history(20)
        self.assertEqual(page["总"], 1)
        record = page["消息"][0]
        for field in ("消息ID", "事件ID", "sequence", "conversation_id", "profile_id",
                      "sender", "attachments", "endpoint_id", "delivery_status", "protocol_version"):
            self.assertIn(field, record)
        self.assertEqual(record["事件ID"], "evt-dup")

    def test_panel_receive_archives_synthetic_voice_by_media_id(self):
        fixture = IsolatedPanel(PROJECT)
        self.addCleanup(fixture.close)
        fixture.panel.初始化媒体库()
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b"\x00\x00" * 800)
        api = fixture.panel.Api()
        result = api.receive({
            "event_id": "evt-voice", "message_id": "msg-voice", "text": "听一下",
            "段": [{"type": "text", "text": "听一下"}, {
                "type": "record", "mime": "audio/wav", "name": "test.wav",
                "b64": base64.b64encode(output.getvalue()).decode("ascii"),
            }],
        })
        self.assertTrue(result["ok"], result)
        record = api.chat_history(10)["消息"][0]
        voice = next(segment for segment in record["段"] if segment.get("type") == "record")
        self.assertTrue(voice.get("媒体ID", "").startswith("m_"))
        self.assertTrue(voice.get("url", "").startswith("/media/m_"))

    def test_unknown_delivery_retains_stable_record_and_blocks_unsafe_retry(self):
        fixture = IsolatedPanel(PROJECT)
        self.addCleanup(fixture.close)
        fixture.panel.初始化媒体库()
        api = fixture.panel.Api()

        class Broken:
            def send(self, *_args, **_kwargs):
                raise RuntimeError("simulated disconnect")

        api.adapter = Broken()
        failed = api.chat_send("第一次失败")
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["delivery_status"], "unknown")
        self.assertEqual(api.chat_history(20)["总"], 1)
        self.assertFalse(api.chat_retry(failed["message_id"])["ok"])

        api.adapter = DemoConnector()
        retried = api.chat_send("重试成功")
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(api.chat_history(20)["总"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
