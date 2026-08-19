"""Animated backdrops for the buddy screen.

A scene is scenery, not chrome: it lives between the sky gradient and
everything that carries information, so a fish can swim behind a stat card but
never over one. Each scene reads the same live inputs the mood does -- load,
heat, fan speed, weather, time of day -- because a backdrop that ignores the
machine is just a screensaver, and this panel exists to show the machine.

Two layers, because two kinds of scenery exist:

    back    part of the sky itself (starfield, synthwave, matrix). Built
            right after the sky bands, so the sun, stars and rain that
            _build_weather creates afterwards draw OVER them.
    front   things standing in front of the sky (skyline, aquarium). Built
            after the weather, so the sun sets BEHIND a skyscraper and a
            star never twinkles through the water.

Both are always below the cards, the face and the text, which are created
later still. Tk's z-order is creation order, and this module leans on that
rather than juggling tags.

Scenes follow the same economy as everything else here: items are created once
in build() and only ever moved or recoloured. Colours that track the palette
change in recolor(), which the host calls from its own _repaint_static, so a
mood swing recolours a scene exactly as often as it recolours the sky.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import theme


@dataclass
class Context:
    """What one frame knows. Built by the host, consumed by scenes and rigs."""
    clock: float          # seconds since the scene was built
    dt: float             # seconds per frame
    now: float            # time.monotonic()
    stress: object        # buddy.Stress: .value/.heat/.load/.driver
    vitals: dict          # every numeric calc: metric this sample produced
    weather: object       # sources.weather.Weather or None
    mood: object          # the current buddy.Mood
    pal: dict             # the eased palette: sky_top, sky_bot, glow, accent...
    ink: dict             # ink_for() result: text/dim/faint/light

    @property
    def load(self) -> float:
        return float(getattr(self.stress, "load", 0.0) or 0.0)

    @property
    def heat(self) -> float:
        return float(getattr(self.stress, "heat", 0.0) or 0.0)

    @property
    def level(self) -> float:
        return float(getattr(self.stress, "value", 0.0) or 0.0)

    @property
    def daylight(self) -> float:
        return float(getattr(self.weather, "daylight", 1.0) or 0.0) \
            if self.weather is not None else 1.0


def fan_fraction(ctx: Context, cfg: dict) -> float:
    """How hard the fans are working, 0..1. Duty cycle where a board reports
    one, RPM against the configured full-scale where it does not, and the CPU
    fan and GPU fan race each other for the honour. Shared with the rigs: the
    fish and the cat's tail are driven by the same number."""
    best = 0.0
    for pct in ("cpu_fan_pct", "gpu_fan_pct"):
        value = ctx.vitals.get(pct)
        if value is not None:
            best = max(best, float(value) / 100.0)
    fans = cfg.get("fans", {}) or {}
    for rpm, key in (("cpu_fan_rpm", "cpu_max_rpm"),
                     ("gpu_fan_rpm", "gpu_max_rpm")):
        value = ctx.vitals.get(rpm)
        top = float(fans.get(key, 2200.0) or 2200.0)
        if value is not None and top > 0:
            best = max(best, float(value) / top)
    return max(0.0, min(1.0, best))


class Scene:
    """One backdrop. Subclasses draw; this holds the plumbing."""

    LAYER = "back"

    def __init__(self, host) -> None:
        self.host = host
        self.canvas = host.canvas
        self.geo = host.geo
        self.rng = host._rng
        self.cfg = host.cfg
        self.items: dict = {}

    # Coordinate sugar, same shapes as the host's.
    def _pts(self, flat) -> list[float]:
        g = self.geo
        return [g.x(v) if i % 2 == 0 else g.y(v) for i, v in enumerate(flat)]

    def _box(self, x0, y0, x1, y1) -> list[float]:
        g = self.geo
        return [g.x(x0), g.y(y0), g.x(x1), g.y(y1)]

    def fan_fraction(self, ctx: Context) -> float:
        return fan_fraction(ctx, self.cfg)

    def build(self) -> None:
        raise NotImplementedError

    def update(self, ctx: Context) -> None:
        raise NotImplementedError

    def recolor(self, pal: dict, ink: dict) -> None:
        """Palette changed. Most scenes recolour in update() as they move."""


# --- starfield --------------------------------------------------------------

STAR_COUNT = 64
WARP_CX, WARP_CY = 960.0, 500.0


