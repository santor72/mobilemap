"""Raster line tile generation (Cairo → WebP) and path helpers."""

from __future__ import annotations

import io
import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import asyncpg
import cairo
import mercantile
from PIL import Image

from app.config import Settings

TileLayer = Literal["network", "poles"]
TILE_SIZE = 256
BUFFER_PX = 32

# Fallbacks when style.lineColor / lineWidth are missing.
COLOR_BACKBONE = (0x1A / 255, 0x5F / 255, 0xB4 / 255)  # #1a5fb4
COLOR_OTHER = (0x33 / 255, 0x88 / 255, 0xFF / 255)  # #3388ff
COLOR_POLES = (0.78, 0.27, 0.0)
# Raster CSS px — keep close to the pre-style bake; do not apply the vector
# backbone ×1.75 boost on top of GIS lineWidth (Cairo already reads heavier).
WIDTH_BACKBONE = 2.5
WIDTH_OTHER = 1.25
WIDTH_POLES = 1.5
OPACITY_BACKBONE = 0.95
OPACITY_OTHER = 0.75
OPACITY_POLES = 0.85

_RGB_RE = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})",
    re.IGNORECASE,
)


def _parse_css_color(value: str) -> tuple[float, float, float] | None:
    text = value.strip()
    if text.startswith("#"):
        hexpart = text[1:]
        if len(hexpart) == 3 and all(c in "0123456789abcdefABCDEF" for c in hexpart):
            hexpart = "".join(ch * 2 for ch in hexpart)
        if len(hexpart) < 6:
            return None
        hexpart = hexpart[:6]
        try:
            channels = (
                int(hexpart[0:2], 16),
                int(hexpart[2:4], 16),
                int(hexpart[4:6], 16),
            )
        except ValueError:
            return None
        return channels[0] / 255, channels[1] / 255, channels[2] / 255
    match = _RGB_RE.match(text)
    if match is None:
        return None
    channels: list[float] = []
    for group in match.groups():
        channel = int(group)
        if channel > 255:
            return None
        channels.append(channel / 255)
    return channels[0], channels[1], channels[2]


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def resolve_line_stroke(
    style: dict[str, Any] | None,
    *,
    backbone: bool,
    poles: bool,
) -> tuple[float, float, float, float, float]:
    """Return r, g, b, opacity, width (CSS px).

    Color/width/opacity come from style when set. Class is for LOD and for
    fallback stroke metrics only — no extra backbone width multiplier on top
    of authored GIS lineWidth (that made raster strokes look oversized).
    """
    props = style if isinstance(style, dict) else {}
    raw_color = props.get("lineColor")
    parsed = (
        _parse_css_color(raw_color)
        if isinstance(raw_color, str) and raw_color.strip()
        else None
    )
    if parsed is None:
        if poles:
            r, g, b = COLOR_POLES
        elif backbone:
            r, g, b = COLOR_BACKBONE
        else:
            r, g, b = COLOR_OTHER
    else:
        r, g, b = parsed

    width_value = _optional_float(props.get("lineWidth"))
    opacity_value = _optional_float(props.get("lineOpacity"))
    if width_value is not None:
        width = width_value
    elif poles:
        width = WIDTH_POLES
    elif backbone:
        width = WIDTH_BACKBONE
    else:
        width = WIDTH_OTHER

    if opacity_value is not None:
        opacity = opacity_value
    elif poles:
        opacity = OPACITY_POLES
    elif backbone:
        opacity = OPACITY_BACKBONE
    else:
        opacity = OPACITY_OTHER
    return r, g, b, min(1.0, max(0.0, opacity)), max(0.0, width)


@dataclass
class LineGeom:
    coords: list[tuple[float, float]]  # EPSG:3857 meters
    backbone: bool
    minx: float
    miny: float
    maxx: float
    maxy: float
    r: float
    g: float
    b: float
    a: float
    width: float


def tile_root(settings: Settings) -> Path:
    return Path(settings.tile_cache_dir)


def layer_version_dir(
    settings: Settings, map_id: str, layer: TileLayer, version: str
) -> Path:
    return tile_root(settings) / map_id / layer / version


def tile_file_path(
    settings: Settings,
    map_id: str,
    layer: TileLayer,
    version: str,
    z: int,
    x: int,
    y: int,
    *,
    scale: int = 1,
) -> Path:
    name = f"{y}@2x.webp" if scale == 2 else f"{y}.webp"
    return layer_version_dir(settings, map_id, layer, version) / str(z) / str(x) / name


