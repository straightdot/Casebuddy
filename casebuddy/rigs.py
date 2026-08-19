"""Animated characters: everything the face can be that is not a face.

A rig replaces the buddy's face entirely -- the aura, the yellow ball, the
emoji, all of it -- and draws its own character from canvas primitives, moved
every frame. What stays is everything around it: the mood machine keeps
running (rigs read the same Stress the face did), the caption keeps naming the
mood, the cards keep carrying the numbers.

Two kinds of rig:

    CENTERED    lives where the face lived (Doom face, robot, pet). The aura
                keeps pulsing behind it.
    roaming     owns the whole screen (cat, spider). The aura hides, and the
                rig's items are raised above the cards at build time, because
                a character who walks along the tops of the tiles has to be
                in front of the tiles.

Rigs are pure vectors. No sprites, no fonts, no files -- which is what lets
them recolour with the palette, scale with the geometry, and never be missing
on someone else's machine.

Same drawing economy as the rest of the app: items created once in build(),
then only coords() and itemconfigure() forever after.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import time

from . import theme
from .scenes import Context, _mixc, fan_fraction

# The face's home, mirrored from buddy.py. Not imported from it, because buddy
# imports this module and Python would go around in a circle.
CX, CY, R = 960.0, 386.0, 205.0


class Rig:
    """One character. Subclasses draw; this holds the plumbing."""

    CENTERED = True

    def __init__(self, host) -> None:
        self.host = host
        self.canvas = host.canvas
        self.geo = host.geo
        self.rng = host._rng
        self.cfg = host.cfg
        self.items: dict = {}

    def _pts(self, flat) -> list[float]:
        g = self.geo
        return [g.x(v) if i % 2 == 0 else g.y(v) for i, v in enumerate(flat)]

    def _box(self, x0, y0, x1, y1) -> list[float]:
        g = self.geo
        return [g.x(x0), g.y(y0), g.x(x1), g.y(y1)]

    def _oval(self, cx, cy, w, h) -> list[float]:
        return self._box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

    def fan_fraction(self, ctx: Context) -> float:
        return fan_fraction(ctx, self.cfg)

    def raise_all(self) -> None:
        """Lift every canvas item this rig owns above whatever came later."""
        for value in self.items.values():
            if isinstance(value, int):
                self.canvas.tag_raise(value)

    def caption(self, mood) -> str | None:
        """A caption override, or None to keep the mood's own word."""
        return None

    def build(self) -> None:
        raise NotImplementedError

    def update(self, ctx: Context) -> None:
        raise NotImplementedError


# --- network activity, for the robot's antenna ------------------------------

class _NetMeter:
    """Total bytes per second across real adapters, sampled once a second.

    The collector has no network source and the antenna only needs one number,
    so this asks iphlpapi directly rather than growing the whole pipeline.
    GetIfTable's counters are 32-bit and wrap; a negative delta is one sample
    thrown away, not an error.
    """

    _FIELDS = [("wszName", ctypes.c_wchar * 256)] + [
        (name, ctypes.c_uint32) for name in (
            "dwIndex", "dwType", "dwMtu", "dwSpeed", "dwPhysAddrLen")] + [
        ("bPhysAddr", ctypes.c_ubyte * 8)] + [
        (name, ctypes.c_uint32) for name in (
            "dwAdminStatus", "dwOperStatus", "dwLastChange",
            "dwInOctets", "dwInUcastPkts", "dwInNUcastPkts", "dwInDiscards",
            "dwInErrors", "dwInUnknownProtos", "dwOutOctets", "dwOutUcastPkts",
            "dwOutNUcastPkts", "dwOutDiscards", "dwOutErrors", "dwOutQLen",
            "dwDescrLen")] + [("bDescr", ctypes.c_char * 256)]

    def __init__(self) -> None:
        class Row(ctypes.Structure):
            _fields_ = self._FIELDS

        self._row = Row
        self._last: tuple[float, int] | None = None
        self._at = 0.0
        self.bps = 0.0
        self.dead = False

    def _total(self) -> int | None:
        try:
            size = ctypes.c_ulong(0)
            api = ctypes.windll.iphlpapi
            api.GetIfTable(None, ctypes.byref(size), False)
            buf = ctypes.create_string_buffer(size.value)
            if api.GetIfTable(buf, ctypes.byref(size), False) != 0:
                return None
            count = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint32))[0]
            rows = ctypes.cast(ctypes.byref(buf, 4),
                               ctypes.POINTER(self._row * count))[0]
            # Type 24 is loopback; everything else that is up counts.
            return sum(int(r.dwInOctets) + int(r.dwOutOctets) for r in rows
                       if r.dwType != 24)
        except Exception:
            return None

    def sample(self, now: float) -> float:
        if self.dead or now - self._at < 1.0:
            return self.bps
        self._at = now
        total = self._total()
        if total is None:
            self.dead = True            # never works or always works
            return 0.0
        if self._last is not None:
            span = now - self._last[0]
            delta = total - self._last[1]
            if span > 0 and delta >= 0:
                self.bps = delta / span
        self._last = (now, total)
        return self.bps


# --- the Doom-style status face ----------------------------------------------

SKIN = "#d9a066"
HAIR = "#5b3a24"
BLOOD = "#a3212e"


class DoomFace(Rig):
    """The classic corner-of-the-HUD face, promoted to the whole panel.

    Health is the inverse of stress. Idle he smirks; as the machine heats he
    sweats, bruises, then bleeds, and past ninety percent he is one hit from
    the fight ending. The eyes glance around on their own, exactly as the
    original did, because a face that stares dead ahead reads as a picture
    rather than a person.
    """

    def build(self) -> None:
        c, g = self.canvas, self.geo
        self.items["head"] = c.create_oval(
            *self._oval(CX, CY, 280, 330), fill=SKIN,
            outline=_mixc(SKIN, "#000000", 0.45), width=g.stroke(6))
        # A flat-top haircut: a chord across the head's upper quarter.
        self.items["hair"] = c.create_arc(
            *self._oval(CX, CY, 280, 330), start=38, extent=104,
            style="chord", fill=HAIR, outline="")
        # Damage, from nothing to a bad night. Bruises are recoloured toward
        # the skin to fade them in, because Tk has no alpha.
        for i, (bx, by, bw, bh) in enumerate(
                ((-72, -18, 74, 52), (66, 44, 64, 44), (-8, 96, 88, 40))):
            self.items[f"bruise{i}"] = c.create_oval(
                *self._oval(CX + bx, CY + by, bw, bh), fill=SKIN, outline="")
        for i in range(3):
            self.items[f"blood{i}"] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill=BLOOD, outline="", state="hidden")
        for side in ("l", "r"):
            sign = -1.0 if side == "l" else 1.0
            self.items[f"white_{side}"] = c.create_oval(
                *self._oval(CX + sign * 58, CY - 48, 64, 44),
                fill="#f5efe2", outline=_mixc(SKIN, "#000000", 0.5),
                width=g.stroke(4))
            self.items[f"pupil_{side}"] = c.create_oval(
                *self._oval(CX + sign * 58, CY - 48, 20, 22),
                fill="#1d2733", outline="")
            self.items[f"brow_{side}"] = c.create_line(
                0, 0, 1, 1, fill=HAIR, width=g.stroke(12), capstyle="round")
            self.items[f"ko_{side}"] = c.create_line(
                0, 0, 1, 1, fill="#1d2733", width=g.stroke(8),
                capstyle="round", state="hidden")
            self.items[f"ko2_{side}"] = c.create_line(
                0, 0, 1, 1, fill="#1d2733", width=g.stroke(8),
                capstyle="round", state="hidden")
            self.items[f"sweat_{side}"] = c.create_oval(
                0, 0, 1, 1, fill="#7dd3fc", outline="", state="hidden")
        # Three mouths, one shown at a time: smirk line, gritted teeth,
        # open grimace.
        self.items["teeth"] = c.create_rectangle(
            *self._oval(CX, CY + 92, 128, 40), fill="#ece4d4",
            outline=_mixc(SKIN, "#000000", 0.5), width=g.stroke(4),
            state="hidden")
        for i in range(3):
            self.items[f"gap{i}"] = c.create_line(
                0, 0, 1, 1, fill=_mixc(SKIN, "#000000", 0.4),
                width=g.stroke(3), state="hidden")
        self.items["grimace"] = c.create_oval(
            *self._oval(CX, CY + 96, 110, 64), fill="#3a1610",
            outline="#1f0c08", width=g.stroke(4), state="hidden")
        self.items["smirk"] = c.create_line(
            0, 0, 1, 1, fill="#7a4a2e", width=g.stroke(10),
            smooth=True, capstyle="round")
        self._glance = (0.0, 0.0)
        self._glance_at = 0.0
        self._twitch_at = 0.0
        self._twitch_until = 0.0
        self._drops = [[0.0, 0.0] for _ in range(2)]

    def update(self, ctx: Context) -> None:
        c = self.canvas
        level = ctx.level
        offline = ctx.mood.key == "offline"

        # The head takes the beating: reddens past halfway, wobbles at the
        # top -- and every so often shakes itself off, damage or not.
        if ctx.now >= self._twitch_at:
            self._twitch_at = ctx.now + self.rng.uniform(11.0, 24.0)
            self._twitch_until = ctx.now + 0.6
        wobble = math.sin(ctx.clock * 6.0) * 6.0 * max(0.0, level - 0.8) * 5.0
        if ctx.now < self._twitch_until:
            wobble += math.sin(ctx.now * 34.0) * 7.0
        fx, fy = CX + wobble, CY + math.sin(ctx.clock * 0.9) * 6.0
        skin = _mixc(SKIN, "#c4452e", max(0.0, level - 0.5) * 1.4)
        c.coords(self.items["head"], *self._oval(fx, fy, 280, 330))
        c.itemconfigure(self.items["head"], fill=skin)
        c.coords(self.items["hair"], *self._oval(fx, fy, 280, 330))

        # Bruises fade in one by one across 0.55..0.9.
        for i in range(3):
            start = 0.55 + i * 0.12
            vis = max(0.0, min(1.0, (level - start) / 0.10))
            bx, by, bw, bh = ((-72, -18, 74, 52), (66, 44, 64, 44),
                              (-8, 96, 88, 40))[i]
            c.coords(self.items[f"bruise{i}"],
                     *self._oval(fx + bx, fy + by, bw, bh))
            c.itemconfigure(self.items[f"bruise{i}"],
                            fill=_mixc(skin, "#5b2340", vis * 0.8))
        # Blood runs from the hairline once things are genuinely bad.
        for i, off in enumerate((-60.0, 10.0, 74.0)):
            item = self.items[f"blood{i}"]
            run = max(0.0, level - (0.72 + i * 0.07)) * 340.0
            if run < 4.0 or offline:
                c.itemconfigure(item, state="hidden")
                continue
            x = fx + off
            top = fy - 148.0
            c.coords(item, *self._pts([
                x - 7, top, x + 7, top, x + 4, top + run * 0.7,
                x, top + run, x - 4, top + run * 0.7]))
            c.itemconfigure(item, state="normal")

        # Glance: pick a new direction every couple of seconds.
        if ctx.now >= self._glance_at:
            self._glance_at = ctx.now + self.rng.uniform(1.2, 3.0)
            self._glance = (self.rng.uniform(-16, 16), self.rng.uniform(-8, 10))
        gx, gy = self._glance
        squint = 1.0 - 0.55 * max(0.0, level - 0.6) / 0.4
        for side, sign in (("l", -1.0), ("r", 1.0)):
            ex, ey = fx + sign * 58, fy - 48
            c.coords(self.items[f"white_{side}"],
                     *self._oval(ex, ey, 64, 44 * squint))
            c.coords(self.items[f"pupil_{side}"],
                     *self._oval(ex + gx * 0.8, ey + gy * 0.5, 20, 22 * squint))
            hidden = offline
            for name in (f"white_{side}", f"pupil_{side}"):
                c.itemconfigure(self.items[name],
                                state="hidden" if hidden else "normal")
            for i, name in enumerate((f"ko_{side}", f"ko2_{side}")):
                c.itemconfigure(self.items[name],
                                state="normal" if hidden else "hidden")
                if hidden:
                    d = 20.0
                    s1 = -1.0 if i == 0 else 1.0
                    c.coords(self.items[name],
                             *self._pts([ex - d, ey - d * s1,
                                         ex + d, ey + d * s1]))
            # Brows sink from raised to furious.
            tilt = 10.0 - 34.0 * level
            c.coords(self.items[f"brow_{side}"],
                     *self._pts([ex - sign * 34, ey - 36 - max(0.0, tilt),
                                 ex + sign * 34, ey - 36 + min(0.0, tilt)]))

        # Sweat past the warn line, driven by heat, not load: a cool machine
        # working hard grits its teeth dry.
        sweating = ctx.heat > 0.55 and not offline
        for i, side in enumerate(("l", "r")):
            item = self.items[f"sweat_{side}"]
            if not sweating:
                c.itemconfigure(item, state="hidden")
                continue
            drop = self._drops[i]
            drop[0] += ctx.dt * 90.0
            if drop[0] > 120.0 or drop[0] <= 0.0:
                drop[0] = self.rng.uniform(0.0, 40.0)
                drop[1] = self.rng.uniform(-30.0, 30.0)
            sign = -1.0 if side == "l" else 1.0
            c.coords(item, *self._oval(fx + sign * 148, fy - 60 + drop[1]
                                       + drop[0], 16, 22))
            c.itemconfigure(item, state="normal")

        # One mouth of three.
        smirk = level < 0.45 and not offline
        teeth = 0.45 <= level < 0.80 and not offline
        grim = level >= 0.80 and not offline
        c.itemconfigure(self.items["smirk"],
                        state="normal" if (smirk or offline) else "hidden")
        c.itemconfigure(self.items["teeth"], state="normal" if teeth else "hidden")
        for i in range(3):
            c.itemconfigure(self.items[f"gap{i}"],
                            state="normal" if teeth else "hidden")
        c.itemconfigure(self.items["grimace"], state="normal" if grim else "hidden")
        if smirk or offline:
            curve = 0.0 if offline else (14.0 - level * 20.0)
            mx, my = fx, fy + 96
            c.coords(self.items["smirk"], *self._pts([
                mx - 56, my + curve * 0.4, mx - 20, my - curve * 0.5,
                mx + 26, my - curve, mx + 60, my - curve * 0.2]))
        elif teeth:
            c.coords(self.items["teeth"], *self._oval(fx, fy + 92, 128, 40))
            for i, off in enumerate((-32.0, 0.0, 32.0)):
                c.coords(self.items[f"gap{i}"],
                         *self._pts([fx + off, fy + 74, fx + off, fy + 110]))
        else:
            breathe = 1.0 + 0.10 * math.sin(ctx.clock * 2.4 * math.tau)
            c.coords(self.items["grimace"],
                     *self._oval(fx, fy + 96, 110, 64 * breathe))


# --- the little robot --------------------------------------------------------

