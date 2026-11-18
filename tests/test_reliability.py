"""Reliability failures over real sockets and a separately running fake framework."""
import base64
import io
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "panel"))
sys.path.insert(0, str(PROJECT))

from connectors.remote import DshLocalConnector, MemoryRemoteTransport
from connectors.base import ConnectorError
from profiles import ProfileService
from settings import default_config
from tests.support import IsolatedPanel
from tests.gateway_fixture import SyntheticStack, until, synthetic_voice


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = IsolatedPanel(PROJECT)
        self.addCleanup(self.fixture.close)
        self.panel = self.fixture.panel
        self.panel.初始化媒体库()
        self.api = self.panel.Api()
        self.addCleanup(self.api.close)

    def memory(self):
        self.transport = MemoryRemoteTransport()
        self.api.adapter = DshLocalConnector({"enabled": True, "endpoint_id": "demo",
            "poll_seconds": .05, "heartbeat_seconds": .1, "reconnect_seconds": .1}, self.transport)
        return self.transport

    def event(self, n=1):
        return {"event_id": "evt-" + str(n), "message_id": "msg-" + str(n), "sequence": n,
                "conversation_id": "main", "protocol_version": "dsh-client/1",
                "segments": [{"type": "text", "text": "same text"}],
                "created_at": 123456.75, "sender": {"identity": "original-sender"}}

    def cursor(self):
        return self.api.journal.cursor(self.api._receive_scope())

    def test_save_failure_and_ack_failure_replay_without_cursor_loss(self):
        transport = self.memory()
        transport.push(self.event())
        original = self.panel.记一条
        self.panel.记一条 = lambda *a, **k: None
        self.api.start()
        time.sleep(.4)
        self.assertEqual(transport.acks, [])
        self.assertEqual(self.cursor(), 0)
        self.assertEqual(self.api.chat_history()["总"], 0)
        transport.fail_ack = True
        self.panel.记一条 = original
        until(lambda: self.api.chat_history()["总"] == 1)
        time.sleep(.3)
        self.assertEqual(transport.acks, [])
        self.assertEqual(self.cursor(), 0)
        transport.fail_ack = False
        until(lambda: self.cursor() == 1)
        self.assertEqual(self.api.chat_history()["总"], 1)
        row = self.api.chat_history()["消息"][0]
        self.assertEqual(row["created_at"], 123456.75)
        self.assertEqual(row["sender"]["identity"], "original-sender")
        self.assertEqual(row["source_protocol_version"], "dsh-client/1")
        self.assertEqual(row["local_sequence"], 0)
        self.assertEqual(row["sequence"], 1)

    def test_ack_disconnect_cannot_invalidate_handshake_outside_send_lock(self):
        transport = self.memory()
        transport.fail_ack = True
        transport.push(self.event())
        observed = []
        original = self.api.adapter.disconnected
        def disconnect(exc):
            observed.append(self.api.send_lock._is_owned())
            original(exc)
        with patch.object(self.api.adapter, "disconnected", disconnect):
            self.api.start()
            until(lambda: observed)
        self.assertTrue(all(observed), "receiver reset raced a concurrent outgoing send")

    def test_old_unknown_history_does_not_delay_send_or_receive(self):
        transport = self.memory()
        transport.capabilities["message_status"] = True
        self.api._ensure_remote()
        for n in range(100):
            self.api.journal.put({
                "endpoint_id": "demo", "remote_endpoint_id": transport.endpoint_id,
                "conversation_id": "main", "message_id": f"old-{n}", "event_id": f"old-event-{n}",
                "direction": "out", "delivery_status": "framework_unknown",
                "segments": [{"type": "text", "text": "synthetic history"}],
            })
        checked = []
        started = threading.Event()
        def slow_status(message):
            checked.append(message["message_id"])
            time.sleep(.05)
            return {"delivery_status": "framework_unknown"}
        def refresh():
            with self.api.send_lock:
                started.set()
                self.api._refresh_delivery()
        with patch.object(self.api.adapter, "delivery_status", slow_status):
            worker = threading.Thread(target=refresh)
            worker.start()
            self.assertTrue(started.wait(2))
            begin = time.monotonic()
            sent = self.api.chat_send("new message")
            self.assertTrue(self.api.receive(self.event())["ok"])
            elapsed = time.monotonic() - begin
            worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        self.assertTrue(sent["ok"], sent)
        self.assertLess(elapsed, .5, f"new send/receive waited {elapsed:.3f}s for historical queries")
        self.assertEqual(checked, [], "unconfirmed OneBot results cannot change without receipts")
        print(f"OLD_HISTORY_SEND_RECEIVE_SECONDS={elapsed:.4f}", flush=True)

    def test_pending_delivery_is_bounded_throttled_and_fair_beyond_100_records(self):
        transport = self.memory()
        transport.capabilities["message_status"] = True
        self.api._ensure_remote()
        for n in range(105):
            self.api.journal.put({
                "endpoint_id": "demo", "remote_endpoint_id": transport.endpoint_id,
                "conversation_id": "main", "message_id": f"waiting-{n}", "event_id": f"wait-event-{n}",
                "direction": "out", "delivery_status": "framework_waiting",
                "segments": [{"type": "text", "text": "queued"}],
            })
        checked = []
        def slow_status(message):
            checked.append(message["message_id"])
            time.sleep(.05 if len(checked) == 1 else 0)
            return {"delivery_status": "framework_waiting"}
        with patch.object(self.api.adapter, "delivery_status", slow_status), \
                patch("runtime.time.time", return_value=1700000000.0) as clock:
            begin = time.monotonic()
            self.api._refresh_delivery()
            elapsed = time.monotonic() - begin
            self.assertEqual(checked, ["waiting-0"])
            self.assertLess(elapsed, .3)
            for _ in range(104):
                self.api._refresh_delivery()
            self.assertEqual(len(set(checked)), 105)
            self.assertIn("waiting-104", checked)
            self.api._refresh_delivery()
            self.assertEqual(len(checked), 105, "unchanged status must be throttled")
            clock.return_value += 2.01
            self.api._refresh_delivery()
            self.assertEqual(checked[-1], "waiting-0")
            self.assertEqual(len(checked), 106, "status must be checked again after the throttle")
        print(f"FIRST_PENDING_QUERY_SECONDS={elapsed:.4f} FAIR_RECORDS=105", flush=True)

    def test_slow_status_worker_does_not_hold_send_receive_lock(self):
        transport = self.memory()
        transport.capabilities["message_status"] = True
        self.api._ensure_remote()
        self.api.journal.put({
            "endpoint_id": "demo", "remote_endpoint_id": transport.endpoint_id,
            "conversation_id": "main", "message_id": "waiting", "event_id": "wait-event",
            "direction": "out", "delivery_status": "framework_waiting",
            "segments": [{"type": "text", "text": "queued"}],
        })
        entered, release = threading.Event(), threading.Event()
        def slow_status(message):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("synthetic slow status")
            return {"delivery_status": "framework_unknown"}
        with patch.object(self.api.adapter, "delivery_status", slow_status):
            self.api.start()
            try:
                self.assertTrue(entered.wait(2))
                begin = time.monotonic()
                self.assertTrue(self.api.chat_send("new during slow status")["ok"])
                transport.push(self.event())
                until(lambda: self.cursor() == 1, .5)
                elapsed = time.monotonic() - begin
                self.assertLess(elapsed, .5)
                print(f"ACTIVE_WORKER_SEND_RECEIVE_SECONDS={elapsed:.4f}", flush=True)
            finally:
                release.set()
                self.api.close()

    def test_scope_includes_endpoint_conversation_and_direction_not_text(self):
        event = {**self.event(), "endpoint_id": "a"}
        self.assertTrue(self.api.receive(event)["ok"])
        self.assertTrue(self.api.receive(event)["duplicate"])
        self.assertTrue(self.api.receive({**event, "endpoint_id": "b"})["ok"])
        self.assertTrue(self.api.receive({**event, "conversation_id": "other"})["ok"])
        self.assertTrue(self.api.receive({**event, "message_id": "different", "event_id": "different"})["ok"])
        self.assertEqual(self.api.chat_history()["总"], 4)

    def test_lost_receipt_uses_same_id_and_send_never_advances_receive_cursor(self):
        transport = self.memory()
        self.api.adapter.connect()
        transport.drop_receipt = True
        result = self.api.chat_send("lost receipt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["delivery_status"], "unknown")
        self.assertEqual(self.api.adapter._resume_from, 0)
        retried = self.api.chat_retry(result["message_id"])
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(len(transport.received), 1)
        self.assertEqual(self.api.chat_history()["总"], 1)
        self.assertEqual(self.api.chat_history()["消息"][0]["delivery_status"], "gateway_accepted")
        self.assertEqual(self.api.journal.cursor("demo"), 0)

    def test_endpoint_change_blocks_old_outbox_and_rebind_preserves_history_and_cursors(self):
        transport = self.memory()
        self.api._ensure_remote()
        old_id = transport.endpoint_id
        self.api.journal.checkpoint(self.api._receive_scope(), 20)
        self.api.journal.checkpoint("demo", 20)  # previous release's unscoped checkpoint
        transport.drop_receipt = True
        outgoing = self.api.chat_send("old endpoint uncertain")
        self.assertEqual(outgoing["delivery_status"], "unknown")
        transport.endpoint_id = "new-gateway"
        self.api.adapter.disconnected("test restart")
        with self.assertRaises(ConnectorError):
            self.api._ensure_remote()
        self.assertEqual(self.api.chat_ready()["phase"], "endpoint_changed")
        self.assertFalse(self.api.chat_retry(outgoing["message_id"])["ok"])
        self.assertFalse(self.api.rebind_endpoint("stale-identity")["ok"])
        rebound = self.api.rebind_endpoint("new-gateway")
        self.assertTrue(rebound["ok"])
        self.api._ensure_remote()
        self.assertEqual(self.cursor(), 0)
        self.assertEqual(self.api.journal.cursor(self.api.journal.endpoint_scope("demo", old_id)), 20)
        self.assertFalse(self.api.chat_retry(outgoing["message_id"])["ok"])
        self.assertEqual(len(transport.received), 1)
        self.assertTrue(self.api.chat_send("new endpoint message")["ok"])
        self.assertEqual(self.api.chat_history()["总"], 2)
        # A repeated remote message ID from the replacement is a different endpoint.
        event = self.event()
        self.assertTrue(self.api.receive({**event, "endpoint_id": "demo", "source_endpoint_id": old_id})["ok"])
        self.assertTrue(self.api.receive({**event, "endpoint_id": "demo", "source_endpoint_id": "new-gateway"})["ok"])
        self.assertEqual(self.api.chat_history()["总"], 4)

    def test_versions_wrong_receipts_and_missing_success_are_rejected(self):
        transport = self.memory()
        transport.protocol_version = "dsh-client/999"
        with self.assertRaises(ConnectorError):
            self.api.adapter.connect()
        transport.protocol_version = "dsh-client/1"
        self.api.adapter.connect()
        original = transport.send
        for kind in ("missing", "error", "identity"):
            def bad(payload):
                result = original(payload)
                if kind == "missing":
                    result.pop("ok")
                elif kind == "error":
                    result["error"] = "synthetic"
                else:
                    result["message_id"] = "wrong"
                return result
            with patch.object(transport, "send", bad):
                with self.assertRaises(ConnectorError):
                    self.api.adapter.send("test")

    def test_profile_legacy_names_ids_and_no_fabricated_activity(self):
        config = default_config()
        config.update(me_name="My name", peer_name="Peer name")
        config["profiles"]["local"]["me"]["display_name"] = "我"
        config["profiles"]["local"]["peer"]["display_name"] = "聊天助手"
        service = ProfileService(config)
        state = {"connected": True, "phase": "connected", "self_id": 20, "peer_id": 21}
        first, second = service.payload(state), service.payload(state)
        self.assertEqual(first["me"]["display_name"], "My name")
        self.assertEqual(first["peer"]["display_name"], "Peer name")
        self.assertEqual(first["me"]["identity"], "21")
        self.assertEqual(first["peer"]["identity"], "20")
        self.assertIsNone(second["peer"]["last_active_at"])

    def test_index_commit_failure_after_jsonl_save_is_recoverable(self):
        self.memory()
        original = self.api.journal.put
        with patch.object(self.api.journal, "put", side_effect=OSError("synthetic index failure")):
            with self.assertRaises(OSError):
                self.api.receive(self.event())
        self.assertEqual(self.api.chat_history()["总"], 1)
        self.assertEqual(self.api.journal.cursor("demo"), 0)
        self.assertTrue(self.api.receive(self.event())["duplicate"])
        self.assertEqual(self.api.chat_history()["总"], 1)

    def test_restricted_capabilities_block_media_and_unsafe_retry(self):
        transport = self.memory()
        transport.capabilities.update(image=False, record=False, idempotent_send=False)
        state = self.api.adapter.connect()
        self.assertEqual(state["phase"], "degraded")
        self.assertFalse(state["capabilities"]["image"])
        self.assertNotIn("wss", state["capabilities"])
        with self.assertRaises(ConnectorError):
            self.api.adapter.send("", segments=[{"type": "image", "media_id": "invalid"}])
        transport.drop_receipt = True
        failed = self.api.chat_send("uncertain restricted")
        self.assertEqual(failed["delivery_status"], "unknown")
        self.assertFalse(self.api.chat_retry(failed["message_id"])["ok"])
        self.assertEqual(len(transport.received), 1)

    def test_legacy_history_bootstrap_and_direction_identity(self):
        self.panel.记一条("我", [{"type": "text", "text": "old input"}],
                          message_id="same-id", event_id="same-event", endpoint_id="a")
        recovered = self.panel.Api()
        self.addCleanup(recovered.close)
        event = {**self.event(), "endpoint_id": "a", "message_id": "same-id", "event_id": "same-event"}
        self.assertTrue(recovered.receive(event)["ok"])
        self.assertEqual(recovered.chat_history()["总"], 2)
        another = self.panel.Api()
        self.addCleanup(another.close)
        self.assertTrue(another.receive(event)["duplicate"])
        self.assertEqual(another.chat_history()["总"], 2)

    def test_comfy_config_health_independent_of_selected_chat(self):
        self.api.config["mode"] = "onebot"
        self.api.config["comfyui"]["enabled"] = True
        with patch.object(self.api.comfy, "status", return_value={
                "phase": "connected", "connected": True, "label": "healthy"}):
            row = next(r for r in self.api.state()["services"] if r["name"] == "comfyui")
        self.assertTrue(row["configured"])
        self.assertTrue(row["up"])


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.stack = SyntheticStack()
        self.addCleanup(self.stack.close)
        self.fixture = IsolatedPanel(PROJECT)
        self.addCleanup(self.fixture.close)
        self.panel = self.fixture.panel
        self.panel.初始化媒体库()
        self.api = self.panel.Api(self.stack.panel_config())
        self.addCleanup(self.api.close)

    def incoming(self):
        return [r for r in self.api.chat_history(100)["消息"] if r["谁"] != "我"]

    def cursor(self):
        return self.api.journal.cursor(self.api._receive_scope())

    def test_oversize_callback_does_not_block_text_and_rejection_survives_restart(self):
        import json
        import wave
        gateway_config = json.loads((self.stack.root / "gateway-config.json").read_text())
        gateway_config["media_limits"] = {"image": 512 * 1024, "record": 512 * 1024}
        self.stack.write("gateway-config", gateway_config)
        self.stack.restart("gateway")
        self.stack.request("/send_private_msg", {"message": "before media"}, onebot=True)
        until(lambda: self.stack.request("/__test/stats")["events"] == 1)
        output = io.BytesIO()
        with wave.open(output, "wb") as voice:
            voice.setnchannels(1)
            voice.setsampwidth(2)
            voice.setframerate(16000)
            voice.writeframes(b"\0" * (20 * 16000 * 2))
        raw = output.getvalue()
        self.assertEqual(len(raw), 640044)
        result = self.stack.request("/send_private_msg", {"message": [
            {"type": "record", "data": {"file": "base64://" + base64.b64encode(raw).decode(),
                                       "filename": "synthetic20s.wav"}}]}, onebot=True)
        self.assertEqual(result["retcode"], 0)  # Persisted by the fake endpoint, not yet delivered.
        self.stack.request("/send_private_msg", {"message": "after rejected media"}, onebot=True)
        until(lambda: self.stack.request("/__test/stats")["events"] == 2, 4)
        def only_rejected():
            items = self.stack.request("/__callbacks", onebot=True)["items"]
            return items if len(items) == 1 and items[0]["status"] == "rejected" else None
        rejected = until(only_rejected)
        self.assertEqual(len(rejected), 1)
        item = rejected[0]
        self.assertEqual(item["status"], "rejected")
        self.assertEqual(item["last_error"]["code"], "media_too_large")
        self.assertFalse(item["last_error"]["retryable"])
        self.assertIn("512 KiB", item["last_error"]["message"])
        state = json.loads((self.stack.root / "onebot/transport-state.json").read_text())
        self.assertEqual(state["callbacks"][0]["segments"][0]["data"]["filename"], "synthetic20s.wav")
        attempts = item["attempts"]
        self.stack.restart("onebot")
        time.sleep(.65)
        restored = self.stack.request("/__callbacks", onebot=True)["items"][0]
        self.assertEqual(restored["metadata"], item["metadata"])
        self.assertEqual(restored["attempts"], attempts)
        self.stack.request("/__callbacks/retry", {"event_id": item["metadata"]["event_id"]}, onebot=True)
        until(lambda: self.stack.request("/__callbacks", onebot=True)["items"][0]["attempts"] > attempts)
        self.assertEqual(self.stack.request("/__test/stats")["events"], 2)
        self.api.start()
        until(lambda: len(self.incoming()) == 2)
        self.assertIn("after rejected media", str(self.incoming()[-1]["段"]))
        print("OVERSIZE_BYTES=640044 FOLLOWING_TEXT_RECEIVED=true REJECTED_RETAINED=1", flush=True)

    def test_temporary_callback_failure_recovers_with_original_identity(self):
        self.stack.stop_process("gateway")
        self.stack.request("/send_private_msg", {"message": "temporary outage"}, onebot=True)
        items = until(lambda: [item for item in self.stack.request("/__callbacks", onebot=True)["items"]
                               if item["attempts"]])
        item = items[0]
        self.assertEqual(item["status"], "pending")
        self.assertTrue(item["last_error"]["retryable"])
        self.stack.start("gateway")
        until(lambda: self.stack.request("/__test/stats")["events"] == 1, 7)
        until(lambda: not self.stack.request("/__callbacks", onebot=True)["items"])
        event = self.stack.request("/v1/events")["events"][0]
        for name in ("message_id", "event_id", "conversation_id", "created_at"):
            self.assertEqual(event[name], item["metadata"][name])
        self.assertEqual(self.stack.request("/__test/stats")["events"], 1)

    def test_callback_confirmation_save_failure_keeps_record_until_durable_removal(self):
        self.stack.stop_process("gateway")
        self.stack.request("/send_private_msg", {"message": "durable callback removal"}, onebot=True)
        items = until(lambda: [item for item in self.stack.request("/__callbacks", onebot=True)["items"]
                               if item["attempts"]])
        identity = items[0]["metadata"]
        blocked = self.stack.root / "onebot/transport-state.json.part"
        self.assertTrue(blocked.is_relative_to(self.stack.root))
        blocked.mkdir()
        try:
            self.stack.start("gateway")
            until(lambda: self.stack.request("/__test/stats")["events"] == 1, 7)
            remaining = self.stack.request("/__callbacks", onebot=True)["items"]
            self.assertEqual(len(remaining), 1, "uncommitted removal lost the in-memory callback")
            self.assertEqual(remaining[0]["metadata"], identity)
        finally:
            blocked.rmdir()
        until(lambda: not self.stack.request("/__callbacks", onebot=True)["items"], 7)
        self.assertEqual(self.stack.request("/__test/stats")["events"], 1)
    def test_framework_offline_queue_recovers_without_claiming_processing_or_replaying(self):
        self.stack.stop_process("mock")
        until(lambda: self.stack.request("/__state", onebot=True)["connections"] == 0)
        self.api.start()
        until(lambda: self.api.chat_ready().get("connected"))
        result = self.api.chat_send("framework offline")
        self.assertEqual(result["delivery_status"], "gateway_accepted")
        until(lambda: self.api.chat_history()["消息"][0]["delivery_status"] == "framework_waiting")
        self.assertFalse(self.api.chat_ready()["capabilities"]["delivery_receipts"])
        self.assertEqual(len(self.incoming()), 0)
        self.stack.start("mock")
        until(lambda: len(self.incoming()) == 1)
        until(lambda: self.api.chat_history()["消息"][0]["delivery_status"] == "framework_unknown")
        self.stack.restart("mock")
        time.sleep(.8)
        self.assertEqual(len(self.incoming()), 1)
        self.assertEqual(self.stack.request("/__test/stats")["events"], 1)
        self.assertEqual(self.stack.request("/__state", onebot=True)["pending"]["unknown"], 1)

    def test_real_network_media_ack_restart_and_client_checkpoint(self):
        self.api.start()
        until(lambda: self.api.chat_ready().get("connected"))
        self.assertTrue(self.api.chat_send("text")["ok"])
        until(lambda: len(self.incoming()) == 1)
        from PIL import Image
        output = io.BytesIO()
        Image.new("RGB", (80, 60), "red").save(output, "PNG")
        image_result = self.api.chat_send_image(base64.b64encode(output.getvalue()).decode(), "s.png")
        self.assertTrue(image_result["ok"], image_result)
        voice_result = self.api.chat_send_record(base64.b64encode(synthetic_voice()).decode(), "tone.wav")
        self.assertTrue(voice_result["ok"], voice_result)
        until(lambda: len(self.incoming()) == 3)
        types = {s["type"] for r in self.incoming() for s in r["段"]}
        self.assertTrue({"image", "record"}.issubset(types))
        for row in self.api.chat_history(100)["消息"]:
            for seg in row["段"]:
                self.assertNotIn("b64", seg)
                if seg.get("媒体ID"):
                    self.assertTrue(self.panel.媒体库.resolve_path(seg["媒体ID"]).is_file())
        until(lambda: self.cursor() == 3)
        cursor = self.cursor()
        self.stack.restart("onebot")
        self.stack.restart("gateway")
        self.assertTrue(self.api.chat_send("after restarts")["ok"])
        until(lambda: len(self.incoming()) == 4)
        until(lambda: self.cursor() > cursor)
        self.api.close()
        newer = self.panel.Api(self.stack.panel_config())
        self.addCleanup(newer.close)
        newer.start()
        until(lambda: newer.chat_ready().get("connected"))
        time.sleep(.4)
        self.assertEqual(newer.chat_history(100)["总"], 8)
        self.assertTrue(newer.chat_send("client restarted")["ok"])
        until(lambda: newer.chat_history(100)["总"] == 10)

    def test_network_failure_ack_and_lost_receipt_safe_replay(self):
        self.stack.request("/__test/faults", {"drop_receipt": True, "fail_ack": True,
                                             "duplicate_events": True})
        self.api.start()
        until(lambda: self.api.chat_ready().get("connected"))
        result = self.api.chat_send("uncertain network")
        self.assertEqual(result["delivery_status"], "unknown")
        until(lambda: len(self.incoming()) == 1)
        self.assertEqual(self.cursor(), 0)
        retry = self.api.chat_retry(result["message_id"])
        self.assertTrue(retry["ok"], retry)
        self.stack.request("/__test/faults", {"fail_ack": False, "duplicate_events": False})
        until(lambda: self.cursor() == 1)
        messages = self.api.chat_history(100)["消息"]
        keys = [(r["endpoint_id"], r["conversation_id"], r["direction"], r["message_id"]) for r in messages]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(self.stack.request("/__test/stats")["events"], 1)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.stack.request("/v1/handshake", {"protocol_version": "bad"})
        caught.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.stack.request("/v1/heartbeat", {}, token="wrong")
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()

    def test_tls_uses_only_test_ca_and_checks_hostname(self):
        import json
        import ssl
        import urllib.request
        from tests.make_test_certs import generate
        from tests.gateway_fixture import free_port
        from connectors.remote import RemoteDshConnector
        certificates = generate(self.stack.root / "certificates")
        config_file = self.stack.root / "gateway-config.json"
        config = json.loads(config_file.read_text())
        port = free_port()
        config.update(tls_port=port, cert_file=str(certificates / "server.crt"),
                      key_file=str(certificates / "server.key"))
        config_file.write_text(json.dumps(config), encoding="utf-8")
        self.stack.restart("gateway")
        remote = RemoteDshConnector({
            "enabled": True, "base_url": f"https://localhost:{port}", "token": self.stack.token,
            "ca_file": str(certificates / "ca.crt"), "timeout": 2})
        self.assertTrue(remote.connect()["secure"])
        self.assertTrue(remote.heartbeat()["ok"])
        self.assertTrue(remote.send("TLS synthetic")["ok"])
        insecure_trust = RemoteDshConnector({**remote.config, "ca_file": ""})
        with self.assertRaises(ConnectorError):
            insecure_trust.connect()
        with self.assertRaises(ssl.SSLCertVerificationError):
            import socket
            ctx = ssl.create_default_context(cafile=str(certificates / "ca.crt"))
            with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
                ctx.wrap_socket(sock, server_hostname="wrong.invalid")

    def test_network_restricted_capabilities_wrong_receipt_and_save_failure(self):
        self.stack.request("/__test/faults", {"restrict_media": True, "no_idempotency": True})
        connector = self.api.adapter
        state = connector.connect()
        self.assertEqual(state["phase"], "degraded")
        with self.assertRaises(ConnectorError):
            connector.send("", segments=[{"type": "record", "media_id": "not-a-real-id"}])
        self.stack.request("/__test/faults", {"restrict_media": False, "no_idempotency": False,
                                             "wrong_receipt": True})
        result = self.api.chat_send("network bad identity")
        self.assertEqual(result["delivery_status"], "unknown")
        connector.reconnect()
        self.assertTrue(self.api.chat_retry(result["message_id"])["ok"])
        original = self.panel.记一条
        self.panel.记一条 = lambda *a, **k: None
        self.api.start()
        time.sleep(1)
        self.assertEqual(self.cursor(), 0)
        self.assertEqual(self.stack.request("/__test/stats")["acks"], 0)
        self.panel.记一条 = original
        until(lambda: len(self.incoming()) == 1)
        until(lambda: self.cursor() == 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