def _transparent_webp(size: int) -> bytes:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="WEBP", lossless=True)
    return buf.getvalue()


_EMPTY_1X = _transparent_webp(TILE_SIZE)
_EMPTY_2X = _transparent_webp(TILE_SIZE * 2)


def empty_tile_bytes(*, scale: int = 1) -> bytes:
    return _EMPTY_2X if scale == 2 else _EMPTY_1X


def read_tile_bytes(
    settings: Settings,
    map_id: str,
    layer: TileLayer,
    version: str,
    z: int,
    x: int,
    y: int,
    *,
    scale: int = 1,
) -> bytes | None:
    """Return file bytes if present, else None (caller may lazy-render)."""
    path = tile_file_path(settings, map_id, layer, version, z, x, y, scale=scale)
    if path.is_file():
        return path.read_bytes()
    return None


def _tiles_covering_lines(lines: list[LineGeom], z: int) -> list[mercantile.Tile]:
    needed: set[mercantile.Tile] = set()
    pad = 0.0003
    for ln in lines:
        w, s = mercantile.lnglat(ln.minx, ln.miny)
        e, n = mercantile.lnglat(ln.maxx, ln.maxy)
        west, east = sorted((w, e))
        south, north = sorted((s, n))
        needed.update(
            mercantile.tiles(west - pad, south - pad, east + pad, north + pad, [z])
        )
    return list(needed)


def _bbox_of(coords: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return min(xs), min(ys), max(xs), max(ys)


def _as_style(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _parse_geojson_line(
    geometry: dict[str, Any],
    backbone: bool,
    stroke: tuple[float, float, float, float, float],
) -> list[LineGeom]:
    r, g, b, a, width = stroke
    gtype = geometry.get("type")
    out: list[LineGeom] = []
    if gtype == "LineString":
        raw = geometry.get("coordinates") or []
        coords = [(float(p[0]), float(p[1])) for p in raw if len(p) >= 2]
        if len(coords) >= 2:
            minx, miny, maxx, maxy = _bbox_of(coords)
            out.append(
                LineGeom(coords, backbone, minx, miny, maxx, maxy, r, g, b, a, width)
            )
    elif gtype == "MultiLineString":
        for part in geometry.get("coordinates") or []:
            coords = [(float(p[0]), float(p[1])) for p in part if len(p) >= 2]
            if len(coords) >= 2:
                minx, miny, maxx, maxy = _bbox_of(coords)
                out.append(
                    LineGeom(coords, backbone, minx, miny, maxx, maxy, r, g, b, a, width)
                )
    return out


def _lines_from_rows(rows: list[Any], *, poles: bool) -> list[LineGeom]:
    lines: list[LineGeom] = []
    for row in rows:
        raw = row["geojson"]
        if not raw:
            continue
        geometry = json.loads(raw)
        backbone = bool(row["is_backbone"])
        stroke = resolve_line_stroke(_as_style(row["style"]), backbone=backbone, poles=poles)
        lines.extend(_parse_geojson_line(geometry, backbone, stroke))
    return lines


async def _load_lines(
    pool: asyncpg.Pool,
    settings: Settings,
    map_id: str,
    layer: TileLayer,
    zoom: int,
) -> list[LineGeom]:
    tol_m = settings.simplify_tolerance_meters(zoom)
    class_mode = settings.line_class_mode(zoom) if layer == "network" else "all"
    poles_name = settings.line_tile_poles_layer_name

    params: list[Any] = [map_id]
    if tol_m > 0:
        params.append(tol_m)
        geom_expr = f"ST_Simplify(ST_Transform(f.geom, 3857), ${len(params)}::float8)"
    else:
        geom_expr = "ST_Transform(f.geom, 3857)"

    if layer == "poles":
        params.append(poles_name)
        layer_sql = f"AND l.name = ${len(params)}"
    else:
        params.append(poles_name)
        layer_sql = f"AND l.name IS DISTINCT FROM ${len(params)}"

    class_sql = ""
    if class_mode == "backbone":
        class_sql = """
          AND EXISTS (
                SELECT 1 FROM feature_classes fc
                WHERE fc.feature_id = f.id AND fc.class_name = 'backbone'
              )
        """

    sql = f"""
        SELECT ST_AsGeoJSON({geom_expr})::text AS geojson,
               f.style AS style,
               EXISTS (
                   SELECT 1 FROM feature_classes fc
                   WHERE fc.feature_id = f.id AND fc.class_name = 'backbone'
               ) AS is_backbone
        FROM features f
        JOIN layers l ON l.id = f.layer_id
        WHERE f.map_id = $1::uuid
          AND ST_GeometryType(f.geom) IN ('ST_LineString', 'ST_MultiLineString')
          {layer_sql}
          {class_sql}
    """
    rows = await pool.fetch(sql, *params)
    return _lines_from_rows(rows, poles=(layer == "poles"))


def _render_tile_webp(
    lines: list[LineGeom],
    bounds: mercantile.LngLatBbox,
    *,
    scale: int,
) -> bytes | None:
    """Return WebP bytes, or None if tile is empty (no strokes)."""
    size = TILE_SIZE * scale
    west, south = mercantile.xy(bounds.west, bounds.south)
    east, north = mercantile.xy(bounds.east, bounds.north)
    minx, maxx = (west, east) if west <= east else (east, west)
    miny, maxy = (south, north) if south <= north else (north, south)

    width_m = maxx - minx
    height_m = maxy - miny
    if width_m <= 0 or height_m <= 0:
        return None
    buf_m = BUFFER_PX * (width_m / TILE_SIZE)
    qminx, qmaxx = minx - buf_m, maxx + buf_m
    qminy, qmaxy = miny - buf_m, maxy + buf_m

    hits = [
        ln
        for ln in lines
        if ln.maxx >= qminx and ln.minx <= qmaxx and ln.maxy >= qminy and ln.miny <= qmaxy
    ]
    if not hits:
        return None

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)
    ctx.scale(scale, scale)
    ctx.set_operator(cairo.OPERATOR_SOURCE)
    ctx.set_source_rgba(0, 0, 0, 0)
    ctx.paint()
    ctx.set_operator(cairo.OPERATOR_OVER)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)

    def to_px(x: float, y: float) -> tuple[float, float]:
        px = (x - minx) / width_m * TILE_SIZE
        # canvas y grows down; 3857 y grows north → flip
        py = (maxy - y) / height_m * TILE_SIZE
        return px, py

    # Draw other first, backbone on top
    ordered = sorted(hits, key=lambda ln: 1 if ln.backbone else 0)

    drew = False
    for ln in ordered:
        ctx.set_source_rgba(ln.r, ln.g, ln.b, ln.a)
        ctx.set_line_width(ln.width)
        x0, y0 = to_px(*ln.coords[0])
        ctx.move_to(x0, y0)
        for x, y in ln.coords[1:]:
            ctx.line_to(*to_px(x, y))
        ctx.stroke()
        drew = True

    if not drew:
        return None

    img = Image.frombuffer(
        "RGBA", (size, size), surface.get_data(), "raw", "BGRa", 0, 1
    ).copy()
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=82, method=4)
    return out.getvalue()


