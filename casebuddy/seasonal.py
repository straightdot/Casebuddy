"""Calendar-driven effects: the panel keeps the Indian festive calendar.

What plays, and when:

    Makar Sankranti   Jan 14-15          kites swooping through the sky
    Republic Day      Jan 26             tricolour fireworks
    Holi              eve + main day     gulal powder bursts
    Independence Day  Aug 15             tricolour fireworks
    Navratri          9 nights           a garba ring of lamps circling
      -> Dussehra     through the day
    Diwali            Dhanteras to       diyas along the bottom edge,
                      Bhai Dooj          and fireworks over everything
    New Year          Dec 31 20:00 +     fireworks
                      Jan 1
    December          the whole month    drifting snow

Hindu festivals follow the lunisolar panchang, so their Gregorian dates are
TABLE-DRIVEN below, main day per year, currently 2026-2030. Extend the tables
when the decade runs out; a year with no entry simply skips that festival
rather than guessing. Regional observance can differ by a day -- edit the
table if yours does.

All of it is overlays on the buddy screen, fixed pools of items in the same
create-once economy as everything else, and the active effect is decided from
the local calendar every 30 seconds, not per frame. `seasonal_force` in the
buddy config pins one on for testing: snow, fireworks, kites, tricolor, holi,
garba or diwali.
"""

from __future__ import annotations

import datetime
import math
import time

from . import theme
from .scenes import Context, _mixc

# --- the panchang tables (main day per year) --------------------------------

DIWALI = {2026: (11, 8), 2027: (10, 29), 2028: (10, 17), 2029: (11, 5),
          2030: (10, 26)}
HOLI = {2026: (3, 4), 2027: (3, 22), 2028: (3, 11), 2029: (3, 1),
        2030: (3, 20)}
DUSSEHRA = {2026: (10, 20), 2027: (10, 9), 2028: (9, 27), 2029: (10, 16),
            2030: (10, 6)}

SNOW_N = 18
BURSTS = 2
SPARKS = 24
KITE_N = 5
GARBA_N = 14
DIYA_N = 8

BURST_COLOURS = ("#ffd24a", "#ff5d8f", "#7ce7ff", "#b78cff", "#8affc1")
TRICOLOUR = ("#ff9933", "#f5f5f5", "#138808")
GULAL = ("#e91e63", "#ffc107", "#4caf50", "#ff5722", "#9c27b0", "#00bcd4")
KITE_COLOURS = ("#ff5d8f", "#ffd24a", "#4caf50", "#7ce7ff", "#ff9933")
GARBA_COLOURS = ("#ffd24a", "#ff8c2e", "#ff5d8f")

FORCES = ("snow", "fireworks", "kites", "tricolor", "holi", "garba",
          "diwali")


def _near(date: datetime.date, table: dict, before: int, after: int) -> bool:
    entry = table.get(date.year)
    if not entry:
        return False
    main = datetime.date(date.year, *entry)
    return (main - datetime.timedelta(days=before) <= date
            <= main + datetime.timedelta(days=after))


def active_effect(when=None, force: str = "") -> str | None:
    """Which effect today earns. Festivals outrank the ambient seasons."""
    if force in FORCES:
        return force
    t = time.localtime(when)
    date = datetime.date(t.tm_year, t.tm_mon, t.tm_mday)
    if _near(date, DIWALI, 2, 1):              # Dhanteras through Bhai Dooj
        return "diwali"
    if _near(date, HOLI, 1, 0):                # Holika Dahan eve + Rangwali
        return "holi"
    if _near(date, DUSSEHRA, 9, 0):            # Navratri through Vijayadashami
        return "garba"
    if (t.tm_mon, t.tm_mday) in ((1, 14), (1, 15)):
        return "kites"
    if (t.tm_mon, t.tm_mday) in ((1, 26), (8, 15)):
        return "tricolor"
    if (t.tm_mon == 12 and t.tm_mday == 31 and t.tm_hour >= 20) \
            or (t.tm_mon == 1 and t.tm_mday == 1):
        return "fireworks"
    if t.tm_mon == 12:
        return "snow"
    return None


