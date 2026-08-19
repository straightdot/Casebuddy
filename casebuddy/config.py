"""Defaults plus a shallow-merged config.json override.

Anything the user does not mention keeps its default, so config.json can stay
tiny -- three lines to change a threshold is friendlier than a 90-line file the
user has to keep in sync when the app gains a setting.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

DEFAULTS: dict[str, Any] = {
    "display": {
        # 1-based index into the monitor list, "auto" = the smallest non-primary
        # screen (which is almost always the case panel), or "primary".
        "monitor": "auto",
        # Explicit override, Tk geometry syntax: "1920x1080+2560+0".
        "geometry": None,
        "hide_cursor": True,
        "topmost": True,
        # The panel is 4:3 (800x600) but is fed a 16:9 signal. If its scaler
        # stretches rather than letterboxes, circles come out as eggs; set this
        # to "squash43" to pre-compress vertically so the stretch cancels out.
        # Run `casebuddy.py --calibrate` to find out which one your panel does.
        "aspect_fix": "none",  # "none" | "squash43"
        "background": "#07050d",
        # strftime pattern for the header. "%a %d %b" -> "Mon 17 Aug".
        "date_format": "%a %d %b",
        # strftime for the clock. "%#I:%M %p" -> "8:08 PM"; the # is the
        # Windows way to drop a leading zero, and "%H:%M" gives 24-hour.
        "clock_format": "%#I:%M %p",
        # Re-place the window when the monitor arrangement changes. Windows
        # removes a switched-off display from the desktop and renumbers the
        # rest, which otherwise strands the window at coordinates that no
        # longer exist. Also covers the boot race, where the panel may not have
        # handshaken by the time the autostart task fires.
        "follow_displays": True,
        "follow_poll_seconds": 3.0,
        # Multiplies text and stroke weight only, not the layout grid. Above
        # about 1.2 long strings start to collide.
        "text_scale": 1.0,
        # How far to fade the whole screen toward black after dark, 0 to 0.8.
        # Driven by the real daylight figure rather than by the clock, so a
        # panel bolted inside a case goes quiet when the room does. Needs the
        # sky source to be "weather" or "clock"; 0 switches it off.
        "night_dim": 0.0,
    },
    # Colours. `preset` picks a base palette; any individual key set to a
    # "#rrggbb" value overrides it, and an empty string inherits.
    # See theme.PRESETS for the built-ins.
    "theme": {
        "preset": "violet",
        # Blank picks the best installed face from the candidate lists in
        # theme.py. A family that is not installed is ignored, so a config
        # copied between machines degrades rather than breaking.
        "fonts": {"numeral": "", "label": "", "mono": ""},
        # Sizing on top of display.text_scale, 0.5 to 2.0. Numerals and labels
        # move independently: one slider for both cannot make the readings
        # bigger without also crowding them with their own titles.
        "font_scale": {"numeral": 1.0, "label": 1.0},
        "bg": "", "edge": "", "track": "",
        "text": "", "dim": "", "faint": "",
        "accent": "", "na": "", "warn": "", "crit": "",
    },
    "refresh": {
        # How often the screen repaints.
        "ui_hz": 2.0,
        # How often LibreHardwareMonitor is polled. One HTTP round trip costs
        # ~18 ms, so 2 Hz is comfortable.
        "fast_poll_hz": 2.0,
    },
    # LibreHardwareMonitor is the only source for CPU die temperature, package
    # watts and the effective core clock. It must be running, as Administrator,
    # with Options -> Remote Web Server enabled.
    "lhm": {
        "enabled": True,
        # "http" by default: LHM 0.9.6 no longer registers the
        # root\LibreHardwareMonitor WMI namespace, and probing a namespace that
        # does not exist blocks for ~4 s before failing. Use "auto" or "wmi"
        # only with older builds.
        "transport": "http",  # http | auto | wmi | off
        "http_url": "http://localhost:8085/data.json",
    },
    # Outdoor conditions for the buddy screen's sky, and for the
    # "calc:weather*" metrics any layout can put in a text slot.
    #
    # WHAT LEAVES THE MACHINE: with location "auto" this asks an IP-geolocation
    # service where you are once per launch, which reveals your public IP to
    # it, then asks the weather provider for that spot every refresh_minutes.
    # Put "lat,lon" in `location` and the geolocation call never happens; set
    # enabled to false and neither does.
    "weather": {
        #   weather  real conditions and real sunrise/sunset for your location
        #   clock    daylight only, from the two times below. No network at all.
        #   off      no sky data; the mood has the screen to itself
        "sky": "weather",
        # open-meteo needs no account and no key. openweather needs both.
        "provider": "open-meteo",   # open-meteo | openweather
        "api_key": "",
        # A place name ("Mumbai"), exact coordinates ("23.03,72.59"), or
        # "auto" to look it up from your IP. Coordinates are the only one that
        # makes no lookup request at all.
        "location": "auto",
        # Overrides whatever name the lookup returns. Blank keeps that name.
        "place": "",
        "refresh_minutes": 15,
        # The outdoor line, as a template. Fields: place, temp, feels, sky,
        # condition, humidity, wind, sunrise, sunset, daylight, and sep for the
        # separator. A field with no value takes its separator with it, so a
        # reading with no wind does not leave a gap. Blank uses place/temp/sky.
        "line_format": "",
        # Clouds, rain, snow, stars and lightning. Off leaves the sky as pure
        # colour, which is quieter to have glowing inside a case at night.
        "effects": True,
        # The place / temperature / conditions line under the header rule.
        "show_line": True,
        # How light noon is allowed to get, 0 to 1. Daytime is a genuinely
        # light sky at 1; 0 keeps the old all-dark palette while still moving
        # through dawn, dusk and night. Worth turning down if the panel throws
        # too much light into the case.
        "day_brightness": 1.0,
        # How much of the sky colour the mood owns while the machine is idle.
        # Stress adds to this, so a hot machine ends up red whatever the
        # forecast says. 0 is pure weather, 1 is pure mood.
        "mood_tint": 0.30,
        # Per-condition sky colours, as {"<condition>-<phase>": [top, bottom]}.
        # Condition is one of clear, partly, cloudy, overcast, fog, drizzle,
        # rain, snow, thunder; phase is day, dim or night. Anything absent uses
        # the built-in, so this file only ever holds what you actually changed.
        # Edit them on the Look tab.
        "skies": {},
        # Used only when sky = "clock".
        "day_starts": "07:00",
        "day_ends": "19:00",
    },
    # Presets saved from the Layout tab, as {key: {title, theme, layout}}.
    # They appear alongside the built-ins in the gallery and the Layout tab.
    "user_presets": {},
    # Swap presets by themselves when the room changes, without touching this
    # file: the stashed layout comes back the moment the condition clears.
    "autoswitch": {
        # Preset shown while any app runs fullscreen (a game, a benchmark,
        # a film). Blank never switches.
        "fullscreen_preset": "",
        # Preset shown once the keyboard and mouse have been quiet.
        "idle_preset": "",
        "idle_minutes": 15,
        # Only count idleness outside the weather day window, so a machine
        # left alone at noon keeps its screen.
        "idle_night_only": True,
        "poll_seconds": 5.0,
    },
    # A system-wide shortcut that opens settings. Windows hides new tray icons
    # in the overflow, and a chrome-free window has no taskbar button, so this
    # is the reachability guarantee that does not depend on either.
    "hotkey": {"enabled": True, "combo": "ctrl+alt+f9"},
    # Full-scale for the fan bars, in RPM.
    "fans": {"cpu_max_rpm": 2200.0, "gpu_max_rpm": 3400.0},
    "power": {
        # Everything that is neither the CPU package nor the GPU: chipset, RAM,
        # NVMe/SATA drives, fans, USB devices, VRM and board losses. ~38 W is a
        # fair figure for a B550 board with 2 DIMMs, a couple of drives and
        # 4-5 fans. Measure at the wall once and tune this if you care.
        "baseline_w": 38.0,
        # 80+ Gold sits near 90% through the middle of its load curve, and the
        # DC->AC conversion loss is what separates component draw from wall draw.
        "psu_efficiency": 0.90,
        # Used only when no backend reports real package watts. Linear
        # interpolation across CPU load, clearly flagged in the UI as modeled.
        # Calibrated against Core Temp's own reading on this machine: a 5700X
        # idles around 35 W of package power (the IOD alone accounts for much
        # of that) and its PPT ceiling is 88 W for a 65 W-TDP part.
        "estimate_cpu_when_missing": True,
        "cpu_idle_w": 35.0,
        "cpu_max_w": 88.0,
        # Full-scale for the power gauge. Should exceed your realistic peak.
        "gauge_max_w": 450.0,
    },
    # Which data source feeds which slot on screen. Editable from the
    # Layout tab, where the preview is clickable. "calc:" refs are portable
    # across machines; "lhm:" refs point at one specific sensor identifier.
    "layout": {
        # Which screen to draw. "gauges" is the rings-and-bars dashboard;
        # "buddy" is the character whose mood follows temperature and load.
        # Both read the same header, so the top strip is shared.
        "mode": "gauges",
        # The header strip. Two slots; each joins its two metrics with `sep`.
        "header": [
            {"metric": "calc:cpu_name", "detail": "calc:gpu_name",
             "sep": "   ·   ", "align": "left"},
            {"metric": "calc:date", "detail": "calc:clock",
             "sep": "   ", "align": "right"},
        ],
        "rings": [
            {"label": "CPU TEMP", "metric": "calc:cpu_temp",
             "detail": "calc:cpu_clock", "thresholds": "cpu_temp"},
            {"label": "GPU TEMP", "metric": "calc:gpu_temp",
             "detail": "calc:gpu_clock", "thresholds": "gpu_temp"},
            {"label": "SYSTEM POWER", "metric": "calc:system_power",
             "detail": "calc:power_breakdown", "thresholds": "power"},
        ],
        "bars": [
            {"label": "CPU LOAD", "metric": "calc:cpu_load", "top": "calc:blank",
             "detail": "calc:cpu_clock_nominal", "thresholds": "cpu_load"},
            {"label": "RAM", "metric": "calc:ram_pct", "top": "calc:ram_gb",
             "detail": "calc:ram_speed", "thresholds": "ram"},
            {"label": "V-RAM", "metric": "calc:vram_pct", "top": "calc:vram_gb",
             "detail": "calc:gpu_mem_clock", "thresholds": "vram"},
        ],
        # The bottom strip, on both screens: the two fans plus the wall-plug
        # estimate, each as bar + percent + reading. `percent` shows how far
        # up its own scale a row is, which is the only way a watts row can
        # say "42%" next to the RPM rows saying the same kind of thing.
        "fans": [
            {"label": "CPU FAN", "metric": "calc:cpu_fan_rpm",
             "detail": "calc:blank", "max": 2200.0, "min": 0.0,
             "percent": True},
            {"label": "GPU FAN", "metric": "calc:gpu_fan_rpm",
             "detail": "calc:blank", "max": 3400.0, "min": 0.0,
             "percent": True},
            {"label": "POWER", "metric": "calc:system_power",
             "detail": "calc:blank", "max": 650.0, "min": 0.0,
             "thresholds": "power", "percent": True},
        ],
        # Cards flanking the character in "buddy" mode, filled left, right,
        # then down. Their second line gets roughly eight characters once the
        # numeral has taken its share, so keep those metrics short.
        "stats": [
            {"label": "CPU TEMP", "metric": "calc:cpu_temp",
             "top": "calc:cpu_clock_nominal", "detail": "calc:cpu_clock",
             "thresholds": "cpu_temp"},
            {"label": "GPU TEMP", "metric": "calc:gpu_temp",
             "top": "calc:blank", "detail": "calc:gpu_clock",
             "thresholds": "gpu_temp"},
            {"label": "CPU LOAD", "metric": "calc:cpu_load",
             "top": "calc:blank", "detail": "calc:cpu_power",
             "thresholds": "cpu_load"},
            {"label": "GPU LOAD", "metric": "calc:gpu_load",
             "top": "calc:blank", "detail": "calc:gpu_power",
             "thresholds": "cpu_load"},
            {"label": "RAM", "metric": "calc:ram_pct",
             "top": "calc:ram_speed", "detail": "calc:ram_gb",
             "thresholds": "ram"},
            {"label": "V-RAM", "metric": "calc:vram_pct",
             "top": "calc:gpu_mem_clock", "detail": "calc:vram_gb",
             "thresholds": "vram"},
        ],
        "buddy": {
            # The character bobs and blinks, so it repaints far faster than
            # the gauge screen's ui_hz. Every animation is written against
            # real elapsed time, so this trades CPU for smoothness and
            # nothing else: measured on a 5700X at 1280x720, 30 fps costs
            # roughly a sixth of one core (about 1% of the whole CPU), 15 fps
            # about 8% of one core. Nearly all of it is Tk redrawing -- the
            # scene's own logic is 1-2 ms of each frame. Clamped to 4..30.
            "fps": 30,
            #   drawn  a face built from canvas shapes, which morphs between
            #          moods and takes its colours from the palette
            #   emoji  a real colour emoji per mood, rasterised through Pillow
            #          because Tk on Windows can only draw them in outline
            #   image  your own picture per mood. Anything missing falls back
            #          to that mood's emoji, and then to the drawn face
            "character": "drawn",
            # Colour of the drawn face: "classic" is emoji yellow shading to
            # red as it heats, "theme" wears your accent colour instead.
            "tint": "classic",
            # An animated backdrop behind the character, reacting to the same
            # numbers the mood does. "off", or one of: starfield, synthwave,
            # matrix, aquarium, skyline. See the Character tab.
            "scene": "off",
            # The festive calendar: Makar Sankranti kites, Holi gulal, a
            # Navratri garba ring, Diwali diyas and fireworks, the tricolour
            # on Republic and Independence Day, December snow and New Year
            # fireworks. Lunisolar dates are table-driven in seasonal.py
            # (2026-2030, main day per drik panchang) -- edit there if your
            # region observes a day earlier or later. Dormant otherwise.
            "seasonal": True,
            # How far the mood is pulled toward the theme accent while idle.
            # Without this the Theme tab has almost nothing to act on here,
            # since the mood otherwise owns every colour on screen. The pull
            # fades out as stress rises, so a hot machine still goes red.
            "theme_blend": 0.40,
            # The mood word under the character (WORKING, MELTING...) stays;
            # the changing one-liner beneath it ships off -- the word carries
            # the state, the quip was decoration. The pet's DAY/status line
            # rides the caption switch.
            "show_caption": True,
            "show_quips": False,
            # One emoji per mood, used when character is "emoji". Any glyph
            # your emoji font has. Windows 10 has no melting face, so the top
            # mood defaults to the exploding head.
            "faces": {
                "offline": "\U0001F636", "sleepy": "\U0001F634",
                "chill": "\U0001F60E", "busy": "\U0001F624",
                "sweaty": "\U0001F613", "melting": "\U0001F92F",
            },
            # One picture per mood, used when character is "image". Paths are
            # relative to this file where possible, so a moved install keeps
            # working. Browsing for one in the settings window copies it into
            # a "faces" folder next to config.json.
            "images": {},
            # Override the word under the face. Anything left out keeps the
            # built-in: CHILLING, WORKING, COOKING and so on.
            "captions": {},
            # Override the one-liners, as {mood: ["line", "line"]}.
            "quips": {},
            # What the stress meter is computed from. Both terms are
            # normalised against the thresholds above, so warn lands at 0.6 and
            # critical at 1.0 whatever the units, and the larger one wins.
            "stress": {
                # Any numeric "calc:" metric name works here.
                "heat_sources": ["cpu_temp", "gpu_temp"],
                "load_sources": ["cpu_load", "gpu_load"],
                # Below this temperature the heat term reads zero.
                "heat_floor": 32.0,
                # Load is scaled by this before the two are compared. Under 1.0
                # so a fully loaded but cool machine reads as working rather
                # than suffering: pure load tops out inside "cooking" and
                # cannot reach "melting" on its own.
                "load_weight": 0.78,
                # Quiet for this long and the character naps. 0 disables it.
                "nap_after_seconds": 90.0,
                # Pin the mood scale to THIS machine's real thermal window,
                # per sensor: [floor, sweaty point, melting point] in deg C.
                # A cool-running machine never hits the alert thresholds, so
                # without this its top moods are unreachable. Alert colours
                # keep using "thresholds"; this only moves the character.
                # Example: {"cpu_temp": [50, 66, 74], "gpu_temp": [48, 60, 65]}
                "heat_bands": {},
            },
            # Where each mood ends, on the 0-1 stress scale: chilling up to
            # the first number, working up to the second, sweating up to the
            # third, melting above it. Edited on the Character tab.
            "bands": {"chill": 0.42, "busy": 0.66, "sweaty": 0.86},
        },
    },
    # [warn, critical]. Temperatures in degrees C, everything else in percent.
    "thresholds": {
        "cpu_temp": [72, 85],
        "gpu_temp": [72, 83],
        "cpu_load": [80, 95],
        "ram": [80, 92],
        "vram": [80, 93],
        "power": [65, 85],
    },
}


def _strip_comments(node):
    """JSON has no comments, so keys starting with "_" are treated as prose.

    Applied at every level, not just the top, so the shipped config.json can
    explain a setting right next to it.
    """
    if isinstance(node, dict):
        return {k: _strip_comments(v) for k, v in node.items() if not k.startswith("_")}
    return node


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _stamp_dir(cfg: dict[str, Any], path: str) -> dict[str, Any]:
    """Record where the config came from, for resolving relative file paths.

    Underscore-prefixed, so _diff and _strip_comments both skip it and it can
    never end up written back to disk as a setting.
    """
    cfg["_config_dir"] = os.path.dirname(os.path.abspath(path))
    return cfg


def load(path: str | None = None) -> dict[str, Any]:
    """Load config.json next to the app, or from `path`. Missing file is fine."""
    if path is None:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, "config.json")
    if not os.path.isfile(path):
        return _stamp_dir(copy.deepcopy(DEFAULTS), path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            user = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"[casebuddy] ignoring {path}: {exc}")
        return _stamp_dir(copy.deepcopy(DEFAULTS), path)
    if not isinstance(user, dict):
        return _stamp_dir(copy.deepcopy(DEFAULTS), path)
    return _stamp_dir(_migrate(_merge(DEFAULTS, _strip_comments(user))), path)


def _migrate(cfg: dict[str, Any]) -> dict[str, Any]:
    """Bring older config files forward.

    Kept here rather than in the code that reads each setting, so there is one
    place to look when a key is renamed and no reader has to know two spellings
    forever.
    """
    buddy = cfg.get("layout", {}).get("buddy")
    if isinstance(buddy, dict):
        # "quips" was a bool before it became per-mood text.
        if isinstance(buddy.get("quips"), bool):
            buddy["show_quips"] = buddy["quips"]
            buddy["quips"] = {}
        # "skin" split into "character" (drawn or emoji) and "tint".
        skin = buddy.pop("skin", None)
        if skin is not None and "tint" not in buddy:
            buddy["tint"] = "theme" if skin == "theme" else "classic"
    # The mood set shrank from eight to six: "happy" merged into "chill" and
    # "hot" into "sweaty" (their faces live on as alternates). Overrides for
    # the retired keys would sit in the file forever doing nothing.
    if isinstance(buddy, dict):
        for group in ("faces", "captions", "quips", "images"):
            table = buddy.get(group)
            if isinstance(table, dict):
                for stale in ("happy", "hot"):
                    table.pop(stale, None)
    # Per-tile type controls were reworked from a font family plus two scale
    # factors into font_size / bold / italic. The old keys draw nothing now;
    # drop them so old configs stop hauling them around.
    for kind in ("rings", "bars", "fans", "stats", "header"):
        for slot in cfg.get("layout", {}).get(kind, []) or []:
            if isinstance(slot, dict):
                for stale in ("font", "value_scale", "label_scale"):
                    slot.pop(stale, None)
    return cfg


def config_path(path: str | None = None) -> str:
    """Where config.json lives: next to casebuddy.py unless told otherwise."""
    if path:
        return path
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "config.json")


def _diff(base: dict, current: dict) -> dict:
    """Only what differs from DEFAULTS.

    Saving the whole resolved config would freeze today's defaults into the
    file, so a later change to a default the user never touched would silently
    not reach them. Writing just the overrides keeps the file small and lets
    everything else keep tracking the code.
    """
    out: dict[str, Any] = {}
    for key, value in current.items():
        if key.startswith("_"):
            continue
        if key not in base:
            # A key from an older version that DEFAULTS no longer has. It would
            # otherwise look like an override and be rewritten forever.
            continue
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            sub = _diff(base[key], value)
            if sub:
                out[key] = sub
        elif base.get(key) != value:
            out[key] = value
    return out


def _keep_comments(existing: Any, overrides: dict) -> dict:
    """Carry the user's "_"-prefixed notes across a save."""
    out: dict[str, Any] = {}
    if isinstance(existing, dict):
        for key, value in existing.items():
            if key.startswith("_"):
                out[key] = value
    for key, value in overrides.items():
        prior = existing.get(key) if isinstance(existing, dict) else None
        if isinstance(value, dict):
            out[key] = _keep_comments(prior if isinstance(prior, dict) else {}, value)
        else:
            out[key] = value
    return out


def save(cfg: dict[str, Any], path: str | None = None) -> str:
    """Persist the overrides. Returns the path written."""
    path = config_path(path)
    existing: dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, ValueError):
            existing = {}

    payload = _keep_comments(existing, _diff(DEFAULTS, cfg))
    # Write-then-rename: a crash mid-write must not leave a truncated config
    # that the next launch silently falls back to defaults on.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path
