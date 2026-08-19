"""Settings window.

Native ttk look on purpose: this is an ordinary desktop window on an ordinary
monitor, and styling it like the dashboard would only make it harder to read.
It always opens on the PRIMARY display -- a form rendered on a 4.3" panel would
be unusable, and the kiosk window is topmost there anyway.

SEVEN TABS, IN THE ORDER YOU WOULD USE THEM
-------------------------------------------
    Presets    pick a whole screen
    Layout     point each tile at a sensor, add and remove tiles
    Character  the buddy screen: face, moods, what the stress meter reads
    Look       colours, text size, night dimming
    Data       where readings come from, and when they count as a problem
    Screen     which monitor, what resolution, how the window behaves
    About      diagnostics

Simple fields come from SCHEMA, which is grouped: a tab is a list of titled
groups, so nothing is a flat wall of twenty rows and no setting sits under a
heading that does not describe it. Adding one is still a single line. The
colour grid, the preset gallery, the mood editor, the resolution picker and the
About page are hand-built, because they need behaviour a schema cannot express.
"""

from __future__ import annotations

import copy
import os
import shutil
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable

from . import appicon, buddy, catalog, config, display, emoji, presets, rigs, \
    scenes, theme, themepack
from .sources import weather
from .emoji_picker import EmojiPicker

PAD = {"padx": (0, 10), "pady": 3}
HINT = "#6b6b6b"
# The preset preview: 16:9 like the panel, sized to the blurb column.
PREVIEW_W, PREVIEW_H = 460, 259
# Hints wrap instead of stretching the window. Before this the longest hint on
# a tab set the width of the whole settings window, which is how it grew past
# 1500 px for the sake of one sentence.
HINT_WRAP = 260

# (path, label, kind, options, hint)
#   kind: monitor | choice | bool | float | int | text | pair | scale
# A tab is [(group title, [fields])].
SCHEMA: dict[str, list[tuple[str, list[tuple]]]] = {
    "Look": [
        ("Type", [
            (("theme", "fonts", "numeral"), "Numeral font", "family", None,
             "The big readings. Blank picks the best installed condensed face"),
            (("theme", "fonts", "label"), "Label font", "family", None,
             "Titles, units and secondary lines"),
            (("theme", "font_scale", "numeral"), "Numeral size", "float", None,
             "0.5 to 2.0, on top of text scale"),
            (("theme", "font_scale", "label"), "Label size", "float", None,
             "0.5 to 2.0"),
        ]),
        ("Size and brightness", [
            (("display", "text_scale"), "Text scale", "scale", (0.6, 1.6),
             "Weight of text and strokes. The layout grid does not change"),
            (("display", "night_dim"), "Dim after dark", "float", None,
             "0 to 0.8. Follows real daylight, so it needs a sky source"),
            (("display", "date_format"), "Date format", "text", None,
             "strftime. %a %d %b gives Mon 17 Aug"),
            (("display", "clock_format"), "Clock format", "text", None,
             "%#I:%M %p gives 8:08 PM. %H:%M gives 20:08"),
        ]),
    ],
    "Data": [
        ("Sensor source", [
            (("lhm", "enabled"), "Use LibreHardwareMonitor", "bool", None,
             "Every hardware reading comes from it"),
            (("lhm", "transport"), "Transport", "choice",
             ["http", "auto", "wmi", "off"],
             "http; LHM 0.9.6 dropped the WMI namespace"),
            (("lhm", "http_url"), "Endpoint", "text", None, ""),
            (("refresh", "fast_poll_hz"), "Sensor polls per second", "float", None,
             "One LHM round trip costs about 18 ms"),
            (("refresh", "ui_hz"), "Repaints per second", "float", None,
             "2 is plenty. The character screen sets its own rate"),
        ]),
        ("Weather and daylight", [
            (("weather", "sky"), "Sky source", "choice", ["weather", "clock", "off"],
             "weather = live conditions; clock = daylight only, no network"),
            (("weather", "provider"), "Provider", "choice",
             ["open-meteo", "openweather"], "open-meteo needs no account or key"),
            (("weather", "api_key"), "API key", "text", None, "OpenWeather only"),
            (("weather", "location"), "Location", "text", None,
             "auto looks it up from your IP address; 23.03,72.59 does not"),
            (("weather", "place"), "Place name", "text", None,
             "Blank uses whatever the lookup returns"),
            (("weather", "refresh_minutes"), "Refresh every", "float", None,
             "Minutes"),
            (("weather", "line_format"), "Outdoor line", "text", None,
             "{place}{sep}{temp}{sep}{sky}. Also feels, wind, humidity, "
             "sunrise, sunset, daylight, condition"),
            (("weather", "effects"), "Sky effects", "bool", None,
             "Clouds, rain, snow, stars, lightning"),
            (("weather", "show_line"), "Show outdoor line", "bool", None, ""),
            (("weather", "day_brightness"), "Daytime brightness", "float", None,
             "0 to 1. How light noon gets. Turn it down if the panel throws "
             "too much light into the case"),
            (("weather", "mood_tint"), "Mood over sky", "float", None,
             "0 = pure weather, 1 = pure mood. Stress adds to this"),
            (("weather", "day_starts"), "Day starts", "text", None,
             "HH:MM, used only when sky is clock"),
            (("weather", "day_ends"), "Day ends", "text", None, "HH:MM"),
        ]),
        ("When a reading counts as a problem", [
            (("thresholds", "cpu_temp"), "CPU temp", "pair", None,
             "warn / critical, deg C"),
            (("thresholds", "gpu_temp"), "GPU temp", "pair", None, "deg C"),
            (("thresholds", "cpu_load"), "CPU load", "pair", None, "percent"),
            (("thresholds", "ram"), "RAM", "pair", None, "percent"),
            (("thresholds", "vram"), "V-RAM", "pair", None, "percent"),
            (("thresholds", "power"), "System power", "pair", None,
             "percent of the gauge scale"),
        ]),
        ("Gauge full scale", [
            (("fans", "cpu_max_rpm"), "CPU fan", "float", None,
             "RPM at which the bar reads full"),
            (("fans", "gpu_max_rpm"), "GPU fan", "float", None, "RPM"),
            (("power", "gauge_max_w"), "System power", "float", None, "Watts"),
        ]),
        ("System power estimate", [
            (("power", "baseline_w"), "Baseline draw", "float", None,
             "Board, RAM, drives, fans. Tune against a wall meter"),
            (("power", "psu_efficiency"), "PSU efficiency", "float", None,
             "0.90 for 80+ Gold at mid load"),
            (("power", "cpu_idle_w"), "Modeled CPU idle", "float", None,
             "Used only if LHM cannot report package watts"),
            (("power", "cpu_max_w"), "Modeled CPU max", "float", None,
             "PPT ceiling"),
        ]),
    ],
    "Screen": [
        ("Which display", [
            (("display", "monitor"), "Monitor", "monitor", None,
             "Which screen the dashboard occupies"),
            (("display", "aspect_fix"), "Aspect correction", "choice",
             ["none", "squash43"],
             "Only for non-16:9 panels that stretch the signal"),
        ]),
        ("Window behaviour", [
            (("display", "hide_cursor"), "Hide mouse cursor", "bool", None, ""),
            (("display", "topmost"), "Keep above other windows", "bool", None, ""),
            (("display", "follow_displays"), "Follow display changes", "bool", None,
             "Re-place when a monitor is switched on or off"),
            (("display", "follow_poll_seconds"), "Follow check interval", "float",
             None, "Seconds between layout checks"),
        ]),
        ("Shortcut", [
            (("hotkey", "enabled"), "System-wide hotkey", "bool", None,
             "Opens this window from anywhere"),
            (("hotkey", "combo"), "Key combination", "text", None,
             "e.g. ctrl+alt+f9  (a modifier is required)"),
        ]),
        ("Switch presets by themselves", [
            (("autoswitch", "fullscreen_preset"), "While something is fullscreen",
             "choice", ["", *presets.NAMES],
             "A game, a benchmark, a film. Blank never switches. The screen "
             "you had comes back when it closes; config.json is never touched"),
            (("autoswitch", "idle_preset"), "When nobody is around", "choice",
             ["", *presets.NAMES],
             "After the minutes below with no keyboard or mouse. Blank never "
             "switches"),
            (("autoswitch", "idle_minutes"), "Idle after", "int", None,
             "Minutes of quiet"),
            (("autoswitch", "idle_night_only"), "Only after dark", "bool", None,
             "Uses the day window from the Data tab, so a machine idling at "
             "noon keeps its screen"),
        ]),
    ],
}

