"""Compact, topmost Windows windows for this parser's visible Chromium only."""
import asyncio
import logging
import sys

WINDOW_WIDTH = 520
WINDOW_HEIGHT = 640
logger = logging.getLogger('AvitoBrowserWindow')


def pin_windows(process_id):
    """Return pinned HWNDs belonging to the exact Chromium browser PID."""
    if sys.platform != 'win32':
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE

    class MonitorInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                    ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]

    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    pinned = []

    @callback_type
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != process_id or not user32.IsWindowVisible(hwnd):
            return True
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, len(name))
        if not name.value.startswith('Chrome_WidgetWin_'):
            return True
        if user32.GetWindowLongW(hwnd, -20) & 0x8:  # WS_EX_TOPMOST
            pinned.append(hwnd)
            return True  # Preserve a size/position the user already chose.
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return True
        work = info.rcWork
        width = min(WINDOW_WIDTH, work.right - work.left)
        height = min(WINDOW_HEIGHT, work.bottom - work.top)
        x = max(work.left, work.right - width - 12)
        y = work.top + min(12, max(0, work.bottom - work.top - height))
        # TOPMOST persists when focus moves away; do not repeatedly steal focus or resize.
        if user32.SetWindowPos(hwnd, wintypes.HWND(-1), x, y, width, height, 0x0010 | 0x0040):
            pinned.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return pinned


async def prepare_visible_window(page):
    if sys.platform != 'win32':
        return False
    session = None
    try:
        session = await page.context.browser.new_browser_cdp_session()
        result = await session.send('SystemInfo.getProcessInfo')
        process_id = next(int(item['id']) for item in result['processInfo'] if item['type'] == 'browser')
        pinned = False
        for _ in range(20):
            # A popup page can precede its native window. Keep checking even if
            # the browser already owns another topmost window.
            pinned = bool(await asyncio.to_thread(pin_windows, process_id)) or pinned
            await asyncio.sleep(.1)
        if pinned:
            return True
        logger.warning('Не удалось закрепить видимое окно браузера поверх остальных окон')
    except Exception as exc:
        logger.warning('Не удалось настроить видимое окно браузера: %s', type(exc).__name__)
    finally:
        if session is not None:
            try:
                await session.detach()
            except Exception:
                pass
    return False
