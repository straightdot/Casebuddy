"""Draws the dashboard onto a Tk canvas.

Every canvas item is created once in `build()` and thereafter only has its
text/coords/color changed. This is not a micro-optimization: deleting and
recreating a ~120-item scene leaks roughly 9.7 MB per 1000 frames on the Tcl
side, linearly and with no plateau -- about 840 MB/day at 1 Hz. Incremental
updates hold flat. Nothing on a periodic path may call delete().

All coordinates below are in the 1920x1080 design space. theme.Geometry maps
them to real pixels, including the optional 4:3 pre-squash.
"""

from __future__ import annotations

import tkinter.font as tkfont

from . import theme
from .metrics import Reading, Snapshot, fmt_tile

MARGIN = 30
COLS = 3
GAP = 24
CELL_W = (theme.DESIGN_W - 2 * MARGIN - (COLS - 1) * GAP) / COLS  # 604

# Vertical rhythm. Tk lays text out from a bounding box roughly 1.2x the font
# size tall, so a 140 px numeral anchored "sw" reaches ~168 px above its anchor.
# Every gap below was checked against that.

HEADER_Y = 56
RULE_Y = 102

# Ring row: label 112..180, ring 187..501, detail 514..579
RING_LABEL_Y = 112
RING_CY = 344
RING_R = 143
RING_VALUE_Y = 330
RING_UNIT_Y = 398
RING_DETAIL_Y = 514

# Bar row: label 618..686, numeral 694..862, bar 882..920
BAR_LABEL_Y = 618
BAR_VALUE_Y = 862
BAR_TRACK_Y = 882

# Fan strip: two rows of live RPM bars.
FAN_RULE_Y = 944
FAN_ROWS_Y = (978, 1050)  # 72 apart, so a 34 px bar leaves a 38 px gutter
FAN_BAR_X0 = 330
FAN_BAR_X1 = 1500

# Bands reserved inside a ring tile for its label and its detail line. With no
# rect override the derived geometry lands within a couple of pixels of the
# original hand-tuned constants, so the shipped screen is unchanged.
RING_LABEL_BAND = 76
RING_DETAIL_BAND = 76

# Gauge sweep: 225 deg (upper-left) clockwise through 270 deg to -45 (lower-right).
ARC_START = 225.0
ARC_EXTENT = -270.0


def tile_scale(rect, default_w: float, default_h: float) -> float:
    """How far to shrink a tile's type, given how far the tile itself shrank.

    Without this a tile dragged to a third of its size keeps full-size numerals
    and simply overflows its own box. Floored at 0.45: past that the label stops
    being readable on a 4.3" panel and hiding it would be more honest than
    shrinking it further.
    """
    return max(0.45, min(1.0, min(rect[2] / default_w, rect[3] / default_h)))


def cell_x(index: int) -> float:
    return MARGIN + index * (CELL_W + GAP)


def ring_rect(slot: dict, index: int) -> tuple:
    """Where ring `index` goes: its own dragged rect, or the built-in column."""
    from .buddy import clean_rect

    return clean_rect(slot.get("rect")) or (
        cell_x(index), RING_LABEL_Y - 8, CELL_W,
        RING_DETAIL_Y + 66 - (RING_LABEL_Y - 8))


def bar_rect(slot: dict, index: int) -> tuple:
    from .buddy import clean_rect

    return clean_rect(slot.get("rect")) or (
        cell_x(index), BAR_LABEL_Y - 8, CELL_W,
        BAR_TRACK_Y + theme.BAR_HEIGHT + 8 - (BAR_LABEL_Y - 8))


def fan_rect(slot: dict, index: int) -> tuple:
    from .buddy import clean_rect

    cy = FAN_ROWS_Y[index] if index < len(FAN_ROWS_Y) else FAN_ROWS_Y[-1]
    return clean_rect(slot.get("rect")) or (
        MARGIN, cy - 30, theme.DESIGN_W - 2 * MARGIN, 60)