CHARACTER_FIELDS = [
    ("The face", [
        (("layout", "buddy", "character"), "Character", "choice",
         list(rigs.CHARACTERS),
         "Three faces (drawn morphs between moods; emoji and image use the "
         "rows on the right) and eight animated characters: the doom face "
         "takes damage, the robot vents, the cat lives on the cards, the "
         "web-slinger swings, the starship strafes, the dragon breathes "
         "fire, the car races the layout, the pet grows with uptime"),
        (("layout", "buddy", "scene"), "Backdrop", "choice",
         list(scenes.NAMES),
         "An animated scene behind the character, driven by live readings. "
         "Stars, the grid and the code speed up with load; the aquarium "
         "swims at fan speed and cooks with heat; the skyline lights up as "
         "the CPU and GPU work; the lair's lava glows with temperature; the "
         "raceway streams at load speed"),
        (("layout", "buddy", "seasonal"), "Festival effects", "bool", None,
         "The Indian calendar: kites on Sankranti, gulal bursts on Holi, a "
         "garba ring through Navratri, diyas and fireworks over Diwali, "
         "tricolour on Republic and Independence Day, December snow, New "
         "Year fireworks"),
        (("layout", "buddy", "tint"), "Drawn face colour", "choice",
         ["classic", "theme"],
         "classic is emoji yellow reddening as it heats; theme uses your accent"),
        (("layout", "buddy", "theme_blend"), "Follow theme colour", "float", None,
         "0 to 1. How far the face and sky lean toward your accent while "
         "idle. Bars, tabs and the mood word wear the accent outright"),
        (("layout", "buddy", "fps"), "Animation rate", "int", None,
         "4 to 30. At 30 the character screen costs about a sixth of one "
         "core; drop it to 15 for about 8%"),
    ]),
    ("When each mood applies", [
        (("layout", "buddy", "bands", "chill"), "Chilling up to", "float", None,
         "Stress runs 0 to 1: heat and load folded together, with 1.0 pinned "
         "to this machine's real maximum by the calibration in config.json. "
         "Each mood owns the range up to its number"),
        (("layout", "buddy", "bands", "busy"), "Working up to", "float", None,
         "Must be above the chilling edge; nonsense values are straightened "
         "out rather than obeyed"),
        (("layout", "buddy", "bands", "sweaty"), "Sweating up to", "float", None,
         "Melting is everything above this"),
        (("layout", "buddy", "stress", "nap_after_seconds"), "Napping after",
         "float", None,
         "Seconds of near-idle quiet before the character sleeps. 0 never "
         "naps. No signal is its own state, from the sensors"),
    ]),
    ("The caption", [
        (("layout", "buddy", "show_caption"), "Show the mood word", "bool", None,
         "WORKING, MELTING and so on, under the character. The pet's DAY "
         "line rides this too"),
        (("layout", "buddy", "show_quips"), "Show the one-liner", "bool", None,
         "The changing line under the mood word. Off by default"),
    ]),
]

COLOR_ROWS = [
    ("accent", "Accent", "Gauges, bars and normal-state numbers"),
    ("bg", "Background", ""),
    ("text", "Text", "Brightest text"),
    ("dim", "Text (dim)", "Labels and secondary values"),
    ("faint", "Text (faint)", "Header line"),
    ("track", "Gauge track", "Unfilled part of rings and bars"),
    ("edge", "Rules", "Divider lines"),
    ("na", "Unavailable", "Shown when a sensor has no value"),
    ("warn", "Warning", "At the warn threshold"),
    ("crit", "Critical", "At the critical threshold"),
]

TAB_BLURB = {
    "Presets": "Pick a whole screen: the built-ins, plus any preset you "
               "save from the Layout tab. Applying one writes the same "
               "layout and palette the next two tabs edit by hand, and your "
               "own colour picks survive the switch.",
    "Layout": "Click any tile in the preview to change what it shows. Pick "
              "a preset from the dropdown, or save the current screen as "
              "your own.",
    "Character": "The buddy screen: which character is up, its backdrop, "
                 "and what each mood is called and looks like.",
    "Look": "Colours and size. Bars, card tabs and the mood word wear the "
            "accent you pick; the sky and the face follow the mood and the "
            "weather, leaning toward your accent while the machine idles.",
    "Data": "Where readings come from, and the point at which one counts as a "
            "problem. Thresholds here drive both screens.",
    "Screen": "Which monitor the dashboard lives on, at what resolution, and "
              "how the window behaves.",
}

REVERT_SECONDS = 15


def _get(cfg: dict, path: tuple) -> Any:
    node: Any = cfg
    for key in path:
        node = node[key]
    return node


def _set(cfg: dict, path: tuple, value: Any) -> None:
    node = cfg
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


