"""Colors, fonts and the coordinate transform.

EVERYTHING in dashboard.py is authored against a fixed 1920x1080 design space.
`Geometry` maps that onto whatever the window actually is, and also handles the
4:3 panel problem described below.

WHY THE SIZES ARE SO LARGE
--------------------------
The panel is 1280x720 native across a 4.3" diagonal:

    diagonal in pixels = sqrt(1280^2 + 720^2) = 1469 px
    pixel density      = 1469 px / 4.3 in     = 342 PPI
    one panel pixel    = 25.4 / 342           = 0.074 mm

Everything here is authored in a 1920x1080 design space, which Geometry maps
onto the real window: at 1280x720 that is a uniform 0.667x, so one design pixel
is 0.667 * 0.074 = 0.050 mm of glass.

    F_DETAIL  54 px -> 2.7 mm
    F_LABEL   56 px -> 2.8 mm
    F_HERO   140 px -> 6.9 mm
    stroke     8 px -> 0.40 mm

DRIVE THE PANEL AT ITS NATIVE RESOLUTION. Feeding this one an upscaled
1920x1080 signal caused desktop-wide stutter and visible cursor lag, and the
scaler's downscale blurred everything on top of that. At native 1280x720 the
render is 1:1 and text is noticeably crisper. The sizes above are unchanged
from the upscaled era because the physical result works out nearly identical
(0.050 mm/px versus 0.046 mm before) -- what improved is sharpness, not size.
"""

from __future__ import annotations

DESIGN_W = 1920
DESIGN_H = 1080

# --- palette --------------------------------------------------------------
# Purple on black. The background is near-black with a violet cast so the panel
# throws a coloured glow into the case rather than a grey one.
#
# Status colours deliberately break the scheme: purple means fine, and amber or
# red means something needs attention. A monitor that is one colour until it is
# not is far easier to read at a glance than one where every tile competes.

BG = "#07050d"
PANEL = "#100b1c"
PANEL_EDGE = "#2b1f4a"
TRACK = "#1d1436"

TEXT = "#f3edff"
TEXT_DIM = "#b9a5e8"
# Was #4d5a6b and effectively unreadable once the panel had downscaled it.
TEXT_FAINT = "#8a76b8"

# One accent colour everywhere. An earlier pass used three shades -- a lighter
# tint for the CPU fan bar and a brighter one for the gauges -- which made the
# panel look like it was signalling differences that did not exist.
OK = "#8b5cf6"
WARN = "#ffb020"
CRIT = "#ff3d71"
NA = "#4d3f70"

STATE_COLOR = {"ok": OK, "warn": WARN, "crit": CRIT, "na": NA}

FAN_CPU = OK
FAN_GPU = OK

# --- themes ---------------------------------------------------------------
#
# Each preset sets only the colours that define its character; anything it
# omits is inherited from the violet baseline above. WARN and CRIT are
# deliberately shared across every preset: the whole point of the scheme is
# that the panel is one colour until something needs attention, and a theme
# that recoloured the alarm states would defeat that.

