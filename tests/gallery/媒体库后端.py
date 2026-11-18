import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "panel"))
"""只走临时 MediaArchive 的画册分页、版本和并发回归。"""
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

from PIL import Image

项目 = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(项目))
from tests.support import IsolatedPanel


def image_bytes(index=0):
    stream = io.BytesIO()
    Image.new("RGB", (96, 72), (index, 42, 180)).save(stream, "PNG")
    return stream.getvalue()


class MediaGalleryRegression(unittest.TestCase):
    def setUp(self):
        self.fixture = IsolatedPanel(项目)
        self.addCleanup(self.fixture.close)
        self.panel = self.fixture.panel
        self.api = self.fixture.api
        self.archive = self.panel.初始化媒体库()

    def add(self, index=0, name="same.png"):
        return self.api.gallery_add(name, base64.b64encode(image_bytes(index)).decode("ascii"))

    def test_keyset_insert_between_pages_does_not_repeat_or_skip(self):
        for sort in ("新", "旧"):
            with self.subTest(sort=sort):
                original = {self.add(index)["名"] for index in range(4)}
                first = self.api.gallery_list(数=1, 排序=sort)
                self.assertTrue(first["ok"], first)
                cursor = first["下个"]
                self.assertIn("媒体ID", cursor)
                self.assertIn("时", cursor)
                self.assertNotIn("偏移", cursor)
                self.add(20 if sort == "新" else 21)
                found = [first["画"][0]["名"]]
                second = self.api.gallery_list(数=1, 排序=sort, 游标=cursor)
                self.assertTrue(second["变化"], second)
                while True:
                    self.assertTrue(second["ok"], second)
                    found.extend(x["名"] for x in second["画"])
                    if not second["还有"]:
                        break
                    second = self.api.gallery_list(数=1, 排序=sort, 游标=second["下个"])
                self.assertEqual(len(found), len(set(found)))
                self.assertTrue(original.issubset(found))
                for kw in ({"排序": "旧" if sort == "新" else "新"},
                           {"搜索": "different"}, {"月份": "2025-01"}, {"收纳": True}):
                    args = {"数": 1, "排序": sort, "游标": cursor, **kw}
                    self.assertFalse(self.api.gallery_list(**args)["ok"], args)

    def test_visible_version_ignores_duplicate_upload_collect_and_chat_metadata(self):
        first = self.add()
        initial = self.api.gallery_list()
        version = initial["版本"]
        duplicate = self.add(name="renamed.png")
        self.assertTrue(duplicate["重复"], duplicate)
        self.assertEqual(self.api.gallery_list()["版本"], version)
        record = [{"谁": "她", "段": [{"type": "image", "媒体ID": first["名"]}]}]
        with patch.object(self.panel, "_聊天索引", return_value=record):
            for _ in range(2):
                self.assertEqual(self.api.gallery_collect()["收了"], 0)
                self.assertEqual(self.api.gallery_list()["版本"], version)
        self.archive.ingest_bytes(image_bytes(), "chat.png", source="chat",
                                  metadata={"chat_only": "changed"})
        self.archive.add_reference(first["名"], "chat:repeat")
        for _ in range(3):
            status = self.api.gallery_status(版本=version)
            self.assertEqual(status["版本"], version)
            self.assertFalse(status["变化"])
        self.api.gallery_del(first["名"])
        stored_version = self.api.gallery_status()["版本"]
        self.assertNotEqual(stored_version, version)
        self.api.gallery_restore(first["名"])
        self.assertEqual(self.api.gallery_status()["版本"], version)
        self.add(1)
        self.assertTrue(self.api.gallery_status(版本=version)["变化"])
        for kwargs in ({"收纳": True}, {"搜索": "same"}, {"月份": "2025-01"}, {"排序": "旧"}):
            page = self.api.gallery_list(**kwargs)
            self.assertEqual(page["版本"], self.api.gallery_status(**kwargs)["版本"])
            self.assertNotEqual(page["版本"], self.api.gallery_status()["版本"])

    def test_32_simultaneous_identical_uploads_have_exactly_one_creator(self):
        barrier = threading.Barrier(32)

        def upload(_):
            barrier.wait(timeout=15)
            return self.add()

        with ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(upload, range(32)))
        self.assertTrue(all(r["ok"] for r in results), results)
        self.assertEqual(sum(not r["重复"] for r in results), 1, results)
        self.assertEqual(self.api.gallery_status()["总"], 1)
        self.assertEqual(len(self.archive.references(results[0]["名"])), 1)

    def test_32_different_contents_with_same_name_remain_distinct(self):
        with ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(self.add, range(32)))
        self.assertTrue(all(r["ok"] and not r["重复"] for r in results), results)
        self.assertEqual(len({r["名"] for r in results}), 32)
        self.assertEqual(self.api.gallery_status()["总"], 32)
        for result in results:
            with Image.open(self.archive.resolve_path(result["名"])) as image:
                image.load()

    def test_chat_first_and_stored_reupload_preserve_single_artwork(self):
        item = self.archive.ingest_bytes(image_bytes(), "chat.png", source="chat")
        self.archive.add_reference(item["media_id"], "chat:first")
        first = self.add()
        self.assertFalse(first["重复"])
        self.api.gallery_del(first["名"])
        with patch.object(self.panel, "_聊天索引", return_value=[
            {"谁": "她", "段": [{"type": "image", "媒体ID": first["名"]}]}
        ]):
            self.assertEqual(self.api.gallery_collect()["收了"], 0)
        duplicate = self.add()
        self.assertTrue(duplicate["重复"])
        self.assertEqual(self.api.gallery_status()["总"], 1)
        self.assertEqual(self.api.gallery_status(收纳=True)["总"], 0)
        self.assertEqual(len([r for r in self.archive.references(first["名"])
                              if r["kind"] == "workshop"]), 1)

    def test_workshop_membership_is_atomic_across_archive_instances(self):
        item = self.archive.ingest_bytes(image_bytes(), "chat.png", source="chat")
        other = self.panel.MediaArchive(self.archive.root)
        barrier = threading.Barrier(32)

        def reference(index):
            barrier.wait(timeout=15)
            archive = self.archive if index % 2 else other
            return archive.get_or_create_workshop_reference(item["media_id"], "same.png")

        with ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(reference, range(32)))
        self.assertEqual(sum(r["created"] for r in results), 1)
        self.assertEqual(len(self.archive.list_workshop()), 1)

    def test_periodic_migration_cleanup_respects_deadline_and_retries_failures(self):
        legacy = Path(self.panel.聊媒体目录) / "legacy.png"
        legacy.write_bytes(image_bytes())
        message = Path(self.panel.聊记录文件)
        message.write_text('{"段":[{"type":"image","文件":"legacy.png"}]}\n', encoding="utf-8")
        result = self.archive.migrate_legacy([message], [legacy.parent])
        self.assertEqual(result["status"], "complete", result)
        marker = next((self.archive.migration_dir / "trash").rglob("*.confirmed"))
        source = marker.with_name(marker.name.removesuffix(".confirmed"))
        os.utime(marker, (1000, 1000))
        clock = [1000 + self.archive.get_settings()["trash_days"] * 86400 - 1]
        real_unlink = Path.unlink

        def fail_unlink(path, *args, **kwargs):
            if path == source:
                raise PermissionError("injected file busy")
            return real_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_unlink):
            self.assertEqual(self.archive.cleanup_migration(clock[0] + 2), 0)
        self.assertTrue(self.archive.migration_cleanup_errors())
        self.assertTrue(source.exists())
        ticks = []

        def sleep(_seconds):
            ticks.append(_seconds)
            if len(ticks) == 2:
                self.assertTrue(source.exists(), "期限前不应清理")
                clock[0] += 2
            elif len(ticks) == 3:
                self.assertFalse(source.exists(), "周期维护应补清已到期的副本")
                raise StopIteration()

        with patch("media_archive.core._now", side_effect=lambda: clock[0]), \
                patch.object(self.panel.time, "sleep", side_effect=sleep):
            with self.assertRaises(StopIteration):
                self.panel._媒体后台()
        self.assertEqual(ticks, [1.5, 3600, 3600])


if __name__ == "__main__":
    unittest.main(verbosity=2)
