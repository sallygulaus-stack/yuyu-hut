"""HTTP helper shared by local and remote connectors."""

from __future__ import annotations

import json
import ssl
from typing import Any
import urllib.request
import urllib.error

from .base import ConnectorError


def ssl_context(config):
    return ssl.create_default_context(cafile=config.get("ca_file") or None)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ConnectorError("网关不允许重定向")


def open_request(config, request):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ssl_context(config)),
        NoRedirect())
    return opener.open(request, timeout=float(config.get("timeout", 15)))


def request_json(config: dict[str, Any], route: str, body: Any = None) -> dict[str, Any]:
    url = str(config.get("base_url") or "").rstrip("/") + route
    if not url.startswith(("http://", "https://")):
        raise ConnectorError("连接地址必须是 HTTP 或 HTTPS")
    headers = {"Accept": "application/json"}
    secret = config.get("token") or config.get("api_key")
    if secret:
        headers["Authorization"] = "Bearer " + str(secret)
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="GET" if body is None else "POST",
    )
    try:
        with open_request(config, request) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ConnectorError("连接端响应超过限制")
            result = json.loads(raw)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ConnectorError("HTTP 请求被拒绝（%d）" % code) from None
    except Exception:
        raise ConnectorError("连接端请求失败") from None
    if not isinstance(result, dict):
        raise ConnectorError("连接端返回的不是 JSON 对象")
    return result
