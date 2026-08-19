"""A grid of colour emoji to click, instead of a box to type a glyph into.

Typing one meant either knowing the character or pasting it, and the entry box
showed Tk's monochrome outline rather than what the panel would actually draw
-- so you could not tell whether the glyph you picked existed until you saw the
panel. Here every tile is the real Pillow rasterisation, so what you click is
exactly what appears.

Anything the installed font cannot draw is dropped from the grid rather than
shown as a box, which is why the list below can be generous: a Windows 10
machine simply gets a slightly shorter palette than a Windows 11 one.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# Grouped the way somebody looking for a face would look for one, not by
# Unicode block.
GROUPS: list[tuple[str, list[str]]] = [
    ("Happy", [
        "\U0001F600", "\U0001F603", "\U0001F604", "\U0001F601", "\U0001F606",
        "\U0001F60A", "\U0001F642", "\U0001F643", "\U0001F609", "\U0001F60C",
        "\U0001F60D", "\U0001F929", "\U0001F618", "\U0001F61B", "\U0001F92A",
        "\U0001F60B",
    ]),
    ("Cool and calm", [
        "\U0001F60E", "\U0001F913", "\U0001F9D0", "\U0001F914", "\U0001F610",
        "\U0001F611", "\U0001F636", "\U0001F60F", "\U0001F634", "\U0001F62A",
        "\U0001F971", "\U0001F978", "\U0001F975", "\U0001F976", "\U0001F927",
        "\U0001F92B",
    ]),
    ("Working", [
        "\U0001F624", "\U0001F620", "\U0001F621", "\U0001F92C", "\U0001F633",
        "\U0001F631", "\U0001F630", "\U0001F628", "\U0001F627", "\U0001F626",
        "\U0001F62B", "\U0001F613", "\U0001F616", "\U0001F623", "\U0001F629",
        "\U0001F62D",
    ]),
    ("Trouble", [
        "\U0001F92F", "\U0001F974", "\U0001F635", "\U0001F912", "\U0001F915",
        "\U0001F922", "\U0001F92E", "\U0001F607", "\U0001F608", "\U0001F480",
        "\U0001F47B", "\U0001F47D", "\U0001F916", "\U0001F921", "\U0001F979",
        "\U0001FAE0",
    ]),
    ("Creatures", [
        "\U0001F431", "\U0001F436", "\U0001F98A", "\U0001F43B", "\U0001F43C",
        "\U0001F42F", "\U0001F981", "\U0001F438", "\U0001F419", "\U0001F984",
        "\U0001F995", "\U0001F996", "\U0001F41D", "\U0001F98B", "\U0001F427",
        "\U0001F989",
    ]),
    ("Things", [
        "\U0001F525", "❄", "⚡", "\U0001F4A7", "☀", "\U0001F319",
        "☁", "\U0001F327", "\U0001F328", "⛈", "\U0001F32A",
        "\U0001F4A5", "\U0001F4AA", "\U0001F9CA", "\U0001F321", "\U0001F50B",
    ]),
]

TILE = 30          # pixels per glyph in the grid
COLUMNS = 8


class EmojiPicker(tk.Toplevel):
    """Modal-ish grid. Calls `on_pick` with the chosen character."""

    def __init__(self, parent: tk.Misc, cache, current: str, on_pick) -> None:
        super().__init__(parent)
        self.title("Choose an emoji")
        self.resizable(False, False)
        self.transient(parent)
        self.attributes("-topmost", True)
        self.cache = cache
        self.on_pick = on_pick
        self._photos: list = []          # Tk holds only weak references

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        ttk.Label(body, foreground="#666666", wraplength=430, justify="left",
                  text="Click one to use it. Anything your emoji font cannot "
                       "draw is left out of the grid.").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        row = 1
        for title, chars in GROUPS:
            usable = [ch for ch in chars if cache.usable(ch)]
            if not usable:
                continue
            ttk.Label(body, text=title).grid(row=row, column=0, columnspan=2,
                                             sticky="w", pady=(6, 2))
            row += 1
            grid = ttk.Frame(body)
            grid.grid(row=row, column=0, columnspan=2, sticky="w")
            row += 1
            for index, char in enumerate(usable):
                photo = cache.photo(char, TILE, self)
                if photo is None:
                    continue
                self._photos.append(photo)
                button = ttk.Button(grid, image=photo, width=3,
                                    command=lambda ch=char: self._choose(ch))
                button.grid(row=index // COLUMNS, column=index % COLUMNS,
                            padx=1, pady=1)

        ttk.Separator(body, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(10, 8))
        row += 1

        ttk.Label(body, text="Or paste any glyph").grid(row=row, column=0,
                                                        sticky="w")
        self.typed = tk.StringVar(value=current)
        entry = ttk.Entry(body, textvariable=self.typed, width=6,
                          justify="center")
        entry.grid(row=row, column=1, sticky="w", padx=(8, 0))
        row += 1

        buttons = ttk.Frame(body)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Use typed",
                   command=lambda: self._choose(self.typed.get().strip())).pack(
            side="left", padx=(0, 8))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left")

        self.bind("<Escape>", lambda _e: self.destroy())
        self.update_idletasks()
        self._centre_on(parent)
        entry.focus_set()

    def _centre_on(self, parent: tk.Misc) -> None:
        try:
            x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
            y = parent.winfo_rooty() + 60
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    def _choose(self, char: str) -> None:
        if char:
            self.on_pick(char)
        self.destroy()