PRESETS: dict[str, dict[str, str]] = {
    "violet": {
        "bg": "#07050d", "edge": "#2b1f4a", "track": "#1d1436",
        "text": "#f3edff", "dim": "#b9a5e8", "faint": "#8a76b8",
        "accent": "#8b5cf6", "na": "#4d3f70",
    },
    "cyan": {
        "bg": "#03080c", "edge": "#12303c", "track": "#0d2430",
        "text": "#e8f7ff", "dim": "#8fc9de", "faint": "#5f93a6",
        "accent": "#22d3ee", "na": "#2c4650",
    },
    "amber": {
        "bg": "#0a0703", "edge": "#3a2a12", "track": "#2b1f0d",
        "text": "#fff6e8", "dim": "#e0c08f", "faint": "#a68a5f",
        "accent": "#f59e0b", "na": "#524231",
    },
    "green": {
        "bg": "#040906", "edge": "#153524", "track": "#0f281b",
        "text": "#eafff3", "dim": "#93d9b4", "faint": "#63a583",
        "accent": "#2ee6a8", "na": "#2f4c3e",
    },
    "crimson": {
        "bg": "#0b0406", "edge": "#3c1520", "track": "#2c0f18",
        "text": "#fff0f3", "dim": "#e5a3b0", "faint": "#a86f7c",
        "accent": "#f43f5e", "na": "#54303a",
    },
    "ice": {
        "bg": "#04060b", "edge": "#1b2740", "track": "#131c30",
        "text": "#eef4ff", "dim": "#a8bcdd", "faint": "#7387a8",
        "accent": "#60a5fa", "na": "#37455e",
    },
    "mono": {
        "bg": "#050505", "edge": "#2a2a2a", "track": "#1c1c1c",
        "text": "#f5f5f5", "dim": "#b4b4b4", "faint": "#828282",
        "accent": "#e4e4e4", "na": "#454545",
    },
    # The character presets each get a palette that IS their vibe, not just
    # a nearest match from the generic seven.
    "web": {
        # The suit: spider red on midnight blue. Everything structural is
        # blue so the red is his alone.
        "bg": "#060913", "edge": "#22335f", "track": "#16224a",
        "text": "#f2f4ff", "dim": "#9fb0dd", "faint": "#6c7cab",
        "accent": "#e8414f", "na": "#3c4569",
    },
    "doomguy": {
        # Scorched brass and ember: the brown steel of the old status bar,
        # with the ammo-counter orange-red as the accent.
        "bg": "#0a0603", "edge": "#42301b", "track": "#2e2113",
        "text": "#ffeeda", "dim": "#d8b48b", "faint": "#9c7f58",
        "accent": "#e0562b", "na": "#4e4030",
    },
    "lagoon": {
        # Deeper and greener than cyan: tank glass at night, kelp edges,
        # a teal accent that reads as water rather than as electronics.
        "bg": "#02100f", "edge": "#124238", "track": "#0c2f2a",
        "text": "#e6fffb", "dim": "#8fd6c8", "faint": "#5da294",
        "accent": "#2dd4bf", "na": "#2b4a44",
    },
    "nebula": {
        # Deep space: indigo-black, faint violet dust, an ion-engine blue
        # accent. Built for the starship over the starfield.
        "bg": "#04040f", "edge": "#242a5c", "track": "#161b44",
        "text": "#eef0ff", "dim": "#a7aeea", "faint": "#7078b8",
        "accent": "#6d8cff", "na": "#3a4070",
    },
    "drake": {
        # Obsidian, old gold and ember: cave walls lit by the pool.
        "bg": "#0d0503", "edge": "#4a2a10", "track": "#331d0b",
        "text": "#ffeedd", "dim": "#dfae7e", "faint": "#a37a4f",
        "accent": "#ff9c2e", "na": "#4e3a28",
    },
    "asphalt": {
        # Graphite and signal yellow: pit lane at night.
        "bg": "#0a0a0c", "edge": "#33363e", "track": "#222429",
        "text": "#f5f6f8", "dim": "#c2c6cf", "faint": "#878c96",
        "accent": "#ffd21f", "na": "#45484f",
    },
}

PRESET_NAMES = tuple(PRESETS)

# Keys the user may override individually, mapped to the module globals they
# drive. Kept explicit so a typo in config.json cannot silently set some
# unrelated attribute.
_COLOR_KEYS = {
    "bg": "BG", "edge": "PANEL_EDGE", "track": "TRACK",
    "text": "TEXT", "dim": "TEXT_DIM", "faint": "TEXT_FAINT",
    "accent": "OK", "na": "NA", "warn": "WARN", "crit": "CRIT",
}