def _write_tile(
    path: Path,
    lines: list[LineGeom],
    tile: mercantile.Tile,
    *,
    scale: int,
) -> bool:
    bounds = mercantile.bounds(tile)
    data = _render_tile_webp(lines, bounds, scale=scale)
    if data is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


async def ensure_tile_bytes(
    pool: asyncpg.Pool,
    settings: Settings,
    map_id: str,
    layer: TileLayer,
    version: str,
    z: int,
    x: int,
    y: int,
    *,
    scale: int = 1,
) -> bytes:
    """Disk hit or lazy-render one tile into the version tree."""
    existing = read_tile_bytes(settings, map_id, layer, version, z, x, y, scale=scale)
    if existing is not None:
        return existing

    tile = mercantile.Tile(x=x, y=y, z=z)
    lines = await _load_lines_for_bbox(
        pool, settings, map_id, layer, z, mercantile.bounds(tile)
    )
    path = tile_file_path(settings, map_id, layer, version, z, x, y, scale=scale)
    data = _render_tile_webp(lines, mercantile.bounds(tile), scale=scale)
    if data is None:
        # Cache empty marker as tiny transparent to avoid re-query storms
        data = empty_tile_bytes(scale=scale)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


async def _load_lines_for_bbox(
    pool: asyncpg.Pool,
    settings: Settings,
    map_id: str,
    layer: TileLayer,
    zoom: int,
    bounds: mercantile.LngLatBbox,
) -> list[LineGeom]:
    tol_m = settings.simplify_tolerance_meters(zoom)
    class_mode = settings.line_class_mode(zoom) if layer == "network" else "all"
    poles_name = settings.line_tile_poles_layer_name
    pad = 0.002
    west, south, east, north = (
        bounds.west - pad,
        bounds.south - pad,
        bounds.east + pad,
        bounds.north + pad,
    )

    params: list[Any] = [map_id, west, south, east, north]
    if tol_m > 0:
        params.append(tol_m)
        geom_expr = f"ST_Simplify(ST_Transform(f.geom, 3857), ${len(params)}::float8)"
    else:
        geom_expr = "ST_Transform(f.geom, 3857)"

    if layer == "poles":
        params.append(poles_name)
        layer_sql = f"AND l.name = ${len(params)}"
    else:
        params.append(poles_name)
        layer_sql = f"AND l.name IS DISTINCT FROM ${len(params)}"

    class_sql = ""
    if class_mode == "backbone":
        class_sql = """
          AND EXISTS (
                SELECT 1 FROM feature_classes fc
                WHERE fc.feature_id = f.id AND fc.class_name = 'backbone'
              )
        """

    sql = f"""
        SELECT ST_AsGeoJSON({geom_expr})::text AS geojson,
               f.style AS style,
               EXISTS (
                   SELECT 1 FROM feature_classes fc
                   WHERE fc.feature_id = f.id AND fc.class_name = 'backbone'
               ) AS is_backbone
        FROM features f
        JOIN layers l ON l.id = f.layer_id
        WHERE f.map_id = $1::uuid
          AND ST_GeometryType(f.geom) IN ('ST_LineString', 'ST_MultiLineString')
          AND ST_Intersects(
                f.geom,
                ST_MakeEnvelope($2, $3, $4, $5, 4326)
              )
          {layer_sql}
          {class_sql}
    """
    rows = await pool.fetch(sql, *params)
    return _lines_from_rows(rows, poles=(layer == "poles"))


