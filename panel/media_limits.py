"""Bounded media sizes shared by the client and gateway."""

MIB = 1024 * 1024
DEFAULT_LIMITS = {"image": 10 * MIB, "record": 20 * MIB}
LEGACY_GATEWAY_LIMITS = {"image": 512 * 1024, "record": 512 * 1024}
LEGACY_ONEBOT_LIMITS = {"image": 2 * MIB, "record": 2 * MIB}
MAX_MEDIA_BYTES = 50 * MIB


def media_limits(values=None, defaults=None):
    defaults = defaults or DEFAULT_LIMITS
    values = values if isinstance(values, dict) else {}
    result = {}
    for kind in DEFAULT_LIMITS:
        value = values.get(kind, defaults[kind])
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_MEDIA_BYTES:
            raise ValueError("媒体上限必须是 1 到 52428800 之间的整数字节数")
        result[kind] = value
    return result


def encoded_size(size):
    return 4 * ((size + 2) // 3)


def request_limit(limits):
    return encoded_size(max(limits.values())) + MIB


def size_label(size):
    if size % MIB == 0:
        return f"{size // MIB} MiB"
    return f"{size / 1024:g} KiB"
