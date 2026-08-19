#!/usr/bin/env python3
"""CaseBuddy - a case-mounted system telemetry dashboard.

    python casebuddy.py                 run on the panel (auto-detected)
    python casebuddy.py --windowed      small preview window on the main screen
    python casebuddy.py --calibrate     figure out whether the panel stretches
    python casebuddy.py --monitors      list displays and exit
    python casebuddy.py --probe         print one sensor reading and exit

Use casebuddy.pyw (or run.bat) to start it without a console window.
"""

from __future__ import annotations

import argparse
import sys
import time


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="casebuddy", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", metavar="PATH", help="path to config.json")
    p.add_argument("--monitor", metavar="SEL",
                   help='display to use: "auto", "primary", an index (1,2,...), '
                        r'or a device name like \\.\DISPLAY2')
    p.add_argument("--windowed", action="store_true",
                   help="run in a small window on the primary display")
    p.add_argument("--geometry", metavar="WxH+X+Y",
                   help="exact window geometry, e.g. 1920x1080+0+0")
    p.add_argument("--calibrate", action="store_true",
                   help="show the aspect-ratio calibration pattern")
    p.add_argument("--squash", action="store_true",
                   help="force aspect_fix=squash43 for this run")
    p.add_argument("--monitors", action="store_true", help="list displays and exit")
    p.add_argument("--no-tray", action="store_true",
                   help="do not create the notification-area icon")
    p.add_argument("--settings", action="store_true",
                   help="open the settings window on startup")
    p.add_argument("--probe", action="store_true",
                   help="print one sensor reading to the console and exit")
    return p


def cmd_monitors() -> int:
    from casebuddy.window import enable_dpi_awareness, list_monitors

    print("DPI awareness:", enable_dpi_awareness())
    monitors = list_monitors()
    if not monitors:
        print("no monitors reported")
        return 1
    for i, mon in enumerate(monitors, 1):
        print(f"  {i}. {mon}")
    return 0


def cmd_probe(cfg: dict) -> int:
    from casebuddy.collector import Collector

    col = Collector(cfg)
    col.start()
    # One fast tick, plus enough slack for the LHM thread's first (slow) probe.
    print("sampling ...")
    time.sleep(6.0)
    snap = col.latest()

    width = max(len(k) for k in snap.readings) if snap.readings else 8
    for key, r in snap.readings.items():
        if r.value is None:
            print(f"  {key:<{width}}  --           [{r.source or 'unavailable'}]")
        else:
            mark = "~" if r.estimated else " "
            print(f"  {key:<{width}} {mark}{r.value:8.1f} {r.unit:<3} "
                  f"{r.state:<4} {r.detail}  [{r.source}]")
    print()
    for key, value in snap.facts.items():
        print(f"  {key:<10} {value}")
    for note in snap.notices:
        print(f"\n  note: {note}")
    col.stop()
    return 0


def claim_single_instance() -> bool:
    """True if we are the only casebuddy. False means one is already running.

    Matters because there are two plausible startup mechanisms -- a Scheduled
    Task and a Startup-folder shortcut -- and if both are configured, or one is
    configured and you also launch it by hand, you would otherwise get two
    dashboards fighting over the same screen and two sets of sensor polls.

    The mutex is released automatically when the process dies, including on a
    hard kill, so a crash cannot lock us out.
    """
    import ctypes

    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Local\ scopes it to this logon session, which is what we want: a second
    # user logging in should get their own dashboard.
    handle = kernel32.CreateMutexW(None, False, "Local\\casebuddy-single-instance")
    if not handle:
        return True  # cannot tell; let it run rather than refuse to start
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return False
    # Deliberately leaked: the handle must outlive this function for the whole
    # process lifetime.
    globals()["_INSTANCE_MUTEX"] = handle
    return True


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if sys.platform != "win32":
        print("CaseBuddy targets Windows (NVML + LibreHardwareMonitor + Win32 display APIs)")
        return 2

    if args.monitors:
        return cmd_monitors()

    from casebuddy import config

    cfg = config.load(args.config)
    if args.monitor:
        cfg["display"]["monitor"] = args.monitor
    if args.squash:
        cfg["display"]["aspect_fix"] = "squash43"
    if args.geometry:
        cfg["display"]["geometry"] = args.geometry

    if args.probe:
        return cmd_probe(cfg)

    if not claim_single_instance():
        print("CaseBuddy is already running (tray icon is in the notification area)")
        return 0

    from casebuddy import appicon
    from casebuddy.collector import Collector
    from casebuddy.tray import Tray
    from casebuddy.window import MonitorWindow, list_monitors

    # Before any window exists, or the taskbar groups us under the interpreter
    # and shows its icon instead of ours.
    appicon.set_app_id()

    collector = Collector(cfg)
    collector.start()
    window = MonitorWindow(cfg, collector, calibrate=args.calibrate, windowed=args.windowed)

    if not args.no_tray:
        # A chrome-free window has no taskbar button and no Alt-Tab entry, so
        # without this there is no way to reach the app once it is running.
        tray = Tray(
            post=window.post,
            on_settings=window.open_settings,
            on_quit=window.quit,
            on_reload=window.reload_config,
            on_move=window.move_to,
            on_toggle_squash=window.toggle_squash,
            list_monitors=list_monitors,
        )
        if tray.start():
            window.tray = tray

    if cfg["hotkey"]["enabled"]:
        from casebuddy.hotkey import HotKey

        hk = HotKey(cfg["hotkey"]["combo"], lambda: window.post(window.open_settings))
        if hk.start():
            window.hotkey = hk

    # Fullscreen-game / idle-at-night preset switching. Started always and
    # cheap when unconfigured: it reads the live cfg every poll, so switching
    # it on from the settings window needs no restart.
    from casebuddy.autoswitch import AutoSwitcher

    AutoSwitcher(window).start()

    if args.settings:
        window.post(window.open_settings)

    try:
        window.run()
    except KeyboardInterrupt:
        pass
    finally:
        window.collector.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
