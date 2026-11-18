# -*- coding: utf-8 -*-
"""媒体归档离线验收：不碰真实工作区数据。"""

from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "panel"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "panel" / "media_archive"))

import io
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import core as core_module
from core import MediaArchive, MediaArchiveError


def make_png(color, size=(320, 240)):
    out = io.BytesIO()
    Image.new("RGBA", size, color).save(out, "PNG")
    return out.getvalue()


def make_gradient(size=(2560, 1440)):
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(size[1]):
        for x in range(size[0]):
            pixels[x, y] = (
                int(x * 255 / max(1, size[0] - 1)),
                int(y * 255 / max(1, size[1] - 1)),
                int((x + y) * 255 / max(1, size[0] + size[1] - 2)),
            )
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 80, 900, 540), outline="white", width=8)
    draw.text((120, 120), "Media archive QA", fill="white")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def make_gif():
    frames = [
        Image.new("RGBA", (96, 64), (255, 40, 120, 255)),
        Image.new("RGBA", (96, 64), (40, 120, 255, 255)),
    ]
    out = io.BytesIO()
    frames[0].save(
        out, "GIF", save_all=True, append_images=frames[1:], duration=80, loop=0
    )
    return out.getvalue()


def make_jpeg():
    image = Image.new("RGB", (480, 320))
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            pixels[x, y] = ((x * 13) % 256, (y * 17) % 256, (x + y * 3) % 256)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=92)
    return out.getvalue()


def make_webp(size=(300, 2800)):
    out = io.BytesIO()
    Image.new("RGB", size, (45, 110, 210)).save(out, "WEBP", quality=95)
    return out.getvalue()


def assert_file(path):
    assert path is not None and path.is_file() and path.stat().st_size > 0


