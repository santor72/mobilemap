from __future__ import annotations

import math
import random
from typing import Any


STYLE_KEYS = {
    "iconId",
    "iconColor",
    "iconScale",
    "markerShape",
    "recolorIcon",
    "lineColor",
    "lineWidth",
    "lineOpacity",
    "fillColor",
    "fillOpacity",
}


def _perp_dist_m(
    p: list[float],
    a: list[float],
    b: list[float],
    meters_per_deg_lon: float,
    meters_per_deg_lat: float,
) -> float:
    """Approximate distance from point p to segment ab in meters (lon, lat)."""
    ax, ay = a[0] * meters_per_deg_lon, a[1] * meters_per_deg_lat
    bx, by = b[0] * meters_per_deg_lon, b[1] * meters_per_deg_lat
    px, py = p[0] * meters_per_deg_lon, p[1] * meters_per_deg_lat
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def douglas_peucker(
    coords: list[list[float]],
    tolerance_m: float,
    ref_lat: float,
) -> list[list[float]]:
    if tolerance_m <= 0 or len(coords) <= 2:
        return coords

    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = 111_320.0 * max(0.01, math.cos(math.radians(ref_lat)))

    def _simplify(points: list[list[float]]) -> list[list[float]]:
        if len(points) <= 2:
            return points
        start, end = points[0], points[-1]
        max_dist = -1.0
        index = 0
        for i in range(1, len(points) - 1):
            d = _perp_dist_m(points[i], start, end, meters_per_deg_lon, meters_per_deg_lat)
            if d > max_dist:
                max_dist = d
                index = i
        if max_dist > tolerance_m:
            left = _simplify(points[: index + 1])
            right = _simplify(points[index:])
            return left[:-1] + right
        return [start, end]

    return _simplify(coords)


def stride_downsample(coords: list[list[float]], max_points: int) -> list[list[float]]:
    """Keep first/last and evenly spaced vertices. O(n), cheap pre-pass before DP."""
    if max_points <= 2 or len(coords) <= max_points:
        return coords
    n = len(coords)
    out: list[list[float]] = [coords[0]]
    for i in range(1, max_points - 1):
        idx = int(round(i * (n - 1) / (max_points - 1)))
        if coords[idx] is not out[-1]:
            out.append(coords[idx])
    if out[-1] is not coords[-1]:
        out.append(coords[-1])
    return out


def extract_style(properties: dict[str, Any]) -> dict[str, Any]:
    return {k: properties[k] for k in STYLE_KEYS if k in properties}


def normalize_feature(raw: dict[str, Any]) -> dict[str, Any] | None:
    geometry = raw.get("geometry") or {}
    gtype = geometry.get("type")
    if gtype not in ("Point", "LineString"):
        return None

    props = dict(raw.get("properties") or {})
    style = extract_style(props)
    for k in STYLE_KEYS:
        props.pop(k, None)

    feature_id = raw.get("id") or props.get("id")
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": geometry,
        "properties": {
            "id": props.get("id", feature_id),
            "layer_id": props.get("layer_id"),
            "kind": props.get("kind", gtype),
            "number": props.get("number"),
            "title": props.get("title"),
            "style": style,
            "classes": list(props.get("classes") or []),
        },
    }


