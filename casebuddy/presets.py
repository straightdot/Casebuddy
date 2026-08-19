"""Ready-made screens: a layout and a palette applied together.

A preset is not a new mechanism. It writes exactly the same cfg["layout"] and
cfg["theme"] the Layout and Theme tabs write by hand, so anything a preset does
can afterwards be picked apart, repointed at a different sensor, or recoloured,
and nothing here has to know about it. That is the whole reason presets are
data rather than code paths.

Every preset carries a COMPLETE layout, including the slots its own mode does
not draw. A buddy preset still ships rings and bars; a gauge preset still ships
stat cards. It costs a few lines and means switching mode later -- in the
editor, or by hand in config.json -- always finds sensible slots waiting rather
than an empty screen.
"""

from __future__ import annotations

import copy

from . import config

# Kept next to the presets that use it so a change here reaches all of them.
_HEADER = [
    {"metric": "calc:cpu_name", "detail": "calc:gpu_name",
     "sep": "   ·   ", "align": "left"},
    {"metric": "calc:date", "detail": "calc:clock", "sep": "   ", "align": "right"},
]


def _layout(mode: str = "gauges", **slots) -> dict:
    """A complete layout: the shipped default, with the named slots replaced."""
    base = copy.deepcopy(config.DEFAULTS["layout"])
    base["mode"] = mode
    base["header"] = copy.deepcopy(_HEADER)
    for key, value in slots.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            # Merged, not replaced. A preset that names two character options
            # must not silently drop fps, the stress weights and the face map
            # that live in the same block.
            merged = copy.deepcopy(base[key])
            merged.update(copy.deepcopy(value))
            base[key] = merged
        else:
            base[key] = copy.deepcopy(value)
    return base


def _ring(label: str, metric: str, detail: str, thresholds: str = "",
          lo: float | None = None, hi: float | None = None) -> dict:
    slot = {"label": label, "metric": metric, "detail": detail}
    if thresholds:
        slot["thresholds"] = thresholds
    if lo is not None:
        slot["min"] = lo
    if hi is not None:
        slot["max"] = hi
    return slot


def _bar(label: str, metric: str, top: str, detail: str,
         thresholds: str = "") -> dict:
    slot = {"label": label, "metric": metric, "top": top, "detail": detail}
    if thresholds:
        slot["thresholds"] = thresholds
    return slot


# The shipped bottom strip: both fans plus the wall-plug estimate. Presets
# reorder it but never thin it -- these three rows are the baseline every
# screen carries (Minimal excepted, whose whole point is an empty strip).
_FANS = [
    {"label": "CPU FAN", "metric": "calc:cpu_fan_rpm",
     "detail": "calc:blank", "max": 2200.0, "min": 0.0, "percent": True},
    {"label": "GPU FAN", "metric": "calc:gpu_fan_rpm",
     "detail": "calc:blank", "max": 3400.0, "min": 0.0, "percent": True},
    {"label": "POWER", "metric": "calc:system_power",
     "detail": "calc:blank", "max": 650.0, "min": 0.0,
     "thresholds": "power", "percent": True},
]
_FANS_GPU_FIRST = [_FANS[1], _FANS[0], _FANS[2]]


