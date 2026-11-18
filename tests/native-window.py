"""Opt-in Windows desktop smoke test. --icons does not use the mouse."""
import ctypes
from ctypes import wintypes
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "panel"))


def wait(condition):
    for _ in range(80):
        if condition():
            return True
        time.sleep(.05)
    return False


def exercise(shell, health):
    import pyautogui as mouse
    from pystray._util import win32

    window, handle = shell.window, shell._handle()
    user32 = shell.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = (wintypes.POINT,)
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
    original_focus, original_cursor = user32.GetForegroundWindow(), mouse.position()

    def position(selector, fraction=.5):
        x, y, width, height = window.evaluate_js(f"""(() => {{
          const r = document.querySelector({selector!r}).getBoundingClientRect();
          return [r.x + r.width * {fraction}, r.y + r.height * .5, innerWidth, innerHeight];
        }})()""")
        rect, point = wintypes.RECT(), wintypes.POINT()
        assert user32.GetClientRect(handle, ctypes.byref(rect))
        assert user32.ClientToScreen(handle, ctypes.byref(point))
        x = round(point.x + x * rect.right / width)
        y = round(point.y + y * rect.bottom / height)
        assert user32.GetAncestor(user32.WindowFromPoint(wintypes.POINT(x, y)), 2) == handle
        return x, y

    def drag(selector, dx, dy, fraction=.5, button="left"):
        assert user32.GetForegroundWindow() == handle
        x, y = position(selector, fraction)
        mouse.moveTo(x, y)
        mouse.dragTo(x + dx, y + dy, duration=.44, button=button)

    def tray_open():
        win32.PostMessage(shell.tray._hwnd, win32.WM_NOTIFY, 0, win32.WM_LBUTTONUP)

    try:
        assert wait(lambda: shell.tray.visible and shell.tray._hwnd)
        if user32.GetForegroundWindow() != handle:
            user32.SetForegroundWindow(handle)
            if user32.GetForegroundWindow() != handle:
                mouse.click(*position(".tb-grow"))
        assert wait(lambda: user32.GetForegroundWindow() == handle)
        x, y = window.x, window.y
        window.evaluate_js("document.querySelector('#ta').value = 'Selectable native composer sample text';")
        drag("#ta", 130, 0, .02)
        assert (window.x, window.y) == (x, y), "Dragging the composer moved the window"
        selected = window.evaluate_js("""(() => {
          const box = document.querySelector('#ta');
          return box.selectionEnd - box.selectionStart;
        })()""")
        assert selected > 0, "The composer did not select text"
        drag("#btnMin", -60, 42)
        assert (window.x, window.y) == (x, y) and not user32.IsIconic(handle)
        drag(".tb-grow", 65, 35, button="right")
        assert (window.x, window.y) == (x, y), "Right-click dragged the window"
        drag(".tb-grow", 65, 35)
        move = (window.x - x, window.y - y)
        assert move[0] >= 30 and move[1] >= 15, f"Title bar move was {move}"

        width, height = window.width, window.height
        drag('[data-window-edge="17"]', 88, 48)
        growth = (window.width - width, window.height - height)
        assert growth[0] >= 55 and growth[1] >= 27, f"Corner resize was {growth}"
        mouse.doubleClick(*position(".tb-grow"), interval=.08)
        assert wait(lambda: user32.IsZoomed(handle)), "Double-click did not maximize"
        mouse.click(*position("#btnMax"))
        assert wait(lambda: not user32.IsZoomed(handle)), "Maximize button did not restore"

        mouse.click(*position("#btnClose"))
        assert wait(lambda: not user32.IsWindowVisible(handle)), "Close button did not hide"
        with urllib.request.urlopen(health, timeout=1) as response:
            assert response.status == 200
        tray_open()
        assert wait(lambda: user32.IsWindowVisible(handle)), "Tray did not reopen the hidden window"
        mouse.click(*position("#btnMin"))
        assert wait(lambda: user32.IsIconic(handle)), "Minimize button failed"
        tray_open()
        assert wait(lambda: not user32.IsIconic(handle)), "Tray did not restore the minimized window"
        user32.PostMessageW(handle, 0x0010, 0, 0)
        assert wait(lambda: not user32.IsWindowVisible(handle)), "System close did not hide"
        list(shell.tray.menu)[1](shell.tray)
        print(f"NATIVE_OK title_delta={move} corner_delta={growth} text_selected={selected} "
              "title_controls=maximize_restore_minimize tray=hide_show_restore_quit", flush=True)
    finally:
        mouse.moveTo(*original_cursor, _pause=False)
        if original_focus and original_focus != handle:
            user32.SetForegroundWindow(original_focus)