class Starfield(Scene):
    """Stars streaming outward from the middle. Idle they drift; loaded they
    streak, so a render kicking off reads as the jump to lightspeed."""

    LAYER = "back"

    def build(self) -> None:
        c = self.canvas
        # The tunnel mouth: three soft discs where the streaks are born,
        # lit once the machine crosses into the warp tiers.
        for k, radius in enumerate((150.0, 84.0, 38.0)):
            self.items[f"core{k}"] = c.create_oval(
                0, 0, 1, 1, fill="#ffffff", outline="", state="hidden")
        self._stars = []
        for i in range(STAR_COUNT):
            self._stars.append(self._spawn(seeded=True))
            self.items[f"s{i}"] = c.create_line(
                0, 0, 1, 1, fill="#ffffff", capstyle="round",
                width=self.geo.stroke(3))

    def _spawn(self, seeded: bool = False) -> list[float]:
        r = self.rng
        return [r.uniform(0, math.tau),                    # angle
                r.uniform(0.02, 1.0 if seeded else 0.12),  # radius, 0..1
                r.uniform(0.5, 1.4)]                       # personal pace

    def update(self, ctx: Context) -> None:
        c, g = self.canvas, self.geo
        # Load sets the pace; past the WORKING line the whole stream
        # accelerates again -- paired with the starship dropping small and
        # nose-away, it reads as the jump to hyperspace.
        speed = (0.05 + 0.75 * ctx.load) \
            * (1.0 + 1.8 * max(0.0, ctx.level - 0.42))
        # Over a bright sky white stars vanish, so they flip to dark specks --
        # dust motes rather than stars, but still legibly "moving at speed".
        toward = "#26313f" if ctx.ink.get("light") else "#ffffff"
        boost = max(0.0, ctx.level - 0.42)
        pulse = 1.0 + 0.10 * math.sin(ctx.clock * 5.0)
        for k, radius in enumerate((150.0, 84.0, 38.0)):
            item = self.items[f"core{k}"]
            if boost <= 0.0:
                c.itemconfigure(item, state="hidden")
                continue
            size = radius * (0.6 + boost) * pulse
            c.coords(item, g.x(WARP_CX - size), g.y(WARP_CY - size * 0.62),
                     g.x(WARP_CX + size), g.y(WARP_CY + size * 0.62))
            c.itemconfigure(item, state="normal",
                            fill=_mixc(ctx.pal["sky_top"], "#e8f4ff",
                                       (0.30, 0.55, 0.85)[k] * min(1.0, boost * 2.5)))
        for i, star in enumerate(self._stars):
            star[1] += ctx.dt * speed * star[2]
            if star[1] >= 1.0:
                self._stars[i] = star = self._spawn()
            angle, radius, _pace = star
            x = WARP_CX + math.cos(angle) * radius * 1180.0
            y = WARP_CY + math.sin(angle) * radius * 660.0
            # The streak grows with speed and with distance from centre; near
            # the middle even a fast star is a dot, which sells the depth.
            trail = radius * (6.0 + 220.0 * speed)
            c.coords(self.items[f"s{i}"],
                     g.x(x - math.cos(angle) * trail),
                     g.y(y - math.sin(angle) * trail * 0.56),
                     g.x(x), g.y(y))
            c.itemconfigure(self.items[f"s{i}"],
                            fill=_mixc(ctx.pal["sky_top"], toward,
                                       0.25 + 0.65 * radius),
                            width=g.stroke(2.0 + 3.0 * radius))


# --- synthwave --------------------------------------------------------------

HORIZON = 560.0
RAY_N = 13
SCROLL_N = 9
SUN2_R = 170.0
SUN2_CUTS = 4


