"""A system-wide hotkey, so the app is reachable when the tray icon is not.

Windows 10 hides newly-registered notification icons in the overflow flyout by
default, and on builds before the `NotifyIconSettings` registry layout there is
no supported way to promote one programmatically -- pinning it is a manual,
one-time user action. A chrome-free kiosk window has no taskbar button and no
Alt-Tab entry either, so without this there is a real state where the app is
running and genuinely unreachable.

RegisterHotKey with a NULL window posts WM_HOTKEY to the *thread's* queue, and
Tk's mainloop does not pump thread messages, so this needs its own thread with
its own GetMessage loop. The callback is handed back to the Tk thread by the
caller's `post`.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
from typing import Callable

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
# Without this, holding the combo down repeats it dozens of times a second.
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_MODIFIERS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "super": MOD_WIN,
}

_NAMED_KEYS = {
    "space": 0x20, "esc": 0x1B, "escape": 0x1B, "tab": 0x09,
    "enter": 0x0D, "return": 0x0D, "home": 0x24, "end": 0x23,
    "insert": 0x2D, "delete": 0x2E, "pause": 0x13,
}


def parse_combo(combo: str) -> tuple[int, int] | None:
    """'ctrl+alt+m' -> (modifiers, virtual-key). None if unparseable."""
    mods = 0
    key = None
    for part in str(combo).lower().replace(" ", "").split("+"):
        if not part:
            continue
        if part in _MODIFIERS:
            mods |= _MODIFIERS[part]
        elif part in _NAMED_KEYS:
            key = _NAMED_KEYS[part]
        elif len(part) == 1 and part.isalnum():
            key = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            key = 0x70 + int(part[1:]) - 1
        else:
            return None
    if key is None or mods == 0:
        # A bare key with no modifier would swallow that key system-wide.
        return None
    return mods | MOD_NOREPEAT, key


# Tried in order if the configured combo is already owned by another app.
# Verified free on the target machine; ctrl+alt+m was not (something else had it).
FALLBACKS = ("ctrl+alt+f9", "ctrl+shift+f9", "ctrl+alt+f12", "ctrl+shift+alt+m")


class HotKey:
    def __init__(self, combo: str, on_press: Callable[[], None]) -> None:
        self.combo = combo
        self.active_combo: str | None = None
        self._on_press = on_press
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ok = False

    def start(self) -> bool:
        """Register the configured combo, or the first free fallback.

        Hotkeys are a global, first-come resource: another app may already own
        the one in the config. Silently ending up with no shortcut is the worst
        outcome, since the tray icon may also be hidden -- so fall back and say
        which one actually took.
        """
        candidates = [self.combo] + [c for c in FALLBACKS if c != self.combo]
        for combo in candidates:
            parsed = parse_combo(combo)
            if parsed is None:
                print(f"[casebuddy] hotkey '{combo}' is not valid; skipping")
                continue
            ready = threading.Event()
            self._ok = False
            self._thread = threading.Thread(
                target=self._run, args=(parsed, ready, combo),
                name="casebuddy-hotkey", daemon=True,
            )
            self._thread.start()
            ready.wait(timeout=3.0)
            if self._ok:
                self.active_combo = combo
                if combo != self.combo:
                    print(f"[casebuddy] '{self.combo}' was taken; using '{combo}' instead")
                return True
        print("[casebuddy] no hotkey could be registered")
        return False

    def _run(self, parsed: tuple[int, int], ready: threading.Event,
             combo: str) -> None:
        mods, key = parsed
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._thread_id = kernel32.GetCurrentThreadId()

        hotkey_id = 1
        if not user32.RegisterHotKey(None, hotkey_id, mods, key):
            err = ctypes.get_last_error()
            # 1409 = ERROR_HOTKEY_ALREADY_REGISTERED: another app owns it.
            note = " (already taken by another app)" if err == 1409 else ""
            print(f"[casebuddy] could not register hotkey '{combo}'{note}")
            ready.set()
            return

        self._ok = True
        print(f"[casebuddy] hotkey '{combo}' opens settings")
        ready.set()

        try:
            msg = wt.MSG()
            while True:
                got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if got in (0, -1):  # WM_QUIT or error
                    break
                if msg.message == WM_HOTKEY:
                    try:
                        self._on_press()
                    except Exception as exc:
                        print(f"[casebuddy] hotkey handler failed: {exc!r}")
        finally:
            user32.UnregisterHotKey(None, hotkey_id)

    def stop(self) -> None:
        if self._thread_id:
            ctypes.WinDLL("user32").PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread_id = None


if __name__ == "__main__":  # smoke test
    for combo in ("ctrl+alt+m", "ctrl+shift+f9", "win+m", "m", "bogus+z"):
        print(f"{combo:16s} -> {parse_combo(combo)}")
