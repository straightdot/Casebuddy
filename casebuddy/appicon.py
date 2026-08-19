"""The CaseBuddy mark, in the three forms Windows asks for.

One drawing, three consumers: the notification area wants an HICON loaded from
a file, Tk wants a PhotoImage for `iconphoto`, and the taskbar wants an .ico
through `iconbitmap`. Keeping them in one place is why the tray badge and the
taskbar button can never end up showing different art.

The taskbar part needs a nudge. The dashboard window is `overrideredirect`, so
it has no taskbar button by design -- that is the whole point of an appliance
display -- and a Toplevel owned by such a window does not get one either. The
settings window therefore asks for WS_EX_APPWINDOW explicitly, which is the
documented way to say "this one IS a real window, give it a button".
"""

from __future__ import annotations

import ctypes
import os
import tempfile

from . import theme

try:
    from PIL import Image, ImageDraw, ImageTk
    HAVE_PIL = True
except ImportError:  # pragma: no cover - depends on the install
    HAVE_PIL = False

GWL_EXSTYLE = -20
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
WM_SETICON = 0x0080
ICON_SMALL, ICON_BIG, ICON_SMALL2 = 0, 1, 2
GCLP_HICON, GCLP_HICONSM = -14, -34
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040

# DISABLED, and measured rather than assumed. Setting an AppUserModelID makes
# the shell resolve the taskbar button's artwork through the shortcut database
# instead of through the window, and on this machine it resolved to NOTHING --
# a blank button. Tried with the id alone, with a Start-menu shortcut carrying
# the .ico, and with System.AppUserModel.ID written onto that shortcut through
# IPropertyStore; blank every time. Empty is worse than borrowing the
# interpreter's icon, so the id stays off.
#
# The window itself is correct either way: WM_SETICON and the class icon are
# both set below, which is why the title bar, Alt-Tab and the tray all show the
# gauge ring. Only the taskbar BUTTON follows the executable, and the way to
# change that is to launch from a purpose-built exe with the icon compiled in.
APP_ID = ""

_ICO: str | None = None
_PHOTO = None


def set_app_id() -> None:
    """Group the taskbar button under CaseBuddy rather than the interpreter.

    DELIBERATELY A NO-OP BY DEFAULT. Setting an explicit AppUserModelID makes
    the shell look the id up in the shortcut database for its artwork, and with
    no Start-menu shortcut registered under that id it finds none -- the button
    then draws EMPTY, which is worse than borrowing the interpreter's icon.
    Left in place, and callable, for whenever a real installer exists to
    register the shortcut alongside it.
    """
    if not APP_ID:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def _draw(size: int):
    """The gauge ring: a track, and an arc filled to about two thirds."""
    scale = 4
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = int(big * 0.10)
    box = (pad, pad, big - pad, big - pad)
    width = int(big * 0.22)
    draw.arc(box, start=135, end=45, fill=theme.TRACK, width=width)
    draw.arc(box, start=135, end=300, fill=theme.OK, width=width)
    # Rendered at 4x and resampled, because PIL's arc has no antialiasing and
    # a 16 px ring drawn directly comes out as a staircase.
    return image.resize((size, size), Image.LANCZOS)


def ico_path() -> str:
    """A multi-size .ico on disk. Written once per run.

    Several sizes in one file so Windows can pick per-DPI rather than scaling
    a single bitmap badly.
    """
    global _ICO
    if _ICO and os.path.isfile(_ICO):
        return _ICO
    if not HAVE_PIL:
        return ""
    path = os.path.join(tempfile.gettempdir(), "casebuddy.ico")
    try:
        _draw(256).save(path, format="ICO",
                        sizes=[(16, 16), (20, 20), (24, 24), (32, 32),
                               (48, 48), (64, 64), (128, 128), (256, 256)])
    except OSError:
        return ""
    _ICO = path
    return path


def photo(widget):
    """A PhotoImage of the mark, cached. None when Pillow is missing."""
    global _PHOTO
    if _PHOTO is not None or not HAVE_PIL:
        return _PHOTO
    try:
        _PHOTO = ImageTk.PhotoImage(_draw(64), master=widget)
    except Exception:
        _PHOTO = None
    return _PHOTO


def apply_to(window, taskbar: bool = False) -> None:
    """Give a Tk window the mark, and optionally a taskbar button.

    Never raises: an icon is decoration, and a machine without Pillow or an
    older Tk should still get its settings window.
    """
    try:
        image = photo(window)
        if image is not None:
            # default=True so dialogs opened from here inherit it too.
            window.iconphoto(True, image)
    except Exception:
        pass
    try:
        path = ico_path()
        if path:
            window.iconbitmap(default=path)
    except Exception:
        pass
    if taskbar:
        _show_in_taskbar(window)


def _show_in_taskbar(window) -> None:
    try:
        window.update_idletasks()
        hwnd = int(window.wm_frame(), 16)
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        _set_native_icon(hwnd)
        # The button only appears on the next map, so bounce it.
        window.withdraw()
        window.deiconify()
    except Exception:
        pass


def _set_native_icon(hwnd: int) -> None:
    """Hand the window a real HICON.

    Tk's iconphoto sets the icon Tk knows about; the taskbar reads the one the
    window answers WM_SETICON with. Setting both is what makes the button, the
    title bar and Alt-Tab agree.
    """
    path = ico_path()
    if not path:
        return
    user32 = ctypes.windll.user32
    handles = {}
    for which, size in ((ICON_SMALL, 16), (ICON_SMALL2, 16), (ICON_BIG, 32)):
        handle = handles.get(size)
        if handle is None:
            handle = user32.LoadImageW(None, path, IMAGE_ICON, size, size,
                                       LR_LOADFROMFILE)
            handles[size] = handle
        if handle:
            user32.SendMessageW(hwnd, WM_SETICON, which, handle)
    # The taskbar button often reads the window CLASS icon rather than the one
    # the window answers WM_SETICON with, so set both.
    setter = getattr(user32, "SetClassLongPtrW", None) or user32.SetClassLongW
    setter.restype = ctypes.c_void_p
    setter.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    for index, size in ((GCLP_HICON, 32), (GCLP_HICONSM, 16)):
        if handles.get(size):
            setter(ctypes.c_void_p(hwnd), index, ctypes.c_void_p(handles[size]))