class SettingsWindow:
    def __init__(self, parent: tk.Misc, cfg: dict, monitors: list,
                 on_apply: Callable[[dict], None], collector=None,
                 on_live: Callable[[dict], None] | None = None) -> None:
        self.cfg = copy.deepcopy(cfg)
        self.monitors = monitors
        self.on_apply = on_apply
        self.collector = collector
        self.on_live = on_live
        self._live_job = None
        self.layout_editor = None
        self.vars: dict[tuple, Any] = {}
        self.color_vars: dict[str, tk.StringVar] = {}
        self.swatches: dict[str, tk.Frame] = {}
        self.mood_vars: dict[str, dict] = {}
        self._previews: dict[str, Any] = {}     # PhotoImage refs, or Tk drops them
        self.emoji = emoji.shared()

        self.win = tk.Toplevel(parent)
        self.win.title("CaseBuddy settings")
        self.win.resizable(False, False)
        # The dashboard sets -topmost; without this the dialog can open behind
        # it when both land on the same screen.
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        self._body: ttk.Frame | None = None
        self._status: ttk.Label | None = None
        self._build()
        self._place_on_primary()
        # The dashboard is overrideredirect and deliberately has no taskbar
        # button; a Toplevel it owns inherits that. This window is an ordinary
        # one and should be findable, so it asks for a button of its own.
        appicon.apply_to(self.win, taskbar=True)
        self.win.attributes("-topmost", True)
        self.win.focus_force()

    def set_collector(self, collector) -> None:
        """Adopt the collector that apply_config just built.

        apply_config REPLACES the collector rather than mutating it -- poll
        rates and the endpoint are fixed at construction -- so the reference
        taken when this window opened points at a stopped one the moment you
        press Save & Apply. The preview then froze on that collector's last
        snapshot, which looked exactly like an edit failing to apply.
        """
        self.collector = collector
        if self.layout_editor is not None:
            self.layout_editor.collector = collector
            self.layout_editor._refresh_catalog()

    # --- helpers ----------------------------------------------------------

    def _monitor_choices(self) -> list[str]:
        out = ["auto", "primary"]
        for index, mon in enumerate(self.monitors, 1):
            tag = " (primary)" if mon.primary else ""
            out.append(f"{index}. {mon.width}x{mon.height} at {mon.x},{mon.y}{tag}")
        return out

    @staticmethod
    def _monitor_to_value(choice: str) -> str:
        if choice in ("auto", "primary"):
            return choice
        return choice.split(".", 1)[0].strip()

    def _monitor_from_value(self, value: Any) -> str:
        text = str(value)
        if text in ("auto", "primary"):
            return text
        for choice in self._monitor_choices()[2:]:
            if choice.split(".", 1)[0].strip() == text:
                return choice
        return "auto"

    def _target_device(self) -> str | None:
        """The device name of the monitor the dashboard is set to use."""
        selector = str(self.cfg["display"]["monitor"])
        if selector == "primary":
            mon = next((m for m in self.monitors if m.primary), None)
        elif selector == "auto":
            secondary = [m for m in self.monitors if not m.primary]
            mon = min(secondary, key=lambda m: m.width * m.height) if secondary else None
        elif selector.isdigit() and 1 <= int(selector) <= len(self.monitors):
            mon = self.monitors[int(selector) - 1]
        else:
            mon = None
        return mon.device if mon else None

    # --- layout -----------------------------------------------------------

    def _build(self) -> None:
        body = ttk.Frame(self.win)
        body.grid(row=0, column=0, sticky="nsew")
        self._body = body

        nb = ttk.Notebook(body)
        nb.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")

        self._tab_presets(nb)
        self._tab_layout(nb)
        self._tab_character(nb)
        self._tab_look(nb)
        self._tab_schema(nb, "Data", columns=2)
        self._tab_screen(nb)
        self._tab_about(nb)

        buttons = ttk.Frame(body, padding=(10, 0, 10, 10))
        buttons.grid(row=1, column=0, sticky="ew")
        ttk.Button(buttons, text="Reset to defaults", command=self.reset).pack(side="left")
        # "Close", not "Cancel": Save & Apply has already taken effect by the
        # time this button is useful, so there is nothing left to cancel.
        ttk.Button(buttons, text="Close", command=self.close).pack(side="right")
        ttk.Button(buttons, text="Save & Apply", command=self.save).pack(
            side="right", padx=(0, 8))
        self._status = ttk.Label(buttons, text="", foreground="#207020")
        self._status.pack(side="left", padx=(14, 0))

    def _page(self, nb: ttk.Notebook, name: str) -> ttk.Frame:
        """A tab with its one-line explanation already at the top."""
        frame = ttk.Frame(nb, padding=12)
        nb.add(frame, text=name)
        blurb = TAB_BLURB.get(name)
        if blurb:
            ttk.Label(frame, text=blurb, foreground=HINT, wraplength=880,
                      justify="left").grid(row=0, column=0, columnspan=4,
                                           sticky="w", pady=(0, 10))
        return frame

    def _tab_schema(self, nb: ttk.Notebook, name: str, columns: int = 1) -> None:
        frame = self._page(nb, name)
        self._add_groups(frame, SCHEMA[name], columns=columns, row=1)

    def _add_groups(self, parent: tk.Misc, groups: list, columns: int = 1,
                    row: int = 0) -> None:
        """Lay titled groups out in `columns`, shortest-first down each one."""
        holders = []
        for index in range(columns):
            holder = ttk.Frame(parent)
            holder.grid(row=row, column=index, sticky="nw", padx=(0, 18))
            holders.append(holder)
        for index, (title, fields) in enumerate(groups):
            box = ttk.LabelFrame(holders[index % columns], text=title, padding=10)
            box.pack(anchor="nw", fill="x", pady=(0, 10))
            self._add_fields(box, fields)

    def _add_fields(self, frame: tk.Misc, fields: list[tuple]) -> None:
        for row, (path, label, kind, options, hint) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", **PAD)
            self._add_widget(frame, row, path, kind, options)
            if hint:
                ttk.Label(frame, text=hint, foreground=HINT,
                          wraplength=HINT_WRAP, justify="left").grid(
                    row=row, column=3, sticky="w", padx=(12, 0))

    def _add_widget(self, frame: tk.Misc, row: int, path: tuple,
                    kind: str, options) -> None:
        value = _get(self.cfg, path)

        if kind == "bool":
            var: Any = tk.BooleanVar(value=bool(value))
            ttk.Checkbutton(frame, variable=var).grid(row=row, column=1, sticky="w")
        elif kind == "scale":
            lo, hi = options
            var = tk.DoubleVar(value=float(value))
            holder = ttk.Frame(frame)
            holder.grid(row=row, column=1, columnspan=2, sticky="w")
            readout = ttk.Label(holder, width=5, text=f"{float(value):.2f}")
            scale = ttk.Scale(holder, from_=lo, to=hi, variable=var, length=170,
                              command=lambda v, r=readout: r.configure(
                                  text=f"{float(v):.2f}"))
            scale.pack(side="left")
            readout.pack(side="left", padx=(8, 0))
            # ttk.Scale snaps to whole pixels, so simply opening this window
            # nudged 1.00 to 0.95 and any later Save persisted the drift.
            # Restore the configured value once the widget has settled.
            original = float(value)
            holder.after_idle(lambda v=original, r=readout: (
                var.set(v), r.configure(text=f"{v:.2f}")))
        elif kind == "family":
            # Populated from Tk rather than SCHEMA: what is installed is a
            # property of the machine, not of the app.
            import tkinter.font as tkfont

            values = [""] + sorted({str(name) for name in tkfont.families(self.win)})
            var = tk.StringVar(value=str(value))
            ttk.Combobox(frame, textvariable=var, values=values, state="readonly",
                         width=30).grid(row=row, column=1, columnspan=2, sticky="w")
        elif kind in ("choice", "monitor"):
            if kind == "monitor":
                values = self._monitor_choices()
                var = tk.StringVar(value=self._monitor_from_value(value))
                width = 28
            else:
                values = list(options or [])
                var = tk.StringVar(value=str(value))
                width = 16
            ttk.Combobox(frame, textvariable=var, values=values, state="readonly",
                         width=width).grid(row=row, column=1, columnspan=2,
                                           sticky="w")
        elif kind == "pair":
            warn = tk.StringVar(value=str(value[0]))
            crit = tk.StringVar(value=str(value[1]))
            ttk.Entry(frame, textvariable=warn, width=7).grid(row=row, column=1,
                                                              sticky="w")
            ttk.Entry(frame, textvariable=crit, width=7).grid(
                row=row, column=2, sticky="w", padx=(6, 0))
            var = (warn, crit)
        else:  # float | int | text
            var = tk.StringVar(value=str(value))
            width = 40 if path[-1] in ("http_url", "line_format") else 22
            ttk.Entry(frame, textvariable=var, width=width).grid(
                row=row, column=1, columnspan=2, sticky="w")

        self.vars[path] = (kind, var)

    # --- presets ----------------------------------------------------------

    def _tab_presets(self, nb: ttk.Notebook) -> None:
        frame = self._page(nb, "Presets")

        tree = ttk.Treeview(frame, columns=("kind",), show="tree headings",
                            height=8, selectmode="browse")
        tree.heading("#0", text="Preset")
        tree.heading("kind", text="Screen")
        tree.column("#0", width=210, stretch=False)
        tree.column("kind", width=100, stretch=False)
        tree.grid(row=1, column=0, sticky="nw")
        self._preset_tree = tree
        self._fill_preset_tree()

        side = ttk.Frame(frame)
        side.grid(row=1, column=1, sticky="nw", padx=(16, 0))
        self._preset_blurb = ttk.Label(side, wraplength=460, justify="left", text="")
        self._preset_blurb.pack(anchor="w")

        # The preview is the real renderer on a small canvas, wearing the
        # preset's own palette, fed sample readings that sweep the whole
        # stress range -- so a character preset demonstrates its behaviours
        # before you commit to it.
        self._preview_canvas = tk.Canvas(
            side, width=PREVIEW_W, height=PREVIEW_H, highlightthickness=1,
            highlightbackground="#3a3a3a", bd=0)
        self._preview_canvas.pack(anchor="w", pady=(12, 0))
        self._preview_scene = None
        self._preview_t0 = time.monotonic()
        self._preview_job = None

        ttk.Button(side, text="Apply preset", command=self._apply_preset).pack(
            anchor="w", pady=(14, 0))
        ttk.Label(side, foreground=HINT, wraplength=460, justify="left",
                  text="Nothing is written to disk until Save & Apply.").pack(
            anchor="w", pady=(8, 0))

        packs = ttk.LabelFrame(side, text="Theme packs", padding=10)
        packs.pack(anchor="w", pady=(18, 0), fill="x")
        ttk.Label(packs, foreground=HINT, wraplength=430, justify="left",
                  text="One file bundling the whole look: layout, palette, "
                       "skies, character, scene, even your face pictures. "
                       "Share it or keep it as a backup. Your location and "
                       "API key are never included.").pack(anchor="w")
        row = ttk.Frame(packs)
        row.pack(anchor="w", pady=(8, 0))
        ttk.Button(row, text="Export...", width=10,
                   command=self._export_pack).pack(side="left")
        ttk.Button(row, text="Import...", width=10,
                   command=self._import_pack).pack(side="left", padx=(8, 0))

        tree.bind("<<TreeviewSelect>>", lambda _e: self._preset_selected())
        tree.bind("<Double-1>", lambda _e: self._apply_preset())

        current = presets.matches(self.cfg)
        self._mark_current(current)
        tree.selection_set(current if current in presets.registry(self.cfg)
                           else presets.NAMES[0])
        self._preset_selected()

    def _fill_preset_tree(self) -> None:
        tree = self._preset_tree
        for item in tree.get_children():
            tree.delete(item)
        for name, entry in presets.registry(self.cfg).items():
            tree.insert("", "end", iid=name, text=entry["title"],
                        values=(entry["kind"],))

    def _mark_current(self, current: str | None) -> None:
        tree = getattr(self, "_preset_tree", None)
        if tree is None:
            return
        for name, entry in presets.registry(self.cfg).items():
            if not tree.exists(name):
                continue
            title = entry["title"]
            tree.item(name, text=f"{title}   (current)" if name == current else title)

    def _selected_preset(self) -> str | None:
        tree = getattr(self, "_preset_tree", None)
        chosen = tree.selection() if tree is not None else ()
        name = chosen[0] if chosen else None
        return name if name in presets.registry(self.cfg) else None

    def _preset_selected(self) -> None:
        name = self._selected_preset()
        self._preset_blurb.configure(
            text=presets.registry(self.cfg)[name]["blurb"] if name else "")
        self._preview_build(name)

    # --- the preview ------------------------------------------------------

    def _preview_build(self, name: str | None) -> None:
        if getattr(self, "_preview_job", None) is not None:
            self.win.after_cancel(self._preview_job)
            self._preview_job = None
        self._preview_scene = None
        canvas = getattr(self, "_preview_canvas", None)
        if canvas is None or name is None:
            return
        canvas.delete("all")
        cfg = presets.apply(self.cfg, name)
        # The pet rig persists its state next to the config; a PREVIEW pet
        # must not feed or age the real one, so it lives in the temp dir.
        import tempfile

        cfg["_config_dir"] = tempfile.gettempdir()
        try:
            with self._preview_palette(cfg["theme"]):
                from .dashboard import make_scene

                scene = make_scene(canvas, theme.Geometry(PREVIEW_W, PREVIEW_H),
                                   cfg)
                scene.build()
                # Assigned before the first frame so _preview_snap can read
                # the preset's layout for its sample readings.
                self._preview_scene = scene
                scene.update(self._preview_snap())
        except Exception as exc:      # a broken preview must not break the tab
            print(f"[casebuddy] preset preview error: {exc!r}")
            self._preview_scene = None
            return
        self._preview_job = self.win.after(66, self._preview_tick)

    def _preview_tick(self) -> None:
        self._preview_job = None
        if self._preview_scene is None:
            return
        try:
            with self._preview_palette(self._preview_scene.cfg["theme"]):
                self._preview_scene.update(self._preview_snap())
            self._preview_job = self.win.after(66, self._preview_tick)
        except tk.TclError:
            self._preview_scene = None      # window went away mid-frame
        except Exception as exc:
            print(f"[casebuddy] preset preview error: {exc!r}")
            self._preview_scene = None

    @staticmethod
    def _preview_palette(theme_cfg: dict):
        """The preset's palette, worn only inside this block.

        The renderers read theme.* module globals at paint time, and the live
        panel repaints from the same globals on the same Tk thread -- so the
        preview swaps them in, draws one frame, and puts every one of them
        back before anything else can run.
        """
        import contextlib

        attrs = tuple(theme._COLOR_KEYS.values()) + (
            "NUMERAL_SCALE", "LABEL_SCALE", "FAN_CPU", "FAN_GPU", "STATE_COLOR")

        @contextlib.contextmanager
        def swap():
            saved = {name: getattr(theme, name) for name in attrs}
            theme.apply(theme_cfg)
            try:
                yield
            finally:
                for name, value in saved.items():
                    setattr(theme, name, value)

        return swap()

    def _preview_snap(self):
        """Sample readings sweeping the whole stress range over ~45 seconds,
        so a character preview walks through its own behaviours -- the hero
        hangs, then crawls, then swings, then fights, on a loop."""
        import math

        from .metrics import Reading, Snapshot

        base = 0.5 + 0.45 * math.sin(
            (time.monotonic() - self._preview_t0) * math.tau / 45.0)

        def sample(ref: str) -> tuple[float | None, str]:
            short = ref.split(":", 1)[-1]
            got = catalog.CALC.get(short)
            if got is None:
                return 42.0, ""
            _label, unit, numeric, lo, hi = got
            if short == "blank" or not numeric:
                return None, ""
            span = 0.15 + 0.75 * base
            return lo + (hi - lo) * span, unit

        def reading(slot: dict) -> Reading:
            value, unit = sample(str(slot.get("metric", "")))
            if value is None:
                return Reading()
            dvalue, dunit = sample(str(slot.get("detail", "")))
            detail = f"{dvalue:.0f} {dunit}".strip() if dunit else ""
            return Reading(value=round(value), unit=unit, fraction=base,
                           detail=detail, state="ok")

        readings = {}
        layout = self._preview_scene.cfg["layout"] if self._preview_scene \
            else {}
        for kind, prefix in (("rings", "ring"), ("bars", "bar"),
                             ("fans", "fan"), ("stats", "stat")):
            for index, slot in enumerate(layout.get(kind, []) or []):
                readings[f"{prefix}{index}"] = reading(slot)
        readings["hdr0"] = Reading(detail="RYZEN 7 5700X · RTX 3070")
        readings["hdr1"] = Reading(detail=time.strftime("%a %d %b   %#I:%M %p"))
        return Snapshot(
            ts=time.time(), readings=readings,
            vitals={"cpu_load": base * 100.0, "gpu_load": base * 85.0,
                    "cpu_temp": 32.0 + base * 60.0,
                    "gpu_temp": 30.0 + base * 56.0,
                    "cpu_fan_rpm": 800.0 + base * 1600.0,
                    "cpu_fan_pct": base * 100.0,
                    "gpu_fan_rpm": 900.0 + base * 2400.0,
                    "gpu_fan_pct": base * 100.0,
                    "ram_pct": 55.0, "vram_pct": 48.0},
            weather=None)

    def _apply_preset(self) -> None:
        name = self._selected_preset()
        if name is None:
            return
        self.apply_preset_key(name)

    def apply_preset_key(self, name: str) -> None:
        """Apply any preset by key: the gallery button, and the Layout tab's
        picker, both land here."""
        applied = presets.apply(self.cfg, name)
        self.cfg["layout"] = applied["layout"]
        self.cfg["theme"] = applied["theme"]

        # Every widget bound to something the preset rewrote has to be resynced.
        # Otherwise the next _collect writes the stale widget value back over
        # the preset and quietly undoes half of what was just applied.
        self._preset_var.set(self.cfg["theme"]["preset"])
        self._load_theme_fields()
        for path, (kind, var) in self.vars.items():
            if path[0] == "layout":
                self._reload_var(path, kind, var)
        self._reload_moods()
        if self.layout_editor is not None:
            self.layout_editor.reload()

        self._mark_current(name)
        if self.layout_editor is not None:
            self.layout_editor.refresh_presets()
        self._say(f"{presets.registry(self.cfg)[name]['title']} applied - "
                  f"Save & Apply to keep")
        self._live()

    # --- presets saved from the Layout tab --------------------------------

    def save_layout_preset(self) -> None:
        """Bottle the CURRENT layout and palette as a named preset."""
        name = simpledialog.askstring(
            "CaseBuddy", "Name for this preset:", parent=self.win)
        if not name or not name.strip():
            return
        name = name.strip()
        collected = self._collect()
        if collected is None:
            return
        key = "user-" + "".join(ch if ch.isalnum() else "-"
                                for ch in name.lower()).strip("-")
        book = self.cfg.setdefault("user_presets", {})
        if key in presets.PRESETS:
            key = key + "-2"
        book[key] = {"title": name,
                     "theme": copy.deepcopy(collected["theme"]),
                     "layout": copy.deepcopy(collected["layout"])}
        self._fill_preset_tree()
        self._mark_current(presets.matches(self.cfg))
        if self.layout_editor is not None:
            self.layout_editor.refresh_presets()
        self._say(f"Preset '{name}' saved - Save & Apply to keep it on disk")

    def update_layout_preset(self, key: str | None) -> None:
        """Overwrite one of the user's own presets with the current state."""
        book = self.cfg.get("user_presets") or {}
        if not key or key not in book:
            messagebox.showinfo(
                "CaseBuddy",
                "Built-in presets cannot be overwritten.\n"
                "Use 'Save as preset' to make this layout your own.",
                parent=self.win)
            return
        collected = self._collect()
        if collected is None:
            return
        book[key]["theme"] = copy.deepcopy(collected["theme"])
        book[key]["layout"] = copy.deepcopy(collected["layout"])
        if self.layout_editor is not None:
            self.layout_editor.refresh_presets()
        self._mark_current(presets.matches(self.cfg))
        self._say(f"Preset '{book[key]['title']}' updated - Save & Apply "
                  f"to keep it on disk")

    # --- theme packs --------------------------------------------------------

    def _export_pack(self) -> None:
        """The CURRENT state of this window, not the last save: exporting what
        you are looking at is the only behaviour that is not a surprise."""
        cfg = self._collect()
        if cfg is None:
            return
        cfg["_config_dir"] = self.cfg.get("_config_dir", "")
        path = filedialog.asksaveasfilename(
            parent=self.win, title="Export theme pack",
            defaultextension=".cbtheme.json",
            initialfile="my-look.cbtheme.json",
            filetypes=[("CaseBuddy theme pack", "*.cbtheme.json"),
                       ("JSON", "*.json")])
        if not path:
            return
        try:
            themepack.export_pack(cfg, path)
        except OSError as exc:
            messagebox.showerror("CaseBuddy", f"Could not write the pack:\n{exc}",
                                 parent=self.win)
            return
        self._say(f"Exported {os.path.basename(path)}")

    def _import_pack(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.win, title="Import theme pack",
            filetypes=[("CaseBuddy theme pack", "*.cbtheme.json *.json"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            got = themepack.import_pack(self.cfg, path)
        except ValueError as exc:
            messagebox.showerror("CaseBuddy", str(exc), parent=self.win)
            return
        self.cfg["layout"] = got["layout"]
        self.cfg["theme"] = got["theme"]
        self.cfg["weather"] = got["weather"]
        # The same resync dance as applying a preset: every widget bound to
        # something the pack rewrote must be reloaded, or the next _collect
        # writes stale widget values back over it.
        self._preset_var.set(self.cfg["theme"]["preset"])
        self._load_theme_fields()
        for vpath, (kind, var) in self.vars.items():
            if vpath[0] in ("layout", "weather"):
                self._reload_var(vpath, kind, var)
        self._reload_moods()
        self._sky_load()
        if self.layout_editor is not None:
            self.layout_editor.reload()
        self._mark_current(presets.matches(self.cfg))
        self._say(f"Imported {os.path.basename(path)} - Save & Apply to keep")
        self._live()

    def _reload_var(self, path: tuple, kind: str, var) -> None:
        """Push a config value back into the widget that edits it."""
        try:
            value = _get(self.cfg, path)
        except (KeyError, TypeError):
            return
        if kind == "bool":
            var.set(bool(value))
        elif kind == "scale":
            var.set(float(value))
        elif kind == "pair":
            var[0].set(str(value[0]))
            var[1].set(str(value[1]))
        else:
            var.set(str(value))

    # --- layout -----------------------------------------------------------

    def _tab_layout(self, nb: ttk.Notebook) -> None:
        from .layout_editor import LayoutEditor

        def changed() -> None:
            # Editing a tile usually stops the layout being any known preset.
            self._mark_current(presets.matches(self.cfg))
            self._say("layout changed - Save & Apply to keep")
            self._live()

        editor = LayoutEditor(
            nb, self.cfg, self.collector, on_change=changed,
            presets_api={
                "entries": lambda: [(k, e["title"], e["kind"] == "Custom")
                                    for k, e in presets.registry(self.cfg).items()],
                "matches": lambda: presets.matches(self.cfg),
                "apply": self.apply_preset_key,
                "save": self.save_layout_preset,
                "update": self.update_layout_preset,
            })
        nb.add(editor, text="Layout")
        self.layout_editor = editor

    # --- character --------------------------------------------------------

    def _tab_character(self, nb: ttk.Notebook) -> None:
        frame = self._page(nb, "Character")
        self._add_groups(frame, CHARACTER_FIELDS, columns=1, row=1)

        box = ttk.LabelFrame(frame, text="Moods", padding=10)
        box.grid(row=1, column=1, sticky="nw", padx=(18, 0))
        ttk.Label(box, foreground=HINT, wraplength=640, justify="left",
                  text="One row per mood, coolest first. The name is what the "
                       "panel prints under the face -- change it to anything. "
                       "Separate one-liners with a vertical bar.").grid(
            row=0, column=0, columnspan=6, sticky="w", pady=(0, 8))
        headings = ("Band", "Name", "Emoji", "Picture", "", "One-liners")
        for column, title in enumerate(headings):
            ttk.Label(box, text=title, foreground=HINT).grid(
                row=1, column=column, sticky="w", padx=(0, 8))

        # From buddy, not hand-listed: this grid once carried its own copy of
        # the mood order and silently broke when two moods merged.
        for index, key in enumerate(buddy.MOOD_ORDER):
            row = index + 2
            # The internal band name, so a row can still be matched against the
            # thresholds in the docs once its display name has been changed.
            ttk.Label(box, text=key, foreground=HINT).grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=2)

            # One field for the name, not a fixed label plus a separate
            # override: two columns that both looked like the mood's name was
            # the confusing part, and only one of them did anything.
            caption = tk.StringVar(value=self._mood_caption(key))
            ttk.Entry(box, textvariable=caption, width=16).grid(
                row=row, column=1, sticky="w", padx=(0, 10))

            face = tk.StringVar(value=self._mood_value("faces", key))
            picker = ttk.Button(box, width=4,
                                command=lambda k=key: self._pick_emoji(k))
            picker.grid(row=row, column=2, sticky="w", padx=(0, 10))

            image = tk.StringVar(value=self._mood_value("images", key))
            browse = ttk.Button(box, width=16,
                                command=lambda k=key: self._pick_image(k))
            browse.grid(row=row, column=3, sticky="w", padx=(0, 2))
            clear = ttk.Button(box, text="x", width=2,
                               command=lambda k=key: self._clear_image(k))
            clear.grid(row=row, column=4, sticky="w", padx=(0, 10))

            quips = tk.StringVar(value=self._mood_quips(key))
            ttk.Entry(box, textvariable=quips, width=44).grid(row=row, column=5,
                                                              sticky="w")

            self.mood_vars[key] = {"face": face, "caption": caption,
                                   "quips": quips, "preview": picker,
                                   "image": image, "browse": browse}
            face.trace_add("write", lambda *_a, k=key: (self._sync_face(k),
                                                        self._live()))
            image.trace_add("write", lambda *_a, k=key: (self._sync_face(k),
                                                         self._live()))
            caption.trace_add("write", lambda *_a: self._live())
            quips.trace_add("write", lambda *_a: self._live())
            self._sync_face(key)

        if not self.emoji.available:
            ttk.Label(box, foreground="#a05000", wraplength=560, justify="left",
                      text="No colour emoji font found, so the emoji character "
                           "is unavailable and the face will be drawn instead."
                      ).grid(row=len(buddy.MOOD_ORDER) + 2, column=0,
                             columnspan=5, sticky="w", pady=(8, 0))

    def _mood_value(self, group: str, key: str) -> str:
        stored = (self.cfg["layout"]["buddy"].get(group) or {}).get(key)
        if group == "faces":
            return str(stored or buddy.MOODS[key].emoji)
        return str(stored or "")

    def _config_dir(self) -> str:
        return str(self.cfg.get("_config_dir")
                   or os.path.dirname(config.config_path()))

    def _pick_image(self, key: str) -> None:
        """Choose a picture for one mood, and copy it in.

        Copied rather than referenced so the config is self-contained: a face
        should not vanish because the original was moved out of a downloads
        folder. Replacing it later just overwrites the same file.
        """
        chosen = filedialog.askopenfilename(
            parent=self.win,
            title=f"Face for {buddy.MOODS[key].caption.title()}",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                       ("All files", "*.*")])
        if not chosen:
            return
        if self.emoji.image_file(chosen, 64) is None:
            messagebox.showerror(
                "CaseBuddy", f"Could not read that as an image:\n{chosen}",
                parent=self.win)
            return
        folder = os.path.join(self._config_dir(), "faces")
        extension = os.path.splitext(chosen)[1].lower() or ".png"
        target = os.path.join(folder, f"{key}{extension}")
        try:
            os.makedirs(folder, exist_ok=True)
            if os.path.abspath(chosen) != os.path.abspath(target):
                shutil.copyfile(chosen, target)
        except OSError as exc:
            messagebox.showerror("CaseBuddy", f"Could not copy it in:\n{exc}",
                                 parent=self.win)
            return
        self.emoji.forget_file(target)
        self.mood_vars[key]["image"].set(f"faces/{key}{extension}")
        self._say(f"picture set for {buddy.MOODS[key].caption.title()}")

    def _clear_image(self, key: str) -> None:
        row = self.mood_vars.get(key)
        if row is not None:
            row["image"].set("")

    def _mood_caption(self, key: str) -> str:
        """What this mood is called now: the override, or the built-in."""
        stored = (self.cfg["layout"]["buddy"].get("captions") or {}).get(key)
        return str(stored or buddy.MOODS[key].caption)

    def _pick_emoji(self, key: str) -> None:
        row = self.mood_vars.get(key)
        if row is None:
            return
        EmojiPicker(self.win, self.emoji, row["face"].get().strip(),
                    lambda char, k=key: self.mood_vars[k]["face"].set(char))

    def _mood_quips(self, key: str) -> str:
        stored = self.cfg["layout"]["buddy"].get("quips")
        lines = stored.get(key) if isinstance(stored, dict) else None
        return " | ".join(str(line) for line in lines) if lines else ""

    def _reload_moods(self) -> None:
        for key, row in self.mood_vars.items():
            row["face"].set(self._mood_value("faces", key))
            row["image"].set(self._mood_value("images", key))
            row["caption"].set(self._mood_caption(key))
            row["quips"].set(self._mood_quips(key))

    def _sync_face(self, key: str) -> None:
        """Preview what this mood will actually draw.

        The picture wins when there is one, because that is what the panel
        does; the emoji is what it falls back to.
        """
        row = self.mood_vars.get(key)
        if row is None:
            return
        stored = row["image"].get().strip()
        row["browse"].configure(
            text=os.path.basename(stored) if stored else "Browse...")

        photo = None
        if stored:
            photo = self.emoji.image_photo(
                buddy.resolve_image(stored, self._config_dir()), 24, self.win)
        char = row["face"].get().strip()
        if photo is None and char:
            photo = self.emoji.photo(char, 24, self.win)
        if photo is None:
            self._previews.pop(key, None)
            row["preview"].configure(image="", text="?" if (char or stored) else "")
            return
        self._previews[key] = photo          # Tk keeps only a weak reference
        row["preview"].configure(image=photo, text="")

    # --- look -------------------------------------------------------------

    def _tab_look(self, nb: ttk.Notebook) -> None:
        frame = self._page(nb, "Look")
        self._add_groups(frame, SCHEMA["Look"], columns=1, row=1)

        box = ttk.LabelFrame(frame, text="Palette", padding=10)
        box.grid(row=1, column=1, sticky="nw", padx=(18, 0))

        ttk.Label(box, text="Preset").grid(row=0, column=0, sticky="w", **PAD)
        self._preset_var = tk.StringVar(
            value=str(self.cfg["theme"].get("preset", "violet")))
        combo = ttk.Combobox(box, textvariable=self._preset_var,
                             values=list(theme.PRESET_NAMES), state="readonly",
                             width=16)
        combo.grid(row=0, column=1, columnspan=3, sticky="w")
        combo.bind("<<ComboboxSelected>>", lambda _e: self._preset_changed())
        ttk.Label(box, text="Sets every colour below; edit any of them to override",
                  foreground=HINT).grid(row=0, column=4, sticky="w", padx=(14, 0))

        ttk.Separator(box, orient="horizontal").grid(
            row=1, column=0, columnspan=5, sticky="ew", pady=(10, 8))

        resolved = theme.resolve(self.cfg["theme"])
        for i, (key, label, hint) in enumerate(COLOR_ROWS):
            row = i + 2
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", **PAD)

            var = tk.StringVar(value=resolved[key])
            self.color_vars[key] = var

            swatch = tk.Frame(box, width=54, height=22, bg=resolved[key],
                              relief="solid", borderwidth=1)
            swatch.grid(row=row, column=1, sticky="w")
            swatch.grid_propagate(False)
            self.swatches[key] = swatch

            ttk.Entry(box, textvariable=var, width=11).grid(
                row=row, column=2, sticky="w", padx=(8, 0))
            var.trace_add("write",
                          lambda *_a, k=key: (self._sync_swatch(k), self._live()))
            ttk.Button(box, text="Pick", width=7,
                       command=lambda k=key: self._pick_colour(k)).grid(
                row=row, column=3, sticky="w", padx=(6, 0))
            if hint:
                ttk.Label(box, text=hint, foreground=HINT,
                          wraplength=HINT_WRAP, justify="left").grid(
                    row=row, column=4, sticky="w", padx=(12, 0))
        self._sky_editor(frame)

    # --- sky colours ------------------------------------------------------

    def _sky_editor(self, parent: tk.Misc) -> None:
        """Nine conditions by three phases, two colours each, in five widgets.

        Twenty-seven pairs is far too many to lay out at once and almost all of
        them are never touched, so this edits one at a time: pick which sky,
        then edit it. Only the ones actually changed reach config.json.
        """
        box = ttk.LabelFrame(parent, text="Sky colours", padding=10)
        box.grid(row=2, column=1, sticky="nw", padx=(18, 0), pady=(10, 0))
        self._sky_loading = False

        ttk.Label(box, foreground=HINT, wraplength=430, justify="left",
                  text="The character screen only. Day is noon, dim is the "
                       "overcast daytime floor, night is after dark; dawn and "
                       "dusk are blended from them.").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        ttk.Label(box, text="Sky").grid(row=1, column=0, sticky="w", **PAD)
        self._sky_cond = tk.StringVar(value="clear")
        self._sky_phase = tk.StringVar(value="day")
        conditions = ttk.Combobox(box, textvariable=self._sky_cond, width=12,
                                  state="readonly", values=list(weather.CONDITIONS))
        conditions.grid(row=1, column=1, sticky="w")
        phases = ttk.Combobox(box, textvariable=self._sky_phase, width=8,
                              state="readonly", values=["day", "dim", "night"])
        phases.grid(row=1, column=2, sticky="w", padx=(6, 0))
        ttk.Button(box, text="Reset", width=7, command=self._sky_reset).grid(
            row=1, column=3, sticky="w", padx=(8, 0))
        for combo in (conditions, phases):
            combo.bind("<<ComboboxSelected>>", lambda _e: self._sky_load())

        self._sky_vars, self._sky_swatches = {}, {}
        for index, (slot, title) in enumerate((("top", "Top"), ("bot", "Bottom"))):
            row = index + 2
            ttk.Label(box, text=title).grid(row=row, column=0, sticky="w", **PAD)
            swatch = tk.Frame(box, width=54, height=22, relief="solid",
                              borderwidth=1)
            swatch.grid(row=row, column=1, sticky="w")
            swatch.grid_propagate(False)
            var = tk.StringVar()
            ttk.Entry(box, textvariable=var, width=11).grid(
                row=row, column=2, sticky="w", padx=(6, 0))
            ttk.Button(box, text="Pick", width=7,
                       command=lambda k=slot: self._sky_pick(k)).grid(
                row=row, column=3, sticky="w", padx=(8, 0))
            self._sky_vars[slot] = var
            self._sky_swatches[slot] = swatch
            var.trace_add("write", lambda *_a: self._sky_changed())

        self._sky_load()

    def _sky_key(self) -> str:
        return buddy.sky_key(self._sky_cond.get(), self._sky_phase.get())

    def _sky_load(self) -> None:
        """Show the selected sky: the override if there is one, else built-in."""
        top, bot = buddy.resolve_sky(self._sky_cond.get(), self._sky_phase.get(),
                                     self.cfg["weather"].get("skies"))
        self._sky_loading = True
        try:
            self._sky_vars["top"].set(top)
            self._sky_vars["bot"].set(bot)
        finally:
            self._sky_loading = False
        self._sky_swatch()

    def _sky_swatch(self) -> None:
        for slot, swatch in self._sky_swatches.items():
            value = self._sky_vars[slot].get().strip()
            if theme._HEX.match(value):
                try:
                    swatch.configure(bg=value)
                except tk.TclError:
                    pass

    def _sky_changed(self) -> None:
        self._sky_swatch()
        if self._sky_loading:
            return
        top = self._sky_vars["top"].get().strip()
        bot = self._sky_vars["bot"].get().strip()
        if not (theme._HEX.match(top) and theme._HEX.match(bot)):
            return                        # mid-typing
        skies = self.cfg["weather"].setdefault("skies", {})
        built = buddy.built_in_sky(self._sky_cond.get(), self._sky_phase.get())
        # Only a genuine departure is stored, so a later change to the built-in
        # still reaches anyone who never edited that sky.
        if (top.lower(), bot.lower()) == (built[0].lower(), built[1].lower()):
            skies.pop(self._sky_key(), None)
        else:
            skies[self._sky_key()] = [top, bot]
        self._live()

    def _sky_pick(self, slot: str) -> None:
        initial = self._sky_vars[slot].get().strip() or "#000000"
        try:
            _rgb, chosen = colorchooser.askcolor(color=initial, parent=self.win,
                                                 title="CaseBuddy - sky")
        except tk.TclError:
            return
        if chosen:
            self._sky_vars[slot].set(chosen)

    def _sky_reset(self) -> None:
        self.cfg["weather"].setdefault("skies", {}).pop(self._sky_key(), None)
        self._sky_load()
        self._live()

    def _sync_swatch(self, key: str) -> None:
        value = self.color_vars[key].get().strip()
        if theme._HEX.match(value):
            try:
                self.swatches[key].configure(bg=value)
            except tk.TclError:
                pass

    def _preset_changed(self) -> None:
        """Load the preset's colours into the fields, discarding overrides.

        This is the Look tab's own palette picker: choosing a palette by name
        here is the deliberate way to shed per-key overrides. A SCREEN preset
        from the Presets tab goes through _load_theme_fields instead, which
        keeps them.
        """
        resolved = theme.resolve({"preset": self._preset_var.get()})
        for key, var in self.color_vars.items():
            var.set(resolved[key])

    def _load_theme_fields(self) -> None:
        """Show cfg's theme as it will actually render: the palette base with
        the user's surviving overrides folded in."""
        resolved = theme.resolve(self.cfg["theme"])
        for key, var in self.color_vars.items():
            var.set(resolved[key])

    def _pick_colour(self, key: str) -> None:
        initial = self.color_vars[key].get().strip() or "#000000"
        try:
            _rgb, chosen = colorchooser.askcolor(color=initial, parent=self.win,
                                                 title=f"CaseBuddy - {key}")
        except tk.TclError:
            return
        if chosen:
            self.color_vars[key].set(chosen)

    # --- screen -----------------------------------------------------------

    def _tab_screen(self, nb: ttk.Notebook) -> None:
        frame = self._page(nb, "Screen")
        self._add_groups(frame, SCHEMA["Screen"], columns=1, row=1)
        box = ttk.LabelFrame(frame, text="Resolution", padding=10)
        box.grid(row=1, column=1, sticky="nw", padx=(18, 0))
        self._add_resolution(box)

    def _add_resolution(self, frame: tk.Misc) -> None:
        """Resolution is an action, not a stored setting.

        Persisting a target resolution would mean the app fighting whatever the
        user or Windows does next. This applies the mode immediately and then
        makes you confirm it, so an unusable mode cannot strand you.
        """
        device = self._target_device()
        ttk.Label(frame, text="Mode").grid(row=0, column=0, sticky="w", **PAD)

        self._res_var = tk.StringVar()
        modes = display.list_modes(device) if device else []
        native = display.edid_native(device) if device else None
        current = display.current_mode(device) if device else None

        labels, self._res_map = [], {}
        for mode in modes:
            label = str(mode)
            if native and (mode.width, mode.height) == native:
                label += "   <- native"
            labels.append(label)
            self._res_map[label] = mode
            if current and mode == current:
                self._res_var.set(label)

        ttk.Combobox(frame, textvariable=self._res_var, values=labels,
                     state="readonly", width=30).grid(row=0, column=1, sticky="w")
        ttk.Button(frame, text="Apply", width=8,
                   command=self._apply_resolution).grid(row=0, column=2,
                                                        sticky="w", padx=(10, 0))

        # A panel advertises every mode its scaler accepts, not just the one
        # its glass has. Driving this one off-native caused desktop stutter, so
        # say so plainly rather than leaving it to be rediscovered.
        note, colour = "", HINT
        if native and current and (current.width, current.height) != native:
            note = (f"Running {current.width}x{current.height} but the panel is "
                    f"{native[0]}x{native[1]}. Off-native scaling can cause "
                    f"stutter across the whole desktop.")
            colour = "#a05000"
        elif native:
            note = f"Native {native[0]}x{native[1]} (from EDID) - correct."
            colour = "#207020"
        if note:
            ttk.Label(frame, text=note, foreground=colour, wraplength=420,
                      justify="left").grid(row=1, column=0, columnspan=3,
                                           sticky="w", pady=(8, 0))

    def _apply_resolution(self) -> None:
        device = self._target_device()
        mode = self._res_map.get(self._res_var.get())
        if not device or mode is None:
            return
        previous = display.current_mode(device)
        ok, message = display.set_mode(device, mode)
        if not ok:
            messagebox.showerror("CaseBuddy", f"Could not set {mode}:\n{message}",
                                 parent=self.win)
            return
        self._confirm_or_revert(device, previous, mode)

    def _confirm_or_revert(self, device: str, previous, applied) -> None:
        """Keep the new mode only if confirmed, else put the old one back.

        A mode the panel cannot display leaves a black screen and no way to
        undo it, which on a headless case panel means a reboot.
        """
        dialog = tk.Toplevel(self.win)
        dialog.title("Keep this resolution?")
        dialog.attributes("-topmost", True)
        dialog.resizable(False, False)
        dialog.transient(self.win)

        label = ttk.Label(dialog, padding=16, justify="left")
        label.pack()
        row = ttk.Frame(dialog, padding=(16, 0, 16, 16))
        row.pack(fill="x")

        state = {"left": REVERT_SECONDS, "job": None, "done": False}

        def finish(keep: bool) -> None:
            if state["done"]:
                return
            state["done"] = True
            if state["job"]:
                dialog.after_cancel(state["job"])
            if not keep and previous is not None:
                display.set_mode(device, previous, test_first=False)
            dialog.destroy()
            self._say(f"resolution {'kept' if keep else 'reverted'}")

        def tick() -> None:
            if state["done"]:
                return
            label.configure(
                text=f"Now running {applied}.\n\n"
                     f"Reverting to {previous} in {state['left']} s "
                     f"unless you keep it.")
            if state["left"] <= 0:
                finish(False)
                return
            state["left"] -= 1
            state["job"] = dialog.after(1000, tick)

        ttk.Button(row, text="Keep", command=lambda: finish(True)).pack(side="right")
        ttk.Button(row, text="Revert now",
                   command=lambda: finish(False)).pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        tick()

    # --- about ------------------------------------------------------------

    def _tab_about(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb, padding=12)
        nb.add(frame, text="About")

        text = tk.Text(frame, width=96, height=26, relief="flat", wrap="word",
                       background=self.win.cget("background"), borderwidth=0)
        text.pack(fill="both", expand=True)
        text.insert("end", self._diagnostics())
        text.configure(state="disabled")

        ttk.Button(frame, text="Refresh",
                   command=lambda: self._refresh_about(text)).pack(anchor="w",
                                                                   pady=(8, 0))

    def _refresh_about(self, widget: tk.Text) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", self._diagnostics())
        widget.configure(state="disabled")

    def _diagnostics(self) -> str:
        lines = ["CaseBuddy - case-mounted system telemetry", ""]
        lines.append(f"config file : {config.config_path()}")
        lines.append("")
        lines.append("Displays")
        for index, mon in enumerate(self.monitors, 1):
            tag = " (primary)" if mon.primary else ""
            lines.append(f"  {index}. {mon.device}  {mon.width}x{mon.height} "
                         f"at {mon.x},{mon.y}{tag}")
            native = display.edid_native(mon.device)
            current = display.current_mode(mon.device)
            if current:
                lines.append(f"      current {current}")
            if native:
                match = "native" if current and (current.width, current.height) == native \
                    else "NOT native - can cause stutter"
                lines.append(f"      EDID    {native[0]}x{native[1]}  ({match})")
        lines.append("")
        lines.append("Screen")
        layout = self.cfg.get("layout", {})
        lines.append(f"  mode: {layout.get('mode', 'gauges')}")
        if str(layout.get("mode")) == "buddy":
            options = layout.get("buddy", {})
            lines.append(f"  character: {options.get('character', 'drawn')}"
                         f"   emoji font: "
                         f"{self.emoji.path or 'not found'}")
        lines.append("")
        lines.append("Sky")
        lines.append(f"  source: {self.cfg['weather'].get('sky', 'weather')}")
        watcher = getattr(self.collector, "weather", None)
        if watcher is None:
            lines.append("  status: no collector attached")
        else:
            lines.append(f"  location: {watcher.located}")
            reading = watcher.latest()
            if reading is None:
                lines.append("  status: off")
            elif reading.ok:
                when = time.strftime("%H:%M", time.localtime(reading.fetched))
                lines.append(f"  reading: {reading.line or 'daylight only'}"
                             f"  (fetched {when})")
                if reading.sunrise and reading.sunset:
                    lines.append(
                        "  sunrise {}   sunset {}   daylight {:.0%}".format(
                            time.strftime("%H:%M", time.localtime(reading.sunrise)),
                            time.strftime("%H:%M", time.localtime(reading.sunset)),
                            reading.daylight))
            else:
                lines.append(f"  status: {reading.status or 'no reading yet'}")
        lines.append("")
        lines.append("Sensor backend")
        url = self.cfg["lhm"]["http_url"]
        lines.append(f"  LibreHardwareMonitor at {url}")
        try:
            import urllib.request

            with urllib.request.urlopen(url, timeout=2) as response:
                response.read(64)
            lines.append("  status: responding")
        except Exception as exc:
            lines.append(f"  status: NOT responding ({type(exc).__name__})")
            lines.append("  every CPU and GPU tile will read '--' until it is running")
        if self.collector is not None:
            lines.append(f"  sensors offered: {len(getattr(self.collector, 'rows', []))}")
        lines.append("")
        lines.append("Everything on screen comes from LibreHardwareMonitor, which must")
        lines.append("run as Administrator with Options -> Remote Web Server enabled.")
        lines.append("System power is an estimate: a desktop exposes no whole-system")
        lines.append("power sensor, so it is measured CPU + GPU watts plus a baseline,")
        lines.append("divided by PSU efficiency. Tune those two on the Data tab.")
        return "\n".join(lines)

    # --- placement --------------------------------------------------------

    def _place_on_primary(self) -> None:
        self.win.update_idletasks()
        width, height = self.win.winfo_width(), self.win.winfo_height()
        target = next((m for m in self.monitors if m.primary), None)
        if target is None:
            self.win.geometry("+60+60")
            return
        x = target.x + (target.width - width) // 2
        y = target.y + max(20, (target.height - height) // 4)
        self.win.geometry(f"+{max(target.x, x)}+{max(target.y, y)}")

    # --- actions ----------------------------------------------------------

    def _say(self, message: str) -> None:
        if self._status is not None:
            self._status.configure(text=message)
            self.win.after(5000, lambda: self._status.configure(text=""))

    def _live(self) -> None:
        """Push visual edits to the panel, debounced.

        Rebuilding the canvas on every keystroke would thrash it, so coalesce
        a burst of edits into one redraw.
        """
        if self.on_live is None:
            return
        if self._live_job is not None:
            self.win.after_cancel(self._live_job)
        self._live_job = self.win.after(350, self._do_live)

    def _do_live(self) -> None:
        self._live_job = None
        cfg = self._collect(quiet=True)
        if cfg is not None:
            try:
                self.on_live(cfg)
            except Exception as exc:
                print(f"[casebuddy] live preview failed: {exc!r}")

    def _collect(self, quiet: bool = False) -> dict | None:
        cfg = copy.deepcopy(self.cfg)
        for path, (kind, var) in self.vars.items():
            name = ".".join(path)
            try:
                if kind == "bool":
                    _set(cfg, path, bool(var.get()))
                elif kind == "monitor":
                    _set(cfg, path, self._monitor_to_value(var.get()))
                elif kind == "choice":
                    _set(cfg, path, var.get())
                elif kind == "scale":
                    _set(cfg, path, round(float(var.get()), 2))
                elif kind == "pair":
                    _set(cfg, path, [float(var[0].get()), float(var[1].get())])
                elif kind == "float":
                    _set(cfg, path, float(var.get()))
                elif kind == "int":
                    # Via float so "15.0" is accepted; stored as an int so a
                    # value equal to the default is not saved as an override.
                    _set(cfg, path, int(round(float(var.get()))))
                else:
                    _set(cfg, path, var.get())
            except (ValueError, TypeError):
                if quiet:
                    return None
                messagebox.showerror("CaseBuddy", f"{name} needs a number.",
                                     parent=self.win)
                return None

        self._collect_moods(cfg)

        cfg["theme"]["preset"] = self._preset_var.get()
        preset = theme.resolve({"preset": self._preset_var.get()})
        for key, var in self.color_vars.items():
            value = var.get().strip()
            if value and not theme._HEX.match(value):
                if quiet:
                    return None
                messagebox.showerror(
                    "CaseBuddy", f"'{value}' is not a colour.\nUse #rgb or #rrggbb.",
                    parent=self.win)
                return None
            # Store only genuine departures from the preset, so switching
            # preset later actually changes something.
            cfg["theme"][key] = "" if value.lower() == preset[key].lower() else value
        return cfg

    def _collect_moods(self, cfg: dict) -> None:
        """Fold the mood grid back into faces / captions / quips.

        Only departures are stored. A caption box left showing the built-in
        would otherwise be written out as an override, and the built-in could
        then never be improved for anyone who had opened this window once.
        """
        faces, captions, quips, images = {}, {}, {}, {}
        for key, row in self.mood_vars.items():
            face = row["face"].get().strip()
            if face and face != buddy.MOODS[key].emoji:
                faces[key] = face
            picture = row["image"].get().strip()
            if picture:
                images[key] = picture
            caption = row["caption"].get().strip()
            # The box is prefilled with the built-in, so only a real departure
            # is stored -- otherwise every mood would be written out as an
            # override the first time this window opened.
            if caption and caption != buddy.MOODS[key].caption:
                captions[key] = caption
            lines = [part.strip() for part in row["quips"].get().split("|")]
            lines = [part for part in lines if part]
            if lines and tuple(lines) != buddy.MOODS[key].quips:
                quips[key] = lines
        options = cfg.setdefault("layout", {}).setdefault("buddy", {})
        # Faces keep the full default map so a cleared box falls back to it.
        merged = dict(config.DEFAULTS["layout"]["buddy"]["faces"])
        merged.update(faces)
        options["faces"] = merged
        options["images"] = images
        options["captions"] = captions
        options["quips"] = quips

    def save(self) -> None:
        cfg = self._collect()
        if cfg is None:
            return
        try:
            config.save(cfg)
        except OSError as exc:
            messagebox.showerror("CaseBuddy", f"Could not write config:\n{exc}",
                                 parent=self.win)
            return
        # Updated IN PLACE, never rebound. The layout editor holds a reference
        # to this exact dict; rebinding it left the editor writing into an
        # orphan, so after one Save & Apply the inspector still showed your
        # edits and no tile on screen ever changed again.
        self.cfg.clear()
        self.cfg.update(copy.deepcopy(cfg))
        if self.layout_editor is not None:
            self.layout_editor.reload()
        self.on_apply(cfg)
        self._say(f"Saved and applied at {time.strftime('%H:%M:%S')}")

    def reset(self) -> None:
        if not messagebox.askyesno("CaseBuddy", "Reset every setting to its default?",
                                   parent=self.win):
            return
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.vars.clear()
        self.color_vars.clear()
        self.swatches.clear()
        self.mood_vars.clear()
        self._previews.clear()
        self.layout_editor = None
        if self._body is not None:
            self._body.destroy()
        self._build()

    def close(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass
