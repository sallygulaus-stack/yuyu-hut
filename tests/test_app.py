"""End-to-end tests of configuration, offline startup and local HTTP boundaries."""
import base64
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "panel"))
from tests.support import IsolatedPanel
from settings import default_config, load_config


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = IsolatedPanel(PROJECT)
        self.addCleanup(self.fixture.close)
        self.panel = self.fixture.panel
        self.panel.初始化媒体库()
        self.api = self.panel.Api()

    def test_demo_has_no_external_requests_and_persists_both_sides(self):
        with patch("adapters.urllib.request.urlopen", side_effect=AssertionError("unexpected network")):
            result = self.api.chat_send("离线验收", "test-client")
            self.assertTrue(result["ok"], result)
            self.assertTrue(self.api.chat_ready()["通"])
            self.assertTrue(all(not row["up"] for row in self.api.state()["services"][1:]))
            self.assertFalse(self.api.comfy_submit({"test": {}})["ok"])
        history = self.api.chat_history()
        self.assertEqual(history["总"], 2)
        self.assertEqual([row["谁"] for row in history["消息"]], ["我", "她"])
        self.assertTrue(Path(self.panel.聊记录文件).is_relative_to(self.fixture.root))

    def test_cursor_remains_stable_across_append_and_archive(self):
        for index in range(40):
            self.panel.记一条("我", [{"type": "text", "text": str(index)}])
        first = self.api.chat_history(10)
        self.assertEqual(first["前游标"], 30)
        self.api.chat_send("追加")
        second = self.api.chat_history(10, 0, first["前游标"])
        self.assertEqual([row["序"] for row in second["消息"]], list(range(20, 30)))
        conversation = self.api.conversation_id
        self.assertFalse(self.api.new_pond()["ok"])
        current = self.api.pond_status()
        self.assertEqual(current["scope"], "local")
        self.assertFalse(current["remote_archive"])
        archived = self.api.new_pond(current["confirmation"])
        self.assertTrue(archived["ok"], archived)
        self.assertEqual(self.api.conversation_id, conversation)
        self.assertFalse(self.api.pond_status()["available"])
        self.assertFalse(self.api.new_pond(current["confirmation"])["ok"])
        self.assertEqual(self.api.chat_history(100)["总"], 42)
        self.assertEqual(self.api.chat_history(1)["消息"][0]["本地分段"], archived["归档"])
        self.assertTrue(self.api.chat_send("归档后")["ok"])
        self.assertEqual(self.api.chat_history(100)["总"], 44)
        self.assertEqual(self.api.chat_history(2)["消息"][0]["conversation_id"], conversation)

    def test_local_split_refuses_stale_confirmation_and_never_calls_adapter(self):
        self.api.chat_send("第一段")
        current = self.api.pond_status()
        self.api.chat_send("后来的一句")
        with patch.object(self.api.adapter, "send", side_effect=AssertionError("unexpected send")):
            self.assertFalse(self.api.new_pond(current["confirmation"])["ok"])
            self.assertTrue(Path(self.panel.聊记录文件).is_file())
            self.assertTrue(self.api.new_pond(self.api.pond_status()["confirmation"])["ok"])
        self.assertEqual(self.api.chat_history()["总"], 4)

    def test_local_diary_dates_reopen_and_refuse_concurrent_overwrite(self):
        self.assertEqual(self.api.diary_dates(), {"ok": True, "dates": []})
        self.assertEqual(self.api.diary_read("2024-02-29")["revision"], 0)
        for invalid in ("2025-02-29", "2024-2-9", "../2024-02-29", True):
            self.assertFalse(self.api.diary_read(invalid)["ok"])
            self.assertFalse(self.api.diary_save(invalid, "内容", 0)["ok"])
        for invalid in ("", "  ", 5, "a\0b", "\ud800", "a" * 20001):
            self.assertFalse(self.api.diary_save("2024-02-29", invalid, 0)["ok"])
        initial = self.api.diary_save("2024-02-29", "<b>今天</b>\n 留一行。", 0)
        self.assertRegex(initial["revision"], "^[a-f0-9]{64}$")
        self.assertTrue(self.api.diary_save("2025-01-02", "新的一年。", 0)["ok"])
        self.assertEqual(self.api.diary_dates()["dates"], ["2025-01-02", "2024-02-29"])
        fresh = self.panel.Api()
        self.assertEqual(fresh.diary_read("2024-02-29")["body"], "<b>今天</b>\n 留一行。")
        self.assertTrue(fresh.diary_save("2024-02-29", "窗口二", initial["revision"])["ok"])
        self.assertTrue(self.api.diary_save("2024-02-29", "过期草稿", initial["revision"])["conflict"])
        self.assertEqual(self.api.diary_read("2024-02-29")["body"], "窗口二")
        self.assertEqual((self.fixture.root / "diary" / "2024-02-29.md").read_text(encoding="utf-8"), "窗口二")

    def test_diary_reads_agent_files_and_legacy_without_modifying_old_database(self):
        from PIL import Image

        folder = self.fixture.root / "diary"
        photos = folder / "photos"
        photos.mkdir(parents=True)
        (folder / "2025-03-01.md").write_text("助手写的正文", encoding="utf-8-sig")
        png = photos / "2025-03-01-own.png"
        Image.new("RGB", (3, 5), "teal").save(png)
        photo_only = photos / "2025-04-01-own.jpg"
        Image.new("RGB", (3, 5), "navy").save(photo_only)
        (photos / "2025-03-01-wrong.png").write_bytes(b"<svg/>")
        legacy = folder / "entries.sqlite3"
        with sqlite3.connect(legacy) as db:
            db.execute("CREATE TABLE entries (day TEXT PRIMARY KEY, body TEXT, revision INTEGER)")
            db.execute("INSERT INTO entries VALUES (?, ?, ?)", ("2025-02-01", "旧版试写", 1))
        db.close()
        before = legacy.read_bytes()
        self.assertEqual(self.api.diary_dates()["dates"], ["2025-04-01", "2025-03-01", "2025-02-01"])
        entry = self.api.diary_read("2025-03-01")
        self.assertEqual(entry["body"], "助手写的正文")
        self.assertEqual(entry["photos"][0]["name"], png.name)
        self.assertEqual(base64.b64decode(self.api.diary_image("2025-03-01", png.name)["image"].split(",")[1]),
                         png.read_bytes())
        for invalid in ("../" + png.name, "..\\" + png.name, photo_only.name, "2025-03-01-wrong.png"):
            self.assertFalse(self.api.diary_image("2025-03-01", invalid)["ok"])
        (folder / "2025-03-01.md").write_text("助手另外写的一句", encoding="utf-8")
        self.assertTrue(self.api.diary_save("2025-03-01", "过期手稿", entry["revision"])["conflict"])
        old = self.api.diary_read("2025-02-01")
        self.assertEqual(old["body"], "旧版试写")
        self.assertTrue(self.api.diary_save("2025-02-01", "新版正文", old["revision"])["ok"])
        self.assertEqual(self.api.diary_read("2025-02-01")["body"], "新版正文")
        self.assertEqual(before, legacy.read_bytes())

    def test_diary_refuses_linked_files(self):
        folder = self.fixture.root / "diary"
        photos = folder / "photos"
        photos.mkdir(parents=True)
        outside = self.fixture.root / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        try:
            document = folder / "2025-01-01.md"
            document.symlink_to(outside)
            picture = photos / "2025-01-01-nonlocal.png"
            picture.symlink_to(outside)
        except OSError as exc:
            self.skipTest("无法创建软链接：" + str(exc))
        self.assertEqual(self.api.diary_dates()["dates"], [])
        self.assertFalse(self.api.diary_read("2025-01-01")["ok"])
        self.assertFalse(self.api.diary_save("2025-01-01", "不能改到外面", 0)["ok"])
        self.assertFalse(self.api.diary_image("2025-01-01", picture.name)["ok"])
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside")

    def test_explicit_configuration_and_data_dir_override_environment(self):
        location = self.fixture.root / "config.json"
        location.write_text(json.dumps({"mode": "onebot", "onebot": {"enabled": True}}), encoding="utf-8")
        with patch.dict(os.environ, {"QQ_PANEL_DATA_DIR": str(self.fixture.root / "environment")}):
            config = load_config(location, self.fixture.root / "explicit", True, 0)
        self.assertEqual(config["mode"], "demo")
        self.assertEqual(Path(config["data_dir"]), self.fixture.root / "explicit")
        self.assertEqual(config["onebot"]["base_url"], default_config()["onebot"]["base_url"])

    def start_server(self):
        port = self.panel.起界面服务(self.api, 0)
        self.server = self.panel._server
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        return "http://127.0.0.1:%d" % port

    def test_rpc_auth_origin_allowlist_and_static_file_boundaries(self):
        address = self.start_server()
        with urllib.request.urlopen(address + "/api/bootstrap") as response:
            bootstrap = json.load(response)
        token = bootstrap["session_token"]

        def call(route, headers=None):
            return urllib.request.urlopen(urllib.request.Request(
                address + route, data=b"[]",
                headers={"Content-Type": "application/json", **(headers or {})}), timeout=5)

        for route, headers, expected in (
            ("/api/profile", {}, 403),
            ("/api/diary_dates", {}, 403),
            ("/api/diary_image", {}, 403),
            ("/api/pond_status", {}, 403),
            ("/api/profile_appearance", {}, 403),
            ("/api/_send", {"X-Panel-Token": token}, 404),
            ("/api/profile", {"X-Panel-Token": token, "Origin": "https://example.invalid"}, 403),
            ("/api/profile", {"X-Panel-Token": token, "Host": "example.invalid"}, 403),
        ):
            with self.subTest(route=route, headers=list(headers)):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    call(route, headers)
                self.assertEqual(caught.exception.code, expected)
                caught.exception.close()
        with call("/api/profile", {"X-Panel-Token": token}) as response:
            self.assertEqual(json.load(response)["peer_name"], "聊天助手")
        for route in ("/config.json", "/panel.py", "/assets/../../settings.py", "/say", "/diary/entries.sqlite3"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(address + route)
            self.assertEqual(caught.exception.code, 404)
            caught.exception.close()

    def test_profile_appearance_persists_locally_and_resets_without_changing_identity(self):
        from PIL import Image, PngImagePlugin
        address = self.start_server()
        header = {"X-Panel-Token": json.load(urllib.request.urlopen(address + "/api/bootstrap"))["session_token"]}
        output = io.BytesIO()
        note = PngImagePlugin.PngInfo()
        note.add_text("not_public", "metadata is removed")
        Image.new("RGBA", (700, 550), (10, 40, 100, 100)).save(output, "PNG", pnginfo=note)
        image64 = base64.b64encode(output.getvalue()).decode()

        def edit(role, patch):
            request = urllib.request.Request(address + "/api/profile_appearance", data=json.dumps([role, patch]).encode(),
                                             headers={**header, "Content-Type": "application/json"})
            with urllib.request.urlopen(request) as response:
                return json.load(response)

        baseline = self.api.profile()
        source = self.api.profiles.payload(self.api.chat_ready())
        self.assertFalse(self.api.appearance.path.exists())
        self.assertFalse(edit("peer", {"identity": "invalid"})["ok"])
        self.assertFalse(edit("me", {"avatar": "not base64"})["ok"])
        self.assertFalse(edit("peer", {"display_name": "foo\nbar"})["ok"])
        self.assertFalse(edit("peer", {"signature": "secret: not allowed"})["ok"])
        self.assertFalse(self.api.appearance.path.exists())
        self.assertTrue(edit("me", {"display_name": "自己的显示名", "signature": ""})["ok"])
        self.assertTrue(edit("peer", {"display_name": "对方的显示名", "signature": "本机\n签名", "avatar": image64})["ok"])
        current = self.api.peer_profile()
        avatar = current["profile"]["avatar"]
        self.assertEqual(current["profile"]["display_name"], "对方的显示名")
        self.assertEqual(current["profile"]["signature"], "本机\n签名")
        self.assertEqual(current["profile"]["identity"], baseline["peer"]["identity"])
        self.assertEqual(current["profile"]["field_sources"], baseline["peer"]["field_sources"])
        self.assertEqual(current["endpoint"], baseline["endpoint"])
        self.assertEqual(self.api.profiles.payload(self.api.chat_ready()), source)
        # History and connector payloads keep the underlying sender rather than presentation aliases.
        self.assertTrue(self.api.chat_send("合成档案隔离")["ok"])
        self.assertEqual(self.api.chat_history()["消息"][0]["sender"]["display_name"], source["me"]["display_name"])
        with urllib.request.urlopen(address + avatar) as response:
            self.assertEqual(response.headers["Content-Type"], "image/png")
            with Image.open(io.BytesIO(response.read())) as uploaded:
                self.assertEqual(max(uploaded.size), 512)
                self.assertNotIn("not_public", uploaded.info)

        reopened = self.panel.Api()
        self.assertEqual(reopened.profile()["appearance_customized"], {"me": True, "peer": True})
        self.assertEqual(reopened.peer_profile()["profile"]["avatar"], avatar)
        self.assertEqual(reopened.my_profile()["profile"]["display_name"], "自己的显示名")
        self.assertTrue(edit("peer", {"reset": True})["ok"])
        restored = self.api.profile()
        for field in ("display_name", "signature", "avatar"):
            self.assertEqual(restored["peer"][field], baseline["peer"][field])
        self.assertEqual(restored["me_name"], "自己的显示名")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(address + avatar)
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()

    def test_media_range_thumbnail_and_browser_export(self):
        from PIL import Image
        output = io.BytesIO()
        Image.new("RGB", (800, 600), "green").save(output, "PNG")
        added = self.api.gallery_add("sample.png", base64.b64encode(output.getvalue()).decode())
        address = self.start_server()
        url = address + "/media/" + added["媒体ID"]
        with urllib.request.urlopen(url) as response:
            content = response.read()
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=0-9"})) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), content[:10])
            self.assertEqual(response.headers["Content-Range"], "bytes 0-9/%d" % len(content))
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=-10"})) as response:
            self.assertEqual(response.read(), content[-10:])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=999999999-"}))
        self.assertEqual(caught.exception.code, 416)
        caught.exception.close()
        with urllib.request.urlopen(url + "?s=1") as response:
            with Image.open(io.BytesIO(response.read())) as image:
                self.assertLessEqual(max(image.size), 560)
        with urllib.request.urlopen(address + "/download/" + added["媒体ID"]) as response:
            self.assertEqual(response.read(), content)
            self.assertIn("attachment", response.headers["Content-Disposition"])
        self.api.media_delete(added["媒体ID"])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(address + "/download/" + added["媒体ID"])
        caught.exception.close()
        with urllib.request.urlopen(url + "?s=1") as response:
            self.assertTrue(response.read())

    def test_fresh_process_starts_with_only_a_temporary_data_dir(self):
        data = self.fixture.root / "subprocess"
        completed = subprocess.run(
            [sys.executable, "-B", str(PROJECT / "panel" / "panel.py"), "--demo",
             "--smoke-test", "--data-dir", str(data), "--no-browser"],
            cwd=self.fixture.root, capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("SMOKE_TEST=PASS", completed.stdout)
        self.assertTrue((data / "archive" / "index.sqlite3").is_file())
        self.assertTrue((data / "chat" / "消息.jsonl").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
