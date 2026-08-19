"""Theme packs: one file that IS your setup.

A pack is a single JSON file bundling everything that decides what the panel
looks like -- the full layout (mode, every tile, the character, the scene),
the palette, the sky overrides, the visual weather options, and the custom
face images, embedded as base64 so the one file really is the whole look.

What a pack deliberately does NOT carry: your location, the weather provider,
API keys, monitor selection, poll rates. Those are facts about YOUR machine
and network, not about the look -- and a pack is exactly the file people hand
to somebody else.

Import never trusts the file. Unknown top-level keys are dropped, asset names
are flattened to basenames so a hostile pack cannot write outside the faces
folder, oversized assets are refused, and anything structurally wrong raises
ValueError with a sentence the settings window can show as-is.
"""

from __future__ import annotations

import base64
import copy
import json
import os

from . import config

CURRENT = 1

# The weather keys that are part of the look. Everything else in cfg["weather"]
# stays untouched on import, and is never written on export.
WX_LOOK_KEYS = ("effects", "show_line", "day_brightness", "mood_tint",
                "line_format")

ASSET_CAP = 4 * 1024 * 1024      # per image, decoded
FACES_DIR = "faces"


def export_pack(cfg: dict, path: str, name: str = "") -> str:
    """Write the current look to `path`. Returns the path written."""
    layout = copy.deepcopy(cfg.get("layout", {}))
    wx = cfg.get("weather", {}) or {}
    pack = {
        "casebuddy_pack": CURRENT,
        "name": name or os.path.splitext(os.path.basename(path))[0],
        "layout": layout,
        "theme": copy.deepcopy(cfg.get("theme", {})),
        "skies": copy.deepcopy(wx.get("skies", {}) or {}),
        "weather_look": {key: wx[key] for key in WX_LOOK_KEYS if key in wx},
        "assets": {},
    }

    # Embed the face images and repoint the layout at the embedded names, so
    # the pack works on a machine that has never seen the originals.
    images = (layout.get("buddy", {}) or {}).get("images", {}) or {}
    base = str(cfg.get("_config_dir", "") or "")
    kept: dict = {}
    for mood, ref in images.items():
        from .buddy import resolve_image

        full = resolve_image(str(ref), base)
        try:
            if full and os.path.isfile(full) \
                    and os.path.getsize(full) <= ASSET_CAP:
                with open(full, "rb") as fh:
                    raw = fh.read()
                filename = os.path.basename(full)
                pack["assets"][filename] = base64.b64encode(raw).decode("ascii")
                kept[mood] = f"{FACES_DIR}/{filename}"
        except OSError:
            pass                    # an unreadable image just stays out
    if "buddy" in layout:
        layout["buddy"]["images"] = kept

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(pack, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def import_pack(cfg: dict, path: str) -> dict:
    """A new config with the pack's look applied. Raises ValueError on junk."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            pack = json.load(fh)
    except OSError as exc:
        raise ValueError(f"cannot read the file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(pack, dict) or "casebuddy_pack" not in pack:
        raise ValueError("this is not a CaseBuddy theme pack")
    try:
        version = int(pack["casebuddy_pack"])
    except (TypeError, ValueError):
        raise ValueError("this is not a CaseBuddy theme pack") from None
    if version > CURRENT:
        raise ValueError("this pack is from a newer CaseBuddy; update first")

    layout = pack.get("layout")
    theme = pack.get("theme")
    if not isinstance(layout, dict) or not isinstance(theme, dict):
        raise ValueError("the pack is missing its layout or theme")

    out = copy.deepcopy(cfg)
    # Merged over the defaults, exactly as config.json itself is, so a pack
    # from an older version that lacks a newer setting still gets its default
    # rather than a KeyError at draw time.
    out["layout"] = config._merge(config.DEFAULTS["layout"], layout)
    out["theme"] = config._merge(config.DEFAULTS["theme"], theme)
    if isinstance(pack.get("skies"), dict):
        out.setdefault("weather", {})["skies"] = copy.deepcopy(pack["skies"])
    look = pack.get("weather_look")
    if isinstance(look, dict):
        for key in WX_LOOK_KEYS:
            if key in look:
                out.setdefault("weather", {})[key] = copy.deepcopy(look[key])

    _unpack_assets(out, pack)
    return out


def _unpack_assets(out: dict, pack: dict) -> None:
    """Write embedded images into faces/ and repoint the layout at them."""
    assets = pack.get("assets")
    images = (out.get("layout", {}).get("buddy", {}) or {}).get("images", {})
    if not isinstance(assets, dict) or not isinstance(images, dict):
        return
    base = str(out.get("_config_dir", "") or ".")
    faces = os.path.join(base, FACES_DIR)
    written: set[str] = set()
    for filename, encoded in assets.items():
        # Basename only: a pack that says "..\\..\\evil.exe" writes nothing.
        safe = os.path.basename(str(filename))
        if not safe or safe.startswith("."):
            continue
        try:
            raw = base64.b64decode(str(encoded), validate=True)
        except (ValueError, TypeError):
            continue
        if not raw or len(raw) > ASSET_CAP:
            continue
        try:
            os.makedirs(faces, exist_ok=True)
            with open(os.path.join(faces, safe), "wb") as fh:
                fh.write(raw)
            written.add(safe)
        except OSError:
            continue
    # Any image reference whose asset did not arrive is dropped rather than
    # left dangling; that mood falls back to its emoji, which is the designed
    # behaviour for a missing picture.
    kept = {}
    for mood, ref in images.items():
        name = os.path.basename(str(ref))
        if name in written:
            kept[mood] = f"{FACES_DIR}/{name}"
    out["layout"]["buddy"]["images"] = kept
