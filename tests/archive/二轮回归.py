import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "panel"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "panel" / "media_archive"))
"""迁移不可读消息、无效 ID 和静态图尺寸的隔离故障回归。"""
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageOps

from core import MediaArchive, MediaArchiveError


class ArchiveRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="archive-regression-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = MediaArchive(self.root / "archive")
        self.legacy = self.root / "legacy"
        self.legacy.mkdir()
        self.image = self.legacy / "old.png"
        Image.new("RGB", (80, 60), "teal").save(self.image)
        self.message = self.root / "messages.jsonl"
        self.record = {"消息ID": "same-id", "时": 123, "谁": "她",
                       "段": [{"type": "image", "文件": "old.png"}]}
        self.write_record()

    def write_record(self):
        self.message.write_text(json.dumps(self.record) + "\n", encoding="utf-8")

    def migrate(self):
        return self.archive.migrate_legacy([self.message], [self.legacy])

    def assert_retry(self):
        result = self.migrate()
        self.assertEqual(result["status"], "complete", result)
        record = json.loads(self.message.read_text(encoding="utf-8"))
        self.assertEqual(record["消息ID"], "same-id")
        self.assertEqual((record["时"], record["谁"]), (123, "她"))
        segment = record["段"][0]
        self.assertNotIn("文件", segment)
        self.assertTrue(self.archive.resolve_path(segment["媒体ID"]))
        self.assertFalse(self.image.exists())
        refs = self.archive.references(segment["媒体ID"])
        self.assertEqual(len([r for r in refs if r["kind"] == "chat"]), 1)
        self.assertTrue(self.migrate()["skipped"])

    def test_unreadable_message_preserves_all_sources_then_retries(self):
        # 包括已归档但未搬走的源图，不能靠 durable metadata 绕过阻塞。
        self.archive.ingest_file(self.image, retention="permanent",
                                 metadata={"legacy_path": str(self.image.resolve())})
        real_read = Path.read_text

        def fail_read(path, *args, **kwargs):
            if path == self.message:
                raise OSError("injected message read failure")
            return real_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", fail_read):
            result = self.migrate()
        self.assertEqual(result["moved_old"], 0, result)
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["status"], "complete")
        self.assertTrue(any(r["kind"] == "message-read" for r in result["failed_items"]))
        with Image.open(self.image) as image:
            image.load()
        self.assertEqual(json.loads(self.message.read_text()), self.record)
        self.assert_retry()

    def test_invalid_json_line_preserves_sources_then_retries(self):
        self.message.write_text(self.message.read_text() + "{invalid\n", encoding="utf-8")
        result = self.migrate()
        self.assertFalse(result["ok"], result)
        self.assertNotEqual(result["status"], "complete")
        self.assertTrue(any(r["kind"] == "message-parse" for r in result["failed_items"]))
        self.assertEqual(result["moved_old"], 0)
        self.assertTrue(self.image.is_file())
        self.assertEqual(json.loads(self.message.read_text(encoding="utf-8").splitlines()[0]),
                         self.record)
        self.write_record()
        self.assert_retry()

    def test_old_completion_marker_does_not_bypass_safe_preflight(self):
        with self.archive._connect() as connection:
            connection.execute(
                "INSERT INTO schema_meta(key,value) VALUES('legacy_migrated_v2','1')"
            )
        self.message.write_text(self.message.read_text() + "{invalid\n", encoding="utf-8")
        result = self.migrate()
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["moved_old"], 0)
        self.assertTrue(self.image.exists())
        self.write_record()
        self.assert_retry()

    def test_invalid_id_falls_back_and_archive_failure_keeps_old_reference(self):
        self.record["段"][0]["媒体ID"] = "m_missing"
        self.write_record()
        with patch.object(self.archive, "_build_archive",
                          side_effect=MediaArchiveError("injected archive failure")):
            result = self.migrate()
        self.assertFalse(result["ok"])
        self.assertEqual(result["moved_old"], 0)
        self.assertTrue(self.image.is_file())
        self.assertEqual(json.loads(self.message.read_text()), self.record)
        self.assert_retry()

    def test_invalid_id_with_valid_source_is_converted(self):
        self.record["段"][0]["媒体ID"] = "m_missing"
        self.write_record()
        self.assert_retry()

    def test_existing_id_with_unreadable_archive_falls_back(self):
        item = self.archive.ingest_bytes(b"unreadable", "bad.bin")
        self.archive.resolve_path(item["media_id"]).unlink()
        self.record["段"][0]["媒体ID"] = item["media_id"]
        self.write_record()
        self.assert_retry()

    def test_shared_and_later_sources_remain_blocked_when_message_fails(self):
        later = self.legacy / "later.png"
        Image.new("RGB", (80, 60), "blue").save(later)
        self.record["段"].append({"type": "image", "文件": "later.png"})
        self.write_record()
        self.archive.ingest_file(self.image, retention="permanent",
                                 metadata={"legacy_path": str(self.image.resolve())})
        with patch.object(self.archive, "add_reference",
                          side_effect=MediaArchiveError("injected reference failure")):
            result = self.migrate()
        self.assertFalse(result["ok"])
        self.assertEqual(result["moved_old"], 0, result)
        self.assertTrue(self.image.exists())
        self.assertTrue(later.exists())
        self.archive.cleanup_migration(10**12)
        self.assertTrue(self.image.exists())
        self.assertTrue(later.exists())
        self.assertEqual(json.loads(self.message.read_text()), self.record)
        self.assertEqual(self.migrate()["status"], "complete")
        self.assertFalse(self.image.exists())
        self.assertFalse(later.exists())

    def test_interruption_before_old_move_reopens_and_resumes_without_extra_refs(self):
        with patch("core.shutil.move", side_effect=KeyboardInterrupt("injected interruption")):
            with self.assertRaises(KeyboardInterrupt):
                self.migrate()
        self.assertTrue(self.image.exists())
        published = json.loads(self.message.read_text(encoding="utf-8"))
        media_id = published["段"][0]["媒体ID"]
        self.assertTrue(self.archive.resolve_path(media_id))
        self.archive = MediaArchive(self.archive.root)
        self.assertEqual(self.migrate()["status"], "complete")
        self.assertFalse(self.image.exists())
        self.assertEqual(len(self.archive.references(media_id)), 1)

    def test_actual_archive_dimensions_for_noisy_oversized_formats(self):
        image = Image.frombytes("RGB", (2700, 2700), random.Random(42).randbytes(2700 * 2700 * 3))
        for fmt, options in [("WEBP", {"quality": 1}), ("JPEG", {"quality": 2}), ("PNG", {})]:
            with self.subTest(format=fmt):
                payload = io.BytesIO()
                image.save(payload, fmt, **options)
                item = self.archive.ingest_bytes(payload.getvalue(), "large." + fmt.lower())
                path = self.archive.resolve_path(item["media_id"])
                with Image.open(path) as decoded:
                    decoded.load()
                    self.assertLessEqual(max(decoded.size), 2560)
                    self.assertEqual(max(decoded.size), 2560)

    def test_exact_limit_and_exif_orientation(self):
        image = Image.new("RGB", (2560, 32), "blue")
        for fmt in ("WEBP", "JPEG", "PNG"):
            with self.subTest(format=fmt):
                payload = io.BytesIO()
                image.save(payload, fmt)
                item = self.archive.ingest_bytes(payload.getvalue(), "limit." + fmt.lower())
                with Image.open(self.archive.resolve_path(item["media_id"])) as decoded:
                    decoded.load()
                    self.assertEqual(decoded.size, (2560, 32))
        payload = io.BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (2700, 100), "green").save(payload, "JPEG", exif=exif)
        item = self.archive.ingest_bytes(payload.getvalue(), "oriented.jpg")
        with Image.open(self.archive.resolve_path(item["media_id"])) as decoded:
            self.assertLessEqual(max(decoded.size), 2560)
            self.assertGreater(ImageOps.exif_transpose(decoded).height, decoded.width)

    def test_oversized_fallback_must_be_rejected_before_database_acceptance(self):
        payload = io.BytesIO()
        Image.new("RGB", (2700, 64), "red").save(payload, "PNG")

        def oversized(source, *args):
            target = self.archive.tmp_dir / "injected.png"
            target.write_bytes(source.read_bytes())
            return target, ".png", "original-fallback"

        with patch.object(self.archive, "_build_archive", side_effect=oversized):
            with self.assertRaises(MediaArchiveError):
                self.archive.ingest_bytes(payload.getvalue(), "oversized.png")
        self.assertEqual(self.archive.list_media(), [])

    def test_oversized_encoder_failure_is_explicit_and_keeps_source(self):
        with Image.open(self.image) as image:
            image.resize((2700, 64)).save(self.image)
        original_save = Image.Image.save

        def fail_webp(image, path, format=None, **options):
            if format == "WEBP":
                raise OSError("injected encoder failure")
            return original_save(image, path, format=format, **options)

        with patch.object(Image.Image, "save", fail_webp):
            with self.assertRaises(MediaArchiveError):
                self.archive.ingest_file(self.image)
        self.assertTrue(self.image.is_file())
        self.assertEqual(self.archive.list_media(), [])
        self.assertFalse(list(self.archive.tmp_dir.iterdir()))

    def test_animation_original_dimension_is_an_explicit_exception(self):
        for fmt in ("GIF", "WEBP", "PNG"):
            with self.subTest(format=fmt):
                payload = io.BytesIO()
                first = Image.new("RGB", (2700, 8), "red")
                first.save(payload, fmt, save_all=True,
                           append_images=[Image.new("RGB", first.size, "blue")],
                           duration=60, loop=0)
                item = self.archive.ingest_bytes(payload.getvalue(), "animated." + fmt.lower())
                self.assertEqual(item["archive_mode"], "original-animated")
                with Image.open(self.archive.resolve_path(item["media_id"])) as decoded:
                    self.assertEqual(decoded.size, (2700, 8))
                    self.assertEqual(decoded.n_frames, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
