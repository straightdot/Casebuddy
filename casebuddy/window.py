"""The Tk window: picks the right physical monitor, goes chrome-free, repaints.

Windows-specific bits are done through ctypes rather than pywin32 so the app
still runs if pywin32 is missing (it is only really needed for the LHM WMI
transport).
"""

from __future__ import annotations

import copy
import ctypes
import ctypes.wintypes as wt
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass

from . import appicon, theme
from .collector import Collector
from .dashboard import make_scene
from .settings_ui import SettingsWindow

MONITORINFOF_PRIMARY = 1


@dataclass
class Monitor:
    device: str
    x: int
    y: int
    width: int
    height: int
    primary: bool

    @property
    def area(self) -> int:
        return self.width * self.height

    def __str__(self) -> str:
        tag = " (primary)" if self.primary else ""
        return f"{self.device}  {self.width}x{self.height} at {self.x},{self.y}{tag}"


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


def enable_dpi_awareness() -> str:
    """Make Tk geometry mean real pixels.

    Without this, a DPI-unaware process gets virtualized coordinates on a
    scaled desktop and a request for "1920x1080 at x=2560" lands somewhere
    else entirely. Must run before Tk creates its first window. Tk 9 may
    already declare awareness via its manifest, in which case these calls
    fail harmlessly.
    """
    try:
        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (Win10 1703+)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except Exception:
        pass
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "already set or unavailable"


def list_monitors() -> list[Monitor]:
    """Physical monitors in enumeration order."""
    found: list[Monitor] = []

    proc_type = ctypes.WINFUNCTYPE(
        ctypes.c_int, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM
    )

    def callback(hmonitor, _hdc, _rect, _param):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if ctypes.windll.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            found.append(
                Monitor(
                    device=info.szDevice,
                    x=r.left,
                    y=r.top,
                    width=r.right - r.left,
                    height=r.bottom - r.top,
                    primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                )
            )
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(0, None, proc_type(callback), 0)
    return found


def choose_monitor(monitors: list[Monitor], selector) -> Monitor | None:
    """Resolve a config selector to one monitor.

    "auto" picks the smallest non-primary screen, which on a machine with a
    case panel bolted on is essentially always the panel.
    """
    if not monitors:
        return None
    if isinstance(selector, int) and not isinstance(selector, bool):
        if 1 <= selector <= len(monitors):
            return monitors[selector - 1]
        return None
    text = str(selector).strip().lower()
    if text == "primary":
        return next((m for m in monitors if m.primary), monitors[0])
    if text == "auto":
        secondary = [m for m in monitors if not m.primary]
        if secondary:
            return min(secondary, key=lambda m: m.area)
        return monitors[0]
    if text.isdigit():
        return choose_monitor(monitors, int(text))
    return next((m for m in monitors if m.device.lower() == text), None)


