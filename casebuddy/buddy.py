"""The buddy scene: one character whose mood tracks the machine.

TWO CHARACTERS, ONE SCENE
-------------------------
`character: "drawn"` builds the face from canvas primitives -- ovals, arcs and
smooth polylines whose control points move between moods, so the mouth bends
and the eyes narrow continuously rather than cutting between fixed pictures.

`character: "emoji"` puts a real colour emoji there instead: any glyph you
pick, per mood. That has to go through Pillow, because Tk draws text with GDI,
which has no colour-font support, and a glyph handed to the canvas as text
arrives as a flat black outline. See emoji.py.

Everything around the face -- sky, aura, weather, particles, cards, fan bars --
is identical either way.

MOOD
----
One scalar drives all of it. `stress` folds how hot the machine is together
with how hard it is working, normalised against the same warn and critical
thresholds the gauge screen uses, so amber over there means "sweating" over
here and the two screens can never disagree.

Heat outranks load on purpose, and by a configurable amount. A CPU pinned at
100% but sitting at 55 C is working, not suffering; multiplying the load term
by `load_weight` (0.78 by default) puts pure load at the top of the "cooking"
band and leaves "melting" for real temperature. Both terms, and which sensors
feed them, live in cfg["layout"]["buddy"]["stress"].

Both terms are kept on the Stress object rather than collapsed into one
number, so anything that wants to show the working -- a tile pointed at them,
a diagnostic, a future readout -- can, without recomputing anything.

Mood changes are damped twice. A band edge must be crossed by a margin before
it counts, and no mood may last less than a couple of seconds; without both, a
value resting exactly on a threshold makes the character strobe. The palette
then eases toward the new mood over about a third of a second rather than
snapping, which is what makes a load spike read as weather instead of a glitch.

WHY THE THEME STILL MATTERS HERE
--------------------------------
The mood owns the colours, which at first left the Theme tab with nothing to
act on -- picking a new accent changed a character screen barely at all. So the
mood's accent and glow are blended toward the theme accent by `theme_blend`,
and that blend fades out as stress rises. Idle, the screen is yours; melting,
it is red whatever you chose. Text takes its colours from the theme outright.

As everywhere else here, nothing on a periodic path calls delete(); see the
note at the top of dashboard.py for what that costs.
"""

from __future__ import annotations

import math
import os
import random
import time
import tkinter.font as tkfont
from dataclasses import dataclass, replace

import re

from . import emoji as emoji_mod
from . import rigs as rigs_mod
from . import scenes as scenes_mod
from . import seasonal as seasonal_mod
from . import theme
from .metrics import Reading, Snapshot, fmt_tile

MARGIN = 36.0
HEADER_Y = 56.0
RULE_Y = 102.0

# Stat cards down each side. Wide enough to reach from the margin almost to the
# aura, which is what stops the screen reading as a face with empty corners.
# CARD_TOP leaves a clear full-width band at y 110..170 for the outdoor line.
# The cards are drawn after it and are opaque, so at a higher position they
# simply ate both ends of it.
CARD_W, CARD_H, CARD_GAP = 520.0, 190.0, 14.0
CARD_X = (36.0, 1364.0)
CARD_TOP = 176.0
CARD_ROWS = 3
# Nothing may be dragged smaller than this: below it a tile cannot hold its own
# label and numeral, and would only ever read as a rendering fault. Both are
# multiples of the editor snap, or a tile dragged to the minimum would snap one
# step BELOW it, fail validation, and jump back to its grid position.
MIN_TILE_W, MIN_TILE_H = 152.0, 72.0

FACE_CX, FACE_CY, FACE_R = 960.0, 386.0, 205.0

CAPTION_Y = 618.0
QUIP_Y = 750.0

# The bottom strip: fan bars only, centred in what is left. The gauge screen
# lays its fan rows out the same way on purpose.
STRIP_RULE_Y = 840.0
FAN_CENTER = 955.0
FAN_PITCH = 78.0
FAN_BAR_X = (330.0, 1480.0)

# Right-hand columns on a bar row, as offsets back from its right edge. Fixed
# rather than one concatenated right-aligned string: rows carry different
# numbers of fields, and a row with a voltage in it pushed its watts out of
# line with the RPM above. Fixed columns line up whatever each row shows.
FAN_COL_VALUE = 0.0        # right edge of the reading
FAN_COL_PCT = 210.0        # right edge of the percent
FAN_COL_DETAIL = 330.0     # right edge of the secondary field
FAN_RESERVE = 520.0        # what the bar must leave free for all three

# The outdoor readout, and the arc the sun and moon climb. The arc is a narrow
# vertical rise in the one column of sky that neither a card (x > 1364) nor the
# aura (x < 1223) reaches.
WX_LINE_Y = 116.0
SUN_X = 1290.0
SUN_Y = (566.0, 206.0)      # horizon, then apex
SUN_R = 40.0
CLOUD_BAND = (140.0, 250.0)

F_CAPTION = 100
F_QUIP = 52
F_CARD_LABEL = 40
F_CARD_VALUE = 74
F_CARD_UNIT = 38
F_CARD_DETAIL = 42
F_METER_SMALL = 42
F_HEADER = 50
F_WX = 44
F_ZZZ = (44, 62, 84)

AURA_N = 7
RESCUE_N = 4       # emergency fans at MELTING, one per cooking component
SNOW_N = 14
DROP_N = 6
WISP_N = 6
FLAME_N = 7
ZZZ_N = 3
WX_N = 26
STAR_N = 26
CLOUD_N = 5

MOOD_DWELL_S = 2.5
MOOD_MARGIN = 0.035
FADE_TAU_S = 0.32
QUIP_EVERY_S = 9.0

# Alternate particle read of the same mood, cycled every 12-24 seconds so an
# hour parked in one band does not play one loop the whole time. Moods absent
# here keep their single look.
PARTICLE_ALT = {"busy": "none", "melting": "steam"}

TWILIGHT_TOP = "#c2451f"
TWILIGHT_BOT = "#3a0f10"

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


# --- colour ---------------------------------------------------------------

def _rgb(colour: str) -> tuple[int, int, int]:
    text = colour.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb)


def mix(a: str, b: str, t: float) -> str:
    """`t` of the way from colour a to colour b."""
    t = max(0.0, min(1.0, t))
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return _hex((ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t))


def shade(colour: str, amount: float) -> str:
    """Positive lightens toward white, negative darkens toward black."""
    return mix(colour, "#ffffff" if amount >= 0 else "#000000", abs(amount))


def luminance(colour: str) -> float:
    """Perceived brightness, 0 to 1. Rec. 709 weights."""
    red, green, blue = (v / 255.0 for v in _rgb(colour))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def tint(base: str, over: str, amount: float) -> str:
    """Colour `base` toward `over` while keeping how bright `base` was.

    The mood has to be able to turn the sky red without also turning it dark:
    a plain mix of a dark mood colour over a bright noon sky produced a grey
    afternoon, which defeats the whole point of a daylight cycle. So hue comes
    from the mood and brightness comes from the time of day -- which also means
    the same mood reads bright red at noon and deep red at midnight, both
    correct.
    """
    mixed = mix(base, over, amount)
    want, got = luminance(base), luminance(mixed)
    if got < 0.004:
        return mixed
    scale = want / got
    return _hex(tuple(min(255.0, v * scale) for v in _rgb(mixed)))


# Above this, a sky counts as light and everything painted straight onto it
# switches to dark ink. Cards keep light text, because they turn into dark
# panels at the same moment.
LIGHT_SKY = 0.40

INK_ON_LIGHT = {"text": "#12171f", "dim": "#333d4a", "faint": "#4d5866"}


def ink_for(sky_top: str, sky_bot: str) -> dict:
    """Text colours for whatever is painted directly on the sky.

    A daytime sky is genuinely light now, so the header, the outdoor line, the
    caption and the bottom rows cannot go on using the theme's pale text -- it
    simply disappears. This flips those, and only those: card and gauge text
    stays light because the cards themselves go dark over a light sky.
    """
    average = (luminance(sky_top) + luminance(sky_bot)) / 2.0
    if average > LIGHT_SKY:
        return dict(INK_ON_LIGHT, light=True)
    return {"text": theme.TEXT, "dim": theme.TEXT_DIM,
            "faint": theme.TEXT_FAINT, "light": False}


# --- moods ----------------------------------------------------------------

@dataclass(frozen=True)
class Mood:
    key: str
    caption: str
    quips: tuple[str, ...]
    emoji: str

    sky_top: str
    sky_bot: str
    glow: str
    body: str
    ink: str
    accent: str

    eye_w: float = 34.0
    eye_h: float = 44.0
    lids: bool = False
    dizzy: bool = False
    brow: float = 0.0
    mouth_curve: float = 0.7
    mouth_w: float = 68.0
    mouth_open: float = 0.0
    mouth_wave: float = 0.0
    tongue: bool = False
    shades: bool = False
    blush: float = 0.0

    bob_amp: float = 8.0
    bob_hz: float = 0.28
    jitter: float = 0.0
    particles: str = "none"     # none|snow|zzz|spark|sweat|steam|fire


MOODS: dict[str, Mood] = {
    "offline": Mood(
        key="offline", caption="NO SIGNAL", emoji="\U0001F636",
        quips=("waiting for sensors", "is LibreHardwareMonitor running?",
               "nothing to report"),
        sky_top="#14161c", sky_bot="#07080b", glow="#232733",
        body="#4a5163", ink="#171a21", accent="#8892a4",
        eye_h=10.0, brow=-0.2, mouth_curve=0.0, mouth_w=60.0,
        bob_amp=3.0, bob_hz=0.16, particles="none",
    ),
    "sleepy": Mood(
        key="sleepy", caption="NAPPING", emoji="\U0001F634",
        quips=("do not disturb", "back in a minute", "dreaming of benchmarks",
               "wake me if something happens"),
        sky_top="#17123a", sky_bot="#06040f", glow="#3a2a80",
        body="#d8b13f", ink="#2a1c05", accent="#a78bfa",
        lids=True, brow=0.5, mouth_curve=0.35, mouth_w=52.0,
        bob_amp=14.0, bob_hz=0.16, particles="zzz",
    ),
    "chill": Mood(
        key="chill", caption="CHILLING", emoji="\U0001F60E",
        quips=("this is nothing", "cool as ever", "wake me for a render",
               "barely trying", "room temperature"),
        sky_top="#0b2b3f", sky_bot="#04101a", glow="#1e6f8c",
        body="#f5d76e", ink="#2a1f05", accent="#38d6f0",
        shades=True, brow=0.4, mouth_curve=0.8, mouth_w=74.0,
        bob_amp=10.0, bob_hz=0.22, particles="snow",
    ),
    "busy": Mood(
        key="busy", caption="WORKING", emoji="\U0001F624",
        quips=("earning my keep", "on it", "actually busy", "give me a second"),
        sky_top="#2a1050", sky_bot="#0b0420", glow="#a21caf",
        body="#ffc233", ink="#2b1a04", accent="#f0abfc",
        eye_w=40.0, eye_h=24.0, brow=-0.35, mouth_curve=0.25, mouth_w=64.0,
        bob_amp=6.0, bob_hz=0.6, particles="spark",
    ),
    "sweaty": Mood(
        key="sweaty", caption="SWEATING", emoji="\U0001F613",
        quips=("getting warm", "could use a fan", "hold on", "this is fine"),
        sky_top="#4a2408", sky_bot="#150a02", glow="#d97706",
        body="#ffab2e", ink="#331a03", accent="#fbbf24",
        eye_w=44.0, eye_h=50.0, brow=-0.6, mouth_curve=-0.15, mouth_w=70.0,
        mouth_wave=1.0, blush=0.5,
        bob_amp=5.0, bob_hz=0.9, jitter=1.0, particles="sweat",
    ),
    "melting": Mood(
        key="melting", caption="MELTING", emoji="\U0001F92F",
        quips=("I regret everything", "throttling imminent", "do something",
               "was it something I ran?"),
        sky_top="#6b0a0a", sky_bot="#1c0202", glow="#ef4444",
        body="#f2452f", ink="#3d0805", accent="#ff5c47",
        dizzy=True, eye_w=48.0, eye_h=48.0, brow=-1.0,
        mouth_curve=-0.9, mouth_w=82.0, mouth_open=0.95, tongue=True, blush=1.0,
        bob_amp=3.0, bob_hz=2.0, jitter=4.5, particles="fire",
    ),
}

MOOD_ORDER = ("offline", "sleepy", "chill", "busy", "sweaty", "melting")