class Robot(Rig):
    """A service bot wearing the theme's accent. The antenna is its network
    light, the mouth is a load bar in disguise, and past the warn line its
    vents start venting -- because a robot does not sweat, it exhausts."""

    def build(self) -> None:
        c, g = self.canvas, self.geo
        mk = self.items
        mk["shadow"] = c.create_oval(*self._oval(CX, 640, 320, 44),
                                     fill="#000000", outline="")
        mk["mast"] = c.create_line(0, 0, 1, 1, fill="#7d8aa3",
                                   width=g.stroke(7))
        mk["bulb"] = c.create_oval(0, 0, 1, 1, fill="#ff5c47", outline="")
        for name, (w, h) in (("torso", (300, 190)), ("head", (250, 170))):
            mk[name] = c.create_rectangle(
                0, 0, 1, 1, fill="#8e9bb1", outline="#3d4657",
                width=g.stroke(6))
        mk["visor"] = c.create_rectangle(0, 0, 1, 1, fill="#10151d",
                                         outline="#2a3140", width=g.stroke(4))
        for side in ("l", "r"):
            mk[f"eye_{side}"] = c.create_rectangle(
                0, 0, 1, 1, fill="#7ce7ff", outline="")
            mk[f"arm_{side}"] = c.create_line(
                0, 0, 1, 1, fill="#7d8aa3", width=g.stroke(16),
                capstyle="round")
            mk[f"hand_{side}"] = c.create_oval(0, 0, 1, 1, fill="#5d6a83",
                                               outline="")
            mk[f"vent_{side}"] = c.create_rectangle(
                0, 0, 1, 1, fill="#3d4657", outline="")
        for i in range(5):
            mk[f"led{i}"] = c.create_rectangle(0, 0, 1, 1, fill="#22303f",
                                               outline="")
        for i in range(4):
            mk[f"puff{i}"] = c.create_line(
                0, 0, 1, 1, fill="#ffffff", width=g.stroke(10), smooth=True,
                capstyle="round", state="hidden")
        self._net = _NetMeter()
        self._puffs = [self.rng.uniform(0, 40) for _ in range(4)]
        self._scan_at = 0.0
        self._scan_until = 0.0

    def update(self, ctx: Context) -> None:
        c = self.canvas
        level = ctx.level

        # Hover: a slow bob that stiffens as the machine works, like a fan
        # spinning up. The shadow shrinks as the body rises.
        bob = math.sin(ctx.clock * (0.5 + level) * math.tau) * (14.0 - 6.0 * level)
        jit = self.rng.uniform(-1.0, 1.0) * 3.0 * max(0.0, level - 0.75) * 4.0
        x, y = CX + jit, CY + 30 + bob

        body = _mixc("#8e9bb1", ctx.pal["accent"], 0.35)
        trim = _mixc(body, "#000000", 0.55)
        c.itemconfigure(self.items["torso"], fill=body, outline=trim)
        c.itemconfigure(self.items["head"], fill=_mixc(body, "#ffffff", 0.10),
                        outline=trim)

        c.coords(self.items["shadow"],
                 *self._oval(CX, 646, 300 - bob * 4.0, 40 - bob * 1.5))
        c.itemconfigure(self.items["shadow"],
                        fill=_mixc(ctx.pal["sky_bot"], "#000000", 0.5))
        c.coords(self.items["torso"], *self._oval(x, y + 118, 300, 190))
        c.coords(self.items["head"], *self._oval(x, y - 60, 250, 170))
        c.coords(self.items["visor"], *self._oval(x, y - 66, 198, 96))

        # Antenna: the bulb blinks at network speed. Solid quiet red when the
        # wire is silent, urgent green flicker under real traffic.
        mast_top = y - 145 - 60
        c.coords(self.items["mast"], *self._pts([x, y - 145, x, mast_top]))
        bps = self._net.sample(ctx.now)
        activity = min(1.0, (bps / 5e6) ** 0.5) if bps > 1024 else 0.0
        period = 2.6 - 2.35 * activity
        lit = (ctx.clock % period) < period * 0.5
        c.coords(self.items["bulb"], *self._oval(x, mast_top - 12, 26, 26))
        c.itemconfigure(self.items["bulb"],
                        fill=(_mixc("#2eea8d", "#b7ffd9", activity * 0.5)
                              if lit and activity > 0.0 else
                              ("#ff5c47" if lit else
                               _mixc(ctx.pal["sky_top"], "#682018", 0.6))))

        # Eyes: tall and bright when fresh, flattening to tired slits as the
        # stress climbs. Blink handled by the same squash, and every so often
        # both eyes sweep the room -- a scan pass, because it is a robot.
        blink = 0.15 if (ctx.clock % 4.7) < 0.14 else 1.0
        eh = max(6.0, 56.0 * (1.0 - 0.6 * level) * blink)
        glow = _mixc("#7ce7ff", "#ff8163", max(0.0, level - 0.4) * 1.6)
        if ctx.now >= self._scan_at:
            self._scan_at = ctx.now + self.rng.uniform(10.0, 22.0)
            self._scan_until = ctx.now + 1.6
        look = 0.0
        if ctx.now < self._scan_until:
            look = math.sin((self._scan_until - ctx.now) * math.tau / 1.6) * 13.0
        for side, sign in (("l", -1.0), ("r", 1.0)):
            c.coords(self.items[f"eye_{side}"],
                     *self._oval(x + sign * 52 + look, y - 66, 44, eh))
            c.itemconfigure(self.items[f"eye_{side}"], fill=glow)
            # Arms trail the hover; at high stress they brace forward.
            ax = x + sign * 150
            reach = 40.0 * max(0.0, level - 0.7)
            hx = ax + sign * (26.0 - reach * 0.4)
            hy = y + 190 - reach
            c.coords(self.items[f"arm_{side}"],
                     *self._pts([ax, y + 60, hx, hy]))
            c.coords(self.items[f"hand_{side}"], *self._oval(hx, hy, 40, 40))
            c.itemconfigure(self.items[f"arm_{side}"], fill=trim)
            c.itemconfigure(self.items[f"hand_{side}"], fill=body)
            c.coords(self.items[f"vent_{side}"],
                     *self._oval(x + sign * 118, y + 96, 30, 90))
            c.itemconfigure(self.items[f"vent_{side}"], fill=trim)

        # The mouth is five LEDs; the load lights them left to right.
        lit_n = int(round(ctx.load * 5.0))
        for i in range(5):
            c.coords(self.items[f"led{i}"],
                     *self._oval(x - 72 + i * 36, y + 4, 26, 14))
            c.itemconfigure(self.items[f"led{i}"],
                            fill=(glow if i < lit_n else
                                  _mixc("#22303f", ctx.pal["sky_bot"], 0.3)))

        # Exhaust when hot: little steam wisps out of the vents.
        venting = ctx.heat > 0.55
        for i in range(4):
            item = self.items[f"puff{i}"]
            if not venting:
                c.itemconfigure(item, state="hidden")
                continue
            self._puffs[i] = (self._puffs[i] + ctx.dt * 60.0) % 70.0
            rise = self._puffs[i]
            sign = -1.0 if i % 2 == 0 else 1.0
            px = x + sign * (135 + rise * 0.8)
            py = y + 96 - rise
            pts = []
            for k in range(4):
                t = k / 3.0
                pts.extend((px + math.sin(ctx.clock * 3 + i + t * 3) * 8 * t,
                            py - 30 * t))
            c.coords(item, *self._pts(pts))
            c.itemconfigure(item, state="normal",
                            fill=_mixc(ctx.pal["sky_top"], "#ffffff",
                                       0.5 * (1.0 - rise / 70.0)))


# --- the tamagotchi ----------------------------------------------------------

PET_FILE = "pet.json"
HEART = "#ff5d8f"


