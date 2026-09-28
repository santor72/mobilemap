"""Extract plate/glyph masks from GIS icon PNGs and compose colored PNGs."""

from __future__ import annotations

import hashlib
import io
from collections import Counter
from dataclasses import dataclass

from PIL import Image

# Near-white plate threshold (matches sync analysis of GIS icons).
_LIGHT_MIN = 220


@dataclass(frozen=True)
class MaskExtract:
    """RGBA mask: R=glyph coverage, G=plate coverage, B=0, A=source alpha."""

    mask_png: bytes
    glyph_color: str  # #rrggbb
    shape_hash: str
    width: int
    height: int


def _is_light(r: int, g: int, b: int) -> bool:
    return r >= _LIGHT_MIN and g >= _LIGHT_MIN and b >= _LIGHT_MIN


def _hex_color(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def parse_hex_color(value: str) -> tuple[int, int, int]:
    raw = value.strip().lstrip("#")
    if len(raw) != 6:
        raise ValueError(f"invalid hex color: {value!r}")
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def extract_mask_and_color(png_bytes: bytes) -> MaskExtract:
    im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    width, height = im.size
    pixels = list(im.getdata())

    glyph_rgb: Counter[tuple[int, int, int]] = Counter()
    out: list[tuple[int, int, int, int]] = []

    for r, g, b, a in pixels:
        if a <= 0:
            out.append((0, 0, 0, 0))
            continue
        if _is_light(r, g, b):
            out.append((0, a, 0, a))
        else:
            out.append((a, 0, 0, a))
            if a >= 200:
                glyph_rgb[(r, g, b)] += 1

    if glyph_rgb:
        glyph_color = _hex_color(glyph_rgb.most_common(1)[0][0])
    else:
        glyph_color = "#000000"

    mask_im = Image.new("RGBA", (width, height))
    mask_im.putdata(out)
    buf = io.BytesIO()
    mask_im.save(buf, format="PNG")
    mask_png = buf.getvalue()

    # Hash binary glyph|plate bits (ignore coverage strength / color).
    bits = bytearray()
    for gr, pl, _, a in out:
        flags = 0
        if a > 0 and gr > 0:
            flags |= 1
        if a > 0 and pl > 0:
            flags |= 2
        bits.append(flags)
    shape_hash = hashlib.sha256(bits).hexdigest()

    return MaskExtract(
        mask_png=mask_png,
        glyph_color=glyph_color,
        shape_hash=shape_hash,
        width=width,
        height=height,
    )


def compose_png(mask_png: bytes, glyph_color: str) -> bytes:
    """Rebuild icon: white plate + glyph_color glyph, alpha from mask."""
    cr, cg, cb = parse_hex_color(glyph_color)
    mask = Image.open(io.BytesIO(mask_png)).convert("RGBA")
    width, height = mask.size
    out_pixels: list[tuple[int, int, int, int]] = []

    for gr, pl, _, a in mask.getdata():
        if a <= 0 or (gr <= 0 and pl <= 0):
            out_pixels.append((0, 0, 0, 0))
            continue
        total = gr + pl
        # Weighted blend of glyph color and white plate.
        r = (cr * gr + 255 * pl) // total
        g = (cg * gr + 255 * pl) // total
        b = (cb * gr + 255 * pl) // total
        out_pixels.append((r, g, b, a))

    out = Image.new("RGBA", (width, height))
    out.putdata(out_pixels)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()
