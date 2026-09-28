from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.data.gis_reader import GisError, GisReader
from app.data.processing import extract_style
from app.db import get_pool
from app.sync.icons import IconSyncPipeline

MAX_SUBDIVIDE_DEPTH = 8
MIN_CELL_DEG = 0.002  # ~200m


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _split_bbox(
    west: float, south: float, east: float, north: float
) -> list[tuple[float, float, float, float]]:
    mx = (west + east) / 2
    my = (south + north) / 2
    return [
        (west, south, mx, my),
        (mx, south, east, my),
        (west, my, mx, north),
        (mx, my, east, north),
    ]


def _feature_row(raw: dict[str, Any], map_id: str) -> dict[str, Any] | None:
    geometry = raw.get("geometry") or {}
    gtype = geometry.get("type")
    if gtype not in ("Point", "LineString"):
        return None
    coords = geometry.get("coordinates")
    if not coords:
        return None
    props = dict(raw.get("properties") or {})
    style = extract_style(props)
    feature_id = raw.get("id") or props.get("id")
    if not feature_id:
        return None
    layer_id = props.get("layer_id")
    if not layer_id:
        return None
    return {
        "id": str(feature_id),
        "map_id": map_id,
        "layer_id": str(layer_id),
        "kind": props.get("kind") or gtype,
        "number": props.get("number"),
        "title": props.get("title"),
        "description": props.get("description"),
        "style": style,
        "geometry": geometry,
        "version": props.get("version"),
    }