class Tamagotchi(Rig):
    """A pet the machine keeps alive. Uptime feeds it, age grows it, and how
    you treated the machine yesterday decides its mood today.

    Everything it remembers lives in pet.json next to config.json:

        born          when it hatched (epoch seconds)
        fed_minutes   lifetime minutes of uptime it has been fed
        today / today_minutes    the current day's feeding
        hot           {"YYYY-MM-DD": minutes above the warn line}
        last_seen     epoch of the last save, for detecting neglect

    Thermal abuse is charged to the calendar day it happened; the pet wakes up
    sulky the day AFTER a bad day, which is both kinder and more legible than
    punishing mid-session.
    """

    GROWTH = ((0, 110.0), (1, 126.0), (3, 148.0), (7, 168.0), (14, 184.0))

    def build(self) -> None:
        c, g = self.canvas, self.geo
        self._path = os.path.join(
            str(self.cfg.get("_config_dir", "") or "."), PET_FILE)
        self._state = self._load()
        self._saved_at = time.monotonic()
        self._hot_carry = 0.0
        self._fed_carry = 0.0
        self._hearts: list[list[float]] = []
        self._hop_at = 0.0
        self._hop_until = 0.0

        self.items["blob"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, fill="#8ee6c9", smooth=True,
            outline=_mixc("#8ee6c9", "#000000", 0.45), width=g.stroke(6))
        for side in ("l", "r"):
            self.items[f"eye_{side}"] = c.create_oval(
                0, 0, 1, 1, fill="#173028", outline="")
            self.items[f"lid_{side}"] = c.create_line(
                0, 0, 1, 1, fill="#173028", width=g.stroke(9),
                capstyle="round", state="hidden")
            self.items[f"cheek_{side}"] = c.create_oval(
                0, 0, 1, 1, fill="#ffb7c9", outline="", state="hidden")
        self.items["mouth"] = c.create_line(
            0, 0, 1, 1, fill="#173028", width=g.stroke(9), smooth=True,
            capstyle="round")
        self.items["mouth_o"] = c.create_oval(
            0, 0, 1, 1, fill="#3a1620", outline="", state="hidden")
        # Grows a sprout at three days and a crown at seven. Milestones,
        # because a pet that never changes is a screensaver.
        self.items["stem"] = c.create_line(
            0, 0, 1, 1, fill="#3f9e5f", width=g.stroke(7), state="hidden")
        self.items["leaf"] = c.create_oval(
            0, 0, 1, 1, fill="#54c47a", outline="", state="hidden")
        self.items["crown"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, fill="#ffd24a",
            outline=_mixc("#ffd24a", "#000000", 0.4), width=g.stroke(4),
            state="hidden")
        for i in range(3):
            self.items[f"heart{i}"] = c.create_text(
                0, 0, text="♥", fill=HEART, state="hidden",
                font=self.host.f_quip, anchor="center")

    # --- memory -------------------------------------------------------

    def _load(self) -> dict:
        state = {"born": time.time(), "fed_minutes": 0.0, "today": "",
                 "today_minutes": 0.0, "hot": {}, "last_seen": time.time()}
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                got = json.load(fh)
            if isinstance(got, dict):
                state.update({k: got[k] for k in state if k in got})
        except (OSError, ValueError):
            pass
        return state

    def _save(self) -> None:
        try:
            self._state["last_seen"] = time.time()
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass

    def _account(self, ctx: Context) -> None:
        """Feed the pet with this frame, and bill any overheating to today."""
        today = time.strftime("%Y-%m-%d")
        if self._state.get("today") != today:
            self._state["today"] = today
            self._state["today_minutes"] = 0.0
            # Keep a week of history; the verdict only ever reads yesterday.
            hot = self._state.setdefault("hot", {})
            for day in sorted(hot)[:-7]:
                del hot[day]
        self._fed_carry += ctx.dt / 60.0
        if self._fed_carry >= 0.25:
            before = self._state["fed_minutes"]
            self._state["fed_minutes"] = before + self._fed_carry
            self._state["today_minutes"] += self._fed_carry
            # A heart every full hour of lifetime feeding.
            if int(before // 60.0) != int(self._state["fed_minutes"] // 60.0):
                self._burst_hearts()
            self._fed_carry = 0.0
        if ctx.heat > 0.6:
            self._hot_carry += ctx.dt / 60.0
            if self._hot_carry >= 0.25:
                hot = self._state.setdefault("hot", {})
                hot[today] = float(hot.get(today, 0.0)) + self._hot_carry
                self._hot_carry = 0.0
        if time.monotonic() - self._saved_at > 120.0:
            self._saved_at = time.monotonic()
            self._save()

    def _burst_hearts(self) -> None:
        for i in range(3):
            self._hearts.append([CX + self.rng.uniform(-90, 90),
                                 CY - 40.0, self.rng.uniform(0.0, 0.5), i])

    # --- verdicts -----------------------------------------------------

    def age_days(self) -> int:
        return max(0, int((time.time() - float(self._state.get("born", 0)))
                          // 86400))

    def _yesterday_hot(self) -> float:
        day = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        return float(self._state.get("hot", {}).get(day, 0.0))

    def _neglected(self) -> bool:
        return time.time() - float(self._state.get("last_seen", 0)) > 2 * 86400 \
            and float(self._state.get("today_minutes", 0.0)) < 30.0

    def _radius(self) -> float:
        age = self.age_days()
        size = self.GROWTH[0][1]
        for day, r in self.GROWTH:
            if age >= day:
                size = r
        return size

    def temper(self, ctx: Context) -> str:
        if ctx.level > 0.80:
            return "panic"
        if ctx.level > 0.55:
            return "worried"
        if self._neglected():
            return "hungry"
        if self._yesterday_hot() > 30.0:
            return "sulky"
        if ctx.daylight < 0.15 and ctx.load < 0.2:
            return "asleep"
        return "happy"

    def caption(self, mood) -> str | None:
        word = {"panic": "TOO HOT", "worried": "UNEASY", "hungry": "HUNGRY",
                "sulky": "SULKING", "asleep": "ASLEEP",
                "happy": "CONTENT"}[self._temper_now]
        return f"DAY {self.age_days()} · {word}"

    # --- drawing --------------------------------------------------------

    def update(self, ctx: Context) -> None:
        c, g = self.canvas, self.geo
        self._account(ctx)
        temper = self._temper_now = self.temper(ctx)

        r = self._radius()
        breathe = 1.0 + 0.05 * math.sin(ctx.clock * (0.4 if temper == "asleep"
                                                     else 1.1) * math.tau)
        squash = 1.0 + 0.16 * max(0.0, ctx.level - 0.7)
        jig = self.rng.uniform(-3, 3) if temper == "panic" else 0.0
        # A happy pet hops now and then: three quick bounces, then settles.
        hop = 0.0
        if temper == "happy":
            if ctx.now >= self._hop_at:
                self._hop_at = ctx.now + self.rng.uniform(14.0, 30.0)
                self._hop_until = ctx.now + 0.9
            if ctx.now < self._hop_until:
                t = (0.9 - (self._hop_until - ctx.now)) / 0.9
                hop = abs(math.sin(t * math.pi * 3.0)) * 26.0
        x, y = CX + jig, CY + 40 - r * 0.2 - hop

        # The blob: twelve points around an ellipse, each wobbling on its own
        # phase so the outline is always alive.
        pts = []
        for k in range(12):
            a = k / 12.0 * math.tau
            wob = 1.0 + 0.05 * math.sin(ctx.clock * 2.0 + k * 1.7)
            pts.extend((x + math.cos(a) * r * 1.06 * squash * wob,
                        y + math.sin(a) * r * breathe * wob))
        c.coords(self.items["blob"], *self._pts(pts))
        body = {"panic": "#ff9c7a", "worried": "#ffd27a", "hungry": "#bcd0c9",
                "sulky": "#9aa8d4", "asleep": "#7fc4b2",
                "happy": "#8ee6c9"}[temper]
        c.itemconfigure(self.items["blob"], fill=body,
                        outline=_mixc(body, "#000000", 0.45))

        asleep = temper == "asleep"
        for side, sign in (("l", -1.0), ("r", 1.0)):
            ex, ey = x + sign * r * 0.38, y - r * 0.22
            c.itemconfigure(self.items[f"eye_{side}"],
                            state="hidden" if asleep else "normal")
            c.itemconfigure(self.items[f"lid_{side}"],
                            state="normal" if asleep else "hidden")
            if asleep:
                c.coords(self.items[f"lid_{side}"],
                         *self._pts([ex - 18, ey + 4, ex, ey + 10,
                                     ex + 18, ey + 4]))
            else:
                flat = 0.4 if temper == "sulky" else 1.0
                c.coords(self.items[f"eye_{side}"],
                         *self._oval(ex, ey, 26, 34 * flat))
            cheek = temper == "happy"
            c.itemconfigure(self.items[f"cheek_{side}"],
                            state="normal" if cheek else "hidden")
            if cheek:
                c.coords(self.items[f"cheek_{side}"],
                         *self._oval(x + sign * r * 0.62, y + r * 0.08, 40, 24))

        my = y + r * 0.30
        open_mouth = temper in ("hungry", "panic")
        c.itemconfigure(self.items["mouth"],
                        state="hidden" if open_mouth else "normal")
        c.itemconfigure(self.items["mouth_o"],
                        state="normal" if open_mouth else "hidden")
        if open_mouth:
            c.coords(self.items["mouth_o"], *self._oval(x, my, 52, 44))
        else:
            curve = {"happy": 16.0, "asleep": 8.0, "worried": -8.0,
                     "sulky": -14.0, "hungry": 0.0, "panic": 0.0}[temper]
            c.coords(self.items["mouth"], *self._pts([
                x - 34, my - curve * 0.5, x, my + curve * 0.5,
                x + 34, my - curve * 0.5]))

        # Milestones.
        age = self.age_days()
        top = y - r * breathe
        show_sprout = age >= 3 and age < 7
        for name in ("stem", "leaf"):
            c.itemconfigure(self.items[name],
                            state="normal" if show_sprout else "hidden")
        if show_sprout:
            sway = math.sin(ctx.clock * 1.4) * 6.0
            c.coords(self.items["stem"],
                     *self._pts([x, top + 6, x + sway, top - 34]))
            c.coords(self.items["leaf"],
                     *self._oval(x + sway + 14, top - 40, 34, 20))
        crown = age >= 7
        c.itemconfigure(self.items["crown"],
                        state="normal" if crown else "hidden")
        if crown:
            c.coords(self.items["crown"], *self._pts([
                x - 44, top + 4, x - 44, top - 30, x - 22, top - 12,
                x, top - 36, x + 22, top - 12, x + 44, top - 30,
                x + 44, top + 4]))

        # Hearts drift up and die quietly.
        alive = []
        for heart in self._hearts:
            heart[1] -= ctx.dt * 60.0
            heart[2] += ctx.dt
            if heart[2] < 2.2:
                alive.append(heart)
        self._hearts = alive[:3]
        for i in range(3):
            item = self.items[f"heart{i}"]
            if i < len(self._hearts):
                hx, hy, _t, _k = self._hearts[i]
                c.coords(item, g.x(hx), g.y(hy))
                c.itemconfigure(item, state="normal", fill=HEART)
            else:
                c.itemconfigure(item, state="hidden")


# --- the cat -------------------------------------------------------------

CAT = "#4b5568"
CAT_DARK = "#343c4b"
# Where the cat lives when there are no cards to live on: the header rule.
RULE_Y_FALLBACK = 102.0


class Cat(Rig):
    """A cat living on top of the stat cards.

    Idle it curls up and sleeps on one; working it patrols along the card
    tops, hopping between them; and the moment the machine crosses the warn
    line it does the one thing cats are for -- calmly pushes something off
    the edge. The tail never stops, and its wag speed is the fan speed,
    because both are how hard the box is trying to stay calm.
    """

    CENTERED = False

    def build(self) -> None:
        c, g = self.canvas, self.geo
        tiles = self.host.tile_rects()
        # Walkable roofs. No cards at all leaves the header rule as home.
        self._roofs = ([(x + 20.0, x + w - 20.0, y) for x, y, w, _h, _m in tiles]
                       or [(300.0, 1620.0, RULE_Y_FALLBACK)])
        self._tile = self.rng.randrange(len(self._roofs))
        roof = self._roofs[self._tile]
        self._x = self.rng.uniform(roof[0], roof[1])
        self._facing = 1.0
        self._state = "sit"
        self._since = 0.0
        self._restless = 15.0
        self._stretch_until = 0.0
        self._groom_at = 0.0
        self._groom_until = 0.0
        self._flick_at = 0.0
        self._flick_until = 0.0
        self._flick_side = "l"
        self._hop = None           # (x0, y0, x1, y1, started, target tile)
        self._knock = None         # [x, y, vx, vy, age]
        self._heat_prev = 0.0
        self._paw_until = 0.0

        for name in ("tail",):
            self.items[name] = c.create_line(
                0, 0, 1, 1, fill=CAT, width=g.stroke(16), smooth=True,
                capstyle="round")
        for i in range(4):
            self.items[f"leg{i}"] = c.create_line(
                0, 0, 1, 1, fill=CAT_DARK, width=g.stroke(13),
                capstyle="round")
        self.items["body"] = c.create_oval(0, 0, 1, 1, fill=CAT,
                                           outline=CAT_DARK, width=g.stroke(4))
        self.items["head"] = c.create_oval(0, 0, 1, 1, fill=CAT,
                                           outline=CAT_DARK, width=g.stroke(4))
        for side in ("l", "r"):
            self.items[f"ear_{side}"] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill=CAT, outline=CAT_DARK,
                width=g.stroke(3))
            self.items[f"eye_{side}"] = c.create_oval(
                0, 0, 1, 1, fill="#ffd24a", outline="")
            self.items[f"shut_{side}"] = c.create_line(
                0, 0, 1, 1, fill="#141922", width=g.stroke(5),
                capstyle="round", state="hidden")
        self.items["block"] = c.create_rectangle(
            0, 0, 1, 1, fill="#c9a15a", outline="#8a6c3a",
            width=g.stroke(3), state="hidden")

    # --- brain ---------------------------------------------------------

    def _pool(self, ctx: Context) -> tuple:
        level = ctx.level
        if level < 0.20:
            return ("sleep", "sit")
        if level < 0.42:
            return ("sit", "walk")
        return ("walk",)

    def _think(self, ctx: Context) -> None:
        # Same restlessness as the spider: within a tier the cat stirs every
        # so often -- wakes to sit, pads around, settles back down -- because
        # even a sleeping cat moves more than a sleeping screenshot.
        pool = self._pool(ctx)
        switch = None
        if self._hop is None:
            if self._state not in pool:
                if ctx.now - self._since > 3.0:
                    switch = (pool[0] if len(pool) == 1
                              or self.rng.random() < 0.7 else pool[1])
            elif len(pool) > 1 and ctx.now - self._since > self._restless:
                switch = next(s for s in pool if s != self._state)
        if switch is not None:
            # Waking up starts with the obligatory long stretch.
            if self._state == "sleep" and switch != "sleep":
                self._stretch_until = ctx.now + 1.1
            self._state = switch
            self._since = ctx.now
            self._restless = self.rng.uniform(12.0, 28.0)
        # Grooming passes while sitting, and ear flicks whenever awake.
        if self._state == "sit" and ctx.now >= self._groom_at:
            self._groom_at = ctx.now + self.rng.uniform(9.0, 18.0)
            self._groom_until = ctx.now + 1.4
        if self._state != "sleep" and ctx.now >= self._flick_at:
            self._flick_at = ctx.now + self.rng.uniform(5.0, 13.0)
            self._flick_until = ctx.now + 0.3
            self._flick_side = "l" if self.rng.random() < 0.5 else "r"

        # The warn line crossed upward: shove something off the roof. Cats do
        # not panic; the block does.
        heat = ctx.heat
        if heat > 0.6 >= self._heat_prev and self._knock is None \
                and self._state != "sleep":
            self._knock = [self._x + self._facing * 60.0,
                           self._roofs[self._tile][2] - 20.0,
                           self._facing * 140.0, 0.0, 0.0]
            self._paw_until = ctx.now + 0.5
        self._heat_prev = heat

    def _stroll(self, ctx: Context) -> None:
        if self._hop is not None:
            return
        if self._state != "walk":
            return
        lo, hi, _y = self._roofs[self._tile]
        pace = (90.0 + 260.0 * max(0.0, ctx.level - 0.42)) * ctx.dt
        self._x += self._facing * pace
        if not lo <= self._x <= hi:
            self._x = max(lo, min(hi, self._x))
            # At the edge: usually turn, sometimes leap to another card.
            if len(self._roofs) > 1 and self.rng.random() < 0.35:
                other = self.rng.randrange(len(self._roofs))
                while other == self._tile:
                    other = self.rng.randrange(len(self._roofs))
                tlo, thi, ty = self._roofs[other]
                tx = self.rng.uniform(tlo, thi)
                self._hop = [self._x, self._roofs[self._tile][2],
                             tx, ty, ctx.now, other]
            else:
                self._facing = -self._facing

    # --- body ----------------------------------------------------------

    def update(self, ctx: Context) -> None:
        c = self.canvas
        self._think(ctx)
        self._stroll(ctx)

        y = self._roofs[self._tile][2]
        x = self._x
        airborne = 0.0
        if self._hop is not None:
            x0, y0, x1, y1, started, target = self._hop
            t = min(1.0, (ctx.now - started) / 0.7)
            x = x0 + (x1 - x0) * t
            y = y0 + (y1 - y0) * t - math.sin(t * math.pi) * 140.0
            airborne = math.sin(t * math.pi)
            self._facing = 1.0 if x1 >= x0 else -1.0
            if t >= 1.0:
                self._tile = target
                self._x = x1
                self._hop = None

        sleeping = self._state == "sleep" and self._hop is None
        f = self._facing
        suit, dark = CAT, CAT_DARK
        stretching = ctx.now < self._stretch_until
        grooming = ctx.now < self._groom_until and self._state == "sit"

        # Body: long when walking, round when curled, LONG when stretching --
        # front low, rear high, the full wake-up bow.
        if sleeping:
            bw, bh = 150.0, 84.0
            by = y - bh / 2 + 6
            hx, hy = x - f * 40.0, y - 34.0
        elif stretching:
            bw, bh = 190.0, 56.0
            by = y - bh / 2 - 10.0
            hx, hy = x + f * 104.0, y - 34.0
        else:
            bw, bh = 140.0, 74.0
            by = y - bh / 2 - 18.0
            hx, hy = x + f * 74.0, y - 78.0
        if grooming:
            # Head dips toward the shoulder it is washing.
            hx, hy = x + f * 40.0, y - 52.0                 + math.sin(ctx.clock * 6.0) * 6.0
        c.coords(self.items["body"], *self._oval(x, by, bw, bh))
        c.coords(self.items["head"], *self._oval(hx, hy, 74.0, 66.0))

        for side, sign in (("l", -1.0), ("r", 1.0)):
            ex = hx + sign * 22.0
            # A flicked ear folds its tip for a fraction of a second.
            tip = 48.0
            if side == self._flick_side and ctx.now < self._flick_until:
                tip = 30.0 + math.sin(ctx.now * 60.0) * 6.0
            c.coords(self.items[f"ear_{side}"], *self._pts([
                ex - 12, hy - 24, ex + 12, hy - 24, ex + sign * 4, hy - tip]))
            eye_open = not sleeping
            c.itemconfigure(self.items[f"eye_{side}"],
                            state="normal" if eye_open else "hidden")
            c.itemconfigure(self.items[f"shut_{side}"],
                            state="hidden" if eye_open else "normal")
            if eye_open:
                c.coords(self.items[f"eye_{side}"],
                         *self._oval(hx + sign * 16, hy - 4, 12,
                                     14 if ctx.level > 0.42 else 8))
            else:
                c.coords(self.items[f"shut_{side}"],
                         *self._pts([hx + sign * 22, hy - 2,
                                     hx + sign * 8, hy + 2]))

        # Legs: tucked asleep, planted sitting, swinging on the walk.
        phase = ctx.clock * (6.0 + 10.0 * ctx.level)
        for i in range(4):
            item = self.items[f"leg{i}"]
            if sleeping:
                c.itemconfigure(item, state="hidden")
                continue
            c.itemconfigure(item, state="normal")
            ox = (-42.0, -16.0, 18.0, 46.0)[i]
            swing = math.sin(phase + i * 1.7) * (14.0 if self._state == "walk"
                                                 or airborne else 0.0)
            top_y = by + bh * 0.2
            foot_y = y - airborne * 26.0
            # The paw raised mid-shove, or licking a paw mid-groom.
            grooming_paw = (ctx.now < self._groom_until
                            and self._state == "sit"
                            and i == (3 if f > 0 else 0))
            if (ctx.now < self._paw_until or grooming_paw)                     and i == (3 if f > 0 else 0):
                foot_y = y - 42.0
                swing = f * (16.0 + math.sin(ctx.clock * 7.0) * 10.0
                             if grooming_paw else 30.0)
            c.coords(item, *self._pts([x + ox, top_y,
                                       x + ox + swing, foot_y]))

        # The tail is the fan gauge nobody asked for.
        wag = ctx.clock * (2.0 + 11.0 * self.fan_fraction(ctx))
        base_x, base_y = x - f * bw * 0.48, by - 8.0
        pts = []
        for k in range(5):
            t = k / 4.0
            pts.extend((base_x - f * t * 80.0,
                        base_y - t * 70.0
                        + math.sin(wag + t * 2.6) * 22.0 * (0.3 + t)))
        if sleeping:
            pts = []
            for k in range(5):
                t = k / 4.0
                a = math.pi * (0.2 + t * 0.9)
                pts.extend((x + math.cos(a) * bw * 0.52,
                            y - 6 + math.sin(a) * -14.0))
        c.coords(self.items["tail"], *self._pts(pts))
        c.itemconfigure(self.items["tail"], fill=suit)
        c.itemconfigure(self.items["body"], fill=suit, outline=dark)
        c.itemconfigure(self.items["head"], fill=suit, outline=dark)

        # The shoved block: slides, then discovers gravity.
        if self._knock is not None:
            k = self._knock
            k[4] += ctx.dt
            lo, hi, roof_y = self._roofs[self._tile]
            k[0] += k[2] * ctx.dt
            if not lo - 10 <= k[0] <= hi + 10:
                k[3] += 900.0 * ctx.dt
                k[1] += k[3] * ctx.dt
            if k[1] > theme.DESIGN_H + 40 or k[4] > 4.0:
                self._knock = None
                c.itemconfigure(self.items["block"], state="hidden")
            else:
                c.coords(self.items["block"],
                         *self._oval(k[0], k[1], 30, 30))
                c.itemconfigure(self.items["block"], state="normal")



# --- the spider hero -------------------------------------------------------

SUIT = "#e23636"
SUIT_NIGHT = "#8c1f1f"
SUIT_BLUE = "#2b4bd7"
BLUE_NIGHT = "#16255e"
HEAD_R = 50.0          # chibi: the head is nearly the size of the body
LIMB_W = 16
WEB = "#dfe8f2"

# The chest emblem, as offsets from its centre: a fat body with four legs a
# side, simplified until it survives being six pixels tall on the panel.
EMBLEM = ((0, -14), (4, -4), (14, -10), (5, 0), (14, 10), (4, 5), (0, 16),
          (-4, 5), (-14, 10), (-5, 0), (-14, -10), (-4, -4))

# The mask: radial threads fanning over the top of the head, crossed by two
# rings. Angles in degrees, 180..360 being the upper half on a canvas.
MASK_RAYS = (185, 215, 245, 270, 295, 325, 355)
MASK_RINGS = (20.0, 36.0)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


class Spider(Rig):
    """A twelve-joint hero who treats the layout as a city.

    The stress bands choose his verb: hanging upside-down while the machine
    idles, perched on a card, crawling the header at half load, swinging
    between web anchors as it climbs -- swing rate from the load, swing width
    from the real wind outside -- and past ninety percent he squares up and
    throws hands at the hottest tile, which visibly takes the hits. A heat
    spike gets a thwip: one web line to the offending card and a splat over
    its gauge. Rain sends him under an overhang, the night darkens the suit,
    and lightning makes him flinch, because it should.
    """

    CENTERED = False

    JOINTS = ("head", "chest", "hip", "hand_l", "hand_r", "foot_l", "foot_r",
              "elbow_l", "elbow_r", "knee_l", "knee_r", "eye_off")

    def build(self) -> None:
        c, g = self.canvas, self.geo
        tiles = self.host.tile_rects()
        self._tiles = tiles
        # Web anchors along the header rule, inboard enough that a full-width
        # swing arc stays clear of both card columns.
        self._anchors = [(x, 112.0) for x in (680.0, 960.0, 1240.0)]
        self._anchor = 1
        self._anchor_at = 0.0     # its own clock: resetting the state dwell
        self._state = "hang"      # here kept fight from ever engaging
        self._since = 0.0
        self._restless = 12.0     # seconds before an idle verb goes stale
        self._swing_phase = 0.0
        self._heat_prev = 0.0
        self._thwip_until = 0.0
        self._thwip_tile = 0
        self._shake_until = 0.0
        # Cards he may rest on: middle and bottom rows only. The chibi head
        # stands about 240 design-px above a roof, and on the top row that
        # put it through the header and off the screen entirely.
        self._resting = [i for i, t in enumerate(tiles) if t[1] > 220.0]             or list(range(len(tiles)))
        self._perch = self.rng.choice(self._resting) if tiles else 0
        self._pose: dict[str, tuple[float, float]] = {}

        # Creation order is depth order: thread behind everything, then legs,
        # the body over their roots, arms over the body, the head over the
        # shoulders, the mask pattern and eyes on the head, and the web
        # effects over the lot.
        self.items["thread"] = c.create_line(
            0, 0, 1, 1, fill=WEB, width=g.stroke(3), state="hidden")
        self.items["hammock"] = c.create_line(
            0, 0, 1, 1, fill=WEB, width=g.stroke(4), smooth=True,
            state="hidden")
        for side in ("l", "r"):
            self.items[f"leg_up_{side}"] = c.create_line(
                0, 0, 1, 1, fill=SUIT_BLUE, width=g.stroke(18),
                capstyle="round")
            self.items[f"leg_lo_{side}"] = c.create_line(
                0, 0, 1, 1, fill=SUIT, width=g.stroke(18), capstyle="round")
            self.items[f"foot_{side}"] = c.create_oval(
                0, 0, 1, 1, fill=SUIT, outline="", width=0)
        self.items["pelvis"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, fill=SUIT_BLUE, smooth=True, outline="")
        self.items["torso"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, fill=SUIT, smooth=True,
            outline="#43101a", width=g.stroke(4))
        self.items["emblem"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, fill="#160a0d", outline="")
        for side in ("l", "r"):
            self.items[f"arm_up_{side}"] = c.create_line(
                0, 0, 1, 1, fill=SUIT_BLUE, width=g.stroke(LIMB_W),
                capstyle="round")
            self.items[f"arm_lo_{side}"] = c.create_line(
                0, 0, 1, 1, fill=SUIT, width=g.stroke(LIMB_W),
                capstyle="round")
            self.items[f"hand_{side}"] = c.create_oval(
                0, 0, 1, 1, fill=SUIT, outline="", width=0)
        self.items["head"] = c.create_oval(
            0, 0, 1, 1, fill=SUIT, outline="#43101a", width=g.stroke(5))
        for i in range(len(MASK_RAYS)):
            self.items[f"ray{i}"] = c.create_line(
                0, 0, 1, 1, fill="#8f1620", width=g.stroke(2.5))
        for i in range(len(MASK_RINGS)):
            self.items[f"ring{i}"] = c.create_line(
                0, 0, 1, 1, fill="#8f1620", width=g.stroke(2.5), smooth=True)
        for side in ("l", "r"):
            self.items[f"eye_{side}"] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill="#f4f7fb", smooth=True,
                outline="#10151d", width=g.stroke(4))
        self.items["thwip"] = c.create_line(
            0, 0, 1, 1, fill=WEB, width=g.stroke(5), state="hidden")
        self.items["splat"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, fill=WEB, outline="", state="hidden")

    # --- choosing the verb ----------------------------------------------

    def _raining(self, ctx: Context) -> bool:
        return str(getattr(ctx.weather, "condition", "")) in (
            "rain", "drizzle", "thunder")

    def _pool(self, ctx: Context) -> tuple:
        """The verbs this stress tier allows. More than one at the calm end,
        because a hero pinned to one perch for a whole idle evening reads as
        a screensaver that crashed -- he gets restless and rotates."""
        level = ctx.level
        if str(getattr(ctx.mood, "key", "")) == "sleepy":
            return ("nap",)
        if self._raining(ctx) and level < 0.50:
            return ("shelter",)
        if level >= 0.85:
            return ("fight",)
        if level >= 0.52:
            return ("swing",)
        if level >= 0.30:
            return ("perch", "swing", "lounge")
        if level >= 0.16:
            return ("perch", "lounge", "hang")
        return ("hang", "lounge", "perch")

    def _hot_tile(self, ctx: Context) -> int:
        """Index of the stat card showing whatever is driving the stress."""
        driver = str(getattr(ctx.stress, "driver", "HEAT"))
        if driver == "HEAT":
            cpu = float(ctx.vitals.get("cpu_temp", 0.0) or 0.0)
            gpu = float(ctx.vitals.get("gpu_temp", 0.0) or 0.0)
            want = "calc:cpu_temp" if cpu >= gpu else "calc:gpu_temp"
        else:
            cpu = float(ctx.vitals.get("cpu_load", 0.0) or 0.0)
            gpu = float(ctx.vitals.get("gpu_load", 0.0) or 0.0)
            want = "calc:cpu_load" if cpu >= gpu else "calc:gpu_load"
        for index, tile in enumerate(self._tiles):
            if tile[4] == want:
                return index
        return 0

    def _think(self, ctx: Context) -> None:
        pool = self._pool(ctx)
        switch = None
        if self._state not in pool:
            # The machine moved tiers: settle into the new tier's lead verb
            # (usually), after the dwell that keeps a flickering reading from
            # rebuilding him twice a second.
            if ctx.now - self._since > 4.0:
                switch = (pool[0] if len(pool) == 1 or self.rng.random() < 0.6
                          else self.rng.choice(pool[1:]))
        elif len(pool) > 1 and ctx.now - self._since > self._restless:
            # Same tier, held too long: do something else from it.
            switch = self.rng.choice([s for s in pool if s != self._state])
        if switch is not None:
            if self._state == "fight":
                # Put the furniture back before walking away from it.
                for index in range(len(self._tiles)):
                    self.host.nudge_tile(index, 0.0, 0.0)
            self._state = switch
            self._since = ctx.now
            self._restless = self.rng.uniform(9.0, 20.0)
            if switch in ("perch", "lounge") and self._tiles:
                self._perch = self.rng.choice(self._resting)
        # A heat spike is worth a thwip whatever he is doing.
        heat = ctx.heat
        if heat > 0.6 >= self._heat_prev and self._tiles:
            self._thwip_until = ctx.now + 1.6
            self._thwip_tile = self._hot_tile(ctx)
        self._heat_prev = heat

    # --- poses -----------------------------------------------------------
    #
    # Each returns absolute design-space points for all twelve joints. The
    # drawn skeleton is smoothed toward the target every frame, so switching
    # verbs is a movement rather than a cut.

    def _pose_hang(self, ctx: Context) -> dict:
        ax, ay = self._anchors[self._anchor]
        sway = math.sin(ctx.clock * 0.7) * 26.0
        hip = (ax + sway, ay + 210.0)
        chest = (hip[0] + sway * 0.1, hip[1] + 78.0)
        head = (chest[0], chest[1] + 62.0)
        dangle = math.sin(ctx.clock * 1.1) * 10.0
        return {
            "head": head, "chest": chest, "hip": hip,
            "hand_l": (chest[0] - 44.0 + dangle, chest[1] + 118.0),
            "hand_r": (chest[0] + 44.0 - dangle, chest[1] + 116.0),
            "foot_l": (hip[0] - 26.0, hip[1] - 96.0),
            "foot_r": (hip[0] + 26.0, hip[1] - 92.0),
            "_thread": (ax, ay, hip[0], hip[1] - 40.0),
        }

    def _pose_perch(self, ctx: Context) -> dict:
        if not self._tiles:
            return self._pose_hang(ctx)
        x, y, w, _h, _m = self._tiles[self._perch]
        px = x + w * 0.5
        py = y
        breathe = math.sin(ctx.clock * 1.6) * 4.0
        hip = (px, py - 74.0 + breathe)
        chest = (px, hip[1] - 58.0)
        head = (chest[0], chest[1] - 52.0)
        return {
            "head": head, "chest": chest, "hip": hip,
            "hand_l": (px - 34.0, py - 6.0),
            "hand_r": (px + 34.0, py - 6.0),
            "foot_l": (px - 52.0, py),
            "foot_r": (px + 52.0, py),
        }

    def _pose_lounge(self, ctx: Context) -> dict:
        """Flat on his back on a card roof, one knee up, an arm behind the
        head: the picture of a hero off the clock. Replaced the header crawl,
        whose oversized head never fit the strip it crawled along."""
        if not self._tiles:
            return self._pose_hang(ctx)
        x, y, w, _h, _m = self._tiles[self._perch]
        px = x + w * 0.5
        f = 1.0 if px < 960.0 else -1.0          # head points toward centre
        breathe = math.sin(ctx.clock * 1.0) * 3.0
        hip = (px - f * 10.0, y - 26.0)
        chest = (px + f * 36.0, y - 36.0 + breathe * 0.5)
        head = (px + f * 78.0, y - 44.0 + breathe)
        return {
            "head": head, "chest": chest, "hip": hip,
            "hand_l": (px + f * 98.0, y - 66.0),      # behind the head
            "hand_r": (px + f * 12.0, y - 44.0),      # on the belly
            "foot_l": (px - f * 96.0, y - 6.0),       # stretched out
            "foot_r": (px - f * 34.0, y - 30.0),      # the knee up
        }

    def _hammock_anchors(self) -> tuple:
        """The inner-top corners of the two top side cards."""
        left = [(t[0] + t[2], t[1]) for t in self._tiles
                if t[0] + t[2] / 2.0 < 960.0]
        right = [(t[0], t[1]) for t in self._tiles
                 if t[0] + t[2] / 2.0 >= 960.0]
        la = min(left, key=lambda pt: pt[1]) if left else (556.0, 176.0)
        ra = min(right, key=lambda pt: pt[1]) if right else (1364.0, 176.0)
        return la, ra

    def _pose_nap(self, ctx: Context) -> dict:
        """NAPPING: he rigs a web hammock between the two top cards and
        sleeps in the sag, swaying, hands folded on his chest."""
        la, ra = self._hammock_anchors()
        cx = (la[0] + ra[0]) / 2.0 + math.sin(ctx.clock * 0.5) * 16.0
        cy = max(la[1], ra[1]) + 118.0 + math.sin(ctx.clock * 1.1) * 7.0
        breathe = math.sin(ctx.clock * 0.9) * 4.0
        hip = (cx + 38.0, cy - 18.0)
        chest = (cx - 24.0, cy - 24.0 + breathe * 0.5)
        head = (cx - 74.0, cy - 26.0 + breathe)
        return {
            "head": head, "chest": chest, "hip": hip,
            "hand_l": (cx - 20.0, cy - 40.0 + breathe),
            "hand_r": (cx - 2.0, cy - 36.0 + breathe),
            "foot_l": (cx + 92.0, cy - 22.0),
            "foot_r": (cx + 108.0, cy - 12.0),
            "_hammock": (la[0], la[1], cx, cy + 10.0, ra[0], ra[1]),
        }

    def _pose_swing(self, ctx: Context) -> dict:
        wind = float(getattr(ctx.weather, "wind_kph", 0.0) or 0.0)
        omega = 1.0 + 2.4 * ctx.level
        width = 0.45 + min(0.55, wind / 70.0)
        self._swing_phase += ctx.dt * omega
        theta = math.sin(self._swing_phase * math.tau * 0.5) * width
        # At each extreme, web the next anchor in the direction of travel.
        if abs(theta) > width * 0.985:
            going = 1 if theta < 0 else -1
            want = max(0, min(len(self._anchors) - 1, self._anchor + going))
            if want != self._anchor and ctx.now - self._anchor_at > 1.2:
                self._anchor = want
                self._anchor_at = ctx.now
        ax, ay = self._anchors[self._anchor]
        length = 330.0
        hip = (ax + math.sin(theta) * length, ay + math.cos(theta) * length)
        lean = math.sin(theta) * 0.7
        chest = (hip[0] + lean * 30.0, hip[1] - 74.0)
        head = (chest[0] + lean * 22.0, chest[1] - 52.0)
        up_x, up_y = ax - chest[0], ay - chest[1]
        mag = math.hypot(up_x, up_y) or 1.0
        up_x, up_y = up_x / mag, up_y / mag
        return {
            "head": head, "chest": chest, "hip": hip,
            "hand_l": (chest[0] + up_x * 96.0 - 14.0, chest[1] + up_y * 96.0),
            "hand_r": (chest[0] + up_x * 96.0 + 14.0, chest[1] + up_y * 96.0),
            "foot_l": (hip[0] - lean * 90.0 - 38.0, hip[1] + 86.0),
            "foot_r": (hip[0] - lean * 90.0 + 42.0, hip[1] + 98.0),
            "_thread": (ax, ay, chest[0] + up_x * 80.0, chest[1] + up_y * 80.0),
        }

    def _pose_fight(self, ctx: Context) -> dict:
        target = self._hot_tile(ctx)
        x, y, w, h, _m = self._tiles[target] if self._tiles else (
            760.0, 300.0, 400.0, 190.0, "")
        # Stand just inboard of the tile; `toward` is the punching direction.
        on_right = x + w / 2.0 > 960.0
        px = (x - 120.0) if on_right else (x + w + 120.0)
        toward = 1.0 if on_right else -1.0
        py = y + h * 0.55
        beat = ctx.clock * (3.0 + 4.0 * ctx.level)
        jab = math.sin(beat * math.tau) > 0.0
        punch = abs(math.sin(beat * math.tau)) ** 0.5
        hip = (px, py + 50.0)
        chest = (px + toward * (10.0 + punch * 16.0), py - 20.0)
        head = (chest[0] + toward * 16.0, chest[1] - 52.0)
        reach = 120.0 * punch
        hands = {
            "hand_l": (chest[0] + toward * (30.0 + (reach if jab else 0.0)),
                       chest[1] + (6.0 if jab else 24.0)),
            "hand_r": (chest[0] + toward * (30.0 + (0.0 if jab else reach)),
                       chest[1] + (24.0 if jab else 6.0)),
        }
        # Each landing punch rattles the tile.
        if punch > 0.9 and ctx.now > self._shake_until:
            self._shake_until = ctx.now + 0.22
        offset = 0.0
        if ctx.now < self._shake_until:
            offset = math.sin(ctx.now * 60.0) * 4.0
        self.host.nudge_tile(target, toward * max(0.0, offset), 0.0)
        return {
            "head": head, "chest": chest, "hip": hip,
            **hands,
            "foot_l": (px - toward * 34.0, py + 128.0),
            "foot_r": (px + toward * 20.0, py + 128.0),
        }

    def _pose_shelter(self, ctx: Context) -> dict:
        if not self._tiles:
            return self._pose_hang(ctx)
        x, y, w, h, _m = self._tiles[0]
        # Tucked under the card's outer bottom corner, out of the rain.
        px = x + w * 0.5
        py = y + h + 96.0
        shiver = math.sin(ctx.clock * 8.0) * 2.0
        hip = (px + shiver, py - 40.0)
        chest = (px, hip[1] - 44.0)
        head = (chest[0], chest[1] - 44.0)
        return {
            "head": head, "chest": chest, "hip": hip,
            "hand_l": (px - 20.0, chest[1] + 10.0),
            "hand_r": (px + 20.0, chest[1] + 10.0),
            "foot_l": (px - 30.0, py),
            "foot_r": (px + 30.0, py),
        }

    # --- drawing ----------------------------------------------------------

    def update(self, ctx: Context) -> None:
        c = self.canvas
        self._think(ctx)
        pose = {
            "hang": self._pose_hang, "perch": self._pose_perch,
            "lounge": self._pose_lounge, "nap": self._pose_nap,
            "swing": self._pose_swing, "fight": self._pose_fight,
            "shelter": self._pose_shelter,
        }[self._state](ctx)

        # Lightning: he flinches, limbs thrown wide for the frame.
        if getattr(self.host, "_flash_until", 0.0) > ctx.now:
            for key in ("hand_l", "hand_r", "foot_l", "foot_r"):
                px, py = pose[key]
                sign = -1.0 if key.endswith("_l") else 1.0
                pose[key] = (px + sign * 30.0, py - 20.0)

        # Ease the skeleton toward the target so verb changes are movement.
        step = 1.0 - math.exp(-ctx.dt / 0.10)
        drawn = {}
        for key in ("head", "chest", "hip", "hand_l", "hand_r",
                    "foot_l", "foot_r"):
            tx, ty = pose[key]
            ox, oy = self._pose.get(key, (tx, ty))
            drawn[key] = (_lerp(ox, tx, step), _lerp(oy, ty, step))
        self._pose = drawn

        self._draw_body(ctx, drawn)

        # Web thread and hammock, when this pose earns them.
        thread = pose.get("_thread")
        if thread:
            c.coords(self.items["thread"], *self._pts(list(thread)))
            c.itemconfigure(self.items["thread"], state="normal")
        else:
            c.itemconfigure(self.items["thread"], state="hidden")
        hammock = pose.get("_hammock")
        if hammock:
            c.coords(self.items["hammock"], *self._pts(list(hammock)))
            c.itemconfigure(self.items["hammock"], state="normal")
        else:
            c.itemconfigure(self.items["hammock"], state="hidden")

        self._draw_thwip(ctx, drawn["hand_r"])

    def _draw_body(self, ctx: Context, drawn: dict) -> None:
        """The chibi puppet: an oversized masked head on a small two-tone
        body. Red torso, gloves, boots and mask; blue sleeves, thighs and
        pelvis; the mask carries its web of rays and rings and the chest its
        emblem. Everything is placed off the same seven smoothed joints the
        stick figure used, so every pose and verb carries over unchanged."""
        c = self.canvas
        night = 1.0 - ctx.daylight
        suit = _mixc(SUIT, SUIT_NIGHT, night * 0.8)
        blue = _mixc(SUIT_BLUE, BLUE_NIGHT, night * 0.8)
        dark = _mixc(suit, "#000000", 0.5)

        head = drawn["head"]
        chest, hip = drawn["chest"], drawn["hip"]

        # The torso axis, and its perpendicular: the body rotates with the
        # pose (upright, inverted on the thread, horizontal on the crawl),
        # so its shape is built from these rather than from fixed offsets.
        ax, ay = hip[0] - chest[0], hip[1] - chest[1]
        mag = math.hypot(ax, ay) or 1.0
        nx, ny = ax / mag, ay / mag              # chest -> hip
        px, py = -ny, nx                          # across the shoulders

        # Limbs first (they draw beneath the torso items created after them):
        # two segments through a bent joint, sleeve/thigh in blue, glove/boot
        # in red, a round fist or foot capping the tip.
        for name, root, tip, out, width, cap in (
                ("leg_l", (hip[0] + px * -16, hip[1] + py * -16),
                 drawn["foot_l"], -1.0, 18, 15.0),
                ("leg_r", (hip[0] + px * 16, hip[1] + py * 16),
                 drawn["foot_r"], 1.0, 18, 15.0),
                ("arm_l", (chest[0] + px * -26, chest[1] + py * -26),
                 drawn["hand_l"], -1.0, LIMB_W, 13.0),
                ("arm_r", (chest[0] + px * 26, chest[1] + py * 26),
                 drawn["hand_r"], 1.0, LIMB_W, 13.0)):
            dx, dy = tip[0] - root[0], tip[1] - root[1]
            span = math.hypot(dx, dy) or 1.0
            jx = (root[0] + tip[0]) / 2.0 + (-dy / span) * 24.0 * out
            jy = (root[1] + tip[1]) / 2.0 + (dx / span) * 24.0 * out
            kind, side = name.split("_")
            upper = self.items[f"{kind}_up_{side}"]
            lower = self.items[f"{kind}_lo_{side}"]
            c.coords(upper, *self._pts([root[0], root[1], jx, jy]))
            c.coords(lower, *self._pts([jx, jy, tip[0], tip[1]]))
            c.itemconfigure(upper, fill=blue)
            c.itemconfigure(lower, fill=suit)
            end = self.items[f"{'foot' if kind == 'leg' else 'hand'}_{side}"]
            c.coords(end, *self._oval(tip[0], tip[1], cap * 2, cap * 2))
            c.itemconfigure(end, fill=suit)

        # Pelvis, then the torso capsule over it, shoulders wider than the
        # waist, then the emblem sitting high on the chest.
        c.coords(self.items["pelvis"], *self._pts([
            hip[0] + px * 30, hip[1] + py * 30,
            hip[0] + nx * 26 + px * 20, hip[1] + ny * 26 + py * 20,
            hip[0] + nx * 26 - px * 20, hip[1] + ny * 26 - py * 20,
            hip[0] - px * 30, hip[1] - py * 30]))
        c.itemconfigure(self.items["pelvis"], fill=blue)
        c.coords(self.items["torso"], *self._pts([
            chest[0] - nx * 14 + px * 26, chest[1] - ny * 14 + py * 26,
            chest[0] + px * 38, chest[1] + py * 38,
            hip[0] + px * 28, hip[1] + py * 28,
            hip[0] + nx * 8, hip[1] + ny * 8,
            hip[0] - px * 28, hip[1] - py * 28,
            chest[0] - px * 38, chest[1] - py * 38,
            chest[0] - nx * 14 - px * 26, chest[1] - ny * 14 - py * 26]))
        c.itemconfigure(self.items["torso"], fill=suit, outline=dark)
        ex, ey = chest[0] + nx * 20, chest[1] + ny * 20
        c.coords(self.items["emblem"],
                 *self._pts([v for ox, oy in EMBLEM
                             for v in (ex + ox * 0.9, ey + oy * 0.9)]))

        # The head: nearly body-sized, masked, expressive only through the
        # eyes -- which is the whole trick of the character.
        hx, hy = head
        c.coords(self.items["head"],
                 *self._oval(hx, hy, HEAD_R * 2.0, HEAD_R * 1.9))
        c.itemconfigure(self.items["head"], fill=suit, outline=dark)

        thread_ink = _mixc("#8f1620", "#000000", night * 0.4)
        wx, wy = hx, hy - 5.0
        for i, angle in enumerate(MASK_RAYS):
            a = math.radians(angle)
            c.coords(self.items[f"ray{i}"], *self._pts([
                wx, wy,
                wx + math.cos(a) * HEAD_R * 0.94,
                wy + math.sin(a) * HEAD_R * 0.86]))
            c.itemconfigure(self.items[f"ray{i}"], fill=thread_ink)
        for i, radius in enumerate(MASK_RINGS):
            pts = []
            for angle in range(180, 361, 30):
                a = math.radians(angle)
                pts.extend((wx + math.cos(a) * radius,
                            wy + math.sin(a) * radius * 0.9))
            c.coords(self.items[f"ring{i}"], *self._pts(pts))
            c.itemconfigure(self.items[f"ring{i}"], fill=thread_ink)

        # Two huge teardrop eyes, tilted with the direction of travel.
        lean = (chest[0] - head[0]) * 0.08
        for side, sign in (("l", -1.0), ("r", 1.0)):
            bx, by = hx + sign * 8 - lean, hy + 8
            c.coords(self.items[f"eye_{side}"], *self._pts([
                bx + sign * 4, by + 12,
                bx + sign * 3, by - 8,
                bx + sign * 22, by - 20,
                bx + sign * 37, by - 4,
                bx + sign * 24, by + 12]))

    def _draw_thwip(self, ctx: Context, hand: tuple) -> None:
        c = self.canvas
        if ctx.now >= self._thwip_until or not self._tiles:
            c.itemconfigure(self.items["thwip"], state="hidden")
            c.itemconfigure(self.items["splat"], state="hidden")
            return
        x, y, w, h, _m = self._tiles[self._thwip_tile]
        tx, ty = x + w / 2.0, y + h / 2.0
        left = self._thwip_until - ctx.now
        # First quarter second the line extends; then the splat holds.
        t = min(1.0, (1.6 - left) / 0.25)
        c.coords(self.items["thwip"],
                 *self._pts([hand[0], hand[1],
                             _lerp(hand[0], tx, t), _lerp(hand[1], ty, t)]))
        c.itemconfigure(self.items["thwip"], state="normal")
        if t >= 1.0:
            pts = []
            for k in range(16):
                a = k / 16.0 * math.tau
                radius = 46.0 if k % 2 == 0 else 20.0
                pts.extend((tx + math.cos(a) * radius,
                            ty + math.sin(a) * radius * 0.8))
            c.coords(self.items["splat"], *self._pts(pts))
            c.itemconfigure(self.items["splat"], state="normal")
        else:
            c.itemconfigure(self.items["splat"], state="hidden")


# --- the starship ------------------------------------------------------------

# The hull, pointing +x, origin at the centre of mass. Rotated per frame.
HULL = ((104, 0), (44, -17), (-30, -24), (-64, -13), (-64, 13), (-30, 24),
        (44, 17))
CANOPY = ((56, -6), (26, -11), (10, 0), (26, 11), (56, 6))
WING_T = ((-2, -15), (-44, -52), (-64, -47), (-32, -12))
WING_B = ((-2, 15), (-44, 52), (-64, 47), (-32, 12))
GUN_T = ((58, -13), (102, -13))
GUN_B = ((58, 13), (102, 13))
NOSE = 104.0

# The ship seen from BEHIND, for the warp tiers: a wide wedge, a dorsal
# peak, and three engine pods facing the viewer. Offsets from its centre.
REAR_HULL = ((0, -57), (46, -19), (116, 5), (70, 32), (-70, 32),
             (-116, 5), (-46, -19))
REAR_PODS = ((-54, 11, 20.0, "#6ea8ff"), (0, 3, 27.0, "#e8f4ff"),
             (54, 11, 20.0, "#b78cff"))
BOLT_SPEED = 1500.0
BOLT_N = 3


class Ship(Rig):
    """A little starship that treats the machine as its weather.

    At rest it holds station in the middle, thrusters barely breathing. As
    the load rises it flies patrol loops, faster and wider, afterburners
    lengthening with the work -- and past eighty-five percent it swings its
    patrol toward whichever tile is causing the trouble and strafes it with
    laser bolts. Each hit flashes on the tile and knocks it sideways. A heat
    spike earns a volley wherever the ship happens to be. Pair it with the
    starfield backdrop: the stars streak with the same load that lights the
    engines, which is what makes it read as one machine.
    """

    CENTERED = False

    POOLS = (
        (0.20, ("hover", "drift")),
        (0.85, ("figure8", "orbit", "lane")),
        (9.99, ("strafe", "barrage")),
    )

    def build(self) -> None:
        c, g = self.canvas, self.geo
        self._tiles = self.host.tile_rects()
        self._x, self._y = CX, CY
        self._heading = 0.0
        self._phase = self.rng.uniform(0, math.tau)
        self._mode = "hover"
        self._since = 0.0
        self._restless = 10.0
        self._scale = 1.0
        self._drift = [CX, CY, self.rng.uniform(0, math.tau)]
        self._bolts = [[0.0, 0.0, 0.0, 0.0, 2.0, 0] for _ in range(BOLT_N)]
        self._fire_at = 0.0
        self._impact_until = 0.0
        self._impact_at = (0.0, 0.0)
        self._impact_tile = 0
        self._heat_prev = 0.0
        self._volley = 0

        for i in range(BOLT_N):
            self.items[f"bolt{i}"] = c.create_line(
                0, 0, 1, 1, fill="#7ce7ff", width=g.stroke(6),
                capstyle="round", state="hidden")
        self.items["glow"] = c.create_oval(0, 0, 1, 1, fill="#3a2410",
                                           outline="")
        for name in ("flame_t", "flame_b"):
            self.items[name] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill="#ff8c2e", smooth=True, outline="")
        for name, colour in (("wing_t", "#5d6a83"), ("wing_b", "#5d6a83"),
                             ("hull", "#9aa7bd")):
            self.items[name] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill=colour, smooth=True,
                outline="#2a3140", width=g.stroke(4))
        for name in ("gun_t", "gun_b"):
            self.items[name] = c.create_line(
                0, 0, 1, 1, fill="#3d4657", width=g.stroke(7),
                capstyle="round")
        self.items["canopy"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, fill="#7ce7ff", smooth=True,
            outline="#134b5e", width=g.stroke(3))
        self.items["strobe"] = c.create_oval(0, 0, 1, 1, fill="#ff3d55",
                                             outline="", state="hidden")
        # The rear view, hidden until a warp tier wants it.
        for k in range(len(REAR_PODS)):
            self.items[f"rhalo{k}"] = c.create_oval(
                0, 0, 1, 1, fill="#1a2a55", outline="", state="hidden")
        self.items["rhull"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, fill="#2a3140", smooth=True,
            outline="#10151d", width=g.stroke(4), state="hidden")
        for k in range(len(REAR_PODS)):
            self.items[f"rcore{k}"] = c.create_oval(
                0, 0, 1, 1, fill="#e8f4ff", outline="", state="hidden")
        self.items["flash"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, fill="#cfe9ff", outline="", state="hidden")

    # --- flight plan -----------------------------------------------------

    def _hot_tile(self, ctx: Context) -> int:
        driver = str(getattr(ctx.stress, "driver", "LOAD"))
        if driver == "HEAT":
            cpu = float(ctx.vitals.get("cpu_temp", 0.0) or 0.0)
            gpu = float(ctx.vitals.get("gpu_temp", 0.0) or 0.0)
            want = "calc:cpu_temp" if cpu >= gpu else "calc:gpu_temp"
        else:
            cpu = float(ctx.vitals.get("cpu_load", 0.0) or 0.0)
            gpu = float(ctx.vitals.get("gpu_load", 0.0) or 0.0)
            want = "calc:cpu_load" if cpu >= gpu else "calc:gpu_load"
        for index, tile in enumerate(self._tiles):
            if tile[4] == want:
                return index
        return 0

    def _pool(self, ctx: Context) -> tuple:
        """Flight modes by mood tier. CHILLING is local flying; WORKING
        recedes into the star stream (the stars already rush toward the
        viewer, so a small ship parked at their origin reads as speed);
        SWEATING is the same at hyper; MELTING is the gun run; NAPPING sets
        it down on the mood word to sleep."""
        if str(getattr(ctx.mood, "key", "")) == "sleepy":
            return ("land",)
        level = ctx.level
        if level < 0.42:
            return ("hover", "drift", "figure8", "orbit", "lane")
        if level < 0.66:
            return ("warp",)
        if level < 0.86:
            return ("hyper",)
        return ("strafe", "barrage")

    def _think(self, ctx: Context) -> None:
        """Same restlessness as the spider: rotate within the tier, so the
        patrol is a figure-eight one minute and a lazy orbit the next."""
        pool = self._pool(ctx)
        switch = None
        if self._mode not in pool:
            if ctx.now - self._since > 3.0:
                switch = (pool[0] if self.rng.random() < 0.6
                          else self.rng.choice(pool))
        elif len(pool) > 1 and ctx.now - self._since > self._restless:
            switch = self.rng.choice([m for m in pool if m != self._mode])
        if switch is not None:
            self._mode = switch
            self._since = ctx.now
            self._restless = self.rng.uniform(8.0, 16.0)
            if switch == "drift":
                self._drift = [self._x, self._y,
                               self.rng.uniform(0, math.tau)]

    def _steer(self, ctx: Context) -> tuple:
        """(x, y, burn, forced aim) for this frame's flight mode."""
        level = ctx.level
        mode = self._mode
        if mode == "hover":
            return (CX + math.sin(ctx.clock * 0.5) * 30.0,
                    CY + math.sin(ctx.clock * 0.8) * 22.0, 0.06, None)
        if mode == "land":
            # A vertical touchdown on the mood word: nose up all the way,
            # retro-burning until the tail settles onto the letters.
            settling = abs(self._y - 560.0) > 24.0
            return (CX, 560.0, 0.35 if settling else 0.0, -math.pi / 2.0)
        if mode in ("warp", "hyper"):
            # Seen from behind, low centre, flying into the tunnel whose
            # mouth the starfield lights above it. Weave sells the piloting;
            # the stars sell the speed.
            weave = 26.0 if mode == "warp" else 12.0
            return (CX + math.sin(ctx.clock * 0.7) * weave * 2.2,
                    556.0 + math.sin(ctx.clock * 1.1) * weave,
                    0.9 if mode == "warp" else 1.3, None)
        if mode == "drift":
            # Engines nearly cold, wandering wherever momentum takes it,
            # bouncing gently off the edges of the open sky.
            d = self._drift
            d[2] += math.sin(ctx.clock * 0.23) * ctx.dt * 0.5
            d[0] += math.cos(d[2]) * 46.0 * ctx.dt
            d[1] += math.sin(d[2]) * 30.0 * ctx.dt
            if not 240.0 <= d[0] <= 1680.0:
                d[2] = math.pi - d[2]
                d[0] = max(240.0, min(1680.0, d[0]))
            if not 180.0 <= d[1] <= 620.0:
                d[2] = -d[2]
                d[1] = max(180.0, min(620.0, d[1]))
            return (d[0], d[1], 0.10, None)
        self._phase += ctx.dt * (0.28 + 1.1 * level)
        if mode == "orbit":
            sweep = 260.0 + 200.0 * level
            return (CX + math.cos(self._phase) * sweep,
                    380.0 + math.sin(self._phase) * 150.0,
                    0.25 + 0.75 * level, None)
        if mode == "lane":
            # A flat patrol lane across the top of the open sky.
            return (CX + math.sin(self._phase * 0.8) * 620.0,
                    166.0 + math.cos(self._phase * 2.4) * 8.0,
                    0.30 + 0.70 * level, None)
        if mode == "figure8" or not self._tiles:
            sweep = 300.0 + 220.0 * level
            return (CX + math.sin(self._phase) * sweep,
                    360.0 + math.sin(self._phase * 2.0) * 140.0,
                    0.25 + 0.75 * level, None)
        # strafe / barrage: the fight modes.
        x, y, w, h, _m = self._tiles[self._hot_tile(ctx)]
        gx, gy = x + w / 2.0, y + h / 2.0
        if mode == "barrage":
            # Park off the tile's inner shoulder and pour it on.
            dx, dy = CX - gx, 400.0 - gy
            mag = math.hypot(dx, dy) or 1.0
            return (gx + dx / mag * 430.0, gy + dy / mag * 430.0, 0.55,
                    math.atan2(gy - self._y, gx - self._x))
        cx = (gx + CX) / 2.0
        cy = (gy + 340.0) / 2.0
        return (cx + math.sin(self._phase) * (300.0 + 220.0 * level),
                cy + math.sin(self._phase * 2.0) * 140.0, 1.0, None)

    def update(self, ctx: Context) -> None:
        c = self.canvas
        self._think(ctx)
        tx, ty, burn, aim = self._steer(ctx)

        # Ease position; aim the nose along actual travel -- unless the mode
        # forces an aim, which is how the barrage keeps its guns on target
        # while parked.
        step = 1.0 - math.exp(-ctx.dt / 0.22)
        nx = _lerp(self._x, tx, step)
        ny = _lerp(self._y, ty, step)
        vx, vy = nx - self._x, ny - self._y
        speed = math.hypot(vx, vy)
        want = None
        if aim is not None and speed < 1.4:
            want = aim
        elif speed > 0.6:
            want = math.atan2(vy, vx)
        if want is not None:
            turn = math.atan2(math.sin(want - self._heading),
                              math.cos(want - self._heading))
            self._heading += turn * min(1.0, ctx.dt * 6.0)
        # The land mode holds its aim through the whole descent -- letting
        # the heading follow the downward velocity turned the touchdown into
        # a nosedive.
        if self._mode == "land" and aim is not None:
            turn = math.atan2(math.sin(aim - self._heading),
                              math.cos(aim - self._heading))
            self._heading += turn * min(1.0, ctx.dt * 6.0)
            want = None
        self._x, self._y = nx, ny
        rear = self._mode in ("warp", "hyper")
        if rear:
            if self._mode == "hyper":
                # Rattling at the edge of the envelope.
                self._x += self.rng.uniform(-3.0, 3.0)
                self._y += self.rng.uniform(-2.5, 2.5)
            self._draw_rear(ctx)
            self._draw_bolts(ctx)
            return
        for k in range(len(REAR_PODS)):
            c.itemconfigure(self.items[f"rhalo{k}"], state="hidden")
            c.itemconfigure(self.items[f"rcore{k}"], state="hidden")
        c.itemconfigure(self.items["rhull"], state="hidden")
        # Un-hide what the rear view hid; without this the ship stayed
        # invisible in every mode after its first warp.
        for name in ("wing_t", "wing_b", "hull", "gun_t", "gun_b", "canopy"):
            c.itemconfigure(self.items[name], state="normal")
        scale = self._scale

        cos, sin = math.cos(self._heading), math.sin(self._heading)

        def place(points, stretch: float = 1.0) -> list[float]:
            flat = []
            for ox, oy in points:
                ox = ox * stretch if ox < -40 else ox
                flat.extend((self._x + (ox * cos - oy * sin) * scale,
                             self._y + (ox * sin + oy * cos) * scale))
            return self._pts(flat)

        # Engine glow, then the afterburners over it: both live behind the
        # hull. Length rides the load; past the fight line the core goes
        # blue-white.
        flick = 0.7 + 0.3 * math.sin(ctx.clock * 11.0)
        thrust = max(burn, ctx.load * 0.5)
        length = (10.0 + 150.0 * thrust) * flick
        engines_on = thrust > 0.04
        glow_r = (16.0 + 60.0 * thrust * flick) * scale
        gx = self._x + (-64.0 * scale) * cos
        gy = self._y + (-64.0 * scale) * sin
        c.coords(self.items["glow"], *self._oval(gx, gy, glow_r * 2, glow_r * 2))
        c.itemconfigure(self.items["glow"],
                        state="normal" if engines_on else "hidden",
                        fill=_mixc(ctx.pal["sky_bot"], "#ff9c3e",
                                   0.20 + 0.45 * thrust))
        hot_core = ctx.level >= 0.66 or self._mode == "hyper"
        for name, oy in (("flame_t", -8.0), ("flame_b", 8.0)):
            flame = ((-52, oy - 5), (-52 - length, oy * 0.6), (-52, oy + 5))
            c.coords(self.items[name], *place(flame))
            c.itemconfigure(self.items[name],
                            state="normal" if engines_on else "hidden",
                            fill=_mixc("#ff8c2e",
                                       "#9ad8ff" if hot_core else "#ffd24a",
                                       flick - 0.4))
        hull = _mixc("#9aa7bd", ctx.pal["accent"], 0.28)
        c.coords(self.items["wing_t"], *place(WING_T))
        c.coords(self.items["wing_b"], *place(WING_B))
        c.coords(self.items["hull"], *place(HULL))
        c.coords(self.items["gun_t"], *place(GUN_T))
        c.coords(self.items["gun_b"], *place(GUN_B))
        c.coords(self.items["canopy"], *place(CANOPY))
        for name in ("wing_t", "wing_b"):
            c.itemconfigure(self.items[name],
                            fill=_mixc(hull, "#000000", 0.30))
        c.itemconfigure(self.items["hull"], fill=hull)

        # The nav strobe on the top wing: one short blink every second and a
        # half, red in the calm, white-hot in a fight.
        if (ctx.clock % 1.5) < 0.12:
            sx = self._x + ((-48.0) * cos - (-50.0) * sin) * scale
            sy = self._y + ((-48.0) * sin + (-50.0) * cos) * scale
            c.coords(self.items["strobe"],
                     *self._oval(sx, sy, 14 * scale, 14 * scale))
            c.itemconfigure(self.items["strobe"], state="normal",
                            fill="#f2f7ff" if ctx.level >= 0.85 else "#ff3d55")
        else:
            c.itemconfigure(self.items["strobe"], state="hidden")

        self._guns(ctx, burn)
        self._draw_bolts(ctx)

    def _draw_rear(self, ctx: Context) -> None:
        """The reference shot: hull from behind, pods at the camera."""
        c = self.canvas
        for name in ("glow", "flame_t", "flame_b", "wing_t", "wing_b",
                     "hull", "gun_t", "gun_b", "canopy", "strobe"):
            c.itemconfigure(self.items[name], state="hidden")
        x, y = self._x, self._y
        hyper = self._mode == "hyper"
        hull = _mixc("#2a3140", ctx.pal["accent"], 0.18)
        pulse = 0.85 + 0.15 * math.sin(ctx.clock * (13.0 if hyper else 7.0))
        for k, (ox, oy, radius, colour) in enumerate(REAR_PODS):
            halo = radius * (2.6 if hyper else 2.1) * pulse
            c.coords(self.items[f"rhalo{k}"],
                     *self._oval(x + ox, y + oy, halo * 2, halo * 2))
            c.itemconfigure(self.items[f"rhalo{k}"], state="normal",
                            fill=_mixc(ctx.pal["sky_bot"], colour, 0.45))
        c.coords(self.items["rhull"],
                 *self._pts([v for ox, oy in REAR_HULL
                             for v in (x + ox, y + oy)]))
        c.itemconfigure(self.items["rhull"], state="normal", fill=hull)
        for k, (ox, oy, radius, colour) in enumerate(REAR_PODS):
            core = radius * pulse
            c.coords(self.items[f"rcore{k}"],
                     *self._oval(x + ox, y + oy, core * 2, core * 2))
            c.itemconfigure(self.items[f"rcore{k}"], state="normal",
                            fill=_mixc(colour, "#ffffff",
                                       0.5 if hyper else 0.2))

    # --- weapons -----------------------------------------------------------

    def _guns(self, ctx: Context, burn: float) -> None:
        # A heat spike earns a volley from anywhere; a sustained fight keeps
        # firing whenever the nose sweeps across the target.
        heat = ctx.heat
        if heat > 0.6 >= self._heat_prev:
            self._volley = 3
        self._heat_prev = heat
        if not self._tiles or ctx.now < self._fire_at \
                or self._mode in ("warp", "hyper"):
            return
        attacking = ctx.level >= 0.85 or self._mode == "barrage"
        if not attacking and self._volley <= 0:
            return
        target = self._hot_tile(ctx)
        x, y, w, h, _m = self._tiles[target]
        gx, gy = x + w / 2.0, y + h / 2.0
        aim = math.atan2(gy - self._y, gx - self._x)
        off = abs(math.atan2(math.sin(aim - self._heading),
                             math.cos(aim - self._heading)))
        if off > 0.5 and not self._volley:
            return                     # nose not on target; keep flying
        for bolt in self._bolts:
            if bolt[4] >= 1.0:
                nose_x = self._x + math.cos(self._heading) * NOSE * self._scale
                nose_y = self._y + math.sin(self._heading) * NOSE * self._scale
                bolt[:] = [nose_x, nose_y, gx, gy, 0.0, target]
                self._fire_at = ctx.now + 0.4
                if self._volley > 0:
                    self._volley -= 1
                break

    def _draw_bolts(self, ctx: Context) -> None:
        c = self.canvas
        for i, bolt in enumerate(self._bolts):
            item = self.items[f"bolt{i}"]
            if bolt[4] >= 1.0:
                c.itemconfigure(item, state="hidden")
                continue
            sx, sy, tx, ty, t, target = bolt
            span = math.hypot(tx - sx, ty - sy) or 1.0
            bolt[4] = t = min(1.0, t + ctx.dt * BOLT_SPEED / span)
            bx = _lerp(sx, tx, t)
            by = _lerp(sy, ty, t)
            tail = min(46.0, span * 0.12)
            c.coords(item, *self._pts([
                bx - (tx - sx) / span * tail, by - (ty - sy) / span * tail,
                bx, by]))
            c.itemconfigure(item, state="normal")
            if t >= 1.0:
                c.itemconfigure(item, state="hidden")
                self._impact_until = ctx.now + 0.22
                self._impact_at = (tx, ty)
                self._impact_tile = int(target)
        # The impact: a brief star on the tile, and the tile takes the hit.
        flash = self.items["flash"]
        if ctx.now < self._impact_until:
            fx, fy = self._impact_at
            left = self._impact_until - ctx.now
            size = 26.0 + 90.0 * (0.22 - left)
            pts = []
            for k in range(12):
                a = k / 12.0 * math.tau
                radius = size if k % 2 == 0 else size * 0.42
                pts.extend((fx + math.cos(a) * radius,
                            fy + math.sin(a) * radius * 0.8))
            c.coords(flash, *self._pts(pts))
            c.itemconfigure(flash, state="normal")
            self.host.nudge_tile(self._impact_tile,
                                 math.sin(ctx.now * 70.0) * 3.5, 0.0)
        else:
            c.itemconfigure(flash, state="hidden")
            self.host.nudge_tile(self._impact_tile, 0.0, 0.0)