class Dashboard:
    """Owns the canvas items. One instance per window.

    `frame_ms` of 0 means "repaint at the configured ui_hz". Only an animated
    scene overrides it; see buddy.BuddyScene.
    """

    frame_ms = 0

    # Slots come from cfg["layout"], not from constants here: the Layout tab
    # can repoint any tile at any of the ~190 available metrics, and the
    # collector keys its readings by position (ring0, bar1, fan0...).

    def _slots(self, kind: str, prefix: str) -> list[tuple[str, str]]:
        rows = self.cfg.get("layout", {}).get(kind, [])
        return [(f"{prefix}{i}", str(slot.get("label", ""))) for i, slot in enumerate(rows)]

    def _fan_colour(self, index: int) -> str:
        # Resolved at paint time, never cached at import: theme.apply()
        # installs the configured palette after this module is imported.
        return theme.FAN_CPU if index == 0 else theme.FAN_GPU

    def __init__(self, canvas, geo: theme.Geometry, cfg: dict) -> None:
        self.canvas = canvas
        self.geo = geo
        self.cfg = cfg
        self.items: dict[str, int] = {}

        families = set(tkfont.families(canvas))
        self.installed = families
        self.names = names = theme.Fonts(families,
                                         cfg.get("theme", {}).get("fonts"))
        self._font_cache: dict = {}
        self.f_hero = self._font(names.numeral, theme.F_HERO, "bold", "numeral")
        self.f_hero_sm = self._font(names.numeral, theme.F_HERO_SMALL, "bold",
                                    "numeral")
        self.f_unit = self._font(names.label, theme.F_UNIT, "normal")
        self.f_label = self._font(names.label, theme.F_LABEL, "bold")
        self.f_detail = self._font(names.label, theme.F_DETAIL, "normal")
        self.f_header = self._font(names.label, theme.F_HEADER, "normal")
        self.f_fan = self._font(names.label, theme.F_FAN, "bold")

    def _role_scale(self, role: str) -> float:
        """Numerals and labels size independently; see theme.NUMERAL_SCALE.

        Taken from an explicit role rather than inferred from the family name,
        because a tile with its own font has a family that matches neither of
        the theme's two and would otherwise always be sized as a label.
        """
        return theme.NUMERAL_SCALE if role == "numeral" else theme.LABEL_SCALE

    def _font(self, family: str, size: float, weight: str,
              role: str = "label") -> tkfont.Font:
        return tkfont.Font(
            root=self.canvas, family=family,
            size=self.geo.font(size * self._role_scale(role)), weight=weight
        )

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


    # --- construction -----------------------------------------------------

    def build(self) -> None:
        g, c = self.geo, self.canvas
        c.configure(bg=theme.BG, highlightthickness=0, bd=0)

        self.items["hw"] = c.create_text(
            g.x(MARGIN), g.y(HEADER_Y), anchor="w", fill=theme.TEXT_FAINT,
            font=self.f_header, text="",
        )
        self.items["clock"] = c.create_text(
            g.x(theme.DESIGN_W - MARGIN), g.y(HEADER_Y), anchor="e", fill=theme.TEXT_DIM,
            font=self.f_header, text="",
        )
        c.create_line(
            g.x(MARGIN), g.y(RULE_Y), g.x(theme.DESIGN_W - MARGIN), g.y(RULE_Y),
            fill=theme.PANEL_EDGE, width=g.stroke(5),
        )

        layout = self.cfg.get("layout", {})
        for i, (key, label) in enumerate(self._slots('rings', 'ring')):
            self._build_ring(i, key, label, layout["rings"][i])
        for i, (key, label) in enumerate(self._slots('bars', 'bar')):
            self._build_bar(i, key, label, layout["bars"][i])

        c.create_line(
            g.x(MARGIN), g.y(FAN_RULE_Y), g.x(theme.DESIGN_W - MARGIN), g.y(FAN_RULE_Y),
            fill=theme.PANEL_EDGE, width=g.stroke(5),
        )
        for i, (key, label) in enumerate(self._slots('fans', 'fan')):
            self._build_fan(i, key, label, self._fan_colour(i), layout["fans"][i])

    def _build_ring(self, index: int, key: str, label: str, slot: dict) -> None:
        g, c = self.geo, self.canvas
        x0, y0, w, h = ring_rect(slot, index)
        # Label band on top, detail band underneath, ring in whatever is left,
        # so a resized tile keeps all three rather than overlapping them.
        scale = tile_scale((x0, y0, w, h), CELL_W,
                           RING_DETAIL_Y + 66 - (RING_LABEL_Y - 8))
        label_band = RING_LABEL_BAND * scale
        detail_band = RING_DETAIL_BAND * scale
        cx = x0 + w / 2
        band = max(60.0, h - label_band - detail_band)
        radius = max(24.0, min(w, band) / 2 - theme.RING_STROKE / 2)
        ring_cy = y0 + label_band + band / 2
        # The numeral has to fit INSIDE the ring, which shrinks faster than the
        # tile does once the tile stops being square.
        hero_scale = min(scale, radius / RING_R)
        size, bold, italic = self._slot_style(slot)
        slant = "italic" if italic else "roman"
        heavy = (lambda natural: "bold" if bold else natural)
        f_label = self._tile_font(self.names.label, theme.F_LABEL, heavy("bold"),
                                  scale * size, "label", slant)
        f_hero = self._tile_font(self.names.numeral, theme.F_HERO, heavy("bold"),
                                 hero_scale * size, "numeral", slant)
        f_hero_sm = self._tile_font(self.names.numeral, theme.F_HERO_SMALL,
                                    heavy("bold"), hero_scale * size, "numeral",
                                    slant)
        f_unit = self._tile_font(self.names.label, theme.F_UNIT, heavy("normal"),
                                 hero_scale * size, "label", slant)
        f_detail = self._tile_font(self.names.label, theme.F_DETAIL,
                                   heavy("normal"), scale * size, "label", slant)
        self.items[f"{key}.fonts"] = (f_hero, f_hero_sm)
        box = (
            g.x(cx - radius), g.y(ring_cy - radius),
            g.x(cx + radius), g.y(ring_cy + radius),
        )
        stroke = g.stroke(theme.RING_STROKE)

        c.create_text(
            g.x(cx), g.y(y0 + 8), anchor="n", fill=theme.TEXT_DIM,
            font=f_label, text=label,
        )
        # Track first so the value arc paints over it.
        c.create_arc(
            *box, start=ARC_START, extent=ARC_EXTENT, style="arc",
            outline=theme.TRACK, width=stroke,
        )
        self.items[f"{key}.arc"] = c.create_arc(
            *box, start=ARC_START, extent=-0.01, style="arc",
            outline=theme.NA, width=stroke, state="hidden",
        )
        self.items[f"{key}.value"] = c.create_text(
            g.x(cx), g.y(ring_cy - 14 * scale), anchor="center", fill=theme.NA,
            font=f_hero, text="--",
        )
        self.items[f"{key}.unit"] = c.create_text(
            g.x(cx), g.y(ring_cy + 54 * hero_scale), anchor="n",
            fill=theme.TEXT_FAINT, font=f_unit, text="",
        )
        self.items[f"{key}.detail"] = c.create_text(
            g.x(cx), g.y(y0 + h - detail_band + 8 * scale), anchor="n",
            fill=theme.TEXT_DIM, font=f_detail, text="",
        )

    def _build_bar(self, index: int, key: str, label: str, slot: dict) -> None:
        g, c = self.geo, self.canvas
        x0, y0, w, h = bar_rect(slot, index)
        scale = tile_scale((x0, y0, w, h), CELL_W,
                           BAR_TRACK_Y + theme.BAR_HEIGHT + 8 - (BAR_LABEL_Y - 8))
        x1 = x0 + w
        track_y = y0 + h - theme.BAR_HEIGHT * scale
        value_y = track_y - 20 * scale
        size, bold, italic = self._slot_style(slot)
        slant = "italic" if italic else "roman"
        heavy = (lambda natural: "bold" if bold else natural)
        f_label = self._tile_font(self.names.label, theme.F_LABEL, heavy("bold"),
                                  scale * size, "label", slant)
        f_hero = self._tile_font(self.names.numeral, theme.F_HERO, heavy("bold"),
                                 scale * size, "numeral", slant)
        f_hero_sm = self._tile_font(self.names.numeral, theme.F_HERO_SMALL,
                                    heavy("bold"), scale * size, "numeral", slant)
        f_unit = self._tile_font(self.names.label, theme.F_UNIT, heavy("normal"),
                                 scale * size, "label", slant)
        f_detail = self._tile_font(self.names.label, theme.F_DETAIL,
                                   heavy("normal"), scale * size, "label", slant)
        self.items[f"{key}.fonts"] = (f_hero, f_hero_sm)
        bar_h = theme.BAR_HEIGHT * scale

        c.create_text(
            g.x(x0), g.y(y0 + 8 * scale), anchor="nw", fill=theme.TEXT_DIM,
            font=f_label, text=label,
        )
        # Shares the title line, right-aligned. The label is short enough
        # ("RAM", "V-RAM") that there is room, and it keeps the live figure
        # from competing with the static clock in the bottom-right slot.
        self.items[f"{key}.top"] = c.create_text(
            g.x(x1), g.y(y0 + 14 * scale), anchor="ne", fill=theme.TEXT_DIM,
            font=f_detail, text="",
        )
        self.items[f"{key}.value"] = c.create_text(
            g.x(x0), g.y(value_y), anchor="sw", fill=theme.NA,
            font=f_hero, text="--",
        )
        self.items[f"{key}.unit"] = c.create_text(
            g.x(x0), g.y(value_y), anchor="sw", fill=theme.TEXT_FAINT,
            font=f_unit, text="",
        )
        self.items[f"{key}.detail"] = c.create_text(
            g.x(x1), g.y(value_y), anchor="se", fill=theme.TEXT_DIM,
            font=f_detail, text="",
        )
        c.create_rectangle(
            g.x(x0), g.y(track_y), g.x(x1), g.y(track_y + bar_h),
            fill=theme.TRACK, outline="",
        )
        self.items[f"{key}.fill"] = c.create_rectangle(
            g.x(x0), g.y(track_y), g.x(x0), g.y(track_y + bar_h),
            fill=theme.NA, outline="", state="hidden",
        )
        self.items[f"{key}.x0"] = x0
        self.items[f"{key}.x1"] = x1
        self.items[f"{key}.rows"] = (value_y, track_y, bar_h)

    def _build_fan(self, index: int, key: str, label: str, color: str,
                   slot: dict) -> None:
        g, c = self.geo, self.canvas
        fx, fy, fw, fh = fan_rect(slot, index)
        cy = fy + fh / 2
        half = min(theme.FAN_BAR_HEIGHT, fh * 0.58) / 2
        # Label on the left, reading on the right, bar with what is between.
        scale = max(0.45, min(1.0, fh / 60.0))
        size, bold, italic = self._slot_style(slot)
        f_fan = self._tile_font(self.names.label, theme.F_FAN,
                                "bold" if bold else "bold", scale * size, "label",
                                "italic" if italic else "roman")
        bar0 = fx + min(300.0 * scale, fw * 0.17)
        bar1 = fx + fw - min(388.0 * scale, fw * 0.24)

        c.create_text(
            g.x(fx), g.y(cy), anchor="w", fill=theme.TEXT_DIM,
            font=f_fan, text=label,
        )
        c.create_rectangle(
            g.x(bar0), g.y(cy - half), g.x(bar1), g.y(cy + half),
            fill=theme.TRACK, outline="",
        )
        self.items[f"{key}.fill"] = c.create_rectangle(
            g.x(bar0), g.y(cy - half), g.x(bar0), g.y(cy + half),
            fill=color, outline="", state="hidden",
        )
        self.items[f"{key}.value"] = c.create_text(
            g.x(fx + fw), g.y(cy), anchor="e", fill=theme.NA,
            font=f_fan, text="--",
        )
        self.items[f"{key}.cy"] = cy
        self.items[f"{key}.bar"] = (bar0, bar1, half)
        self.items[f"{key}.percent"] = bool(slot.get("percent"))

    # --- per-frame update -------------------------------------------------

    def update(self, snap: Snapshot, history: dict | None = None) -> None:
        c = self.canvas

        notice = snap.notices[0] if snap.notices else ""
        if notice:
            # A warning is worth more than the hardware names it displaces.
            c.itemconfigure(self.items["hw"], text=notice.upper(), fill=theme.WARN)
        else:
            c.itemconfigure(self.items["hw"],
                            text=snap.get("hdr0").detail.upper(),
                            fill=theme.TEXT_FAINT)
        c.itemconfigure(self.items["clock"], text=snap.get("hdr1").detail.upper())

        for key, _ in self._slots('rings', 'ring'):
            self._update_ring(key, snap.get(key))
        for key, _ in self._slots('bars', 'bar'):
            self._update_bar(key, snap.get(key))
        for i, (key, _label) in enumerate(self._slots('fans', 'fan')):
            self._update_fan(key, snap.get(key), self._fan_colour(i))

    def _update_ring(self, key: str, r: Reading) -> None:
        c = self.canvas
        color = theme.STATE_COLOR.get(r.state, theme.NA)
        text = fmt_tile(r)

        c.itemconfigure(
            self.items[f"{key}.value"],
            text=text,
            fill=color if r.available else theme.NA,
            font=self.items[f"{key}.fonts"][1 if len(text) > 3 else 0],
        )
        c.itemconfigure(self.items[f"{key}.unit"], text=r.unit if r.available else "")
        c.itemconfigure(self.items[f"{key}.detail"], text=r.detail)

        arc = self.items[f"{key}.arc"]
        if r.fraction:
            c.itemconfigure(
                arc, state="normal", outline=color,
                extent=max(-359.9, ARC_EXTENT * r.fraction),
            )
        else:
            c.itemconfigure(arc, state="hidden")

    def _update_bar(self, key: str, r: Reading) -> None:
        g, c = self.geo, self.canvas
        color = theme.STATE_COLOR.get(r.state, theme.NA)
        text = fmt_tile(r)

        value_item = self.items[f"{key}.value"]
        hero, hero_sm = self.items[f"{key}.fonts"]
        font = hero_sm if len(text) > 3 else hero
        c.itemconfigure(value_item, text=text, fill=color if r.available else theme.NA, font=font)

        # The unit sits immediately after a number whose width changes, so it
        # gets repositioned from a live measurement rather than a fixed offset.
        x0 = self.items[f"{key}.x0"]
        value_y, track_y, bar_h = self.items[f"{key}.rows"]
        unit_x = g.x(x0) + font.measure(text) + g.w(14)
        c.coords(self.items[f"{key}.unit"], unit_x, g.y(value_y))
        c.itemconfigure(self.items[f"{key}.unit"], text=r.unit if r.available else "")
        c.itemconfigure(self.items[f"{key}.detail"], text=r.detail)
        c.itemconfigure(self.items[f"{key}.top"], text=r.top)

        fill = self.items[f"{key}.fill"]
        if r.fraction:
            x1 = x0 + (self.items[f"{key}.x1"] - x0) * r.fraction
            c.coords(
                fill, g.x(x0), g.y(track_y), g.x(x1), g.y(track_y + bar_h),
            )
            c.itemconfigure(fill, state="normal", fill=color)
        else:
            c.itemconfigure(fill, state="hidden")

    def _update_fan(self, key: str, r: Reading, color: str) -> None:
        g, c = self.geo, self.canvas
        cy = self.items[f"{key}.cy"]
        bar0, bar1, half = self.items[f"{key}.bar"]

        if r.available:
            label = f"{r.value:.0f} {r.unit or 'RPM'}"
            # See the note in buddy.py: a row showing watts has no duty sensor
            # to borrow a percentage from, so it reads its own fill instead.
            if self.items[f"{key}.percent"] and r.fraction is not None:
                label = f"{r.fraction * 100:.0f}%   {label}"
            if r.detail:
                label = f"{r.detail}   {label}"
        else:
            label = "--"
        c.itemconfigure(
            self.items[f"{key}.value"], text=label,
            fill=theme.TEXT if r.available else theme.NA,
        )

        fill = self.items[f"{key}.fill"]
        if r.fraction:
            x1 = bar0 + (bar1 - bar0) * r.fraction
            c.coords(fill, g.x(bar0), g.y(cy - half), g.x(x1), g.y(cy + half))
            c.itemconfigure(fill, state="normal", fill=color)
        else:
            c.itemconfigure(fill, state="hidden")