PRESETS: dict[str, dict] = {
    "classic": {
        "title": "Classic",
        "kind": "Gauges",
        "blurb": "The shipped screen. Temperatures and system power as rings, "
                 "load and memory as bars, both fans and the wall-plug watts "
                 "along the bottom. The one to come back to.",
        "theme": {"preset": "violet"},
        "layout": _layout("gauges"),
    },
    "thermal": {
        "title": "Thermal",
        "kind": "Gauges",
        "blurb": "Everything that gets hot, largest first: CPU die, GPU core "
                 "and GPU hot spot. Loads drop to bars. For watching a cooling "
                 "change or chasing a throttle.",
        "theme": {"preset": "crimson"},
        "layout": _layout(
            "gauges",
            rings=[
                _ring("CPU TEMP", "calc:cpu_temp", "calc:cpu_clock", "cpu_temp"),
                _ring("GPU TEMP", "calc:gpu_temp", "calc:gpu_clock", "gpu_temp"),
                _ring("GPU HOT SPOT", "calc:gpu_hotspot", "calc:gpu_fan_rpm",
                      "gpu_temp", 30.0, 110.0),
            ],
            bars=[
                _bar("CPU LOAD", "calc:cpu_load", "calc:cpu_power",
                     "calc:cpu_clock_nominal", "cpu_load"),
                _bar("GPU LOAD", "calc:gpu_load", "calc:gpu_power",
                     "calc:gpu_clock", "cpu_load"),
                _bar("RAM", "calc:ram_pct", "calc:ram_gb", "calc:ram_speed", "ram"),
            ],
        ),
    },
    "gaming": {
        "title": "Gaming",
        "kind": "Gauges",
        "blurb": "GPU first and the CPU relegated. Core temperature, hot spot "
                 "and board power up top; GPU load and V-RAM below. GPU fan "
                 "takes the first bar.",
        "theme": {"preset": "ice"},
        "layout": _layout(
            "gauges",
            rings=[
                _ring("GPU TEMP", "calc:gpu_temp", "calc:gpu_clock", "gpu_temp"),
                _ring("GPU HOT SPOT", "calc:gpu_hotspot", "calc:gpu_fan_pct",
                      "gpu_temp", 30.0, 110.0),
                _ring("GPU POWER", "calc:gpu_power", "calc:gpu_mem_clock",
                      "", 0.0, 400.0),
            ],
            bars=[
                _bar("GPU LOAD", "calc:gpu_load", "calc:blank",
                     "calc:gpu_clock", "cpu_load"),
                _bar("V-RAM", "calc:vram_pct", "calc:vram_gb",
                     "calc:gpu_mem_clock", "vram"),
                _bar("CPU LOAD", "calc:cpu_load", "calc:blank",
                     "calc:cpu_clock", "cpu_load"),
            ],
            fans=_FANS_GPU_FIRST,
        ),
    },
    "power": {
        "title": "Power draw",
        "kind": "Gauges",
        "blurb": "Watts everywhere. Wall-plug estimate flanked by the two "
                 "components that are actually measured, so you can see which "
                 "half of the machine is spending it.",
        "theme": {"preset": "amber"},
        "layout": _layout(
            "gauges",
            rings=[
                _ring("SYSTEM POWER", "calc:system_power",
                      "calc:power_breakdown", "power"),
                _ring("CPU POWER", "calc:cpu_power", "calc:cpu_clock",
                      "", 0.0, 120.0),
                _ring("GPU POWER", "calc:gpu_power", "calc:gpu_clock",
                      "", 0.0, 400.0),
            ],
            bars=[
                _bar("CPU LOAD", "calc:cpu_load", "calc:cpu_temp",
                     "calc:cpu_clock_nominal", "cpu_load"),
                _bar("GPU LOAD", "calc:gpu_load", "calc:gpu_temp",
                     "calc:gpu_clock", "cpu_load"),
                _bar("RAM", "calc:ram_pct", "calc:ram_gb", "calc:ram_speed", "ram"),
            ],
        ),
    },
    "minimal": {
        "title": "Minimal",
        "kind": "Gauges",
        "blurb": "Greyscale, and the fan strip switched off entirely. Six "
                 "numbers and nothing else. The quietest thing to have glowing "
                 "inside a case at night.",
        "theme": {"preset": "mono"},
        "layout": _layout(
            "gauges",
            bars=[
                _bar("CPU LOAD", "calc:cpu_load", "calc:blank",
                     "calc:cpu_clock", "cpu_load"),
                _bar("RAM", "calc:ram_pct", "calc:ram_gb", "calc:blank", "ram"),
                _bar("V-RAM", "calc:vram_pct", "calc:vram_gb", "calc:blank", "vram"),
            ],
            fans=[],
        ),
    },
    "buddy": {
        "title": "Buddy",
        "kind": "Character",
        "blurb": "A face that reacts to the machine, wearing the accent "
                 "colour -- shipped in neon green, and it follows whatever "
                 "accent you pick afterwards. Chilling behind sunglasses at "
                 "idle; sweating, then on fire as the machine cooks. Swap the "
                 "face for an emoji or your own picture on the Character tab.",
        "theme": {"preset": "green"},
        "layout": _layout("buddy", buddy={"character": "drawn", "tint": "theme"}),
    },
    "spidey": {
        "title": "Web-slinger",
        "kind": "Character",
        "blurb": "A hero over a city skyline whose windows light up as the "
                 "CPU and GPU work. He hangs while it idles, swings as the "
                 "load climbs -- swing width from the real wind outside -- "
                 "and past ninety percent he squares up and fights the "
                 "hottest tile.",
        "theme": {"preset": "web"},
        "layout": _layout("buddy",
                          buddy={"character": "spider", "scene": "skyline"}),
    },
    "statusface": {
        "title": "Status face",
        "kind": "Character",
        "blurb": "The classic corner-of-the-HUD face, promoted to the whole "
                 "panel. Smirks while the machine idles; sweats, bruises and "
                 "bleeds as the heat climbs. The eyes glance around on their "
                 "own, exactly as they used to.",
        "theme": {"preset": "doomguy"},
        "layout": _layout("buddy", buddy={"character": "doom"}),
    },
    "mech": {
        "title": "Mech",
        "kind": "Character",
        "blurb": "The service robot in the code rain. Its antenna blinks "
                 "with real network traffic, the five-LED mouth is a load "
                 "bar, its eyes sweep the room on patrol, and past the warn "
                 "line the vents start venting. The matrix falls faster as "
                 "the machine works.",
        "theme": {"preset": "green"},
        "layout": _layout("buddy",
                          buddy={"character": "robot", "scene": "matrix"}),
    },
    "dragonslair": {
        "title": "Dragon's Lair",
        "kind": "Character",
        "blurb": "A serpent in a lava cave. It coils up and blows smoke "
                 "rings while the machine sleeps, glides and perches at "
                 "idle, circles its hoard as the load climbs, and past the "
                 "melting line it breathes fire at the hottest tile until "
                 "the card rattles. The pool below glows with the real "
                 "temperature; embers rise with the load.",
        "theme": {"preset": "drake"},
        "layout": _layout("buddy",
                          buddy={"character": "dragon", "scene": "lair"}),
    },
    "circuit": {
        "title": "Circuit",
        "kind": "Character",
        "blurb": "A racer whose track is the layout itself. It laps the "
                 "screen, drifting every corner with tire smoke, hits nitro "
                 "as the machine sweats, and at melting leaves the circuit "
                 "to do smoking donuts around the hottest tile. Pit stop on "
                 "the mood word when the machine naps; headlights after "
                 "real sunset; a checkered flag for surviving a melt.",
        "theme": {"preset": "asphalt"},
        "layout": _layout("buddy",
                          buddy={"character": "car", "scene": "raceway"}),
    },
    "starship": {
        "title": "Starship",
        "kind": "Character",
        "blurb": "A ship over the star stream. It holds station while the "
                 "machine idles, flies patrol as the load rises -- "
                 "afterburners lengthening with the work, the stars streaking "
                 "past at the same load -- and past eighty-five percent it "
                 "swings in and strafes whichever tile is causing the "
                 "trouble. Every hit lands visibly.",
        "theme": {"preset": "nebula"},
        "layout": _layout("buddy",
                          buddy={"character": "ship", "scene": "starfield"}),
    },
    "fishtank": {
        "title": "Fish tank",
        "kind": "Character",
        "blurb": "A pet that grows with uptime and sulks the day after you "
                 "cook it, floating over an aquarium: fish swim at fan speed, "
                 "bubbles rise with CPU load, and the water itself heats from "
                 "blue toward red.",
        "theme": {"preset": "lagoon"},
        "layout": _layout("buddy",
                          buddy={"character": "pet", "scene": "aquarium"}),
    },
}

