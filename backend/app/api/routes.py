from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from app.auth import require_basic_auth
from app.config import Settings, get_settings
from app.data.factory import create_reader
from app.data.gis_reader import GisError, GisReader
from app.data.postgis_reader import PostgisReader
from app.data.processing import normalize_feature_detail
from app.sync.job import MapSyncService

router = APIRouter(prefix="/api")

Reader = GisReader | PostgisReader


def get_reader(settings: Settings = Depends(get_settings)) -> Reader:
    try:
        return create_reader(settings)
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "BAD_CONFIG", "error": str(exc)},
        ) from exc


def _raise_gis(exc: GisError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "error": exc.message},
    )


def _require_sync_access(
    _: Annotated[str, Depends(require_basic_auth)],
    settings: Settings = Depends(get_settings),
    x_sync_token: Annotated[str | None, Header()] = None,
) -> None:
    if settings.sync_token and x_sync_token != settings.sync_token:
        raise HTTPException(
            status_code=403,
            detail={"code": "SYNC_FORBIDDEN", "error": "Invalid sync token"},
        )


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/config")
async def public_config(
    _: Annotated[str, Depends(require_basic_auth)],
    settings: Settings = Depends(get_settings),
) -> dict:
    return {
        "map_provider": settings.map_provider,
        "yandex_maps_api_key": settings.yandex_maps_api_key,
        "point_detail_zoom": settings.gis_point_detail_zoom,
        "point_detail_zoom_second": (
            settings.gis_point_detail_zoom_second
            if settings.point_second_lod_active
            else None
        ),
        "point_icon_size": settings.gis_point_icon_size,
        "point_circle_size": settings.gis_point_circle_size,
        "point_fixed_size_max_zoom": settings.gis_point_fixed_size_max_zoom,
        "icon_fixed": settings.gis_point_icon_fixed,
        # Non-empty → overview points stay clickable while allowlist filter is on
        "point_overview_icons": settings.point_overview_icons,
        "point_overview_icons_second": (
            settings.point_overview_icons_second
            if settings.point_second_lod_active
            else []
        ),
        "max_point_count": settings.gis_max_point_count,
        "max_line_vertices": settings.gis_max_line_vertices,
        "initial_radius_km": settings.gis_initial_radius_km,
        "data_source": settings.data_source,
        "line_render": settings.line_render_mode,
        "tile_z_min": settings.tile_z_min,
        "tile_z_max": settings.tile_z_max,
        "line_tile_poles_layer_name": settings.line_tile_poles_layer_name,
    }