# The two retired moods live on as ALTERNATE FACES: every 12-24 seconds the
# variety clock flips a surviving mood between its own look and the retired
# one that merged into it -- CRUISING's wide grin inside CHILLING, COOKING's
# panting tongue inside SWEATING. Only the face and its particles swap; the
# caption, sky and palette stay the surviving mood's own.
ALT_FACES: dict[str, Mood] = {}


def _build_alt_faces() -> None:
    ALT_FACES["chill"] = replace(
        MOODS["chill"], emoji="\U0001F600", shades=False,
        eye_w=36.0, eye_h=46.0, brow=0.25, mouth_curve=0.95, mouth_w=80.0,
        mouth_open=0.0, mouth_wave=0.0, tongue=False, blush=0.0,
        bob_amp=9.0, bob_hz=0.34, jitter=0.0, particles="none")
    ALT_FACES["sweaty"] = replace(
        MOODS["sweaty"], emoji="\U0001F975",
        eye_w=46.0, eye_h=30.0, brow=-0.85, mouth_curve=-0.4, mouth_w=76.0,
        mouth_open=0.75, mouth_wave=0.0, tongue=True, blush=0.85,
        bob_amp=4.0, bob_hz=1.4, jitter=2.2, particles="steam")


_build_alt_faces()

# Upper bound of each band, crossed with hysteresis. "offline" and "sleepy"
# are not reached by stress alone, so they are deliberately absent. These are
# the DEFAULTS; cfg["layout"]["buddy"]["bands"] moves them per machine, from
# the "When each mood applies" group on the Character tab.
#
# Rebalanced against a real machine (idle 0.26, light work 0.45, medium 0.60,
# heavy 0.80, full load 1.0 once heat_bands are calibrated): the old edges put
# idle into "happy" and squeezed "sweaty" into an 0.60-0.75 sliver the readings
# jumped straight across. These give all six working moods a slice that
# actually gets visited.
DEFAULT_BANDS = {"chill": 0.42, "busy": 0.66, "sweaty": 0.86}
BANDS: tuple[tuple[float, str], ...] = (
    (DEFAULT_BANDS["chill"], "chill"),
    (DEFAULT_BANDS["busy"], "busy"),
    (DEFAULT_BANDS["sweaty"], "sweaty"),
    (9.99, "melting"),
)


def bands_from(opts: dict) -> tuple:
    """The mood ladder this config asks for, made safe to walk.

    Every edge is clamped into 0.05..0.98 and forced strictly ascending, so
    no hand-edited config can produce a band the readings step over backwards
    or a mood that swallows the whole scale. Melting is always the remainder.
    """
    table = {**DEFAULT_BANDS, **(opts.get("bands") or {})}

    def edge(key: str) -> float:
        try:
            return max(0.05, min(0.98, float(table.get(key))))
        except (TypeError, ValueError):
            return DEFAULT_BANDS[key]

    chill = edge("chill")
    busy = max(edge("busy"), chill + 0.02)
    sweaty = max(edge("sweaty"), busy + 0.02)
    return ((chill, "chill"), (busy, "busy"), (sweaty, "sweaty"),
            (9.99, "melting"))

_FADE_KEYS = ("sky_top", "sky_bot", "glow", "body", "ink", "accent")

SKIES: dict[tuple[str, bool], tuple[str, str]] = {
    ("clear", True): ("#12406e", "#061626"),
    ("clear", False): ("#0b1233", "#03060f"),
    ("partly", True): ("#17456a", "#07182a"),
    ("partly", False): ("#0d1734", "#040711"),
    ("cloudy", True): ("#2b3d4c", "#0c1218"),
    ("cloudy", False): ("#161e28", "#05070a"),
    ("overcast", True): ("#343e46", "#0f1216"),
    ("overcast", False): ("#191e23", "#06070a"),
    ("fog", True): ("#3b4248", "#14181b"),
    ("fog", False): ("#1c2125", "#070809"),
    ("drizzle", True): ("#254050", "#0a1015"),
    ("drizzle", False): ("#13212b", "#04080b"),
    ("rain", True): ("#1c3245", "#070d12"),
    ("rain", False): ("#101d29", "#03070a"),
    ("snow", True): ("#33465a", "#0d141c"),
    ("snow", False): ("#1a2532", "#05080c"),
    ("thunder", True): ("#2a2246", "#08060f"),
    ("thunder", False): ("#171238", "#04030b"),
}

# Daylight skies. The entries above are the DIM day reference and the night;
# these are what a real daytime sky looks like, and `day_brightness` says how
# far from one toward the other to go. Blending through the dim set rather than
# straight from night is what gives dusk its washed-out middle.
SKIES_BRIGHT: dict[str, tuple[str, str]] = {
    "clear": ("#4a9fd8", "#cfe4f2"),
    "partly": ("#5fa3cf", "#d3e2ea"),
    "cloudy": ("#8d9aa6", "#c4ccd2"),
    "overcast": ("#8b9298", "#bcc1c5"),
    "fog": ("#a9adb0", "#d0d2d3"),
    "drizzle": ("#74899a", "#b0bec7"),
    "rain": ("#61798a", "#a2b3bf"),
    "snow": ("#9dafbd", "#dbe3ea"),
    "thunder": ("#63607e", "#a29eb6"),
}

# condition -> (cloud count, precipitation count, celestial visibility 0..1)
WX_LOOK: dict[str, tuple[int, int, float]] = {
    "clear": (0, 0, 1.0),
    "partly": (2, 0, 0.85),
    "cloudy": (4, 0, 0.35),
    "overcast": (5, 0, 0.0),
    "fog": (5, 0, 0.0),
    "drizzle": (5, 12, 0.0),
    "rain": (5, WX_N, 0.0),
    "snow": (5, 20, 0.0),
    "thunder": (5, WX_N, 0.0),
}


def sky_key(condition: str, phase: str) -> str:
    """The config key for one sky: "rain-day", "clear-night" and so on."""
    return f"{condition}-{phase}"


def built_in_sky(condition: str, phase: str) -> tuple[str, str]:
    """What CaseBuddy ships for one condition and phase, before overrides."""
    if phase == "night":
        return SKIES.get((condition, False), SKIES[("cloudy", False)])
    if phase == "dim":
        return SKIES.get((condition, True), SKIES[("cloudy", True)])
    return SKIES_BRIGHT.get(condition, SKIES_BRIGHT["cloudy"])


def resolve_sky(condition: str, phase: str, overrides: dict | None) -> tuple[str, str]:
    """One sky, with any user override applied.

    An override is a two-colour list. Anything malformed falls through to the
    built-in rather than raising: these are hand-editable in config.json.
    """
    pair = (overrides or {}).get(sky_key(condition, phase))
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        top, bot = (str(v).strip() for v in pair)
        if _HEX.match(top) and _HEX.match(bot):
            return top, bot
    return built_in_sky(condition, phase)


def sky_for(weather, brightness: float = 1.0,
            overrides: dict | None = None) -> tuple[str, str] | None:
    """The sky this reading calls for, or None when there is no reading.

    Three looks fall out of one blend rather than being three cases:

        day       the bright palette, at full `brightness`
        evening   half-lit and washed warm, because daylight is passing 0.5
                  exactly while twilight is at its peak
        night     the dark palette

    `daylight` walks 0 to 1 across the half hour around sunrise, and `twilight`
    is a bump peaking at the horizon crossing. Neither is a switch, so the
    screen moves through the whole cycle by itself.

    `brightness` (weather.day_brightness) says how light noon is allowed to
    get. This panel lives inside a case, so 0 keeps the old all-dark behaviour
    without giving up the cycle.

    Everything is quantised to fiftieths: these move every frame otherwise, and
    the palette ease would repaint all 24 sky bands forever chasing a change
    far too small to see.
    """
    if weather is None or not getattr(weather, "ok", False):
        return None
    condition = str(getattr(weather, "condition", "cloudy"))
    dim = resolve_sky(condition, "dim", overrides)
    night = resolve_sky(condition, "night", overrides)
    bright = resolve_sky(condition, "day", overrides)

    lit = max(0.0, min(1.0, brightness))
    day = (mix(dim[0], bright[0], lit), mix(dim[1], bright[1], lit))

    daylight = round(float(getattr(weather, "daylight", 1.0)) / 0.02) * 0.02
    twilight = round(float(getattr(weather, "twilight", 0.0)) / 0.02) * 0.02
    top = mix(night[0], day[0], daylight)
    bot = mix(night[1], day[1], daylight)
    if twilight > 0.01:
        top = mix(top, TWILIGHT_TOP, twilight * 0.45)
        bot = mix(bot, TWILIGHT_BOT, twilight * 0.28)
    return top, bot


# --- stress ---------------------------------------------------------------

def _norm(value: float | None, floor: float, warn: float, crit: float) -> float | None:
    """Map a reading onto 0..1 against its own warn/critical thresholds.

    Anchored so `warn` always lands at 0.6 and `crit` at 1.0, whatever the
    units. That is what lets a temperature in C and a load in percent be
    compared with max() without either winning by an accident of scale.
    """
    if value is None:
        return None
    if value <= floor:
        return 0.0
    if value <= warn:
        return 0.6 * (value - floor) / max(1e-6, warn - floor)
    return min(1.0, 0.6 + 0.4 * (value - warn) / max(1e-6, crit - warn))


@dataclass(frozen=True)
class Stress:
    """The mood input, with the two terms it came from kept alongside."""
    value: float | None = None
    heat: float | None = None
    load: float | None = None
    hottest: float | None = None    # the raw degrees behind `heat`
    busiest: float | None = None    # the raw percent behind `load`
    driver: str = ""                # "HEAT" or "LOAD"


DEFAULT_STRESS = {
    "heat_sources": ["cpu_temp", "gpu_temp"],
    "load_sources": ["cpu_load", "gpu_load"],
    "heat_floor": 32.0,
    # Below 1.0 so a fully loaded but cool machine reads as working rather than
    # suffering: pure load tops out inside "cooking" and cannot reach "melting".
    "load_weight": 0.78,
    "nap_after_seconds": 90.0,
    # Per-sensor mood calibration, as {source: [floor, sweaty, melting]}.
    # A well-cooled machine never reaches the alert thresholds -- a CPU that
    # peaks at 75 C against a melting point of 85 C simply has no top moods --
    # so this pins the mood scale to what THIS machine actually does: 0 at
    # floor, 0.6 (the sweaty line) at the middle figure, 1.0 (melting) at the
    # last. Alert colours on the tiles keep using the real thresholds; this
    # only moves the character.
    "heat_bands": {},
}


def stress_of(vitals: dict, thresholds: dict, options: dict | None = None) -> Stress:
    """Combine temperature and load into one 0..1 reading, plus its parts."""
    options = {**DEFAULT_STRESS, **(options or {})}

    def pair(name: str, fallback: tuple[float, float]) -> tuple[float, float]:
        got = (thresholds or {}).get(name) or fallback
        try:
            return float(got[0]), float(got[1])
        except (TypeError, ValueError, IndexError, KeyError):
            return fallback

    cpu_w, cpu_c = pair("cpu_temp", (72.0, 85.0))
    gpu_w, gpu_c = pair("gpu_temp", (72.0, 83.0))
    load_w, load_c = pair("cpu_load", (80.0, 95.0))
    floor = float(options.get("heat_floor", 32.0))
    bands = options.get("heat_bands") or {}

    heats, degrees = [], []
    for name in options.get("heat_sources") or []:
        raw = vitals.get(name)
        # A calibrated band pins this sensor's whole mood scale to the
        # machine's own thermal window; without one, the alert thresholds
        # stand in.
        triple = bands.get(name)
        if isinstance(triple, (list, tuple)) and len(triple) == 3:
            try:
                lo, warn, crit = (float(v) for v in triple)
            except (TypeError, ValueError):
                lo, warn, crit = floor, *((gpu_w, gpu_c) if "gpu" in name
                                          else (cpu_w, cpu_c))
        else:
            lo = floor
            warn, crit = (gpu_w, gpu_c) if "gpu" in name else (cpu_w, cpu_c)
        scaled = _norm(raw, lo, warn, crit)
        if scaled is not None:
            heats.append(scaled)
            degrees.append(raw)

    loads, percents = [], []
    for name in options.get("load_sources") or []:
        raw = vitals.get(name)
        scaled = _norm(raw, 0.0, load_w, load_c)
        if scaled is not None:
            loads.append(scaled)
            percents.append(raw)

    if not heats and not loads:
        return Stress()

    heat = max(heats) if heats else 0.0
    load = max(loads) if loads else 0.0
    weighted = load * float(options.get("load_weight", 0.78))
    return Stress(
        value=max(heat, weighted), heat=heat, load=load,
        hottest=max(degrees) if degrees else None,
        busiest=max(percents) if percents else None,
        driver="HEAT" if heat >= weighted else "LOAD",
    )


