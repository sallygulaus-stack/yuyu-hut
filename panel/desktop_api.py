"""Expose the same commands as the browser, plus native window controls."""
from types import SimpleNamespace


def window_api(api, rpc_methods, controls=None):
    commands = {name: getattr(api, name) for name in rpc_methods if callable(getattr(api, name, None))}
    controls = api if controls is None else controls
    for name in ("win_close", "win_min", "win_max", "win_size", "win_drag"):
        method = getattr(controls, name, None)
        if callable(method):
            commands[name] = method
    return SimpleNamespace(**commands)