def exercise_icons(shell, notifications):
    from System import IntPtr
    from System.Drawing import Icon
    from System.Drawing.Imaging import ImageFormat
    from System.IO import MemoryStream
    from PIL import Image
    from pystray._util import win32

    def pixels(icon):
        bitmap = icon.ToBitmap()
        with MemoryStream() as output:
            try:
                bitmap.Save(output, ImageFormat.Png)
                with Image.open(BytesIO(bytes(output.ToArray()))) as image:
                    rgba = image.convert("RGBA")
                    return rgba.size, rgba.tobytes()
            finally:
                bitmap.Dispose()

    def handle_pixels(handle):
        icon = Icon.FromHandle(IntPtr(handle))
        try:
            return pixels(icon)
        finally:
            icon.Dispose()

    u32 = shell.user32
    u32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    u32.SendMessageW.restype = wintypes.LPARAM
    handle = shell._handle()
    assert handle and u32.IsWindowVisible(handle), "Native window was not visible"
    assets = ROOT / "panel/web/assets"
    sizes = []
    for variant in (0, 1):  # ICON_SMALL and ICON_BIG
        displayed = u32.SendMessageW(handle, 0x007F, variant, 0)  # WM_GETICON
        assert displayed, f"Native icon variant {variant} was not attached"
        size, rgba = handle_pixels(displayed)
        expected = Icon(str(assets / "app-icon.ico"), *size)
        try:
            assert (size, rgba) == pixels(expected), f"Native icon variant {variant} differs from the brand"
        finally:
            expected.Dispose()
        sizes.append(size)

    read_app_id = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID
    read_app_id.argtypes = (ctypes.POINTER(wintypes.LPWSTR),)
    read_app_id.restype = ctypes.c_long
    app_id = wintypes.LPWSTR()
    assert read_app_id(ctypes.byref(app_id)) == 0
    free = ctypes.windll.ole32.CoTaskMemFree
    free.argtypes = (ctypes.c_void_p,)
    free.restype = None
    try:
        taskbar_id = app_id.value
        assert taskbar_id == "YuyuHut.Desktop"
    finally:
        free(app_id)

    assert wait(lambda: shell.tray.visible and notifications), "No successful Windows tray registration"
    tray_window, tray_icon = notifications[-1]
    assert tray_window == shell.tray._hwnd
    with tempfile.TemporaryDirectory(prefix="yy-icon-proof-") as folder:
        file = Path(folder) / "expected.ico"
        with Image.open(assets / "app-icon.png") as brand:
            brand.save(file)
        expected = win32.LoadImage(None, str(file), win32.IMAGE_ICON, 0, 0,
                                   win32.LR_DEFAULTSIZE | win32.LR_LOADFROMFILE)
        try:
            tray_size, tray_rgba = handle_pixels(tray_icon)
            assert (tray_size, tray_rgba) == handle_pixels(expected), "Registered tray icon differs from the brand"
        finally:
            win32.DestroyIcon(expected)
    print(f"NATIVE_ICONS_OK window={sizes} taskbar_id={taskbar_id} "
          f"tray={tray_size} compare=exact_pixels tray_registration=accepted", flush=True)
    shell.quit()


def main():
    if sys.platform != "win32":
        raise SystemExit("Windows desktop required")
    import panel
    from desktop_window import WindowsControls
    from pystray._util import win32

    original = WindowsControls.run
    icons_only = "--icons" in sys.argv
    errors = []
    notifications = []
    called = threading.Event()
    completed = threading.Event()
    health = lambda: f"http://127.0.0.1:{panel.界面端口}/health"

    def run(shell, webview):
        def check():
            if called.is_set():
                return
            called.set()
            try:
                if icons_only:
                    exercise_icons(shell, notifications)
                else:
                    exercise(shell, health())
            except BaseException as exc:
                errors.append(exc)
                shell.quit()
            finally:
                completed.set()

        shell.window.events.loaded += check
        original(shell, webview)

    WindowsControls.run = run
    data = Path(tempfile.mkdtemp(prefix="yy-native-test-"))
    config = data / "config.json"
    config.write_text('{"title": "Yuyu Hut Native Test"}', encoding="utf-8")
    sys.argv = [str(ROOT / "panel/panel.py"), "--config", str(config), "--data-dir", str(data), "--demo", "--desktop"]
    notify = win32.Shell_NotifyIcon

    def register(operation, details):
        result = notify(operation, details)
        if operation == win32.NIM_ADD and result and details.uFlags & win32.NIF_ICON:
            notifications.append((details.hWnd, details.hIcon))
        return result

    with patch.object(win32, "Shell_NotifyIcon", register):
        result = panel.main()
    assert completed.wait(5), "Desktop check was not run"
    if errors:
        raise errors[0]
    assert result == 0, result
    try:
        urllib.request.urlopen(health(), timeout=.5)
    except OSError:
        print("NATIVE_EXIT_OK panel_server=closed", flush=True)
    else:
        raise AssertionError("Panel port still responds after desktop quit")


if __name__ == "__main__":
    main()