class Synthwave(Scene):
    """The neon grid. Verticals converge on the horizon; horizontals roll
    toward the viewer at load speed, and a banded sun sits where it always
    sits on a cassette cover."""

    LAYER = "back"

    def build(self) -> None:
        c, g = self.canvas, self.geo

        # The sun first, so the grid draws over its lower edge.
        self.items["sun"] = c.create_arc(
            *self._box(WARP_CX - SUN2_R, HORIZON - SUN2_R,
                       WARP_CX + SUN2_R, HORIZON + SUN2_R),
            start=0, extent=180, style="chord", outline="", fill="#ff2fb3")
        # The classic horizontal slits, faked with sky-coloured bars whose
        # fill recolor() keeps matched to the band of gradient behind them.
        for i in range(SUN2_CUTS):
            y = HORIZON - 14.0 - i * (26.0 + i * 7.0)
            self.items[f"cut{i}"] = c.create_rectangle(
                *self._box(WARP_CX - SUN2_R, y - (4.0 + i * 2.2),
                           WARP_CX + SUN2_R, y + (4.0 + i * 2.2)),
                outline="", fill="#000000")

        for k in range(RAY_N):
            spread = k - (RAY_N - 1) / 2.0
            self.items[f"ray{k}"] = c.create_line(
                g.x(WARP_CX + spread * 26.0), g.y(HORIZON),
                g.x(WARP_CX + spread * 300.0), g.y(theme.DESIGN_H),
                fill="#ff2fb3", width=g.stroke(3))
        self._scroll = [i / SCROLL_N for i in range(SCROLL_N)]
        for i in range(SCROLL_N):
            self.items[f"row{i}"] = c.create_line(
                0, 0, 1, 1, fill="#ff2fb3", width=g.stroke(3))
        self.items["edge"] = c.create_line(
            g.x(0), g.y(HORIZON), g.x(theme.DESIGN_W), g.y(HORIZON),
            fill="#22d3ee", width=g.stroke(5))
        self.recolor(self.host.pal, self.host.ink)

    def recolor(self, pal: dict, ink: dict) -> None:
        c = self.canvas
        pink = _mixc("#ff2fb3", pal["accent"], 0.30)
        cyan = _mixc("#22d3ee", pal["accent"], 0.30)
        c.itemconfigure(self.items["sun"], fill=_mixc(pink, "#ffb02e", 0.35))
        for i in range(SUN2_CUTS):
            y = HORIZON - 14.0 - i * (26.0 + i * 7.0)
            band = _mixc(pal["sky_top"], pal["sky_bot"], y / theme.DESIGN_H)
            c.itemconfigure(self.items[f"cut{i}"], fill=band)
        for k in range(RAY_N):
            c.itemconfigure(self.items[f"ray{k}"], fill=_mixc(pink, pal["sky_bot"], 0.35))
        c.itemconfigure(self.items["edge"], fill=cyan)

    def update(self, ctx: Context) -> None:
        c, g = self.canvas, self.geo
        speed = 0.06 + 0.55 * ctx.load
        pink = _mixc("#ff2fb3", ctx.pal["accent"], 0.30)
        for i in range(SCROLL_N):
            self._scroll[i] = (self._scroll[i] + ctx.dt * speed) % 1.0
            t = self._scroll[i]
            # t^2.4 spaces the rows like a floor receding to the horizon;
            # linear spacing reads as a ladder, not a plane.
            y = HORIZON + (theme.DESIGN_H - HORIZON) * (t ** 2.4)
            c.coords(self.items[f"row{i}"],
                     g.x(0), g.y(y), g.x(theme.DESIGN_W), g.y(y))
            c.itemconfigure(self.items[f"row{i}"],
                            fill=_mixc(ctx.pal["sky_bot"], pink, 0.25 + 0.75 * t),
                            width=g.stroke(2.0 + 4.0 * t))


# --- matrix rain ------------------------------------------------------------

COL_N = 20
TRAIL = 9
GLYPHS = "0123456789ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝZ"
CELL = 54.0


