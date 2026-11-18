"""Optional desktop shell and Windows tray controls."""
import os
from pathlib import Path
import sys
import threading
import time

from desktop_api import window_api

DESKTOP_UNAVAILABLE = 78
MIN_WIDTH, MIN_HEIGHT = 390, 520
WINDOWS_APP_ID = "YuyuHut.Desktop"


class WindowsControls:
    def __init__(self, title, icon_path, tray_path):
        import ctypes
        from ctypes import wintypes
        import pystray
        from PIL import Image

        self.window = None
        self.icon_path = icon_path
        self.quitting = threading.Event()
        self.start_failed = False
        self.dragging = threading.Lock()
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        self.user32.IsIconic.argtypes = self.user32.IsZoomed.argtypes = (wintypes.HWND,)
        self.user32.IsWindowVisible.argtypes = (wintypes.HWND,)
        self.user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        self.user32.FindWindowW.restype = wintypes.HWND
        self.user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        self.user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        self.user32.GetAsyncKeyState.restype = ctypes.c_short
        self.user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
        self.user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        self.get_dpi = getattr(self.user32, "GetDpiForWindow", None)
        if self.get_dpi:
            self.get_dpi.argtypes = (wintypes.HWND,)
            self.get_dpi.restype = wintypes.UINT
        self.user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, wintypes.UINT)

        with Image.open(tray_path) as source:
            image = source.copy()
        self.tray = pystray.Icon("yuyu_hut", image, title, pystray.Menu(
            pystray.MenuItem("打开小窝", self.show, default=True),
            pystray.MenuItem("退出小窝", self.quit),
        ))

    def _handle(self):
        import ctypes
        from ctypes import wintypes

        native = getattr(self.window, "native", None)
        try:
            handle = int(native.Handle.ToInt64()) if native is not None else None
        except (AttributeError, RuntimeError, TypeError):
            handle = None
        if not handle:
            handle = self.user32.FindWindowW(None, self.window.title)
        if handle:
            owner = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
            if owner.value == os.getpid():
                return handle
        return None

    def _closing(self):
        if self.quitting.is_set() or not self.tray.visible:
            return True
        self.window.hide()
        return False

    def show(self, *_):
        handle = self._handle()
        if handle and not self.quitting.is_set():
            if self.user32.IsIconic(handle):
                self.window.restore()
            self.window.show()

    def quit(self, *_):
        self.quitting.set()
        handle = self._handle()
        if handle:
            self.user32.PostMessageW(handle, 0x0010, 0, 0)  # WM_CLOSE

    def win_close(self):
        if self.tray.visible:
            self.window.hide()
        else:
            self.quit()
        return {"ok": True}

    def win_min(self):
        self.window.minimize()
        return {"ok": True}

    def win_max(self):
        handle = self._handle()
        if handle:
            if self.user32.IsZoomed(handle):
                self.window.restore()
            else:
                self.window.maximize()
        return {"ok": bool(handle)}

    def _drag(self, edge=None):
        import ctypes
        from ctypes import wintypes

        handle = self._handle()
        u32 = self.user32
        if (not handle or u32.IsZoomed(handle) or not (u32.GetAsyncKeyState(1) & 0x8000)
                or not self.dragging.acquire(blocking=False)):
            return {"ok": False}
        origin, bounds = wintypes.POINT(), wintypes.RECT()
        if not (u32.GetCursorPos(ctypes.byref(origin)) and u32.GetWindowRect(handle, ctypes.byref(bounds))):
            self.dragging.release()
            return {"ok": False}
        dpi = (self.get_dpi(handle) or 96) if self.get_dpi else 96
        min_width, min_height = round(MIN_WIDTH * dpi / 96), round(MIN_HEIGHT * dpi / 96)

        def follow_cursor():
            # Frameless WinForms windows do not enter the system caption move loop.
            try:
                cursor, previous = wintypes.POINT(), None
                while (not self.quitting.is_set() and u32.GetAsyncKeyState(1) & 0x8000
                       and u32.IsWindowVisible(handle) and u32.GetCursorPos(ctypes.byref(cursor))):
                    delta = (cursor.x - origin.x, cursor.y - origin.y)
                    if delta != previous:
                        dx, dy = delta
                        left, top, right, bottom = bounds.left, bounds.top, bounds.right, bounds.bottom
                        if edge is None:
                            left, top, right, bottom = left + dx, top + dy, right + dx, bottom + dy
                        else:
                            if edge in (10, 13, 16):
                                left = min(left + dx, right - min_width)
                            if edge in (11, 14, 17):
                                right = max(right + dx, left + min_width)
                            if edge in (12, 13, 14):
                                top = min(top + dy, bottom - min_height)
                            if edge in (15, 16, 17):
                                bottom = max(bottom + dy, top + min_height)
                        # Preserve activation and Z order as the cursor moves.
                        u32.SetWindowPos(handle, None, left, top, right - left, bottom - top, 0x14)
                        previous = delta
                    time.sleep(.012)
            finally:
                self.dragging.release()

        threading.Thread(target=follow_cursor, name="window-drag", daemon=True).start()
        return {"ok": True}

    def win_drag(self):
        return self._drag()

    def win_size(self, edge):
        if type(edge) is not int or not 10 <= edge <= 17:
            return {"ok": False}
        return self._drag(edge)

    def run(self, webview):
        from System import Action
        from System.Drawing import Icon

        icon = Icon(str(self.icon_path))

        def set_window_icon():
            form = self.window.native

            def apply_icon():
                form.Icon = icon

            # Older WinForms backends ignore start(icon=...) and dispatch before_show asynchronously.
            if form.InvokeRequired:
                form.BeginInvoke(Action(apply_icon))
            else:
                apply_icon()

        self.window.events.before_show += set_window_icon
        self.window.events.closing += self._closing
        finished = threading.Event()

        def watch_startup():
            for _ in range(120):
                if finished.wait(.25) or self.window.events.loaded.is_set():
                    return
            self.start_failed = True
            self.quit()

        try:
            self.tray.run_detached()
            try:
                webview.start(watch_startup, gui="edgechromium", private_mode=True)
            finally:
                finished.set()
                self.tray.stop()
        finally:
            icon.Dispose()
        if self.start_failed or not self.window.events.loaded.is_set() and not self.quitting.is_set():
            raise RuntimeError("桌面页面加载失败，请检查系统的 WebView2 组件")