_HEX = __import__("re").compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def resolve(theme_cfg: dict) -> dict[str, str]:
    """Preset plus per-key overrides -> a full {key: '#rrggbb'} palette."""
    base = dict(PRESETS["violet"])
    base.setdefault("warn", WARN)
    base.setdefault("crit", CRIT)
    name = str((theme_cfg or {}).get("preset", "violet")).lower()
    if name in PRESETS:
        base.update(PRESETS[name])
    for key in _COLOR_KEYS:
        value = (theme_cfg or {}).get(key)
        # An empty string means "inherit from the preset", which is what the
        # settings UI writes when a custom colour is cleared.
        if isinstance(value, str) and value.strip() and _HEX.match(value.strip()):
            base[key] = value.strip()
    base.setdefault("warn", "#ffb020")
    base.setdefault("crit", "#ff3d71")
    return base


def dim(colour: str, amount: float) -> str:
    """Pull one colour toward black. `amount` 0 leaves it alone."""
    amount = max(0.0, min(1.0, amount))
    if amount <= 0.0:
        return colour
    red, green, blue = (int(v * (1.0 - amount)) for v in _rgb(colour))
    return f"#{red:02x}{green:02x}{blue:02x}"


def _rgb(colour: str) -> tuple[int, int, int]:
    text = colour.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def apply(theme_cfg: dict, night: float = 0.0) -> dict[str, str]:
    """Push a palette into the module globals the drawing code reads.

    dashboard.py looks colours up as theme.OK / theme.BG at paint time rather
    than caching them, so rebinding these is enough for a theme change to take
    effect on the next repaint.

    `night` fades the whole palette toward black. It is a separate argument
    rather than a config key because it is not a preference -- it tracks the
    daylight figure and changes by itself through the evening.
    """
    palette = resolve(theme_cfg)
    if night > 0.0:
        palette = {key: dim(value, night) for key, value in palette.items()}
    globals_ = globals()
    for key, attr in _COLOR_KEYS.items():
        globals_[attr] = palette[key]
    scale = (theme_cfg or {}).get("font_scale") or {}

    def clamp(value, low=0.5, high=2.0):
        try:
            return max(low, min(high, float(value)))
        except (TypeError, ValueError):
            return 1.0

    globals_["NUMERAL_SCALE"] = clamp(scale.get("numeral", 1.0))
    globals_["LABEL_SCALE"] = clamp(scale.get("label", 1.0))
    globals_["FAN_CPU"] = palette["accent"]
    globals_["FAN_GPU"] = palette["accent"]
    globals_["STATE_COLOR"] = {
        "ok": palette["accent"], "warn": palette["warn"],
        "crit": palette["crit"], "na": palette["na"],
    }
    return palette

# --- font sizes, in design-space pixels -----------------------------------

F_HERO = 140       # the big number in a tile
F_HERO_SMALL = 112  # same slot when the value needs 4+ glyphs
F_UNIT = 50
F_LABEL = 56
F_DETAIL = 54      # raised from 46: 2.1 mm on the panel was too small to read
F_HEADER = 50
F_FAN = 52

STROKE_MIN = 8
RING_STROKE = 30
BAR_HEIGHT = 38
FAN_BAR_HEIGHT = 34


def pick_font(families: set[str], candidates: list[str], fallback: str) -> str:
    for name in candidates:
        if name in families:
            return name
    return fallback


# Bahnschrift is Microsoft's DIN derivative -- condensed, unambiguous digits,
# ships with Windows 10, and holds up well when scaled down hard. Segoe UI
# Semibold is the fallback for the same reason it is the Windows UI face.
NUMERAL_CANDIDATES = ["Bahnschrift SemiBold Condensed", "Bahnschrift", "Segoe UI Semibold", "Segoe UI"]
LABEL_CANDIDATES = ["Segoe UI Semibold", "Segoe UI", "Bahnschrift", "Tahoma"]
MONO_CANDIDATES = ["Cascadia Mono", "Consolas", "Lucida Console", "Courier New"]


