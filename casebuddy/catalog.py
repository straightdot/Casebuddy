"""What a dashboard slot can be pointed at.

Two kinds of metric reference:

  lhm:<SensorId>   any sensor LibreHardwareMonitor publishes, e.g.
                   "lhm:/amdcpu/0/temperature/2". There are ~145 of them on
                   this machine, so the catalogue is built by enumerating what
                   is actually there rather than by curating a fixed list --
                   a board with different fan headers or a second GPU gets its
                   sensors offered automatically.

  calc:<name>      something derived rather than measured: the system power
                   estimate, a used/total percentage, a "7.0 / 15.9 GB" string.

Keeping both behind one string means a slot's configuration is just text, and
config.json stays diffable.
"""

from __future__ import annotations

from dataclasses import dataclass

# LHM sensor type -> (unit shown, sensible gauge range)
TYPE_UNITS: dict[str, tuple[str, float, float]] = {
    "Temperature": ("°C", 20.0, 100.0),
    "Load": ("%", 0.0, 100.0),
    "Power": ("W", 0.0, 250.0),
    "Clock": ("MHz", 0.0, 5000.0),
    "Fan": ("RPM", 0.0, 2500.0),
    "Control": ("%", 0.0, 100.0),
    "Voltage": ("V", 0.0, 13.0),
    "Data": ("GB", 0.0, 64.0),
    "SmallData": ("MB", 0.0, 16384.0),
    "Throughput": ("KB/s", 0.0, 100000.0),
    "Level": ("%", 0.0, 100.0),
    "Factor": ("", 0.0, 10.0),
}

# Friendly names for the identifier prefixes LHM uses.
GROUPS = {
    "/amdcpu": "CPU",
    "/intelcpu": "CPU",
    "/gpu-nvidia": "GPU",
    "/gpu-amd": "GPU",
    "/gpu-intel": "GPU",
    "/lpc": "Motherboard",
    "/ram": "Memory",
    "/vram": "Memory",
    "/memory": "Memory",
    "/hdd": "Storage",
    "/nvme": "Storage",
}


@dataclass(frozen=True)
class MetricDef:
    ref: str
    label: str          # what the picker shows
    unit: str
    group: str
    lo: float = 0.0
    hi: float = 100.0
    # Text metrics (like "7.0 / 15.9 GB") have no numeric value and cannot
    # drive a gauge; the editor greys them out for value slots.
    numeric: bool = True


def _group_for(ident: str) -> str:
    for prefix, name in GROUPS.items():
        if ident.startswith(prefix):
            return name
    return "Other"


# --- computed metrics -----------------------------------------------------
#
# Each takes the collector's context dict and returns (value, unit, text).
# `text` wins when present, which is how composite strings work.

# name -> (label, unit, numeric, lo, hi)
#
# These are the portable ones. They go through the matching rules in
# sources/lhm.py, so "calc:cpu_temp" means the right thing on an Intel box with
# a different super-I/O chip, whereas a raw "lhm:/amdcpu/0/temperature/2" is
# specific to this machine. Defaults therefore use calc: refs; lhm: refs are
# the escape hatch for anything not covered.
CALC: dict[str, tuple[str, str, bool, float, float]] = {
    "cpu_temp": ("CPU temperature", "°C", True, 25, 95),
    "cpu_power": ("CPU package power", "W", True, 0, 120),
    "cpu_load": ("CPU load", "%", True, 0, 100),
    "cpu_clock": ("CPU effective clock", "MHz", True, 0, 5000),
    "cpu_clock_nominal": ("CPU clock ceiling", "MHz", True, 0, 5000),
    "cpu_fan_rpm": ("CPU fan speed", "RPM", True, 0, 2200),
    "cpu_fan_pct": ("CPU fan duty", "%", True, 0, 100),
    "mobo_temp": ("Motherboard temperature", "°C", True, 20, 90),

    "gpu_temp": ("GPU temperature", "°C", True, 25, 95),
    "gpu_hotspot": ("GPU hot spot", "°C", True, 25, 110),
    "gpu_power": ("GPU board power", "W", True, 0, 400),
    "gpu_load": ("GPU load", "%", True, 0, 100),
    "gpu_clock": ("GPU core clock", "MHz", True, 0, 3000),
    "gpu_mem_clock": ("GPU memory clock", "MHz", True, 0, 12000),
    "gpu_fan_rpm": ("GPU fan speed", "RPM", True, 0, 3400),
    "gpu_fan_pct": ("GPU fan duty", "%", True, 0, 100),

    "ram_pct": ("RAM used", "%", True, 0, 100),
    "ram_gb": ("RAM used / total", "", False, 0, 100),
    "ram_speed": ("RAM speed", "MHz", True, 0, 8000),
    "vram_pct": ("V-RAM used", "%", True, 0, 100),
    "vram_gb": ("V-RAM used / total", "", False, 0, 100),

    "system_power": ("System power (estimate)", "W", True, 0, 650),
    "power_breakdown": ("Power split (CPU / GPU)", "", False, 0, 100),
    "outside_temp": ("Outside temperature", "°C", True, -10, 50),
    "outside_feels": ("Outside, feels like", "°C", True, -10, 55),
    "weather": ("Outside conditions", "", False, 0, 100),
    "weather_place": ("Weather location", "", False, 0, 100),
    "weather_line": ("Weather line (place, temp, sky)", "", False, 0, 100),

    "cpu_name": ("CPU model", "", False, 0, 100),
    "gpu_name": ("GPU model", "", False, 0, 100),
    "date": ("Date", "", False, 0, 100),
    "clock": ("Time of day", "", False, 0, 100),
    # Sorted to the top of every picker by layout_editor: emptying a slot is
    # a thing people look for, and "(nothing)" filed under C for Computed is
    # not where anyone looks for it.
    "blank": ("Blank - leave this slot empty", "", False, 0, 100),
}


def calc_metrics() -> list[MetricDef]:
    return [
        MetricDef(f"calc:{name}", label, unit, "Computed", lo, hi, numeric)
        for name, (label, unit, numeric, lo, hi) in CALC.items()
    ]


def lhm_metrics(rows: list[tuple[str, str, str, float]]) -> list[MetricDef]:
    """One MetricDef per live LHM sensor.

    `rows` is (identifier, sensor type, name, value) as flattened by
    sources.lhm. Sensors reading exactly zero are still listed -- an idle fan
    header is a legitimate thing to put on screen.
    """
    out = []
    for ident, stype, name, _value in rows:
        unit, lo, hi = TYPE_UNITS.get(stype, ("", 0.0, 100.0))
        group = _group_for(ident)
        out.append(MetricDef(
            ref=f"lhm:{ident}",
            label=f"{name}  [{stype}]",
            unit=unit, group=group, lo=lo, hi=hi, numeric=True,
        ))
    # Stable, human order: group, then label.
    out.sort(key=lambda m: (m.group, m.label.lower()))
    return out


def build(rows: list[tuple[str, str, str, float]]) -> list[MetricDef]:
    return calc_metrics() + lhm_metrics(rows)


def by_ref(catalog: list[MetricDef]) -> dict[str, MetricDef]:
    return {m.ref: m for m in catalog}


def resolve(ref: str, rows_by_id: dict[str, float], calc_values: dict[str, object]):
    """(value, text) for a metric reference. Either may be None."""
    if not ref or ref == "calc:blank":
        return None, ""
    if ref.startswith("lhm:"):
        return rows_by_id.get(ref[4:]), None
    if ref.startswith("calc:"):
        value = calc_values.get(ref[5:])
        if isinstance(value, str):
            return None, value
        return value, None
    return None, None
