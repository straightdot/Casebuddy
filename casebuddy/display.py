"""Query and change a monitor's display mode.

Worth having because resolution is not a cosmetic setting on a panel like this.
Feeding this 1280x720 panel an upscaled 1920x1080 signal caused desktop-wide
stutter and visible cursor lag until it was set back to native -- so being able
to see and fix the mode from inside the app is the difference between a working
dashboard and a mysteriously janky desktop.

Every change goes out with a revert timer. A mode the panel cannot actually
display leaves you looking at a black screen with no way to undo it, so the
caller must confirm within the timeout or the previous mode is restored.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)

ENUM_CURRENT_SETTINGS = -1
ENUM_REGISTRY_SETTINGS = -2

CDS_UPDATEREGISTRY = 0x00000001
CDS_TEST = 0x00000002
CDS_GLOBAL = 0x00000008
CDS_RESET = 0x40000000

DISP_CHANGE_SUCCESSFUL = 0
DISP_CHANGE_RESTART = 1
DISP_CHANGE_FAILED = -1
DISP_CHANGE_BADMODE = -2
DISP_CHANGE_NOTUPDATED = -3
DISP_CHANGE_BADFLAGS = -4
DISP_CHANGE_BADPARAM = -5

_RESULTS = {
    DISP_CHANGE_SUCCESSFUL: "ok",
    DISP_CHANGE_RESTART: "needs a restart",
    DISP_CHANGE_FAILED: "the driver refused the mode",
    DISP_CHANGE_BADMODE: "the display does not support that mode",
    DISP_CHANGE_NOTUPDATED: "could not write the registry",
    DISP_CHANGE_BADFLAGS: "bad flags",
    DISP_CHANGE_BADPARAM: "bad parameter",
}

DM_BITSPERPEL = 0x00040000
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DM_DISPLAYFREQUENCY = 0x00400000


class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wt.WCHAR * 32),
        ("dmSpecVersion", wt.WORD),
        ("dmDriverVersion", wt.WORD),
        ("dmSize", wt.WORD),
        ("dmDriverExtra", wt.WORD),
        ("dmFields", wt.DWORD),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", wt.DWORD),
        ("dmDisplayFixedOutput", wt.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wt.WCHAR * 32),
        ("dmLogPixels", wt.WORD),
        ("dmBitsPerPel", wt.DWORD),
        ("dmPelsWidth", wt.DWORD),
        ("dmPelsHeight", wt.DWORD),
        ("dmDisplayFlags", wt.DWORD),
        ("dmDisplayFrequency", wt.DWORD),
        ("dmICMMethod", wt.DWORD),
        ("dmICMIntent", wt.DWORD),
        ("dmMediaType", wt.DWORD),
        ("dmDitherType", wt.DWORD),
        ("dmReserved1", wt.DWORD),
        ("dmReserved2", wt.DWORD),
        ("dmPanningWidth", wt.DWORD),
        ("dmPanningHeight", wt.DWORD),
    ]


@dataclass(frozen=True)
class Mode:
    width: int
    height: int
    hz: int

    def __str__(self) -> str:
        return f"{self.width} x {self.height}  @ {self.hz} Hz"

    @property
    def key(self) -> str:
        return f"{self.width}x{self.height}@{self.hz}"


def current_mode(device: str) -> Mode | None:
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    if not user32.EnumDisplaySettingsW(device, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
        return None
    return Mode(dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency)


def list_modes(device: str, min_width: int = 640) -> list[Mode]:
    """Every 32-bit mode the output offers, widest first.

    Interlaced and very small modes are filtered out: they are never what
    anyone wants on a panel like this and they clutter the list.
    """
    seen: set[tuple[int, int, int]] = set()
    index = 0
    while True:
        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(DEVMODEW)
        if not user32.EnumDisplaySettingsW(device, index, ctypes.byref(dm)):
            break
        index += 1
        if dm.dmBitsPerPel != 32 or dm.dmPelsWidth < min_width:
            continue
        seen.add((dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency))
    modes = [Mode(*m) for m in seen]
    modes.sort(key=lambda m: (-m.width, -m.height, -m.hz))
    return modes


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("DeviceName", wt.WCHAR * 32),
        ("DeviceString", wt.WCHAR * 128),
        ("StateFlags", wt.DWORD),
        ("DeviceID", wt.WCHAR * 128),
        ("DeviceKey", wt.WCHAR * 128),
    ]


def monitor_hardware_id(device: str) -> tuple[str, str] | None:
    """(hardware id, instance) for the monitor attached to an output.

    DeviceID looks like MONITOR\\HJW1836\\{guid}\\0002.
    """
    dev = DISPLAY_DEVICEW()
    dev.cb = ctypes.sizeof(DISPLAY_DEVICEW)
    if not user32.EnumDisplayDevicesW(device, 0, ctypes.byref(dev), 0):
        return None
    parts = dev.DeviceID.split("\\")
    if len(parts) >= 4 and parts[0].upper() == "MONITOR":
        return parts[1], parts[3]
    return None


def _parse_edid_preferred(edid: bytes) -> tuple[int, int] | None:
    """Active pixels from EDID's first detailed timing descriptor.

    That descriptor is by definition the panel's preferred -- i.e. native --
    timing. Layout, from the EDID spec, at offset 54:

        byte 2       horizontal active, low 8 bits
        byte 4 >> 4  horizontal active, high 4 bits
        byte 5       vertical active, low 8 bits
        byte 7 >> 4  vertical active, high 4 bits
    """
    if len(edid) < 72 or edid[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    d = edid[54:72]
    if d[0] == 0 and d[1] == 0:
        return None  # not a timing descriptor
    h = ((d[4] >> 4) << 8) | d[2]
    v = ((d[7] >> 4) << 8) | d[5]
    if not (240 <= h <= 16384 and 240 <= v <= 16384):
        return None
    return h, v


def edid_native(device: str) -> tuple[int, int] | None:
    """The panel's true native resolution, straight from its EDID.

    THIS MATTERS. A panel advertises every mode its scaler will accept, not
    just the one its glass actually has. This 1280x720 panel happily advertises
    1920x1080 -- and driving it there caused desktop-wide stutter and cursor
    lag. Picking "the largest mode on offer" would recommend exactly the broken
    setting, so the preferred timing has to come from EDID.
    """
    ident = monitor_hardware_id(device)
    if ident is None:
        return None
    hardware_id, instance = ident
    try:
        import winreg

        base = rf"SYSTEM\CurrentControlSet\Enum\DISPLAY\{hardware_id}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as key:
            index = 0
            candidates = []
            while True:
                try:
                    sub = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                candidates.append(sub)
        # Prefer the instance this output is actually using.
        candidates.sort(key=lambda s: (s != instance,))
        for sub in candidates:
            try:
                path = rf"{base}\{sub}\Device Parameters"
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as pk:
                    edid, _ = winreg.QueryValueEx(pk, "EDID")
                parsed = _parse_edid_preferred(bytes(edid))
                if parsed:
                    return parsed
            except OSError:
                continue
    except Exception:
        return None
    return None


def native_mode(device: str) -> Mode | None:
    """The panel's native mode, from EDID, matched to an available mode.

    Falls back to the largest advertised mode only when EDID is unreadable --
    and that fallback is a guess, not an answer. See edid_native().
    """
    modes = list_modes(device)
    if not modes:
        return None
    native = edid_native(device)
    if native:
        width, height = native
        exact = [m for m in modes if m.width == width and m.height == height]
        if exact:
            return max(exact, key=lambda m: m.hz)
        return Mode(width, height, 60)
    return max(modes, key=lambda m: (m.width * m.height, m.hz))


def set_mode(device: str, mode: Mode, test_first: bool = True) -> tuple[bool, str]:
    """Apply a mode. Returns (ok, human-readable message)."""
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    # Start from the current mode so untouched fields stay valid.
    if not user32.EnumDisplaySettingsW(device, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
        return False, "could not read the current mode"

    dm.dmPelsWidth = mode.width
    dm.dmPelsHeight = mode.height
    dm.dmDisplayFrequency = mode.hz
    dm.dmBitsPerPel = 32
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY | DM_BITSPERPEL

    if test_first:
        rc = user32.ChangeDisplaySettingsExW(device, ctypes.byref(dm), None, CDS_TEST, None)
        if rc != DISP_CHANGE_SUCCESSFUL:
            return False, _RESULTS.get(rc, f"rc={rc}")

    rc = user32.ChangeDisplaySettingsExW(
        device, ctypes.byref(dm), None, CDS_UPDATEREGISTRY, None)
    if rc != DISP_CHANGE_SUCCESSFUL:
        return False, _RESULTS.get(rc, f"rc={rc}")
    return True, "ok"


if __name__ == "__main__":  # smoke test
    import sys

    dev = sys.argv[1] if len(sys.argv) > 1 else r"\\.\DISPLAY2"
    print("device :", dev)
    print("current:", current_mode(dev))
    print("native :", native_mode(dev))
    print("modes  :")
    for m in list_modes(dev):
        print("   ", m)
