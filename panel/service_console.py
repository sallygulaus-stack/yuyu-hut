"""Local settings, protocol checks and bounded optional service previews."""

import base64
from copy import deepcopy
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request

from adapters import chat_adapter
from connectors.http import open_request, request_json


SETTINGS_FILE = "connections.local.json"
CHAT_MODES = frozenset({"dsh_local", "dsh_remote"})
MODES_EDITABLE = CHAT_MODES | {"demo"}
LOOPBACK = frozenset({"127.0.0.1", "::1"})
SERVICE_DEFAULTS = {"image": "http://127.0.0.1:8188", "voice": "http://127.0.0.1:7868"}
PHASES = frozenset({"configured", "connected", "connecting", "degraded", "disabled", "disconnected",
                    "endpoint_changed", "error", "offline"})
MODES = CHAT_MODES | {"demo", "onebot", "model", "test_window", "trial", "preview"}
CAPABILITIES = {"text": "文字", "image": "图片", "record": "语音文件",
                "idempotent_send": "幂等发送", "delivery_receipts": "处理回执",
                "history_context": "会话上下文"}


def address(value, *, local=False, https=False, root=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 500:
        raise ValueError("请填写服务地址")
    value = value.strip()
    if re.search(r"[\s\\@?#\x00-\x1f\x7f]", unquote(value)):
        raise ValueError("地址不能含凭据、空白、查询参数或片段")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("地址或端口格式不正确") from None
    if not parsed.hostname or port == 0 or parsed.scheme not in ("http", "https"):
        raise ValueError("请输入有效的 HTTP 或 HTTPS 地址")
    if local and (parsed.hostname not in LOOPBACK or parsed.scheme != "http"):
        raise ValueError("本机服务只接受 http://127.0.0.1 或 http://[::1]")
    if https and parsed.scheme != "https":
        raise ValueError("远程网关必须使用 HTTPS")
    path = parsed.path.rstrip("/")
    if root and path:
        raise ValueError("请填写服务根地址，不要填写接口路径")
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), path, "", ""))


def _chat_settings(patch, current=None):
    if not isinstance(patch, dict) or not isinstance(patch.get("mode"), str) or patch["mode"] not in MODES_EDITABLE:
        raise ValueError("请选择受支持的聊天连接")
    mode = patch["mode"]
    if mode == "demo":
        return {"mode": "demo"}
    url = address(patch.get("base_url"), local=mode == "dsh_local", https=mode == "dsh_remote",
                  root=mode == "dsh_local")
    current = current or {}
    same_host = current.get("base_url") == url and current.get("mode") == mode
    token = patch.get("token")
    if token is None:
        token = current.get("token", "") if same_host else ""
    if not isinstance(token, str) or not re.fullmatch(r"[!-~]{1,1024}", token):
        raise ValueError("需要填写网关令牌（不能有空格或换行）")
    return {"mode": mode, "base_url": url, "token": token}


def _service_settings(kind, patch):
    if (not isinstance(kind, str) or kind not in SERVICE_DEFAULTS or not isinstance(patch, dict) or
            type(patch.get("enabled")) is not bool):
        raise ValueError("无效的服务设置")
    return {"enabled": patch["enabled"],
            "base_url": address(patch.get("base_url") or SERVICE_DEFAULTS[kind], local=True, root=True)}


def read_controls(root):
    try:
        with (Path(root) / SETTINGS_FILE).open("rb") as handle:
            raw = handle.read(16385)
        if len(raw) > 16384:
            raise ValueError("Settings size")
        raw = json.loads(raw)
        if not isinstance(raw, dict):
            raise ValueError("Settings shape")
        controls = {}
        if "binding_key" in raw:
            if not isinstance(raw["binding_key"], str) or not re.fullmatch(r"[a-f0-9]{64}", raw["binding_key"]):
                raise ValueError("Binding key")
            controls["binding_key"] = raw["binding_key"]
        if "chat" in raw:
            chat = _chat_settings(raw["chat"])
            local_id = raw["chat"].get("binding")
            if chat["mode"] != "demo":
                if not isinstance(local_id, str) or not re.fullmatch(r"panel-[a-f0-9]{32}", local_id):
                    raise ValueError("Binding id")
                chat["binding"] = local_id
            controls["chat"] = chat
        if "initial_binding" in raw:
            initial = raw["initial_binding"]
            if (not isinstance(initial, list) or len(initial) != 2 or
                    any(not isinstance(value, str) or not re.fullmatch(r"panel-[a-f0-9]{32}", value)
                        for value in initial)):
                raise ValueError("Initial binding")
            controls["initial_binding"] = initial
        if "bridge" in raw:
            controls["bridge"] = address(raw["bridge"], local=True, root=True) if raw["bridge"] else ""
        for kind in SERVICE_DEFAULTS:
            if kind in raw:
                controls[kind] = _service_settings(kind, raw[kind])
        return controls, ""
    except FileNotFoundError:
        return {}, ""
    except (OSError, TypeError, ValueError):
        return {}, "连接设置文件无法读取；未应用其中的地址"


