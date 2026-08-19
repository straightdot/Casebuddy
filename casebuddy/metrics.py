"""The shape the UI consumes: one Reading per tile, bundled into a Snapshot.

Keeping the display model separate from the sensor backends means a tile can
say "measured", "modeled" or "unavailable" without every backend having to know
how the dashboard wants to draw those three cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field

OK, WARN, CRIT, NA = "ok", "warn", "crit", "na"


@dataclass
class Reading:
    """One number destined for one tile."""

    value: float | None = None
    unit: str = ""
    # 0.0-1.0 fill for the gauge, independent of `value`'s scale.
    fraction: float | None = None
    # Secondary line, bottom-right of a bar tile, e.g. "3200 MHz".
    detail: str = ""
    # Third string, top-right of a bar tile alongside the label, e.g.
    # "7.3 / 15.9 GB". Kept separate from `detail` so a tile can carry both a
    # static fact and a live one without them competing for one slot.
    top: str = ""
    state: str = NA
    # True when the number came from a model rather than a sensor. The UI
    # prefixes these with "~" so nobody mistakes them for a measurement.
    estimated: bool = False
    source: str = ""

    @property
    def available(self) -> bool:
        return self.value is not None


@dataclass
class Snapshot:
    ts: float = 0.0
    readings: dict[str, Reading] = field(default_factory=dict)
    # Free-form strings the footer shows: clocks, fans, uptime, warnings.
    facts: dict[str, str] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)
    # Every numeric "calc:" metric this sample produced, keyed by its short
    # name. Slots only carry what the layout happens to point at, and the
    # buddy scene has to judge the machine's mood from temperatures and loads
    # whether or not any tile is showing them.
    vitals: dict[str, float] = field(default_factory=dict)
    # Outdoor conditions, when the weather source is on. None means "no sky
    # data", which every consumer must treat as ordinary rather than as an
    # error -- it is the state on a machine with no network.
    weather: object | None = None

    def get(self, key: str) -> Reading:
        return self.readings.get(key, Reading())


def fmt_tile(reading: Reading) -> str:
    """The big number on a tile: "--" when absent, "~" when modeled."""
    if reading.value is None:
        return "--"
    return f"~{reading.value:.0f}" if reading.estimated else f"{reading.value:.0f}"


def classify(value: float | None, warn: float, crit: float) -> str:
    if value is None:
        return NA
    if value >= crit:
        return CRIT
    if value >= warn:
        return WARN
    return OK


def fraction(value: float | None, lo: float, hi: float) -> float | None:
    """Map a value onto 0..1 for gauge fill, clamped."""
    if value is None or hi <= lo:
        return None
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def fmt_bytes_gb(num: int | None) -> str:
    if num is None:
        return "--"
    return f"{num / (1024 ** 3):.1f}"


def fmt_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours:02d}h"
    return f"{hours:02d}h {minutes:02d}m"