# --- the dragon --------------------------------------------------------------

SEG_N = 9                    # body segments behind the head
SEG_GAP = 34.0
DRAGON_HIDE = "#7a2d0e"      # scales
DRAGON_BELLY = "#d8963e"
DRAGON_WING = "#a3421a"
EMBER_TRAIL = 6
RING_N = 3                   # smoke rings while coiled


class Dragon(Rig):
    """A serpent for the lair. The body is a chain of segments playing
    follow-the-leader behind the head, so every flight undulates for free.

    NAPPING coils it on a card, breathing smoke rings; CHILLING it perches
    or glides; WORKING it patrols, or swoops low to circle the POWER row
    like a hoard; SWEATING it blazes with an ember trail, or hovers on big
    wing-beats; MELTING parks it beside the hottest tile and it breathes a
    cone of fire that visibly rattles the card. Heat spikes lob a fireball,
    rain sends it under an overhang dripping, lightning flares its wings,
    and after dark its eye glows.
    """

    CENTERED = False

    def build(self) -> None:
        c, g = self.canvas, self.geo
        tiles = self.host.tile_rects()
        self._tiles = tiles
        self._resting = [i for i, t in enumerate(tiles) if t[1] > 220.0] \
            or list(range(len(tiles)))
        self._perch = self.rng.choice(self._resting) if tiles else 0
        self._state = "glide"
        self._since = 0.0
        self._restless = 12.0
        self._phase = self.rng.uniform(0, math.tau)
        self._head = [CX, CY]
        self._heading = 0.0
        self._segs = [[CX - (i + 1) * SEG_GAP, CY] for i in range(SEG_N)]
        self._flourish_until = 0.0
        self._flare_until = 0.0
        self._heat_prev = 0.0
        self._ball = None            # [t, sx, sy, tx, ty, tile]
        self._scorch_until = 0.0
        self._scorch_at = (0.0, 0.0)
        self._ring_t = [9.9] * RING_N
        self._ring_next = 0.0

        # Depth order: wings under the body, body tail-to-head, head parts,
        # then everything it emits.
        for side in ("l", "r"):
            self.items[f"wing_{side}"] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill=DRAGON_WING, smooth=False,
                outline="#4a1c08", width=g.stroke(3))
        for i in range(SEG_N - 1, -1, -1):
            self.items[f"seg{i}"] = c.create_oval(
                0, 0, 1, 1, fill=DRAGON_HIDE, outline="#4a1c08",
                width=g.stroke(3))
        # A bone spike riding each of the front segments: the silhouette
        # that says dragon rather than caterpillar.
        for i in range(6):
            self.items[f"spike{i}"] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill="#e8d8b0", outline="#8a7448",
                width=g.stroke(2))
        self.items["tailfin"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, fill=DRAGON_WING, outline="")
        # The head: a skull with a brow, a tapered snout and an underjaw,
        # two swept-back horns, an amber eye with a slit pupil, a nostril.
        for name in ("horn_a", "horn_b"):
            self.items[name] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill="#e8d8b0", smooth=True,
                outline="#8a7448", width=g.stroke(2))
        self.items["head"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, fill=DRAGON_HIDE, smooth=True,
            outline="#4a1c08", width=g.stroke(4))
        self.items["jaw"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, fill=DRAGON_BELLY, smooth=True, outline="")
        self.items["nostril"] = c.create_oval(0, 0, 1, 1, fill="#2a0c04",
                                              outline="")
        self.items["eyeglow"] = c.create_oval(
            0, 0, 1, 1, fill="#ffd24a", outline="", state="hidden")
        self.items["eye"] = c.create_oval(0, 0, 1, 1, fill="#ffb43a",
                                          outline="#2a0c04",
                                          width=g.stroke(2))
        self.items["pupil"] = c.create_line(
            0, 0, 1, 1, fill="#1d0a04", width=g.stroke(3), capstyle="round")
        for i in range(8):
            self.items[f"fire{i}"] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill="#f97316", smooth=True, outline="",
                state="hidden")
        for i in range(EMBER_TRAIL):
            self.items[f"trail{i}"] = c.create_oval(
                0, 0, 1, 1, fill="#ff9c3e", outline="", state="hidden")
        self._trail = [[0.0, 0.0, 9.9] for _ in range(EMBER_TRAIL)]
        self.items["ball"] = c.create_oval(0, 0, 1, 1, fill="#ffd24a",
                                           outline="", state="hidden")
        self.items["scorch"] = c.create_oval(
            0, 0, 1, 1, fill="", outline="#ff6a1e", width=g.stroke(6),
            state="hidden")
        for i in range(RING_N):
            self.items[f"ring{i}"] = c.create_oval(
                0, 0, 1, 1, fill="", outline="#9aa3ad", width=g.stroke(4),
                state="hidden")
        for i in range(2):
            self.items[f"drip{i}"] = c.create_line(
                0, 0, 1, 1, fill="#7dd3fc", width=g.stroke(4),
                capstyle="round", state="hidden")
        self._drips = [self.rng.uniform(0, 60) for _ in range(2)]

    # --- brain ----------------------------------------------------------

    def _raining(self, ctx: Context) -> bool:
        return str(getattr(ctx.weather, "condition", "")) in (
            "rain", "drizzle", "thunder")

    def _hot_tile(self, ctx: Context) -> int:
        driver = str(getattr(ctx.stress, "driver", "HEAT"))
        kind = "temp" if driver == "HEAT" else "load"
        cpu = float(ctx.vitals.get(f"cpu_{kind}", 0.0) or 0.0)
        gpu = float(ctx.vitals.get(f"gpu_{kind}", 0.0) or 0.0)
        want = f"calc:cpu_{kind}" if cpu >= gpu else f"calc:gpu_{kind}"
        for index, tile in enumerate(self._tiles):
            if tile[4] == want:
                return index
        return 0

    def _pool(self, ctx: Context) -> tuple:
        if str(getattr(ctx.mood, "key", "")) == "sleepy":
            return ("coil",)
        level = ctx.level
        if self._raining(ctx) and level < 0.50:
            return ("shelter",)
        if level >= 0.86:
            return ("breathe",)
        if level >= 0.66:
            return ("blaze", "gust")
        if level >= 0.42:
            return ("patrol", "hoard")
        return ("glide", "perch")

    def _think(self, ctx: Context) -> None:
        pool = self._pool(ctx)
        switch = None
        if self._state not in pool:
            if ctx.now - self._since > 3.5:
                switch = (pool[0] if len(pool) == 1 or self.rng.random() < 0.6
                          else self.rng.choice(pool[1:]))
        elif len(pool) > 1 and ctx.now - self._since > self._restless:
            switch = self.rng.choice([m for m in pool if m != self._state])
            # A change of idle verb often comes with a wing-stretch and yawn.
            if self.rng.random() < 0.6:
                self._flourish_until = ctx.now + 0.9
        if switch is not None:
            if self._state == "breathe":
                for index in range(len(self._tiles)):
                    self.host.nudge_tile(index, 0.0, 0.0)
            self._state = switch
            self._since = ctx.now
            self._restless = self.rng.uniform(10.0, 20.0)
            if switch in ("perch", "coil", "shelter") and self._tiles:
                self._perch = self.rng.choice(self._resting)
        heat = ctx.heat
        if heat > 0.6 >= self._heat_prev and self._tiles and self._ball is None:
            x, y, w, h, _m = self._tiles[self._hot_tile(ctx)]
            self._ball = [0.0, self._head[0], self._head[1],
                          x + w / 2.0, y + h / 2.0, self._hot_tile(ctx)]
        self._heat_prev = heat

    def _target(self, ctx: Context) -> tuple:
        """Where the head wants to be, and how fast it chases."""
        state = self._state
        level = ctx.level
        if state == "coil" or state == "shelter":
            if self._tiles:
                x, y, w, h, _m = self._tiles[self._perch]
                if state == "coil":
                    return (x + w * 0.5 - 20.0, y - 64.0, 1.6)
                return (x + w * 0.5, y + h + 70.0, 1.6)
            return (CX, CY, 1.6)
        if state == "perch":
            if self._tiles:
                x, y, w, _h, _m = self._tiles[self._perch]
                return (x + w * 0.5 + math.sin(ctx.clock * 0.8) * 14.0,
                        y - 120.0 + math.sin(ctx.clock * 1.3) * 8.0, 1.8)
            return (CX, CY, 1.8)
        if state == "gust":
            return (CX + math.sin(ctx.clock * 0.9) * 40.0,
                    400.0 + math.sin(ctx.clock * 1.7) * 24.0, 2.2)
        if state == "breathe":
            if self._tiles:
                x, y, w, h, _m = self._tiles[self._hot_tile(ctx)]
                on_right = x + w / 2.0 > 960.0
                px = (x - 150.0) if on_right else (x + w + 150.0)
                return (px, y + h * 0.4 + math.sin(ctx.clock * 2.0) * 10.0, 3.0)
            return (CX, CY, 3.0)
        # The flying verbs share a lissajous; each shapes it differently.
        self._phase += ctx.dt * (0.30 + 1.0 * level) \
            * (1.6 if state == "blaze" else 1.0)
        if state == "hoard":
            return (960.0 + math.cos(self._phase) * 480.0,
                    880.0 + math.sin(self._phase) * 90.0, 2.6)
        sweep = 380.0 + 180.0 * level
        return (CX + math.sin(self._phase) * sweep,
                380.0 + math.sin(self._phase * 2.0) * 150.0,
                2.6 if state == "blaze" else 2.0)

    # --- body ------------------------------------------------------------

    def update(self, ctx: Context) -> None:
        self._think(ctx)
        tx, ty, pace = self._target(ctx)
        step = 1.0 - math.exp(-ctx.dt * pace)
        hx = _lerp(self._head[0], tx, step)
        hy = _lerp(self._head[1], ty, step)
        vx, vy = hx - self._head[0], hy - self._head[1]
        if math.hypot(vx, vy) > 0.4:
            want = math.atan2(vy, vx)
            turn = math.atan2(math.sin(want - self._heading),
                              math.cos(want - self._heading))
            self._heading += turn * min(1.0, ctx.dt * 5.0)
        self._head = [hx, hy]

        coiled = self._state in ("coil", "shelter")
        if coiled:
            # Wound around itself on the roof: each segment parks on a
            # tightening spiral instead of trailing the head.
            for i, seg in enumerate(self._segs):
                a = 2.6 + i * 0.72 + math.sin(ctx.clock * 0.5) * 0.05
                radius = 66.0 - i * 5.0
                sx = hx + 26.0 + math.cos(a) * radius
                sy = hy + 34.0 + math.sin(a) * radius * 0.55
                seg[0] = _lerp(seg[0], sx, step)
                seg[1] = _lerp(seg[1], sy, step)
        else:
            lead_x, lead_y = hx, hy
            for i, seg in enumerate(self._segs):
                dx, dy = lead_x - seg[0], lead_y - seg[1]
                mag = math.hypot(dx, dy) or 1.0
                gap = SEG_GAP * (1.0 - i * 0.04)
                if mag > gap:
                    seg[0] += dx / mag * (mag - gap)
                    seg[1] += dy / mag * (mag - gap)
                # A standing wave down the spine keeps even a straight run
                # alive.
                seg[1] += math.sin(ctx.clock * 3.0 - i * 0.7) * 1.6
                lead_x, lead_y = seg[0], seg[1]

        self._draw_dragon(ctx, coiled)
        self._draw_emissions(ctx)

    def _draw_dragon(self, ctx: Context, coiled: bool) -> None:
        c = self.canvas
        hx, hy = self._head
        night = ctx.daylight < 0.30

        # The profile MIRRORS when travel turns leftward instead of rotating
        # through 180 degrees -- a rotated profile flies upside-down, horns
        # hanging from its chin.
        f = 1.0 if math.cos(self._heading) >= 0.0 else -1.0
        eff = math.atan2(math.sin(self._heading) * f,
                         math.cos(self._heading) * f)
        cos, sin = math.cos(eff), math.sin(eff)

        def rot(pts):
            flat = []
            for ox, oy in pts:
                ox *= f
                flat.extend((hx + ox * cos - oy * sin,
                             hy + ox * sin + oy * cos))
            return self._pts(flat)

        # Wings ride the second segment: bat membranes with three fingers.
        # Flaps grow with effort, spike in a flourish or a lightning flare,
        # and fold away when coiled.
        wx, wy = self._segs[1]
        flap = math.sin(ctx.clock * (3.0 + 5.0 * ctx.level))
        span = 1.0 + 0.5 * ctx.level
        if self._state == "gust":
            flap = math.sin(ctx.clock * 2.2)
            span = 1.7
        if ctx.now < self._flourish_until or ctx.now < self._flare_until:
            span = 1.9
        if coiled:
            span = 0.28
            flap = math.sin(ctx.clock * 0.5) * 0.2
        lift = (0.55 + 0.45 * flap) * span

        def wing(sign, k):
            return [wx + sign * 6.0, wy - 4.0,
                    wx + sign * 34.0, wy - 66.0 * k,
                    wx + sign * 46.0, wy - 40.0 * k,
                    wx + sign * 78.0, wy - 52.0 * k,
                    wx + sign * 84.0, wy - 22.0 * k,
                    wx + sign * 112.0, wy - 16.0 * k,
                    wx + sign * 40.0, wy + 12.0]

        for side, sign in (("l", -1.0), ("r", 1.0)):
            k = lift * (1.0 if sign > 0 else 0.88)
            c.coords(self.items[f"wing_{side}"], *self._pts(wing(sign, k)))

        for i, seg in enumerate(self._segs):
            size = 44.0 - i * 3.2
            c.coords(self.items[f"seg{i}"],
                     *self._oval(seg[0], seg[1], size, size * 0.86))
            c.itemconfigure(self.items[f"seg{i}"],
                            fill=_mixc(DRAGON_HIDE, "#3a1206",
                                       i / (SEG_N * 1.6)))
        # Bone spikes down the spine of the front segments.
        for i in range(6):
            seg = self._segs[i]
            size = 44.0 - i * 3.2
            top = seg[1] - size * 0.40
            half = 7.0 - i * 0.6
            c.coords(self.items[f"spike{i}"], *self._pts([
                seg[0] - half, top, seg[0] + half, top,
                seg[0], top - (16.0 - i * 1.6)]))
        # The tail fin, a small diamond past the last segment.
        tail = self._segs[-1]
        prev = self._segs[-2]
        dx, dy = tail[0] - prev[0], tail[1] - prev[1]
        mag = math.hypot(dx, dy) or 1.0
        fx2, fy2 = tail[0] + dx / mag * 22.0, tail[1] + dy / mag * 22.0
        c.coords(self.items["tailfin"], *self._pts([
            tail[0], tail[1],
            fx2 - dy / mag * 14.0, fy2 + dx / mag * 14.0,
            fx2 + dx / mag * 16.0, fy2 + dy / mag * 16.0,
            fx2 + dy / mag * 14.0, fy2 - dx / mag * 14.0]))

        # Horns first (under the skull), then skull, jaw, nostril, eye.
        c.coords(self.items["horn_a"], *rot([
            (-14, -20), (-20, -26), (-52, -46), (-24, -18)]))
        c.coords(self.items["horn_b"], *rot([
            (-2, -24), (-8, -28), (-32, -42), (-12, -20)]))
        c.coords(self.items["head"], *rot([
            (56, 2), (44, -8), (20, -22), (-14, -26), (-30, -14),
            (-30, 10), (-8, 22), (24, 14), (48, 8)]))
        c.coords(self.items["jaw"], *rot([
            (50, 6), (24, 16), (-6, 20), (-22, 12), (-4, 12), (28, 8)]))
        closed = coiled and self._state == "coil"
        # rot() returns canvas pixels, so the round parts are placed straight
        # in pixel space.
        eye_pt = rot([(12, -9)])
        ex, ey = eye_pt[0], eye_pt[1]
        g2 = self.geo
        er = (11.0 if not closed else 3.0) * g2.kx
        c.coords(self.items["eye"], ex - er, ey - er * 0.9,
                 ex + er, ey + er * 0.9)
        c.coords(self.items["pupil"], ex, ey - er * 0.7, ex, ey + er * 0.7)
        c.itemconfigure(self.items["pupil"],
                        state="hidden" if closed else "normal")
        nos = rot([(44, -4)])
        nr = 3.5 * g2.kx
        c.coords(self.items["nostril"], nos[0] - nr, nos[1] - nr,
                 nos[0] + nr, nos[1] + nr)
        c.itemconfigure(self.items["eyeglow"],
                        state="normal" if night and not closed else "hidden")
        if night and not closed:
            pulse = (16.0 + 3.0 * math.sin(ctx.clock * 2.0)) * g2.kx
            c.coords(self.items["eyeglow"], ex - pulse, ey - pulse,
                     ex + pulse, ey + pulse)

    def _draw_emissions(self, ctx: Context) -> None:
        c = self.canvas
        hx, hy = self._head
        cos, sin = math.cos(self._heading), math.sin(self._heading)

        # Fire breath: a flickering cone from the snout to the tile it is
        # punishing, which shakes under it.
        breathing = self._state == "breathe" and self._tiles
        for i in range(8):
            item = self.items[f"fire{i}"]
            if not breathing:
                c.itemconfigure(item, state="hidden")
                continue
            x, y, w, h, _m = self._tiles[self._hot_tile(ctx)]
            gx, gy = x + w / 2.0, y + h / 2.0
            t = (i + 1) / 8.0
            jx = math.sin(ctx.clock * 9.0 + i * 1.7) * 26.0 * t
            fx = _lerp(hx + cos * 40.0, gx, t)
            fy = _lerp(hy + sin * 40.0, gy, t) + jx * 0.4
            size = 14.0 + 34.0 * t
            flick = 0.6 + 0.4 * math.sin(ctx.clock * 8.0 + i * 2.0)
            c.coords(item, *self._pts(_flame_pts(fx, fy, size, flick)))
            c.itemconfigure(item, state="normal",
                            fill=_mixc("#f97316", "#ffd24a", flick * t))
        if breathing:
            target = self._hot_tile(ctx)
            self.host.nudge_tile(target,
                                 math.sin(ctx.now * 50.0) * 3.0, 0.0)
            self._scorch_until = ctx.now + 0.4
            x, y, w, h, _m = self._tiles[target]
            self._scorch_at = (x + w / 2.0, y + h / 2.0)

        # The lobbed fireball, and the scorch both attacks leave behind.
        if self._ball is not None:
            ball = self._ball
            ball[0] = min(1.0, ball[0] + ctx.dt / 0.7)
            t = ball[0]
            bx = _lerp(ball[1], ball[3], t)
            by = _lerp(ball[2], ball[4], t) - math.sin(t * math.pi) * 160.0
            c.coords(self.items["ball"], *self._oval(bx, by, 26.0, 26.0))
            c.itemconfigure(self.items["ball"], state="normal")
            if t >= 1.0:
                c.itemconfigure(self.items["ball"], state="hidden")
                self._scorch_until = ctx.now + 1.6
                self._scorch_at = (ball[3], ball[4])
                self.host.nudge_tile(int(ball[5]), 3.0, 0.0)
                self._ball = None
        else:
            c.itemconfigure(self.items["ball"], state="hidden")
        if ctx.now < self._scorch_until:
            left = self._scorch_until - ctx.now
            size = 60.0 + 40.0 * (1.6 - left)
            c.coords(self.items["scorch"],
                     *self._oval(self._scorch_at[0], self._scorch_at[1],
                                 size, size * 0.6))
            c.itemconfigure(self.items["scorch"], state="normal")
        else:
            c.itemconfigure(self.items["scorch"], state="hidden")
            if not breathing:
                for index in range(len(self._tiles)):
                    self.host.nudge_tile(index, 0.0, 0.0)

        # The blaze's ember trail.
        blazing = self._state == "blaze"
        for i, ember in enumerate(self._trail):
            item = self.items[f"trail{i}"]
            ember[2] += ctx.dt
            if blazing and ember[2] > 0.9:
                tail = self._segs[-1]
                ember[:] = [tail[0], tail[1], 0.0]
            if ember[2] > 0.9:
                c.itemconfigure(item, state="hidden")
                continue
            size = 10.0 * (1.0 - ember[2] / 0.9)
            c.coords(item, *self._oval(ember[0], ember[1] - ember[2] * 40.0,
                                       size, size))
            c.itemconfigure(item, state="normal",
                            fill=_mixc("#ff9c3e", ctx.pal["sky_top"],
                                       ember[2]))

        # Smoke rings while it sleeps; drips while it shelters from rain.
        sleeping = self._state == "coil"
        if sleeping and ctx.now >= self._ring_next:
            self._ring_next = ctx.now + self.rng.uniform(2.0, 3.5)
            for i in range(RING_N):
                if self._ring_t[i] > 1.0:
                    self._ring_t[i] = 0.0
                    break
        for i in range(RING_N):
            item = self.items[f"ring{i}"]
            self._ring_t[i] += ctx.dt / 2.4
            if not sleeping or self._ring_t[i] > 1.0:
                c.itemconfigure(item, state="hidden")
                continue
            t = self._ring_t[i]
            size = 10.0 + t * 44.0
            c.coords(item, *self._oval(hx + cos * 40.0,
                                       hy + sin * 40.0 - t * 120.0,
                                       size, size * 0.5))
            c.itemconfigure(item, state="normal",
                            outline=_mixc("#9aa3ad", ctx.pal["sky_top"], t))
        sheltering = self._state == "shelter"
        for i in range(2):
            item = self.items[f"drip{i}"]
            if not sheltering:
                c.itemconfigure(item, state="hidden")
                continue
            self._drips[i] = (self._drips[i] + ctx.dt * 160.0) % 90.0
            dx = hx - 40.0 + i * 80.0
            dy = hy - 60.0 + self._drips[i]
            c.coords(item, *self._pts([dx, dy, dx, dy + 14.0]))
            c.itemconfigure(item, state="normal")


