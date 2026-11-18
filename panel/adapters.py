"""Compatibility facade for the protocol-neutral connector package."""
import json
import urllib.parse
import urllib.request
from typing import Any

from connectors.base import ConnectorError
from connectors.demo import DemoConnector
from connectors.onebot import OneBotConnector
from connectors.remote import DshLocalConnector, RemoteDshConnector


def request_json(config, route, body=None):
    url = str(config["base_url"]).rstrip("/") + route
    headers = {"Accept": "application/json"}
    secret = config.get("token") or config.get("api_key")
    if secret:
        headers["Authorization"] = "Bearer " + secret
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="GET" if body is None else "POST")
    with urllib.request.urlopen(request, timeout=float(config.get("timeout", 15))) as response:
        return json.load(response)


class DemoAdapter:
    """Legacy name retained for callers outside the panel."""

    def __init__(self, config=None):
        self._connector = DemoConnector(config)

    def status(self):
        return self._connector.status()

    def send(self, text, images=None, segments=None, **kwargs):
        return self._connector.send(text, images, segments, **kwargs)


class OneBotAdapter:
    def __init__(self, config):
        self.config = config
        self._connector = OneBotConnector(config)

    def status(self):
        return self._connector.status()

    def send(self, text, images=None, segments=None, **kwargs):
        return self._connector.send(text, images, segments, **kwargs)

    def delivery_status(self, message):
        return self._connector.delivery_status(message)


class ModelAdapter:
    def __init__(self, config):
        self.config = config

    def status(self):
        return {"ok": True, "通": True, "连接": 1, "phase": "degraded",
                "configured": True, "connected": True, "label": "模型演示适配器已启用",
                "endpoint_id": "model-single-turn", "degraded": True,
                "capabilities": {"text": True, "image": False, "history_context": False},
                "protocol_version": "dsh-client/1"}

    def send(self, text, images=None, segments=None, **_kwargs):
        if images:
            raise ValueError("当前模型适配器只接收文字")
        result = request_json(self.config, "/chat/completions", {
            "model": self.config["model"], "messages": [{"role": "user", "content": text}],
            "stream": False,
        })
        reply = result["choices"][0]["message"]["content"]
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("模型返回了空消息")
        return {"ok": True, "reply": reply, "推到": 1, "delivery_status": "delivered",
                "endpoint_id": "model-single-turn", "degraded": True,
                "warning": "当前模型适配器只发送当前文字，不附带历史或角色设定"}


class ComfyAdapter:
    def __init__(self, config):
        self.config = config

    def status(self):
        if not self.config.get("enabled"):
            return {"phase": "disabled", "connected": False, "label": "未启用"}
        try:
            request_json(self.config, "/system_stats")
            return {"phase": "connected", "connected": True, "label": "服务可访问"}
        except Exception:
            return {"phase": "error", "connected": False, "label": "已配置，服务不可访问"}

    def submit(self, workflow):
        if not self.config.get("enabled"):
            raise ValueError("ComfyUI 适配器未启用")
        if not isinstance(workflow, dict) or not workflow:
            raise ValueError("需要有效的 ComfyUI API 工作流")
        return request_json(self.config, "/prompt", {"prompt": workflow})

    def results(self, prompt_id):
        if not self.config.get("enabled"):
            raise ValueError("ComfyUI 适配器未启用")
        history = request_json(self.config, "/history/" + urllib.parse.quote(prompt_id, safe=""))
        return history.get(prompt_id, {})

    def image(self, image):
        if not self.config.get("enabled"):
            raise ValueError("ComfyUI 适配器未启用")
        query = urllib.parse.urlencode({key: image[key] for key in ("filename", "subfolder", "type") if key in image})
        headers = {}
        if self.config.get("token"):
            headers["Authorization"] = "Bearer " + self.config["token"]
        request = urllib.request.Request(self.config["base_url"].rstrip("/") + "/view?" + query, headers=headers)
        with urllib.request.urlopen(request, timeout=float(self.config.get("timeout", 30))) as response:
            payload = response.read(50 * 1024 * 1024 + 1)
        if len(payload) > 50 * 1024 * 1024:
            raise ValueError("生成图片超过 50 MB")
        return payload


def chat_adapter(config):
    if config["mode"] == "demo":
        return DemoAdapter(config)
    name = config["mode"]
    if name not in config:
        raise ValueError("未配置所选连接器")
    if not config[name].get("enabled"):
        raise ValueError("所选聊天适配器尚未启用")
    if name == "model" and not config["model"].get("model"):
        raise ValueError("请配置模型名称")
    service = {**config[name], "media_limits": config.get("media_limits")}
    if name == "onebot":
        return OneBotAdapter(service)
    if name == "dsh_local":
        return DshLocalConnector(service)
    if name == "dsh_remote":
        return RemoteDshConnector(service)
    return ModelAdapter(config[name])
