"""Configuration is loaded without creating user directories on import."""
import copy
import json
import os
from pathlib import Path
import sys


def user_data_dir():
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "QQPanel"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "QQPanel"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "qq-panel"


def default_config():
    return json.loads(Path(__file__).with_name("config.example.json").read_text(encoding="utf-8"))


def load_config(path=None, data_dir=None, demo=False, port=None):
    config = default_config()
    location = Path(path) if path else Path(__file__).with_name("config.json")
    explicit = path is not None or location.exists()
    if explicit:
        loaded = json.loads(location.read_text(encoding="utf-8-sig"))
        if not isinstance(loaded, dict):
            raise ValueError("配置必须是 JSON 对象")
        for key, value in loaded.items():
            if isinstance(config.get(key), dict) and isinstance(value, dict):
                config[key] = {**config[key], **value}
            else:
                config[key] = value
    if demo:
        config["mode"] = "demo"
    if config["mode"] not in ("demo", "onebot", "model", "dsh_local", "dsh_remote"):
        raise ValueError("mode 必须是 demo、onebot、model、dsh_local 或 dsh_remote")
    config["port"] = int(port if port is not None else config["port"])
    if not 0 <= config["port"] <= 65535:
        raise ValueError("端口必须在 0 到 65535 之间")
    root = data_dir or os.environ.get("QQ_PANEL_DATA_DIR") or config.get("data_dir")
    config["data_dir"] = str(Path(root).expanduser().resolve() if root else user_data_dir() / config["mode"])
    config["_console_managed"] = not explicit and not demo
    for name in ("onebot", "comfyui", "model", "dsh_local", "dsh_remote"):
        service = config.get(name) if isinstance(config.get(name), dict) else {}
        variable = service.get("token_env") or service.get("api_key_env")
        if variable and os.environ.get(variable):
            service["token" if name != "model" else "api_key"] = os.environ[variable]
        url = str(service.get("base_url") or "")
        if url and (not url.startswith(("http://", "https://", "ws://", "wss://")) or "@" in url):
            raise ValueError("服务地址必须是无内嵌凭据的 HTTP(S)/WS(S) 地址")
    if config["_console_managed"]:
        from service_console import apply_controls, read_controls
        values, warning = read_controls(config["data_dir"])
        apply_controls(config, values)
        config["_console_values"] = values
        config["_console_warning"] = warning
    return copy.deepcopy(config)