def run_desktop(webview, api, rpc_methods, title, address, web_dir, window_slot):
    windows = sys.platform == "win32"
    controls = None
    if windows:
        import ctypes
        from ctypes import wintypes
        from webview.platforms import winforms

        if winforms.renderer != "edgechromium":
            raise RuntimeError("Windows 桌面窗口需要 Microsoft Edge WebView2")
        app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        app_id.argtypes = (wintypes.LPCWSTR,)
        app_id.restype = ctypes.c_long
        result = app_id(WINDOWS_APP_ID)
        if result != 0:
            raise OSError("无法设置小窝的 Windows 任务栏标识")
        assets = Path(web_dir) / "assets"
        controls = WindowsControls(title, assets / "app-icon.ico", assets / "app-icon.png")
    address += ("&" if "?" in address else "?") + "desktop=1"
    if windows:
        address += "&nativewindow=1"
    window = webview.create_window(
        title, address, js_api=window_api(api, rpc_methods, controls), width=1200, height=820,
        min_size=(MIN_WIDTH, MIN_HEIGHT), frameless=True, easy_drag=not windows, text_select=True)
    window_slot["w"] = window
    try:
        if controls:
            controls.window = window
            controls.run(webview)
        else:
            webview.start(private_mode=True)
    finally:
        window_slot["w"] = None