# --- shape helpers --------------------------------------------------------

def _oval(cx: float, cy: float, w: float, h: float):
    return cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0


def _mouth_points(cx: float, cy: float, half_w: float, curve: float,
                  wave: float, phase: float) -> list[float]:
    """A parabola through 11 points; smooth=True rounds it into a lip."""
    pts: list[float] = []
    for i in range(11):
        u = (i / 10.0 - 0.5) * 2.0
        y = cy + curve * half_w * 0.55 * (1.0 - u * u)
        if wave:
            y += wave * 9.0 * math.sin(u * 3.4 + phase)
        pts.extend((cx + half_w * u, y))
    return pts


def _brow_points(cx: float, cy: float, half_w: float, tilt: float,
                 inner_sign: float) -> list[float]:
    """Three points. `tilt` +1 arches up and out, -1 drops the inner end."""
    lift = 12.0 * tilt
    return [
        cx - inner_sign * half_w, cy - lift,
        cx, cy - 8.0 - 6.0 * tilt,
        cx + inner_sign * half_w, cy + lift * 0.4,
    ]


def _lid_points(cx: float, cy: float, half_w: float) -> list[float]:
    """A closed eye: a shallow cup with its ends turned up."""
    return [
        cx - half_w, cy - 5.0,
        cx - half_w * 0.4, cy + 7.0,
        cx, cy + 9.0,
        cx + half_w * 0.4, cy + 7.0,
        cx + half_w, cy - 5.0,
    ]


def _drop_points(x: float, y: float, s: float) -> list[float]:
    return [
        x, y - s,
        x + 0.45 * s, y - 0.15 * s,
        x + 0.62 * s, y + 0.35 * s,
        x + 0.30 * s, y + 0.78 * s,
        x - 0.30 * s, y + 0.78 * s,
        x - 0.62 * s, y + 0.35 * s,
        x - 0.45 * s, y - 0.15 * s,
    ]


def _flame_points(x: float, y: float, w: float, h: float, wobble: float) -> list[float]:
    return [
        x - w / 2.0, y,
        x + w / 2.0, y,
        x + w * 0.26, y - h * 0.45 + wobble,
        x + w * 0.20, y - h * 0.70,
        x, y - h,
        x - w * 0.20, y - h * 0.70,
        x - w * 0.26, y - h * 0.45 - wobble,
    ]


def _cloud_points(x: float, y: float, w: float, h: float) -> list[float]:
    """A flat-bottomed lumpy blob, deliberately asymmetric so that five of them
    drifting at different speeds do not read as one stamp repeated."""
    return [
        x - w / 2, y + h / 2,
        x - w * 0.46, y + h * 0.04,
        x - w * 0.30, y - h * 0.34,
        x - w * 0.04, y - h * 0.50,
        x + w * 0.18, y - h * 0.28,
        x + w * 0.38, y - h * 0.40,
        x + w * 0.48, y + h * 0.02,
        x + w / 2, y + h / 2,
    ]


def _wisp_points(x: float, y: float, h: float, sway: float, phase: float) -> list[float]:
    pts: list[float] = []
    for i in range(6):
        t = i / 5.0
        pts.extend((x + math.sin(phase + t * 4.0) * sway * (0.3 + t), y - h * t))
    return pts


def tile_scale(rect, default_w: float, default_h: float) -> float:
    """How far to shrink a tile's type, given how far the tile itself shrank.

    Without this a tile dragged to a third of its size keeps full-size numerals
    and simply overflows its own box. Floored at 0.45: past that the label stops
    being readable on a 4.3" panel and hiding it would be more honest than
    shrinking it further.
    """
    return max(0.45, min(1.0, min(rect[2] / default_w, rect[3] / default_h)))


