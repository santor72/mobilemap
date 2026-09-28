from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class GisError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.upstream_status = upstream_status


class GisReader:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base = settings.gis_base_url.rstrip("/")
        self._token = settings.gis_api_token
        self._timeout = settings.gis_timeout_seconds
        self._cache_dir = Path(settings.asset_cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def _overview_icon_ids(self, zoom: int) -> set[str] | None:
        """Resolve GIS_POINT_OVERVIEW_ICONS → icon UUID set, or None if inactive/unavailable."""
        if not self._settings.overview_icon_filter_active(zoom):
            return None
        names = self._settings.point_overview_icons
        try:
            from app.db import get_pool

            pool = get_pool()
        except RuntimeError:
            logger.warning(
                "GIS_POINT_OVERVIEW_ICONS is set but DB pool is unavailable; "
                "skipping overview icon filter"
            )
            return None
        rows = await pool.fetch(
            "SELECT id::text AS id FROM icon_uuid WHERE icon_name = ANY($1::text[])",
            names,
        )
        return {r["id"] for r in rows}

    def _ensure_configured(self) -> None:
        if not self._base or not self._token:
            raise GisError(503, "GIS_NOT_CONFIGURED", "GIS URL or token is not configured")

    def _headers(self, accept: str = "application/json") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": accept,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        accept: str = "application/json",
    ) -> httpx.Response:
        self._ensure_configured()
        url = f"{self._base}/integration/v1{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    headers=self._headers(accept),
                )
        except httpx.TimeoutException as exc:
            raise GisError(503, "GIS_UNAVAILABLE", "GIS request timed out") from exc
        except httpx.HTTPError as exc:
            raise GisError(503, "GIS_UNAVAILABLE", f"GIS network error: {exc}") from exc

        if response.status_code == 401:
            raise GisError(502, "GIS_AUTH_FAILED", "GIS rejected the token", upstream_status=401)

        if response.status_code in (400, 404, 409, 422):
            payload = self._error_payload(response)
            raise GisError(
                response.status_code,
                payload.get("code") or "GIS_CLIENT_ERROR",
                payload.get("error") or response.text or "GIS client error",
                upstream_status=response.status_code,
            )

        if response.status_code == 429 or response.status_code >= 500:
            raise GisError(
                503,
                "GIS_UNAVAILABLE",
                "GIS temporarily unavailable",
                upstream_status=response.status_code,
            )

        if response.status_code >= 400:
            raise GisError(
                502,
                "GIS_HTTP_ERROR",
                f"Unexpected GIS HTTP status {response.status_code}",
                upstream_status=response.status_code,
            )

        return response

    @staticmethod
    def _error_payload(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    async def list_maps(self) -> list[dict[str, Any]]:
        response = await self._request("GET", "/maps")
        data = response.json()
        if not isinstance(data, dict) or "rows" not in data:
            raise GisError(502, "GIS_RESPONSE_INVALID", "Invalid /maps response")
        return data["rows"]

    async def list_layers(self, map_id: str) -> list[dict[str, Any]]:
        response = await self._request("GET", f"/maps/{map_id}/layers")
        data = response.json()
        if not isinstance(data, dict) or "rows" not in data:
            raise GisError(502, "GIS_RESPONSE_INVALID", "Invalid /layers response")
        return data["rows"]

    async def get_bounds(self, map_id: str) -> dict[str, float]:
        response = await self._request("GET", f"/maps/{map_id}/bounds")
        data = response.json()
        if not isinstance(data, dict):
            raise GisError(502, "GIS_RESPONSE_INVALID", "Invalid /bounds response")
        return data

    async def get_raw_features(
        self,
        map_id: str,
        bbox: tuple[float, float, float, float],
        layers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Raw GIS FeatureCollection without mobile thinning/simplify."""
        west, south, east, north = bbox
        params: dict[str, Any] = {"bbox": f"{west},{south},{east},{north}"}
        if layers:
            params["layers"] = ",".join(layers)
        response = await self._request("GET", f"/maps/{map_id}/features", params=params)
        data = response.json()
        if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
            raise GisError(502, "GIS_RESPONSE_INVALID", "Invalid /features response")
        return data

    async def get_features(
        self,
        map_id: str,
        bbox: tuple[float, float, float, float],
        layers: list[str] | None,
        zoom: int,
    ) -> dict[str, Any]:
        from app.data.processing import process_feature_collection

        west, south, east, north = bbox
        params: dict[str, Any] = {"bbox": f"{west},{south},{east},{north}"}
        if layers:
            params["layers"] = ",".join(layers)
        response = await self._request("GET", f"/maps/{map_id}/features", params=params)
        data = response.json()
        if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
            raise GisError(502, "GIS_RESPONSE_INVALID", "Invalid /features response")
        overview_icon_ids = await self._overview_icon_ids(zoom)
        result = process_feature_collection(
            data,
            zoom=zoom,
            max_point_count=self._settings.gis_max_point_count,
            point_limit_max_zoom=self._settings.gis_point_fixed_size_max_zoom,
            max_line_vertices=self._settings.gis_max_line_vertices,
            simplify_tolerance_m=self._settings.simplify_tolerance_meters(zoom),
            bbox=bbox,
            overview_icon_ids=overview_icon_ids,
        )
        if self._settings.line_render_mode == "raster":
            points = [
                f
                for f in result.get("features") or []
                if (f.get("geometry") or {}).get("type") == "Point"
            ]
            meta = dict(result.get("meta") or {})
            meta["line_count"] = 0
            meta["lines_thinned"] = False
            meta["line_render"] = "raster"
            return {
                **result,
                "features": points,
                "thinned": bool(meta.get("points_thinned")),
                "meta": meta,
            }
        meta = dict(result.get("meta") or {})
        meta["line_render"] = "vector"
        result["meta"] = meta
        return result

    async def get_feature(self, feature_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"/features/{feature_id}")
        data = response.json()
        if not isinstance(data, dict):
            raise GisError(502, "GIS_RESPONSE_INVALID", "Invalid feature response")
        return data

    async def fetch_asset_from_gis(self, asset_id: str) -> bytes:
        """Fetch PNG from GIS only (no local compose / cache short-circuit)."""
        response = await self._request(
            "GET",
            f"/assets/{asset_id}",
            accept="image/png",
        )
        content = response.content
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise GisError(502, "GIS_RESPONSE_INVALID", "Asset is not a valid PNG")
        return content

    async def get_asset(self, asset_id: str) -> bytes:
        # 1) Composed / legacy cache at ASSET_CACHE_DIR/{uuid}.png
        cache_path = self._cache_dir / f"{asset_id}.png"
        if cache_path.is_file():
            return cache_path.read_bytes()

        # 2) Compose from local mask + glyph_color when DB has them
        from app.data.icon_assets import try_compose_from_db

        composed = await try_compose_from_db(self._settings, asset_id)
        if composed is not None:
            return composed

        # 3) GIS fetch → write composed cache path (sync may later extract mask)
        content = await self.fetch_asset_from_gis(asset_id)
        cache_path.write_bytes(content)
        return content
