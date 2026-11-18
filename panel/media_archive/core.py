# -*- coding: utf-8 -*-
"""独立媒体归档。

这个模块只依赖 Python 标准库；如果安装了 Pillow，会额外提供图片方向修正、
WebP 归档和缩略图。媒体 ID 由内容 SHA-256 派生，不暴露任何机器绝对路径。
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping


class MediaArchiveError(RuntimeError):
    """归档操作失败。"""


DEFAULT_SETTINGS = {
    "temporary_days": 30,
    "trash_days": 7,
    "auto_expire": True,
    "archive_max_dimension": 2560,
    "archive_quality": 90,
    "thumbnail_max_dimension": 560,
    "thumbnail_quality": 82,
}

IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}
ANIMATED_EXTENSIONS = {".gif", ".apng", ".webp"}
LEGACY_MIGRATION_MARKER = "legacy_migrated_v3"


class _ManagedConnection(sqlite3.Connection):
    """SQLite context manager that also closes the handle on Windows."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _now() -> float:
    return time.time()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return {} if fallback is None else fallback
    try:
        return json.loads(value)
    except Exception:
        return {} if fallback is None else fallback


def _normal_ext(filename: str, mime: str = "") -> str:
    ext = Path(str(filename or "")).suffix.lower()
    if not ext:
        ext = mimetypes.guess_extension(str(mime or "").split(";", 1)[0].strip()) or ".bin"
    if len(ext) > 12 or any(ch in ext for ch in "/\\\x00"):
        ext = ".bin"
    return ext


def _normal_mime(filename: str, mime: str = "") -> str:
    clean = str(mime or "").split(";", 1)[0].strip().lower()
    if clean:
        return clean
    return mimetypes.guess_type(str(filename or ""))[0] or "application/octet-stream"


def _safe_name(name: str, default: str = "media") -> str:
    name = os.path.basename(str(name or "")).strip()
    if not name:
        return default
    return name[:240]


def _temp_path(directory: Path, prefix: str, suffix: str) -> Path:
    """Create a named temporary file and close the descriptor immediately.

    ``mkstemp`` deliberately returns an open descriptor.  Keeping that
    descriptor alive on Windows prevents the later atomic ``os.replace`` or
    cleanup from succeeding reliably.
    """
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=directory)
    os.close(fd)
    return Path(name)


