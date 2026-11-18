"""Generate synthetic samples at runtime. No user media ships in the repository."""
import base64
import io
from pathlib import Path

from PIL import Image, ImageDraw


def seed(storage, api):
    root = Path(storage["DATA_DIR"])
    marker = root / ".demo-seeded"
    if marker.exists():
        return
    palette = [(68, 142, 179), (206, 89, 122), (58, 155, 118),
               (203, 158, 52), (121, 101, 174), (64, 155, 161)]
    samples = []
    for index in range(18):
        color = palette[index % len(palette)]
        image = Image.new("RGB", (640, 800), tuple(min(255, channel + 65) for channel in color))
        draw = ImageDraw.Draw(image)
        draw.rectangle((48, 48, 592, 752), fill=color)
        draw.ellipse((120, 120, 520, 520), fill=(241, 246, 249))
        draw.polygon([(82, 680), (305, 325 + index * 5), (555, 680)],
                     fill=palette[(index + 2) % len(palette)])
        draw.text((80, 80), "SAMPLE %02d" % (index + 1), fill=(255, 255, 255), font_size=32)
        output = io.BytesIO()
        image.save(output, "PNG")
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        samples.append(encoded)
        added = api.gallery_add("sample-%02d.png" % index, encoded)
        if not added.get("ok"):
            raise RuntimeError("演示图片生成失败")
    storage["记一条"]("她", [{"type": "text", "text": "你好，欢迎来到本地聊天。"}])
    for index in range(32):
        storage["记一条"]("我" if index % 2 else "她", [
            {"type": "text", "text": "演示消息 %02d" % (index + 1)}])
    api.receive({"text": "这是演示作品。", "段": [
        {"type": "image", "name": "sample.png", "mime": "image/png", "b64": samples[0]}]})
    marker.write_text("synthetic-v1\n", encoding="ascii")