def point_grid_dims(
    max_points: int,
    west: float,
    south: float,
    east: float,
    north: float,
) -> tuple[int, int]:
    """Cols/rows with cols*rows <= max_points, matching bbox aspect ratio."""
    if max_points <= 1:
        return 1, 1
    width = max(east - west, 1e-12)
    height = max(north - south, 1e-12)
    aspect = width / height
    cols = max(1, int(round(math.sqrt(max_points * aspect))))
    rows = max(1, max_points // cols)
    while cols * rows > max_points and cols > 1:
        cols -= 1
        rows = max(1, max_points // cols)
    return cols, rows


def sample_points_by_grid(
    points: list[dict[str, Any]],
    limit: int,
    bbox: tuple[float, float, float, float],
) -> list[dict[str, Any]]:
    """Keep at most one point per viewport cell (closest to cell center)."""
    if limit <= 0 or len(points) <= limit:
        return points
    west, south, east, north = bbox
    cols, rows = point_grid_dims(limit, west, south, east, north)
    width = max(east - west, 1e-12)
    height = max(north - south, 1e-12)
    cell_w = width / cols
    cell_h = height / rows

    best: dict[tuple[int, int], tuple[float, str, dict[str, Any]]] = {}
    for f in points:
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        lon = float(coords[0])
        lat = float(coords[1])
        gx = min(cols - 1, max(0, int((lon - west) / cell_w)))
        gy = min(rows - 1, max(0, int((lat - south) / cell_h)))
        cx = west + (gx + 0.5) * cell_w
        cy = south + (gy + 0.5) * cell_h
        dist2 = (lon - cx) ** 2 + (lat - cy) ** 2
        fid = str(f.get("id") or (f.get("properties") or {}).get("id") or "")
        prev = best.get((gx, gy))
        if prev is None or dist2 < prev[0] or (dist2 == prev[0] and fid < prev[1]):
            best[(gx, gy)] = (dist2, fid, f)
    return [item[2] for item in best.values()]


def filter_points_by_icon_ids(
    points: list[dict[str, Any]],
    allowed_icon_ids: set[str] | None,
) -> list[dict[str, Any]]:
    """Keep only points whose style.iconId is in allowed_icon_ids.

    None = no filter. Empty set = drop all points with icons (and circles).
    """
    if allowed_icon_ids is None:
        return points
    out: list[dict[str, Any]] = []
    for f in points:
        style = (f.get("properties") or {}).get("style") or {}
        icon_id = style.get("iconId")
        if icon_id is not None and str(icon_id) in allowed_icon_ids:
            out.append(f)
    return out


def line_classes(feature: dict[str, Any]) -> list[str]:
    props = feature.get("properties") or {}
    raw = props.get("classes") or []
    return [str(c) for c in raw]


def is_backbone_line(feature: dict[str, Any]) -> bool:
    return "backbone" in line_classes(feature)


def filter_lines_by_class_mode(
    lines: list[dict[str, Any]], mode: str
) -> list[dict[str, Any]]:
    """mode=backbone → only backbone; mode=all → unchanged."""
    if mode != "backbone":
        return lines
    return [f for f in lines if is_backbone_line(f)]


def _partition_backbone(
    lines: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    backbone: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for f in lines:
        (backbone if is_backbone_line(f) else other).append(f)
    return backbone, other


def _fill_cap_priority(
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
    cap: int,
) -> list[dict[str, Any]]:
    """Keep up to cap lines: all of primary (shuffled if over), then secondary."""
    if cap <= 0:
        return []
    primary = primary[:]
    secondary = secondary[:]
    random.shuffle(primary)
    random.shuffle(secondary)
    if len(primary) >= cap:
        return primary[:cap]
    return primary + secondary[: cap - len(primary)]


def preselect_lines(
    lines: list[dict[str, Any]], max_vertices: int
) -> tuple[list[dict[str, Any]], bool]:
    """Drop excess lines BEFORE simplify — DP over tens of thousands is the main CPU cost.

    When classes are present, prefer backbone over the rest (no br/access split).
    """
    if max_vertices <= 0 or not lines:
        return lines, False
    # After coarse simplify ~4–12 verts/line; keep a small surplus for budget packing.
    cap = max(64, max_vertices // 4)
    if len(lines) <= cap:
        return lines, False
    backbone, other = _partition_backbone(lines)
    if not backbone:
        return random.sample(lines, cap), True
    return _fill_cap_priority(backbone, other, cap), True


def per_line_vertex_cap(tolerance_m: float, max_line_vertices: int) -> int:
    """Hard cap per line before/alongside DP; coarser zoom → fewer verts."""
    if max_line_vertices <= 0:
        return 0
    if tolerance_m >= 40:
        return 8
    if tolerance_m >= 20:
        return 12
    if tolerance_m >= 8:
        return 24
    if tolerance_m >= 2:
        return 48
    return 128


def simplify_line_feature(
    feature: dict[str, Any],
    tolerance_m: float,
    *,
    max_vertices_per_line: int = 0,
) -> dict[str, Any]:
    coords = feature["geometry"].get("coordinates") or []
    if len(coords) < 2:
        return feature

    if max_vertices_per_line > 0 and len(coords) > max_vertices_per_line:
        coords = stride_downsample(coords, max_vertices_per_line)

    if tolerance_m > 0 and len(coords) > 2:
        ref_lat = (coords[0][1] + coords[-1][1]) / 2
        # Coarse zoom: stride already did most work; light DP only if still long.
        if tolerance_m >= 20 and len(coords) <= max_vertices_per_line:
            simplified = coords
        else:
            simplified = douglas_peucker(coords, tolerance_m, ref_lat)
        if len(simplified) < 2:
            simplified = [coords[0], coords[-1]]
        coords = simplified

    out = dict(feature)
    out["geometry"] = {"type": "LineString", "coordinates": coords}
    return out


def apply_vertex_budget(
    lines: list[dict[str, Any]], max_vertices: int
) -> tuple[list[dict[str, Any]], bool]:
    """Pack lines into a vertex budget. Backbone first, then everything else."""
    if max_vertices <= 0:
        return lines, False

    def vertex_count(f: dict[str, Any]) -> int:
        return len(f["geometry"].get("coordinates") or [])

    total = sum(vertex_count(f) for f in lines)
    if total <= max_vertices:
        return lines, False

    backbone, other = _partition_backbone(lines)
    random.shuffle(backbone)
    random.shuffle(other)

    kept: list[dict[str, Any]] = []
    used = 0
    for group in (backbone, other):
        for f in group:
            n = vertex_count(f)
            if n == 0:
                continue
            if used + n <= max_vertices:
                kept.append(f)
                used += n
    return kept, True


def process_feature_collection(
    collection: dict[str, Any],
    *,
    zoom: int,
    max_point_count: int,
    point_limit_max_zoom: int,
    max_line_vertices: int,
    simplify_tolerance_m: float,
    bbox: tuple[float, float, float, float],
    line_class_mode: str = "all",
    overview_icon_ids: set[str] | None = None,
) -> dict[str, Any]:
    gis_truncated = bool(collection.get("truncated"))
    points: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []

    for raw in collection.get("features") or []:
        normalized = normalize_feature(raw)
        if not normalized:
            continue
        if normalized["geometry"]["type"] == "Point":
            points.append(normalized)
        else:
            lines.append(normalized)

    points = filter_points_by_icon_ids(points, overview_icon_ids)

    points_thinned = False
    if max_point_count > 0 and zoom <= point_limit_max_zoom:
        before = len(points)
        points = sample_points_by_grid(points, max_point_count, bbox)
        points_thinned = len(points) < before

    before_class = len(lines)
    lines = filter_lines_by_class_mode(lines, line_class_mode)
    lines_class_filtered = len(lines) < before_class

    lines, lines_pre_thinned = preselect_lines(lines, max_line_vertices)
    per_line_cap = per_line_vertex_cap(simplify_tolerance_m, max_line_vertices)
    lines = [
        simplify_line_feature(
            f,
            simplify_tolerance_m,
            max_vertices_per_line=per_line_cap,
        )
        for f in lines
    ]
    lines, lines_budget_thinned = apply_vertex_budget(lines, max_line_vertices)
    lines_thinned = lines_pre_thinned or lines_budget_thinned or lines_class_filtered

    return {
        "type": "FeatureCollection",
        "features": points + lines,
        "truncated": gis_truncated,
        "thinned": points_thinned or lines_thinned,
        "meta": {
            "point_count": len(points),
            "line_count": len(lines),
            "gis_truncated": gis_truncated,
            "points_thinned": points_thinned,
            "lines_thinned": lines_thinned,
            "simplify_tolerance_m": simplify_tolerance_m,
            "line_class_mode": line_class_mode,
        },
    }


def normalize_feature_detail(raw: dict[str, Any]) -> dict[str, Any]:
    geometry = raw.get("geometry") or {}
    style = raw.get("style") or {}
    if isinstance(style, dict) is False:
        style = {}
    return {
        "id": raw.get("id"),
        "layer_id": raw.get("layer_id"),
        "map_id": raw.get("map_id"),
        "layer_name": raw.get("layer_name"),
        "number": raw.get("number"),
        "kind": raw.get("kind"),
        "title": raw.get("title"),
        "description": raw.get("description"),
        "geometry": geometry,
        "style": style,
        "classes": list(raw.get("classes") or []),
        "version": raw.get("version"),
    }