# Multiplied into every font at creation, on top of display.text_scale.
# Separate numerals from labels because they do different jobs: the numerals
# want to be as large as the tile allows, while the labels around them mostly
# want to stay out of the way, and one slider cannot serve both.
NUMERAL_SCALE = 1.0
LABEL_SCALE = 1.0


class Fonts:
    """Resolved family names. Built once, after Tk exists.

    `prefs` is cfg["theme"]["fonts"]. A name that is not installed is ignored
    rather than honoured, so a config copied from another machine degrades to
    the built-in candidate list instead of falling back to Tk's default.
    """

    def __init__(self, families: set[str], prefs: dict | None = None) -> None:
        prefs = prefs or {}

        def chosen(key: str, candidates: list[str], fallback: str) -> str:
            want = str(prefs.get(key) or "").strip()
            if want and want in families:
                return want
            return pick_font(families, candidates, fallback)

        self.numeral = chosen("numeral", NUMERAL_CANDIDATES, "Arial")
        self.label = chosen("label", LABEL_CANDIDATES, "Arial")
        self.mono = chosen("mono", MONO_CANDIDATES, "Courier New")


class Geometry:
    """Maps the 1920x1080 design space onto the real window.

    NON-16:9 PANELS
    ---------------
    A 1280x720 panel is exactly 16:9, the same as the design space, so the
    mapping is a clean uniform scale and `squash` should stay off.

    It matters when a panel is NOT 16:9 -- a 4:3 800x600 one fed a 16:9 signal,
    say. Such a scaler does one of two things, and which one is a property of
    the panel you cannot query from Windows:

      letterbox  the image is fitted inside the panel with black bars top and
                 bottom. Both axes shrink equally. Nothing distorts.

      stretch    the image is squeezed onto the full panel, so the axes shrink
                 by different factors and circles come out as eggs.

    `squash=True` pre-compensates for the stretch case: vertical geometry is
    scaled to 75% and drawn as a centered band, so the panel's own 1.333x
    vertical stretch cancels back to correct proportions. Font sizes shrink by
    the same 0.75, landing glyphs at the right physical height -- slightly
    condensed rather than slightly stretched, the better-looking of the two
    errors.

    Run `casebuddy.py --calibrate` if your panel is not 16:9.
    """

    def __init__(self, width: int, height: int, squash: bool = False,
                 text_scale: float = 1.0) -> None:
        self.width = width
        self.height = height
        self.squash = squash
        # Multiplies text and stroke weight only, NOT the layout grid. Scaling
        # the grid as well would simply push tiles off a screen the layout was
        # built to fill exactly; this instead changes how heavy the content
        # sits inside a fixed frame. Far enough above 1.0 and long strings will
        # start to collide, which is why the UI clamps it.
        self.text_scale = max(0.6, min(1.6, float(text_scale or 1.0)))

        factor = 0.75 if squash else 1.0
        self.kx = width / DESIGN_W
        self.ky = (height / DESIGN_H) * factor
        # Center the (possibly squashed) band vertically so the black bars are
        # symmetric.
        self.y0 = (height - DESIGN_H * self.ky) / 2.0
        # Fonts scale uniformly; using the smaller axis avoids overflowing a
        # window that is not exactly 16:9.
        self.kf = min(self.kx, height / DESIGN_H) * factor

    def x(self, value: float) -> float:
        return value * self.kx

    def y(self, value: float) -> float:
        return self.y0 + value * self.ky

    def w(self, value: float) -> float:
        return value * self.kx

    def h(self, value: float) -> float:
        return value * self.ky

    def font(self, size: float) -> int:
        # Negative sizes tell Tk the number is pixels, not points, which keeps
        # the layout independent of the display's DPI setting.
        return -max(8, int(round(size * self.kf * self.text_scale)))

    def stroke(self, value: float) -> int:
        return max(1, int(round(value * self.kf * self.text_scale)))
