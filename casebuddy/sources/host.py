"""Static hardware facts that LibreHardwareMonitor does not publish.

Both of these are fixed for the life of the machine, so they are looked up once
and cached. Everything that actually changes comes from lhm.py.
"""

from __future__ import annotations

import re


def cpu_model() -> str:
    """Marketing name of the CPU, straight from the registry.

    platform.processor() only yields "AMD64 Family 25 Model 33 ...", and a WMI
    query for Win32_Processor takes ~200 ms. The registry read is instant.
    """
    try:
        import winreg

        key = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
            name, _ = winreg.QueryValueEx(handle, "ProcessorNameString")
        text = " ".join(str(name).split())
        for tail in (" Processor", " CPU"):
            text = text.replace(tail, "")
        return re.sub(r"\s+\d+-Core.*$", "", text).strip()
    except Exception:
        import platform

        return platform.processor() or "CPU"


def ram_speed_mts() -> float | None:
    """DIMM transfer rate, e.g. 3200 for DDR4-3200.

    LibreHardwareMonitor exposes DIMM capacity and timings but no memory clock
    sensor, so this is the one number that has to come from WMI.
    ConfiguredClockSpeed is what the modules are actually running at; Speed is
    what they are rated for and can read higher than the board has them set to.
    """
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return None
    try:
        pythoncom.CoInitialize()
        wmi = win32com.client.GetObject(r"winmgmts:\\.\root\cimv2")
        best = None
        query = "SELECT ConfiguredClockSpeed, Speed FROM Win32_PhysicalMemory"
        for row in wmi.ExecQuery(query):
            for attr in ("ConfiguredClockSpeed", "Speed"):
                value = getattr(row, attr, None)
                if value:
                    value = float(value)
                    if best is None or value > best:
                        best = value
                    break
        return best
    except Exception:
        return None


if __name__ == "__main__":  # smoke test
    print("cpu:", cpu_model())
    print("ram:", ram_speed_mts(), "MT/s")