def main():
    with tempfile.TemporaryDirectory(prefix="media-archive-test-") as temp:
        archive = MediaArchive(Path(temp) / "store")

        # 完全相同内容只占一份对象空间。
        first = archive.ingest_bytes(
            make_png((255, 40, 120, 255)),
            "portrait.png",
            "image/png",
            source="chat",
            retention="temporary",
        )
        assert first["archive_bytes"] < first["original_bytes"]
        duplicate = archive.ingest_bytes(
            make_png((255, 40, 120, 255)),
            "same-content.png",
            "image/png",
            source="chat",
            retention="temporary",
        )
        assert first["media_id"] == duplicate["media_id"]
        assert archive.stats()["total"]["count"] == 1

        # 多个临时引用共享同一对象，最后一个引用到期后才进回收站。
        archive.add_reference(
            first["media_id"],
            ref_id="message:one:0",
            kind="chat",
            created_at=100,
            expires_at=110,
        )
        archive.add_reference(
            first["media_id"],
            ref_id="message:two:0",
            kind="chat",
            created_at=101,
            expires_at=120,
        )
        assert archive.expire(115)["moved"] == 0
        assert archive.expire(130)["moved"] == 1
        assert len(archive.list_trash()) == 1
        assert_file(archive.resolve_path(first["media_id"], thumbnail=True))

        # 回收站恢复并永久保留必须把文件移回 objects。
        restored = archive.set_permanent(first["media_id"], True)
        assert restored["status"] == "active"
        assert restored["retention"] == "permanent"
        assert_file(archive.resolve_path(first["media_id"]))
        assert not archive.list_trash()

        # 永久引用阻止自动回收；取消最后一个永久引用后才允许回收。
        guarded = archive.ingest_bytes(
            make_png((30, 180, 80, 255)),
            "guarded.png",
            "image/png",
            source="chat",
            retention="temporary",
        )
        archive.add_reference(
            guarded["media_id"],
            ref_id="guarded:temp",
            kind="chat",
            created_at=100,
            expires_at=110,
        )
        archive.add_reference(
            guarded["media_id"],
            ref_id="guarded:pin",
            kind="workshop",
            permanent=True,
        )
        assert archive.expire(200)["moved"] == 0
        releasable = archive.ingest_bytes(
            make_png((120, 40, 180, 255)),
            "releasable.png",
            "image/png",
            source="chat",
            retention="temporary",
        )
        archive.add_reference(
            releasable["media_id"],
            ref_id="releasable:temp",
            kind="chat",
            created_at=100,
            expires_at=110,
        )
        assert archive.expire(200)["moved"] == 1

        # 回收站宽限期后只删除清晰版本，缩略图和消息索引仍在。
        purge_at = 200 + archive.get_settings()["trash_days"] * 86400 + 1
        assert archive.purge(purge_at)["deleted"] == 1
        deleted = archive.get_media(releasable["media_id"])
        assert deleted["status"] == "deleted"
        assert archive.resolve_path(releasable["media_id"]) is None
        assert_file(archive.resolve_path(releasable["media_id"], thumbnail=True))
        try:
            archive.restore(releasable["media_id"])
        except MediaArchiveError:
            pass
        else:
            raise AssertionError("已彻底清理的媒体不应恢复")

        manual = archive.ingest_bytes(
            make_png((180, 90, 30, 255)),
            "manual-delete.png",
            "image/png",
            source="chat",
        )
        manual_thumb = archive.resolve_path(manual["media_id"], thumbnail=True)
        assert_file(manual_thumb)
        deleted_manual = archive.delete_now(manual["media_id"])
        assert deleted_manual["status"] == "deleted"
        assert archive.resolve_path(manual["media_id"]) is None
        assert_file(archive.resolve_path(manual["media_id"], thumbnail=True))

        # 动图保留原文件，透明 PNG 保留透明属性并生成缩略图。
        animated = archive.ingest_bytes(
            make_gif(), "anim.gif", "image/gif", source="chat"
        )
        assert animated["animated"] is True
        assert animated["archive_mode"] == "original-animated"
        assert animated["extension"] == ".gif"
        transparent = archive.ingest_bytes(
            make_png((0, 0, 0, 0), (800, 600)),
            "alpha.png",
            "image/png",
            source="chat",
        )
        assert transparent["transparent"] is True
        assert_file(archive.resolve_path(transparent["media_id"], thumbnail=True))

        jpeg = archive.ingest_bytes(
            make_jpeg(), "photo.jpg", "image/jpeg", source="chat"
        )
        assert_file(archive.resolve_path(jpeg["media_id"]))
        with Image.open(archive.resolve_path(jpeg["media_id"])) as decoded:
            decoded.verify()
        tall_webp = archive.ingest_bytes(
            make_webp(), "tall.webp", "image/webp", source="chat"
        )
        assert max(tall_webp["width"], tall_webp["height"]) == 2800
        with Image.open(archive.resolve_path(tall_webp["media_id"])) as decoded:
            assert max(decoded.size) <= archive.get_settings()["archive_max_dimension"]
            decoded.verify()

        # 代表性大图记录实际压缩数据，不承诺固定比例。
        sample = archive.ingest_bytes(
            make_gradient(),
            "gradient.png",
            "image/png",
            source="workshop",
            retention="permanent",
            metadata={"text_sensitive": True},
        )
        print(
            "compression sample: original=%d archive=%d thumbnail=%d ratio=%.3f"
            % (
                sample["original_bytes"],
                sample["archive_bytes"],
                sample["thumb_bytes"],
                sample["archive_bytes"] / max(1, sample["original_bytes"]),
            )
        )

        # 迁移保留消息 ID、顺序和内容，重复执行可安全跳过。
        legacy = Path(temp) / "legacy"
        legacy_media = legacy / "media"
        legacy_media.mkdir(parents=True)
        legacy_file = legacy_media / "old.png"
        legacy_file.write_bytes(make_png((10, 20, 30, 255)))
        orphan_file = legacy_media / "orphan.png"
        orphan_file.write_bytes(make_png((40, 50, 60, 255)))
        orphan_id = "m_" + hashlib.sha256(orphan_file.read_bytes()).hexdigest()[:32]
        messages = legacy / "messages.jsonl"
        messages.write_text(
            json.dumps(
                {
                    "消息ID": "msg-old",
                    "谁": "她",
                    "时": 123,
                    "段": [{"type": "image", "文件": "old.png"}],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        migrated = MediaArchive(Path(temp) / "migration-store")
        result = migrated.migrate_legacy(
            message_files=[messages],
            media_dirs=[legacy_media],
        )
        assert result["ok"] and result["rewritten"] == 1
        record = json.loads(messages.read_text(encoding="utf-8").splitlines()[0])
        assert record["消息ID"] == "msg-old"
        assert record["段"][0]["媒体ID"].startswith("m_")
        assert migrated.get_media(orphan_id)["retention"] == "permanent"
        assert not orphan_file.exists()
        again = migrated.migrate_legacy(
            message_files=[messages], media_dirs=[legacy_media]
        )
        assert again["skipped"] is True

        # 故障注入：归档失败时不改消息、不移旧图，也不写完成标记；修复后
        # 重试只创建一个媒体引用并完成迁移。
        fail_root = Path(temp) / "migration-fail"
        fail_media = fail_root / "media"
        fail_media.mkdir(parents=True)
        fail_file = fail_media / "retry.png"
        fail_file.write_bytes(make_png((90, 40, 200, 255)))
        fail_messages = fail_root / "messages.jsonl"
        original_record = {
            "消息ID": "msg-retry",
            "谁": "她",
            "时": 321,
            "段": [{"type": "image", "文件": "retry.png"}],
        }
        fail_messages.write_text(
            json.dumps(original_record, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        class FailingArchive(MediaArchive):
            fail = True

            def _build_archive(self, *args, **kwargs):
                if self.fail:
                    raise MediaArchiveError("注入：归档写入失败")
                return super()._build_archive(*args, **kwargs)

        retry_archive = FailingArchive(Path(temp) / "migration-retry-store")
        failed = retry_archive.migrate_legacy(
            message_files=[fail_messages], media_dirs=[fail_media]
        )
        assert failed["status"] in ("failed", "partial") and failed["failed_items"]
        assert fail_file.is_file()
        assert json.loads(fail_messages.read_text(encoding="utf-8")) == original_record
        retry_archive.fail = False
        recovered = retry_archive.migrate_legacy(
            message_files=[fail_messages], media_dirs=[fail_media]
        )
        assert recovered["status"] == "complete"
        assert not fail_file.exists()
        retried = json.loads(fail_messages.read_text(encoding="utf-8"))
        assert retried["段"][0]["媒体ID"].startswith("m_")
        assert retry_archive.resolve_path(retried["段"][0]["媒体ID"])
        assert retry_archive.migrate_legacy(
            message_files=[fail_messages], media_dirs=[fail_media]
        )["skipped"]

        # 消息原子重写失败时，旧消息和旧图都保留；下一次运行可继续。
        write_root = Path(temp) / "migration-write-fail"
        write_media = write_root / "media"
        write_media.mkdir(parents=True)
        write_file = write_media / "write.png"
        write_file.write_bytes(make_png((20, 180, 70, 255)))
        write_messages = write_root / "messages.jsonl"
        write_messages.write_text(
            json.dumps({
                "消息ID": "msg-write",
                "谁": "她",
                "时": 654,
                "段": [{"type": "image", "文件": "write.png"}],
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        write_archive = MediaArchive(Path(temp) / "migration-write-store")
        real_replace = core_module.os.replace

        def fail_message_replace(source, target):
            if str(source).endswith(".migrating"):
                raise OSError("注入：消息文件发布失败")
            return real_replace(source, target)

        with patch.object(core_module.os, "replace", side_effect=fail_message_replace):
            failed_write = write_archive.migrate_legacy(
                message_files=[write_messages], media_dirs=[write_media]
            )
        assert failed_write["status"] in ("failed", "partial")
        assert write_file.exists()
        assert json.loads(write_messages.read_text(encoding="utf-8"))["段"][0]["文件"] == "write.png"
        done_write = write_archive.migrate_legacy(
            message_files=[write_messages], media_dirs=[write_media]
        )
        assert done_write["status"] == "complete" and not write_file.exists()

        # 进程在消息发布后、旧文件搬移前中断：结果明确为未完成，
        # 原文件仍在；下一次运行通过 durable legacy_path 继续搬移。
        move_root = Path(temp) / "migration-move-fail"
        move_media = move_root / "media"
        move_media.mkdir(parents=True)
        move_file = move_media / "move.png"
        move_file.write_bytes(make_png((220, 80, 20, 255)))
        move_messages = move_root / "messages.jsonl"
        move_messages.write_text(
            json.dumps({
                "消息ID": "msg-move",
                "谁": "她",
                "时": 987,
                "段": [{"type": "image", "文件": "move.png"}],
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        move_archive = MediaArchive(Path(temp) / "migration-move-store")

        def fail_move(*args, **kwargs):
            raise OSError("注入：旧副本搬移中断")

        with patch.object(core_module.shutil, "move", side_effect=fail_move):
            interrupted = move_archive.migrate_legacy(
                message_files=[move_messages], media_dirs=[move_media]
            )
        assert interrupted["status"] == "partial"
        assert move_file.exists()
        published = json.loads(move_messages.read_text(encoding="utf-8"))
        assert published["段"][0]["媒体ID"].startswith("m_")
        completed = move_archive.migrate_legacy(
            message_files=[move_messages], media_dirs=[move_media]
        )
        assert completed["status"] == "complete" and not move_file.exists()
        trash_root = move_archive.migration_dir / "trash"
        trash_dirs = [p for p in trash_root.iterdir() if p.is_dir()]
        assert trash_dirs
        before_cutoff = move_archive.cleanup_migration(
            time.time() + move_archive.get_settings()["trash_days"] * 86400 - 1
        )
        assert before_cutoff == 0
        # Maintenance can remove empty run directories without releasing any media.
        trash_dirs = [p for p in trash_root.iterdir() if p.is_dir()]
        old_time = time.time() - (move_archive.get_settings()["trash_days"] + 1) * 86400
        for directory in trash_dirs:
            os.utime(directory, (old_time, old_time))
            for marker in directory.glob("*.confirmed"):
                os.utime(marker, (old_time, old_time))
        assert move_archive.cleanup_migration(time.time()) >= 1

        # 旧版本留下的未标记迁移副本不会直接删除；若其内容与已验证
        # 归档完全相同，先补确认标记，再从新的宽限期起算。
        reconcile = MediaArchive(Path(temp) / "migration-reconcile-store")
        known = reconcile.ingest_bytes(
            make_png((15, 25, 35, 255)),
            "known.png",
            "image/png",
            source="unknown",
            retention="permanent",
        )
        legacy_trash = reconcile.migration_dir / "trash" / "old-run"
        legacy_trash.mkdir(parents=True)
        legacy_copy = legacy_trash / "known.png"
        legacy_copy.write_bytes(make_png((15, 25, 35, 255)))
        old_time = time.time() - (reconcile.get_settings()["trash_days"] + 1) * 86400
        os.utime(legacy_copy, (old_time, old_time))
        assert reconcile.cleanup_migration(time.time()) == 0
        marker = legacy_copy.with_name(legacy_copy.name + ".confirmed")
        assert marker.exists() and legacy_copy.exists()
        os.utime(marker, (old_time, old_time))
        assert reconcile.cleanup_migration(time.time()) == 1
        assert not legacy_copy.exists() and not marker.exists()

        stats = archive.stats()
        assert stats["total"]["count"] >= 3
        print("media archive acceptance: PASS")


if __name__ == "__main__":
    main()