def card_origin(index: int, count: int) -> tuple[float, float]:
    """Top-left of stat card `index`, in design space.

    Fills left, right, then down, and centres the block vertically so a
    four-card layout does not sit high with a gap under it.
    """
    rows = max(1, min(CARD_ROWS, (max(1, count) + 1) // 2))
    top = CARD_TOP + (CARD_ROWS - rows) * (CARD_H + CARD_GAP) / 2.0
    row = min(index // 2, rows - 1)
    return CARD_X[index % 2], top + row * (CARD_H + CARD_GAP)


def resolve_image(path: str, base: str) -> str:
    """Absolute path for a stored image reference.

    Stored relative to the config file where possible, so a copied install or
    a moved folder keeps working; absolute paths are honoured as given.
    """
    path = str(path or "").strip()
    if not path:
        return ""
    if os.path.isabs(path) or not base:
        return path
    return os.path.normpath(os.path.join(base, path))


def clean_rect(value) -> tuple[float, float, float, float] | None:
    """A stored [x, y, w, h] if it is usable, else None.

    Anything malformed falls back to the built-in grid rather than throwing:
    a hand-edited config should never be able to blank the screen.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x, y, w, h = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    if w < MIN_TILE_W or h < MIN_TILE_H:
        return None
    return x, y, w, h


def card_rect(slot: dict, index: int, count: int) -> tuple:
    """Where stat card `index` goes: its own rect, or the built-in grid."""
    return clean_rect(slot.get("rect")) or (*card_origin(index, count),
                                            CARD_W, CARD_H)


def fan_rect(slot: dict, index: int, count: int) -> tuple:
    return clean_rect(slot.get("rect")) or (
        MARGIN, fan_row_y(index, count) - 32.0,
        theme.DESIGN_W - 2 * MARGIN, 64.0)


def fan_row_y(index: int, count: int) -> float:
    """Centre line of fan row `index`, in design space.

    Centred as a block rather than pinned to the top, so one, two or three
    rows all sit balanced in the strip instead of leaving a hole under them.
    """
    count = max(1, min(3, count))
    first = FAN_CENTER - (count - 1) * FAN_PITCH / 2.0
    return first + index * FAN_PITCH


# --- the scene ------------------------------------------------------------

class BuddyScene:
    """Same contract as Dashboard: build() once, then update(snap) per frame."""

    SKY_BANDS = 24

    def __init__(self, canvas, geo: theme.Geometry, cfg: dict) -> None:
        self.canvas = canvas
        self.geo = geo
        self.cfg = cfg
        self.items: dict = {}

        opts = dict(cfg.get("layout", {}).get("buddy", {}) or {})
        fps = float(opts.get("fps", 30) or 30)
        self.frame_ms = int(1000.0 / max(4.0, min(30.0, fps)))
        self.character = str(opts.get("character", "drawn")).lower()
        # "skin" was the old name, and it used "emoji" to mean the yellow face,
        # which became ambiguous once the character itself could be an emoji.
        self.tint = str(opts.get("tint") or
                        ("theme" if opts.get("skin") == "theme" else "classic")).lower()
        # How far the mood is pulled toward the theme accent while idle. Without
        # this the Theme tab had nothing to act on here, since the mood owns
        # every colour on the screen.
        self.theme_blend = max(0.0, min(1.0, float(opts.get("theme_blend", 0.40))))
        quips = opts.get("quips")
        self.show_quips = bool(quips if isinstance(quips, bool)
                               else opts.get("show_quips", False))
        self.show_caption = bool(opts.get("show_caption", True))
        self.stress_opts = dict(opts.get("stress") or {})
        self.bands = bands_from(opts)
        self.images = dict(opts.get("images") or {})
        self.image_base = str(cfg.get("_config_dir", "") or "")
        self.moods = apply_overrides(opts)
        self.emoji = emoji_mod.shared()
        if self.character == "emoji" and not self.emoji.available:
            print("[casebuddy] no colour emoji font found; drawing the face instead")
            self.character = "drawn"

        wx = cfg.get("weather") or {}
        self.wx_effects = bool(wx.get("effects", True))
        self.wx_line = bool(wx.get("show_line", True))
        self.wx_mood_tint = max(0.0, min(1.0, float(wx.get("mood_tint", 0.30))))
        self.day_brightness = max(0.0, min(1.0,
                                           float(wx.get("day_brightness", 1.0))))
        self.sky_overrides = dict(wx.get("skies") or {})
        self.night_dim = max(0.0, min(0.8, float(
            cfg.get("display", {}).get("night_dim", 0.0) or 0.0)))

        self.installed = set(tkfont.families(canvas))
        self.names = names = theme.Fonts(self.installed,
                                         cfg.get("theme", {}).get("fonts"))
        self._font_cache: dict = {}
        self.f_caption = self._font(names.numeral, F_CAPTION, "bold", "numeral")
        self.f_quip = self._font(names.label, F_QUIP, "normal")
        self.f_header = self._font(names.label, F_HEADER, "normal")
        self.f_wx = self._font(names.label, F_WX, "normal")
        self.f_label = self._font(names.label, F_CARD_LABEL, "bold")
        self.f_value = self._font(names.numeral, F_CARD_VALUE, "bold")
        self.f_unit = self._font(names.label, F_CARD_UNIT, "normal")
        self.f_detail = self._font(names.label, F_CARD_DETAIL, "normal")
        self.f_meter_small = self._font(names.label, F_METER_SMALL, "normal")
        self.f_zzz = [self._font(names.numeral, size, "bold") for size in F_ZZZ]

        self.mood = self.moods["chill"]
        # All of these are read before the first real frame, so they exist now.
        # _sky is the one sky_for() answer per frame: palette, particles and
        # weather all ask the same question, and it costs ~30 colour mixes.
        self._weather = None
        self._sky: tuple[str, str] | None = None
        self._stress = Stress()
        self._vitals: dict = {}
        self.ink = {"text": theme.TEXT, "dim": theme.TEXT_DIM,
                    "faint": theme.TEXT_FAINT, "light": False}
        self.card_ink = dict(self.ink)
        self.pal = self._targets()

        now = time.monotonic()
        self._mood_since = now
        self._calm_since = now
        self._blink_at = now + 3.0
        self._blink_until = 0.0
        # The variety clock: flips which take on the mood is playing.
        self._variant = 0
        self._variant_at = now + 15.0
        # Where the drawn face is looking, eased toward a wandering target.
        self._look = [0.0, 0.0]
        self._look_t = (0.0, 0.0)
        self._look_at = now + 2.0
        self._quip = self.mood.quips[0] if self.mood.quips else ""
        self._quip_at = 0.0
        self._t0 = now
        self._shown = ""
        self._wx_shown = -1
        self._flash_until = 0.0
        self._flash_at = now + 6.0
        self._rng = random.Random(20250818)
        self._stars = [(self._rng.uniform(60, 1860), self._rng.uniform(120, 620),
                        self._rng.uniform(2.5, 6.0), self._rng.uniform(0, math.tau))
                       for _ in range(STAR_N)]
        self._clouds = [[self._rng.uniform(-200, 2100),
                         self._rng.uniform(*CLOUD_BAND),
                         self._rng.uniform(9, 22),
                         self._rng.uniform(260, 460)]
                        for _ in range(CLOUD_N)]
        self._wx = [self._new_wx(seed=True) for _ in range(WX_N)]
        self._snow = [self._new_snow(seed=True) for _ in range(SNOW_N)]
        self._drops = [self._new_drop(i) for i in range(DROP_N)]
        self._zzz = [i / ZZZ_N for i in range(ZZZ_N)]

        # The backdrop and the character rig, if any. Last, because both
        # borrow the host's rng, fonts and palette, all of which have to exist
        # first. Built during build() at the right depth and ticked from
        # update(); "off" and a plain face cost nothing at all.
        self.scene = scenes_mod.make(opts.get("scene", "off"), self)
        # The character selector doubles as the rig name: "drawn", "emoji"
        # and "image" are faces handled here, anything else is looked up as a
        # rig, and a typo falls back to the drawn face rather than a blank.
        self.rig = rigs_mod.make(self.character, self)
        self._nudges: dict[int, tuple[float, float]] = {}
        # December snow and New Year fireworks. Dormant the other ~330 days.
        self.seasonal = (seasonal_mod.Seasonal(self)
                         if opts.get("seasonal", True) else None)

    def _role_scale(self, role: str) -> float:
        """Numerals and labels size independently; see theme.NUMERAL_SCALE.

        Taken from an explicit role rather than inferred from the family name,
        because a tile with its own font matches neither of the theme's two and
        would otherwise always be sized as a label.
        """
        return theme.NUMERAL_SCALE if role == "numeral" else theme.LABEL_SCALE

    def _font(self, family: str, size: float, weight: str,
              role: str = "label") -> tkfont.Font:
        return tkfont.Font(root=self.canvas, family=family,
                           size=self.geo.font(size * self._role_scale(role)),
                           weight=weight)

    def _slot_style(self, slot: dict) -> tuple:
        """(size multiplier, force bold, force italic) for ONE tile.

        Returns a plain tuple of three scalars on purpose. The version this
        replaced also returned font family NAMES, and unpacking it as
        `numeral, label, ...` shadowed the `label` parameter that carries the
        tile's TITLE -- so every tile on both screens drew the name of its font
        where its title should have been.
        """
        slot = slot or {}

        def number(key: str) -> float:
            try:
                return max(0.4, min(2.5, float(slot.get(key, 1.0))))
            except (TypeError, ValueError):
                return 1.0

        return number("font_size"), bool(slot.get("bold")), bool(slot.get("italic"))


    def _tile_font(self, family: str, size: float, weight: str, scale: float,
                   role: str = "label", slant: str = "roman"):
        """A font at `size * scale`, cached.

        Tk fonts are expensive to create and a rebuild would otherwise make one
        per tile per repaint, so they are keyed by their rounded pixel size --
        which also means tiles at similar scales share.
        """
        rounded = max(10, int(round(size * scale * self._role_scale(role))))
        key = (family, rounded, weight, slant)
        cached = self._font_cache.get(key)
        if cached is None:
            cached = tkfont.Font(root=self.canvas, family=family,
                                 size=self.geo.font(rounded), weight=weight,
                                 slant=slant)
            self._font_cache[key] = cached
        return cached


    # --- palette ----------------------------------------------------------

    def _dim(self) -> float:
        """How far to pull the whole scene toward black, 0..1.

        Tied to daylight rather than to the clock, so a panel inside a case
        goes quiet when the room does. Quantised, or the ease below would
        repaint every band on every frame of the whole evening.
        """
        if self.night_dim <= 0.0 or self._weather is None:
            return 0.0
        daylight = float(getattr(self._weather, "daylight", 1.0))
        return round(self.night_dim * (1.0 - daylight) / 0.05) * 0.05

    def _targets(self) -> dict:
        """The palette to head for: the mood, over the real sky, dimmed at night.

        With weather available the sky starts as the outdoor one and the mood is
        mixed over it, weighted by stress. Idle, that is mostly weather -- the
        panel shows what it is like outside. As the machine heats up the mood
        takes over and the sky goes amber, then red, whatever the forecast says.
        The alarm has to win; ambience is what it wins against.

        The theme accent is blended in by the same falling weight, which is what
        makes the Theme tab do something on this screen without letting a chosen
        colour mask a machine that is cooking.
        """
        want = {key: getattr(self.mood, key) for key in _FADE_KEYS}
        if self.tint == "theme":
            want["body"] = theme.OK
            want["ink"] = shade(theme.OK, -0.72)

        level = 0.0 if self._stress.value is None else self._stress.value
        if self.theme_blend > 0.0:
            pull = round(self.theme_blend * (1.0 - level) / 0.05) * 0.05
            want["accent"] = mix(want["accent"], theme.OK, pull)
            want["glow"] = mix(want["glow"], theme.OK, pull * 0.7)

        outdoor = self._sky
        if outdoor is not None:
            strength = round(min(0.92, self.wx_mood_tint + 0.55 * level)
                             / 0.05) * 0.05
            want["sky_top"] = tint(outdoor[0], want["sky_top"], strength)
            want["sky_bot"] = tint(outdoor[1], want["sky_bot"], strength)

        dim = self._dim()
        if dim:
            want = {key: shade(value, -dim) for key, value in want.items()}
        return want

    # --- build ------------------------------------------------------------

    def _slots(self, kind: str, prefix: str) -> list[tuple[str, str]]:
        rows = self.cfg.get("layout", {}).get(kind, []) or []
        return [(f"{prefix}{i}", str(s.get("label", ""))) for i, s in enumerate(rows)]

    def build(self) -> None:
        g, c = self.geo, self.canvas
        c.configure(bg=self.pal["sky_bot"], highlightthickness=0, bd=0)

        # Sky: horizontal bands recoloured in place. Tk has no gradient, and a
        # per-frame PhotoImage would cost far more than 24 itemconfigures.
        step = theme.DESIGN_H / self.SKY_BANDS
        for i in range(self.SKY_BANDS):
            self.items[f"sky{i}"] = c.create_rectangle(
                0, g.y(i * step) - 1, g.x(theme.DESIGN_W), g.y((i + 1) * step) + 1,
                fill=self.pal["sky_bot"], outline="")

        # Depth order: back scenery, stars, the sun behind the clouds, then
        # rain, front scenery, then the aura, the character, and whatever
        # clings to it. Cards and text come later still, so nothing a scene
        # draws can ever sit over a reading.
        if self.scene is not None and self.scene.LAYER == "back":
            self.scene.build()
        self._build_weather()
        if self.scene is not None and self.scene.LAYER == "front":
            self.scene.build()
        if self.seasonal is not None:
            self.seasonal.build()
        self._build_particles(("snow", "wisp", "zzz"))
        self._build_face()
        self._build_particles(("drop", "flame"))
        if self.rig is not None:
            self.rig.build()
        self._build_rescue()

        self.items["hw"] = c.create_text(
            g.x(MARGIN), g.y(HEADER_Y), anchor="w", fill=self.pal["accent"],
            font=self.f_header, text="")
        self.items["clock"] = c.create_text(
            g.x(theme.DESIGN_W - MARGIN), g.y(HEADER_Y), anchor="e",
            fill=self.pal["accent"], font=self.f_header, text="")
        self.items["rule"] = c.create_line(
            g.x(MARGIN), g.y(RULE_Y), g.x(theme.DESIGN_W - MARGIN), g.y(RULE_Y),
            fill=self.pal["glow"], width=g.stroke(5))
        # After the clouds, so they drift behind it rather than over it.
        self.items["wxline"] = c.create_text(
            g.x(FACE_CX), g.y(WX_LINE_Y), anchor="n", fill=self.pal["accent"],
            font=self.f_wx, text="", state="hidden")

        rows = self.cfg.get("layout", {}).get("stats", []) or []
        slots = self._slots("stats", "stat")
        for index, (key, label) in enumerate(slots):
            self._build_card(index, len(slots), key, label, rows[index])

        self.items["caption"] = c.create_text(
            g.x(FACE_CX), g.y(CAPTION_Y), anchor="n", fill=self.pal["accent"],
            font=self.f_caption, text=self.mood.caption)
        self.items["quip"] = c.create_text(
            g.x(FACE_CX), g.y(QUIP_Y), anchor="n", fill=self.pal["accent"],
            font=self.f_quip, text="")

        self.items["strip"] = c.create_line(
            g.x(MARGIN), g.y(STRIP_RULE_Y), g.x(theme.DESIGN_W - MARGIN),
            g.y(STRIP_RULE_Y), fill=self.pal["glow"], width=g.stroke(4))
        fan_rows = self.cfg.get("layout", {}).get("fans", []) or []
        fans = self._slots("fans", "fan")
        for index, (key, label) in enumerate(fans):
            self._build_fan(index, key, label, len(fans), fan_rows[index])

        # A roaming rig walks along the tops of the cards, so it has to draw
        # in front of them -- and they were created after it.
        if self.rig is not None and not self.rig.CENTERED:
            self.rig.raise_all()

        self._repaint_static()

    def _build_card(self, index: int, count: int, key: str, label: str,
                    slot: dict) -> None:
        g, c = self.geo, self.canvas
        x0, y0, w, h = card_rect(slot, index, count)
        # Everything inside is measured from the rect, type included, so a
        # dragged card keeps its proportions instead of spilling its numeral
        # out of a smaller box.
        scale = tile_scale((x0, y0, w, h), CARD_W, CARD_H)
        pad = min(32.0 * scale, w * 0.07)
        base = y0 + h - 18.0 * scale
        size, bold, italic = self._slot_style(slot)
        slant = "italic" if italic else "roman"
        heavy = (lambda natural: "bold" if bold else natural)
        f_label = self._tile_font(self.names.label, F_CARD_LABEL, heavy("bold"),
                                  scale * size, "label", slant)
        f_value = self._tile_font(self.names.numeral, F_CARD_VALUE, heavy("bold"),
                                  scale * size, "numeral", slant)
        f_unit = self._tile_font(self.names.label, F_CARD_UNIT, heavy("normal"),
                                 scale * size, "label", slant)
        f_detail = self._tile_font(self.names.label, F_CARD_DETAIL,
                                   heavy("normal"), scale * size, "label", slant)

        self.items[f"{key}.box"] = c.create_rectangle(
            g.x(x0), g.y(y0), g.x(x0 + w), g.y(y0 + h),
            fill=self.pal["sky_top"], outline=self.pal["glow"], width=g.stroke(3))
        self.items[f"{key}.tab"] = c.create_rectangle(
            g.x(x0), g.y(y0), g.x(x0 + 11 * scale), g.y(y0 + h),
            fill=self.pal["accent"], outline="")
        self.items[f"{key}.label"] = c.create_text(
            g.x(x0 + pad), g.y(y0 + 20 * scale), anchor="nw",
            fill=theme.TEXT_DIM, font=f_label, text=label)
        # Shares the title line, right-aligned. The gauge screen puts a bar's
        # second figure in the same place, so the two screens read alike.
        self.items[f"{key}.top"] = c.create_text(
            g.x(x0 + w - pad), g.y(y0 + 24 * scale), anchor="ne",
            fill=theme.TEXT_DIM, font=f_unit, text="")
        self.items[f"{key}.value"] = c.create_text(
            g.x(x0 + pad), g.y(base), anchor="sw", fill=theme.TEXT,
            font=f_value, text="--")
        self.items[f"{key}.unit"] = c.create_text(
            g.x(x0 + pad), g.y(base - 4 * scale), anchor="sw",
            fill=theme.TEXT_DIM, font=f_unit, text="")
        self.items[f"{key}.detail"] = c.create_text(
            g.x(x0 + w - pad), g.y(base - 2 * scale), anchor="se",
            fill=theme.TEXT_DIM, font=f_detail, text="")
        self.items[f"{key}.origin"] = (x0 + pad, base - 4 * scale)
        self.items[f"{key}.font"] = f_value

    def _build_fan(self, index: int, key: str, label: str, count: int,
                   slot: dict) -> None:
        g, c = self.geo, self.canvas
        x0, y0, w, h = fan_rect(slot, index, count)
        cy = y0 + h / 2.0
        scale = max(0.45, min(1.0, h / 64.0))
        half = min(theme.FAN_BAR_HEIGHT * scale, h * 0.55) / 2.0
        size, bold, italic = self._slot_style(slot)
        f_row = self._tile_font(self.names.label, F_METER_SMALL,
                                "bold" if bold else "normal", scale * size,
                                "label", "italic" if italic else "roman")
        # The label needs room on the left and three columns on the right; the
        # bar takes what is between them, whatever the tile is dragged to.
        bar0 = x0 + min(294.0 * scale, w * 0.17)
        bar1 = x0 + w - min(FAN_RESERVE * scale, w * 0.36)

        self.items[f"{key}.label"] = c.create_text(
            g.x(x0), g.y(cy), anchor="w", fill=theme.TEXT_DIM,
            font=f_row, text=label)
        self.items[f"{key}.track"] = c.create_rectangle(
            g.x(bar0), g.y(cy - half), g.x(bar1), g.y(cy + half),
            fill=self.pal["sky_top"], outline="")
        self.items[f"{key}.fill"] = c.create_rectangle(
            g.x(bar0), g.y(cy - half), g.x(bar0), g.y(cy + half),
            fill=self.pal["accent"], outline="", state="hidden")
        for name, offset in (("value", FAN_COL_VALUE), ("pct", FAN_COL_PCT),
                             ("detail", FAN_COL_DETAIL)):
            self.items[f"{key}.{name}"] = c.create_text(
                g.x(x0 + w - offset * scale), g.y(cy), anchor="e",
                fill=theme.TEXT, font=f_row, text="")
        self.items[f"{key}.cy"] = cy
        self.items[f"{key}.bar"] = (bar0, bar1, half)
        self.items[f"{key}.percent"] = bool(slot.get("percent"))

    def _build_face(self) -> None:
        g, c = self.geo, self.canvas
        cx, cy, r = FACE_CX, FACE_CY, FACE_R

        for i in range(AURA_N):
            scale = 1.34 - i * (0.30 / (AURA_N - 1))
            self.items[f"aura{i}"] = c.create_oval(
                *self._box(_oval(cx, cy, r * 2 * scale, r * 2 * scale)),
                fill=self.pal["glow"], outline="")

        # The emoji face is one image item; the drawn face is thirty-odd
        # primitives. Both are always created and only one is ever shown, so
        # switching character never has to rebuild the scene.
        self.items["glyph"] = c.create_image(
            g.x(cx), g.y(cy), anchor="center", state="hidden")

        self.items["body"] = c.create_oval(
            *self._box(_oval(cx, cy, r * 2, r * 2)),
            fill=self.pal["body"], outline=shade(self.pal["body"], -0.35),
            width=g.stroke(5))
        # Kept up on the rim. Lower down it sat across the left brow and read
        # as a blemish rather than a highlight.
        self.items["gloss"] = c.create_oval(
            *self._box(_oval(cx - r * 0.42, cy - r * 0.66, r * 0.48, r * 0.22)),
            fill=shade(self.pal["body"], 0.32), outline="")

        for side, sign in (("l", -1.0), ("r", 1.0)):
            ex, ey = cx + sign * r * 0.36, cy - r * 0.20
            self.items[f"blush_{side}"] = c.create_oval(
                *self._box(_oval(cx + sign * r * 0.62, cy + r * 0.12, 74, 40)),
                fill=self.pal["body"], outline="", state="hidden")
            self.items[f"eye_{side}"] = c.create_oval(
                *self._box(_oval(ex, ey, 34, 44)), fill=self.pal["ink"], outline="")
            self.items[f"shine_{side}"] = c.create_oval(
                *self._box(_oval(ex + 8, ey - 10, 13, 13)), fill="#ffffff", outline="")
            self.items[f"lid_{side}"] = c.create_line(
                *self._pts(_lid_points(ex, ey, 30)), fill=self.pal["ink"],
                width=g.stroke(9), smooth=True, capstyle="round", state="hidden")
            self.items[f"spiral_{side}"] = c.create_arc(
                *self._box(_oval(ex, ey, 56, 56)), start=90, extent=300,
                style="arc", outline=self.pal["ink"], width=g.stroke(7),
                state="hidden")
            self.items[f"spiral2_{side}"] = c.create_arc(
                *self._box(_oval(ex, ey, 28, 28)), start=270, extent=300,
                style="arc", outline=self.pal["ink"], width=g.stroke(7),
                state="hidden")
            self.items[f"brow_{side}"] = c.create_line(
                *self._pts(_brow_points(ex, cy - r * 0.46, 42, 0.0, sign)),
                fill=self.pal["ink"], width=g.stroke(11), smooth=True,
                capstyle="round")

        my = cy + r * 0.30
        self.items["mouth_open"] = c.create_oval(
            *self._box(_oval(cx, my, 90, 60)), fill=shade(self.pal["ink"], 0.08),
            outline=shade(self.pal["ink"], -0.3), width=g.stroke(4), state="hidden")
        self.items["tongue"] = c.create_oval(
            *self._box(_oval(cx, my + 18, 62, 34)), fill="#e0526b", outline="",
            state="hidden")
        self.items["mouth"] = c.create_line(
            *self._pts(_mouth_points(cx, my, 68, 0.8, 0.0, 0.0)),
            fill=self.pal["ink"], width=g.stroke(12), smooth=True, capstyle="round")

        # Last, so the lenses sit over the eyes they cover.
        for name in ("shades_l", "shades_r"):
            self.items[name] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill="#141821", outline="#394052",
                width=g.stroke(4), smooth=True, state="hidden")
        self.items["shades_bridge"] = c.create_line(
            0, 0, 1, 1, fill="#141821", width=g.stroke(10), state="hidden")

        self._drawn_parts = (
            ["body", "gloss", "mouth", "mouth_open", "tongue",
             "shades_l", "shades_r", "shades_bridge"]
            + [f"{name}_{side}" for side in ("l", "r")
               for name in ("blush", "eye", "shine", "lid", "spiral",
                            "spiral2", "brow")])

    def _build_particles(self, families: tuple) -> None:
        g, c = self.geo, self.canvas
        if "snow" in families:
            for i in range(SNOW_N):
                self.items[f"snow{i}"] = c.create_oval(
                    0, 0, 1, 1, fill="#ffffff", outline="", state="hidden")
        if "drop" in families:
            for i in range(DROP_N):
                self.items[f"drop{i}"] = c.create_polygon(
                    *self._pts(_drop_points(0, 0, 10)), fill="#7dd3fc", outline="",
                    smooth=True, state="hidden")
        if "wisp" in families:
            for i in range(WISP_N):
                self.items[f"wisp{i}"] = c.create_line(
                    *self._pts(_wisp_points(0, 0, 10, 4, 0)), fill="#ffffff",
                    width=g.stroke(10), smooth=True, capstyle="round",
                    state="hidden")
        if "flame" in families:
            for i in range(FLAME_N):
                self.items[f"flame{i}"] = c.create_polygon(
                    *self._pts(_flame_points(0, 0, 10, 10, 0)), fill="#f97316",
                    outline="", smooth=True, state="hidden")
        if "zzz" in families:
            for i in range(ZZZ_N):
                self.items[f"zzz{i}"] = c.create_text(
                    0, 0, text="Z", anchor="center", fill="#ffffff",
                    font=self.f_zzz[i], state="hidden")

    def _build_weather(self) -> None:
        g, c = self.geo, self.canvas
        for i, (x, y, size, _phase) in enumerate(self._stars):
            self.items[f"star{i}"] = c.create_oval(
                *self._box(_oval(x, y, size, size)), fill="#ffffff", outline="",
                state="hidden")
        self.items["sun.glow"] = c.create_oval(
            0, 0, 1, 1, fill="#ffd76a", outline="", state="hidden")
        self.items["sun.disc"] = c.create_oval(
            0, 0, 1, 1, fill="#ffd76a", outline="", state="hidden")
        for i in range(CLOUD_N):
            self.items[f"cloud{i}"] = c.create_polygon(
                *self._pts(_cloud_points(0, 0, 100, 40)), fill="#ffffff",
                outline="", smooth=True, state="hidden")
        for i in range(WX_N):
            self.items[f"wx{i}"] = c.create_line(
                0, 0, 1, 1, fill="#9ec9ff", width=g.stroke(4), capstyle="round",
                state="hidden")

    # --- the emergency fans -------------------------------------------------

    def _build_rescue(self) -> None:
        """MELTING's rescue crew: a little desk fan per cooking component,
        wheeled in and pointed straight at the offending tile. Faces and the
        centred rigs only -- the spider, the cat and the ship already have
        their own answer to a machine on fire."""
        c, g = self.canvas, self.geo
        for i in range(RESCUE_N):
            self.items[f"resq{i}.pole"] = c.create_line(
                0, 0, 1, 1, fill="#6e7787", width=g.stroke(7),
                capstyle="round", state="hidden")
            self.items[f"resq{i}.base"] = c.create_oval(
                0, 0, 1, 1, fill="#4a5261", outline="", state="hidden")
            for k in range(3):
                self.items[f"resq{i}.b{k}"] = c.create_polygon(
                    0, 0, 1, 1, 2, 2, fill="#cfd8e6", smooth=True,
                    outline="", state="hidden")
            self.items[f"resq{i}.ring"] = c.create_oval(
                0, 0, 1, 1, fill="", outline="#8b95a6",
                width=g.stroke(5), state="hidden")
            self.items[f"resq{i}.hub"] = c.create_oval(
                0, 0, 1, 1, fill="#4a5261", outline="", state="hidden")
            for k in range(2):
                self.items[f"resq{i}.a{k}"] = c.create_line(
                    0, 0, 1, 1, fill="#bfe9f5", width=g.stroke(4),
                    capstyle="round", state="hidden")

    def _hot_tiles(self) -> list[tuple]:
        """Stat cards showing a temperature that is genuinely cooking, judged
        by the same calibrated heat scale the mood uses."""
        opts = {**DEFAULT_STRESS, **self.stress_opts}
        bands = opts.get("heat_bands") or {}
        thresholds = self.cfg.get("thresholds", {})
        out = []
        rows = self.cfg.get("layout", {}).get("stats", []) or []
        for index, slot in enumerate(rows):
            short = str(slot.get("metric", "")).split(":", 1)[-1]
            if "temp" not in short and short != "gpu_hotspot":
                continue
            raw = self._vitals.get(short)
            if raw is None:
                continue
            triple = bands.get(short)
            if isinstance(triple, (list, tuple)) and len(triple) == 3:
                lo, warn, crit = (float(v) for v in triple)
            else:
                lo = float(opts.get("heat_floor", 32.0))
                pair = thresholds.get(
                    "gpu_temp" if "gpu" in short else "cpu_temp") or (72, 85)
                warn, crit = float(pair[0]), float(pair[1])
            scaled = _norm(raw, lo, warn, crit)
            if scaled is not None and scaled >= 0.75:
                out.append((scaled, card_rect(slot, index, len(rows))))
        # Hottest first, so when there are more cooking parts than fans the
        # fans go where they are needed most.
        out.sort(reverse=True)
        return [rect for _scaled, rect in out[:RESCUE_N]]

    def _draw_rescue(self, clock: float) -> None:
        c = self.canvas
        active = (self.mood.key == "melting"
                  and (self.rig is None or self.rig.CENTERED))
        hot = self._hot_tiles() if active else []
        for i in range(RESCUE_N):
            parts = ("pole", "base", "ring", "hub", "b0", "b1", "b2",
                     "a0", "a1")
            if i >= len(hot):
                for part in parts:
                    c.itemconfigure(self.items[f"resq{i}.{part}"],
                                    state="hidden")
                continue
            x, y, w, h = hot[i]
            # Inboard of the card, blowing outward at it.
            toward = -1.0 if x + w / 2.0 < 960.0 else 1.0
            fx = (x + w + 64.0) if toward < 0 else (x - 64.0)
            fy = y + h * 0.5
            for part in parts:
                c.itemconfigure(self.items[f"resq{i}.{part}"], state="normal")
            c.coords(self.items[f"resq{i}.pole"],
                     *self._pts([fx, fy + 64.0, fx, fy + 24.0]))
            c.coords(self.items[f"resq{i}.base"],
                     *self._box(_oval(fx, fy + 66.0, 64.0, 16.0)))
            spin = clock * 16.0 + i * 1.3
            for k in range(3):
                a = spin + k * (math.tau / 3.0)
                tipx = fx + math.cos(a) * 26.0
                tipy = fy + math.sin(a) * 26.0
                px_, py_ = -math.sin(a), math.cos(a)
                c.coords(self.items[f"resq{i}.b{k}"], *self._pts([
                    fx + px_ * 5.0, fy + py_ * 5.0,
                    tipx + px_ * 10.0, tipy + py_ * 10.0,
                    tipx - px_ * 10.0, tipy - py_ * 10.0,
                    fx - px_ * 5.0, fy - py_ * 5.0]))
            c.coords(self.items[f"resq{i}.ring"],
                     *self._box(_oval(fx, fy, 68.0, 68.0)))
            c.coords(self.items[f"resq{i}.hub"],
                     *self._box(_oval(fx, fy, 14.0, 14.0)))
            # The draught: short dashes streaming at the tile.
            for k in range(2):
                prog = (clock * 220.0 + i * 40.0 + k * 34.0) % 72.0
                ax = fx + toward * (40.0 + prog)
                ay = fy - 10.0 + k * 20.0
                c.coords(self.items[f"resq{i}.a{k}"],
                         *self._pts([ax, ay, ax + toward * 18.0, ay]))
                c.itemconfigure(self.items[f"resq{i}.a{k}"],
                                fill=mix(self.pal["sky_top"], "#bfe9f5",
                                         1.0 - prog / 90.0))

    # --- coordinate sugar -------------------------------------------------

    def _box(self, box) -> list[float]:
        g = self.geo
        x0, y0, x1, y1 = box
        return [g.x(x0), g.y(y0), g.x(x1), g.y(y1)]

    def _pts(self, flat) -> list[float]:
        g = self.geo
        return [g.x(v) if i % 2 == 0 else g.y(v) for i, v in enumerate(flat)]

    # --- particle bookkeeping ---------------------------------------------

    def _new_snow(self, seed: bool = False) -> list[float]:
        r = self._rng
        return [r.uniform(470, 1450), r.uniform(110, 700) if seed else 110.0,
                r.uniform(24, 62), r.uniform(0, math.tau), r.uniform(5, 11)]

    def _new_drop(self, index: int) -> list[float]:
        r = self._rng
        side = -1.0 if index % 2 == 0 else 1.0
        return [FACE_CX + side * FACE_R * r.uniform(0.72, 0.94),
                FACE_CY - FACE_R * r.uniform(0.10, 0.45),
                r.uniform(150, 260), r.uniform(0, 1.2), r.uniform(13, 20)]

    def _new_wx(self, seed: bool = False) -> list[float]:
        r = self._rng
        return [r.uniform(-40, 1960), r.uniform(110, 1070) if seed else 100.0,
                r.uniform(0, 1), r.uniform(0, math.tau), r.uniform(0.6, 1.0)]

    # --- per-frame --------------------------------------------------------

    def update(self, snap: Snapshot, history: dict | None = None) -> None:
        now = time.monotonic()
        clock = now - self._t0

        self._weather = getattr(snap, "weather", None)
        self._sky = sky_for(self._weather, self.day_brightness,
                            self.sky_overrides)
        self._vitals = getattr(snap, "vitals", None) or {}
        self._stress = stress_of(self._vitals, self.cfg.get("thresholds", {}),
                                 self.stress_opts)

        mood = self._pick_mood(now)
        if now >= self._variant_at:
            self._variant ^= 1
            self._variant_at = now + self._rng.uniform(12.0, 24.0)
        face = self._face_mood(mood)
        self._ease_palette()
        ctx = self._context(clock, now)
        self._draw_weather(clock, now)
        if self.scene is not None:
            self.scene.update(ctx)
        if self.seasonal is not None:
            self.seasonal.update(ctx)
        self._draw_header(snap)
        self._draw_cards(snap)
        self._draw_face(face, clock, now, ctx)
        self._draw_particles(face, clock)
        self._draw_rescue(clock)
        self._draw_caption(mood, now)
        self._draw_fans(snap)

    # --- what the rigs hold on to ------------------------------------------

    TILE_PARTS = ("box", "tab", "label", "top", "value", "unit", "detail")

    def tile_rects(self) -> list[tuple]:
        """Every stat card's (x, y, w, h, metric), in design space.

        The cat walks along these and the spider webs onto them, so they are
        the rigs' whole map of the world. The metric rides along so a rig can
        aim at "the hottest one" by name.
        """
        rows = self.cfg.get("layout", {}).get("stats", []) or []
        return [(*card_rect(slot, index, len(rows)),
                 str(slot.get("metric", "")))
                for index, slot in enumerate(rows)]

    def nudge_tile(self, index: int, dx: float, dy: float) -> None:
        """Push stat card `index` off its home by a design-space offset.

        Absolute, not cumulative: passing (0, 0) puts the card back exactly,
        whatever sequence of shakes came before. That is what makes it safe to
        call from a rig every frame without drift.
        """
        current = self._nudges.get(index, (0.0, 0.0))
        ddx, ddy = dx - current[0], dy - current[1]
        if not ddx and not ddy:
            return
        self._nudges[index] = (dx, dy)
        for part in self.TILE_PARTS:
            item = self.items.get(f"stat{index}.{part}")
            if isinstance(item, int):
                self.canvas.move(item, ddx * self.geo.kx, ddy * self.geo.ky)

    def _context(self, clock: float, now: float) -> scenes_mod.Context:
        """This frame, packaged for whichever scene or rig wants it."""
        return scenes_mod.Context(
            clock=clock, dt=self.frame_ms / 1000.0, now=now,
            stress=self._stress, vitals=self._vitals, weather=self._weather,
            mood=self.mood, pal=self.pal, ink=self.ink)

    def _face_mood(self, mood: Mood) -> Mood:
        """The face to wear this variant: the mood's own, or the retired
        mood that merged into it. User face/caption overrides win: a mood
        whose emoji the user changed keeps that choice on both variants."""
        if not self._variant:
            return mood
        alt = ALT_FACES.get(mood.key)
        if alt is None:
            return mood
        if self.moods[mood.key].emoji != MOODS[mood.key].emoji:
            return replace(alt, emoji=self.moods[mood.key].emoji)
        return alt

    def _pick_mood(self, now: float) -> Mood:
        stress = self._stress.value
        nap_after = float(self.stress_opts.get(
            "nap_after_seconds", DEFAULT_STRESS["nap_after_seconds"]))
        if stress is None:
            want = "offline"
        else:
            load = self._stress.load or 0.0
            if load > 0.06 or stress > 0.30:
                self._calm_since = now
            if nap_after > 0 and now - self._calm_since >= nap_after and stress < 0.28:
                want = "sleepy"
            else:
                order = [name for _hi, name in self.bands]
                if self.mood.key in order:
                    index = order.index(self.mood.key)
                    lo = 0.0 if index == 0 else self.bands[index - 1][0]
                    hi = self.bands[index][0]
                    # Still inside the current band, allowing for the margin.
                    if lo - MOOD_MARGIN <= stress <= hi + MOOD_MARGIN:
                        return self.mood
                want = next(name for hi, name in self.bands if stress <= hi)

        if want != self.mood.key and now - self._mood_since >= MOOD_DWELL_S:
            self.mood = self.moods[want]
            self._mood_since = now
            self._quip_at = 0.0
        return self.mood

    def _ease_palette(self) -> None:
        want = self._targets()
        if all(self.pal[key] == value for key, value in want.items()):
            return
        step = 1.0 - math.exp(-(self.frame_ms / 1000.0) / FADE_TAU_S)
        for key, target in want.items():
            if self.pal[key] == target:
                continue
            nxt = mix(self.pal[key], target, step)
            # Colour space is 8-bit, so an exponential ease never quite lands.
            # Snap once the remaining error is smaller than a step.
            if max(abs(a - b) for a, b in zip(_rgb(nxt), _rgb(target))) <= 2:
                nxt = target
            self.pal[key] = nxt
        self._repaint_static()

    def _repaint_static(self) -> None:
        """Everything whose colour follows the mood or the theme, not the frame."""
        c, pal = self.canvas, self.pal
        top, bot = pal["sky_top"], pal["sky_bot"]
        ink = ink_for(top, bot)
        self.ink = ink
        # UI chrome -- caption, tabs, fan fills -- wears the THEME accent, so
        # the palette on the Look tab actually paints this screen. The mood
        # keeps what is alive: sky, face, aura, particles. Before this, a red
        # "web" palette produced a stubbornly cyan panel because chill's own
        # accent owned every bar.
        on_sky_accent = (mix(theme.OK, "#000000", 0.45) if ink["light"]
                         else theme.OK)
        for i in range(self.SKY_BANDS):
            c.itemconfigure(self.items[f"sky{i}"],
                            fill=mix(top, bot, i / (self.SKY_BANDS - 1.0)))
        c.configure(bg=bot)

        for i in range(AURA_N):
            c.itemconfigure(self.items[f"aura{i}"],
                            fill=mix(top, pal["glow"], 0.04 + i * 0.05))

        c.itemconfigure(self.items["body"], fill=pal["body"],
                        outline=shade(pal["body"], -0.35))
        c.itemconfigure(self.items["gloss"], fill=shade(pal["body"], 0.32))
        for side in ("l", "r"):
            c.itemconfigure(self.items[f"eye_{side}"], fill=pal["ink"])
            c.itemconfigure(self.items[f"lid_{side}"], fill=pal["ink"])
            c.itemconfigure(self.items[f"brow_{side}"], fill=pal["ink"])
            c.itemconfigure(self.items[f"spiral_{side}"], outline=pal["ink"])
            c.itemconfigure(self.items[f"spiral2_{side}"], outline=pal["ink"])
            c.itemconfigure(self.items[f"blush_{side}"],
                            fill=mix(pal["body"], "#e0526b", 0.55))
        c.itemconfigure(self.items["mouth"], fill=pal["ink"])
        c.itemconfigure(self.items["mouth_open"], fill=shade(pal["ink"], 0.08),
                        outline=shade(pal["ink"], -0.3))

        # A card is a PANEL, not part of the sky, so it takes the palette
        # outright. Background, Rules and Gauge track on the Look tab used to be
        # dead on this screen because everything here was derived from the mood
        # instead. Card text then flips on the luminance of whatever background
        # was chosen, so picking a light one stays readable.
        card_bg = theme.BG
        card_edge = theme.PANEL_EDGE
        self.card_ink = card_ink = ink_for(card_bg, card_bg)
        for key, _label in self._slots("stats", "stat"):
            c.itemconfigure(self.items[f"{key}.box"], fill=card_bg, outline=card_edge)
            c.itemconfigure(self.items[f"{key}.label"], fill=card_ink["dim"])
            c.itemconfigure(self.items[f"{key}.top"], fill=card_ink["faint"])
        for key, _label in self._slots("fans", "fan"):
            c.itemconfigure(self.items[f"{key}.track"], fill=theme.TRACK)
            c.itemconfigure(self.items[f"{key}.fill"], fill=theme.OK)
            c.itemconfigure(self.items[f"{key}.value"], fill=ink["text"])
            c.itemconfigure(self.items[f"{key}.pct"], fill=ink["dim"])
            c.itemconfigure(self.items[f"{key}.detail"], fill=ink["dim"])
            c.itemconfigure(self.items[f"{key}.label"], fill=ink["dim"])

        if self.scene is not None:
            self.scene.recolor(pal, ink)

        c.itemconfigure(self.items["caption"], fill=on_sky_accent)
        c.itemconfigure(self.items["quip"], fill=mix(on_sky_accent, top, 0.35))
        for name in ("rule", "strip"):
            c.itemconfigure(self.items[name], fill=theme.PANEL_EDGE)
        for name in ("hw", "clock"):
            c.itemconfigure(self.items[name], fill=ink["faint"])

    def _draw_header(self, snap: Snapshot) -> None:
        c = self.canvas
        notice = snap.notices[0] if snap.notices else ""
        c.itemconfigure(self.items["hw"],
                        text=(notice or snap.get("hdr0").detail).upper(),
                        fill=theme.WARN if notice
                        else self.ink.get("faint", theme.TEXT_FAINT))
        c.itemconfigure(self.items["clock"], text=snap.get("hdr1").detail.upper())

    def _draw_cards(self, snap: Snapshot) -> None:
        g, c = self.geo, self.canvas
        ink = getattr(self, "card_ink", None) or {
            "text": theme.TEXT, "dim": theme.TEXT_DIM, "faint": theme.TEXT_FAINT}
        for key, _label in self._slots("stats", "stat"):
            reading: Reading = snap.get(key)
            text = fmt_tile(reading)
            state = theme.STATE_COLOR.get(reading.state, theme.NA)
            alarm = reading.state in ("warn", "crit")
            anchor_x, anchor_y = self.items[f"{key}.origin"]

            c.itemconfigure(self.items[f"{key}.value"], text=text,
                            fill=(state if alarm else ink["text"])
                            if reading.available else theme.NA)
            # The unit trails a numeral whose width changes, so its position
            # comes from a live measurement rather than a fixed offset.
            c.coords(self.items[f"{key}.unit"],
                     g.x(anchor_x) + self.items[f"{key}.font"].measure(text)
                     + g.w(12), g.y(anchor_y))
            c.itemconfigure(self.items[f"{key}.unit"], fill=ink["dim"],
                            text=reading.unit if reading.available else "")
            c.itemconfigure(self.items[f"{key}.detail"], fill=ink["dim"],
                            text=reading.detail)
            c.itemconfigure(self.items[f"{key}.top"], text=reading.top)
            c.itemconfigure(self.items[f"{key}.tab"],
                            fill=state if alarm else theme.OK)

    def _draw_fans(self, snap: Snapshot) -> None:
        g, c = self.geo, self.canvas
        for key, _label in self._slots("fans", "fan"):
            reading = snap.get(key)
            cy = self.items[f"{key}.cy"]
            value = (f"{reading.value:.0f} {reading.unit or 'RPM'}"
                     if reading.available else "--")
            # How full the bar is, as a number. A fan row gets its percent from
            # a real duty-cycle sensor, but a row showing watts has no such
            # thing, and this is the only way to read its own scale.
            pct = ""
            if self.items[f"{key}.percent"] and reading.fraction is not None:
                pct = f"{reading.fraction * 100:.0f}%"
            c.itemconfigure(self.items[f"{key}.value"], text=value)
            c.itemconfigure(self.items[f"{key}.pct"], text=pct)
            c.itemconfigure(self.items[f"{key}.detail"], text=reading.detail)
            bar0, bar1, half = self.items[f"{key}.bar"]
            fill = self.items[f"{key}.fill"]
            if reading.fraction:
                x1 = bar0 + (bar1 - bar0) * reading.fraction
                c.coords(fill, g.x(bar0), g.y(cy - half),
                         g.x(x1), g.y(cy + half))
                c.itemconfigure(fill, state="normal")
            else:
                c.itemconfigure(fill, state="hidden")

    def _draw_face(self, mood: Mood, clock: float, now: float,
                   ctx=None) -> None:
        c, r = self.canvas, FACE_R

        bob = math.sin(clock * mood.bob_hz * math.tau) * mood.bob_amp
        shift = 0.0
        if mood.jitter:
            bob += self._rng.uniform(-mood.jitter, mood.jitter)
            shift = self._rng.uniform(-mood.jitter, mood.jitter)
        fx, fy = FACE_CX + shift, FACE_CY + bob

        level = 0.0 if self._stress.value is None else self._stress.value
        # A roaming rig owns the whole screen; a halo pinned where the face
        # used to be would just be a stain it keeps walking away from.
        aura_on = self.rig is None or self.rig.CENTERED
        for i in range(AURA_N):
            if not aura_on:
                c.itemconfigure(self.items[f"aura{i}"], state="hidden")
                continue
            scale = 1.34 - i * (0.30 / (AURA_N - 1))
            pulse = 1.0 + 0.02 * (1.0 + level) \
                * math.sin(clock * (0.5 + i * 0.2) * math.tau)
            size = r * 2 * scale * pulse
            c.itemconfigure(self.items[f"aura{i}"], state="normal")
            c.coords(self.items[f"aura{i}"], *self._box(_oval(fx, fy, size, size)))

        if self.rig is not None:
            c.itemconfigure(self.items["glyph"], state="hidden")
            for name in self._drawn_parts:
                c.itemconfigure(self.items[name], state="hidden")
            self.rig.update(ctx if ctx is not None
                            else self._context(clock, now))
            return

        if (self.character in ("emoji", "image")
                and self._draw_glyph(mood, fx, fy, clock)):
            return
        c.itemconfigure(self.items["glyph"], state="hidden")
        self._draw_drawn_face(mood, fx, fy, clock, now)

    @staticmethod
    def _frame_at(frames: list, clock: float):
        """The frame this many seconds into the loop."""
        total = sum(delay for _photo, delay in frames)
        if total <= 0:
            return frames[0][0]
        elapsed = (clock * 1000.0) % total
        running = 0
        for photo, delay in frames:
            running += delay
            if elapsed < running:
                return photo
        return frames[-1][0]

    def _draw_glyph(self, mood: Mood, fx: float, fy: float,
                    clock: float = 0.0) -> bool:
        """Place the mood's picture or emoji.

        False if neither can be drawn, so the caller falls back to the face
        built from canvas shapes. A missing image quietly becomes its emoji
        rather than an empty panel.
        """
        g, c = self.geo, self.canvas
        want = int(round(FACE_R * 2 * g.kx))
        photo = None
        if self.character == "image":
            path = resolve_image(self.images.get(mood.key, ""), self.image_base)
            # An animated file plays; a still one is a single image. Both come
            # from the same cache, so a GIF is decoded once.
            frames = self.emoji.image_frames(path, want, self.canvas)
            photo = (self._frame_at(frames, clock) if frames
                     else self.emoji.image_photo(path, want, self.canvas))
        if photo is None:
            photo = self.emoji.photo(mood.emoji, want, self.canvas)
        if photo is None:
            return False
        for name in self._drawn_parts:
            c.itemconfigure(self.items[name], state="hidden")
        c.itemconfigure(self.items["glyph"], image=photo, state="normal")
        c.coords(self.items["glyph"], g.x(fx), g.y(fy))
        return True

    def _draw_drawn_face(self, mood: Mood, fx: float, fy: float,
                         clock: float, now: float) -> None:
        c, r = self.canvas, FACE_R
        c.itemconfigure(self.items["body"], state="normal")
        c.itemconfigure(self.items["gloss"], state="normal")
        c.coords(self.items["body"], *self._box(_oval(fx, fy, r * 2, r * 2)))
        c.coords(self.items["gloss"],
                 *self._box(_oval(fx - r * 0.42, fy - r * 0.66, r * 0.48, r * 0.22)))

        # The face looks around on its own: a wandering gaze target, eased,
        # so the eyes drift rather than teleport. Both eyes share it.
        if now >= self._look_at:
            self._look_at = now + self._rng.uniform(1.8, 4.5)
            self._look_t = (self._rng.uniform(-8.0, 8.0),
                            self._rng.uniform(-5.0, 5.0))
        look_step = 1.0 - math.exp(-(self.frame_ms / 1000.0) / 0.15)
        self._look[0] += (self._look_t[0] - self._look[0]) * look_step
        self._look[1] += (self._look_t[1] - self._look[1]) * look_step

        blink = self._blink_factor(now)
        for side, sign in (("l", -1.0), ("r", 1.0)):
            ex = fx + sign * r * 0.36 + self._look[0]
            ey = fy - r * 0.20 + self._look[1]

            c.itemconfigure(self.items[f"lid_{side}"],
                            state="normal" if mood.lids else "hidden")
            if mood.lids:
                c.coords(self.items[f"lid_{side}"], *self._pts(_lid_points(ex, ey, 30)))

            for name in (f"spiral_{side}", f"spiral2_{side}"):
                c.itemconfigure(self.items[name],
                                state="normal" if mood.dizzy else "hidden")
            if mood.dizzy:
                spin = clock * 90.0
                c.coords(self.items[f"spiral_{side}"], *self._box(_oval(ex, ey, 56, 56)))
                c.itemconfigure(self.items[f"spiral_{side}"], start=spin % 360)
                c.coords(self.items[f"spiral2_{side}"], *self._box(_oval(ex, ey, 28, 28)))
                c.itemconfigure(self.items[f"spiral2_{side}"], start=(spin + 180) % 360)

            hidden = mood.lids or mood.dizzy
            c.itemconfigure(self.items[f"eye_{side}"],
                            state="hidden" if hidden else "normal")
            c.coords(self.items[f"eye_{side}"],
                     *self._box(_oval(ex, ey, mood.eye_w,
                                      max(3.0, mood.eye_h * blink))))
            shine = not hidden and blink > 0.6
            c.itemconfigure(self.items[f"shine_{side}"],
                            state="normal" if shine else "hidden")
            if shine:
                c.coords(self.items[f"shine_{side}"],
                         *self._box(_oval(ex + mood.eye_w * 0.22,
                                          ey - mood.eye_h * 0.24, 13, 13)))

            c.itemconfigure(self.items[f"brow_{side}"], state="normal")
            c.coords(self.items[f"brow_{side}"],
                     *self._pts(_brow_points(ex, fy - r * 0.46, 42, mood.brow, sign)))
            c.itemconfigure(self.items[f"blush_{side}"],
                            state="normal" if mood.blush > 0.05 else "hidden")
            if mood.blush > 0.05:
                c.coords(self.items[f"blush_{side}"],
                         *self._box(_oval(fx + sign * r * 0.62, fy + r * 0.12, 74, 40)))

        self._draw_shades(mood, fx, fy)

        my = fy + r * 0.30
        wide = mood.mouth_open > 0.15
        c.itemconfigure(self.items["mouth"], state="hidden" if wide else "normal")
        c.itemconfigure(self.items["mouth_open"], state="normal" if wide else "hidden")
        c.itemconfigure(self.items["tongue"],
                        state="normal" if (wide and mood.tongue) else "hidden")
        if wide:
            breathe = 1.0 + 0.12 * math.sin(clock * 2.2 * math.tau)
            w = mood.mouth_w * 1.5
            h = mood.mouth_w * 1.15 * mood.mouth_open * breathe
            c.coords(self.items["mouth_open"], *self._box(_oval(fx, my, w, h)))
            c.coords(self.items["tongue"],
                     *self._box(_oval(fx, my + h * 0.26, w * 0.62, h * 0.50)))
        else:
            c.coords(self.items["mouth"],
                     *self._pts(_mouth_points(fx, my, mood.mouth_w, mood.mouth_curve,
                                              mood.mouth_wave, clock * 3.4)))

    def _draw_shades(self, mood: Mood, fx: float, fy: float) -> None:
        c, r = self.canvas, FACE_R
        state = "normal" if mood.shades else "hidden"
        for name in ("shades_l", "shades_r", "shades_bridge"):
            c.itemconfigure(self.items[name], state=state)
        if not mood.shades:
            return

        ey = fy - r * 0.20
        for side, sign in (("l", -1.0), ("r", 1.0)):
            inner, outer = fx + sign * r * 0.13, fx + sign * r * 0.70
            x0, x1 = (outer, inner) if sign < 0 else (inner, outer)
            top, bot = ey - 42, ey + 32
            # Slightly tapered toward the nose, which is what stops a pair of
            # plain rectangles from reading as goggles.
            c.coords(self.items[f"shades_{side}"], *self._pts([
                x0, top, x1, top, x1, bot - 12, (x0 + x1) / 2.0, bot, x0, bot - 16,
            ]))
        c.coords(self.items["shades_bridge"],
                 *self._pts([fx - r * 0.13, ey - 28, fx + r * 0.13, ey - 28]))

    def _blink_factor(self, now: float) -> float:
        if now >= self._blink_at:
            self._blink_until = now + 0.12
            self._blink_at = now + self._rng.uniform(2.6, 6.5)
        if now < self._blink_until:
            t = 1.0 - (self._blink_until - now) / 0.12
            return max(0.06, abs(t * 2.0 - 1.0))
        return 1.0

    # --- particles --------------------------------------------------------

    def _draw_particles(self, mood: Mood, clock: float) -> None:
        kind = mood.particles
        # A rig does its own sweating, steaming and venting, in its own idiom
        # and at its own position; the face's particles would double up on it,
        # pinned to a spot the character may not even be standing in.
        if self.rig is not None:
            kind = "none"
        elif self._variant and mood.key in PARTICLE_ALT:
            kind = PARTICLE_ALT[mood.key]
        # Chill uses drifting snow to stand in for "it is cool in here". With a
        # real sky overhead that would be a second, contradictory snowfall, so
        # the measured one wins.
        if kind == "snow" and self._sky is not None:
            kind = "none"
        if kind != self._shown:
            self._shown = kind
            self._show("snow", SNOW_N, kind in ("snow", "spark"))
            self._show("drop", DROP_N, kind == "sweat")
            self._show("wisp", WISP_N, kind == "steam")
            self._show("flame", FLAME_N, kind == "fire")
            self._show("zzz", ZZZ_N, kind == "zzz")

        dt = self.frame_ms / 1000.0
        if kind == "snow":
            self._draw_snow(dt)
        elif kind == "spark":
            self._draw_spark(clock)
        elif kind == "sweat":
            self._draw_sweat(dt)
        elif kind == "steam":
            self._draw_steam(clock)
        elif kind == "fire":
            self._draw_fire(clock)
        elif kind == "zzz":
            self._draw_zzz(dt)

    def _show(self, prefix: str, count: int, visible: bool) -> None:
        state = "normal" if visible else "hidden"
        for i in range(count):
            self.canvas.itemconfigure(self.items[f"{prefix}{i}"], state=state)

    def _draw_snow(self, dt: float) -> None:
        c = self.canvas
        for i, flake in enumerate(self._snow):
            flake[1] += flake[2] * dt
            if flake[1] > 700:
                self._snow[i] = flake = self._new_snow()
            x = flake[0] + math.sin(flake[1] * 0.02 + flake[3]) * 16.0
            rad = flake[4]
            c.coords(self.items[f"snow{i}"], *self._box(_oval(x, flake[1], rad, rad)))
            c.itemconfigure(self.items[f"snow{i}"], fill="#e8f7ff")

    def _draw_spark(self, clock: float) -> None:
        """Ticks orbiting the head: something is being worked on."""
        c = self.canvas
        colour = mix(self.pal["accent"], "#ffffff", 0.4)
        for i in range(SNOW_N):
            angle = clock * 0.9 + i * (math.tau / SNOW_N)
            radius = FACE_R * (1.16 + 0.05 * math.sin(clock * 2.0 + i))
            x = FACE_CX + math.cos(angle) * radius
            y = FACE_CY + math.sin(angle) * radius * 0.92
            rad = 6.0 + 4.0 * (0.5 + 0.5 * math.sin(clock * 3.0 + i * 1.7))
            c.coords(self.items[f"snow{i}"], *self._box(_oval(x, y, rad, rad)))
            c.itemconfigure(self.items[f"snow{i}"], fill=colour)

    def _draw_sweat(self, dt: float) -> None:
        c = self.canvas
        for i, drop in enumerate(self._drops):
            if drop[3] > 0:
                drop[3] -= dt
                c.itemconfigure(self.items[f"drop{i}"], state="hidden")
                continue
            drop[1] += drop[2] * dt
            # Stops at the jaw line. Falling further took drops down through
            # the caption, where they read as a rendering fault.
            if drop[1] > FACE_CY + FACE_R * 1.08:
                self._drops[i] = self._new_drop(i)
                continue
            c.coords(self.items[f"drop{i}"],
                     *self._pts(_drop_points(drop[0], drop[1], drop[4])))
            c.itemconfigure(self.items[f"drop{i}"], state="normal", fill="#7dd3fc")

    def _draw_steam(self, clock: float) -> None:
        """Wisps off the top of the head.

        They live behind the face and there is only about 110 design pixels
        between the crown and the header rule, so travel and length are both
        sized to that gap; a longer climb put most of them either inside the
        silhouette or through the header.
        """
        c = self.canvas
        for i in range(WISP_N):
            x = FACE_CX - FACE_R * 0.7 + i * (FACE_R * 1.4 / (WISP_N - 1))
            rise = (clock * 34.0 + i * 8.0) % 48.0
            c.coords(self.items[f"wisp{i}"],
                     *self._pts(_wisp_points(x, FACE_CY - FACE_R * 1.02 - rise,
                                             52, 14.0, clock * 2.0 + i)))
            c.itemconfigure(self.items[f"wisp{i}"],
                            fill=mix(self.pal["sky_top"], "#ffffff",
                                     0.30 + 0.40 * (1.0 - rise / 48.0)))

    def _draw_fire(self, clock: float) -> None:
        c = self.canvas
        for i in range(FLAME_N):
            angle = math.pi + (i + 0.5) * (math.pi / FLAME_N)
            # 1.10 rather than 1.02: at the flanks this puts the flame just
            # clear of the silhouette instead of half-buried in it.
            x = FACE_CX + math.cos(angle) * FACE_R * 1.10
            y = FACE_CY - math.sin(angle) * FACE_R * 1.10
            flick = 0.6 + 0.4 * math.sin(clock * 7.0 + i * 2.1)
            c.coords(self.items[f"flame{i}"],
                     *self._pts(_flame_points(x, y, 46.0, 45.0 + 55.0 * flick,
                                              math.sin(clock * 9.0 + i) * 7.0)))
            c.itemconfigure(self.items[f"flame{i}"],
                            fill=mix("#f97316", "#fde047", flick * 0.6))

    def _draw_zzz(self, dt: float) -> None:
        c, g = self.canvas, self.geo
        for i in range(ZZZ_N):
            self._zzz[i] = (self._zzz[i] + dt * 0.16) % 1.0
            t = self._zzz[i]
            c.coords(self.items[f"zzz{i}"],
                     g.x(FACE_CX + FACE_R * 0.62 + t * 90.0),
                     g.y(FACE_CY - FACE_R * 0.72 - t * 150.0))
            # Fades in as well as out, so a Z never pops into existence.
            visible = min(1.0, t / 0.2, (1.0 - t) / 0.35)
            c.itemconfigure(self.items[f"zzz{i}"],
                            fill=mix(self.pal["sky_top"], self.pal["accent"],
                                     max(0.0, visible)))

    # --- weather ----------------------------------------------------------

    def _draw_weather(self, clock: float, now: float) -> None:
        c = self.canvas
        outdoor = self._weather
        live = self._sky is not None
        condition = str(getattr(outdoor, "condition", "clear")) if live else "clear"
        daylight = float(getattr(outdoor, "daylight", 1.0)) if live else 1.0
        arc = float(getattr(outdoor, "arc", 0.5)) if live else 0.5
        clouds, precip, celestial = (WX_LOOK.get(condition, (4, 0, 0.0)) if live
                                     else (0, 0, 0.0))
        if not self.wx_effects:
            clouds, precip = 0, 0

        line = self._weather_line() if (live and self.wx_line) else ""
        c.itemconfigure(self.items["wxline"], text=line,
                        state="normal" if line else "hidden",
                        fill=self.ink.get("faint", theme.TEXT_FAINT))

        # Stars only on a clear or nearly clear night. Behind overcast they
        # would be wrong, and nobody reads a star as decoration.
        starry = (live and self.wx_effects and daylight < 0.35
                  and condition in ("clear", "partly"))
        for i, (_x, _y, _size, phase) in enumerate(self._stars):
            item = self.items[f"star{i}"]
            if not starry:
                c.itemconfigure(item, state="hidden")
                continue
            twinkle = ((0.45 + 0.35 * math.sin(clock * 1.4 + phase))
                       * (1.0 - daylight / 0.35))
            c.itemconfigure(item, state="normal",
                            fill=mix(self.pal["sky_top"], "#ffffff", twinkle))

        self._draw_celestial(celestial, daylight, arc, clock)
        self._draw_clouds(clouds, daylight)
        self._draw_precip(condition, precip)
        self._draw_lightning(self.wx_effects and condition == "thunder", now)

    def _weather_line(self) -> str:
        template = str((self.cfg.get("weather") or {}).get("line_format", "") or "")
        try:
            text = (self._weather.format(template) if template
                    else getattr(self._weather, "line", ""))
        except Exception:
            text = getattr(self._weather, "line", "")
        return text.upper()

    def _draw_celestial(self, visibility: float, daylight: float, arc: float,
                        clock: float) -> None:
        """Sun by day, moon by night, rising and setting on its arc."""
        c = self.canvas
        if visibility <= 0.0:
            for name in ("sun.glow", "sun.disc"):
                c.itemconfigure(self.items[name], state="hidden")
            return
        arc = max(0.0, min(1.0, arc))
        y = SUN_Y[0] - (SUN_Y[0] - SUN_Y[1]) * math.sin(math.pi * arc)
        # Crossfaded rather than switched, so the hour around sunset shows one
        # pale disc instead of a sun that becomes a moon between two frames.
        colour = mix("#e6ebf7", "#ffd76a", daylight)
        breathe = 1.0 + 0.03 * math.sin(clock * 0.4 * math.tau)
        glow = SUN_R * 3.4 * breathe
        c.coords(self.items["sun.glow"], *self._box(_oval(SUN_X, y, glow, glow)))
        c.itemconfigure(self.items["sun.glow"], state="normal",
                        fill=mix(self.pal["sky_top"], colour, 0.16 * visibility))
        c.coords(self.items["sun.disc"],
                 *self._box(_oval(SUN_X, y, SUN_R * 2, SUN_R * 2)))
        c.itemconfigure(self.items["sun.disc"], state="normal",
                        fill=mix(self.pal["sky_top"], colour, 0.55 + 0.40 * visibility))

    def _draw_clouds(self, count: int, daylight: float) -> None:
        c, dt = self.canvas, self.frame_ms / 1000.0
        # A white cloud is invisible on a bright sky, so over a light one they
        # darken instead of lightening -- which is also what a real cloud does.
        light = self.ink.get("light", False)
        toward = "#2b3440" if light else "#ffffff"
        tint = (0.14 + 0.06 * daylight) if light else (0.10 + 0.06 * daylight)
        for i, cloud in enumerate(self._clouds):
            item = self.items[f"cloud{i}"]
            if i >= count:
                c.itemconfigure(item, state="hidden")
                continue
            cloud[0] += cloud[2] * dt
            if cloud[0] - cloud[3] > theme.DESIGN_W + 120:
                cloud[0] = -cloud[3] - 120
            c.coords(item, *self._pts(
                _cloud_points(cloud[0], cloud[1], cloud[3], cloud[3] * 0.34)))
            c.itemconfigure(item, state="normal",
                            fill=mix(self.pal["sky_top"], toward, tint + i * 0.015))

    def _draw_precip(self, condition: str, count: int) -> None:
        c, g, dt = self.canvas, self.geo, self.frame_ms / 1000.0
        if count != self._wx_shown:
            self._wx_shown = count
            for i in range(WX_N):
                c.itemconfigure(self.items[f"wx{i}"],
                                state="normal" if i < count else "hidden")
        if not count:
            return

        snowing = condition == "snow"
        light = condition == "drizzle"
        if self.ink.get("light", False):
            colour = "#e8f2ff" if snowing else mix(self.pal["sky_top"],
                                                   "#2f4a63", 0.75)
        else:
            colour = "#eef6ff" if snowing else mix(self.pal["sky_top"],
                                                   "#bcdcff", 0.80)
        for i in range(count):
            particle = self._wx[i]
            if snowing:
                speed = 55.0 + 60.0 * particle[2]
            else:
                speed = (360.0 if light else 720.0) + 420.0 * particle[2]
            particle[1] += speed * dt
            if particle[1] > theme.DESIGN_H + 30:
                self._wx[i] = particle = self._new_wx()
            item = self.items[f"wx{i}"]
            if snowing:
                x = particle[0] + math.sin(particle[1] * 0.016 + particle[3]) * 22.0
                # A round-capped zero-length line is a dot, which is what lets
                # rain and snow share one pool of canvas items.
                c.coords(item, g.x(x), g.y(particle[1]),
                         g.x(x + 0.1), g.y(particle[1]))
                c.itemconfigure(item, fill=colour, width=g.stroke(7 + 6 * particle[4]))
            else:
                length = (16.0 if light else 30.0) * particle[4]
                c.coords(item, g.x(particle[0] + length * 0.28),
                         g.y(particle[1] - length),
                         g.x(particle[0]), g.y(particle[1]))
                c.itemconfigure(item, fill=colour, width=g.stroke(4))

    def _draw_lightning(self, storming: bool, now: float) -> None:
        """A brief whitening of the whole sky. Cheap, and unmistakable."""
        c = self.canvas
        if not storming:
            self._flash_until = 0.0
            return
        if now >= self._flash_at:
            self._flash_at = now + self._rng.uniform(4.0, 11.0)
            self._flash_until = now + 0.10
        if now < self._flash_until:
            # 0.38, not a white-out. This panel is inside a case in a dark room
            # and a full flash reads as a fault rather than as weather.
            flash = mix(self.pal["sky_top"], "#dfe6ff", 0.38)
            for i in range(self.SKY_BANDS):
                c.itemconfigure(self.items[f"sky{i}"], fill=flash)
        elif self._flash_until:
            self._flash_until = 0.0
            self._repaint_static()          # put the real sky back

    # --- caption ----------------------------------------------------------

    def _draw_caption(self, mood: Mood, now: float) -> None:
        c = self.canvas
        if not self.show_caption:
            c.itemconfigure(self.items["caption"], text="")
            c.itemconfigure(self.items["quip"], text="")
            return
        # A rig may know better than the mood: the pet captions itself with
        # its age and appetite, which no stress band could say.
        text = (self.rig.caption(mood) if self.rig is not None else None) \
            or mood.caption
        c.itemconfigure(self.items["caption"], text=text)
        if not self.show_quips or not mood.quips:
            c.itemconfigure(self.items["quip"], text="")
            return
        if now - self._quip_at > QUIP_EVERY_S:
            self._quip_at = now
            self._quip = self._rng.choice(mood.quips)
        c.itemconfigure(self.items["quip"], text=self._quip)


def apply_overrides(opts: dict) -> dict[str, Mood]:
    """MOODS with the user's captions, quips and emoji folded in."""
    faces = opts.get("faces") or {}
    captions = opts.get("captions") or {}
    quips = opts.get("quips") if isinstance(opts.get("quips"), dict) else {}
    out = {}
    for key, mood in MOODS.items():
        changes = {}
        if isinstance(faces.get(key), str) and faces[key].strip():
            changes["emoji"] = faces[key].strip()
        if isinstance(captions.get(key), str) and captions[key].strip():
            changes["caption"] = captions[key].strip()
        lines = quips.get(key)
        if isinstance(lines, (list, tuple)):
            kept = tuple(str(line) for line in lines if str(line).strip())
            if kept:
                changes["quips"] = kept
        out[key] = replace(mood, **changes) if changes else mood
    return out


def stat_regions(cfg: dict) -> list[tuple[str, str, int, float, float, float, float]]:
    """Clickable areas for the Layout editor, in design-space coordinates."""
    layout = cfg.get("layout", {})
    stats = layout.get("stats", []) or []
    fans = layout.get("fans", []) or []
    out = []
    for index, slot in enumerate(stats):
        x, y, w, h = card_rect(slot, index, len(stats))
        out.append((f"stat{index}", "stats", index, x, y, x + w, y + h))
    for index, slot in enumerate(fans):
        x, y, w, h = fan_rect(slot, index, len(fans))
        out.append((f"fan{index}", "fans", index, x, y, x + w, y + h))
    return out