class MediaArchive:
    """内容寻址的媒体存储与生命周期管理。

    ``root`` 是产品自己的数据目录。调用者只需要保存返回的 ``media_id``，
    不应把 ``archive_rel`` 等内部字段写入消息或配置。
    """

    schema_version = 1

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).expanduser().resolve()
        self.db_path = self.root / "index.sqlite3"
        self.objects_dir = self.root / "objects"
        self.thumbs_dir = self.root / "thumbnails"
        self.trash_dir = self.root / "trash"
        self.tmp_dir = self.root / "tmp"
        self.migration_dir = self.root / "migration"
        for directory in (
            self.root,
            self.objects_dir,
            self.thumbs_dir,
            self.trash_dir,
            self.tmp_dir,
            self.migration_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._migration_cleanup_errors: list[dict[str, str]] = []
        self._init_db()

    # ------------------------------------------------------------------
    # SQLite and schema
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30,
            isolation_level=None,
            factory=_ManagedConnection,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS media (
                    media_id TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    retention TEXT NOT NULL CHECK (retention IN ('permanent','temporary')),
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','trash','deleted')),
                    mime TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    archive_rel TEXT,
                    trash_rel TEXT,
                    thumb_rel TEXT,
                    original_bytes INTEGER NOT NULL DEFAULT 0,
                    archive_bytes INTEGER NOT NULL DEFAULT 0,
                    thumb_bytes INTEGER NOT NULL DEFAULT 0,
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    animated INTEGER NOT NULL DEFAULT 0,
                    transparent INTEGER NOT NULL DEFAULT 0,
                    archive_mode TEXT NOT NULL DEFAULT 'original',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    trash_at REAL,
                    trash_until REAL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS media_status_idx ON media(status);
                CREATE INDEX IF NOT EXISTS media_retention_idx ON media(retention);
                CREATE TABLE IF NOT EXISTS refs (
                    ref_id TEXT PRIMARY KEY,
                    media_id TEXT NOT NULL REFERENCES media(media_id),
                    kind TEXT NOT NULL,
                    collection TEXT NOT NULL DEFAULT 'main',
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    permanent INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    display_name TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS refs_media_idx ON refs(media_id);
                CREATE INDEX IF NOT EXISTS refs_expiry_idx ON refs(active, expires_at);
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    media_id TEXT,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_path TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                    (key, _json(value)),
                )
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('version',?)",
                (str(self.schema_version),),
            )

    def _row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["metadata"] = _load_json(result.pop("metadata_json", "{}"))
        result["permanent"] = result.get("retention") == "permanent"
        result["animated"] = bool(result.get("animated"))
        result["transparent"] = bool(result.get("transparent"))
        return result

    def _media_row(self, conn: sqlite3.Connection, media_id: str) -> dict[str, Any] | None:
        return self._row(
            conn.execute("SELECT * FROM media WHERE media_id=?", (str(media_id),)).fetchone()
        )

    def _settings_conn(self, conn: sqlite3.Connection) -> dict[str, Any]:
        values = dict(DEFAULT_SETTINGS)
        for row in conn.execute("SELECT key,value FROM settings"):
            raw = _load_json(row["value"], row["value"])
            values[row["key"]] = raw
        return values

    # ------------------------------------------------------------------
    # File and image helpers
    # ------------------------------------------------------------------
    def _object_path(self, media_id: str, extension: str) -> tuple[str, Path]:
        shard = media_id[2:4] or "00"
        rel = Path("objects") / shard / f"{media_id}{extension}"
        return rel.as_posix(), self.root / rel

    def _thumb_path(self, media_id: str) -> tuple[str, Path]:
        shard = media_id[2:4] or "00"
        rel = Path("thumbnails") / shard / f"{media_id}.webp"
        return rel.as_posix(), self.root / rel

    @staticmethod
    def _sha256_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def _image_info(path: Path) -> dict[str, Any]:
        info: dict[str, Any] = {
            "is_image": False,
            "width": 0,
            "height": 0,
            "animated": False,
            "transparent": False,
            "format": "",
            "orientation": 1,
        }
        try:
            from PIL import Image, ImageOps

            with Image.open(path) as image:
                info["is_image"] = True
                info["animated"] = bool(getattr(image, "is_animated", False))
                info["format"] = str(image.format or "").upper()
                try:
                    info["orientation"] = int(image.getexif().get(274) or 1)
                except Exception:
                    info["orientation"] = 1
                oriented = ImageOps.exif_transpose(image) if not info["animated"] else image
                info["width"], info["height"] = oriented.size
                bands = image.getbands()
                info["transparent"] = (
                    "A" in bands
                    or "transparency" in image.info
                    or image.mode in ("P", "LA")
                )
        except Exception:
            return info
        return info

    def _build_archive(
        self,
        source_path: Path,
        media_id: str,
        display_name: str,
        image_info: Mapping[str, Any],
        settings: Mapping[str, Any],
        metadata: Mapping[str, Any] | None,
    ) -> tuple[Path, str, str]:
        """Return temporary archive path, extension, and archive mode."""
        ext = _normal_ext(display_name)
        if not image_info.get("is_image") or (metadata or {}).get("preserve_original"):
            out = _temp_path(self.tmp_dir, "archive-", ext)
            shutil.copyfile(source_path, out)
            return out, ext, "original-preserved" if (metadata or {}).get("preserve_original") else "original"

        fmt = str(image_info.get("format") or "").upper()
        animated = bool(image_info.get("animated"))
        needs_resize = max(
            int(image_info.get("width") or 0), int(image_info.get("height") or 0)
        ) > int(settings["archive_max_dimension"])
        # 动图按原文件保留（包括原尺寸），避免静态缩放丢帧或改变时序。
        if animated or (fmt in {"GIF", "APNG"} and not needs_resize):
            out = _temp_path(self.tmp_dir, "archive-", ext)
            shutil.copyfile(source_path, out)
            return out, ext, "original-animated"

        # A WebP input is already a compressed archive candidate. Do not encode it
        # again, but only when its oriented longest edge is within the limit.
        if fmt == "WEBP" and not needs_resize:
            out = _temp_path(self.tmp_dir, "archive-", ".webp")
            shutil.copyfile(source_path, out)
            return out, ".webp", "original-webp"

        out = None
        try:
            from PIL import Image, ImageOps

            source_size = source_path.stat().st_size
            out = _temp_path(self.tmp_dir, "archive-", ".webp")
            sensitive = bool((metadata or {}).get("text_sensitive"))
            quality = 95 if sensitive else int(settings["archive_quality"])
            with Image.open(source_path) as image:
                converted = ImageOps.exif_transpose(image)
                has_alpha = (
                    "A" in converted.getbands()
                    or "transparency" in converted.info
                    or converted.mode in ("P", "LA")
                )
                converted = converted.convert("RGBA" if has_alpha else "RGB")
                max_dim = int(settings["archive_max_dimension"])
                if max(converted.size) > max_dim:
                    converted.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                converted.save(out, "WEBP", quality=quality, method=6)
            compressed_size = out.stat().st_size
            # Do not replace a small source with a larger file or a negligible saving.
            if not needs_resize and compressed_size >= source_size * 0.98:
                out.unlink(missing_ok=True)
                fallback = _temp_path(self.tmp_dir, "archive-", ext)
                shutil.copyfile(source_path, fallback)
                return fallback, ext, "original-no-gain"
            return out, ".webp", "webp-lossy"
        except Exception as exc:
            if out is not None:
                out.unlink(missing_ok=True)
            if needs_resize:
                raise MediaArchiveError("静态图缩小归档失败，不能回退为超限原件") from exc
            out = _temp_path(self.tmp_dir, "archive-", ext)
            shutil.copyfile(source_path, out)
            return out, ext, "original-fallback"

    def _build_thumbnail(
        self,
        source_path: Path,
        media_id: str,
        image_info: Mapping[str, Any],
        settings: Mapping[str, Any],
    ) -> tuple[Path | None, int]:
        if not image_info.get("is_image"):
            return None, 0
        try:
            from PIL import Image, ImageOps

            rel, final = self._thumb_path(media_id)
            del rel
            temp = _temp_path(self.tmp_dir, "thumb-", ".webp")
            with Image.open(source_path) as image:
                if getattr(image, "is_animated", False):
                    image.seek(0)
                converted = ImageOps.exif_transpose(image)
                has_alpha = (
                    "A" in converted.getbands()
                    or "transparency" in converted.info
                    or converted.mode in ("P", "LA")
                )
                converted = converted.convert("RGBA" if has_alpha else "RGB")
                max_dim = int(settings["thumbnail_max_dimension"])
                converted.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                converted.save(temp, "WEBP", quality=int(settings["thumbnail_quality"]), method=4)
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp, final)
            return final, final.stat().st_size
        except Exception:
            return None, 0

    @staticmethod
    def _verify_readable(path: Path, image_info: Mapping[str, Any]) -> None:
        if not path.is_file() or path.stat().st_size <= 0:
            raise MediaArchiveError("归档文件为空或未写入")
        with path.open("rb") as handle:
            if not handle.read(32):
                raise MediaArchiveError("归档文件不可读取")
        if image_info.get("is_image"):
            try:
                from PIL import Image

                with Image.open(path) as image:
                    # 尺寸必须来自实际文件；数据库中的宽高保留原作品信息。
                    limit = image_info.get("archive_max_dimension")
                    if (limit and not getattr(image, "is_animated", False)
                            and max(image.size) > int(limit)):
                        raise MediaArchiveError("归档静态图片最长边超过配置上限")
                    image.verify()
                with Image.open(path) as image:
                    image.load()
            except Exception as exc:
                raise MediaArchiveError(f"归档图片校验失败: {exc}") from exc

    # ------------------------------------------------------------------
    # Ingest and references
    # ------------------------------------------------------------------
    def ingest_bytes(
        self,
        data: bytes | bytearray | memoryview,
        filename: str = "",
        mime: str = "",
        source: str = "unknown",
        retention: str = "temporary",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = bytes(data)
        if not payload:
            raise MediaArchiveError("媒体内容为空")
        with tempfile.NamedTemporaryFile(
            dir=self.tmp_dir, prefix="source-", suffix=_normal_ext(filename, mime), delete=False
        ) as handle:
            source_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            return self._ingest_path(
                source_path,
                filename=filename,
                mime=mime,
                source=source,
                retention=retention,
                metadata=metadata,
            )
        finally:
            source_path.unlink(missing_ok=True)

    def ingest_base64(
        self,
        value: str,
        filename: str = "",
        mime: str = "",
        source: str = "unknown",
        retention: str = "temporary",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw = str(value or "")
        if raw.startswith("data:") and "," in raw:
            header, raw = raw.split(",", 1)
            if not mime and ";" in header:
                mime = header[5:].split(";", 1)[0]
        try:
            payload = base64.b64decode(raw, validate=True)
        except Exception as exc:
            raise MediaArchiveError("Base64 媒体内容无效") from exc
        return self.ingest_bytes(payload, filename, mime, source, retention, metadata)

    def ingest_file(
        self,
        path: str | os.PathLike[str],
        filename: str = "",
        mime: str = "",
        source: str = "unknown",
        retention: str = "temporary",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_path = Path(path).expanduser()
        if not source_path.is_file():
            raise MediaArchiveError(f"媒体文件不存在: {source_path}")
        return self._ingest_path(
            source_path,
            filename=filename or source_path.name,
            mime=mime,
            source=source,
            retention=retention,
            metadata=metadata,
        )

    def _ingest_path(
        self,
        source_path: Path,
        filename: str,
        mime: str,
        source: str,
        retention: str,
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        filename = _safe_name(filename, "media")
        mime = _normal_mime(filename, mime)
        retention = "permanent" if retention == "permanent" else "temporary"
        metadata_dict = dict(metadata or {})
        digest, source_size = self._sha256_file(source_path)
        media_id = "m_" + digest[:32]
        image_info = self._image_info(source_path)
        with self._lock:
            with self._connect() as conn:
                existing = self._media_row(conn, media_id)
                settings = self._settings_conn(conn)
                was_deleted = bool(existing and existing["status"] == "deleted")
                if existing and existing["status"] != "deleted":
                    if metadata_dict.get("preserve_original") and existing.get("archive_mode") != "original-preserved":
                        # A new explicit upload can restore original bytes without changing its ID or references.
                        restored, ext, mode = self._build_archive(
                            source_path, media_id, filename, image_info, settings, metadata_dict)
                        rel, dest = self._object_path(media_id, ext)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(restored, dest)
                        conn.execute(
                            "UPDATE media SET archive_rel=?,archive_bytes=?,archive_mode=?,"
                            "extension=?,status='active',trash_rel=NULL,updated_at=? WHERE media_id=?",
                            (rel, source_size, mode, ext, _now(), media_id))
                    if existing["retention"] != "permanent" and retention == "permanent":
                        conn.execute(
                            "UPDATE media SET retention='permanent', updated_at=? WHERE media_id=?",
                            (_now(), media_id),
                        )
                    if source != "unknown" and existing["source"] == "unknown":
                        conn.execute(
                            "UPDATE media SET source=?, updated_at=? WHERE media_id=?",
                            (source, _now(), media_id),
                        )
                    if metadata_dict:
                        merged = dict(existing.get("metadata") or {})
                        merged.update(metadata_dict)
                        conn.execute(
                            "UPDATE media SET metadata_json=?, updated_at=? WHERE media_id=?",
                            (_json(merged), _now(), media_id),
                        )
                    result = self.get_media(media_id) or existing
                    result["created"] = False
                    result["deduplicated"] = True
                    return result

                job_id = "job_" + uuid.uuid4().hex
                now = _now()
                conn.execute(
                    "INSERT INTO jobs(job_id,media_id,kind,status,source_path,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (job_id, media_id, "ingest", "running", str(source_path), now, now),
                )
                archive_temp: Path | None = None
                archive_path: Path | None = None
                thumb_path: Path | None = None
                try:
                    archive_temp, archive_ext, archive_mode = self._build_archive(
                        source_path,
                        media_id,
                        filename,
                        image_info,
                        settings,
                        metadata_dict,
                    )
                    archive_rel, archive_path = self._object_path(media_id, archive_ext)
                    archive_path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(archive_temp, archive_path)
                    archive_temp = None
                    self._verify_readable(
                        archive_path,
                        {**image_info, "archive_max_dimension":
                         None if metadata_dict.get("preserve_original") else settings["archive_max_dimension"]},
                    )
                    thumb_path, thumb_size = self._build_thumbnail(
                        source_path, media_id, image_info, settings
                    )
                    thumb_rel = self._thumb_path(media_id)[0] if thumb_path else None
                    metadata_dict.setdefault("original_filename", filename)
                    metadata_dict.setdefault("original_mime", mime)
                    metadata_dict.setdefault("original_sha256", digest)
                    metadata_dict.setdefault("original_size", source_size)
                    conn.execute(
                        """
                        INSERT INTO media(
                            media_id,sha256,source,retention,status,mime,extension,display_name,
                            archive_rel,trash_rel,thumb_rel,original_bytes,archive_bytes,thumb_bytes,
                            width,height,animated,transparent,archive_mode,created_at,updated_at,
                            metadata_json,error
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(media_id) DO UPDATE SET
                            source=excluded.source,
                            retention=CASE WHEN media.retention='permanent'
                                OR excluded.retention='permanent' THEN 'permanent'
                                ELSE excluded.retention END,
                            status='active',
                            mime=excluded.mime,
                            extension=excluded.extension,
                            display_name=excluded.display_name,
                            archive_rel=excluded.archive_rel,
                            trash_rel=NULL,
                            thumb_rel=excluded.thumb_rel,
                            original_bytes=excluded.original_bytes,
                            archive_bytes=excluded.archive_bytes,
                            thumb_bytes=excluded.thumb_bytes,
                            width=excluded.width,
                            height=excluded.height,
                            animated=excluded.animated,
                            transparent=excluded.transparent,
                            archive_mode=excluded.archive_mode,
                            updated_at=excluded.updated_at,
                            trash_at=NULL,
                            trash_until=NULL,
                            metadata_json=excluded.metadata_json,
                            error=NULL
                        """,
                        (
                            media_id,
                            digest,
                            source,
                            retention,
                            "active",
                            mime,
                            archive_ext,
                            filename,
                            archive_rel,
                            None,
                            thumb_rel,
                            source_size,
                            archive_path.stat().st_size,
                            thumb_size,
                            int(image_info.get("width") or 0),
                            int(image_info.get("height") or 0),
                            int(bool(image_info.get("animated"))),
                            int(bool(image_info.get("transparent"))),
                            archive_mode,
                            now,
                            now,
                            _json(metadata_dict),
                            None,
                        ),
                    )
                    conn.execute(
                        "UPDATE jobs SET status='done',attempts=attempts+1,updated_at=? WHERE job_id=?",
                        (_now(), job_id),
                    )
                    conn.commit()
                    result = self.get_media(media_id) or {}
                    result["created"] = existing is None or was_deleted
                    result["deduplicated"] = not result["created"]
                    return result
                except Exception as exc:
                    conn.execute(
                        "UPDATE jobs SET status='failed',attempts=attempts+1,error=?,updated_at=? "
                        "WHERE job_id=?",
                        (str(exc)[:500], _now(), job_id),
                    )
                    conn.commit()
                    if archive_temp:
                        archive_temp.unlink(missing_ok=True)
                    if archive_path:
                        archive_path.unlink(missing_ok=True)
                    if thumb_path:
                        thumb_path.unlink(missing_ok=True)
                    raise

    def add_reference(
        self,
        media_id: str,
        ref_id: str | None = None,
        kind: str = "chat",
        collection: str = "main",
        created_at: float | None = None,
        expires_at: float | None = None,
        permanent: bool = False,
        display_name: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        ref_id = str(ref_id or ("ref_" + uuid.uuid4().hex))
        created_at = float(created_at if created_at is not None else _now())
        with self._lock:
            with self._connect() as conn:
                moved_file = False
                trashed = active = None
                media = conn.execute(
                    "SELECT retention,status FROM media WHERE media_id=?", (str(media_id),)
                ).fetchone()
                if media is None:
                    raise MediaArchiveError(f"未知媒体 ID: {media_id}")
                if media["status"] == "deleted":
                    raise MediaArchiveError(f"媒体已彻底删除: {media_id}")
                if media["status"] == "trash":
                    stored = conn.execute(
                        "SELECT archive_rel,trash_rel FROM media WHERE media_id=?",
                        (str(media_id),),
                    ).fetchone()
                    if not (stored and stored["archive_rel"] and stored["trash_rel"]):
                        raise MediaArchiveError("回收站文件缺少路径")
                    trashed = self.root / stored["trash_rel"]
                    active = self.root / stored["archive_rel"]
                    if not trashed.is_file():
                        raise MediaArchiveError("回收站文件已经不见了")
                    active.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(trashed, active)
                    moved_file = True
                    media = {"retention": media["retention"], "status": "active"}
                # 媒体整体可以因为另一个作品/收藏引用而永久保留，
                # 但当前这条聊天引用仍应按自己的临时期限到期。
                is_permanent = bool(permanent)
                if is_permanent:
                    expires_at = None
                elif expires_at is None:
                    expires_at = created_at + int(
                        self._settings_conn(conn).get("temporary_days", 30)
                    ) * 86400
                try:
                    if moved_file:
                        conn.execute(
                            "UPDATE media SET status='active',trash_rel=NULL,trash_at=NULL,"
                            "trash_until=NULL,updated_at=? WHERE media_id=?",
                            (_now(), str(media_id)),
                        )
                    conn.execute(
                        """
                        INSERT INTO refs(
                            ref_id,media_id,kind,collection,created_at,expires_at,permanent,
                            active,display_name,metadata_json
                        ) VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(ref_id) DO UPDATE SET
                            media_id=excluded.media_id,
                            kind=excluded.kind,
                            collection=excluded.collection,
                            created_at=excluded.created_at,
                            expires_at=excluded.expires_at,
                            permanent=excluded.permanent,
                            active=1,
                            display_name=excluded.display_name,
                            metadata_json=excluded.metadata_json
                        """,
                        (
                            ref_id,
                            str(media_id),
                            str(kind or "chat"),
                            str(collection or "main"),
                            created_at,
                            expires_at,
                            int(is_permanent),
                            1,
                            str(display_name or ""),
                            _json(metadata),
                        ),
                    )
                    if is_permanent:
                        conn.execute(
                            "UPDATE media SET retention='permanent',status='active',updated_at=? WHERE media_id=?",
                            (_now(), str(media_id)),
                        )
                    conn.commit()
                except Exception:
                    if moved_file and trashed is not None and active is not None:
                        try:
                            if active.is_file() and not trashed.exists():
                                trashed.parent.mkdir(parents=True, exist_ok=True)
                                os.replace(active, trashed)
                        except Exception:
                            pass
                    raise
        return ref_id

    def get_or_create_workshop_reference(
        self,
        media_id: str,
        display_name: str = "",
        metadata: Mapping[str, Any] | None = None,
        restore_stored: bool = True,
    ) -> dict[str, Any]:
        """原子取得作品引用；created 只属于赢得首次创建的请求。

        显式上传可把收纳作品恢复到画册；批量从聊天收取应传
        restore_stored=False，尊重用户已经作出的收纳决定。
        """
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            moved = False
            source = target = None
            try:
                media = self._media_row(conn, media_id)
                if not media or media["status"] == "deleted":
                    raise MediaArchiveError("作品清晰版本不存在")
                path = self.resolve_path(media_id)
                if path is None:
                    raise MediaArchiveError("作品归档文件不存在")
                self._verify_readable(path, {
                    "is_image": bool(media.get("width") or media.get("height")),
                })
                existing = conn.execute(
                    "SELECT ref_id,collection FROM refs "
                    "WHERE media_id=? AND kind='workshop' AND active=1 "
                    "ORDER BY created_at,ref_id LIMIT 1",
                    (media_id,),
                ).fetchone()
                if media["status"] == "trash":
                    source = path
                    target = self.root / media["archive_rel"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(source, target)
                    moved = True
                collection = "gallery"
                if existing:
                    ref_id = existing["ref_id"]
                    collection = "gallery" if restore_stored else existing["collection"]
                    if restore_stored:
                        conn.execute(
                            "UPDATE refs SET collection=? "
                            "WHERE media_id=? AND kind='workshop' AND active=1 AND collection<>?",
                            (collection, media_id, collection),
                        )
                else:
                    ref_id = "workshop:" + media_id
                    conn.execute(
                        """
                        INSERT INTO refs(ref_id,media_id,kind,collection,created_at,
                                         expires_at,permanent,active,display_name,metadata_json)
                        VALUES(?,?,'workshop','gallery',?,NULL,1,1,?,?)
                        ON CONFLICT(ref_id) DO UPDATE SET active=1,permanent=1,
                            collection='gallery',expires_at=NULL,created_at=excluded.created_at,
                            display_name=excluded.display_name,metadata_json=excluded.metadata_json
                        """,
                        (ref_id, media_id, _now(), str(display_name), _json(metadata)),
                    )
                if media["retention"] != "permanent" or media["status"] != "active":
                    conn.execute(
                        "UPDATE media SET retention='permanent',status='active',trash_rel=NULL,"
                        "trash_at=NULL,trash_until=NULL,updated_at=? WHERE media_id=?",
                        (_now(), media_id),
                    )
                conn.commit()
                return {"ref_id": ref_id, "created": existing is None, "collection": collection}
            except BaseException:
                conn.rollback()
                if moved and source is not None and target is not None:
                    os.replace(target, source)
                raise

    def set_permanent(self, media_id: str, permanent: bool = True) -> dict[str, Any]:
        with self._lock:
            with self._connect() as conn:
                media = self._media_row(conn, media_id)
                if not media:
                    raise MediaArchiveError("媒体不存在")
                if media["status"] == "deleted":
                    raise MediaArchiveError("清晰版本已经彻底清理，无法恢复")
                if permanent:
                    moved = False
                    source = target = None
                    if media["status"] == "trash":
                        if not media.get("archive_rel") or not media.get("trash_rel"):
                            raise MediaArchiveError("回收站文件缺少路径")
                        source = self.root / media["trash_rel"]
                        target = self.root / media["archive_rel"]
                        if not source.is_file():
                            raise MediaArchiveError("回收站文件已经不见了")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(source, target)
                        moved = True
                    try:
                        now = _now()
                        conn.execute(
                            "UPDATE media SET retention='permanent',status='active',"
                            "trash_rel=NULL,trash_at=NULL,trash_until=NULL,updated_at=? "
                            "WHERE media_id=?",
                            (now, media_id),
                        )
                        conn.execute(
                            """
                            INSERT INTO refs(
                                ref_id,media_id,kind,collection,created_at,expires_at,permanent,
                                active,display_name,metadata_json
                            ) VALUES(?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(ref_id) DO UPDATE SET
                                active=1,permanent=1,expires_at=NULL,created_at=excluded.created_at
                            """,
                            (
                                f"pin:{media_id}",
                                media_id,
                                "user",
                                "pinned",
                                now,
                                None,
                                1,
                                1,
                                media.get("display_name") or "",
                                _json({"source": "user"}),
                            ),
                        )
                        conn.commit()
                    except Exception:
                        if moved and source is not None and target is not None:
                            try:
                                if target.is_file() and not source.exists():
                                    source.parent.mkdir(parents=True, exist_ok=True)
                                    os.replace(target, source)
                            except Exception:
                                pass
                        raise
                else:
                    conn.execute("DELETE FROM refs WHERE ref_id=?", (f"pin:{media_id}",))
                    # Existing permanent refs still protect the media.
                    permanent_refs = conn.execute(
                        "SELECT 1 FROM refs WHERE media_id=? AND permanent=1 AND active=1 LIMIT 1",
                        (media_id,),
                    ).fetchone()
                    if permanent_refs:
                        conn.execute(
                            "UPDATE media SET retention='permanent',updated_at=? WHERE media_id=?",
                            (_now(), media_id),
                        )
                    else:
                        conn.execute(
                            "UPDATE media SET retention='temporary',updated_at=? WHERE media_id=?",
                            (_now(), media_id),
                        )
                    conn.commit()
        return self.get_media(media_id) or {}

    def update_collection(self, media_id: str, collection: str, kind: str = "workshop") -> int:
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "UPDATE refs SET collection=?,active=1 WHERE media_id=? AND kind=?",
                    (str(collection), str(media_id), str(kind)),
                )
                conn.commit()
                return int(cursor.rowcount)

    # ------------------------------------------------------------------
    # Reads, URLs, statistics
    # ------------------------------------------------------------------
    def get_media(self, media_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return self._media_row(conn, str(media_id))

    def resolve_path(self, media_id: str, thumbnail: bool = False) -> Path | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status,archive_rel,trash_rel,thumb_rel FROM media WHERE media_id=?",
                (str(media_id),),
            ).fetchone()
        if row is None:
            return None
        rel = row["thumb_rel"] if thumbnail else None
        if not rel:
            rel = row["trash_rel"] if row["status"] == "trash" else row["archive_rel"]
        if not rel:
            return None
        path = self.root / rel
        return path if path.is_file() else None

    def list_media(self, status: str | None = None, retention: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if status:
            clauses.append("status=?")
            args.append(str(status))
        if retention:
            clauses.append("retention=?")
            args.append(str(retention))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM media" + where + " ORDER BY created_at DESC,media_id DESC",
                args,
            ).fetchall()
        return [self._row(row) for row in rows if row]

    def references(self, media_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM refs WHERE media_id=? ORDER BY created_at ASC",
                (str(media_id),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["permanent"] = bool(item.get("permanent"))
            item["active"] = bool(item.get("active"))
            item["metadata"] = _load_json(item.pop("metadata_json", "{}"))
            result.append(item)
        return result

    def list_workshop(self, collection: str = "gallery") -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT m.*, r.collection, r.display_name AS ref_display_name, r.ref_id
                FROM refs r JOIN media m ON m.media_id=r.media_id
                WHERE r.kind='workshop' AND r.collection=?
                  AND r.active=1 AND m.status <> 'deleted'
                ORDER BY m.created_at DESC, m.media_id DESC
                """,
                (str(collection),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            item = self._row(row)
            if not item or item["media_id"] in seen:
                continue
            seen.add(item["media_id"])
            result.append(item)
        return result

    def list_trash(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM media WHERE status='trash' ORDER BY trash_until ASC,created_at ASC"
            ).fetchall()
        return [self._row(row) for row in rows if row]

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT retention,status,COUNT(*) count,COALESCE(SUM(archive_bytes),0) bytes "
                "FROM media WHERE status<>'deleted' GROUP BY retention,status"
            ).fetchall()
            thumb = conn.execute(
                "SELECT COUNT(*) count,COALESCE(SUM(thumb_bytes),0) bytes "
                "FROM media WHERE thumb_rel IS NOT NULL AND thumb_bytes>0"
            ).fetchone()
            total = conn.execute(
                "SELECT COUNT(*) count,COALESCE(SUM(archive_bytes),0) bytes "
                "FROM media WHERE status<>'deleted'"
            ).fetchone()
            jobs = conn.execute(
                "SELECT status,COUNT(*) count FROM jobs GROUP BY status"
            ).fetchall()
        result = {
            "permanent": {"count": 0, "bytes": 0},
            "temporary": {"count": 0, "bytes": 0},
            "trash": {"count": 0, "bytes": 0},
            "thumbnails": {
                "count": int((thumb or {"count": 0})["count"]),
                "bytes": int((thumb or {"bytes": 0})["bytes"]),
            },
            "total": {
                "count": int((total or {"count": 0})["count"]),
                "bytes": int((total or {"bytes": 0})["bytes"]),
            },
            "jobs": {row["status"]: int(row["count"]) for row in jobs},
        }
        for row in rows:
            key = "trash" if row["status"] == "trash" else row["retention"]
            if key in result:
                result[key]["count"] += int(row["count"])
                result[key]["bytes"] += int(row["bytes"])
        result["total"]["human"] = self._human_bytes(result["total"]["bytes"])
        for key in ("permanent", "temporary", "trash", "thumbnails"):
            result[key]["human"] = self._human_bytes(result[key]["bytes"])
        return result

    @staticmethod
    def _human_bytes(value: int) -> str:
        number = float(max(0, int(value)))
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if number < 1024 or unit == "TB":
                return f"{number:.1f} {unit}" if unit != "B" else f"{int(number)} B"
            number /= 1024
        return f"{int(value)} B"

    def get_settings(self) -> dict[str, Any]:
        with self._connect() as conn:
            return self._settings_conn(conn)

    def update_settings(self, values: Mapping[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULT_SETTINGS)
        clean: dict[str, Any] = {}
        for key, value in values.items():
            if key not in allowed:
                continue
            if key == "auto_expire":
                clean[key] = bool(value)
            else:
                try:
                    number = int(value)
                except Exception as exc:
                    raise MediaArchiveError(f"设置 {key} 不是数字") from exc
                minimum = 0 if key == "temporary_days" else 1
                if number < minimum:
                    raise MediaArchiveError(f"设置 {key} 超出范围")
                clean[key] = number
        with self._lock:
            with self._connect() as conn:
                for key, value in clean.items():
                    conn.execute(
                        "INSERT INTO settings(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, _json(value)),
                    )
                conn.commit()
        return self.get_settings()

    # ------------------------------------------------------------------
    # Lifecycle and recovery
    # ------------------------------------------------------------------
    def _has_protecting_reference(
        self, conn: sqlite3.Connection, media_id: str, now: float
    ) -> bool:
        row = conn.execute(
            """
            SELECT 1 FROM refs
            WHERE media_id=? AND active=1
              AND (permanent=1 OR expires_at IS NULL OR expires_at>?)
            LIMIT 1
            """,
            (media_id, now),
        ).fetchone()
        return row is not None

    def expire(self, now: float | None = None) -> dict[str, Any]:
        now = float(now if now is not None else _now())
        settings = self.get_settings()
        if not settings.get("auto_expire", True):
            return {"expired_refs": 0, "moved": 0, "skipped": 0, "disabled": True}
        moved = 0
        skipped = 0
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE refs SET active=0 WHERE active=1 AND permanent=0 "
                    "AND expires_at IS NOT NULL AND expires_at<=?",
                    (now,),
                )
                expired_refs = int(conn.total_changes)
                candidates = conn.execute(
                    "SELECT media_id,archive_rel,status FROM media "
                    "WHERE status='active' AND retention='temporary'"
                ).fetchall()
                conn.commit()
            for candidate in candidates:
                media_id = str(candidate["media_id"])
                with self._connect() as check:
                    if self._has_protecting_reference(check, media_id, now):
                        skipped += 1
                        continue
                    row = check.execute(
                        "SELECT archive_rel,status FROM media WHERE media_id=?",
                        (media_id,),
                    ).fetchone()
                if not row or row["status"] != "active":
                    continue
                source_rel = row["archive_rel"]
                if not source_rel:
                    continue
                source = self.root / source_rel
                if not source.is_file():
                    skipped += 1
                    continue
                trash_rel = (Path("trash") / media_id / source.name).as_posix()
                target = self.root / trash_rel
                target.parent.mkdir(parents=True, exist_ok=True)
                moved_file = False
                try:
                    os.replace(source, target)
                    moved_file = True
                    until = now + int(settings["trash_days"]) * 86400
                    with self._connect() as conn:
                        # Re-check immediately before committing the move.
                        if self._has_protecting_reference(conn, media_id, _now()):
                            os.replace(target, source)
                            skipped += 1
                            continue
                        conn.execute(
                            "UPDATE media SET status='trash',trash_rel=?,trash_at=?,trash_until=?,"
                            "updated_at=? WHERE media_id=? AND status='active'",
                            (trash_rel, now, until, _now(), media_id),
                        )
                        conn.commit()
                    moved += 1
                except Exception:
                    if moved_file:
                        try:
                            if target.is_file() and not source.exists():
                                source.parent.mkdir(parents=True, exist_ok=True)
                                os.replace(target, source)
                        except Exception:
                            pass
                    skipped += 1
        return {"expired_refs": expired_refs, "moved": moved, "skipped": skipped, "disabled": False}

    def purge(self, now: float | None = None) -> dict[str, Any]:
        now = float(now if now is not None else _now())
        deleted = 0
        skipped = 0
        with self._lock:
            with self._connect() as conn:
                candidates = conn.execute(
                    "SELECT media_id,trash_rel,trash_until,status FROM media "
                    "WHERE status='trash' AND trash_until IS NOT NULL AND trash_until<=?",
                    (now,),
                ).fetchall()
            for candidate in candidates:
                media_id = str(candidate["media_id"])
                with self._connect() as check:
                    if self._has_protecting_reference(check, media_id, now):
                        skipped += 1
                        continue
                    row = check.execute(
                        "SELECT trash_rel,status FROM media WHERE media_id=?",
                        (media_id,),
                    ).fetchone()
                if not row or row["status"] != "trash":
                    continue
                try:
                    with self._connect() as conn:
                        conn.execute("BEGIN IMMEDIATE")
                        current = conn.execute(
                            "SELECT status,trash_rel,thumb_rel,trash_until FROM media WHERE media_id=?",
                            (media_id,),
                        ).fetchone()
                        if (not current or current["status"] != "trash"
                                or current["trash_until"] is None
                                or current["trash_until"] > _now()
                                or self._has_protecting_reference(conn, media_id, _now())):
                            conn.rollback()
                            skipped += 1
                            continue
                        if current["trash_rel"]:
                            (self.root / current["trash_rel"]).unlink(missing_ok=True)
                        conn.execute(
                            "UPDATE media SET status='deleted',trash_rel=NULL,archive_rel=NULL,"
                            "archive_bytes=0,updated_at=? "
                            "WHERE media_id=? AND status='trash'",
                            (_now(), media_id),
                        )
                        conn.commit()
                    deleted += 1
                except Exception:
                    skipped += 1
        return {"deleted": deleted, "skipped": skipped}

    def run_maintenance(self, now: float | None = None) -> dict[str, Any]:
        return {"expire": self.expire(now), "purge": self.purge(now)}

    def restore(self, media_id: str, permanent: bool = False) -> dict[str, Any]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT status,archive_rel,trash_rel FROM media WHERE media_id=?",
                    (str(media_id),),
                ).fetchone()
                if not row:
                    raise MediaArchiveError("媒体不存在")
                if row["status"] == "deleted":
                    raise MediaArchiveError("清晰版本已经彻底清理，无法恢复")
                if row["status"] != "trash":
                    if permanent:
                        now = _now()
                        result = self._media_row(conn, media_id) or {}
                        conn.execute(
                            "UPDATE media SET retention='permanent',status='active',updated_at=? WHERE media_id=?",
                            (now, str(media_id)),
                        )
                        conn.execute(
                            """
                            INSERT INTO refs(
                                ref_id,media_id,kind,collection,created_at,expires_at,permanent,
                                active,display_name,metadata_json
                            ) VALUES(?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(ref_id) DO UPDATE SET
                                active=1,permanent=1,expires_at=NULL,created_at=excluded.created_at
                            """,
                            (
                                f"pin:{media_id}", str(media_id), "user", "pinned", now,
                                None, 1, 1, (result or {}).get("display_name") or "",
                                _json({"source": "restore"}),
                            ),
                        )
                        conn.commit()
                    return self.get_media(str(media_id)) or {}
                if not row["trash_rel"] or not row["archive_rel"]:
                    raise MediaArchiveError("回收站文件缺少路径")
                permanent_refs = conn.execute(
                    "SELECT 1 FROM refs WHERE media_id=? AND permanent=1 AND active=1 LIMIT 1",
                    (str(media_id),),
                ).fetchone()
                source = self.root / row["trash_rel"]
                target = self.root / row["archive_rel"]
                target.parent.mkdir(parents=True, exist_ok=True)
                if not source.is_file():
                    raise MediaArchiveError("回收站文件已经不见了")
                os.replace(source, target)
                try:
                    conn.execute(
                        "UPDATE media SET status='active',trash_rel=NULL,trash_at=NULL,trash_until=NULL,"
                        "retention=?,updated_at=? WHERE media_id=?",
                        (
                            "permanent" if (permanent or permanent_refs) else "temporary",
                            _now(),
                            str(media_id),
                        ),
                    )
                    if not permanent:
                        settings = self._settings_conn(conn)
                        conn.execute(
                            "UPDATE refs SET active=1,expires_at=? WHERE media_id=? "
                            "AND permanent=0 AND kind='chat'",
                            (
                                _now() + int(settings.get("temporary_days", 30)) * 86400,
                                str(media_id),
                            ),
                        )
                    if permanent:
                        now = _now()
                        conn.execute(
                            """
                            INSERT INTO refs(
                                ref_id,media_id,kind,collection,created_at,expires_at,permanent,
                                active,display_name,metadata_json
                            ) VALUES(?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(ref_id) DO UPDATE SET
                                active=1,permanent=1,expires_at=NULL,created_at=excluded.created_at
                            """,
                            (
                                f"pin:{media_id}", str(media_id), "user", "pinned", now,
                                None, 1, 1, "", _json({"source": "restore"}),
                            ),
                        )
                    conn.commit()
                except Exception:
                    try:
                        if target.is_file() and not source.exists():
                            source.parent.mkdir(parents=True, exist_ok=True)
                            os.replace(target, source)
                    except Exception:
                        pass
                    raise
        return self.get_media(str(media_id)) or {}

    def delete_now(self, media_id: str) -> dict[str, Any]:
        """Explicit user deletion. Automatic collection always uses trash first."""
        media_id = str(media_id)
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT status,archive_rel,trash_rel,thumb_rel FROM media WHERE media_id=?",
                    (media_id,),
                ).fetchone()
                if not row:
                    conn.rollback()
                    raise MediaArchiveError("媒体不存在")
                if row["status"] == "deleted":
                    conn.rollback()
                    return {"media_id": media_id, "status": "deleted"}
                # The product promise is that a hard delete removes only the
                # clear version.  The thumbnail remains addressable for the
                # historical message and its expiration notice.
                for rel in (row["archive_rel"], row["trash_rel"]):
                    if rel:
                        (self.root / rel).unlink(missing_ok=True)
                conn.execute("UPDATE refs SET active=0 WHERE media_id=?", (media_id,))
                conn.execute(
                    "UPDATE media SET status='deleted',archive_rel=NULL,trash_rel=NULL,"
                    "archive_bytes=0,trash_at=NULL,trash_until=NULL,"
                    "updated_at=?,error=NULL WHERE media_id=?",
                    (_now(), media_id),
                )
                conn.commit()
        return self.get_media(media_id) or {"media_id": media_id, "status": "deleted"}

    # ------------------------------------------------------------------
    # Legacy migration
    # ------------------------------------------------------------------
    def migrate_legacy(
        self,
        message_files: Iterable[str | os.PathLike[str]] = (),
        media_dirs: Iterable[str | os.PathLike[str]] = (),
        gallery_dirs: Iterable[str | os.PathLike[str]] = (),
    ) -> dict[str, Any]:
        """将旧 JSONL + 文件目录转换成媒体 ID。

        来源不明的旧媒体按永久保留处理。所有新文件校验成功后才替换 JSONL；
        旧副本移入归档自己的 migration/trash，后续由 ``cleanup_migration`` 清理。
        """
        # v2 曾可能在消息解析不完整时标记完成，不能跳过本版安全校验。
        marker_key = LEGACY_MIGRATION_MARKER
        with self._lock:
            with self._connect() as conn:
                marker = conn.execute(
                    "SELECT value FROM schema_meta WHERE key=?", (marker_key,)
                ).fetchone()
                if marker and marker["value"] == "1":
                    return {
                        "ok": True,
                        "status": "complete",
                        "skipped": True,
                        "migrated": 0,
                        "rewritten": 0,
                        "moved_old": 0,
                        "failed_items": [],
                    }

            media_paths: list[Path] = []
            media_by_name: dict[str, list[Path]] = {}
            path_state: dict[str, dict[str, Any]] = {}
            gallery_keys: set[str] = set()
            blocked_keys: set[str] = set()
            failed_items: list[dict[str, str]] = []
            message_items: list[dict[str, Any]] = []
            migrated = gallery_count = rewritten = moved_old = 0
            backup_root = self.migration_dir / (
                time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
            )
            message_files = [Path(name) for name in message_files]
            gallery_dirs = [Path(name) for name in gallery_dirs]

            def remember(path: Path) -> None:
                try:
                    resolved = path.resolve()
                except OSError:
                    resolved = path.absolute()
                key = str(resolved)
                if key not in path_state:
                    media_paths.append(path)
                    media_by_name.setdefault(path.name, []).append(path)
                    path_state[key] = {"path": path, "ready": False, "reason": ""}

            def fail(kind: str, path: Path | None, error: Exception | str) -> None:
                failed_items.append({
                    "kind": kind,
                    "path": str(path) if path is not None else "",
                    "error": str(error)[:500],
                })
                if path is not None:
                    try:
                        path_state[str(path.resolve())]["reason"] = str(error)[:500]
                    except Exception:
                        pass

            for directory in media_dirs:
                root = Path(directory)
                if not root.is_dir():
                    continue
                for path in root.iterdir():
                    if path.is_file() and not path.name.startswith("."):
                        remember(path)
            for directory in gallery_dirs:
                root = Path(directory)
                if not root.is_dir():
                    continue
                for path in root.iterdir():
                    if path.is_file() and not path.name.startswith("."):
                        remember(path)
            # 先完整读取所有消息。未知的消息内容可能引用任意旧文件，
            # 因此读取/解析失败时本轮不搬动任何旧媒体，也不做“孤立”推断。
            message_lines: dict[Path, list[str]] = {}
            for path in message_files:
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except Exception as exc:
                    fail("message-read", path, exc)
                    continue
                for line_no, line in enumerate(lines, 1):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        if not isinstance(record, dict):
                            raise ValueError("消息必须是 JSON 对象")
                        if record.get("段") is not None and not isinstance(record["段"], list):
                            raise ValueError("消息段必须是数组")
                    except Exception as exc:
                        fail("message-parse", path, f"第 {line_no} 行无法解析: {exc}")
                message_lines[path] = lines
            if failed_items:
                return {
                    "ok": False, "status": "failed", "skipped": False,
                    "migrated": 0, "gallery": 0, "rewritten": 0, "moved_old": 0,
                    "failed_items": failed_items, "message_items": message_items,
                }
            # A previous process may have published the new message file and
            # then died before moving its legacy source.  Reconcile those
            # sources from the durable metadata so the next run can finish the
            # move without rewriting the message again.
            for item in self.list_media():
                legacy_path = str((item.get("metadata") or {}).get("legacy_path") or "")
                if not legacy_path:
                    continue
                try:
                    key = str(Path(legacy_path).resolve())
                except OSError:
                    key = legacy_path
                state = path_state.get(key)
                if state and item.get("status") != "deleted":
                    archived = self.resolve_path(item["media_id"])
                    if archived:
                        try:
                            self._verify_readable(archived, {
                                "is_image": bool(item.get("width") or item.get("height")),
                            })
                            state["ready"] = True
                        except MediaArchiveError:
                            # 留给下面的消息/孤立媒体流程明确记录失败并阻塞搬移。
                            pass

            def ingest_legacy(path: Path, source: str, retention: str) -> dict[str, Any]:
                if not path.is_file():
                    raise MediaArchiveError("旧媒体文件不存在")
                return self.ingest_file(
                    path,
                    source=source,
                    retention=retention,
                    metadata={"legacy_path": str(path.resolve())},
                )

            def verify_media(media_id: str) -> None:
                item = self.get_media(media_id)
                archived_path = self.resolve_path(media_id)
                if not item or item["status"] == "deleted" or archived_path is None:
                    raise MediaArchiveError(f"媒体 ID 不存在或归档不可读取: {media_id}")
                self._verify_readable(archived_path, {
                    "is_image": bool(item.get("width") or item.get("height")),
                })

            # First make every gallery item durable.  A failed item stays exactly
            # where it was and is reported for a later retry.
            for directory in gallery_dirs:
                root = Path(directory)
                if not root.is_dir():
                    continue
                for path in sorted(root.iterdir(), key=lambda x: x.name.casefold()):
                    if not path.is_file() or path.name.startswith("."):
                        continue
                    remember(path)
                    gallery_keys.add(str(path.resolve()))
                    try:
                        item = ingest_legacy(path, "workshop", "permanent")
                        self.add_reference(
                            item["media_id"],
                            ref_id=f"legacy-gallery:{path.resolve()}",
                            kind="workshop",
                            collection="gallery",
                            permanent=True,
                            display_name=path.name,
                        )
                        verify_media(item["media_id"])
                        path_state[str(path.resolve())]["ready"] = True
                        gallery_count += 1
                    except Exception as exc:
                        blocked_keys.add(str(path.resolve()))
                        fail("gallery", path, exc)

            # Rewrite each message file atomically as a unit.  If any image in
            # that file cannot be archived or referenced, leave the whole file
            # and all original filenames untouched.
            for file_name in message_files:
                path = Path(file_name)
                lines = message_lines[path]
                output: list[str] = []
                changed = False
                file_paths: set[str] = set()
                touched_paths: set[str] = set()
                file_results: list[dict[str, Any]] = []
                # 在处理任何段落之前，收集整份文件的旧引用。前面的段落
                # 失败时，后面的旧图也不能被孤立文件流程搬走。
                for line in lines:
                    if not line.strip():
                        continue
                    for segment in json.loads(line).get("段") or []:
                        if isinstance(segment, dict) and segment.get("文件"):
                            old_name = os.path.basename(str(segment["文件"]))
                            touched_paths.update(
                                str(candidate.resolve())
                                for candidate in media_by_name.get(old_name, [])
                            )
                file_error: Exception | None = None
                for line_no, line in enumerate(lines, 1):
                    if not line.strip():
                        output.append(line)
                        continue
                    record = json.loads(line)
                    message_id = str(record.get("消息ID") or "")
                    candidate = dict(record)
                    if not message_id:
                        message_id = "msg_" + hashlib.sha1(
                            f"{path.resolve()}:{line_no}:{line}".encode("utf-8")
                        ).hexdigest()[:24]
                        candidate["消息ID"] = message_id
                        changed = True
                    segments = candidate.get("段")
                    if isinstance(segments, list):
                        new_segments = []
                        for index, raw_segment in enumerate(segments):
                            segment = dict(raw_segment) if isinstance(raw_segment, dict) else raw_segment
                            if not isinstance(segment, dict):
                                new_segments.append(segment)
                                continue
                            media_id = str(segment.get("媒体ID") or "")
                            if not media_id and not segment.get("文件"):
                                new_segments.append(segment)
                                continue
                            old_path: Path | None = None
                            result = {
                                "path": str(path), "line": line_no, "segment": index,
                                "status": "verified", "media_id": media_id,
                            }
                            file_results.append(result)
                            if media_id:
                                try:
                                    verify_media(media_id)
                                except Exception as exc:
                                    if not segment.get("文件"):
                                        result.update(status="retry", error=str(exc))
                                        file_error = exc
                                        break
                                    # 无效 ID 不能遮蔽仍可恢复的旧文件。
                                    media_id = ""
                            if not media_id and segment.get("文件"):
                                old_name = os.path.basename(str(segment.get("文件")))
                                candidates = [
                                    x for x in media_by_name.get(old_name, [])
                                    if x.is_file()
                                ]
                                if len(candidates) == 1:
                                    old_path = candidates[0]
                                elif len(candidates) > 1:
                                    # Prefer an exact path from the legacy
                                    # record; ambiguous names are unsafe to move.
                                    file_error = MediaArchiveError(
                                        f"旧媒体文件名有多个候选: {old_name}"
                                    )
                                    break
                                else:
                                    file_error = MediaArchiveError(
                                        f"旧媒体文件不存在: {old_name}"
                                    )
                                    break
                                remember(old_path)
                                touched_paths.add(str(old_path.resolve()))
                                try:
                                    item = ingest_legacy(old_path, "unknown", "permanent")
                                    media_id = str(item["media_id"])
                                    migrated += 1
                                    result.update(status="converted", media_id=media_id)
                                except Exception as exc:
                                    result.update(status="retry", error=str(exc))
                                    file_error = exc
                                    break
                            if media_id:
                                try:
                                    self.add_reference(
                                        media_id,
                                        ref_id=f"legacy-chat:{path.resolve()}:{line_no}:{index}",
                                        kind="chat",
                                        created_at=float(candidate.get("时") or _now()),
                                        permanent=True,
                                        display_name=str(segment.get("名称") or ""),
                                    )
                                    verify_media(media_id)
                                    refs = self.references(media_id)
                                    expected_ref = f"legacy-chat:{path.resolve()}:{line_no}:{index}"
                                    if not any(ref["ref_id"] == expected_ref and ref["active"]
                                               for ref in refs):
                                        raise MediaArchiveError("迁移消息引用写入校验失败")
                                except Exception as exc:
                                    result.update(status="retry", error=str(exc))
                                    file_error = exc
                                    break
                                if segment.get("媒体ID") != media_id or segment.get("文件"):
                                    changed = True
                                segment["媒体ID"] = media_id
                                segment.pop("文件", None)
                                if old_path is not None:
                                    file_paths.add(str(old_path.resolve()))
                            new_segments.append(segment)
                        if file_error is not None:
                            break
                        candidate["段"] = new_segments
                    output.append(
                        json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
                    )
                if file_error is not None:
                    blocked_keys.update(touched_paths)
                    for result in file_results:
                        result.update(status="retry", error=str(file_error))
                    message_items.extend(file_results)
                    fail("message", path, file_error)
                    continue
                if not changed:
                    message_items.extend(file_results)
                    continue
                try:
                    if path.read_text(encoding="utf-8").splitlines() != lines:
                        raise MediaArchiveError("消息在迁移期间发生变化，保留原文件等待重试")
                    backup_root.mkdir(parents=True, exist_ok=True)
                    backup_key = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
                    backup = backup_root / f"{backup_key}-{path.name}"
                    if not backup.exists():
                        shutil.copy2(path, backup)
                    temp = path.with_suffix(path.suffix + ".migrating")
                    with temp.open("w", encoding="utf-8") as handle:
                        handle.write("\n".join(output) + ("\n" if output else ""))
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp, path)
                    # Verify the published file before allowing any source
                    # file to leave its original location.
                    verify = path.read_text(encoding="utf-8").splitlines()
                    if len(verify) != len(output):
                        raise MediaArchiveError("消息重写校验行数不一致")
                    for expected, actual in zip(output, verify):
                        if not expected.strip() and not actual.strip():
                            continue
                        if json.loads(expected) != json.loads(actual):
                            raise MediaArchiveError("消息重写校验内容不一致")
                        for segment in json.loads(actual).get("段") or []:
                            if isinstance(segment, dict) and segment.get("媒体ID"):
                                verify_media(str(segment["媒体ID"]))
                    rewritten += 1
                    for key in file_paths:
                        if key in path_state:
                            path_state[key]["ready"] = True
                except Exception as exc:
                    blocked_keys.update(touched_paths)
                    for result in file_results:
                        result.update(status="retry", error=str(exc))
                    # A failed publish must restore the original message file
                    # before a retry; the old media remains untouched.
                    try:
                        backup_key = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
                        backup = backup_root / f"{backup_key}-{path.name}"
                        if backup.is_file():
                            shutil.copy2(backup, path)
                    except Exception:
                        pass
                    try:
                        path.with_suffix(path.suffix + ".migrating").unlink(missing_ok=True)
                    except Exception:
                        pass
                    fail("message-write", path, exc)
                message_items.extend(file_results)

            # Preserve legacy media that was not referenced by a currently
            # readable message.  Its source is unknown, so it is permanent by
            # default rather than silently discarded.
            for path in media_paths:
                key = str(path.resolve())
                state = path_state.get(key) or {}
                if (
                    state.get("ready")
                    or key in gallery_keys
                    or key in blocked_keys
                    or not path.is_file()
                ):
                    continue
                try:
                    item = ingest_legacy(path, "unknown", "permanent")
                    verify_media(item["media_id"])
                    state["ready"] = True
                except Exception as exc:
                    fail("orphan-media", path, exc)

            # Retry-safe cleanup: only files explicitly marked ready may move.
            legacy_trash = self.migration_dir / "trash" / time.strftime("%Y%m%d-%H%M%S")
            for path in media_paths:
                key = str(path.resolve())
                state = path_state.get(key) or {}
                if key in blocked_keys or not state.get("ready") or not path.is_file():
                    continue
                try:
                    if self.root in path.resolve().parents:
                        continue
                    target = legacy_trash / path.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        target = target.with_name(
                            f"{target.stem}-{uuid.uuid4().hex[:6]}{target.suffix}"
                        )
                    shutil.move(str(path), str(target))
                    marker = target.with_name(target.name + ".confirmed")
                    marker.write_text(
                        json.dumps(
                            {"source": str(path), "target": str(target), "confirmed_at": _now()},
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    moved_old += 1
                except Exception as exc:
                    try:
                        if target.is_file() and not path.exists():
                            shutil.move(str(target), str(path))
                    except Exception:
                        pass
                    fail("move-old", path, exc)

            complete = not failed_items
            if complete:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO schema_meta(key,value) VALUES(?,?)",
                        (marker_key, "1"),
                    )
                    conn.commit()
            status = "complete" if complete else ("partial" if migrated or gallery_count or rewritten or moved_old else "failed")
            return {
                "ok": complete,
                "status": status,
                "skipped": False,
                "migrated": migrated,
                "gallery": gallery_count,
                "rewritten": rewritten,
                "moved_old": moved_old,
                "failed_items": failed_items,
                "message_items": message_items,
            }

    def cleanup_migration(self, now: float | None = None) -> int:
        settings = self.get_settings()
        cutoff = float(now if now is not None else _now()) - int(settings["trash_days"]) * 86400
        removed = 0
        self._migration_cleanup_errors = []
        root = self.migration_dir / "trash"
        if root.is_dir():
            with self._connect() as conn:
                known = {
                    str(row["sha256"]): str(row["media_id"])
                    for row in conn.execute(
                        "SELECT media_id,sha256 FROM media WHERE status<>'deleted'"
                    )
                }
            for directory in root.iterdir():
                try:
                    if not directory.is_dir():
                        continue
                    # Reconcile migration output created by older versions:
                    # only a byte-for-byte match to a readable product object
                    # may become eligible for the normal grace period.
                    for candidate in directory.iterdir():
                        if not candidate.is_file() or candidate.name.endswith(".confirmed"):
                            continue
                        try:
                            digest, _ = self._sha256_file(candidate)
                            media_id = known.get(digest)
                            if not media_id or not self.resolve_path(media_id):
                                self._migration_cleanup_errors.append({
                                    "path": str(candidate),
                                    "error": "旧副本未能与已验证归档匹配，暂不清理",
                                })
                                continue
                            marker = candidate.with_name(candidate.name + ".confirmed")
                            if not marker.exists():
                                marker.write_text(
                                    json.dumps(
                                        {
                                            "source": str(candidate),
                                            "media_id": media_id,
                                            "confirmed_at": _now(),
                                        },
                                        ensure_ascii=False,
                                    ),
                                    encoding="utf-8",
                                )
                        except Exception as exc:
                            self._migration_cleanup_errors.append({
                                "path": str(candidate),
                                "error": str(exc)[:500],
                            })
                    for marker in directory.glob("*.confirmed"):
                        if marker.stat().st_mtime > cutoff:
                            continue
                        target = marker.with_name(marker.name[: -len(".confirmed")])
                        target.unlink(missing_ok=True)
                        marker.unlink(missing_ok=True)
                        removed += 1
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                except Exception as exc:
                    self._migration_cleanup_errors.append({
                        "path": str(directory),
                        "error": str(exc)[:500],
                    })

        # Published message backups are useful only through the same migration
        # grace period.  They are removed only after the durable marker says
        # the current migration completed.
        with self._connect() as conn:
            marker = conn.execute(
                "SELECT value FROM schema_meta WHERE key=?", (LEGACY_MIGRATION_MARKER,)
            ).fetchone()
        if marker and marker["value"] == "1":
            for backup in self.migration_dir.iterdir():
                if backup.name == "trash" or not backup.is_dir():
                    continue
                try:
                    if backup.stat().st_mtime <= cutoff:
                        shutil.rmtree(backup)
                        removed += 1
                except Exception as exc:
                    self._migration_cleanup_errors.append({
                        "path": str(backup),
                        "error": str(exc)[:500],
                    })
        return removed

    def migration_cleanup_errors(self) -> list[dict[str, str]]:
        return list(self._migration_cleanup_errors)