class MapSyncService:
    def __init__(self, settings: Settings, *, sync_icons: bool | None = None) -> None:
        self._settings = settings
        self._gis = GisReader(settings)
        self._sync_icons = (
            settings.sync_icons if sync_icons is None else sync_icons
        )

    async def sync_all_maps(self) -> list[dict[str, Any]]:
        maps = await self._gis.list_maps()
        results = []
        for m in maps:
            results.append(await self.sync_map(str(m["id"])))
        return results

    async def sync_map(self, map_id: str) -> dict[str, Any]:
        pool = get_pool()
        run_id = uuid.uuid4()
        started = datetime.now(timezone.utc)
        await pool.execute(
            """
            INSERT INTO sync_runs(id, map_id, started_at, status, stats)
            VALUES($1, $2::uuid, $3, 'running', '{}'::jsonb)
            """,
            run_id,
            map_id,
            started,
        )

        stats: dict[str, Any] = {
            "cells": 0,
            "features_upserted": 0,
            "features_deleted": 0,
            "points": 0,
            "lines": 0,
            "skipped_polygons": 0,
        }

        try:
            maps = await self._gis.list_maps()
            map_row = next((m for m in maps if str(m["id"]) == map_id), None)
            if map_row is None:
                raise GisError(404, "MAP_NOT_FOUND", "Map not found in GIS")

            layers = await self._gis.list_layers(map_id)
            try:
                bounds = await self._gis.get_bounds(map_id)
            except GisError as exc:
                if exc.code == "MAP_EMPTY":
                    await self._swap_empty(pool, run_id, map_id, map_row, layers)
                    return await self._finish(pool, run_id, "ok", stats)
                raise

            west, south = float(bounds["xmin"]), float(bounds["ymin"])
            east, north = float(bounds["xmax"]), float(bounds["ymax"])

            await pool.execute(
                "DELETE FROM features_staging WHERE map_id = $1::uuid", map_id
            )
            await pool.execute(
                "DELETE FROM features_staging WHERE sync_run_id = $1", run_id
            )

            seen: set[str] = set()
            await self._fetch_cell(
                pool,
                run_id,
                map_id,
                (west, south, east, north),
                depth=0,
                seen=seen,
                stats=stats,
            )

            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO maps(id, name, created_at, synced_at)
                        VALUES($1::uuid, $2, $3::timestamptz, now())
                        ON CONFLICT (id) DO UPDATE
                        SET name = EXCLUDED.name,
                            created_at = COALESCE(EXCLUDED.created_at, maps.created_at),
                            synced_at = now()
                        """,
                        map_id,
                        map_row.get("name") or map_id,
                        _parse_dt(map_row.get("created_at")),
                    )
                    await conn.execute("DELETE FROM layers WHERE map_id = $1::uuid", map_id)
                    for layer in layers:
                        await conn.execute(
                            """
                            INSERT INTO layers(id, map_id, name, position, count, version)
                            VALUES($1::uuid, $2::uuid, $3, $4, $5, $6)
                            """,
                            str(layer["id"]),
                            map_id,
                            layer.get("name") or "",
                            int(layer.get("position") or 0),
                            int(layer.get("count") or 0),
                            int(layer.get("version") or 0),
                        )
                    # Upsert first, then drop ids missing from this run.
                    # Surviving rows keep feature_classes (ON DELETE CASCADE
                    # only fires for the anti-join delete below).
                    await conn.execute(
                        """
                        INSERT INTO features(
                            id, map_id, layer_id, kind, number, title, description,
                            style, geom, version, updated_at
                        )
                        SELECT id, map_id, layer_id, kind, number, title, description,
                               style, geom, version, now()
                        FROM features_staging
                        WHERE sync_run_id = $1
                        ON CONFLICT (id) DO UPDATE SET
                            map_id = EXCLUDED.map_id,
                            layer_id = EXCLUDED.layer_id,
                            kind = EXCLUDED.kind,
                            number = EXCLUDED.number,
                            title = EXCLUDED.title,
                            description = EXCLUDED.description,
                            style = EXCLUDED.style,
                            geom = EXCLUDED.geom,
                            version = EXCLUDED.version,
                            updated_at = now()
                        """,
                        run_id,
                    )
                    deleted_status = await conn.execute(
                        """
                        DELETE FROM features f
                        WHERE f.map_id = $1::uuid
                          AND NOT EXISTS (
                                SELECT 1
                                FROM features_staging s
                                WHERE s.sync_run_id = $2
                                  AND s.id = f.id
                              )
                        """,
                        map_id,
                        run_id,
                    )
                    stats["features_deleted"] = (
                        int(deleted_status.split()[-1]) if deleted_status else 0
                    )
                    await conn.execute(
                        "DELETE FROM features_staging WHERE sync_run_id = $1", run_id
                    )

            stats["features_upserted"] = len(seen)
            if self._sync_icons:
                icon_stats = await IconSyncPipeline(
                    self._settings, self._gis
                ).sync_map_icons(map_id)
                stats["icons"] = icon_stats
            return await self._finish(pool, run_id, "ok", stats)
        except Exception as exc:
            await self._finish(pool, run_id, "error", stats, error=str(exc))
            raise

    async def _swap_empty(
        self,
        pool: Any,
        run_id: uuid.UUID,
        map_id: str,
        map_row: dict[str, Any],
        layers: list[dict[str, Any]],
    ) -> None:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO maps(id, name, created_at, synced_at)
                    VALUES($1::uuid, $2, $3::timestamptz, now())
                    ON CONFLICT (id) DO UPDATE
                    SET name = EXCLUDED.name, synced_at = now()
                    """,
                    map_id,
                    map_row.get("name") or map_id,
                    _parse_dt(map_row.get("created_at")),
                )
                await conn.execute("DELETE FROM layers WHERE map_id = $1::uuid", map_id)
                for layer in layers:
                    await conn.execute(
                        """
                        INSERT INTO layers(id, map_id, name, position, count, version)
                        VALUES($1::uuid, $2::uuid, $3, $4, $5, $6)
                        """,
                        str(layer["id"]),
                        map_id,
                        layer.get("name") or "",
                        int(layer.get("position") or 0),
                        int(layer.get("count") or 0),
                        int(layer.get("version") or 0),
                    )
                await conn.execute("DELETE FROM features WHERE map_id = $1::uuid", map_id)
                await conn.execute(
                    "DELETE FROM features_staging WHERE sync_run_id = $1", run_id
                )

    async def _finish(
        self,
        pool: Any,
        run_id: uuid.UUID,
        status: str,
        stats: dict[str, Any],
        error: str | None = None,
    ) -> dict[str, Any]:
        await pool.execute(
            """
            UPDATE sync_runs
            SET finished_at = now(), status = $2, error = $3, stats = $4::jsonb
            WHERE id = $1
            """,
            run_id,
            status,
            error,
            json.dumps(stats),
        )
        return {"sync_run_id": str(run_id), "status": status, "stats": stats, "error": error}

    async def _fetch_cell(
        self,
        pool: Any,
        run_id: uuid.UUID,
        map_id: str,
        bbox: tuple[float, float, float, float],
        *,
        depth: int,
        seen: set[str],
        stats: dict[str, Any],
    ) -> None:
        west, south, east, north = bbox
        stats["cells"] += 1
        collection = await self._gis.get_raw_features(map_id, bbox)
        truncated = bool(collection.get("truncated"))
        features = collection.get("features") or []

        width = east - west
        height = north - south
        too_big = truncated and depth < MAX_SUBDIVIDE_DEPTH and (
            width > MIN_CELL_DEG or height > MIN_CELL_DEG
        )
        if too_big:
            for child in _split_bbox(west, south, east, north):
                await self._fetch_cell(
                    pool, run_id, map_id, child, depth=depth + 1, seen=seen, stats=stats
                )
            return

        rows_to_insert: list[tuple] = []
        for raw in features:
            gtype = (raw.get("geometry") or {}).get("type")
            if gtype == "Polygon" or gtype == "MultiPolygon":
                stats["skipped_polygons"] += 1
                continue
            parsed = _feature_row(raw, map_id)
            if not parsed:
                continue
            fid = parsed["id"]
            if fid in seen:
                continue
            seen.add(fid)
            if parsed["geometry"]["type"] == "Point":
                stats["points"] += 1
            else:
                stats["lines"] += 1
            rows_to_insert.append(
                (
                    uuid.UUID(fid),
                    uuid.UUID(map_id),
                    uuid.UUID(parsed["layer_id"]),
                    parsed["kind"],
                    parsed["number"],
                    parsed["title"],
                    parsed["description"],
                    json.dumps(parsed["style"]),
                    json.dumps(parsed["geometry"]),
                    parsed["version"],
                    run_id,
                )
            )

        if not rows_to_insert:
            return

        await pool.executemany(
            """
            INSERT INTO features_staging(
                id, map_id, layer_id, kind, number, title, description,
                style, geom, version, sync_run_id
            )
            VALUES(
                $1, $2, $3, $4, $5, $6, $7,
                $8::jsonb,
                ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON($9), 4326)),
                $10, $11
            )
            ON CONFLICT (id) DO NOTHING
            """,
            rows_to_insert,
        )
