"""Application APIs independent of desktop integration or private services."""
import base64
import io
from pathlib import Path
import threading
import time
import uuid

from adapters import ComfyAdapter, OneBotAdapter, chat_adapter
from appearance import Appearance
from core.models import UnifiedMessage, PROTOCOL_VERSION
from core.store import ReliabilityStore
from connectors.remote import RemoteConnector
from profiles import ProfileService, _safe_text
from service_console import ServiceConsole
from settings import default_config
from media_limits import media_limits, encoded_size, size_label


class PanelRuntime:
    def __init__(self, storage, config=None):
        self.storage = storage
        self.config = config or default_config()
        self.adapter = chat_adapter(self.config)
        self.comfy = ComfyAdapter(self.config["comfyui"])
        self.profiles = ProfileService(self.config)
        self.appearance = Appearance(Path(storage["DATA_DIR"]) / "appearance.json", _safe_text)
        self.send_lock = threading.RLock()
        self.journal = ReliabilityStore(storage["DATA_DIR"], storage["_聊天索引"])
        self.profiles.activity_at = float(self.journal.setting("last_activity") or 0) or None
        self.conversation_id = self.journal.setting("conversation") or "main"
        self.profiles.conversation_id = self.conversation_id
        self.stop_event = threading.Event()
        self.worker = None
        self.delivery_worker = None
        self.console = ServiceConsole(self)
        self.unindexed = {}
        storage["_可靠索引"] = self.journal

    def start(self):
        if isinstance(self.adapter, (RemoteConnector, OneBotAdapter)) and self.worker is None:
            generation = (self.adapter, self.stop_event)
            self.worker = threading.Thread(target=self._receive_loop if isinstance(self.adapter, RemoteConnector)
                                           else self._onebot_status_loop, args=generation,
                                           name="connector-worker", daemon=True)
            self.worker.start()
            if isinstance(self.adapter, RemoteConnector):
                self.delivery_worker = threading.Thread(
                    target=self._onebot_status_loop, args=generation, name="delivery-status-worker", daemon=True)
                self.delivery_worker.start()

    def _onebot_status_loop(self, adapter, stop):
        while not stop.is_set():
            try:
                checked = self._refresh_delivery(adapter)
            except Exception:
                checked = False
            stop.wait(.25 if checked else 1)

    def close(self):
        with self.console.lock:
            self.stop_event.set()
            for worker in (self.worker, self.delivery_worker):
                if worker:
                    worker.join(timeout=float(self.adapter.config.get("timeout", 15)) + 2)
            self.worker = self.delivery_worker = None

    def _receive_loop(self, adapter, stop):
        last_heartbeat = 0
        while not stop.is_set():
            try:
                with self.send_lock:
                    if stop.is_set() or adapter is not self.adapter:
                        return
                    self._ensure_remote()
                    now = time.monotonic()
                    if now - last_heartbeat >= float(adapter.config.get("heartbeat_seconds", 15)):
                        adapter.heartbeat()
                        last_heartbeat = now
                    for event in adapter.poll():
                        if stop.is_set():
                            break
                        saved = self.receive(event)
                        if not saved.get("ok"):
                            raise RuntimeError("接收持久化失败")
                        cursor = adapter.ack(event)
                        self.journal.checkpoint(self._receive_scope(), cursor)
                        adapter._resume_from = cursor
                delay = max(.1, float(adapter.config.get("poll_seconds", .5)))
            except Exception as exc:
                # A failed receive/ack must not clear the handshake during a send.
                with self.send_lock:
                    adapter.disconnected(exc)
                delay = max(.2, float(adapter.config.get("reconnect_seconds", 1)))
            stop.wait(delay)

    def _activate_console_chat(self, adapter, config, persist):
        with self.send_lock:
            if self.stop_event.is_set():
                raise ValueError("面板正在退出，连接未更改")
            if isinstance(adapter, RemoteConnector):
                previous = self.journal.setting("gateway:" + adapter.endpoint_id)
                if previous and previous != adapter.observed_endpoint_id:
                    raise ValueError("网关身份已变化，请在控制台的聊天连接中重新绑定")
            persist()
            self.stop_event.set()
            self.stop_event = threading.Event()
            self.worker = self.delivery_worker = None
            self.adapter = adapter
            self.config.update(config)
            self.start()

    def console_view(self):
        return self.console.view()

    def console_diagnostics(self):
        return self.console.diagnostics()

    def console_chat(self, patch, action):
        if action not in ("test", "save"):
            return {"ok": False, "error": "无效的连接操作"}
        return self.console.configure_chat(patch, save=action == "save")

    def console_bridge(self, value):
        return self.console.configure_bridge(value)

    def console_service(self, kind, patch, action):
        if action not in ("test", "save"):
            return {"ok": False, "error": "无效的服务操作"}
        return self.console.configure_service(kind, patch, save=action == "save")

    def console_sample(self, kind, text=""):
        return self.console.sample(kind, text)

    def console_save_image(self, sample_id):
        return self.console.save_image(sample_id, self.gallery_add)

    def console_open_data(self):
        return self.console.open_data()

    def _receive_scope(self):
        return self.journal.endpoint_scope(self.adapter.endpoint_id, self.adapter.bound_endpoint_id)

    def _ensure_remote(self):
        adapter = self.adapter
        adapter.bound_endpoint_id = self.journal.setting("gateway:" + adapter.endpoint_id) or ""
        adapter.ensure_connected()
        remote_id = str(adapter._hello["endpoint_id"])
        self.journal.bind_endpoint(adapter.endpoint_id, remote_id)
        adapter.bound_endpoint_id = remote_id
        adapter._resume_from = self.journal.cursor(self._receive_scope())
        return remote_id

    def rebind_endpoint(self, observed_endpoint_id):
        """Explicit local consent; never resend old outbox records to the replacement."""
        with self.send_lock:
            adapter = self.adapter
            if not isinstance(adapter, RemoteConnector) or not adapter.endpoint_changed:
                return {"ok": False, "error": "当前没有需要重新绑定的网关"}
            observed = str(observed_endpoint_id or "")
            if not observed or observed != adapter.observed_endpoint_id:
                return {"ok": False, "error": "网关身份已再次变化，请刷新诊断后重试"}
            previous = self.journal.setting("gateway:" + adapter.endpoint_id) or ""
            self.journal.bind_endpoint(adapter.endpoint_id, observed, replace=True)
            adapter.bound_endpoint_id = observed
            adapter.endpoint_changed = False
            adapter._hello = None
            adapter._last_status = "configured"
            adapter._last_error = ""
            return {"ok": True, "previous_endpoint_id": previous, "endpoint_id": observed,
                    "history_preserved": True, "old_messages_forwarded": False}

    def _refresh_delivery(self, adapter=None):
        if adapter is None:
            adapter = self.adapter
        state = adapter.status()
        if not state.get("capabilities", {}).get("message_status"):
            return False
        statuses = ["gateway_accepted", "framework_waiting", "accepted"]
        if state["capabilities"].get("delivery_receipts"):
            statuses.append("framework_unknown")
        with self.send_lock:
            if adapter is not self.adapter:
                return False
            remote_id = adapter.bound_endpoint_id if isinstance(adapter, RemoteConnector) else None
            if isinstance(adapter, RemoteConnector) and (not state.get("connected") or not remote_id):
                return False
            pending = self.journal.outgoing(state["endpoint_id"], statuses, limit=1, remote_id=remote_id,
                                            checked_before=time.time() - 2)
            if not pending:
                return False
            message = pending[0]
            # Reserve one due record before I/O. Even failed queries rotate fairly.
            message["delivery_checked_at"] = time.time()
            self.journal.put(message)
        # Status requests have their own worker and never hold the send/receive lock.
        result = adapter.delivery_status(message)
        with self.send_lock:
            if adapter is not self.adapter:
                return True
            if isinstance(adapter, RemoteConnector) and adapter.bound_endpoint_id != remote_id:
                return True
            current = self.journal.find(message["endpoint_id"], message["conversation_id"], "out",
                                        message["message_id"], remote_id=remote_id or "")
            if not current or current["delivery_status"] not in statuses:
                return True
            message = current
            if message["delivery_status"] == result["delivery_status"]:
                return True
            message.update(delivery_status=result["delivery_status"],
                           gateway_accepted=True, framework_status=result.get("framework_status", ""),
                           delivery_detail=result.get("delivery_detail", ""))
            self.journal.put(message)
            self._往外说("我", message["segments"], "", message.get("origin", ""),
                         message["segments"], message["local_sequence"],
                         message_id=message["message_id"], event_id=message["event_id"],
                         delivery_status=message["delivery_status"], endpoint_id=message["endpoint_id"],
                         message=message)
        return True

    def profile(self):
        return self.appearance.present(self.profiles.payload(self.chat_ready()))

    def my_profile(self):
        return self.appearance.present(self.profiles.my_profile(self.chat_ready()))

    def peer_profile(self):
        return self.appearance.present(self.profiles.peer_profile(self.chat_ready()))

    def profile_appearance(self, role, patch):
        return self.appearance.edit(role, patch)

    def chat_ready(self):
        try:
            return self.adapter.status()
        except Exception:
            return {"ok": False, "通": False, "连接": 0, "connected": False,
                    "configured": False, "phase": "error", "error": "适配器暂时无法连接",
                    "capabilities": {}}

    def state(self):
        ready = self.chat_ready()
        phase = str(ready.get("phase") or ("connected" if ready.get("通") else "offline"))
        services = [{"name": "chat", "label": "聊天", "up": bool(ready.get("connected", ready.get("通"))),
                     "phase": phase, "configured": bool(ready.get("configured", True)),
                     "degraded": bool(ready.get("degraded")),
                     "detail": ready.get("label") or ready.get("error")}]
        mode = str(self.config.get("mode") or "demo")
        for name, label in (("onebot", "OneBot"), ("comfyui", "ComfyUI"), ("model", "模型"),
                            ("dsh_local", "DSH 本地"), ("dsh_remote", "DSH 远程")):
            service = self.config.get(name) if isinstance(self.config.get(name), dict) else {}
            enabled = bool(service.get("enabled"))
            active = mode == name
            current = ready if active else {}
            if name == "comfyui" and enabled and mode != "demo":
                current = self.comfy.status()
            current_phase = str(current.get("phase") or ("configured" if enabled else "disabled"))
            services.append({"name": name, "label": label, "up": bool(current.get("connected")),
                             "phase": current_phase, "configured": enabled,
                             "degraded": bool(current.get("degraded")),
                             "detail": current.get("label") or current.get("error") or
                                       ("已配置" if enabled else "未启用")})
        return {"ok": True, "state": phase, "phase": phase,
                "services": services, "mode": mode, "endpoint": self.profiles.endpoint(ready).to_dict(),
                "capabilities": ready.get("capabilities") or {}}

    def _send(self, text, images, segments, origin):
        with self.send_lock:
            thin = self.storage["_存媒体"](segments, 原件=True)
            if any(item.get("归档失败") for item in thin):
                return {"ok": False, "error": "媒体未能归档，消息未发送"}
            endpoint = self.profiles.endpoint(self.chat_ready()).endpoint_id
            remote_id = ""
            connection_error = ""
            if isinstance(self.adapter, RemoteConnector):
                try:
                    remote_id = self._ensure_remote()
                except Exception:
                    remote_id = self.journal.setting("gateway:" + self.adapter.endpoint_id) or ""
                    connection_error = self.adapter.status().get("error") or "网关暂时无法连接"
            message = UnifiedMessage(
                conversation_id=self.conversation_id, message_id="msg_" + uuid.uuid4().hex,
                event_id="evt_" + uuid.uuid4().hex, sequence=0,
                sender=self.profiles.payload(self.chat_ready())["me"], created_at=time.time(),
                delivery_status="saved", segments=thin,
                attachments=[{"media_id": s["媒体ID"]} for s in thin if s.get("媒体ID")],
                profile_id="me", endpoint_id=endpoint, direction="out", origin=str(origin),
                capabilities=self.chat_ready().get("capabilities") or {},
                source_protocol_version="onebot-v11" if self.config["mode"] == "onebot" else PROTOCOL_VERSION,
                remote_endpoint_id=remote_id,
            ).to_dict()
            sequence = self.storage["记一条"](
                "我", thin, message=message,
            )
            if sequence is None:
                return {"ok": False, "error": "本地消息保存失败，请检查数据目录"}
            message["local_sequence"] = sequence
            self.journal.put(message)
            if connection_error:
                return {"ok": False, "message_id": message["message_id"], "记录序": sequence,
                        "conversation_id": message["conversation_id"], "delivery_status": "saved",
                        "error": connection_error + "；消息仅保存在本地，恢复后可手动重试"}
            return self._deliver(message, images)

    def _remote_segments(self, message):
        values = []
        for original in message["segments"]:
            segment = dict(original)
            media_id = segment.get("媒体ID") or segment.get("media_id")
            if media_id:
                archive = self.storage["媒体库"]
                path = archive.resolve_path(media_id)
                if not path:
                    raise ValueError("本地媒体已不可读取")
                if isinstance(self.adapter, RemoteConnector):
                    reply = self.adapter.upload(Path(path).read_bytes(),
                                                segment.get("mime") or "application/octet-stream",
                                                segment.get("name") or segment.get("名称") or Path(path).name)
                    self.journal.media_map(message["endpoint_id"], reply["media_id"], media_id)
                    segment = {"type": segment["type"], "media_id": reply["media_id"],
                               "mime": reply["mime"], "sha256": reply["sha256"]}
                else:
                    segment = {"type": segment["type"], "mime": segment.get("mime", ""),
                               "name": Path(path).name,
                               "b64": base64.b64encode(Path(path).read_bytes()).decode("ascii")}
            values.append(segment)
        return values

    def _deliver(self, message, images=None):
        with self.send_lock:
            sequence = message["local_sequence"]
            text = "\n".join(s.get("text", "") for s in message["segments"] if s.get("type") == "text")
            if isinstance(self.adapter, RemoteConnector):
                try:
                    remote_id = self._ensure_remote()
                    if not message.get("remote_endpoint_id"):
                        # A pre-binding saved record has never been put on the wire.
                        if message["delivery_status"] not in {"saved", "failed"}:
                            raise ValueError("旧记录缺少远端身份，不能自动重试")
                        message["remote_endpoint_id"] = remote_id
                    if message["remote_endpoint_id"] != remote_id:
                        raise ValueError("此消息属于旧端点，不能转投新端点；历史记录仍保留")
                except Exception as exc:
                    return {"ok": False, "message_id": message["message_id"], "记录序": sequence,
                            "conversation_id": message["conversation_id"],
                            "delivery_status": message["delivery_status"],
                            "error": str(exc) if isinstance(exc, ValueError) else "网关暂不可用或需要重新绑定端点"}
            try:
                wire_segments = self._remote_segments(message)
            except Exception:
                message["delivery_status"] = "failed"
                message["delivery_error"] = "媒体传输准备失败"
                self.journal.put(message)
                return {"ok": False, **message, "error": message["delivery_error"], "记录序": sequence}
            # Persist uncertainty before the network call, including across process crashes.
            message["delivery_status"] = "unknown"
            self.journal.put(message)
            try:
                result = self.adapter.send(text, images, segments=wire_segments,
                                           **{k: message[k] for k in
                                              ("message_id", "event_id", "conversation_id", "sender",
                                               "created_at", "local_sequence")})
                if result.get("ok") is not True:
                    raise ValueError("缺少成功回执")
                message["delivery_status"] = result.get("delivery_status") or "gateway_accepted"
                message["gateway_accepted"] = bool(result.get("accepted"))
                message["framework_status"] = result.get("framework_status", "")
                message["delivery_detail"] = result.get("delivery_detail", "")
                message["source_protocol_version"] = result.get("source_protocol_version") or ""
                message.pop("delivery_error", None)
            except Exception:
                message["delivery_status"] = "unknown"
                message["delivery_error"] = "结果未知；仅支持幂等的端点可安全重试"
                self.journal.put(message)
                self._往外说("我", message["segments"], text, message.get("origin", ""),
                             message["segments"], sequence, message_id=message["message_id"],
                             event_id=message["event_id"], delivery_status="unknown",
                             endpoint_id=message["endpoint_id"], message=message)
                return {"ok": False, "message_id": message["message_id"],
                        "conversation_id": message["conversation_id"], "记录序": sequence,
                        "delivery_status": "unknown", "error": message["delivery_error"]}
            self.journal.put(message)
            self._往外说("我", message["segments"], text, message.get("origin", ""),
                         message["segments"], sequence, message_id=message["message_id"],
                         event_id=message["event_id"], delivery_status=message["delivery_status"],
                         endpoint_id=message["endpoint_id"], message=message)
            if result.get("reply"):
                incoming = self.receive({
                    "text": result["reply"],
                    "message_id": "reply_" + message["message_id"],
                    "event_id": "reply_" + message["event_id"],
                    "conversation_id": message["conversation_id"], "endpoint_id": message["endpoint_id"],
                    "delivery_status": "delivered",
                })
                if not incoming["ok"]:
                    return incoming
            return {"ok": True, "记录序": sequence, "message_id": message["message_id"],
                    "event_id": message["event_id"], "conversation_id": message["conversation_id"],
                    "delivery_status": message["delivery_status"], "推到": result.get("推到", 1)}

    def chat_retry(self, message_id, conversation_id=""):
        with self.send_lock:
            endpoint = self.profiles.endpoint(self.chat_ready()).endpoint_id
            message = self.journal.find(endpoint, conversation_id or self.conversation_id, "out",
                                        str(message_id))
            if not message:
                return {"ok": False, "error": "找不到待重试的消息"}
            caps = self.chat_ready().get("capabilities") or {}
            if isinstance(self.adapter, RemoteConnector):
                try:
                    remote_id = self._ensure_remote()
                except Exception:
                    return {"ok": False, "error": "网关暂不可用或需要重新绑定端点"}
                if message.get("remote_endpoint_id") and message["remote_endpoint_id"] != remote_id:
                    return {"ok": False, "error": "此消息属于旧端点，不能转投新端点；历史记录仍保留"}
                caps = self.adapter.status()["capabilities"]
            if message["delivery_status"] in {
                    "accepted", "delivered", "gateway_accepted", "framework_waiting",
                    "framework_unknown", "framework_accepted"}:
                return {"ok": True, "duplicate": True, "delivery_status": message["delivery_status"],
                        "message_id": message["message_id"], "event_id": message["event_id"],
                        "conversation_id": message["conversation_id"], "记录序": message["local_sequence"]}
            if message["delivery_status"] == "unknown" and not caps.get("idempotent_send"):
                return {"ok": False, "error": "此端点没有幂等确认能力，结果未知时禁止盲目重发"}
            return self._deliver(message)

    def chat_send(self, text, 来自=""):
        text = str(text or "").strip()
        if not text or len(text) > 50000:
            return {"ok": False, "error": "请输入 1 到 50000 字的消息"}
        return self._send(text, [], [{"type": "text", "text": text}], 来自)

    def chat_send_image(self, b64, name="", text="", 来自=""):
        value = str(b64 or "").split(",", 1)[-1]
        limit = self.media_limits()["limits"]["image"]
        if len(value) > encoded_size(limit):
            return {"ok": False, "error": "图片不能超过 " + size_label(limit)}
        try:
            data = base64.b64decode(value, validate=True)
            if len(data) > limit:
                return {"ok": False, "error": "图片不能超过 " + size_label(limit)}
            from PIL import Image
            with Image.open(io.BytesIO(data)) as image:
                mime = Image.MIME.get(image.format, "image/png")
                image.verify()
        except Exception:
            return {"ok": False, "error": "图片无法读取"}
        name = Path(str(name or "image.png")).name
        segments = ([{"type": "text", "text": str(text)}] if text else []) + [
            {"type": "image", "mime": mime, "b64": value, "name": name}]
        return self._send(str(text), [{"b64": value, "name": name}], segments, 来自)

    def chat_send_record(self, b64, name="voice.wav", text="", 来自=""):
        value = str(b64 or "").split(",", 1)[-1]
        limit = self.media_limits()["limits"]["record"]
        if len(value) > encoded_size(limit):
            return {"ok": False, "error": "语音不能超过 " + size_label(limit)}
        try:
            raw = base64.b64decode(value, validate=True)
            if not raw or len(raw) > limit:
                raise ValueError("size")
        except Exception:
            return {"ok": False, "error": "语音无法读取或超过 " + size_label(limit)}
        import mimetypes
        mime = mimetypes.guess_type(str(name))[0] or "audio/wav"
        if not mime.startswith("audio/"):
            return {"ok": False, "error": "需要音频文件"}
        segments = ([{"type": "text", "text": str(text)}] if text else []) + [
            {"type": "record", "mime": mime, "b64": value, "name": Path(name).name}]
        return self._send(str(text), [], segments, 来自)

    def media_limits(self):
        local = media_limits(self.config.get("media_limits"))
        if isinstance(self.adapter, RemoteConnector):
            with self.send_lock:
                try:
                    self._ensure_remote()
                except Exception:
                    pass
        remote = self.chat_ready().get("media_limits") or local
        return {"ok": True, "limits": {kind: min(local[kind], remote[kind]) for kind in local}}

    def receive(self, body):
        body = body if isinstance(body, dict) else {}
        event_id = str(body.get("event_id") or body.get("事件ID") or "").strip()
        message_id = str(body.get("message_id") or body.get("消息ID") or "").strip()
        text = str(body.get("text") or "").strip()
        segments = body.get("segments") if isinstance(body.get("segments"), list) else body.get("段")
        segments = segments if isinstance(segments, list) else []
        if not segments and text:
            segments = [{"type": "text", "text": text}]
        if not segments:
            return {"ok": False, "error": "消息为空"}
        with self.send_lock:
            endpoint = str(body.get("endpoint_id") or "onebot-local")
            conversation = str(body.get("conversation_id") or "main")
            remote_id = str(body.get("source_endpoint_id") or "") if isinstance(self.adapter, RemoteConnector) else ""
            key = (endpoint, remote_id, conversation, message_id, event_id)
            if key in self.unindexed:
                self.journal.put(self.unindexed[key])
                del self.unindexed[key]
            if self.journal.find(endpoint, conversation, "in", message_id, event_id, remote_id):
                return {"ok": True, "duplicate": True, "event_id": event_id, "message_id": message_id}
            for index, original in enumerate(segments):
                remote_media_id = original.get("media_id")
                if original.get("type") in {"image", "record"} and remote_media_id:
                    local = self.journal.media_map(endpoint, remote_media_id)
                    if local and self.storage["媒体库"].resolve_path(local):
                        segments[index] = {"type": original["type"], "媒体ID": local,
                                           "mime": original.get("mime", "")}
                    elif isinstance(self.adapter, RemoteConnector):
                        raw, info = self.adapter.download(remote_media_id)
                        segments[index] = {"type": original["type"], "mime": info["mime"],
                                           "name": info["name"],
                                           "b64": base64.b64encode(raw).decode("ascii"),
                                           "remote_media_id": remote_media_id}
                    else:
                        return {"ok": False, "error": "缺少远端媒体下载连接器"}
            thin = self.storage["_存媒体"](segments, 原件=True)
            if any(item.get("归档失败") for item in thin):
                return {"ok": False, "error": "接收的媒体未能归档"}
            message_id = message_id or "msg_" + uuid.uuid4().hex
            event_id = event_id or "evt_" + uuid.uuid4().hex
            sender = body.get("sender") or self.profiles.payload(self.chat_ready())["peer"]
            message = UnifiedMessage(
                conversation_id=conversation, message_id=message_id, event_id=event_id,
                sequence=int(body.get("sequence") or 0), sender=sender,
                created_at=float(body.get("created_at") or body.get("time") or time.time()),
                delivery_status=str(body.get("delivery_status") or "delivered"), segments=thin,
                attachments=[{"media_id": s["媒体ID"]} for s in thin if s.get("媒体ID")],
                capabilities=body.get("capabilities") or {}, endpoint_id=endpoint,
                profile_id="peer", direction="in",
                source_protocol_version=str(body.get("source_protocol_version") or body.get("protocol_version") or ""),
                remote_endpoint_id=remote_id,
            ).to_dict()
            message["source_endpoint_id"] = str(body.get("source_endpoint_id") or endpoint)
            message["transport_protocol_version"] = str(body.get("transport_protocol_version") or "")
            sequence = self.storage["记一条"]("她", thin, message=message)
            if sequence is None:
                return {"ok": False, "error": "接收的消息未能保存"}
            message["local_sequence"] = sequence
            try:
                self.journal.put(message)
            except Exception:
                self.unindexed[key] = message
                raise
            for original, archived in zip(segments, thin):
                if original.get("remote_media_id") and archived.get("媒体ID"):
                    self.journal.media_map(endpoint, original["remote_media_id"], archived["媒体ID"])
            self.profiles.activity_at = max(self.profiles.activity_at or 0, message["created_at"])
            self.journal.setting("last_activity", self.profiles.activity_at)
            self._往外说("她", thin, text, "", thin, sequence,
                         message_id=message_id, event_id=event_id,
                         delivery_status=str(body.get("delivery_status") or "delivered"),
                         endpoint_id=str(body.get("endpoint_id") or ""), message=message)
            return {"ok": True, "记录序": sequence, "event_id": event_id,
                    "message_id": message_id, "delivery_status": "delivered"}

    def pond_status(self):
        with self.send_lock:
            confirmation = self.storage["_归档指纹"]()
        return {"ok": True, "scope": "local", "remote_archive": False,
                "confirmation": confirmation, "available": bool(confirmation)}

    def new_pond(self, confirmation=None):
        if not isinstance(confirmation, str) or not confirmation:
            return {"ok": False, "error": "请先核对并确认本地分段"}
        with self.send_lock:
            try:
                archived = self.storage["_归档记录"](confirmation)
            except OSError:
                return {"ok": False, "error": "本地记录未能归档"}
        if not archived:
            return {"ok": False, "error": "本地记录已变化，请重新核对"}
        return {"ok": True, "scope": "local", "remote_archive": False, "归档": archived}

    def comfy_submit(self, workflow):
        if self.config["mode"] == "demo":
            return {"ok": False, "error": "演示模式不连接外部生成服务"}
        try:
            return {"ok": True, **self.comfy.submit(workflow)}
        except Exception:
            return {"ok": False, "error": "生成任务提交失败，请检查工作流及连接"}

    def comfy_collect(self, prompt_id):
        if self.config["mode"] == "demo":
            return {"ok": False, "error": "演示模式不连接外部生成服务"}
        try:
            result = self.comfy.results(str(prompt_id))
            added = []
            for output in result.get("outputs", {}).values():
                for image in output.get("images", []):
                    raw = self.comfy.image(image)
                    added.append(self.gallery_add(image.get("filename", "generated.png"),
                                                  base64.b64encode(raw).decode("ascii")))
            return {"ok": all(item.get("ok") for item in added), "作品": added,
                    "完成": bool(result.get("status", {}).get("completed"))}
        except Exception:
            return {"ok": False, "error": "作品收取失败，已归档的作品仍可查看"}

    def win_min(self):
        window = self.storage["WIN"].get("w")
        if window:
            window.minimize()
        return {"ok": True}

    def win_max(self):
        window = self.storage["WIN"].get("w")
        if window:
            if getattr(window, "_panel_maximized", False):
                window.restore()
            else:
                window.maximize()
            window._panel_maximized = not getattr(window, "_panel_maximized", False)
        return {"ok": True}

    def win_close(self):
        window = self.storage["WIN"].get("w")
        if window:
            window.destroy()
        return {"ok": True}

    def win_drag(self):
        return {"ok": True}

    def win_size(self, *_args):
        return {"ok": True}
