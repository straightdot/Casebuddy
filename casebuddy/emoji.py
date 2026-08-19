"""Colour emoji, and your own images, on a Tk canvas.

Tk on Windows draws text through GDI, which has no COLR/CPAL or CBDT support,
so a glyph put on the canvas as text arrives as a flat black outline -- checked
on the Tk 9.0 build this app runs on. Pillow reads those tables directly, and
Segoe UI Emoji carries COLR layers rather than only bitmap strikes, so a glyph
rasterised through Pillow is sharp at any size rather than an upscaled 109 px
sprite. Rendering one costs about 4 ms, which is why they are cached.

Images go through the same cache. They are keyed on the file's modification
time as well as its path, so replacing a picture on disk shows up on the panel
without restarting anything.

Everything here degrades rather than fails. No Pillow, no emoji font, a glyph
the installed font does not have (Windows 10 has no melting face, for one), a
path that no longer exists, a file that is not an image -- every one of them
ends with None coming back, and the caller falls through to the next option.
"""

from __future__ import annotations

import os

try:  # Pillow is required for the emoji character, not for the app
    from PIL import Image, ImageDraw, ImageFont, ImageTk
    HAVE_PIL = True
except ImportError:  # pragma: no cover - depends on the install
    HAVE_PIL = False

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\seguiemj.ttf",          # Segoe UI Emoji, Windows
    r"C:\Windows\Fonts\SEGUIEMJ.TTF",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
)

# A tofu box is a rectangle in one or two colours. A real emoji has many more,
# and this is the only reliable way to tell: the font reports a glyph either
# way, it just draws a box.
MIN_COLOURS = 6

# Frames kept for one animated face. See image_frames.
MAX_FRAMES = 90


def font_path() -> str | None:
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


