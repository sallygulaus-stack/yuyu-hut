"""QQ-style identity cards assembled without exposing private connection data."""

from __future__ import annotations

from copy import deepcopy
import time
import re
import ipaddress
from urllib.parse import urlparse
from typing import Any, Mapping

from core.models import CAPABILITIES, EndpointInfo, Profile, merge_capabilities


DEFAULT_AVATARS = {"me": "assets/default-me.png", "peer": "assets/default-peer.png"}


def _merge(base: Mapping[str, Any], patch: Mapping[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(dict(base))
    if not isinstance(patch, Mapping):
        return result
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge(result[key], value)
        else:
            result[str(key)] = deepcopy(value)
    return result


def _source_patch(raw: Mapping[str, Any], source: str, role: str) -> Mapping[str, Any]:
    value = raw.get(source, {}) if isinstance(raw, Mapping) else {}
    if isinstance(value, Mapping) and isinstance(value.get(role), Mapping):
        return value[role]
    return {}


def _safe_text(value, limit=500):
    text = str(value or "")[:limit]
    if re.search(r"[A-Za-z]:[\\/]|\\\\|/(?:home|root|opt|Users)/|(?:token|secret|password|api_key)\s*[:=]", text, re.I):
        return ""
    if re.search(r"(?:127\.|10\.\d|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)", text):
        return ""
    return text


def _safe_avatar(value):
    text = str(value or "")
    if text.startswith(("assets/", "/media/")) and ".." not in text and "\\" not in text:
        return text
    parsed = urlparse(text)
    if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.query:
        try:
            if not ipaddress.ip_address(parsed.hostname).is_global:
                return ""
        except ValueError:
            if parsed.hostname in {"localhost"} or parsed.hostname.endswith((".local", ".internal")):
                return ""
        return text[:500]
    return ""


def _profile(config: Mapping[str, Any], role: str, state=None) -> Profile:
    raw = config.get("profiles") if isinstance(config.get("profiles"), Mapping) else {}
    defaults = {
        "profile_id": "me" if role == "me" else "peer",
        "display_name": str(config.get("me_name" if role == "me" else "peer_name") or
                             ("我" if role == "me" else "聊天助手")),
        "avatar": DEFAULT_AVATARS[role],
        "signature": "",
        "identity": "local-user" if role == "me" else "local-peer",
        "status": "offline",
        "last_active_at": None,
        "role": "user" if role == "me" else "assistant",
        "voice": {},
        "capabilities": dict((state or {}).get("capabilities") or {}),
        "version": "",
        "source": "local",
    }
    # The order is intentional: DSH role data wins over bridge identity, which
    # wins over the client-local defaults.
    sources = {key: "local" for key in defaults}
    live = (state or {}).get("profiles") or {}
    if (state or {}).get("self_id"):
        live = _merge(live, {"onebot": {"peer": {"identity": str(state["self_id"])}}})
    if (state or {}).get("peer_id"):
        live = _merge(live, {"onebot": {"me": {"identity": str(state["peer_id"])}}})
    for source in ("local", "onebot", "dsh"):
        patch = _merge(_source_patch(raw, source, role), _source_patch(live, source, role))
        for key, value in patch.items():
            if key not in defaults or key in {"profile_id", "source", "capabilities"}:
                continue
            if value in ("", None, {}):
                continue
            if (source == "local" and key == "display_name" and
                    value == ("我" if role == "me" else "聊天助手") and
                    config.get("me_name" if role == "me" else "peer_name")):
                continue
            defaults[key] = deepcopy(value)
            sources[key] = source
    defaults["source"] = sources["display_name"]
    defaults["profile_id"] = str(defaults.get("profile_id") or ("me" if role == "me" else "peer"))
    defaults["display_name"] = str(defaults.get("display_name") or "未命名")[:120]
    defaults["avatar"] = _safe_avatar(defaults.get("avatar")) or DEFAULT_AVATARS[role]
    for key in ("display_name", "signature", "identity", "role", "version"):
        defaults[key] = _safe_text(defaults.get(key))
    defaults["display_name"] = defaults["display_name"] or ("我" if role == "me" else "聊天助手")
    defaults["signature"] = str(defaults.get("signature") or "")[:500]
    defaults["identity"] = str(defaults.get("identity") or "")[:200]
    defaults["status"] = str(defaults.get("status") or "offline")[:40]
    defaults["role"] = str(defaults.get("role") or "")[:80]
    defaults["version"] = str(defaults.get("version") or "")[:80]
    defaults["voice"] = {str(k): _safe_text(v, 80) for k, v in
                         (defaults.get("voice") or {}).items() if k in {"name", "format", "enabled"}}
    defaults["capabilities"] = merge_capabilities(defaults.get("capabilities"))
    try:
        last_active = float(defaults["last_active_at"]) if defaults.get("last_active_at") else None
    except (TypeError, ValueError):
        last_active = None
    return Profile(
        profile_id=str(defaults["profile_id"]),
        display_name=defaults["display_name"],
        avatar=defaults["avatar"],
        signature=defaults["signature"],
        identity=defaults["identity"],
        status=defaults["status"],
        last_active_at=last_active,
        role=defaults["role"],
        voice=defaults["voice"],
        capabilities=defaults["capabilities"],
        version=defaults["version"],
        source=str(defaults["source"]),
        field_sources=sources,
    )


class ProfileService:
    def __init__(self, config: Mapping[str, Any]):
        self.config = config
        self.activity_at = None

    def endpoint(self, status: Mapping[str, Any] | None = None) -> EndpointInfo:
        mode = str(self.config.get("mode") or "demo")
        raw = self.config.get("endpoint") if isinstance(self.config.get("endpoint"), Mapping) else {}
        names = {
            "demo": ("demo", "离线演示", "本机"),
            "onebot": ("onebot-local", "本地 OneBot", "本机"),
            "model": ("model-single-turn", "模型演示适配器", "本机"),
            "dsh_local": ("dsh-local", "DSH 本地连接", "本机"),
            "dsh_remote": ("dsh-remote", "DSH 远程连接", "云端接口"),
        }
        endpoint_id, display_name, location = names.get(mode, (mode, mode, "本机"))
        endpoint_id = str((status or {}).get("endpoint_id") or raw.get("endpoint_id") or endpoint_id)[:120]
        display_name = _safe_text(raw.get("display_name") or display_name, 120)
        location = _safe_text(raw.get("location") or location, 80)
        phase = str((status or {}).get("phase") or ("connected" if mode == "demo" else "configured"))
        secure = mode == "dsh_remote"
        if isinstance(status, Mapping):
            secure = bool(status.get("secure", secure))
        return EndpointInfo(
            endpoint_id=endpoint_id,
            kind=mode,
            display_name=display_name,
            location=location,
            state=phase,
            protocol_version=str((status or {}).get("protocol_version") or
                                 raw.get("protocol_version") or "dsh-client/1"),
            capabilities=merge_capabilities(raw.get("capabilities"), (status or {}).get("capabilities")),
            secure=secure,
        )

    def payload(self, status: Mapping[str, Any] | None = None) -> dict[str, Any]:
        state = dict(status or {})
        endpoint = self.endpoint(state)
        me = _profile(self.config, "me", state)
        peer = _profile(self.config, "peer", state)
        if state.get("connected") and state.get("framework_connected") is not False:
            peer = Profile(**{**peer.to_dict(), "status": "online"})
        if self.activity_at:
            peer = Profile(**{**peer.to_dict(), "last_active_at": self.activity_at})
        return {
            "ok": True,
            "me": me.to_dict(),
            "peer": peer.to_dict(),
            "endpoint": endpoint.to_dict(),
            "capabilities": endpoint.capabilities,
            "protocol_version": endpoint.protocol_version,
            "title": str(self.config.get("title") or "鱼鱼小窝"),
            "peer_name": peer.display_name,
            "me_name": me.display_name,
            "mode": str(self.config.get("mode") or "demo"),
            "status": {k: state.get(k) for k in
                       ("phase", "configured", "connected", "degraded", "label", "error", "version",
                        "endpoint_changed", "bound_endpoint_id", "observed_endpoint_id")},
            "conversation_id": getattr(self, "conversation_id", "main"),
        }

    def my_profile(self, status: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self.payload(status)
        return {**payload, "profile": payload["me"]}

    def peer_profile(self, status: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self.payload(status)
        return {**payload, "profile": payload["peer"]}


def profile_payload(config: Mapping[str, Any], status: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return ProfileService(config).payload(status)