class MatrixRain(Scene):
    """Falling code. Each column is two text items -- a dim multiline trail
    and one bright head -- because a per-glyph item grid would be 200 canvas
    items where 40 will do."""

    LAYER = "back"

    def build(self) -> None:
        import tkinter.font as tkfont

        c, g = self.canvas, self.geo
        self._font = tkfont.Font(root=c, family=self.host.names.mono,
                                 size=g.font(40), weight="bold")
        self._cols = []
        pitch = theme.DESIGN_W / COL_N
        for i in range(COL_N):
            x = pitch * (i + 0.5)
            self._cols.append([
                x,
                self.rng.uniform(-900.0, 900.0),   # head y
                self.rng.uniform(0.55, 1.30),      # personal pace
                None,                              # last drawn cell index
            ])
            self.items[f"t{i}"] = c.create_text(
                g.x(x), 0, anchor="s", justify="center", font=self._font,
                fill="#0a3d1c", text=self._burst())
            self.items[f"h{i}"] = c.create_text(
                g.x(x), 0, anchor="s", font=self._font,
                fill="#b7ffc9", text=self._glyph())

    def _glyph(self) -> str:
        return self.rng.choice(GLYPHS)

    def _burst(self) -> str:
        return "\n".join(self._glyph() for _ in range(TRAIL))

    def update(self, ctx: Context) -> None:
        c, g = self.canvas, self.geo
        speed = 130.0 + 700.0 * ctx.load
        # Bright green on black; ink-dark green when the day sky is light.
        light = ctx.ink.get("light")
        trail_fill = _mixc(ctx.pal["sky_top"], "#0f5d2a" if light else "#27c455",
                           0.85)
        head_fill = "#0b3d1e" if light else "#c8ffd6"
        # Recolour only when the palette actually moved: itemconfigure on a
        # text item makes Tk re-render its glyphs, and doing that to forty
        # items a frame was 7x the cost of every other scene combined.
        if (trail_fill, head_fill) != getattr(self, "_fills", None):
            self._fills = (trail_fill, head_fill)
            for i in range(COL_N):
                c.itemconfigure(self.items[f"t{i}"], fill=trail_fill)
                c.itemconfigure(self.items[f"h{i}"], fill=head_fill)
        for i, col in enumerate(self._cols):
            col[1] += ctx.dt * speed * col[2]
            if col[1] - TRAIL * CELL > theme.DESIGN_H + 60:
                col[1] = self.rng.uniform(-700.0, -60.0)
                col[2] = self.rng.uniform(0.55, 1.30)
                c.itemconfigure(self.items[f"t{i}"], text=self._burst())
            # Real matrix rain moves one glyph cell at a time, not smoothly --
            # and a re-layout of forty text items per frame was most of this
            # scene's cost. Quantising to the cell serves both at once.
            cell = int(col[1] // CELL)
            if cell != col[3]:
                col[3] = cell
                y = cell * CELL
                c.coords(self.items[f"t{i}"], g.x(col[0]), g.y(y - CELL))
                c.coords(self.items[f"h{i}"], g.x(col[0]), g.y(y))
                c.itemconfigure(self.items[f"h{i}"], text=self._glyph())


# --- aquarium ---------------------------------------------------------------

WATER_TOP = 640.0
WATER_BANDS = 8
FISH_N = 6
BUBBLE_N = 14
WEED_N = 3

# Cool water and cooked water. The tint follows the HEAT term specifically,
# not overall stress, because "the water is boiling" is a statement about
# temperature and a compile at 55 C should leave it blue.
WATER_COOL = ("#0f4a63", "#051d2b")
WATER_HOT = ("#5e1d12", "#230704")

FISH_COLOURS = ("#ff9a3d", "#ffd23d", "#4dd0e1", "#f06292", "#9ccc65",
                "#ce93d8")


def _fish_points(x: float, y: float, s: float, facing: float,
                 wag: float) -> list[float]:
    """A fish as one smooth polygon: nose, back, tail fork, belly.
    `facing` is +1 swimming right, -1 left; `wag` swings the tail."""
    f = facing
    return [
        x + f * s, y,                          # nose
        x + f * s * 0.45, y - s * 0.38,        # back
        x - f * s * 0.35, y - s * 0.30,
        x - f * s * 0.60, y - s * 0.06,        # tail root
        x - f * s * 1.05, y - s * 0.42 + wag,  # tail top
        x - f * s * 0.82, y + wag * 0.4,       # tail notch
        x - f * s * 1.05, y + s * 0.42 + wag,  # tail bottom
        x - f * s * 0.60, y + s * 0.10,
        x - f * s * 0.30, y + s * 0.34,        # belly
        x + f * s * 0.50, y + s * 0.36,
    ]


class Aquarium(Scene):
    """The machine as a fish tank. Fan speed sets how hard the fish swim,
    CPU load sets how hard the tank bubbles, and the heat term literally
    changes the water."""

    LAYER = "front"

    def build(self) -> None:
        c, g = self.canvas, self.geo
        step = (theme.DESIGN_H - WATER_TOP) / WATER_BANDS
        for i in range(WATER_BANDS):
            self.items[f"w{i}"] = c.create_rectangle(
                *self._box(0, WATER_TOP + i * step - 1,
                           theme.DESIGN_W, WATER_TOP + (i + 1) * step + 1),
                outline="", fill=WATER_COOL[1])
        # The surface is 16 live points; everything else in the water is
        # content to bob, but a flat waterline reads as a floor.
        self.items["surface"] = c.create_line(
            *self._pts([0, WATER_TOP, theme.DESIGN_W, WATER_TOP]),
            fill="#7fd4e8", width=g.stroke(4), smooth=True)

        for i in range(WEED_N):
            self.items[f"weed{i}"] = c.create_line(
                0, 0, 1, 1, fill="#1c5e40", width=g.stroke(12), smooth=True,
                capstyle="round")
        self._weed_x = [self.rng.uniform(120, 700) if i < 2
                        else self.rng.uniform(1250, 1800) for i in range(WEED_N)]
        self._weed_h = [self.rng.uniform(120, 230) for _ in range(WEED_N)]

        self._fish = []
        for i in range(FISH_N):
            self._fish.append([
                self.rng.uniform(100, 1820),               # x
                self.rng.uniform(WATER_TOP + 80, 1000),    # y
                1.0 if self.rng.random() < 0.5 else -1.0,  # facing
                self.rng.uniform(26, 52),                  # size
                self.rng.uniform(0, math.tau),             # bob phase
                self.rng.uniform(0.7, 1.3),                # personal pace
            ])
            self.items[f"fish{i}"] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill=FISH_COLOURS[i % len(FISH_COLOURS)],
                outline="", smooth=True)
            self.items[f"eye{i}"] = c.create_oval(
                0, 0, 1, 1, fill="#10151c", outline="")

        self._bubbles = [[0.0, -100.0, 0.0, 0.0] for _ in range(BUBBLE_N)]
        self._pending = 0.0
        for i in range(BUBBLE_N):
            self.items[f"bub{i}"] = c.create_oval(
                0, 0, 1, 1, outline="#bfe9f5", width=g.stroke(3),
                fill="", state="hidden")
        self._tinted = -1.0

    def update(self, ctx: Context) -> None:
        c = self.canvas

        # Water tint, quantised to twentieths so it recolours a handful of
        # times across a warm-up rather than every frame.
        heat = round(ctx.heat / 0.05) * 0.05
        if heat != self._tinted:
            self._tinted = heat
            top = _mixc(WATER_COOL[0], WATER_HOT[0], heat)
            bot = _mixc(WATER_COOL[1], WATER_HOT[1], heat)
            for i in range(WATER_BANDS):
                c.itemconfigure(self.items[f"w{i}"],
                                fill=_mixc(top, bot, i / (WATER_BANDS - 1.0)))
            c.itemconfigure(self.items["surface"],
                            fill=_mixc("#7fd4e8", "#ffb199", heat))
            for i in range(WEED_N):
                c.itemconfigure(self.items[f"weed{i}"],
                                fill=_mixc("#1c5e40", "#4a3520", heat))

        pts = []
        for k in range(17):
            x = theme.DESIGN_W * k / 16.0
            pts.extend((x, WATER_TOP + math.sin(ctx.clock * 1.1 + k * 0.9) * 5.0))
        c.coords(self.items["surface"], *self._pts(pts))

        for i in range(WEED_N):
            x, h = self._weed_x[i], self._weed_h[i]
            sway = 10.0 + 26.0 * self.fan_fraction(ctx)
            wp = []
            for k in range(6):
                t = k / 5.0
                wp.extend((x + math.sin(ctx.clock * 0.8 + i * 2.0 + t * 2.6)
                           * sway * t, theme.DESIGN_H - h * t))
            c.coords(self.items[f"weed{i}"], *self._pts(wp))

        pace = 40.0 + 340.0 * self.fan_fraction(ctx)
        for i, fish in enumerate(self._fish):
            x, y, facing, size, phase, personal = fish
            x += facing * pace * personal * ctx.dt
            if x > 1900:
                facing, x = -1.0, 1900.0
            elif x < 20:
                facing, x = 1.0, 20.0
            wob = math.sin(ctx.clock * 1.3 + phase) * 9.0
            wag = math.sin(ctx.clock * (3.0 + pace * 0.02) + phase) * size * 0.30
            fish[0], fish[2] = x, facing
            c.coords(self.items[f"fish{i}"],
                     *self._pts(_fish_points(x, y + wob, size, facing, wag)))
            ex = x + facing * size * 0.62
            c.coords(self.items[f"eye{i}"],
                     *self._box(ex - size * 0.09, y + wob - size * 0.16,
                                ex + size * 0.09, y + wob + size * 0.02))

        # Bubbles: born at the bottom at a rate the CPU sets, dead at the
        # surface. The pool is fixed; a busy machine just recycles it faster.
        self._pending += ctx.dt * (0.8 + 14.0 * ctx.load)
        for i, bub in enumerate(self._bubbles):
            if bub[1] < WATER_TOP + 12:
                if self._pending >= 1.0:
                    self._pending -= 1.0
                    bub[0] = self.rng.uniform(60, 1860)
                    bub[1] = theme.DESIGN_H - 20.0
                    bub[2] = self.rng.uniform(5.0, 13.0)
                    bub[3] = self.rng.uniform(0, math.tau)
                    c.itemconfigure(self.items[f"bub{i}"], state="normal")
                else:
                    c.itemconfigure(self.items[f"bub{i}"], state="hidden")
                    continue
            bub[1] -= ctx.dt * (110.0 + bub[2] * 9.0)
            x = bub[0] + math.sin(bub[1] * 0.03 + bub[3]) * 14.0
            c.coords(self.items[f"bub{i}"],
                     *self._box(x - bub[2], bub[1] - bub[2],
                                x + bub[2], bub[1] + bub[2]))


