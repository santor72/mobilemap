from __future__ import annotations

import json
import math
from typing import Any

import asyncpg

from app.config import Settings
from app.data.gis_reader import GisError, GisReader
from app.data.processing import apply_vertex_budget, point_grid_dims, preselect_lines
from app.db import get_pool


def _meters_to_degrees(meters: float, lat: float) -> float:
    if meters <= 0:
        return 0.0
    meters_per_deg = 111_320.0 * max(0.01, math.cos(math.radians(lat)))
    meters_per_deg_lat = 111_320.0
    return meters / min(meters_per_deg, meters_per_deg_lat)


class PostgisReader:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._gis = GisReader(settings)

    async def list_maps(self) -> list[dict[str, Any]]:
        pool = get_pool()
        rows = await pool.fetch(
            """
            SELECT id::text AS id, name, created_at,
                   lines_tile_version,
                   (SELECT count(*) FROM features f WHERE f.map_id = m.id) AS total
            FROM maps m
            ORDER BY created_at DESC NULLS LAST, name
            """
        )
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "report": {"total": int(r["total"])},
                "lines_tile_version": r["lines_tile_version"],
            }
            for r in rows
        ]

    async def list_layers(self, map_id: str) -> list[dict[str, Any]]:
        pool = get_pool()
        rows = await pool.fetch(
            """
            SELECT id::text AS id, name, position, count, version
            FROM layers
            WHERE map_id = $1::uuid
            ORDER BY position, id
            """,
            map_id,
        )
        if not rows:
            exists = await pool.fetchval("SELECT 1 FROM maps WHERE id = $1::uuid", map_id)
            if not exists:
                raise GisError(404, "MAP_NOT_FOUND", "Map not found")
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "position": r["position"],
                "count": r["count"],
                "version": r["version"],
            }
            for r in rows
        ]

    async def get_bounds(self, map_id: str) -> dict[str, float]:
        pool = get_pool()
        row = await pool.fetchrow(
            """
            SELECT
                ST_XMin(ext) AS xmin,
                ST_YMin(ext) AS ymin,
                ST_XMax(ext) AS xmax,
                ST_YMax(ext) AS ymax
            FROM (
                SELECT ST_Extent(geom) AS ext
                FROM features
                WHERE map_id = $1::uuid
            ) t
            """,
            map_id,
        )
        if row is None or row["xmin"] is None:
            exists = await pool.fetchval("SELECT 1 FROM maps WHERE id = $1::uuid", map_id)
            if not exists:
                raise GisError(404, "MAP_NOT_FOUND", "Map not found")
            raise GisError(404, "MAP_EMPTY", "Map has no features with coordinates")
        return {
            "xmin": float(row["xmin"]),
            "ymin": float(row["ymin"]),
            "xmax": float(row["xmax"]),
            "ymax": float(row["ymax"]),
        }

    async def get_features(
        self,
        map_id: str,
        bbox: tuple[float, float, float, float],
        layers: list[str] | None,
        zoom: int,
    ) -> dict[str, Any]:
        west, south, east, north = bbox
        pool = get_pool()
        tolerance_m = self._settings.simplify_tolerance_meters(zoom)
        mid_lat = (south + north) / 2
        tol_deg = _meters_to_degrees(tolerance_m, mid_lat)

        params: list[Any] = [map_id, west, south, east, north]
        layer_sql = ""
        if layers:
            params.append(layers)
            layer_sql = f"AND layer_id = ANY(${len(params)}::uuid[])"

        icon_sql = ""
        allowlist = self._settings.point_overview_allowlist(zoom)
        if allowlist is not None:
            params.append(allowlist)
            icon_sql = f"""
              AND EXISTS (
                    SELECT 1 FROM icon_uuid iu
                    WHERE iu.icon_name = ANY(${len(params)}::text[])
                      AND iu.id::text = NULLIF(style->>'iconId', '')
                  )
            """

        # Points. Overview zoom: one point per viewport cell in SQL so
        # discarded rows are never serialized to GeoJSON / shipped to Python.
        max_points = self._settings.gis_max_point_count
        limit_points = (
            max_points > 0 and zoom <= self._settings.gis_point_fixed_size_max_zoom
        )
        point_where = f"""
            map_id = $1::uuid
              AND ST_GeometryType(geom) = 'ST_Point'
              AND ST_Intersects(
                    geom,
                    ST_MakeEnvelope($2, $3, $4, $5, 4326)
                  )
              {layer_sql}
              {icon_sql}
        """
        if limit_points:
            cols, rows = point_grid_dims(max_points, west, south, east, north)
            params.extend([cols, rows])
            cols_p = len(params) - 1
            rows_p = len(params)
            point_sql = f"""
                WITH matched AS MATERIALIZED (
                    SELECT id,
                           geom,
                           ST_X(geom) AS lon,
                           ST_Y(geom) AS lat,
                           count(*) OVER () AS match_count
                    FROM features
                    WHERE {point_where}
                ),
                scored AS (
                    SELECT id,
                           geom,
                           lon,
                           lat,
                           match_count,
                           LEAST(
                             ${cols_p}::int - 1,
                             GREATEST(
                               0,
                               floor(
                                 (lon - $2)
                                 / (NULLIF($4 - $2, 0) / ${cols_p}::float8)
                               )::int
                             )
                           ) AS gx,
                           LEAST(
                             ${rows_p}::int - 1,
                             GREATEST(
                               0,
                               floor(
                                 (lat - $3)
                                 / (NULLIF($5 - $3, 0) / ${rows_p}::float8)
                               )::int
                             )
                           ) AS gy
                    FROM matched
                ),
                picked AS MATERIALIZED (
                    SELECT DISTINCT ON (gx, gy)
                           id,
                           match_count
                    FROM scored
                    ORDER BY gx, gy,
                      (lon - ($2 + (gx + 0.5) * (NULLIF($4 - $2, 0) / ${cols_p}::float8)))
                        * (lon - ($2 + (gx + 0.5) * (NULLIF($4 - $2, 0) / ${cols_p}::float8)))
                      + (lat - ($3 + (gy + 0.5) * (NULLIF($5 - $3, 0) / ${rows_p}::float8)))
                        * (lat - ($3 + (gy + 0.5) * (NULLIF($5 - $3, 0) / ${rows_p}::float8))),
                      id
                )
                SELECT f.id::text AS id,
                       f.layer_id::text AS layer_id,
                       f.kind, f.number, f.title, f.style,
                       ST_AsGeoJSON(f.geom)::json AS geometry,
                       p.match_count
                FROM picked p
                JOIN features f ON f.id = p.id
            """
        else:
            point_sql = f"""
                SELECT id::text AS id, layer_id::text AS layer_id, kind, number, title, style,
                       ST_AsGeoJSON(geom)::json AS geometry
                FROM features
                WHERE {point_where}
            """
        point_rows = await pool.fetch(point_sql, *params)

        points_thinned = bool(
            limit_points
            and point_rows
            and int(point_rows[0]["match_count"]) > len(point_rows)
        )

        features: list[dict[str, Any]] = []
        for r in point_rows:
            geometry = r["geometry"]
            if isinstance(geometry, str):
                geometry = json.loads(geometry)
            style = r["style"] or {}
            if isinstance(style, str):
                style = json.loads(style)
            features.append(
                {
                    "type": "Feature",
                    "id": r["id"],
                    "geometry": geometry,
                    "properties": {
                        "id": r["id"],
                        "layer_id": r["layer_id"],
                        "kind": r["kind"] or "Point",
                        "number": r["number"],
                        "title": r["title"],
                        "style": style if isinstance(style, dict) else {},
                    },
                }
            )

        if self._settings.line_render_mode == "raster":
            return {
                "type": "FeatureCollection",
                "features": features,
                "truncated": False,
                "thinned": points_thinned,
                "meta": {
                    "point_count": len(features),
                    "line_count": 0,
                    "gis_truncated": False,
                    "points_thinned": points_thinned,
                    "lines_thinned": False,
                    "simplify_tolerance_m": tolerance_m,
                    "line_class_mode": self._settings.line_class_mode(zoom),
                    "line_render": "raster",
                },
            }

        # Lines with simplify in SQL; optional class LOD (backbone-only at low zoom)
        class_mode = self._settings.line_class_mode(zoom)
        line_params: list[Any] = [map_id, west, south, east, north]
        if tol_deg > 0:
            line_params.append(tol_deg)
            geom_expr = f"ST_Simplify(geom, ${len(line_params)}::float8)"
        else:
            geom_expr = "geom"

        layer_sql_lines = ""
        if layers:
            line_params.append(layers)
            layer_sql_lines = f"AND layer_id = ANY(${len(line_params)}::uuid[])"

        class_sql = ""
        if class_mode == "backbone":
            class_sql = """
              AND EXISTS (
                    SELECT 1 FROM feature_classes fc
                    WHERE fc.feature_id = features.id
                      AND fc.class_name = 'backbone'
                  )
            """

        line_sql = f"""
            SELECT id::text AS id, layer_id::text AS layer_id, kind, number, title, style,
                   ST_AsGeoJSON({geom_expr})::json AS geometry
            FROM features
            WHERE map_id = $1::uuid
              AND ST_GeometryType(geom) IN ('ST_LineString', 'ST_MultiLineString')
              AND ST_Intersects(
                    geom,
                    ST_MakeEnvelope($2, $3, $4, $5, 4326)
                  )
              {layer_sql_lines}
              {class_sql}
        """
        line_rows = await pool.fetch(line_sql, *line_params)

        max_vertices = self._settings.gis_max_line_vertices
        line_features: list[dict[str, Any]] = []
        for r in line_rows:
            geometry = r["geometry"]
            if isinstance(geometry, str):
                geometry = json.loads(geometry)
            if not geometry or geometry.get("type") not in ("LineString", "MultiLineString"):
                continue
            # Flatten MultiLineString into separate features for client (expects LineString)
            if geometry["type"] == "MultiLineString":
                for idx, part in enumerate(geometry.get("coordinates") or []):
                    line_features.append(
                        self._line_feature(r, {"type": "LineString", "coordinates": part}, idx)
                    )
            else:
                line_features.append(self._line_feature(r, geometry))

        # Attach classes before preselect/budget so backbone priority works
        base_line_ids = list(
            {
                str(f["properties"]["id"])
                for f in line_features
                if f.get("properties", {}).get("id")
            }
        )
        classes_by_id = await self._load_classes(pool, base_line_ids)
        for f in line_features:
            base_id = str(f["properties"]["id"])
            f["properties"]["classes"] = classes_by_id.get(base_id, [])

        line_features, lines_pre_thinned = preselect_lines(line_features, max_vertices)
        line_features, budget_thinned = apply_vertex_budget(line_features, max_vertices)
        lines_thinned = lines_pre_thinned or budget_thinned
        features.extend(line_features)

        return {
            "type": "FeatureCollection",
            "features": features,
            "truncated": False,
            "thinned": points_thinned or lines_thinned,
            "meta": {
                "point_count": len(point_rows),
                "line_count": len(line_features),
                "gis_truncated": False,
                "points_thinned": points_thinned,
                "lines_thinned": lines_thinned,
                "simplify_tolerance_m": tolerance_m,
                "line_class_mode": class_mode,
                "line_render": "vector",
            },
        }

    @staticmethod
    async def _load_classes(
        pool: asyncpg.Pool, feature_ids: list[str]
    ) -> dict[str, list[str]]:
        if not feature_ids:
            return {}
        rows = await pool.fetch(
            """
            SELECT feature_id::text AS feature_id,
                   array_agg(class_name ORDER BY class_name) AS classes
            FROM feature_classes
            WHERE feature_id = ANY($1::uuid[])
            GROUP BY feature_id
            """,
            feature_ids,
        )
        return {r["feature_id"]: list(r["classes"] or []) for r in rows}

    @staticmethod
    def _line_feature(row: asyncpg.Record, geometry: dict, suffix: int | None = None) -> dict:
        style = row["style"] or {}
        if isinstance(style, str):
            style = json.loads(style)
        fid = row["id"] if suffix is None else f"{row['id']}:{suffix}"
        return {
            "type": "Feature",
            "id": fid,
            "geometry": geometry,
            "properties": {
                "id": row["id"],
                "layer_id": row["layer_id"],
                "kind": row["kind"] or "LineString",
                "number": row["number"],
                "title": row["title"],
                "style": style if isinstance(style, dict) else {},
                "classes": [],
            },
        }

    async def get_feature(self, feature_id: str) -> dict[str, Any]:
        pool = get_pool()
        # strip MultiLineString suffix if any
        base_id = feature_id.split(":", 1)[0]
        row = await pool.fetchrow(
            """
            SELECT f.id::text AS id,
                   f.layer_id::text AS layer_id,
                   f.map_id::text AS map_id,
                   l.name AS layer_name,
                   f.number, f.kind, f.title, f.description, f.style, f.version,
                   ST_AsGeoJSON(f.geom)::json AS geometry
            FROM features f
            LEFT JOIN layers l ON l.id = f.layer_id
            WHERE f.id = $1::uuid
            """,
            base_id,
        )
        if row is None:
            raise GisError(404, "FEATURE_NOT_FOUND", "Feature not found")
        geometry = row["geometry"]
        if isinstance(geometry, str):
            geometry = json.loads(geometry)
        style = row["style"] or {}
        if isinstance(style, str):
            style = json.loads(style)
        classes = await pool.fetchval(
            """
            SELECT coalesce(array_agg(class_name ORDER BY class_name), '{}')
            FROM feature_classes
            WHERE feature_id = $1::uuid
            """,
            base_id,
        )
        return {
            "id": row["id"],
            "layer_id": row["layer_id"],
            "map_id": row["map_id"],
            "layer_name": row["layer_name"],
            "number": row["number"],
            "kind": row["kind"],
            "title": row["title"],
            "description": row["description"],
            "geometry": geometry,
            "style": style if isinstance(style, dict) else {},
            "classes": list(classes or []),
            "version": row["version"],
        }

    async def get_asset(self, asset_id: str) -> bytes:
        return await self._gis.get_asset(asset_id)