NAMES = tuple(PRESETS)


# Alert sets a tile can be judged against. The empty first entry means "no
# alerts": the tile keeps the normal accent colour whatever it reads.
ALERT_SETS = ("(none)", "cpu_temp", "gpu_temp", "cpu_load", "ram", "vram", "power")

# What a metric is judged against when you first point a tile at it. Anything
# absent gets no alerts rather than an inherited pair that means nothing for it.
ALERT_FOR = {
    "calc:cpu_temp": "cpu_temp",
    "calc:gpu_temp": "gpu_temp",
    "calc:gpu_hotspot": "gpu_temp",
    "calc:mobo_temp": "cpu_temp",
    "calc:cpu_load": "cpu_load",
    "calc:gpu_load": "cpu_load",
    "calc:ram_pct": "ram",
    "calc:vram_pct": "vram",
    "calc:system_power": "power",
}


# How many tiles each kind of slot can hold. The gauge screen has three
# columns and three fan rows; the character screen has three card rows a side.
# The header is two fixed pieces of text, so it is neither grown nor shrunk.
LIMITS = {"header": 2, "rings": 3, "bars": 3, "fans": 3, "stats": 6}


def slot_limit(kind: str) -> int:
    return LIMITS.get(kind, 0)


def blank_slot(kind: str) -> dict:
    """A fresh empty tile of the right shape, ready to be pointed somewhere."""
    slot = {"label": "NEW TILE", "metric": "calc:blank", "detail": "calc:blank"}
    if kind in ("bars", "stats"):
        slot["top"] = "calc:blank"
    if kind == "fans":
        slot.update({"label": "FAN", "metric": "calc:blank", "max": 2200.0})
    return slot


