"""Click-to-edit layout designer.

Shows a live, scaled-down copy of the real dashboard -- the actual Dashboard
class on a small canvas, not a mock-up, so what you click is what you get --
and lets any tile be repointed at any of the ~190 metrics the machine exposes.

Hit regions come from dashboard.slot_regions(), which is defined next to the
drawing code, so the clickable areas cannot drift away from what is painted.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from . import catalog, theme
from .buddy import MIN_TILE_H, MIN_TILE_W
from .presets import ALERT_FOR, ALERT_SETS, blank_slot, slot_limit
from .dashboard import make_scene, slot_regions

PREVIEW_W, PREVIEW_H = 768, 432  # 0.4x the 1920x1080 design space

# The screen a layout draws. Shown in words; stored as the short key.

# Grab radius of the bottom-right resize handle, in preview pixels.
HANDLE_PX = 18
# Everything snaps to this in design space. Four is fine enough to place a tile
# by eye and coarse enough that two tiles nudged to the same edge line up.
SNAP = 4.0


class LayoutEditor(ttk.Frame):
    def __init__(self, parent: tk.Misc, cfg: dict, collector,
                 on_change: Callable[[], None],
                 presets_api: dict | None = None) -> None:
        self.presets_api = presets_api or {}
        super().__init__(parent, padding=10)
        self.cfg = cfg
        self.collector = collector
        self.on_change = on_change
        self.selected: tuple[str, int] | None = None  # (kind, index)
        self._drag: dict | None = None
        self._after = None
        self._rebuild_job = None
        self._preview_ms = 1000
        self._building = False

        self.catalog: list[catalog.MetricDef] = []
        self.by_label: dict[str, str] = {}
        self.by_ref: dict[str, str] = {}
        self._refresh_catalog()

        top = ttk.Frame(self)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(top, text="Preset").pack(side="left", padx=(0, 8))
        self.v_preset = tk.StringVar()
        self._preset_keys: list = []
        self._preset_box = ttk.Combobox(top, textvariable=self.v_preset,
                                        state="readonly", width=26)
        self._preset_box.pack(side="left")
        self._preset_box.bind("<<ComboboxSelected>>",
                              lambda _e: self._preset_chosen())
        ttk.Button(top, text="Save as preset", width=14,
                   command=lambda: self.presets_api.get("save", lambda: None)()
                   ).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Update preset", width=13,
                   command=self._update_preset).pack(side="left", padx=(4, 0))
        self.refresh_presets()
        ttk.Label(top, foreground="#666666",
                  text="Click a tile to edit it. Drag to move, drag the "
                       "corner to resize."
                  ).pack(side="left", padx=(12, 0))
        self.b_reset = ttk.Button(top, text="Reset position", width=14,
                                  command=self._reset_rect, state="disabled")
        self.b_reset.pack(side="right", padx=(6, 0))
        self.b_remove = ttk.Button(top, text="Remove tile", width=12,
                                   command=self._remove_tile, state="disabled")
        self.b_remove.pack(side="right")
        self.b_add = ttk.Button(top, text="Add tile", width=10,
                                command=self._add_tile, state="disabled")
        self.b_add.pack(side="right", padx=(0, 6))

        self.canvas = tk.Canvas(self, width=PREVIEW_W, height=PREVIEW_H,
                                highlightthickness=1, highlightbackground="#888888",
                                bd=0, bg=theme.BG)
        self.canvas.grid(row=1, column=0, sticky="nw")
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)

        self.inspector = ttk.LabelFrame(self, text="No tile selected", padding=10)
        self.inspector.grid(row=1, column=1, sticky="nw", padx=(12, 0))
        self._build_inspector()

        self._build_preview()
        self._tick()

    def destroy(self) -> None:  # stop the preview loop with the tab
        for attr in ("_after", "_rebuild_job"):
            handle = getattr(self, attr, None)
            if handle is not None:
                try:
                    self.after_cancel(handle)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        super().destroy()

    # --- catalogue --------------------------------------------------------

    def _refresh_catalog(self) -> None:
        rows = list(getattr(self.collector, "rows", []) or [])
        self.catalog = catalog.build(rows)
        self.by_label, self.by_ref = {}, {}
        for metric in self.catalog:
            # Blank is not a computed metric anyone browses for by group; it
            # is the "clear this" option, so it keeps its own bare name.
            label = (metric.label if metric.ref == "calc:blank"
                     else f"{metric.group} - {metric.label}")
            # Two sensors can share a name (a card with two fans); keep both
            # reachable by disambiguating with the identifier.
            if label in self.by_label:
                label = f"{label}  ({metric.ref.split('/')[-1]})"
            self.by_label[label] = metric.ref
            self.by_ref[metric.ref] = label

    def _label_for(self, ref: str) -> str:
        if ref in self.by_ref:
            return self.by_ref[ref]
        # A ref pointing at a sensor that is not currently present -- LHM down,
        # or hardware removed. Say so rather than silently snapping to another.
        return f"(unavailable) {ref}"

    def _choices(self) -> list[str]:
        # Blank first, always. Emptying a slot is something people go looking
        # for, and it is not findable filed alphabetically under Computed.
        blank = self.by_ref.get("calc:blank")
        rest = sorted(label for label in self.by_label if label != blank)
        return ([blank] if blank else []) + rest

    # --- preview ----------------------------------------------------------

    def _build_preview(self) -> None:
        self.canvas.delete("all")
        self.geo = theme.Geometry(PREVIEW_W, PREVIEW_H)
        self.dash = make_scene(self.canvas, self.geo, self.cfg)
        self.dash.build()
        # An animated scene wants a faster preview than a static one, but the
        # settings window is not the panel: cap it so editing never competes
        # with the dashboard for the same core.
        wanted = getattr(self.dash, "frame_ms", 0)
        self._preview_ms = max(66, wanted) if wanted else 1000
        self._draw_highlight()

    def refresh_presets(self) -> None:
        """Rebuild the picker: built-ins, the user's own (starred), and the
        combobox text reflecting whichever one the layout still matches."""
        entries = self.presets_api.get("entries", lambda: [])()
        self._preset_keys = [key for key, _t, _c in entries]
        self._preset_box.configure(values=[
            (f"* {title}" if custom else title)
            for _k, title, custom in entries])
        current = self.presets_api.get("matches", lambda: None)()
        if current in self._preset_keys:
            index = self._preset_keys.index(current)
            self.v_preset.set(self._preset_box["values"][index])
        else:
            self.v_preset.set("(custom layout)")

    def _preset_chosen(self) -> None:
        index = self._preset_box.current()
        if 0 <= index < len(self._preset_keys):
            self.presets_api.get("apply", lambda _k: None)(
                self._preset_keys[index])

    def _update_preset(self) -> None:
        index = self._preset_box.current()
        key = (self._preset_keys[index]
               if 0 <= index < len(self._preset_keys) else None)
        self.presets_api.get("update", lambda _k: None)(key)

    def reload(self) -> None:
        """Re-read a layout replaced from outside -- by the Presets tab."""
        self.selected = None
        self.refresh_presets()
        self.inspector.configure(text="No tile selected")
        self._set_inspector_state("disabled")
        self._refresh_catalog()
        self._build_preview()
        self._sync_buttons()

    def _draw_highlight(self, rect: list | None = None) -> None:
        """Outline the selection, plus the corner you can grab to resize it.

        `rect` overrides the stored geometry so a drag can follow the pointer
        without rebuilding the whole scene on every motion event.
        """
        self.canvas.delete("sel")
        if self.selected is None:
            return
        kind, index = self.selected
        if rect is None:
            rect = self._rect_of(kind, index)
        if rect is None:
            return
        g = self.geo
        x, y, w, h = rect
        self.canvas.create_rectangle(
            g.x(x), g.y(y), g.x(x + w), g.y(y + h),
            outline="#ffffff", width=2, dash=(4, 3), tags="sel")
        if kind != "header":
            hx, hy = g.x(x + w), g.y(y + h)
            self.canvas.create_rectangle(
                hx - 9, hy - 9, hx, hy, outline="#ffffff", fill="#ffffff",
                width=1, tags="sel")

    def _rect_of(self, kind: str, index: int) -> list | None:
        """Current geometry of a slot, taken from the drawing code itself."""
        for _key, rkind, rindex, x0, y0, x1, y1 in slot_regions(self.cfg):
            if (rkind, rindex) == (kind, index):
                return [x0, y0, x1 - x0, y1 - y0]
        return None

    def _hit(self, event) -> tuple[str, int] | None:
        g = self.geo
        dx = event.x / g.kx if g.kx else 0
        dy = (event.y - g.y0) / g.ky if g.ky else 0
        # Last match wins: later slots are drawn on top, so an overlap should
        # select the one you can actually see.
        found = None
        for _key, kind, index, x0, y0, x1, y1 in slot_regions(self.cfg):
            if x0 <= dx <= x1 and y0 <= dy <= y1:
                found = (kind, index)
        return found

    def _tick(self) -> None:
        try:
            if self.collector is not None:
                self.dash.update(self.collector.latest())
                self._draw_highlight()
        except Exception:
            pass  # a preview must never take the settings window down
        self._after = self.after(self._preview_ms, self._tick)

    def _on_press(self, event) -> None:
        hit = self._hit(event)
        if hit is None:
            return
        self.selected = hit
        self._load_inspector()
        self._draw_highlight()
        kind, index = hit
        rect = self._rect_of(kind, index)
        if rect is None or kind == "header":
            # The header is two pieces of text pinned to the top rule; there is
            # nothing meaningful to move it to.
            self._drag = None
            return
        self._drag = {
            "rect": list(rect),
            "start": (event.x, event.y),
            "mode": "resize" if self._on_handle(event, rect) else "move",
        }

    def _on_handle(self, event, rect) -> bool:
        g = self.geo
        hx, hy = g.x(rect[0] + rect[2]), g.y(rect[1] + rect[3])
        return abs(event.x - hx) <= HANDLE_PX and abs(event.y - hy) <= HANDLE_PX

    def _on_hover(self, event) -> None:
        """Show the resize cursor over the handle, so it is discoverable."""
        cursor = ""
        if self.selected is not None and self.selected[0] != "header":
            rect = self._rect_of(*self.selected)
            if rect is not None and self._on_handle(event, rect):
                cursor = "bottom_right_corner"
        try:
            self.canvas.configure(cursor=cursor)
        except tk.TclError:
            pass

    def _on_drag(self, event) -> None:
        if self._drag is None or self.selected is None:
            return
        g = self.geo
        dx = (event.x - self._drag["start"][0]) / (g.kx or 1.0)
        dy = (event.y - self._drag["start"][1]) / (g.ky or 1.0)
        x, y, w, h = self._drag["rect"]
        if self._drag["mode"] == "move":
            x, y = x + dx, y + dy
        else:
            w, h = w + dx, h + dy
        rect = _clamp_rect(x, y, w, h, self._drag["mode"])
        slot = self._slot()
        if slot is None:
            return
        slot["rect"] = rect
        # Only the outline moves while the pointer is down; the scene is rebuilt
        # once on release. Rebuilding per motion event drops frames badly.
        self._draw_highlight(rect)

    def _on_release(self, _event) -> None:
        if self._drag is None:
            return
        moved = self._drag["mode"]
        self._drag = None
        self._build_preview()
        self._sync_buttons()
        self._say_change(moved)

    def _say_change(self, mode: str) -> None:
        self.on_change()

    def _reset_rect(self) -> None:
        """Give a tile its grid position back."""
        slot = self._slot()
        if slot is None or "rect" not in slot:
            return
        slot.pop("rect", None)
        self._build_preview()
        self._draw_highlight()
        self._sync_buttons()
        self.on_change()

    # --- inspector --------------------------------------------------------

    def _build_inspector(self) -> None:
        self.v_label = tk.StringVar()
        self.v_metric = tk.StringVar()
        self.v_detail = tk.StringVar()
        self.v_top = tk.StringVar()
        self.v_alert = tk.StringVar()
        self.v_percent = tk.BooleanVar()
        self.v_fontsize = tk.StringVar()
        self.v_bold = tk.BooleanVar()
        self.v_italic = tk.BooleanVar()
        self.v_filter = tk.StringVar()
        self.v_min = tk.StringVar()
        self.v_max = tk.StringVar()

        rows = [
            ("Search", self.v_filter, "search",
             "Narrows the three lists below. Blank shows everything"),
            ("Title", self.v_label, "entry", "Text shown above the tile"),
            ("Value", self.v_metric, "combo",
             "The number or gauge reading (header: left piece)"),
            ("Bottom right", self.v_detail, "combo",
             "Secondary line (header: right piece)"),
            ("Top right", self.v_top, "combo",
             "Shares the title line, right-aligned"),
            ("Alerts", self.v_alert, "combo",
             "Which warn/critical pair colours this tile"),
            ("Gauge min", self.v_min, "entry", "Where the gauge starts"),
            ("Gauge max", self.v_max, "entry", "Where the gauge reads full"),
            ("Show % of scale", self.v_percent, "check",
             "Bar rows: print how full the bar is, next to the reading"),
            ("Font size", self.v_fontsize, "entry",
             "This tile only. 1.0 is the screen size; 0.4 to 2.5"),
            ("Bold", self.v_bold, "check", "Force bold on this tile"),
            ("Italic", self.v_italic, "check", "Force italic on this tile"),
        ]
        self.widgets: dict[str, tk.Widget] = {}
        for row, (label, var, kind, hint) in enumerate(rows):
            ttk.Label(self.inspector, text=label).grid(row=row, column=0, sticky="w",
                                                      pady=4, padx=(0, 8))
            if kind == "combo":
                widget = ttk.Combobox(self.inspector, textvariable=var, width=42,
                                      state="readonly", values=self._choices())
            elif kind == "check":
                widget = ttk.Checkbutton(self.inspector, variable=var)
            else:
                widget = ttk.Entry(self.inspector, textvariable=var, width=44)
            widget.grid(row=row, column=1, sticky="w")
            self.widgets[label] = widget
            note = ttk.Label(self.inspector, text=hint, foreground="#888888")
            note.grid(row=row, column=2, sticky="w", padx=(10, 0))
            if kind == "search":
                self.l_found = note

        self.widgets["Alerts"].configure(values=list(ALERT_SETS))
        for var in (self.v_label, self.v_metric, self.v_detail, self.v_top,
                    self.v_alert, self.v_min, self.v_max, self.v_percent,
                    self.v_fontsize, self.v_bold, self.v_italic):
            var.trace_add("write", lambda *_a: self._apply_inspector())
        # Deliberately NOT in that list. Filtering the pickers is not an edit,
        # and routing it through _apply_inspector would rewrite the slot on
        # every keystroke of a search.
        self.v_filter.trace_add("write", lambda *_a: self._apply_filter())

        self._set_inspector_state("disabled")

    def _set_inspector_state(self, state: str) -> None:
        for name, widget in self.widgets.items():
            try:
                widget.configure(state="readonly" if (state == "normal" and
                                 isinstance(widget, ttk.Combobox)) else state)
            except tk.TclError:
                pass

    def _slot(self) -> dict | None:
        if self.selected is None:
            return None
        kind, index = self.selected
        rows = self.cfg["layout"].get(kind, [])
        return rows[index] if index < len(rows) else None

    def _load_inspector(self) -> None:
        slot = self._slot()
        if slot is None:
            return
        kind, index = self.selected
        self.inspector.configure(text=f"{kind[:-1].title()} {index + 1}")
        self._refresh_catalog()
        self._apply_filter()

        self._building = True  # suppress write-backs while loading fields
        try:
            self.v_label.set(str(slot.get("label", "")))
            self.v_metric.set(self._label_for(str(slot.get("metric", "calc:blank"))))
            self.v_detail.set(self._label_for(str(slot.get("detail", "calc:blank"))))
            self.v_top.set(self._label_for(str(slot.get("top", "calc:blank"))))
            meta = self._meta_for(str(slot.get("metric", "")))
            self.v_alert.set(str(slot.get("thresholds", "") or ALERT_SETS[0]))
            self.v_percent.set(bool(slot.get("percent", False)))
            self.v_fontsize.set(str(slot.get("font_size", 1.0)))
            self.v_bold.set(bool(slot.get("bold", False)))
            self.v_italic.set(bool(slot.get("italic", False)))
            self.v_min.set(str(slot.get("min", meta.lo if meta else 0)))
            self.v_max.set(str(slot.get("max", meta.hi if meta else 100)))
        finally:
            self._building = False

        self._set_inspector_state("normal")
        self._sync_buttons()
        # Bars and stat cards both carry a second figure on their title line;
        # rings and fan rows have nowhere to put one.
        self.widgets["Top right"].configure(
            state="readonly" if kind in ("bars", "stats") else "disabled")
        # Only a row with a bar and no duty sensor of its own needs this.
        self.widgets["Show % of scale"].configure(
            state="normal" if kind == "fans" else "disabled")
        if kind in ("header", "stats"):
            # Neither draws a gauge, so a range would have nothing to fill.
            for name in ("Gauge min", "Gauge max"):
                self.widgets[name].configure(state="disabled")
            if kind == "header":
                self.widgets["Alerts"].configure(state="disabled")

        if kind == "header":
            # Header slots are two pieces of text joined by a separator: no
            # title above them, no gauge, so no range.
            self.widgets["Title"].configure(state="disabled")
            self.inspector.configure(
                text=f"Header {'left' if index == 0 else 'right'}")

    # --- adding and removing tiles ----------------------------------------

    def _sync_buttons(self) -> None:
        """Enable the two buttons only where they would actually do something."""
        if self.selected is None:
            self.b_add.configure(state="disabled")
            self.b_remove.configure(state="disabled")
            self.b_reset.configure(state="disabled")
            return
        kind, _index = self.selected
        slot = self._slot() or {}
        self.b_reset.configure(
            state="normal" if (kind != "header" and "rect" in slot) else "disabled")
        rows = self.cfg["layout"].get(kind, [])
        limit = slot_limit(kind)
        # The header is two fixed pieces of text; there is no third to add and
        # removing one would leave a screen with no clock on it.
        self.b_add.configure(state="normal" if len(rows) < limit else "disabled")
        self.b_remove.configure(
            state="normal" if (kind != "header" and rows) else "disabled")

    def _add_tile(self) -> None:
        if self.selected is None:
            return
        kind, _index = self.selected
        rows = self.cfg["layout"].setdefault(kind, [])
        if len(rows) >= slot_limit(kind):
            return
        rows.append(blank_slot(kind))
        self.selected = (kind, len(rows) - 1)
        self._build_preview()
        self._load_inspector()
        self.on_change()

    def _remove_tile(self) -> None:
        if self.selected is None:
            return
        kind, index = self.selected
        rows = self.cfg["layout"].get(kind, [])
        if kind == "header" or index >= len(rows):
            return
        rows.pop(index)
        self.selected = (kind, min(index, len(rows) - 1)) if rows else None
        self._build_preview()
        if self.selected is None:
            self.inspector.configure(text="No tile selected")
            self._set_inspector_state("disabled")
            self._sync_buttons()
        else:
            self._load_inspector()
        self.on_change()

    def _apply_filter(self) -> None:
        """Narrow the three metric pickers to what was typed in Search.

        Around 215 metrics is far too many to scroll, and the useful ones are
        scattered through it by group. A readonly combobox goes on showing its
        own value even when that value is filtered out of the list, so nothing
        is lost by narrowing hard.
        """
        terms = [_squash(part) for part in self.v_filter.get().split()]
        terms = [t for t in terms if t]
        choices = self._choices()
        # Every term must appear, in any order, against a squashed label. That
        # is what makes "vram" find "V-RAM" and "gpu clock" find "GPU core
        # clock" -- a plain substring search on the raw label finds neither.
        shown = ([c for c in choices
                  if all(t in _squash(c) for t in terms)] if terms else choices)
        for name in ("Value", "Bottom right", "Top right"):
            widget = self.widgets.get(name)
            if widget is not None:
                widget.configure(values=shown)
        if getattr(self, "l_found", None) is not None:
            self.l_found.configure(
                text=(f"{len(shown)} of {len(choices)} metrics" if terms else
                      "Narrows the three lists below. Blank shows everything"))

    def _meta_for(self, ref: str) -> catalog.MetricDef | None:
        for metric in self.catalog:
            if metric.ref == ref:
                return metric
        return None

    def _apply_inspector(self) -> None:
        if self._building:
            return
        slot = self._slot()
        if slot is None:
            return
        kind, _index = self.selected

        slot["label"] = self.v_label.get()
        detail_ref = self.by_label.get(self.v_detail.get())
        if detail_ref:
            slot["detail"] = detail_ref

        metric_ref = self.by_label.get(self.v_metric.get())
        if metric_ref and metric_ref != slot.get("metric"):
            # Repointing a tile has to bring its SCALE along. Keeping the old
            # range and alert set meant a 7001 MHz clock dropped into a slot
            # that used to hold watts was judged against a 650 W gauge and
            # flagged critical -- the number was right and everything around it
            # was wrong.
            slot["metric"] = metric_ref
            meta = self._meta_for(metric_ref)
            if meta is not None:
                slot["min"], slot["max"] = float(meta.lo), float(meta.hi)
            alert = ALERT_FOR.get(metric_ref, "")
            if alert:
                slot["thresholds"] = alert
            else:
                slot.pop("thresholds", None)
            self._building = True
            try:
                self.v_alert.set(alert or ALERT_SETS[0])
                self.v_min.set(str(slot.get("min", 0)))
                self.v_max.set(str(slot.get("max", 100)))
            finally:
                self._building = False
        else:
            chosen = self.v_alert.get()
            if chosen and chosen != ALERT_SETS[0]:
                slot["thresholds"] = chosen
            else:
                slot.pop("thresholds", None)

        if kind == "fans":
            if self.v_percent.get():
                slot["percent"] = True
            else:
                slot.pop("percent", None)

        # Stored only when they actually differ, so a tile left alone keeps
        # inheriting the screen font and a later change to it still reaches.
        try:
            size = round(max(0.4, min(2.5, float(self.v_fontsize.get()))), 2)
            if abs(size - 1.0) < 0.005:
                slot.pop("font_size", None)
            else:
                slot["font_size"] = size
        except (TypeError, ValueError):
            pass                  # mid-typing; leave the previous value alone
        for field, var in (("bold", self.v_bold), ("italic", self.v_italic)):
            if var.get():
                slot[field] = True
            else:
                slot.pop(field, None)
        if kind in ("bars", "stats"):
            ref = self.by_label.get(self.v_top.get())
            if ref:
                slot["top"] = ref
        for field, var in (("min", self.v_min), ("max", self.v_max)):
            try:
                slot[field] = float(var.get())
            except ValueError:
                pass  # mid-typing; leave the previous value alone

        # Titles are baked in at build time, so an edit needs a fresh scene.
        # Debounced, because this fires on every keystroke.
        if self._rebuild_job is not None:
            self.after_cancel(self._rebuild_job)
        self._rebuild_job = self.after(220, self._rebuild)
        self.on_change()

    def _rebuild(self) -> None:
        self._rebuild_job = None
        self._build_preview()


def _clamp_rect(x: float, y: float, w: float, h: float,
                mode: str = "move") -> list[float]:
    """Keep a dragged tile on screen, big enough to read, and on the grid.

    Only the pair being dragged is snapped. Snapping all four meant a plain
    move also nudged the size -- a 190-tall card became 192 just for being
    picked up -- and a resize could shift the corner you were not holding.

    Clamped before snapping so a tile pushed against an edge lands exactly on
    it rather than one step short.
    """
    w = max(MIN_TILE_W, min(float(theme.DESIGN_W), w))
    h = max(MIN_TILE_H, min(float(theme.DESIGN_H), h))
    x = max(0.0, min(theme.DESIGN_W - w, x))
    y = max(0.0, min(theme.DESIGN_H - h, y))

    def snap(value: float) -> float:
        return round(value / SNAP) * SNAP

    if mode == "resize":
        return [x, y, max(MIN_TILE_W, snap(w)), max(MIN_TILE_H, snap(h))]
    return [snap(x), snap(y), w, h]


def _squash(text: str) -> str:
    """Lowercase, with the punctuation that separates words removed.

    Metric labels are written for reading ("V-RAM used / total", "CPU
    temperature"); people search for how they say them ("vram", "cputemp").
    """
    return "".join(ch for ch in text.lower() if ch.isalnum())