# --- city skyline -----------------------------------------------------------

BUILDING_N = 12
WINDOW_CAP = 132


class Skyline(Scene):
    """A silhouette city whose windows are the machine's cores going home for
    the night. The left half of town lights up with CPU load, the right half
    with GPU load, one window at a time -- so a render farm evening rush is
    visible from across the room."""

    LAYER = "front"

    def build(self) -> None:
        c = self.canvas
        r = self.rng
        self._windows: list[tuple[int, bool]] = []   # (item index, left half)
        self._lit: list[bool] = []
        edges = sorted(r.uniform(60, 1860) for _ in range(BUILDING_N))
        tallest, tallest_top = 0, 1080.0
        made = 0
        for b, x in enumerate(edges):
            w = r.uniform(96, 210)
            top = r.uniform(620, 950)
            if top < tallest_top:
                tallest, tallest_top = b, top
            self.items[f"b{b}"] = c.create_rectangle(
                *self._box(x - w / 2, top, x + w / 2, theme.DESIGN_H),
                outline="", fill="#0a0d13")
            self.items[f"b{b}.rect"] = (x - w / 2, top, x + w / 2)
            # Window grid, walked left-right top-bottom until the cap.
            wx = x - w / 2 + 16
            while wx + 22 < x + w / 2 - 10 and made < WINDOW_CAP:
                wy = top + 20
                while wy + 26 < theme.DESIGN_H - 60 and made < WINDOW_CAP:
                    self.items[f"win{made}"] = c.create_rectangle(
                        *self._box(wx, wy, wx + 20, wy + 24),
                        outline="", fill="#ffd27a", state="hidden")
                    self._windows.append((made, x < 960.0))
                    self._lit.append(False)
                    made += 1
                    wy += 58
                wx += 44
        self._beacon_x = edges[tallest]
        self._beacon_y = tallest_top
        self.items["beacon"] = c.create_oval(
            *self._box(self._beacon_x - 7, tallest_top - 26,
                       self._beacon_x + 7, tallest_top - 12),
            outline="", fill="#ff3d55", state="hidden")
        self._stir_at = 0.0
        self.recolor(self.host.pal, self.host.ink)

    def recolor(self, pal: dict, ink: dict) -> None:
        c = self.canvas
        # By day the city is a grey silhouette against the light. By night it
        # sits a step BLUER than the sky rather than a step darker: darker
        # than near-black is indistinguishable from it, and the first cut of
        # this scene was a field of windows floating on nothing.
        body = (_mixc(pal["sky_bot"], "#3a4350", 0.75) if ink.get("light")
                else _mixc(pal["sky_bot"], "#232f47", 0.65))
        for b in range(BUILDING_N):
            c.itemconfigure(self.items[f"b{b}"], fill=body)
        glow = _mixc("#ffd27a", body, 0.45 if ink.get("light") else 0.0)
        for index, _left in self._windows:
            c.itemconfigure(self.items[f"win{index}"], fill=glow)

    def update(self, ctx: Context) -> None:
        c = self.canvas
        # Windows change a couple at a time, twice a second. Snapping the
        # whole grid to the new load in one frame looks like a glitch; a few
        # lights flicking on as the load ramps looks like a city.
        if ctx.now >= self._stir_at:
            self._stir_at = ctx.now + 0.5
            cpu = float(ctx.vitals.get("cpu_load", 0.0) or 0.0) / 100.0
            gpu = float(ctx.vitals.get("gpu_load", 0.0) or 0.0) / 100.0
            for half, want in ((True, cpu), (False, gpu)):
                members = [k for k, (_i, left) in enumerate(self._windows)
                           if left == half]
                if not members:
                    continue
                target = int(round(want * len(members)))
                lit = [k for k in members if self._lit[k]]
                dark = [k for k in members if not self._lit[k]]
                for _ in range(min(3, abs(target - len(lit)))):
                    if target > len(lit) and dark:
                        k = dark.pop(self.rng.randrange(len(dark)))
                        self._lit[k] = True
                        lit.append(k)
                    elif target < len(lit) and lit:
                        k = lit.pop(self.rng.randrange(len(lit)))
                        self._lit[k] = False
            for k, (index, _left) in enumerate(self._windows):
                c.itemconfigure(self.items[f"win{index}"],
                                state="normal" if self._lit[k] else "hidden")
        # The aircraft-warning beacon: on for a beat, off for a beat.
        on = (ctx.clock % 2.4) < 1.2
        c.itemconfigure(self.items["beacon"],
                        state="normal" if on else "hidden")