def _flame_pts(x: float, y: float, s: float, wob: float) -> list[float]:
    return [x - s * 0.5, y + s * 0.4, x + s * 0.5, y + s * 0.4,
            x + s * 0.2, y - s * 0.2 * wob, x, y - s * 0.8,
            x - s * 0.2, y - s * 0.2 * wob]


# --- the race car ------------------------------------------------------------

CAR_BODY = "#e8ebf2"
CAR_TRIM = "#22252c"
TRACK = ((120.0, 138.0), (1800.0, 810.0))   # the loop's bounding box
TRACK_R = 110.0                              # corner radius
SMOKE_N = 5
MARK_N = 8


def _track_path():
    """The perimeter loop as (segments, total length). Each segment is
    (length, fn(d) -> (x, y, heading))."""
    (x0, y0), (x1, y1) = TRACK
    r = TRACK_R
    segs = []

    def straight(ax, ay, bx, by):
        length = math.hypot(bx - ax, by - ay)
        heading = math.atan2(by - ay, bx - ax)

        def fn(d, ax=ax, ay=ay, bx=bx, by=by, L=length, h=heading):
            t = d / L
            return (_lerp(ax, bx, t), _lerp(ay, by, t), h)
        segs.append((length, fn))

    def corner(cx, cy, a0, a1):
        length = abs(a1 - a0) * r

        def fn(d, cx=cx, cy=cy, a0=a0, a1=a1, L=length):
            a = _lerp(a0, a1, d / L)
            return (cx + math.cos(a) * r, cy + math.sin(a) * r,
                    a + math.pi / 2.0)
        segs.append((length, fn))

    straight(x0 + r, y0, x1 - r, y0)
    corner(x1 - r, y0 + r, -math.pi / 2.0, 0.0)
    straight(x1, y0 + r, x1, y1 - r)
    corner(x1 - r, y1 - r, 0.0, math.pi / 2.0)
    straight(x1 - r, y1, x0 + r, y1)
    corner(x0 + r, y1 - r, math.pi / 2.0, math.pi)
    straight(x0, y1 - r, x0, y0 + r)
    corner(x0 + r, y0 + r, math.pi, math.pi * 1.5)
    return segs, sum(length for length, _fn in segs)