class EmojiCache:
    """Rasterised glyphs, keyed by (character, pixel size).

    Holds the PhotoImage references itself: Tk keeps only a weak reference to
    image data, so a PhotoImage nobody stores is collected and the canvas shows
    an empty box.
    """

    def __init__(self) -> None:
        self.path = font_path() if HAVE_PIL else None
        self._photos: dict = {}
        self._missing: set[str] = set()
        self._fonts: dict[int, object] = {}
        self._bad_files: set[str] = set()

    @property
    def available(self) -> bool:
        return bool(self.path)

    def _font(self, size: int):
        if size not in self._fonts:
            self._fonts[size] = ImageFont.truetype(self.path, size)
        return self._fonts[size]

    def image(self, char: str, size: int):
        """A square RGBA image of `char`, or None if it cannot be drawn."""
        if not self.available or not char or char in self._missing:
            return None
        size = max(16, min(1024, int(size)))
        try:
            font = self._font(size)
            pad = size // 3
            canvas = Image.new("RGBA", (size + pad * 2, size + pad * 2), (0, 0, 0, 0))
            ImageDraw.Draw(canvas).text((pad, pad // 2), char, font=font,
                                        embedded_color=True)
            box = canvas.getbbox()
            if box is None:
                self._missing.add(char)
                return None
            glyph = canvas.crop(box)
            rgb = glyph.convert("RGB")
            colours = {rgb.getpixel((x, y))
                       for x in range(0, rgb.width, max(1, rgb.width // 24))
                       for y in range(0, rgb.height, max(1, rgb.height // 24))}
            if len(colours) < MIN_COLOURS:
                self._missing.add(char)
                return None
            # Square and centred, so the caller can place it by its middle and
            # every mood lands in the same spot whatever the glyph's aspect.
            side = max(glyph.width, glyph.height)
            square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            square.paste(glyph, ((side - glyph.width) // 2,
                                 (side - glyph.height) // 2))
            return square
        except Exception:
            self._missing.add(char)
            return None

    @staticmethod
    def _interp(master) -> int:
        """Which Tk interpreter a cached image belongs to.

        A PhotoImage is owned by the interpreter that created it and cannot be
        drawn on a canvas belonging to another one -- it fails with
        `image "pyimageN" does not exist`. The app only ever has one root, but
        keying on it costs nothing and turns a confusing runtime error into a
        second, correct cache entry.
        """
        try:
            return id(master.tk) if master is not None else 0
        except Exception:
            return 0

    def photo(self, char: str, size: int, master=None):
        """A Tk PhotoImage of `char` at `size` pixels, or None."""
        key = ("glyph", char, int(size), self._interp(master))
        if key in self._photos:
            return self._photos[key]
        image = self.image(char, size)
        if image is None:
            return None
        try:
            photo = ImageTk.PhotoImage(image, master=master)
        except Exception:
            return None
        self._photos[key] = photo
        return photo

    def usable(self, char: str) -> bool:
        """Whether this glyph draws as a real emoji. Renders once to find out."""
        return self.image(char, 64) is not None


    # --- images ---------------------------------------------------------

    def image_file(self, path: str, size: int):
        """A square RGBA image from a file, letterboxed, or None.

        Fitted inside the square rather than cropped to it: a picture the user
        chose should arrive whole, not with its edges cut off to suit a layout
        they cannot see.
        """
        if not HAVE_PIL or not path or path in self._bad_files:
            return None
        try:
            with Image.open(path) as opened:
                picture = opened.convert("RGBA")
            picture.thumbnail((size, size), Image.LANCZOS)
            square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            square.paste(picture, ((size - picture.width) // 2,
                                   (size - picture.height) // 2))
            return square
        except Exception:
            self._bad_files.add(path)
            return None

    def image_photo(self, path: str, size: int, master=None):
        """A Tk PhotoImage from a file, or None. Reloads when the file changes."""
        if not path:
            return None
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            return None
        key = ("file", path, int(stamp), int(size), self._interp(master))
        if key in self._photos:
            return self._photos[key]
        picture = self.image_file(path, int(size))
        if picture is None:
            return None
        try:
            photo = ImageTk.PhotoImage(picture, master=master)
        except Exception:
            # Same reasoning as image_frames: a missing Tk root is our problem,
            # not the file's, so it does not get blacklisted for it.
            return None
        self._photos[key] = photo
        return photo

    def image_frames(self, path: str, size: int, master=None):
        """Every frame of an animated file as [(PhotoImage, ms)], or None.

        None means "not animated, use image_photo" -- a still PNG and a broken
        path both land here, and the caller treats them the same way.

        Capped at MAX_FRAMES because the frames are decoded and kept: at a
        262 px face each one is about 275 KB, so a long GIF could otherwise
        quietly cost more memory than the whole rest of the app.
        """
        if not path:
            return None
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            return None
        key = ("frames", path, int(stamp), int(size), self._interp(master))
        if key in self._photos:
            return self._photos[key]
        if not HAVE_PIL or path in self._bad_files:
            return None

        size = max(16, min(1024, int(size)))
        # DECODE FIRST, convert second. Building a PhotoImage needs a live Tk
        # root, and failing that is not the file's fault -- blacklisting the
        # path there meant a perfectly good GIF stayed rejected for the rest of
        # the run once it had been asked for a moment too early.
        try:
            decoded = []
            with Image.open(path) as opened:
                if int(getattr(opened, "n_frames", 1)) <= 1:
                    self._photos[key] = None      # a still image; remember it
                    return None
                for index in range(min(int(opened.n_frames), MAX_FRAMES)):
                    opened.seek(index)
                    frame = opened.convert("RGBA")
                    frame.thumbnail((size, size), Image.LANCZOS)
                    square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                    square.paste(frame, ((size - frame.width) // 2,
                                         (size - frame.height) // 2))
                    # 20 ms floor: some encoders write 0 meaning "as fast as
                    # possible", which would divide by zero below.
                    delay = max(20, int(opened.info.get("duration", 100) or 100))
                    decoded.append((square, delay))
        except Exception:
            self._bad_files.add(path)
            return None
        if not decoded:
            return None
        try:
            frames = [(ImageTk.PhotoImage(image, master=master), delay)
                      for image, delay in decoded]
        except Exception:
            return None                   # no Tk yet; ask again later
        self._photos[key] = frames
        return frames

    def forget_file(self, path: str) -> None:
        """Give a path another chance -- after the user replaces a bad file."""
        self._bad_files.discard(path)


_SHARED: EmojiCache | None = None


def shared() -> EmojiCache:
    """One cache for the process. Rasterised glyphs are worth reusing."""
    global _SHARED
    if _SHARED is None:
        _SHARED = EmojiCache()
    return _SHARED
