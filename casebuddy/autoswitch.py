"""Automatic preset switching: the panel reads the room.

Two conditions, each mapped to a preset name (or to "" for off):

    fullscreen   something is running fullscreen on another monitor -- a
                 game, a benchmark, a film. The gaming preset earns its keep
                 exactly when you cannot alt-tab to check the numbers.
    idle         no keyboard or mouse for N minutes, optionally only after
                 dark. The quiet preset for a machine nobody is looking at.

Fullscreen wins when both hold. Switches are IN MEMORY ONLY: config.json is
never written, and whatever layout and theme were on screen before the switch
are stashed and restored the moment the condition clears. Manually applying
anything from the settings window while a switch is active adopts your change
as the new baseline rather than fighting you for the screen.

Detection is three Win32 calls a poll -- GetForegroundWindow, a rect compare
against that window's monitor, GetLastInputInfo -- all microseconds, all on
the Tk thread, so there is nothing here to synchronise.

Each verdict must hold for two consecutive polls before it acts. Alt-tabbing
through a game flickers the foreground window; without the debounce that
flicker would rebuild the whole screen twice a second.
"""

from __future__ import annotations

import copy
import ctypes
import ctypes.wintypes as wt
import os
import time

from . import presets

DEFAULTS = {
    # Preset applied while a fullscreen app is in the foreground. "" = never.
    "fullscreen_preset": "",
    # Preset applied when input has been quiet for idle_minutes. "" = never.
    "idle_preset": "",
    "idle_minutes": 15,
    # Only count idleness after dark, using the weather day window --
    # day_starts / day_ends -- so a machine idling at noon keeps its screen.
    "idle_night_only": True,
    "poll_seconds": 5.0,
}


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def _idle_seconds() -> float:
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    ticks = ctypes.windll.kernel32.GetTickCount()
    return max(0, ticks - info.dwTime) / 1000.0


def _fullscreen_foreign_window() -> bool:
    """True when the foreground window fills its whole monitor and belongs to
    somebody else. Our own chrome-free panel fills its monitor by design, and
    the desktop (Progman / WorkerW) technically fills the screen too; both
    must not count, or the switcher would trigger on itself."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False

    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return False

    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, buf, 64)
    if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd",
                     "Windows.UI.Core.CoreWindow"):
        return False

    rect = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    if not monitor:
        return False

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                    ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    mon = info.rcMonitor
    # Within a couple of pixels: borderless-fullscreen games sit exactly on
    # the monitor rect, true-fullscreen sometimes one pixel proud of it.
    return (rect.left <= mon.left + 2 and rect.top <= mon.top + 2
            and rect.right >= mon.right - 2 and rect.bottom >= mon.bottom - 2)


def _parse_hhmm(text: str, fallback: float) -> float:
    try:
        hh, mm = str(text).split(":")
        return int(hh) + int(mm) / 60.0
    except (ValueError, AttributeError):
        return fallback


class AutoSwitcher:
    """Owns the poll loop and the stash. One per app."""

    def __init__(self, window) -> None:
        self.window = window
        self._after_id = None
        self._stash: tuple[dict, dict] | None = None   # (layout, theme)
        self._active: str | None = None                # preset currently forced
        self._pending: str | None = None               # last poll's verdict
        self._applied_cfg: object = None               # what we last handed over

    # --- config ------------------------------------------------------------

    def _options(self) -> dict:
        got = dict(DEFAULTS)
        got.update(self.window.cfg.get("autoswitch") or {})
        return got

    def _night(self) -> bool:
        wx = self.window.cfg.get("weather") or {}
        start = _parse_hhmm(wx.get("day_starts", "07:00"), 7.0)
        end = _parse_hhmm(wx.get("day_ends", "19:00"), 19.0)
        t = time.localtime()
        hour = t.tm_hour + t.tm_min / 60.0
        return not (start <= hour < end)

    # --- the loop ------------------------------------------------------------

    def start(self) -> None:
        self._schedule()

    def _schedule(self) -> None:
        opts = self._options()
        ms = max(2000, int(float(opts.get("poll_seconds", 5.0)) * 1000))
        try:
            self._after_id = self.window.root.after(ms, self._poll)
        except Exception:
            self._after_id = None      # window torn down; loop ends here

    def _verdict(self, opts: dict) -> str | None:
        """The preset the world is asking for right now, or None."""
        want = str(opts.get("fullscreen_preset") or "")
        if want in presets.PRESETS and _fullscreen_foreign_window():
            return want
        want = str(opts.get("idle_preset") or "")
        if want in presets.PRESETS:
            quiet = _idle_seconds() >= max(1.0, float(
                opts.get("idle_minutes", 15)) * 60.0)
            if quiet and (not opts.get("idle_night_only", True)
                          or self._night()):
                return want
        return None

    def _poll(self) -> None:
        try:
            opts = self._options()
            # Settings applied a config we did not hand over: whatever is on
            # screen now is the user's own choice. Adopt it as the baseline.
            if self._active and self.window.cfg is not self._applied_cfg:
                self._active = None
                self._stash = None

            verdict = self._verdict(opts)
            if verdict == self._pending:
                if verdict != self._active:
                    self._switch(verdict)
            self._pending = verdict
        except Exception as exc:      # never let the watcher kill the app
            print(f"[casebuddy] autoswitch error: {exc!r}")
        self._schedule()

    def _switch(self, verdict: str | None) -> None:
        # apply_live, not apply_config: a preset swap changes what is drawn,
        # not what is measured, and rebuilding the collector would blank the
        # readings for a second every time a game starts.
        window = self.window
        if verdict is not None:
            if self._stash is None:
                self._stash = (copy.deepcopy(window.cfg.get("layout", {})),
                               copy.deepcopy(window.cfg.get("theme", {})))
            cfg = presets.apply(window.cfg, verdict)
            print(f"[casebuddy] autoswitch: {verdict}")
        else:
            cfg = copy.deepcopy(window.cfg)
            if self._stash is not None:
                cfg["layout"], cfg["theme"] = self._stash
                self._stash = None
            print("[casebuddy] autoswitch: restored")
        self._active = verdict
        window.apply_live(cfg)
        # apply_live folds the change into the window's own dict; a settings
        # Save & Apply replaces that dict wholesale, which is how the poll
        # above notices the user has taken back the controls.
        self._applied_cfg = window.cfg