class Seasonal:
    """The overlay. Built once; does nothing at all outside its dates."""

    def __init__(self, host) -> None:
        self.host = host
        self.canvas = host.canvas
        self.geo = host.geo
        self.rng = host._rng
        opts = host.cfg.get("layout", {}).get("buddy", {}) or {}
        self.force = str(opts.get("seasonal_force", "") or "")
        self.items: dict = {}
        self._effect: str | None = None
        self._checked = 0.0

    def _pts(self, flat) -> list[float]:
        g = self.geo
        return [g.x(v) if i % 2 == 0 else g.y(v) for i, v in enumerate(flat)]

    def _oval(self, cx, cy, w, h) -> list[float]:
        g = self.geo
        return [g.x(cx - w / 2), g.y(cy - h / 2),
                g.x(cx + w / 2), g.y(cy + h / 2)]

    # --- pools ---------------------------------------------------------

    def build(self) -> None:
        c, g = self.canvas, self.geo
        for i in range(SNOW_N):
            self.items[f"flake{i}"] = c.create_oval(
                0, 0, 1, 1, fill="#eef6ff", outline="", state="hidden")
        # One burst pool serves fireworks, tricolour and Diwali; only the
        # palette changes.
        for b in range(BURSTS):
            for i in range(SPARKS):
                self.items[f"spark{b}_{i}"] = c.create_line(
                    0, 0, 1, 1, fill="#ffd24a", width=g.stroke(4),
                    capstyle="round", state="hidden")
        for i in range(KITE_N):
            self.items[f"kite{i}"] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill=KITE_COLOURS[i], outline="",
                state="hidden")
            self.items[f"ktail{i}"] = c.create_line(
                0, 0, 1, 1, fill=KITE_COLOURS[i], width=g.stroke(3),
                smooth=True, state="hidden")
        for i in range(GARBA_N):
            self.items[f"garba{i}"] = c.create_oval(
                0, 0, 1, 1, fill=GARBA_COLOURS[i % 3], outline="",
                state="hidden")
        for i in range(DIYA_N):
            self.items[f"dbase{i}"] = c.create_arc(
                0, 0, 1, 1, start=180, extent=180, style="chord",
                fill="#7a4a2e", outline="", state="hidden")
            self.items[f"dglow{i}"] = c.create_oval(
                0, 0, 1, 1, fill="#3a2410", outline="", state="hidden")
            self.items[f"dflame{i}"] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill="#ffd24a", smooth=True, outline="",
                state="hidden")

        self._flakes = [self._flake(seeded=True) for _ in range(SNOW_N)]
        self._bursts = [[0.0, 0.0, 9.9, 0, 0.0] for _ in range(BURSTS)]
        self._kites = [[self.rng.uniform(200, 1700),
                        self.rng.uniform(150, 500),
                        self.rng.uniform(0, math.tau),
                        self.rng.uniform(0.6, 1.2)] for _ in range(KITE_N)]
        self._shown: str | None = None

    def _flake(self, seeded: bool = False) -> list[float]:
        r = self.rng
        return [r.uniform(-30, 1950),
                r.uniform(90, 1050) if seeded else 80.0,
                r.uniform(30, 75), r.uniform(0, math.tau), r.uniform(4, 10)]

    # --- dispatch --------------------------------------------------------

    def update(self, ctx: Context) -> None:
        if ctx.now >= self._checked:
            self._checked = ctx.now + 30.0
            self._effect = active_effect(force=self.force)
        effect = self._effect
        if effect != self._shown:
            self._hide_all(effect)
            self._shown = effect
        if effect == "snow":
            self._snow(ctx)
        elif effect == "kites":
            self._kites_fly(ctx)
        elif effect == "garba":
            self._garba(ctx)
        elif effect == "holi":
            self._bursts_draw(ctx, GULAL, powder=True)
        elif effect == "tricolor":
            self._bursts_draw(ctx, TRICOLOUR)
        elif effect == "fireworks":
            self._bursts_draw(ctx, BURST_COLOURS)
        elif effect == "diwali":
            self._diyas(ctx)
            self._bursts_draw(ctx, BURST_COLOURS)

    def _hide_all(self, becoming: str | None) -> None:
        c = self.canvas
        for i in range(SNOW_N):
            c.itemconfigure(self.items[f"flake{i}"], state="hidden")
        for b in range(BURSTS):
            self._bursts[b][2] = 9.9
            self._bursts[b][4] = 0.0
            for i in range(SPARKS):
                c.itemconfigure(self.items[f"spark{b}_{i}"], state="hidden")
        for i in range(KITE_N):
            c.itemconfigure(self.items[f"kite{i}"], state="hidden")
            c.itemconfigure(self.items[f"ktail{i}"], state="hidden")
        for i in range(GARBA_N):
            c.itemconfigure(self.items[f"garba{i}"], state="hidden")
        for i in range(DIYA_N):
            for part in ("dbase", "dglow", "dflame"):
                c.itemconfigure(self.items[f"{part}{i}"], state="hidden")
        if becoming in ("fireworks", "tricolor", "holi", "diwali"):
            # In front of everything, cards included: a festival's bursts
            # spent their whole lives hidden behind the UI otherwise, and a
            # few days a year of thin fading sparks over the numbers is the
            # point of having them.
            for b in range(BURSTS):
                for i in range(SPARKS):
                    c.tag_raise(self.items[f"spark{b}_{i}"])

    # --- the effects -----------------------------------------------------

    def _snow(self, ctx: Context) -> None:
        c, g = self.canvas, self.geo
        colour = "#3d4a5c" if ctx.ink.get("light") else "#eef6ff"
        for i, flake in enumerate(self._flakes):
            flake[1] += flake[2] * ctx.dt
            if flake[1] > theme.DESIGN_H + 20:
                self._flakes[i] = flake = self._flake()
            x = flake[0] + math.sin(flake[1] * 0.014 + flake[3]) * 26.0
            rad = flake[4]
            c.coords(self.items[f"flake{i}"],
                     g.x(x - rad), g.y(flake[1] - rad),
                     g.x(x + rad), g.y(flake[1] + rad))
            c.itemconfigure(self.items[f"flake{i}"], fill=colour,
                            state="normal")

    def _bursts_draw(self, ctx: Context, palette: tuple,
                     powder: bool = False) -> None:
        """The shared burst engine. Fireworks are thin streaks that droop;
        Holi powder is fat soft blobs that hang, which is the whole visual
        difference between gunpowder and gulal."""
        c, g = self.canvas, self.geo
        for b, burst in enumerate(self._bursts):
            burst[2] += ctx.dt
            if burst[2] >= 1.5:
                if burst[4] == 0.0:
                    burst[4] = ctx.now + b * 1.7
                if ctx.now < burst[4]:
                    continue
                burst[0] = self.rng.uniform(240, 1680)
                burst[1] = self.rng.uniform(160, 520)
                burst[2] = 0.0
                burst[3] = self.rng.randrange(len(palette))
                burst[4] = ctx.now + self.rng.uniform(1.6, 3.4)
            age = burst[2]
            reach = (150.0 if powder else 190.0) \
                * (1.0 - (1.0 - min(1.0, age / 1.1)) ** 2)
            fade = max(0.0, 1.0 - age / 1.4)
            colour = _mixc(ctx.pal["sky_top"], palette[burst[3]],
                           0.15 + 0.85 * fade)
            for i in range(SPARKS):
                a = i / SPARKS * math.tau
                drop = age * age * (40.0 if powder else 90.0)
                x = burst[0] + math.cos(a) * reach
                y = burst[1] + math.sin(a) * reach * 0.8 + drop
                tail = 2.0 if powder else 10.0 + reach * 0.10
                item = self.items[f"spark{b}_{i}"]
                c.coords(item, g.x(x - math.cos(a) * tail),
                         g.y(y - math.sin(a) * tail * 0.8),
                         g.x(x), g.y(y))
                c.itemconfigure(item, state="normal", fill=colour,
                                width=g.stroke((9.0 if powder else 3.0)
                                               + 3.0 * fade))

    def _kites_fly(self, ctx: Context) -> None:
        c = self.canvas
        for i, kite in enumerate(self._kites):
            kite[2] += ctx.dt * kite[3] * 0.5
            x = kite[0] + math.sin(kite[2]) * 180.0
            y = kite[1] + math.sin(kite[2] * 2.3 + i) * 70.0
            # Nose along the direction of travel.
            vx = math.cos(kite[2]) * 180.0 * 0.5
            vy = math.cos(kite[2] * 2.3 + i) * 70.0 * 1.15
            a = math.atan2(vy, vx)
            cos, sin = math.cos(a), math.sin(a)
            size = 34.0

            def rot(ox, oy):
                return (x + ox * cos - oy * sin, y + ox * sin + oy * cos)

            nose = rot(size, 0)
            top = rot(0, -size * 0.7)
            back = rot(-size * 0.9, 0)
            bot = rot(0, size * 0.7)
            c.coords(self.items[f"kite{i}"], *self._pts([
                nose[0], nose[1], top[0], top[1], back[0], back[1],
                bot[0], bot[1]]))
            c.itemconfigure(self.items[f"kite{i}"], state="normal")
            # The tail streams behind, waving.
            pts = []
            for k in range(5):
                t = k / 4.0
                tx, ty = rot(-size * 0.9 - t * 90.0,
                             math.sin(ctx.clock * 4.0 + i + t * 5.0) * 16.0)
                pts.extend((tx, ty))
            c.coords(self.items[f"ktail{i}"], *self._pts(pts))
            c.itemconfigure(self.items[f"ktail{i}"], state="normal")

    def _garba(self, ctx: Context) -> None:
        """Navratri: a ring of lamps circling the whole screen, the way the
        dance circles the shrine. They pass behind the cards and the face."""
        c = self.canvas
        for i in range(GARBA_N):
            a = ctx.clock * 0.35 + i * (math.tau / GARBA_N)
            x = 960.0 + math.cos(a) * 560.0
            y = 420.0 + math.sin(a) * 260.0
            pulse = 10.0 + 3.0 * math.sin(ctx.clock * 3.0 + i)
            c.coords(self.items[f"garba{i}"],
                     *self._oval(x, y, pulse * 2, pulse * 2))
            c.itemconfigure(self.items[f"garba{i}"], state="normal",
                            fill=_mixc(ctx.pal["sky_top"],
                                       GARBA_COLOURS[i % 3], 0.85))

    def _diyas(self, ctx: Context) -> None:
        """Diwali: a row of lamps along the bottom edge, each flame on its
        own flicker."""
        c = self.canvas
        for i in range(DIYA_N):
            x = 150.0 + i * (1620.0 / (DIYA_N - 1))
            y = 1054.0
            flick = 0.75 + 0.25 * math.sin(ctx.clock * (5.0 + i * 0.7) + i * 2.1)
            c.coords(self.items[f"dglow{i}"],
                     *self._oval(x, y - 18.0, 90.0 * flick, 64.0 * flick))
            c.itemconfigure(self.items[f"dglow{i}"], state="normal",
                            fill=_mixc(ctx.pal["sky_bot"], "#ff9c3e", 0.30))
            c.coords(self.items[f"dbase{i}"], *self._oval(x, y, 56.0, 34.0))
            c.itemconfigure(self.items[f"dbase{i}"], state="normal")
            h = 26.0 * flick
            c.coords(self.items[f"dflame{i}"], *self._pts([
                x - 7.0, y - 8.0, x + 7.0, y - 8.0,
                x + 3.0, y - 8.0 - h * 0.6, x, y - 8.0 - h,
                x - 3.0, y - 8.0 - h * 0.6]))
            c.itemconfigure(self.items[f"dflame{i}"], state="normal",
                            fill=_mixc("#ffd24a", "#ff8c2e", 1.0 - flick))
