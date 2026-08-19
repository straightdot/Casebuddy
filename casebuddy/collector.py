"""Background sampling thread that turns one LHM read into a Snapshot.

Slots are not hardcoded. Every tile on screen is described by cfg["layout"],
which names a metric reference per slot; this resolves those references against
the live sensor set and produces one Reading each. That is what lets the Layout
tab repoint any tile at any of the ~190 available metrics without a code change.

The UI thread never touches a sensor. It reads `collector.latest()`, a plain
object swapped in under a lock, so a slow HTTP round trip cannot stall a
repaint. If the backend throws, the thread logs once and keeps its previous
value rather than blanking the screen.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from . import catalog
from .metrics import NA, Reading, Snapshot, classify, fraction
from .sources.host import cpu_model, ram_speed_mts
from .sources.lhm import LhmReader, LhmSample
from .sources.weather import WeatherWatcher


def fmt_value(value: float | None, unit: str) -> str:
    """One number plus its unit, with a precision that suits the unit."""
    if value is None:
        return ""
    if unit == "%":
        return f"{value:.0f}%"
    if unit == "V":
        return f"{value:.3f} V"
    if unit in ("GB", "MB"):
        return f"{value:.1f} {unit}"
    if not unit:
        return f"{value:.0f}"
    return f"{value:.0f} {unit}"


def _clock(pattern, fallback: str) -> str:
    """strftime, but a typo in a format string must not blank the header.

    These come straight from a text box in the settings window, so an invalid
    pattern is an ordinary thing to hit halfway through typing one.
    """
    for attempt in (str(pattern or ""), fallback):
        if not attempt:
            continue
        try:
            return time.strftime(attempt)
        except (ValueError, TypeError):
            continue
    return ""


def fmt_used_total(used: float | None, total: float | None, unit: str = "GB") -> str:
    if used is None or not total:
        return ""
    return f"{used:.1f} / {total:.1f} {unit}"


class Collector:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self._lhm = LhmReader(
            enabled=bool(cfg["lhm"]["enabled"]),
            transport=str(cfg["lhm"]["transport"]),
            http_url=str(cfg["lhm"]["http_url"]),
        )
        self._dt = 1.0 / max(0.2, float(cfg["refresh"]["fast_poll_hz"]))
        # Its own thread, on a quarter-hour clock. A weather request can hang
        # for its full timeout; a sensor poll happens twice a second. Neither
        # may be able to stall the other.
        self.weather = WeatherWatcher(cfg)

        self._lock = threading.Lock()
        self._snapshot = Snapshot(ts=time.time())
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._logged: set[str] = set()

        self._cpu_name = cpu_model()
        self._ram_speed: float | None = None
        self._ram_speed_done = False
        # Last raw sensor list, so the settings editor can offer a catalogue of
        # what is actually present without doing its own poll.
        self.rows: list = []

    # --- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="casebuddy-collector", daemon=True)
        self._thread.start()
        self.weather.start()

    def stop(self) -> None:
        self._stop.set()
        self.weather.stop()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def latest(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    def _log_once(self, key: str, message: str) -> None:
        if key not in self._logged:
            self._logged.add(key)
            print(f"[casebuddy] {message}")

    # --- sampling loop ----------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                if not self._ram_speed_done:
                    self._ram_speed_done = True
                    self._ram_speed = ram_speed_mts()
                sample = self._lhm.sample()
                self.rows = sample.rows
                snap = self._build(sample)
                with self._lock:
                    self._snapshot = snap
            except Exception as exc:  # a dead sensor must not kill the thread
                self._log_once(f"build:{type(exc).__name__}", f"collector error: {exc!r}")
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.05, self._dt - elapsed))

    # --- metric plumbing --------------------------------------------------

    def _calc_values(self, s: LhmSample) -> dict[str, Any]:
        """Every portable "calc:" metric, resolved for this sample."""
        power_w, power_modeled, breakdown = self._power(s)
        outside = self.weather.latest()
        vram_pct = None
        if s.vram_used_mb is not None and s.vram_total_mb:
            vram_pct = s.vram_used_mb / s.vram_total_mb * 100.0
        return {
            "cpu_temp": s.cpu_temp_c,
            "cpu_power": s.cpu_package_w,
            "cpu_load": s.cpu_load_pct,
            "cpu_clock": s.cpu_clock_mhz,
            "cpu_clock_nominal": s.cpu_clock_nominal_mhz,
            "cpu_fan_rpm": s.cpu_fan_rpm,
            "cpu_fan_pct": s.cpu_fan_pct,
            "mobo_temp": s.mobo_temp_c,
            "gpu_temp": s.gpu_temp_c,
            "gpu_hotspot": s.gpu_hotspot_c,
            "gpu_power": s.gpu_power_w,
            "gpu_load": s.gpu_load_pct,
            "gpu_clock": s.gpu_core_mhz,
            "gpu_mem_clock": s.gpu_mem_mhz,
            "gpu_fan_rpm": s.gpu_fan_rpm,
            "gpu_fan_pct": s.gpu_fan_pct,
            "ram_pct": s.ram_pct,
            "ram_gb": fmt_used_total(s.ram_used_gb, s.ram_total_gb),
            "ram_speed": self._ram_speed,
            "vram_pct": vram_pct,
            "vram_gb": fmt_used_total(
                None if s.vram_used_mb is None else s.vram_used_mb / 1024.0,
                None if s.vram_total_mb is None else s.vram_total_mb / 1024.0),
            "system_power": power_w,
            "power_breakdown": breakdown,
            "outside_temp": outside.temp_c if outside and outside.ok else None,
            "outside_feels": outside.feels_c if outside and outside.ok else None,
            "weather": outside.description if outside and outside.ok else "",
            "weather_place": outside.place if outside and outside.ok else "",
            "weather_line": outside.line if outside and outside.ok else "",
            "cpu_name": self._cpu_name,
            "gpu_name": s.gpu_name or "GPU",
            "date": _clock(self.cfg["display"].get("date_format"), "%a %d %b"),
            "clock": _clock(self.cfg["display"].get("clock_format"), "%#I:%M %p"),
            "blank": "",
            "_power_modeled": power_modeled,
        }

    def _meta(self, ref: str, rows_by_id: dict) -> catalog.MetricDef | None:
        """Unit and gauge range for a reference, without rebuilding the catalogue."""
        if ref.startswith("calc:"):
            name = ref[5:]
            entry = catalog.CALC.get(name)
            if entry:
                label, unit, numeric, lo, hi = entry
                return catalog.MetricDef(ref, label, unit, "Computed", lo, hi, numeric)
            return None
        if ref.startswith("lhm:"):
            ident = ref[4:]
            for row_id, stype, name, _v in self.rows:
                if row_id == ident:
                    unit, lo, hi = catalog.TYPE_UNITS.get(stype, ("", 0.0, 100.0))
                    return catalog.MetricDef(ref, name, unit, "", lo, hi, True)
        return None

    def _text_for(self, ref: str, rows_by_id: dict, calc: dict) -> str:
        value, text = catalog.resolve(ref, rows_by_id, calc)
        if text is not None:
            return text
        meta = self._meta(ref, rows_by_id)
        return fmt_value(value, meta.unit if meta else "")

    def _slot(self, slot: dict, rows_by_id: dict, calc: dict, kind: str) -> Reading:
        ref = str(slot.get("metric", "calc:blank"))
        value, text = catalog.resolve(ref, rows_by_id, calc)
        meta = self._meta(ref, rows_by_id)
        unit = meta.unit if meta else ""

        lo = float(slot.get("min", meta.lo if meta else 0.0))
        hi = float(slot.get("max", meta.hi if meta else 100.0))

        key = slot.get("thresholds")
        state = NA
        if value is not None:
            if key and key in self.cfg["thresholds"]:
                warn, crit = self.cfg["thresholds"][key]
                # Power thresholds are a percentage of the gauge's full scale,
                # not watts, so the comparison has to be normalised.
                probe = value / hi * 100.0 if key == "power" and hi else value
                state = classify(probe, float(warn), float(crit))
            else:
                state = "ok"

        estimated = ref == "calc:system_power"
        return Reading(
            value=value,
            unit=unit if value is not None else "",
            fraction=fraction(value, lo, hi),
            detail=self._text_for(str(slot.get("detail", "calc:blank")), rows_by_id, calc),
            top=self._text_for(str(slot.get("top", "calc:blank")), rows_by_id, calc),
            state=state,
            estimated=estimated,
            source=ref,
        )

    def _build(self, s: LhmSample) -> Snapshot:
        rows_by_id = {ident: value for ident, _t, _n, value in s.rows}
        calc = self._calc_values(s)
        layout = self.cfg["layout"]

        readings: dict[str, Reading] = {}
        # Header slots are pure text: two metrics joined by a separator. They
        # carry no gauge, so they skip the threshold/fraction machinery.
        for index, slot in enumerate(layout.get("header", [])):
            left = self._text_for(str(slot.get("metric", "calc:blank")), rows_by_id, calc)
            right = self._text_for(str(slot.get("detail", "calc:blank")), rows_by_id, calc)
            parts = [p for p in (left, right) if p]
            readings[f"hdr{index}"] = Reading(
                detail=str(slot.get("sep", "   ")).join(parts),
                state="ok", source=str(slot.get("metric", "")))

        # Every kind is resolved whichever screen mode is active, so switching
        # between the gauge and buddy layouts needs no collector restart.
        for kind, prefix in (("rings", "ring"), ("bars", "bar"),
                             ("fans", "fan"), ("stats", "stat")):
            for index, slot in enumerate(layout.get(kind, [])):
                readings[f"{prefix}{index}"] = self._slot(slot, rows_by_id, calc, kind)

        notices: list[str] = []
        if not s.ok and s.status:
            notices.append(s.status)

        facts = {
            "cpu_name": self._cpu_name,
            "gpu_name": s.gpu_name or "GPU",
            "date": calc["date"],
            "clock": calc["clock"],
        }
        # Numerics only: the buddy scene judges mood from these, and a "7.0 /
        # 15.9 GB" string is not something it can threshold.
        vitals = {name: value for name, value in calc.items()
                  if isinstance(value, (int, float)) and not isinstance(value, bool)}
        return Snapshot(ts=time.time(), readings=readings, facts=facts,
                        notices=notices, vitals=vitals, weather=self.weather.latest())

    # --- helpers ----------------------------------------------------------

    def _power(self, s: LhmSample) -> tuple[float | None, bool, str]:
        """Wall-plug estimate, plus the CPU/GPU split shown beneath it.

        A desktop exposes no whole-system power sensor, so this sums the two
        parts that ARE measured and adds a fixed baseline, then divides by PSU
        efficiency. Those last two are assumptions, which is why the tile keeps
        its leading "~" even when both measured terms are present.
        """
        pw = self.cfg["power"]
        cpu_w = s.cpu_package_w
        modeled = False
        if cpu_w is None and pw["estimate_cpu_when_missing"] and s.cpu_load_pct is not None:
            idle, peak = float(pw["cpu_idle_w"]), float(pw["cpu_max_w"])
            cpu_w = idle + (peak - idle) * (s.cpu_load_pct / 100.0)
            modeled = True

        gpu_w = s.gpu_power_w
        mark = "~" if modeled else ""
        cpu_txt = f"{mark}{cpu_w:.0f} W" if cpu_w is not None else "-- W"
        gpu_txt = f"{gpu_w:.0f} W" if gpu_w is not None else "-- W"
        breakdown = f"CPU {cpu_txt}   GPU {gpu_txt}"

        if cpu_w is None and gpu_w is None:
            return None, modeled, breakdown

        efficiency = max(0.5, min(1.0, float(pw["psu_efficiency"])))
        dc = (cpu_w or 0.0) + (gpu_w or 0.0) + float(pw["baseline_w"])
        return dc / efficiency, modeled, breakdown


if __name__ == "__main__":  # smoke test
    from . import config

    col = Collector(config.load())
    col.start()
    time.sleep(2.5)
    snap = col.latest()
    for key, reading in snap.readings.items():
        print(f"{key:6s} {reading.value!s:>8}  {reading.unit:<4} "
              f"{reading.state:<4} top={reading.top!r} detail={reading.detail!r}")
    print("notices:", snap.notices)
    col.stop()
