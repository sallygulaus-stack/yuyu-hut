# -*- coding: utf-8 -*-
"""媒体归档与画册接口集成验收：只使用临时媒体库。"""

from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "panel"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "panel" / "media_archive"))

import io
import sys
import urllib.parse
import urllib.request
import base64
from pathlib import Path

from PIL import Image


项目目录 = Path(__file__).resolve().parents[2]
if str(项目目录) not in sys.path:
    sys.path.insert(0, str(项目目录))

from tests.support import IsolatedPanel


def png(color: tuple[int, int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (640, 480), color).save(out, "PNG")
    return out.getvalue()


def main() -> None:
    with IsolatedPanel(项目目录, "xy_panel_media_acceptance") as fixture:
        面板 = fixture.panel
        temp = fixture.root
        archive = 面板.MediaArchive(Path(temp) / "store")
        temp_root = Path(temp) / "panel-data"
        chat_media = temp_root / "media"
        chat_archive = temp_root / "archive"
        chat_media.mkdir(parents=True)
        chat_archive.mkdir(parents=True)
        面板.聊记录目录 = str(temp_root)
        面板.聊记录文件 = str(temp_root / "消息.jsonl")
        面板.聊天归档目录 = str(temp_root / "归档")
        面板.聊媒体目录 = str(chat_media)
        面板.媒体归档目录 = str(chat_archive)

        # 图片消息故障注入：归档失败、引用失败和可读性校验失败都只能
        # 留下明确失败状态，不能写成没有可访问图片的成功消息。
        class FailArchive(面板.MediaArchive):
            mode = ""

            def _build_archive(self, *args, **kwargs):
                if self.mode == "archive":
                    raise 面板.MediaArchiveError("注入：归档失败")
                return super()._build_archive(*args, **kwargs)

            def add_reference(self, *args, **kwargs):
                if self.mode == "reference":
                    raise 面板.MediaArchiveError("注入：引用失败")
                return super().add_reference(*args, **kwargs)

            def _verify_readable(self, path, image_info):
                if self.mode == "verify":
                    raise 面板.MediaArchiveError("注入：校验失败")
                return super()._verify_readable(path, image_info)

        fail_archive = FailArchive(chat_archive / "failures")
        面板.媒体库 = fail_archive
        for index, mode in enumerate(("archive", "reference", "verify")):
            fail_archive.mode = mode
            failed = 面板._存媒体([{
                "type": "image", "mime": "image/png",
                "name": "故障.png", "b64": base64.b64encode(
                    png((12 + index, 34, 56, 255))
                ).decode("ascii"),
            }])
            assert failed[0].get("归档失败") and failed[0].get("文件")
            assert Path(面板.聊媒体目录, failed[0]["文件"]).is_file()
            assert 面板.记一条("我", failed) is None
        fail_archive.mode = ""
        面板._聊天总数 = None
        面板._聊天计数签名 = None

        first = archive.ingest_bytes(
            png((220, 60, 90, 255)),
            "first.png",
            "image/png",
            source="workshop",
            retention="permanent",
        )
        second = archive.ingest_bytes(
            png((40, 140, 220, 255)),
            "second.png",
            "image/png",
            source="workshop",
            retention="permanent",
        )
        for item in (first, second):
            archive.add_reference(
                item["media_id"],
                ref_id="workshop:" + item["media_id"],
                kind="workshop",
                collection="gallery",
                permanent=True,
            )

        # 画册的新媒体分支使用 created_at，并返回可继续推进的稳定游标。
        面板.媒体库 = archive
        api = 面板.Api.__new__(面板.Api)
        page_one = api.gallery_list(数=1)
        assert page_one["ok"] and len(page_one["画"]) == 1
        assert page_one["下个"] and page_one["还有"]
        page_two = api.gallery_list(数=1, 游标=page_one["下个"])
        assert page_two["ok"] and len(page_two["画"]) == 1
        assert page_two["下个"] is None and not page_two["还有"]
        assert page_one["画"][0]["媒体ID"].startswith("m_")
        assert page_one["画"][0]["收纳"] is False
        same_version = api.gallery_status(
            False, "", "", "新", page_one["版本"]
        )
        assert same_version["ok"] and same_version["版本"] == page_one["版本"]
        assert not same_version["变化"]

        # 画册收纳箱只改 workshop 集合，不把作品送进生命周期 trash。
        archive.update_collection(page_one["画"][0]["媒体ID"], "trash", kind="workshop")
        stored = api.gallery_list(数=10, 收纳=True)
        assert stored["ok"] and len(stored["画"]) == 1
        assert stored["画"][0]["收纳"] is True
        assert archive.get_media(page_one["画"][0]["媒体ID"])["status"] == "active"

        # 使用面板真实的回环服务，由系统选择空闲端口。
        server_api = 面板.Api()
        port = 面板.起界面服务(server_api, port=0)
        server = 面板._server
        try:
            assert server.server_address == ("127.0.0.1", port) and port > 0
            media_id = page_two["画"][0]["媒体ID"]
            url = "http://127.0.0.1:%d/media/%s" % (
                port,
                urllib.parse.quote(media_id),
            )
            with urllib.request.urlopen(url, timeout=5) as response:
                clear = response.read()
                assert response.status == 200 and clear
            with urllib.request.urlopen(url + "?s=1", timeout=5) as response:
                thumb = response.read()
                assert response.status == 200 and thumb
        finally:
            server.shutdown()
            server.server_close()
            server_api.close()

        print("gallery integration: PASS")
        print(
            "pagination: first=%d second=%d trash=%d"
            % (len(page_one["画"]), len(page_two["画"]), len(stored["画"]))
        )
        print(
            "media endpoint without ComfyUI: PASS (%d/%d bytes)"
            % (len(clear), len(thumb))
        )


if __name__ == "__main__":
    main()