# --- the dragon's lair -------------------------------------------------------

STALACTITE_N = 7
EMBER_N = 12
LAVA_BANDS = 5
LAVA_TOP = 950.0

LAVA_COOL = ("#3a1206", "#160702")
LAVA_HOT = ("#ff6a1e", "#7a1d05")


class Lair(Scene):
    """A cave for the dragon: stalactites overhead, a lava pool along the
    bottom whose glow IS the heat term, and embers rising at load speed."""

    LAYER = "front"

    def build(self) -> None:
        c, g = self.canvas, self.geo
        r = self.rng
        step = (theme.DESIGN_H - LAVA_TOP) / LAVA_BANDS
        for i in range(LAVA_BANDS):
            self.items[f"lava{i}"] = c.create_rectangle(
                *self._box(0, LAVA_TOP + i * step - 1,
                           theme.DESIGN_W, LAVA_TOP + (i + 1) * step + 1),
                outline="", fill=LAVA_COOL[1])
        # The pool's surface: a slow bright seam.
        self.items["seam"] = c.create_line(
            0, 0, 1, 1, fill="#ff9c3e", width=g.stroke(5), smooth=True)
        for i in range(STALACTITE_N):
            x = 90.0 + i * (1740.0 / (STALACTITE_N - 1)) + r.uniform(-40, 40)
            w = r.uniform(30, 64)
            h = r.uniform(60, 150)
            self.items[f"stal{i}"] = c.create_polygon(
                *self._pts([x - w, 108.0, x + w, 108.0, x, 108.0 + h]),
                fill="#100a08", outline="")
        for i in range(EMBER_N):
            self.items[f"ember{i}"] = c.create_oval(
                0, 0, 1, 1, fill="#ff9c3e", outline="", state="hidden")
        self._embers = [[0.0, -100.0, 0.0, 0.0] for _ in range(EMBER_N)]
        self._pending = 0.0
        self._glow = -1.0

    def update(self, ctx: Context) -> None:
        c = self.canvas
        heat = round(ctx.heat / 0.05) * 0.05
        if heat != self._glow:
            self._glow = heat
            top = _mixc(LAVA_COOL[0], LAVA_HOT[0], heat)
            bot = _mixc(LAVA_COOL[1], LAVA_HOT[1], heat)
            for i in range(LAVA_BANDS):
                c.itemconfigure(self.items[f"lava{i}"],
                                fill=_mixc(top, bot, i / (LAVA_BANDS - 1.0)))
            for i in range(STALACTITE_N):
                c.itemconfigure(self.items[f"stal{i}"],
                                fill=_mixc("#100a08", "#2a0e04", heat))
        pts = []
        for k in range(13):
            x = theme.DESIGN_W * k / 12.0
            pts.extend((x, LAVA_TOP + math.sin(ctx.clock * 0.9 + k * 1.2) * 6.0))
        c.coords(self.items["seam"], *self._pts(pts))
        c.itemconfigure(self.items["seam"],
                        fill=_mixc("#7a2d0e", "#ffb35e", self._glow))

        # Embers: born on the pool at a rate the load sets, dying high.
        self._pending += ctx.dt * (0.6 + 10.0 * ctx.load)
        for i, ember in enumerate(self._embers):
            if ember[1] < 140.0 or ember[1] > theme.DESIGN_H:
                if self._pending >= 1.0:
                    self._pending -= 1.0
                    ember[0] = self.rng.uniform(60, 1860)
                    ember[1] = LAVA_TOP - 6.0
                    ember[2] = self.rng.uniform(3.0, 7.0)
                    ember[3] = self.rng.uniform(0, math.tau)
                    c.itemconfigure(self.items[f"ember{i}"], state="normal")
                else:
                    c.itemconfigure(self.items[f"ember{i}"], state="hidden")
                    ember[1] = -100.0
                    continue
            ember[1] -= ctx.dt * (60.0 + ember[2] * 14.0)
            x = ember[0] + math.sin(ember[1] * 0.02 + ember[3]) * 20.0
            rad = ember[2]
            c.coords(self.items[f"ember{i}"],
                     *self._box(x - rad, ember[1] - rad, x + rad, ember[1] + rad))
            c.itemconfigure(self.items[f"ember{i}"],
                            fill=_mixc("#ff6a1e", "#ffd24a",
                                       (ember[1] / LAVA_TOP)))