@router.get("/maps/{map_id}/tiles/meta")
async def map_tiles_meta(
    map_id: str,
    _: Annotated[str, Depends(require_basic_auth)],
    settings: Settings = Depends(get_settings),
) -> dict:
    from app.db import get_pool

    if settings.data_source != "postgis":
        return {
            "map_id": map_id,
            "lines_tile_version": None,
            "layers": ["network", "poles"],
            "z_min": settings.tile_z_min,
            "z_max": settings.tile_z_max,
        }
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT lines_tile_version
        FROM maps WHERE id = $1::uuid
        """,
        map_id,
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "MAP_NOT_FOUND", "error": "Map not found"},
        )
    return {
        "map_id": map_id,
        "lines_tile_version": row["lines_tile_version"],
        "layers": ["network", "poles"],
        "z_min": settings.tile_z_min,
        "z_max": settings.tile_z_max,
    }


@router.get("/maps/{map_id}/tiles/lines/{layer}/{tile_version}/{z}/{x}/{filename}")
async def map_line_tile(
    map_id: str,
    layer: str,
    tile_version: str,
    z: int,
    x: int,
    filename: str,
    settings: Settings = Depends(get_settings),
) -> Response:
    """Open versioned tiles (no Basic auth) — perimeter-trusted.

    filename: ``{y}.webp`` or ``{y}@2x.webp``
    Missing files are lazy-rendered into the version tree (or transparent if empty).
    """
    from app.db import get_pool
    from app.tiles.render import empty_tile_bytes, ensure_tile_bytes

    if layer not in ("network", "poles"):
        raise HTTPException(
            status_code=404,
            detail={"code": "TILE_LAYER_UNKNOWN", "error": "Unknown tile layer"},
        )
    if not (0 <= z <= 24 and x >= 0):
        raise HTTPException(status_code=404, detail={"code": "TILE_NOT_FOUND", "error": "Bad tile"})

    scale = 1
    name = filename
    if name.endswith("@2x.webp"):
        scale = 2
        name = name[: -len("@2x.webp")]
    elif name.endswith(".webp"):
        name = name[: -len(".webp")]
    else:
        raise HTTPException(status_code=404, detail={"code": "TILE_NOT_FOUND", "error": "Bad tile"})

    try:
        y = int(name)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "TILE_NOT_FOUND", "error": "Bad tile"}
        ) from exc
    if y < 0:
        raise HTTPException(status_code=404, detail={"code": "TILE_NOT_FOUND", "error": "Bad tile"})

    try:
        pool = get_pool()
        content = await ensure_tile_bytes(
            pool,
            settings,
            map_id,
            layer,  # type: ignore[arg-type]
            tile_version,
            z,
            x,
            y,
            scale=scale,
        )
    except RuntimeError:
        content = empty_tile_bytes(scale=scale)

    return Response(
        content=content,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/maps")
async def list_maps(
    _: Annotated[str, Depends(require_basic_auth)],
    reader: Reader = Depends(get_reader),
) -> dict:
    try:
        rows = await reader.list_maps()
    except GisError as exc:
        _raise_gis(exc)
    return {"rows": rows}


@router.get("/maps/{map_id}/layers")
async def list_layers(
    map_id: str,
    _: Annotated[str, Depends(require_basic_auth)],
    reader: Reader = Depends(get_reader),
) -> dict:
    try:
        rows = await reader.list_layers(map_id)
    except GisError as exc:
        _raise_gis(exc)
    return {"rows": rows}


@router.get("/maps/{map_id}/bounds")
async def map_bounds(
    map_id: str,
    _: Annotated[str, Depends(require_basic_auth)],
    reader: Reader = Depends(get_reader),
) -> dict:
    try:
        return await reader.get_bounds(map_id)
    except GisError as exc:
        _raise_gis(exc)


@router.get("/maps/{map_id}/features")
async def map_features(
    map_id: str,
    _: Annotated[str, Depends(require_basic_auth)],
    bbox: Annotated[str, Query(description="west,south,east,north")],
    zoom: Annotated[int, Query(ge=0, le=24)],
    layers: Annotated[str | None, Query()] = None,
    reader: Reader = Depends(get_reader),
) -> dict:
    try:
        parts = [float(x.strip()) for x in bbox.split(",")]
        if len(parts) != 4:
            raise ValueError
        west, south, east, north = parts
        west, east = sorted((west, east))
        south, north = sorted((south, north))
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_BBOX", "error": "bbox must be west,south,east,north"},
        )

    layer_ids = [x.strip() for x in layers.split(",") if x.strip()] if layers else None

    try:
        return await reader.get_features(
            map_id, (west, south, east, north), layer_ids, zoom
        )
    except GisError as exc:
        _raise_gis(exc)


@router.get("/features/{feature_id}")
async def feature_detail(
    feature_id: str,
    _: Annotated[str, Depends(require_basic_auth)],
    reader: Reader = Depends(get_reader),
) -> dict:
    try:
        raw = await reader.get_feature(feature_id)
    except GisError as exc:
        _raise_gis(exc)
    return normalize_feature_detail(raw)


@router.get("/assets/{asset_id}")
async def asset_png(
    asset_id: str,
    _: Annotated[str, Depends(require_basic_auth)],
    reader: Reader = Depends(get_reader),
) -> Response:
    try:
        content = await reader.get_asset(asset_id)
    except GisError as exc:
        _raise_gis(exc)
    return Response(
        content=content,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.post("/sync")
async def sync_all(
    _: Annotated[None, Depends(_require_sync_access)],
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if not settings.database_url:
        raise HTTPException(
            status_code=503,
            detail={"code": "DB_NOT_CONFIGURED", "error": "DATABASE_URL is not set"},
        )
    try:
        results = await MapSyncService(settings).sync_all_maps()
    except GisError as exc:
        _raise_gis(exc)
    return {"runs": results}


@router.post("/sync/{map_id}")
async def sync_one(
    map_id: str,
    _: Annotated[None, Depends(_require_sync_access)],
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if not settings.database_url:
        raise HTTPException(
            status_code=503,
            detail={"code": "DB_NOT_CONFIGURED", "error": "DATABASE_URL is not set"},
        )
    try:
        return await MapSyncService(settings).sync_map(map_id)
    except GisError as exc:
        _raise_gis(exc)
