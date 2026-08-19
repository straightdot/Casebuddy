"""Notification-area icon, driven straight through Shell_NotifyIcon.

WHY NOT pystray
---------------
pystray adds the icon correctly and then never checks on it again. Measured on
this machine, the icon is dropped from the tray somewhere between 10 and 45
seconds after being added, while pystray keeps reporting `visible == True`.
Enumerating Explorer's own tray toolbars confirmed it: gone from both the
visible strip AND the overflow flyout, with the process still running. Because
pystray does not notice, it never re-adds, so the app becomes unreachable.

Doing it by hand means the icon can be made SELF-HEALING, which is the whole
point of the rewrite:

  * a WM_TIMER every few seconds re-asserts the icon with NIM_MODIFY, and falls
    back to NIM_ADD the moment that fails -- which is exactly what happens once
    Explorer has forgotten us;
  * WM_TASKBARCREATED (broadcast when Explorer restarts) triggers a re-add;
  * ChangeWindowMessageFilterEx opts in to receiving that broadcast even when
    the process is elevated, which a UIPI-filtered process otherwise would not.

The app already talks Win32 directly for display enumeration and the global
hotkey, so this adds no new dependency -- it removes one.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
from typing import Callable

from . import appicon

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- Win32 constants ------------------------------------------------------

WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_QUIT = 0x0012
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
TRAY_CALLBACK = WM_APP + 1

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040

MF_STRING, MF_SEPARATOR, MF_POPUP, MF_GRAYED = 0x0000, 0x0800, 0x0010, 0x0001
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100

MSGFLT_ALLOW = 1
IDT_HEARTBEAT = 1

# Menu command ids
ID_SETTINGS, ID_SQUASH, ID_RELOAD, ID_QUIT = 1, 2, 3, 4
ID_MON_AUTO = 90
ID_MON_BASE = 100


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HANDLE),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wt.HANDLE),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wt.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HANDLE),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HANDLE),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.CreateWindowExW.restype = wt.HWND
user32.LoadImageW.restype = wt.HANDLE
user32.CreatePopupMenu.restype = wt.HMENU
user32.TrackPopupMenu.restype = ctypes.c_int
shell32.Shell_NotifyIconW.restype = wt.BOOL
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]


class Tray:
    def __init__(
        self,
        post: Callable[[Callable[[], None]], None],
        on_settings: Callable[[], None],
        on_quit: Callable[[], None],
        on_reload: Callable[[], None],
        on_move: Callable[[str], None],
        on_toggle_squash: Callable[[], None],
        list_monitors: Callable[[], list],
        heartbeat_seconds: float = 5.0,
    ) -> None:
        self._post = post
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._on_reload = on_reload
        self._on_move = on_move
        self._on_toggle_squash = on_toggle_squash
        self._list_monitors = list_monitors
        self._heartbeat_ms = max(1000, int(heartbeat_seconds * 1000))

        self.available = True
        self.readds = 0  # how many times Explorer dropped us and we recovered

        self._hwnd = None
        self._hicon = None
        self._nid: NOTIFYICONDATAW | None = None
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._wndproc_ref: WNDPROC | None = None
        self._monitors_cache: list = []
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._ready = threading.Event()
        self._ok = False

    # --- lifecycle --------------------------------------------------------

    def start(self) -> bool:
        self._thread = threading.Thread(target=self._run, name="casebuddy-tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)
        return self._ok

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread_id = None

    # --- the icon ---------------------------------------------------------

    def _make_nid(self, flags: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = TRAY_CALLBACK
        nid.hIcon = self._hicon
        nid.szTip = "casebuddy - system monitor"
        return nid

    def _add_icon(self) -> bool:
        nid = self._make_nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        return bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

    def _heartbeat(self) -> None:
        """Re-assert the icon; re-add it if Explorer has forgotten us.

        NIM_MODIFY fails when the icon is no longer registered, which is the
        signal that we were dropped. This is the entire reason for the rewrite.
        """
        nid = self._make_nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        if not shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)):
            if self._add_icon():
                self.readds += 1
                print(f"[casebuddy] tray icon was dropped; re-added (#{self.readds})")

    # --- menu -------------------------------------------------------------

    def _show_menu(self) -> None:
        menu = user32.CreatePopupMenu()
        submenu = user32.CreatePopupMenu()

        self._monitors_cache = []
        try:
            self._monitors_cache = self._list_monitors()
        except Exception:
            pass
        user32.AppendMenuW(submenu, MF_STRING, ID_MON_AUTO, "Auto (smallest secondary)")
        for index, mon in enumerate(self._monitors_cache, 1):
            label = f"{index}. {mon.width}x{mon.height}"
            if mon.primary:
                label += "  (primary)"
            user32.AppendMenuW(submenu, MF_STRING, ID_MON_BASE + index, label)

        user32.AppendMenuW(menu, MF_STRING, ID_SETTINGS, "Settings...")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_POPUP, submenu, "Move to display")
        user32.AppendMenuW(menu, MF_STRING, ID_SQUASH, "Toggle 4:3 correction")
        user32.AppendMenuW(menu, MF_STRING, ID_RELOAD, "Reload config")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "Quit")

        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # Required, or the menu will not dismiss when you click elsewhere.
        user32.SetForegroundWindow(self._hwnd)
        cmd = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, 0, self._hwnd, None
        )
        user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if cmd:
            self._dispatch(cmd)

    def _dispatch(self, cmd: int) -> None:
        if cmd == ID_SETTINGS:
            self._post(self._on_settings)
        elif cmd == ID_SQUASH:
            self._post(self._on_toggle_squash)
        elif cmd == ID_RELOAD:
            self._post(self._on_reload)
        elif cmd == ID_QUIT:
            self._post(self._on_quit)
        elif cmd == ID_MON_AUTO:
            self._post(lambda: self._on_move("auto"))
        elif cmd >= ID_MON_BASE:
            index = cmd - ID_MON_BASE
            self._post(lambda n=index: self._on_move(str(n)))

    # --- window + message loop --------------------------------------------

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == TRAY_CALLBACK:
            event = lparam & 0xFFFF
            if event in (WM_RBUTTONUP, WM_LBUTTONUP):
                self._show_menu()
            elif event == WM_LBUTTONDBLCLK:
                self._post(self._on_settings)
            return 0
        if msg == WM_TIMER and wparam == IDT_HEARTBEAT:
            self._heartbeat()
            return 0
        if msg == self._taskbar_created:
            # Explorer restarted; every icon must be registered again.
            self._add_icon()
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self) -> None:
        try:
            self._thread_id = kernel32.GetCurrentThreadId()
            hinst = kernel32.GetModuleHandleW(None)

            self._wndproc_ref = WNDPROC(self._wndproc)  # must outlive the window
            cls = WNDCLASSW()
            cls.lpfnWndProc = self._wndproc_ref
            cls.hInstance = hinst
            cls.lpszClassName = "casebuddy_tray_wnd"
            if not user32.RegisterClassW(ctypes.byref(cls)):
                err = ctypes.get_last_error()
                if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                    print(f"[casebuddy] tray: RegisterClassW failed ({err})")
                    self._ready.set()
                    return

            self._hwnd = user32.CreateWindowExW(
                0, "casebuddy_tray_wnd", "casebuddy", 0, 0, 0, 0, 0, None, None, hinst, None
            )
            if not self._hwnd:
                print(f"[casebuddy] tray: CreateWindowExW failed ({ctypes.get_last_error()})")
                self._ready.set()
                return

            # Elevated processes are UIPI-filtered out of broadcasts by default,
            # so opt in explicitly or an Explorer restart would lose the icon.
            try:
                user32.ChangeWindowMessageFilterEx(
                    self._hwnd, self._taskbar_created, MSGFLT_ALLOW, None)
            except Exception:
                pass

            self._hicon = user32.LoadImageW(
                None, appicon.ico_path(), IMAGE_ICON, 0, 0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            )
            if not self._add_icon():
                print(f"[casebuddy] tray: Shell_NotifyIcon ADD failed "
                      f"({ctypes.get_last_error()})")
                self._ready.set()
                return

            user32.SetTimer(self._hwnd, IDT_HEARTBEAT, self._heartbeat_ms, None)
            self._ok = True
            self._ready.set()

            msg = wt.MSG()
            while True:
                got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if got in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            print(f"[casebuddy] tray thread failed: {exc!r}")
            self._ready.set()
        finally:
            if self._hwnd:
                nid = self._make_nid(0)
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                user32.DestroyWindow(self._hwnd)
                self._hwnd = None

    def notify(self, message: str, title: str = "CaseBuddy") -> None:
        """Balloon tips are not used; kept so callers do not need to care."""
        return