class MonitorWindow:
    """Fullscreen, chrome-free dashboard window."""

    def __init__(self, cfg: dict, collector: Collector, calibrate: bool = False,
                 windowed: bool = False) -> None:
        self.cfg = cfg
        self.collector = collector
        self.calibrate = calibrate
        self.windowed = windowed
        self.squash = str(cfg["display"]["aspect_fix"]).lower() == "squash43"

        # Colours are module globals that the drawing code reads at paint time,
        # so the palette has to be installed before anything is built.
        theme.apply(cfg.get("theme", {}), night=0.0)

        self.dpi_mode = enable_dpi_awareness()
        self.monitors = list_monitors()
        self.target = choose_monitor(self.monitors, cfg["display"]["monitor"])

        self.root = tk.Tk()
        self.root.title("CaseBuddy")
        self.root.configure(bg=theme.BG)
        # default=True, so the settings window and every dialog inherit it.
        appicon.apply_to(self.root)

        self.canvas: tk.Canvas | None = None
        # Whichever renderer make_scene picked; both honour build()/update().
        self.dash = None
        self.tray = None
        self.hotkey = None
        self._settings: SettingsWindow | None = None
        self._after_id = None
        self._watch_id = None
        self._watch_ms = 3000
        # _place() records into this, so it has to exist first -- otherwise the
        # initial placement is written and then immediately reset to None, and
        # the display watcher sees a spurious change on its first tick.
        self._placed_at: tuple | None = None
        self._layout_sig = self._layout_signature()
        # Quantised dim level currently baked into the palette. The buddy scene
        # dims its own mood colours; this is for everything the theme owns.
        self._night = 0.0
        self._interval = max(100, int(1000 / max(0.2, float(cfg["refresh"]["ui_hz"]))))
        # What _tick actually waits on. An animated scene lowers it in _build.
        self._frame_ms = self._interval

        self._place()
        self._bind_keys()
        self._build()

    # --- display following -------------------------------------------------

    @staticmethod
    def _layout_signature() -> tuple:
        """Everything about the monitor arrangement that could move us."""
        try:
            return tuple(
                (m.device, m.x, m.y, m.width, m.height, m.primary)
                for m in list_monitors()
            )
        except Exception:
            return ()

    def _watch_displays(self) -> None:
        """Re-place the window when the monitor arrangement changes.

        Windows does not merely blank a display you switch off -- it removes it
        from the desktop entirely and renumbers the rest. Turning the main
        monitor off moved the panel from x=2560 to x=0; turning it back on moved
        it back. Reading the layout once at startup left the window stranded at
        coordinates that no longer existed, off-screen, with no way to tell.

        This also removes the boot race: the autostart task fires 25 s after
        logon, and if the panel has not finished handshaking by then the window
        would previously place itself on whatever existed at that instant and
        stay wrong until somebody noticed.
        """
        try:
            signature = self._layout_signature()
            if signature != self._layout_sig:
                self._layout_sig = signature
                self.monitors = list_monitors()
                self.target = choose_monitor(self.monitors, self.cfg["display"]["monitor"])
                want = None
                if self.target is not None:
                    want = (self.target.x, self.target.y,
                            self.target.width, self.target.height)
                if want != self._placed_at:
                    print(f"[casebuddy] display layout changed; re-placing at {want}")
                    self._place()
                    self._build()
                    return  # _build reschedules the watcher
        except Exception as exc:
            print(f"[casebuddy] display watch error: {exc!r}")
        self._watch_id = self.root.after(self._watch_ms, self._watch_displays)

    # --- thread bridge ----------------------------------------------------

    def post(self, fn) -> None:
        """Run `fn` on the Tk thread.

        The tray runs its own message pump on another thread, and Tk is not
        thread-safe. Every tray action comes through here.
        """
        try:
            self.root.after(0, fn)
        except tk.TclError:
            pass  # window already gone

    # --- actions the tray exposes -----------------------------------------

    def open_settings(self) -> None:
        if self._settings is not None:
            try:
                self._settings.win.deiconify()
                self._settings.win.lift()
                self._settings.win.focus_force()
                return
            except tk.TclError:
                self._settings = None
        self.monitors = list_monitors()
        self._settings = SettingsWindow(
            self.root, self.cfg, self.monitors, self.apply_config,
            collector=self.collector, on_live=self.apply_live,
        )

    def apply_config(self, cfg: dict) -> None:
        """Adopt a new config without restarting the process.

        The collector is rebuilt rather than mutated: poll rates, endpoint and
        transport are all baked in at construction, and swapping a live one
        field by field is how you get a half-applied config.
        """
        self.cfg = cfg
        self.squash = str(cfg["display"]["aspect_fix"]).lower() == "squash43"
        self._interval = max(100, int(1000 / max(0.2, float(cfg["refresh"]["ui_hz"]))))
        theme.apply(cfg.get("theme", {}), night=self._night)

        old = self.collector
        self.collector = Collector(cfg)
        self.collector.start()
        old.stop()
        # An open settings window is holding the one we just stopped.
        if self._settings is not None:
            try:
                self._settings.win.winfo_exists()
                self._settings.set_collector(self.collector)
            except (tk.TclError, AttributeError):
                self._settings = None

        self.monitors = list_monitors()
        selector = cfg["display"]["monitor"]
        self.target = choose_monitor(self.monitors, selector)
        if self.target is None:
            print(f"[casebuddy] no display matches {selector!r}; "
                  f"have {[m.device for m in self.monitors]}")
        self._place()
        self._build()

    def apply_live(self, cfg: dict) -> None:
        """Adopt purely visual settings without rebuilding the collector.

        Used while the Layout and Theme tabs are being edited, so the panel
        follows along as you click. Deliberately narrow: it touches only what
        can be redrawn, never poll rates or the sensor endpoint, because
        restarting the collector on every keystroke would stutter the readings
        and re-probe the network.
        """
        self.cfg["layout"] = copy.deepcopy(cfg.get("layout", self.cfg["layout"]))
        self.cfg["theme"] = copy.deepcopy(cfg.get("theme", self.cfg["theme"]))
        self.cfg["thresholds"] = copy.deepcopy(
            cfg.get("thresholds", self.cfg["thresholds"]))
        self.cfg["display"]["text_scale"] = cfg.get("display", {}).get(
            "text_scale", self.cfg["display"].get("text_scale", 1.0))
        theme.apply(self.cfg["theme"], night=self._night)
        # The collector reads cfg on every build, so pointing it at the updated
        # dict is enough for new slots to start producing readings.
        self.collector.cfg = self.cfg
        self._build()

    def move_to(self, selector: str) -> None:
        self.cfg["display"]["monitor"] = selector
        self.monitors = list_monitors()
        self.target = choose_monitor(self.monitors, selector)
        self._place()
        self._build()

    def reload_config(self) -> None:
        from . import config as config_module

        self.apply_config(config_module.load())

    # --- window placement -------------------------------------------------

    def _place(self) -> None:
        cfg = self.cfg["display"]
        if self.windowed:
            # Half-scale preview on the primary screen, for iterating on the
            # layout without walking over to the case.
            self.width, self.height = 960, 540
            self.root.geometry(f"{self.width}x{self.height}+80+80")
            return

        override = cfg.get("geometry")
        if override:
            self.root.overrideredirect(True)
            self.root.geometry(str(override))
            self.root.update_idletasks()
            self.width = self.root.winfo_width()
            self.height = self.root.winfo_height()
            return

        target = self.target
        if target is None:
            print("[casebuddy] no matching monitor; falling back to primary")
            target = next((m for m in self.monitors if m.primary), None)
        if target is None:
            self.width, self.height = 1280, 720
            self.root.geometry(f"{self.width}x{self.height}+0+0")
            return

        self.width, self.height = target.width, target.height
        # overrideredirect before geometry: it drops the title bar AND keeps the
        # window out of the taskbar and Alt-Tab, which is what a permanent
        # appliance display wants. It is also deterministic about which monitor
        # it lands on, unlike -fullscreen.
        want = f"{target.width}x{target.height}+{target.x}+{target.y}"
        self.root.overrideredirect(True)
        self.root.geometry(want)
        # FLUSH BEFORE TOUCHING ATTRIBUTES. Setting -topmost re-asserts the
        # window rect through SetWindowPos, and a geometry request Tk has not
        # processed yet is simply overwritten by it: the new SIZE survives, the
        # new POSITION does not, and the window sits on the old monitor at the
        # new monitor's size. That is why picking a display appeared to do
        # nothing at all -- the config was right and the window never moved.
        self.root.update_idletasks()
        self._placed_at = (target.x, target.y, target.width, target.height)

        if cfg.get("topmost", True):
            self.root.attributes("-topmost", True)
        if cfg.get("hide_cursor", True):
            self.root.configure(cursor="none")

        # Belt and braces: confirm it actually landed, and say so if it did not
        # rather than leaving a panel quietly on the wrong screen.
        self.root.update_idletasks()
        if (self.root.winfo_rootx(), self.root.winfo_rooty()) != (target.x, target.y):
            self.root.geometry(want)
            self.root.update_idletasks()
        landed = (self.root.winfo_rootx(), self.root.winfo_rooty())
        if landed != (target.x, target.y):
            print(f"[casebuddy] wanted {want} on {target.device} but landed at "
                  f"+{landed[0]}+{landed[1]}")

    def _bind_keys(self) -> None:
        for seq in ("<Escape>", "<q>", "<Q>"):
            self.root.bind(seq, lambda _e: self.quit())
        self.root.bind("<c>", lambda _e: self.toggle_squash())
        self.root.bind("<C>", lambda _e: self.toggle_squash())
        self.root.bind("<F5>", lambda _e: self._build())
        # An overrideredirect window does not take focus on its own, so key
        # bindings would never fire without this.
        self.root.after(200, self.root.focus_force)

    # --- canvas lifecycle -------------------------------------------------

    def _build(self) -> None:
        for attr in ("_after_id", "_watch_id"):
            handle = getattr(self, attr, None)
            if handle is not None:
                self.root.after_cancel(handle)
                setattr(self, attr, None)
        if self.canvas is not None:
            self.canvas.destroy()

        self.geo = theme.Geometry(
            self.width, self.height, squash=self.squash,
            text_scale=float(self.cfg["display"].get("text_scale", 1.0)),
        )
        self.root.configure(bg=theme.BG)
        self.canvas = tk.Canvas(
            self.root, width=self.width, height=self.height,
            bg=theme.BG, highlightthickness=0, bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        if self.calibrate:
            self.dash = None
            self._draw_calibration()
            self._start_watch()
            return

        # The layout decides which renderer this is; both honour the same
        # build() / update(snap) contract.
        self.dash = make_scene(self.canvas, self.geo, self.cfg)
        self.dash.build()
        # A scene may ask to repaint faster than sensors are polled -- the
        # buddy character bobs and blinks between readings. It may never ask to
        # go slower, so ui_hz stays the floor.
        wanted = getattr(self.dash, "frame_ms", 0)
        self._frame_ms = min(self._interval, wanted) if wanted else self._interval
        self._tick()
        self._start_watch()

    def _start_watch(self) -> None:
        # Pointless in windowed preview mode, which is not pinned to a screen.
        if self.windowed or not self.cfg["display"].get("follow_displays", True):
            return
        seconds = float(self.cfg["display"].get("follow_poll_seconds", 3.0))
        self._watch_ms = max(500, int(seconds * 1000))
        self._watch_id = self.root.after(self._watch_ms, self._watch_displays)

    def toggle_squash(self) -> None:
        self.squash = not self.squash
        self._build()

    # --- frame loop -------------------------------------------------------

    def _tick(self) -> None:
        try:
            if self.dash is not None:
                snap = self.collector.latest()
                self.dash.update(snap)
                self._night_dim(snap)
        except Exception as exc:  # never let a paint error kill the display
            print(f"[casebuddy] repaint error: {exc!r}")
        self._after_id = self.root.after(self._frame_ms, self._tick)

    def _night_dim(self, snap) -> None:
        """Fade the theme palette down after dark.

        Static canvas items -- labels, tracks, rules -- take their colour at
        build time, so a palette change only reaches them through a rebuild.
        That is why the level is quantised to twentieths: across a whole dusk
        it costs a handful of rebuilds rather than one per frame.
        """
        want = float(self.cfg["display"].get("night_dim", 0.0) or 0.0)
        if want <= 0.0:
            if self._night:
                self._night = 0.0
                theme.apply(self.cfg.get("theme", {}), night=0.0)
                self._build()
            return
        weather = getattr(snap, "weather", None)
        if weather is None or not getattr(weather, "ok", False):
            return
        daylight = float(getattr(weather, "daylight", 1.0))
        level = round(max(0.0, min(0.8, want)) * (1.0 - daylight) / 0.05) * 0.05
        if abs(level - self._night) < 0.001:
            return
        self._night = level
        theme.apply(self.cfg.get("theme", {}), night=level)
        self._build()

    def run(self) -> None:
        self.root.mainloop()

    def quit(self) -> None:
        for attr in ("_after_id", "_watch_id"):
            handle = getattr(self, attr, None)
            if handle is not None:
                try:
                    self.root.after_cancel(handle)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        if self.hotkey is not None:
            self.hotkey.stop()
            self.hotkey = None
        if self.tray is not None:
            self.tray.stop()
            self.tray = None
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # --- calibration ------------------------------------------------------

    def _draw_calibration(self) -> None:
        """A pattern that answers one question: does this panel stretch or letterbox?

        The panel is 4:3 but is fed 16:9. If its scaler letterboxes, the circle
        is round. If it stretches, the circle is a vertical egg and the square
        is a portrait rectangle -- in which case aspect_fix should be squash43.
        """
        g, c = self.geo, self.canvas
        assert c is not None
        families = set(tkfont.families(c))
        names = theme.Fonts(families)
        f_big = (names.label, g.font(64), "bold")
        f_mid = (names.label, g.font(50), "normal")

        c.create_text(
            g.x(960), g.y(70), anchor="n", fill=theme.TEXT, font=f_big,
            text="ASPECT CALIBRATION",
        )

        # Square, 420x420 in design space.
        c.create_rectangle(
            g.x(180), g.y(330), g.x(600), g.y(750),
            outline=theme.OK, width=g.stroke(10),
        )
        c.create_text(g.x(390), g.y(790), anchor="n", fill=theme.TEXT_DIM,
                      font=f_mid, text="SQUARE?")

        # Circle, 420 diameter.
        c.create_oval(
            g.x(750), g.y(330), g.x(1170), g.y(750),
            outline=theme.TEXT, width=g.stroke(10),
        )
        c.create_line(g.x(960), g.y(330), g.x(960), g.y(750),
                      fill=theme.PANEL_EDGE, width=g.stroke(4))
        c.create_line(g.x(750), g.y(540), g.x(1170), g.y(540),
                      fill=theme.PANEL_EDGE, width=g.stroke(4))
        c.create_text(g.x(960), g.y(790), anchor="n", fill=theme.TEXT_DIM,
                      font=f_mid, text="ROUND?")

        # 3x3 grid of 140px cells -- easy to eyeball for square-ness.
        for i in range(4):
            step = 140 * i
            c.create_line(g.x(1320 + step), g.y(330), g.x(1320 + step), g.y(750),
                          fill=theme.WARN, width=g.stroke(5))
            c.create_line(g.x(1320), g.y(330 + step), g.x(1740), g.y(330 + step),
                          fill=theme.WARN, width=g.stroke(5))
        c.create_text(g.x(1530), g.y(790), anchor="n", fill=theme.TEXT_DIM,
                      font=f_mid, text="SQUARE CELLS?")

        mode = "squash43" if self.squash else "none"
        c.create_text(
            g.x(960), g.y(900), anchor="n", fill=theme.OK if not self.squash else theme.WARN,
            font=f_big, text=f'aspect_fix = "{mode}"',
        )
        c.create_text(
            g.x(960), g.y(985), anchor="n", fill=theme.TEXT_DIM, font=f_mid,
            text="Shapes correct?  Keep this setting.    Press C to toggle    ESC to exit",
        )


if __name__ == "__main__":  # python -m casebuddy.window -> print monitor table
    enable_dpi_awareness()
    for i, mon in enumerate(list_monitors(), 1):
        print(f"{i}. {mon}")
