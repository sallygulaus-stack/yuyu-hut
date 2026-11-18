"""First-run data selection for the Windows double-click entry."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "panel"))
from desktop_window import DESKTOP_UNAVAILABLE
from settings import user_data_dir


def offer_browser():
    import tkinter as tk
    from tkinter import messagebox

    app = tk.Tk()
    app.withdraw()
    try:
        return messagebox.askyesno(
            "鱼鱼小窝",
            "桌面窗口未能打开。要用浏览器打开同一份小窝资料吗？",
            parent=app,
        )
    finally:
        app.destroy()


def choose(default):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    app = tk.Tk()
    app.title("鱼鱼小窝")
    app.resizable(True, False)
    app.columnconfigure(0, weight=1)
    panel = ttk.Frame(app, padding=18)
    panel.grid(sticky="nsew")
    panel.columnconfigure(0, weight=1)
    value, selected = tk.StringVar(value=str(default)), []
    ttk.Label(panel, text="小窝数据放在哪里？").grid(row=0, column=0, sticky="w", pady=(0, 4))
    ttk.Label(panel, text="聊天、附件和自己的作品都放在这里。直接使用默认位置也可以。").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))
    ttk.Entry(panel, textvariable=value, width=50, state="readonly").grid(row=2, column=0, sticky="ew")

    def browse():
        new = filedialog.askdirectory(parent=app, title="选择小窝数据文件夹", mustexist=False)
        if new:
            value.set(new)

    def finish():
        path = Path(value.get()).expanduser().resolve()
        if path.is_relative_to(ROOT) or path == Path(path.anchor):
            messagebox.showwarning("换一个目录", "请选择代码目录以外的专用文件夹，不要选择整个磁盘。", parent=app)
            return
        try:
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=path):
                pass
        except OSError:
            messagebox.showwarning("无法保存", "这里不能写入文件。请选择自己的文件夹。", parent=app)
            return
        selected.append(path)
        app.destroy()

    ttk.Button(panel, text="更改位置", command=browse).grid(row=2, column=1, padx=(7, 0))
    ttk.Button(panel, text="打开小窝", command=finish).grid(row=3, column=1, sticky="e", pady=(12, 0))
    app.mainloop()
    return selected[0] if selected else None


def main():
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--use-defaults", action="store_true")
    parser.add_argument("--desktop-first", action="store_true")
    parser.add_argument("--browser", action="store_true")
    options, args = parser.parse_known_args()
    if options.browser and "--desktop" in args:
        parser.error("请选择桌面或浏览器中的一种启动方式")
    desktop_first = (sys.platform == "win32" and options.desktop_first and
                     not options.use_defaults and not options.browser and not args)
    command = [sys.executable, "-B", str(ROOT / "panel/panel.py")]
    if "-h" in args or "--help" in args:
        return subprocess.call(command + args, cwd=ROOT)

    explicit = any(x in ("--data-dir", "--config", "--demo") or x.startswith(("--data-dir=", "--config=")) for x in args)
    if not explicit and not os.environ.get("QQ_PANEL_DATA_DIR") and not (ROOT / "panel/config.json").is_file():
        home = user_data_dir()
        prefs = home / "launcher.json"
        if prefs.exists():
            data = Path(json.loads(prefs.read_text(encoding="utf-8"))["data_dir"])
            if not data.is_dir():
                if not desktop_first:
                    raise SystemExit("上次的数据目录不可用，请先接回磁盘或重新选择。")
                data = choose(data)
        else:
            # Never reuse the demonstration dataset as a new user's personal library.
            data = home / "home"
            if desktop_first:
                data = choose(data)
        if data is None:
            return 0
        data.mkdir(parents=True, exist_ok=True)
        home.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=home, delete=False) as tmp:
            json.dump({"data_dir": str(data)}, tmp, ensure_ascii=False)
        os.replace(tmp.name, prefs)
        args += ["--data-dir", str(data)]
    if not desktop_first:
        return subprocess.call(command + args, cwd=ROOT)

    result = subprocess.call(command + args + ["--desktop"], cwd=ROOT)
    if result != DESKTOP_UNAVAILABLE:
        return result
    if not offer_browser():
        return 0

    # Give the browser session its own console so the user can stop it with Ctrl+C.
    command[0] = str(Path(sys.executable).with_name("python.exe"))
    return subprocess.call(command + args, cwd=ROOT, creationflags=subprocess.CREATE_NEW_CONSOLE)


if __name__ == "__main__":
    raise SystemExit(main())