class RaceCar(Rig):
    """A top-down racer whose circuit IS the layout: down the straights past
    the cards, drifting the corners with tire smoke.

    CHILLING it sits parked, engine ticking over, blipping the throttle when
    restless; WORKING it laps, drifting every corner and changing racing
    line; SWEATING adds nitro flames and speed lines; MELTING sends it off
    the circuit to do smoking donuts around the hottest tile, which rattles
    under it. NAPPING is a pit stop on the mood word -- jacked up, engine
    off, Zs rising. Headlights come on after real sunset, rain throws spray
    off the tires, and dropping back out of MELTING earns a checkered flag.
    """

    CENTERED = False

    def build(self) -> None:
        c, g = self.canvas, self.geo
        self._tiles = self.host.tile_rects()
        self._segs, self._length = _track_path()
        self._dist = 0.0
        self._state = "park"
        self._since = 0.0
        self._restless = 10.0
        self._x, self._y = CX, TRACK[0][1]
        self._heading = 0.0
        self._drift = 0.0
        self._lane = 0.0
        self._lane_t = 0.0
        self._rev_until = 0.0
        self._spin = 0.0
        self._melt_prev = False
        self._flag_until = 0.0
        self._puff_next = 0.0
        self._heat_prev = 0.0

        for i in range(MARK_N):
            self.items[f"mark{i}"] = c.create_line(
                0, 0, 1, 1, fill="#22252c", width=g.stroke(6),
                capstyle="round", state="hidden")
        self._marks = [[0.0, 0.0, 0.0, 0.0, 9.9] for _ in range(MARK_N)]
        for i in range(SMOKE_N):
            self.items[f"smoke{i}"] = c.create_oval(
                0, 0, 1, 1, fill="#8b95a6", outline="", state="hidden")
        self._smoke = [[0.0, 0.0, 9.9] for _ in range(SMOKE_N)]
        for i in range(4):
            self.items[f"speed{i}"] = c.create_line(
                0, 0, 1, 1, fill="#ffd21f", width=g.stroke(3),
                capstyle="round", state="hidden")
        for i in range(2):
            self.items[f"spray{i}"] = c.create_line(
                0, 0, 1, 1, fill="#7dd3fc", width=g.stroke(4),
                capstyle="round", state="hidden")
            self.items[f"beam{i}"] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill="#3a3a20", outline="", state="hidden")
            self.items[f"nitro{i}"] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill="#7ce7ff", smooth=True, outline="",
                state="hidden")
        self.items["jack"] = c.create_rectangle(
            0, 0, 1, 1, fill="#5d6a83", outline="", state="hidden")
        for i in range(4):
            self.items[f"tire{i}"] = c.create_polygon(
                0, 0, 1, 1, 2, 2, fill="#14161c", outline="")
        self.items["body"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, fill=CAR_BODY, smooth=True,
            outline=CAR_TRIM, width=g.stroke(4))
        self.items["cockpit"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, fill="#22303f", smooth=True, outline="")
        for i in range(3):
            self.items[f"puff{i}"] = c.create_oval(
                0, 0, 1, 1, fill="#8b95a6", outline="", state="hidden")
        self._puffs = [[0.0, 0.0, 9.9] for _ in range(3)]
        self.items["zzz"] = c.create_text(
            0, 0, text="Z", anchor="center", fill="#9aa3ad",
            font=self.host.f_quip, state="hidden")
        self._zzz_t = 0.0
        self.items["flagpole"] = c.create_line(
            0, 0, 1, 1, fill="#8b95a6", width=g.stroke(5), state="hidden")
        self.items["flag"] = c.create_polygon(
            0, 0, 1, 1, 2, 2, fill="#f5f6f8", outline="#22252c",
            width=g.stroke(3), state="hidden")

    def _pool(self, ctx: Context) -> tuple:
        if str(getattr(ctx.mood, "key", "")) == "sleepy":
            return ("pit",)
        level = ctx.level
        if level >= 0.86:
            return ("donuts",)
        if level >= 0.66:
            return ("nitro",)
        if level >= 0.42:
            return ("race",)
        return ("park", "warmup")

    def _think(self, ctx: Context) -> None:
        pool = self._pool(ctx)
        switch = None
        if self._state not in pool:
            if ctx.now - self._since > 3.0:
                switch = pool[0]
        elif len(pool) > 1 and ctx.now - self._since > self._restless:
            switch = next(m for m in pool if m != self._state)
        if switch is not None:
            if self._state == "donuts":
                for index in range(len(self._tiles)):
                    self.host.nudge_tile(index, 0.0, 0.0)
            self._state = switch
            self._since = ctx.now
            self._restless = self.rng.uniform(10.0, 22.0)
        # Restless throttle blip while parked; racing-line change on track.
        if ctx.now - self._since > self._restless * 0.5 \
                and ctx.now >= self._rev_until + 4.0 \
                and self._state == "park":
            self._rev_until = ctx.now + 0.8
        if self._state in ("race", "nitro") and ctx.now >= self._lane_t:
            self._lane_t = ctx.now + self.rng.uniform(8.0, 16.0)
            self._lane = self.rng.uniform(-26.0, 26.0)
        # The checkered flag when it survives a melt.
        melting = ctx.level >= 0.86
        if self._melt_prev and not melting:
            self._flag_until = ctx.now + 3.0
        self._melt_prev = melting

    def _hot_tile(self, ctx: Context) -> int:
        cpu = float(ctx.vitals.get("cpu_temp", 0.0) or 0.0)
        gpu = float(ctx.vitals.get("gpu_temp", 0.0) or 0.0)
        want = "calc:cpu_temp" if cpu >= gpu else "calc:gpu_temp"
        for index, tile in enumerate(self._tiles):
            if tile[4] == want:
                return index
        return 0

    def _at(self, dist: float) -> tuple:
        d = dist % self._length
        for length, fn in self._segs:
            if d <= length:
                return fn(d)
            d -= length
        return self._segs[-1][1](self._segs[-1][0])

    def update(self, ctx: Context) -> None:
        self._think(ctx)
        state = self._state
        raining = str(getattr(ctx.weather, "condition", "")) in (
            "rain", "drizzle", "thunder")
        moving = False
        drifting = False

        if state in ("race", "nitro", "warmup"):
            speed = {"warmup": 260.0, "race": 420.0,
                     "nitro": 620.0}[state] + 700.0 * ctx.level
            if raining:
                speed *= 0.8
            before = self._at(self._dist)[2]
            self._dist += speed * ctx.dt
            x, y, heading = self._at(self._dist)
            # Racing line: slide across the lane width.
            self._lane += (0.0 - 0.0)   # eased below via offset use
            ox = -math.sin(heading) * self._lane
            oy = math.cos(heading) * self._lane
            x, y = x + ox, y + oy
            turn = math.atan2(math.sin(heading - before),
                              math.cos(heading - before))
            drifting = abs(turn) > 0.002
            self._drift = _lerp(self._drift,
                                (0.5 if turn > 0 else -0.5) if drifting
                                else 0.0, 1.0 - math.exp(-ctx.dt / 0.15))
            self._x, self._y, self._heading = x, y, heading + self._drift
            moving = True
        elif state == "donuts":
            if self._tiles:
                x, y, w, h, _m = self._tiles[self._hot_tile(ctx)]
                gx, gy = x + w / 2.0, y + h / 2.0
                self._spin += ctx.dt * (3.2 + ctx.level)
                radius = 150.0 + math.sin(self._spin * 0.5) * 20.0
                self._x = _lerp(self._x, gx + math.cos(self._spin) * radius,
                                1.0 - math.exp(-ctx.dt / 0.2))
                self._y = _lerp(self._y, gy + math.sin(self._spin) * radius * 0.7,
                                1.0 - math.exp(-ctx.dt / 0.2))
                self._heading = self._spin + math.pi / 2.0 + 0.7
                self.host.nudge_tile(self._hot_tile(ctx),
                                     math.sin(ctx.now * 40.0) * 3.0, 0.0)
                moving = True
                drifting = True
        elif state == "pit":
            tx, ty = CX, 566.0
            self._x = _lerp(self._x, tx, 1.0 - math.exp(-ctx.dt / 0.4))
            self._y = _lerp(self._y, ty, 1.0 - math.exp(-ctx.dt / 0.4))
            turn = math.atan2(math.sin(0.0 - self._heading),
                              math.cos(0.0 - self._heading))
            self._heading += turn * min(1.0, ctx.dt * 4.0)
        else:                      # park: pole position on the top straight
            tx, ty, th = self._at(self._length * 0.02)
            self._x = _lerp(self._x, tx, 1.0 - math.exp(-ctx.dt / 0.4))
            self._y = _lerp(self._y, ty, 1.0 - math.exp(-ctx.dt / 0.4))
            turn = math.atan2(math.sin(th - self._heading),
                              math.cos(th - self._heading))
            self._heading += turn * min(1.0, ctx.dt * 4.0)

        self._draw_car(ctx, state, moving, drifting, raining)

    def _draw_car(self, ctx: Context, state: str, moving: bool,
                  drifting: bool, raining: bool) -> None:
        c = self.canvas
        x, y = self._x, self._y
        revving = ctx.now < self._rev_until
        pitted = state == "pit" and abs(y - 566.0) < 12.0
        lift = 7.0 if pitted else 0.0
        bob = math.sin(ctx.clock * 9.0) * (1.5 if revving else 0.6) \
            if not moving else 0.0
        y = y - lift + bob
        cos, sin = math.cos(self._heading), math.sin(self._heading)

        def rot(pts):
            flat = []
            for ox, oy in pts:
                flat.extend((x + ox * cos - oy * sin,
                             y + ox * sin + oy * cos))
            return self._pts(flat)

        body = _mixc(CAR_BODY, ctx.pal["accent"], 0.35)
        squat = 1.15 if revving else 1.0
        for i, (ox, oy) in enumerate(((26, -19), (26, 19), (-26, -19),
                                      (-26, 19))):
            c.coords(self.items[f"tire{i}"], *rot([
                (ox - 8, oy - 5), (ox + 8, oy - 5),
                (ox + 8, oy + 5), (ox - 8, oy + 5)]))
        c.coords(self.items["body"], *rot([
            (42, 0), (30, -13 * squat), (-8, -16 * squat), (-34, -12),
            (-40, 0), (-34, 12), (-8, 16 * squat), (30, 13 * squat)]))
        c.itemconfigure(self.items["body"], fill=body)
        c.coords(self.items["cockpit"], *rot([
            (16, 0), (6, -8), (-12, -8), (-12, 8), (6, 8)]))

        c.itemconfigure(self.items["jack"],
                        state="normal" if pitted else "hidden")
        if pitted:
            c.coords(self.items["jack"],
                     *self._oval(x, self._y + 16.0, 30.0, 10.0))
            self._zzz_t = (self._zzz_t + ctx.dt * 0.4) % 1.0
            g = self.geo
            c.coords(self.items["zzz"], g.x(x + 50.0 + self._zzz_t * 40.0),
                     g.y(y - 40.0 - self._zzz_t * 70.0))
            c.itemconfigure(self.items["zzz"], state="normal",
                            fill=_mixc(ctx.pal["sky_top"], "#9aa3ad",
                                       1.0 - self._zzz_t))
        else:
            c.itemconfigure(self.items["zzz"], state="hidden")

        # Exhaust: puffs at idle and on the throttle blip.
        idle_engine = state in ("park", "warmup") or revving
        if idle_engine and ctx.now >= self._puff_next:
            self._puff_next = ctx.now + (0.25 if revving else
                                         self.rng.uniform(1.6, 3.2))
            for puff in self._puffs:
                if puff[2] > 1.0:
                    puff[:] = [x - cos * 44.0, y - sin * 44.0, 0.0]
                    break
        for i, puff in enumerate(self._puffs):
            item = self.items[f"puff{i}"]
            puff[2] += ctx.dt / 1.1
            if puff[2] > 1.0:
                c.itemconfigure(item, state="hidden")
                continue
            size = 8.0 + puff[2] * 18.0
            c.coords(item, *self._oval(puff[0], puff[1] - puff[2] * 30.0,
                                       size, size))
            c.itemconfigure(item, state="normal",
                            fill=_mixc("#8b95a6", ctx.pal["sky_top"],
                                       puff[2]))

        # Drift smoke and the marks it leaves.
        if drifting:
            for puff in self._smoke:
                if puff[2] > 0.8:
                    puff[:] = [x - cos * 30.0 + self.rng.uniform(-8, 8),
                               y - sin * 30.0 + self.rng.uniform(-8, 8), 0.0]
                    break
            for mark in self._marks:
                if mark[4] > 1.6:
                    mark[:] = [x - cos * 26.0, y - sin * 26.0,
                               x - cos * 46.0, y - sin * 46.0, 0.0]
                    break
        for i, puff in enumerate(self._smoke):
            item = self.items[f"smoke{i}"]
            puff[2] += ctx.dt / 0.8
            if puff[2] > 1.0:
                c.itemconfigure(item, state="hidden")
                continue
            size = 12.0 + puff[2] * 26.0
            c.coords(item, *self._oval(puff[0], puff[1], size, size))
            c.itemconfigure(item, state="normal",
                            fill=_mixc("#8b95a6", ctx.pal["sky_top"],
                                       puff[2]))
        for i, mark in enumerate(self._marks):
            item = self.items[f"mark{i}"]
            mark[4] += ctx.dt / 1.6
            if mark[4] > 1.0:
                c.itemconfigure(item, state="hidden")
                continue
            c.coords(item, *self._pts(mark[:4]))
            c.itemconfigure(item, state="normal",
                            fill=_mixc("#22252c", ctx.pal["sky_bot"],
                                       mark[4]))

        # Nitro flames and speed lines at SWEATING and above.
        hot = state in ("nitro", "donuts")
        for i in range(2):
            item = self.items[f"nitro{i}"]
            if not hot:
                c.itemconfigure(item, state="hidden")
            else:
                oy = -10.0 if i == 0 else 10.0
                length = 26.0 + 22.0 * (0.6 + 0.4 * math.sin(
                    ctx.clock * 12.0 + i))
                c.coords(item, *rot([(-40, oy - 4), (-40 - length, oy * 0.7),
                                     (-40, oy + 4)]))
                c.itemconfigure(item, state="normal")
            speed_item = self.items[f"speed{i}"]
            speed_item2 = self.items[f"speed{i + 2}"]
            if state == "nitro" and moving:
                for k, it in ((0, speed_item), (1, speed_item2)):
                    off = -30.0 - k * 16.0 - (ctx.clock * 700.0) % 40.0
                    oy2 = (-22.0 if i == 0 else 22.0)
                    c.coords(it, *rot([(off, oy2), (off - 26.0, oy2)]))
                    c.itemconfigure(it, state="normal")
            else:
                c.itemconfigure(speed_item, state="hidden")
                c.itemconfigure(speed_item2, state="hidden")

        # Rain spray and night headlights, from the real weather and sun.
        for i in range(2):
            spray = self.items[f"spray{i}"]
            if raining and moving:
                oy = -20.0 if i == 0 else 20.0
                jag = self.rng.uniform(6.0, 18.0)
                c.coords(spray, *rot([(-30, oy), (-30 - jag, oy * 1.4)]))
                c.itemconfigure(spray, state="normal")
            else:
                c.itemconfigure(spray, state="hidden")
            beam = self.items[f"beam{i}"]
            if ctx.daylight < 0.30 and state != "pit":
                oy = -10.0 if i == 0 else 10.0
                c.coords(beam, *rot([(42, oy), (150, oy - 22), (150, oy + 22)]))
                c.itemconfigure(beam, state="normal",
                                fill=_mixc(ctx.pal["sky_top"], "#fff7c4", 0.30))
            else:
                c.itemconfigure(beam, state="hidden")

        # The checkered flag at the start line after surviving a melt.
        if ctx.now < self._flag_until:
            fx, fy = CX, TRACK[0][1] - 4.0
            wave = math.sin(ctx.clock * 8.0) * 10.0
            c.coords(self.items["flagpole"],
                     *self._pts([fx, fy, fx, fy - 60.0]))
            c.coords(self.items["flag"], *self._pts([
                fx, fy - 60.0, fx + 54.0, fy - 52.0 + wave,
                fx + 54.0, fy - 24.0 + wave, fx, fy - 32.0]))
            for name in ("flagpole", "flag"):
                c.itemconfigure(self.items[name], state="normal")
        else:
            for name in ("flagpole", "flag"):
                c.itemconfigure(self.items[name], state="hidden")