def mode_of(cfg: dict) -> str:
    """Which screen the layout asks for. Unknown names fall back to gauges."""
    name = str(cfg.get("layout", {}).get("mode", "gauges")).lower()
    return name if name in ("gauges", "buddy") else "gauges"


def make_scene(canvas, geo: theme.Geometry, cfg: dict):
    """The renderer this layout calls for.

    Both classes take (canvas, geo, cfg) and expose build()/update(snap), so
    the window and the layout preview do not care which one they are holding.
    """
    if mode_of(cfg) == "buddy":
        from .buddy import BuddyScene

        return BuddyScene(canvas, geo, cfg)
    return Dashboard(canvas, geo, cfg)


def slot_regions(cfg: dict) -> list[tuple[str, str, int, float, float, float, float]]:
    """Clickable areas per slot, in design-space coordinates.

    Returned as (key, kind, index, x0, y0, x1, y1). The Layout editor maps a
    click on the preview back through Geometry into this space, so the regions
    and the drawing stay defined in one place and cannot drift apart.
    """
    out = []
    layout = cfg.get("layout", {})
    header = layout.get("header", [])
    mid = theme.DESIGN_W / 2
    for index in range(len(header)):
        x0, x1 = (MARGIN, mid) if index == 0 else (mid, theme.DESIGN_W - MARGIN)
        out.append((f"hdr{index}", "header", index, x0, 18, x1, RULE_Y - 6))

    # The header is common to both screens; everything below it is not.
    if mode_of(cfg) == "buddy":
        from .buddy import stat_regions

        return out + stat_regions(cfg)

    # Straight from the same rects the builders draw, so a dragged tile is
    # clickable exactly where it appears and the two can never drift apart.
    for kind, prefix, rect_of in (("rings", "ring", ring_rect),
                                  ("bars", "bar", bar_rect),
                                  ("fans", "fan", fan_rect)):
        for index, slot in enumerate(layout.get(kind, []) or []):
            x, y, w, h = rect_of(slot, index)
            out.append((f"{prefix}{index}", kind, index, x, y, x + w, y + h))
    return out
