"""The single sensor source: LibreHardwareMonitor over its HTTP endpoint.

Everything on the dashboard comes from here. That is a deliberate choice.
LHM is already a hard requirement for CPU die temperature, package power and
fan RPM -- none of which Windows exposes to unprivileged code -- so reading the
GPU and memory from a *second* source only bought the ability to keep half a
dashboard alive while the other half showed dashes. One source means one
failure mode and one sampling instant.

Two transports:

  HTTP  GET /data.json. The default. Values arrive as display strings
        ("45.6 <deg>C"), which are locale-formatted -- a German box writes
        "45,6" -- so the parser handles both separators.
  WMI   root\\LibreHardwareMonitor. Removed in LHM 0.9.6; kept for old builds.
        Probing a namespace that does not exist blocks for ~4 s before failing,
        which is why it is no longer tried by default.

If LHM is not running, `sample()` returns empty fields and `status` explains
how to fix it. The dashboard renders dashes and keeps going.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_HTTP_URL = "http://localhost:8085/data.json"
WMI_NAMESPACE = r"root\LibreHardwareMonitor"

SETUP_HINT = "LibreHardwareMonitor is not running"


@dataclass
class LhmSample:
    # --- CPU ---
    cpu_temp_c: float | None = None
    cpu_package_w: float | None = None
    cpu_load_pct: float | None = None
    # Busiest core's effective clock. The average collapses to ~180 MHz at idle
    # because parked cores drag it down, which reads as a fault rather than idle.
    cpu_clock_mhz: float | None = None
    # The requested boost multiplier: 4200 MHz here, and frozen there.
    cpu_clock_nominal_mhz: float | None = None
    cpu_fan_rpm: float | None = None
    cpu_fan_pct: float | None = None
    mobo_temp_c: float | None = None

    # --- GPU ---
    gpu_name: str | None = None
    gpu_temp_c: float | None = None
    gpu_hotspot_c: float | None = None
    gpu_core_mhz: float | None = None
    gpu_mem_mhz: float | None = None
    gpu_power_w: float | None = None
    gpu_load_pct: float | None = None
    gpu_fan_rpm: float | None = None
    gpu_fan_pct: float | None = None
    vram_used_mb: float | None = None
    vram_total_mb: float | None = None

    # --- memory ---
    ram_pct: float | None = None
    ram_used_gb: float | None = None
    ram_avail_gb: float | None = None

    fans_rpm: dict[str, float] = field(default_factory=dict)
    # Every sensor this poll saw: (identifier, type, name, value). Carried
    # so the settings editor can offer any of them as a data source without
    # a second round trip, and so the catalogue reflects what is actually
    # present rather than a curated guess.
    rows: list = field(default_factory=list)
    source: str = "unavailable"
    status: str = SETUP_HINT

    @property
    def ok(self) -> bool:
        return self.source != "unavailable"

    @property
    def ram_total_gb(self) -> float | None:
        if self.ram_used_gb is None or self.ram_avail_gb is None:
            return None
        return self.ram_used_gb + self.ram_avail_gb


# --- sensor matching ------------------------------------------------------
#
# Names differ by CPU generation, GPU vendor and super-I/O chip, so each metric
# gets an ordered candidate list rather than one hard-coded string. First hit
# wins. Verified against a Ryzen 7 5700X / RTX 3070 / MSI B550 (NCT6687D).
#
# (identifier_prefix_regex, sensor_type, name_regex)

_Rule = tuple[str, str, str]

RULES: dict[str, list[_Rule]] = {
    "cpu_temp_c": [
        (r"^/amdcpu/", "Temperature", r"^Core \(Tctl/Tdie\)$"),
        (r"^/amdcpu/", "Temperature", r"^CCD1 \(Tdie\)$"),
        (r"^/intelcpu/", "Temperature", r"^CPU Package$"),
        (r"^/(amd|intel)cpu/", "Temperature", r"Tdie|Tctl"),
        (r"^/(amd|intel)cpu/", "Temperature", r"^Core (Average|Max)$"),
        (r"^/lpc/", "Temperature", r"^CPU$"),
    ],
    "cpu_package_w": [
        (r"^/(amd|intel)cpu/", "Power", r"^Package$"),
        (r"^/(amd|intel)cpu/", "Power", r"^CPU Package$"),
    ],
    "cpu_load_pct": [
        (r"^/(amd|intel)cpu/", "Load", r"^CPU Total$"),
    ],
    "cpu_fan_rpm": [
        (r"^/lpc/", "Fan", r"^CPU Fan$"),
        (r"^/lpc/", "Fan", r"^CPU"),
    ],
    "cpu_fan_pct": [
        (r"^/lpc/", "Control", r"^CPU Fan$"),
        (r"^/lpc/", "Control", r"^CPU"),
    ],
    "mobo_temp_c": [
        (r"^/lpc/", "Temperature", r"^(System|Motherboard)$"),
        (r"^/lpc/", "Temperature", r"^VRM"),
    ],
    "gpu_temp_c": [
        (r"^/gpu-", "Temperature", r"^GPU Core$"),
        (r"^/gpu-", "Temperature", r"^GPU$"),
    ],
    "gpu_hotspot_c": [
        (r"^/gpu-", "Temperature", r"Hot ?Spot"),
    ],
    "gpu_core_mhz": [
        (r"^/gpu-", "Clock", r"^GPU Core$"),
    ],
    "gpu_mem_mhz": [
        (r"^/gpu-", "Clock", r"^GPU Memory$"),
    ],
    "gpu_power_w": [
        (r"^/gpu-", "Power", r"^GPU Package$"),
        (r"^/gpu-", "Power", r"^GPU Board Power$"),
    ],
    "gpu_load_pct": [
        (r"^/gpu-", "Load", r"^GPU Core$"),
    ],
    # SmallData is LHM's megabyte-valued type.
    #
    # Three different VRAM figures are on offer and they disagree. Measured
    # together on an RTX 3070:
    #
    #   /vram/load                  30.1 %    Windows' SHARED graphics budget
    #                                         (9.3 of 21.6 GB) -- not the card
    #   GPU Memory Used             975 MB    NVML v1: includes the ~175 MB
    #                                         driver-reserved block
    #   D3D Dedicated Memory Used   807 MB    matches nvidia-smi's 801 MiB and
    #                                         Task Manager
    #
    # Task Manager is the reference anyone can cross-check against, so the D3D
    # figure wins. GPU Memory Used is the fallback: it is the more complete
    # "occupancy" number but reads ~2 percentage points high against every
    # other tool, which invites a bug report rather than trust.
    "vram_used_mb": [
        (r"^/gpu-", "SmallData", r"^D3D Dedicated Memory Used$"),
        (r"^/gpu-", "SmallData", r"^GPU Memory Used$"),
    ],
    "vram_total_mb": [
        (r"^/gpu-", "SmallData", r"^GPU Memory Total$"),
    ],
    "ram_pct": [
        (r"^/ram/", "Load", r"^Memory$"),
    ],
    # Data is LHM's gigabyte-valued type.
    "ram_used_gb": [
        (r"^/ram/", "Data", r"^Memory Used$"),
    ],
    "ram_avail_gb": [
        (r"^/ram/", "Data", r"^Memory Available$"),
    ],
}


def _parse_value(raw) -> float | None:
    """Coerce a WMI float or an LHM display string ('45,6 °C') to a number."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return None if value != value else value  # drop NaN
    text = str(raw).strip()
    if not text:
        return None
    m = re.match(r"^[-+]?\d+(?:[.,]\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


class _SensorTable:
    """Flat view of everything LHM is publishing, indexed for rule matching."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, float]] = []  # ident, type, name, value
        self.hardware: dict[str, str] = {}  # ident prefix -> hardware name

    def add(self, ident: str, stype: str, name: str, value: float | None,
            hardware: str = "") -> None:
        if value is None:
            return
        ident = ident or ""
        self.rows.append((ident, stype or "", name or "", value))
        if hardware:
            key = "/".join(ident.strip("/").split("/")[:2])
            self.hardware.setdefault("/" + key, hardware)

    def match(self, rules: list[_Rule]) -> float | None:
        for ident_re, stype, name_re in rules:
            for ident, row_type, name, value in self.rows:
                if row_type != stype or not re.search(ident_re, ident):
                    continue
                if not re.search(name_re, name):
                    continue
                return value
        return None

    def match_max(self, ident_re: str, stype: str, name_re: str) -> float | None:
        """Largest value across every sensor matching the pattern."""
        best = None
        for ident, row_type, name, value in self.rows:
            if row_type != stype or not re.search(ident_re, ident):
                continue
            if not re.search(name_re, name):
                continue
            if best is None or value > best:
                best = value
        return best

    def hardware_name(self, prefix_re: str) -> str | None:
        for key, name in self.hardware.items():
            if re.search(prefix_re, key):
                return name
        return None

    def chassis_fans(self) -> dict[str, float]:
        out = {}
        for ident, row_type, name, value in self.rows:
            if row_type == "Fan" and value > 0 and not ident.startswith("/gpu-"):
                out[name] = value
        return out

    def __bool__(self) -> bool:
        return bool(self.rows)


class LhmReader:
    def __init__(
        self,
        enabled: bool = True,
        transport: str = "http",  # http | auto | wmi | off
        http_url: str = DEFAULT_HTTP_URL,
        retry_s: float = 15.0,
    ) -> None:
        self.enabled = enabled and transport != "off"
        self.transport = transport
        self.http_url = http_url
        self.retry_s = retry_s
        self.status = "not started"

        self._wmi_svc = None
        self._wmi_dead_until = 0.0
        self._http_dead_until = 0.0
        self._com_ready = False
        # Set only for problems the user must fix, as opposed to the ordinary
        # "LHM simply is not running" case, which the setup hint covers better.
        self._hard_error: str | None = None

    # --- WMI transport ----------------------------------------------------

    def _wmi_table(self) -> _SensorTable | None:
        now = time.monotonic()
        if now < self._wmi_dead_until:
            return None
        try:
            import pythoncom
            import win32com.client
        except ImportError:
            self._wmi_dead_until = float("inf")
            return None

        try:
            # The collector runs on a worker thread, and COM is per-thread.
            if not self._com_ready:
                pythoncom.CoInitialize()
                self._com_ready = True
            if self._wmi_svc is None:
                locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
                self._wmi_svc = locator.ConnectServer(".", WMI_NAMESPACE)

            table = _SensorTable()
            query = "SELECT Identifier, Name, SensorType, Value FROM Sensor"
            for row in self._wmi_svc.ExecQuery(query):
                table.add(str(row.Identifier or ""), str(row.SensorType or ""),
                          str(row.Name or ""), _parse_value(row.Value))
            if not table:
                self._wmi_svc = None
                self._wmi_dead_until = now + self.retry_s
                return None
            return table
        except Exception as exc:
            self._wmi_svc = None
            self._wmi_dead_until = now + self.retry_s
            if "Invalid namespace" not in str(exc):
                self._hard_error = f"LHM WMI: {exc}"
            return None

    # --- HTTP transport ---------------------------------------------------

    @staticmethod
    def _walk(node: dict, table: _SensorTable, depth: int = 0, hardware: str = "") -> None:
        """Depth 0 is the root, 1 the computer, 2 the hardware device."""
        text = str(node.get("Text") or "")
        if depth == 2 and not node.get("SensorId"):
            hardware = text
        if node.get("SensorId") and node.get("Type"):
            table.add(str(node["SensorId"]), str(node["Type"]), text,
                      _parse_value(node.get("Value")), hardware)
        for child in node.get("Children") or ():
            if isinstance(child, dict):
                LhmReader._walk(child, table, depth + 1, hardware)

    def _http_table(self) -> _SensorTable | None:
        now = time.monotonic()
        if now < self._http_dead_until:
            return None
        try:
            with urllib.request.urlopen(self.http_url, timeout=2.0) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            self._http_dead_until = now + self.retry_s
            if isinstance(exc, ValueError):
                self._hard_error = f"LHM returned unparsable JSON: {exc}"
            return None
        table = _SensorTable()
        if isinstance(payload, dict):
            self._walk(payload, table)
        if not table:
            self._http_dead_until = now + self.retry_s
            return None
        return table

    # --- public -----------------------------------------------------------

    def sample(self) -> LhmSample:
        if not self.enabled:
            return LhmSample(status="disabled in config")

        table = None
        used = ""
        self._hard_error = None
        if self.transport in ("http", "auto"):
            table = self._http_table()
            used = "lhm-http"
        if table is None and self.transport in ("auto", "wmi"):
            table = self._wmi_table()
            used = "lhm-wmi"

        if table is None:
            self.status = self._hard_error or SETUP_HINT
            return LhmSample(status=self.status)

        self.status = "ok"
        s = LhmSample(source=used, status="ok")
        for attr, rules in RULES.items():
            setattr(s, attr, table.match(rules))

        # Clocks: the busiest core's effective figure, plus the frozen ceiling.
        s.cpu_clock_mhz = table.match_max(
            r"^/(amd|intel)cpu/", "Clock", r"\(Effective\)$"
        )
        s.cpu_clock_nominal_mhz = table.match_max(
            r"^/(amd|intel)cpu/", "Clock", r"^(Cores \(Average\)|Core #\d+)$"
        )
        # A dual-fan card reports each rotor separately; the faster one is the
        # meaningful number and all the strip has room for.
        s.gpu_fan_rpm = table.match_max(r"^/gpu-", "Fan", r".")
        s.gpu_fan_pct = table.match_max(r"^/gpu-", "Control", r".")
        s.gpu_name = table.hardware_name(r"^/gpu-")

        s.fans_rpm = table.chassis_fans()
        s.rows = list(table.rows)
        if s.cpu_fan_rpm is None and s.fans_rpm:
            s.cpu_fan_rpm = max(s.fans_rpm.values())
        return s


if __name__ == "__main__":  # smoke test
    r = LhmReader()
    print(r.sample())