# --- the night raceway -------------------------------------------------------

FENCE_N = 14


class Raceway(Scene):
    """Trackside furniture for the car: catch-fence lights streaming past
    along the top and bottom lanes, faster as the machine works -- parallax
    for a track the rig itself drives."""

    LAYER = "back"

    def build(self) -> None:
        c, g = self.canvas, self.geo
        for i in range(FENCE_N * 2):
            self.items[f"fence{i}"] = c.create_line(
                0, 0, 1, 1, fill="#ffd21f", width=g.stroke(4),
                capstyle="round")
        self._offset = 0.0

    def update(self, ctx: Context) -> None:
        c = self.canvas
        self._offset = (self._offset + ctx.dt * (120.0 + 900.0 * ctx.level)) \
            % (theme.DESIGN_W / FENCE_N)
        pitch = theme.DESIGN_W / FENCE_N
        dash = 16.0 + 40.0 * ctx.level
        for i in range(FENCE_N * 2):
            lane = i // FENCE_N              # 0 = top lane, 1 = bottom lane
            k = i % FENCE_N
            # Opposite directions on the two lanes, like passing armco.
            if lane == 0:
                x = (k * pitch - self._offset) % theme.DESIGN_W
                y = 118.0
            else:
                x = (k * pitch + self._offset) % theme.DESIGN_W
                y = 826.0
            c.coords(self.items[f"fence{i}"],
                     *self._pts([x, y, x + dash, y]))
            c.itemconfigure(self.items[f"fence{i}"],
                            fill=_mixc(ctx.pal["sky_top"],
                                       "#ffd21f" if k % 3 else "#f5f6f8",
                                       0.5 + 0.5 * ctx.level))