# --- registry ----------------------------------------------------------------

RIGS: dict[str, type] = {
    "doom": DoomFace,
    "robot": Robot,
    "pet": Tamagotchi,
    "cat": Cat,
    "spider": Spider,
    "ship": Ship,
    "dragon": Dragon,
    "car": RaceCar,
}

# What the settings selector shows for each face the character can wear,
# built-ins included. Order is the order offered.
CHARACTERS = ("drawn", "emoji", "image", "doom", "robot", "cat", "spider",
              "ship", "dragon", "car", "pet")

LABELS = {
    "drawn": "Drawn face - morphs between moods",
    "emoji": "Emoji - one colour glyph per mood",
    "image": "Pictures - your own image or GIF per mood",
    "doom": "Doom face - takes damage as the heat climbs",
    "robot": "Robot - vents steam, antenna blinks with the network",
    "cat": "Cat - lives on the cards, knocks things off when it gets hot",
    "spider": "Web-slinger - hangs, crawls, swings and fights by load",
    "ship": "Starship - afterburners on load, strafes the busiest tile",
    "dragon": "Dragon - serpentine flight, breathes fire at the hottest tile",
    "car": "Race car - laps the layout, drifts, does donuts on the offender",
    "pet": "Pet - grows with uptime, remembers how you treat the machine",
}


def make(name: str, host) -> Rig | None:
    cls = RIGS.get(str(name or "").strip().lower())
    return cls(host) if cls else None