def apply_controls(config, controls):
    chat = controls.get("chat")
    if chat:
        mode = config["mode"] = chat["mode"]
        if mode != "demo":
            config[mode] = {**config[mode], "enabled": True, "base_url": chat["base_url"],
                            "token": chat["token"], "endpoint_id": chat["binding"],
                            "timeout": 5, "ca_file": ""}
    if "image" in controls:
        config["comfyui"] = {**config["comfyui"], **controls["image"], "token": "", "timeout": 5}


def _write_controls(root, controls):
    root.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete=False,
                                         prefix=".connections-", suffix=".tmp") as handle:
            temp = Path(handle.name)
            json.dump(controls, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(root / SETTINGS_FILE)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def _binding_id(key, chat):
    value = json.dumps([chat[k] for k in ("mode", "base_url", "token")], separators=(",", ":")).encode("utf-8")
    return "panel-" + hmac.digest(bytes.fromhex(key), value, "sha256").hex()[:32]


def diagnostic(mode, status):
    phase = status.get("phase")
    mode = mode if mode in MODES else "external"
    return {"mode": mode, "phase": phase if phase in PHASES else "offline",
            "framework": status.get("framework_connected") if type(status.get("framework_connected")) is bool else None,
            "capabilities": [value for key, value in CAPABILITIES.items()
                             if (status.get("capabilities") or {}).get(key) is True]}


def _small_media(config, route, *, body=None, timeout=10, limit=4 * 1024 * 1024):
    request = Request(config["base_url"] + route,
                      data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      headers={} if body is None else {"Content-Type": "application/json"},
                      method="GET" if body is None else "POST")
    with open_request({**config, "timeout": timeout}, request) as response:
        data = response.read(limit + 1)
        mime = response.headers.get_content_type()
    if not data or len(data) > limit:
        raise ValueError("样本为空或超过容量")
    return data, mime


def _image_workflow(checkpoint):
    def node(kind, **inputs):
        return {"class_type": kind, "inputs": inputs}

    return {
        "1": node("CheckpointLoaderSimple", ckpt_name=checkpoint),
        "2": node("CLIPTextEncode", clip=["1", 1], text="a red ball on a white table, soft daylight"),
        "3": node("CLIPTextEncode", clip=["1", 1], text=""),
        "4": node("EmptyLatentImage", width=384, height=384, batch_size=1),
        "5": node("KSampler", model=["1", 0], positive=["2", 0], negative=["3", 0], latent_image=["4", 0],
                  seed=7, steps=6, cfg=5.0, sampler_name="euler", scheduler="normal", denoise=1.0),
        "6": node("VAEDecode", samples=["5", 0], vae=["1", 2]),
        "7": node("PreviewImage", images=["6", 0]),
    }


class ServiceConsole:
    def __init__(self, runtime=None, *, root=None):
        self.runtime = runtime
        self.root = Path(runtime.storage["DATA_DIR"] if runtime is not None else root).resolve()
        self.managed = runtime is None or runtime.config.get("_console_managed") is True
        if runtime is None:
            self.controls, self.warning = read_controls(self.root)
        else:
            self.controls = deepcopy(runtime.config.get("_console_values") or {})
            self.warning = runtime.config.get("_console_warning") or ""
        self.lock = threading.RLock()
        self.preview_lock = threading.Lock()
        self.image_samples = {}

    def service_options(self):
        with self.lock:
            return {"ok": True, "warning": self.warning,
                    "service_options": {kind: {"enabled": False, "base_url": default} | (self.controls.get(kind) or {})
                                        for kind, default in SERVICE_DEFAULTS.items()}}

    def view(self):
        with self.lock:
            mode = self.runtime.config["mode"]
            chat = self.controls.get("chat") or {"mode": mode}
            result = {"ok": True, "editable": self.managed, "warning": self.warning,
                      "chat": {"mode": mode, "base_url": chat.get("base_url", ""),
                               "has_token": bool(chat.get("token"))},
                      "data_path": str(self.root), "can_open_data": os.name == "nt",
                      "service_options": self.service_options()["service_options"],
                      "bridge": self.controls.get("bridge", ""),
                      "billing": {"phase": "unavailable", "source": "未接入账单数据源"}}
        if not self.managed:
            result["services"] = [{"label": row.get("label"), "phase": row.get("phase")}
                                  for row in self.runtime.state().get("services", []) if row.get("configured")]
        result["diagnostic"] = diagnostic(mode, self.runtime.chat_ready())
        return result

    def diagnostics(self):
        return {"ok": True, **diagnostic(self.runtime.config["mode"], self.runtime.chat_ready())}

    def _save(self, updated):
        _write_controls(self.root, updated)
        self.controls = updated
        self.warning = ""

    def configure_chat(self, patch, save=False):
        if not self.managed or self.runtime is None:
            return {"ok": False, "error": "此连接由启动配置管理，不能在这里改写"}
        with self.lock:
            try:
                current = self.controls.get("chat") or {}
                chat = _chat_settings(patch, current)
                updated = {**self.controls, "chat": chat}
                if chat["mode"] != "demo":
                    # Reopening exactly the same binding resumes its cursor, even after switching away.
                    key = updated.get("binding_key")
                    if not key:
                        key = updated["binding_key"] = secrets.token_hex(32)
                        if current.get("binding"):
                            updated["initial_binding"] = [_binding_id(key, current), current["binding"]]
                    stable = _binding_id(key, chat)
                    initial = updated.get("initial_binding")
                    chat["binding"] = initial[1] if initial and initial[0] == stable else stable
                config = deepcopy(self.runtime.config)
                apply_controls(config, updated)
                adapter = chat_adapter(config)
                if chat["mode"] == "demo":
                    details = {"mode": "demo", "phase": "offline", "capabilities": []}
                else:
                    # Testing is a protocol handshake. It never sends a chat message.
                    try:
                        adapter.connect()
                    except Exception:
                        return {"ok": False, "error": "握手失败：请核对网关地址、令牌和 dsh-client/1 协议；远程地址还需有效证书"}
                    details = diagnostic(chat["mode"], adapter.status())
                if save:
                    self.runtime._activate_console_chat(adapter, config, lambda: self._save(updated))
                return {"ok": True, "saved": bool(save), "diagnostic": details,
                        "chat": {"mode": chat["mode"], "base_url": chat.get("base_url", ""),
                                 "has_token": bool(chat.get("token"))}}
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            except OSError:
                return {"ok": False, "error": "连接设置未保存，请检查数据目录"}

    def configure_bridge(self, value):
        if not self.managed or self.runtime is None:
            return {"ok": False, "error": "桥接入口由启动配置管理"}
        try:
            cleaned = address(value, local=True, root=True) if value else ""
            with self.lock:
                self._save({**self.controls, "bridge": cleaned})
            return {"ok": True, "url": cleaned}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except OSError:
            return {"ok": False, "error": "未能保存桥接入口"}

    @staticmethod
    def _service_check(kind, config):
        if kind == "voice":
            status = request_json(config, "/health")
            if status.get("ok") is not True or type(status.get("model_loaded")) is not bool:
                raise ValueError("未识别到兼容的语音服务")
            return "接口可达，模型已加载" if status["model_loaded"] else "接口可达，模型尚未加载"
        status = request_json(config, "/system_stats")
        if not isinstance(status.get("system"), dict) or not isinstance(status.get("devices"), list):
            raise ValueError("未识别到 ComfyUI 服务")
        return "ComfyUI 接口可达"

    def configure_service(self, kind, patch, save=False):
        if not self.managed:
            return {"ok": False, "error": "此服务由启动配置管理"}
        try:
            normalized = _service_settings(kind, patch)
            label = self._service_check(kind, {**normalized, "timeout": 4}) if normalized["enabled"] else "未启用"
            if save:
                with self.lock:
                    updated = {**self.controls, kind: normalized}
                    self._save(updated)
                    if kind == "image" and self.runtime is not None:
                        # Only the connector configuration changes; no process is started.
                        from adapters import ComfyAdapter
                        self.runtime.config["comfyui"] = {**normalized, "timeout": 5}
                        self.runtime.comfy = ComfyAdapter(self.runtime.config["comfyui"])
            return {"ok": True, "saved": bool(save), "label": ("已保存 · " if save else "") + label,
                    "settings": normalized}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except OSError:
            return {"ok": False, "error": "服务设置没有保存"}
        except Exception:
            return {"ok": False, "error": "服务不可达，请检查本机地址和接口"}

    def sample(self, kind, text=""):
        with self.lock:
            config = (self.controls.get(kind) or {}).copy() if isinstance(kind, str) else {}
        if not self.managed or not config.get("enabled"):
            return {"ok": False, "error": "请先启用并保存这个本机服务"}
        if not self.preview_lock.acquire(blocking=False):
            return {"ok": False, "error": "已有样本任务在处理"}
        try:
            if kind == "voice":
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 80:
                    raise ValueError("试听文字须为 1 到 80 个字符")
                data, mime = _small_media(config, "/tts/audio", body={"text": text.strip()}, timeout=30)
                wave = data.startswith(b"RIFF") and data[8:12] == b"WAVE"
                mp3 = data.startswith(b"ID3") or (len(data) > 1 and data[0] == 255 and data[1] & 224 == 224)
                if not ((wave and mime in ("audio/wav", "audio/x-wav")) or (mp3 and mime == "audio/mpeg")):
                    raise ValueError("服务没有返回可播放的 WAV 或 MP3")
                mime = "audio/wav" if wave else "audio/mpeg"
            elif kind == "image":
                data, mime = self._sample_image({**config, "timeout": 4})
            else:
                raise ValueError("未知的样本类型")
            result = {"ok": True, "media": base64.b64encode(data).decode("ascii"), "mime": mime}
            if kind == "image":
                with self.lock:
                    if len(self.image_samples) >= 4:
                        self.image_samples.pop(next(iter(self.image_samples)))
                    sample_id = secrets.token_hex(16)
                    self.image_samples[sample_id] = (time.monotonic() + 600, result["media"])
                result["preview_id"] = sample_id
            return result
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            return {"ok": False, "error": "样本未生成，请检查本机服务、模型和任务队列"}
        finally:
            self.preview_lock.release()

    def save_image(self, sample_id, archive):
        entry = None
        if isinstance(sample_id, str):
            with self.lock:
                entry = self.image_samples.get(sample_id)
        if not entry or entry[0] <= time.monotonic():
            return {"ok": False, "error": "这张预览已失效，请重新生成"}
        result = archive("生图测试.png", entry[1])
        if not result.get("ok"):
            return {"ok": False, "error": "画册未能保存这张图"}
        return {"ok": True, "duplicate": bool(result.get("重复")),
                "label": "已在画册" if result.get("重复") else "已存入画册"}

    def _sample_image(self, config):
        options = request_json(config, "/object_info/CheckpointLoaderSimple")
        try:
            models = options["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
            checkpoint = models[0] if isinstance(models, list) and isinstance(models[0], str) else ""
        except (KeyError, IndexError, TypeError):
            checkpoint = ""
        if not checkpoint or len(checkpoint) > 512:
            raise ValueError("ComfyUI 没有可用于测试的检查点")
        try:
            job = request_json(config, "/prompt", {"prompt": _image_workflow(checkpoint)})
        except Exception:
            raise ValueError("工作流未通过 ComfyUI 校验，请检查检查点模型") from None
        key = job.get("prompt_id")
        if not isinstance(key, str) or not re.fullmatch(r"[a-fA-F0-9-]{36}", key):
            raise ValueError("ComfyUI 没有返回有效的任务编号")
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            history = request_json(config, "/history/" + key).get(key) or {}
            if history.get("status", {}).get("status_str") == "error":
                raise ValueError("样本任务失败，请在 ComfyUI 检查此模型")
            images = history.get("outputs", {}).get("7", {}).get("images") or []
            if images:
                result = images[0]
                name = result.get("filename")
                if not isinstance(name, str) or re.fullmatch(r"[\w.-]{1,240}", name) is None or name in (".", ".."):
                    raise ValueError("ComfyUI 返回的图像文件名无效")
                folder = result.get("subfolder") or ""
                if result.get("type") != "temp" or folder:
                    raise ValueError("样本不是临时预览图")
                raw, _ = _small_media(config, "/view?" + urlencode({"filename": name, "type": "temp"}))
                from PIL import Image
                with Image.open(io.BytesIO(raw), formats=("PNG", "JPEG", "WEBP")) as image:
                    if image.width * image.height > 4_000_000:
                        raise ValueError("测试图尺寸超限")
                    image.thumbnail((512, 512))
                    output = io.BytesIO()
                    image.convert("RGB").save(output, "PNG")
                    return output.getvalue(), "image/png"
            time.sleep(.65)
        raise ValueError("已提交但仍在等待结果，请在 ComfyUI 查看任务队列")

    def open_data(self):
        if os.name != "nt":
            return {"ok": False, "error": "当前平台请复制目录位置"}
        try:
            if not self.root.is_dir():
                return {"ok": False, "error": "数据目录不存在"}
            os.startfile(str(self.root))
            return {"ok": True}
        except OSError:
            return {"ok": False, "error": "无法打开数据目录，请复制目录位置"}