# The palette keys a user can override one at a time on the Look tab.
_THEME_COLOURS = ("bg", "edge", "track", "text", "dim", "faint",
                  "accent", "na", "warn", "crit")


def user_presets(cfg: dict) -> dict:
    """Presets the user saved from the Layout tab, normalised to the same
    shape as the built-ins. Anything malformed is skipped, not raised: these
    live in config.json and are hand-editable."""
    out: dict[str, dict] = {}
    for key, entry in (cfg.get("user_presets") or {}).items():
        if not isinstance(entry, dict) or not isinstance(entry.get("layout"),
                                                         dict):
            continue
        out[str(key)] = {
            "title": str(entry.get("title", key)),
            "kind": "Custom",
            "blurb": str(entry.get("blurb", "")
                         or "Saved from the Layout tab. Update it there, or "
                            "hand-edit user_presets in config.json."),
            "theme": dict(entry.get("theme") or {}),
            "layout": entry["layout"],
        }
    return out


def registry(cfg: dict) -> dict:
    """Every preset this config can see: built-ins, then the user's own."""
    book = dict(PRESETS)
    book.update(user_presets(cfg))
    return book


def apply(cfg: dict, name: str) -> dict:
    """`cfg` with the preset's layout and palette in place. Never mutates.

    THE USER'S HAND WINS. A preset brings its own base palette, but any
    colour the user has explicitly picked on the Look tab -- stored as a
    non-empty override key -- plus their font families and sizes, carry
    across the switch instead of being wiped by it. Applying a preset should
    change the screen, not undo the parts of the look they chose on purpose.
    Picking a palette by name on the Look tab is still the way to shed the
    overrides deliberately.
    """
    preset = registry(cfg)[name]
    out = copy.deepcopy(cfg)
    out["layout"] = copy.deepcopy(preset["layout"])
    mine = cfg.get("theme", {}) or {}
    out["theme"] = copy.deepcopy(config.DEFAULTS["theme"])
    out["theme"].update(copy.deepcopy(preset["theme"]))
    if name in PRESETS:
        # Built-ins: the user's hand-picked colours and fonts survive.
        # A USER preset's saved theme IS their hand, so it applies verbatim.
        for key in _THEME_COLOURS:
            value = str(mine.get(key, "") or "").strip()
            if value:
                out["theme"][key] = value
        for key in ("fonts", "font_scale"):
            if isinstance(mine.get(key), dict):
                out["theme"][key] = copy.deepcopy(mine[key])
    return out


def matches(cfg: dict) -> str | None:
    """Which preset this config still is, if any.

    Compared on the layout alone. Recolouring a preset leaves it recognisable
    as that preset; repointing a tile at a different sensor does not, which is
    the distinction the gallery wants when it marks the current entry.
    """
    def shape(layout: dict) -> tuple:
        # Everything that changes what is on screen -- but not fps or the
        # quip toggle, which are preferences rather than a different screen.
        # Character, tint and scene stay in: they are all that separates the
        # character presets from each other.
        buddy = layout.get("buddy") or {}
        return (
            str(layout.get("mode", "gauges")).lower(),
            layout.get("header"), layout.get("rings"), layout.get("bars"),
            layout.get("fans"), layout.get("stats"),
            str(buddy.get("character", "drawn")).lower(),
            str(buddy.get("tint", "classic")).lower(),
            str(buddy.get("scene", "off")).lower(),
        )

    mine = shape(cfg.get("layout", {}))
    for name, preset in registry(cfg).items():
        if mine == shape(preset["layout"]):
            return name
    return None