async def generate_map_tiles(
    pool: asyncpg.Pool, settings: Settings, map_id: str
) -> dict[str, Any]:
    extent = await pool.fetchrow(
        """
        SELECT ST_XMin(ext) AS xmin, ST_YMin(ext) AS ymin,
               ST_XMax(ext) AS xmax, ST_YMax(ext) AS ymax
        FROM (
            SELECT ST_Extent(geom) AS ext
            FROM features
            WHERE map_id = $1::uuid
              AND ST_GeometryType(geom) IN ('ST_LineString', 'ST_MultiLineString')
        ) t
        """,
        map_id,
    )
    if extent is None or extent["xmin"] is None:
        raise RuntimeError("Map has no line features")

    version_new = uuid.uuid4().hex[:12]
    z_min = settings.tile_z_min
    z_max = settings.tile_z_max
    written = 0
    scanned = 0

    for layer in ("network", "poles"):
        layer_t: TileLayer = layer  # type: ignore[assignment]
        for z in range(z_min, z_max + 1):
            lines = await _load_lines(pool, settings, map_id, layer_t, z)
            tiles = _tiles_covering_lines(lines, z)
            for tile in tiles:
                scanned += 1
                for scale in (1, 2):
                    path = tile_file_path(
                        settings,
                        map_id,
                        layer_t,
                        version_new,
                        tile.z,
                        tile.x,
                        tile.y,
                        scale=scale,
                    )
                    if _write_tile(path, lines, tile, scale=scale):
                        written += 1

    row = await pool.fetchrow(
        """
        SELECT lines_tile_version, lines_tile_version_prev
        FROM maps WHERE id = $1::uuid
        """,
        map_id,
    )
    if row is None:
        raise RuntimeError("Map not found")

    old = row["lines_tile_version"]
    prev = row["lines_tile_version_prev"]

    await pool.execute(
        """
        UPDATE maps
        SET lines_tile_version = $2,
            lines_tile_version_prev = $3
        WHERE id = $1::uuid
        """,
        map_id,
        version_new,
        old,
    )

    # GC: drop older than previous (keep current + prev)
    if prev and prev != version_new and prev != old:
        for layer in ("network", "poles"):
            stale = layer_version_dir(settings, map_id, layer, prev)  # type: ignore[arg-type]
            if stale.exists():
                shutil.rmtree(stale, ignore_errors=True)

    return {
        "map_id": map_id,
        "lines_tile_version": version_new,
        "previous": old,
        "z_min": z_min,
        "z_max": z_max,
        "tiles_written": written,
        "tiles_scanned": scanned,
    }