# --- registry ---------------------------------------------------------------

SCENES: dict[str, type] = {
    "starfield": Starfield,
    "synthwave": Synthwave,
    "matrix": MatrixRain,
    "aquarium": Aquarium,
    "skyline": Skyline,
    "lair": Lair,
    "raceway": Raceway,
}

NAMES = ("off",) + tuple(SCENES)

# What the settings selector shows, in the order it shows them.
LABELS = {
    "off": "None - just the sky",
    "starfield": "Starfield - stars streak with load",
    "synthwave": "Synthwave - the neon grid rolls with load",
    "matrix": "Matrix - code falls faster under load",
    "aquarium": "Aquarium - fans swim it, load bubbles it, heat cooks it",
    "skyline": "City skyline - CPU lights the left half, GPU the right",
    "lair": "Dragon's lair - the lava pool glows with heat, embers rise with load",
    "raceway": "Night raceway - trackside lights stream past at load speed",
}


def make(name: str, host) -> Scene | None:
    """The named scene bound to this host, or None for "off" or a typo."""
    cls = SCENES.get(str(name or "").strip().lower())
    return cls(host) if cls else None


def _mixc(a: str, b: str, t: float) -> str:
    """Local colour mix; scenes cannot import buddy (buddy imports scenes)."""
    t = max(0.0, min(1.0, t))

    def rgb(colour: str) -> tuple[int, int, int]:
        text = colour.lstrip("#")
        if len(text) == 3:
            text = "".join(ch * 2 for ch in text)
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)

    ra, ga, ba = rgb(a)
    rb, gb, bb = rgb(b)
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, int(round(v)))) for v in
        (ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t))
