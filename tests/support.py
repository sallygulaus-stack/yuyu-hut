"""Isolated panel fixture shared by migrated regression suites."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid


class MediaFingerprint:
    def __init__(self, project):
        self.roots = [project / "data", project / "panel" / "web" / "assets"]
        self.before = self.snapshot()

    def snapshot(self):
        result = {}

        def visit(path, key):
            if path.is_symlink():
                result[key] = ("link", os.readlink(path))
            elif path.is_dir():
                result[key] = ("directory", path.stat().st_mtime_ns)
                for child in sorted(path.iterdir()):
                    visit(child, key + "/" + child.name)
            elif path.is_file():
                stat = path.stat()
                result[key] = ("file", stat.st_size, stat.st_mtime_ns,
                               hashlib.sha256(path.read_bytes()).hexdigest())
            else:
                result[key] = ("absent",)
        for index, root in enumerate(self.roots):
            visit(root, str(index))
        return result

    def verify(self):
        after = self.snapshot()
        assert after == self.before, "测试修改了项目原有媒体文件"
        files = [key for key, value in after.items() if value[0] == "file"]
        return {"unchanged": True, "roots": len(self.roots), "files": len(files),
                "sqlite_files": sum(".sqlite" in key or key.endswith((".db", "-wal", "-shm")) for key in files),
                "sha256": hashlib.sha256(json.dumps(after, sort_keys=True).encode()).hexdigest()}


class IsolatedPanel:
    def __init__(self, project, name="isolated_panel"):
        self.temp = tempfile.TemporaryDirectory(prefix="qq-panel-test-")
        self.root = Path(self.temp.name)
        sys.path.insert(0, str(project / "panel"))
        spec = importlib.util.spec_from_file_location(name + uuid.uuid4().hex, project / "panel" / "panel.py")
        self.panel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.panel)
        assert self.panel.媒体库 is None, "导入不应初始化用户媒体库"
        self.panel.configure_data(self.root)
        for name in ("画册目录", "画册缓存目录", "画册收纳目录", "聊记录目录",
                     "聊天归档目录", "聊媒体目录", "聊缩略目录", "媒体归档目录"):
            Path(getattr(self.panel, name)).mkdir(parents=True, exist_ok=True)
        self.api = self.panel.Api.__new__(self.panel.Api)

    def close(self):
        self.panel.媒体库 = None
        self.temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
