from functools import lru_cache
import re

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


LineClassMode = str  # "backbone" | "all"


def parse_simplify_table(raw: str) -> list[tuple[int, float]]:
    """Parse 'zoom:meters,...' into sorted (zoom, meters) pairs."""
    pairs: list[tuple[int, float]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        zoom_s, meters_s = part.split(":", 1)
        pairs.append((int(zoom_s.strip()), float(meters_s.strip())))
    pairs.sort(key=lambda p: p[0])
    return pairs


def parse_line_classes_table(raw: str) -> list[tuple[int, LineClassMode]]:
    """Parse 'zoom:backbone|all,...' into sorted (zoom, mode) pairs."""
    pairs: list[tuple[int, LineClassMode]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        zoom_s, mode_s = part.split(":", 1)
        mode = mode_s.strip().lower()
        if mode not in ("backbone", "all"):
            raise ValueError(
                f"Invalid GIS_LINE_CLASSES_BY_ZOOM mode '{mode_s.strip()}'; "
                "expected backbone|all"
            )
        pairs.append((int(zoom_s.strip()), mode))
    pairs.sort(key=lambda p: p[0])
    return pairs


def parse_icon_name_list(raw: str) -> list[str]:
    """Parse comma-separated icon names; normalize like sync naming."""
    out: list[str] = []
    seen: set[str] = set()
    for part in (raw or "").split(","):
        name = part.strip().lower().replace(" ", "_").replace(".", "")
        name = re.sub(r"[^a-z0-9_]+", "_", name).strip("_")
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    map_provider: str = Field(default="yandex21", alias="MAP_PROVIDER")
    yandex_maps_api_key: str = Field(default="", alias="YANDEX_MAPS_API_KEY")

    auth_username: str = Field(default="", alias="AUTH_USERNAME")
    auth_password: str = Field(default="", alias="AUTH_PASSWORD")

    gis_base_url: str = Field(default="", alias="GIS_BASE_URL")
    gis_api_token: str = Field(default="", alias="GIS_API_TOKEN")
    gis_timeout_seconds: float = Field(default=20.0, alias="GIS_TIMEOUT_SECONDS")

    gis_point_detail_zoom: int = Field(default=15, alias="GIS_POINT_DETAIL_ZOOM")
    gis_point_icon_size: int = Field(default=32, alias="GIS_POINT_ICON_SIZE")
    gis_point_circle_size: int = Field(default=22, alias="GIS_POINT_CIRCLE_SIZE")
    gis_point_fixed_size_max_zoom: int = Field(
        default=15, alias="GIS_POINT_FIXED_SIZE_MAX_ZOOM"
    )
    # When true: always GIS_POINT_ICON_SIZE / CIRCLE_SIZE (ignore zoom + iconScale)
    gis_point_icon_fixed: bool = Field(default=False, alias="GIS_POINT_ICON_FIXED")
    gis_max_point_count: int = Field(default=300, alias="GIS_MAX_POINT_COUNT")
    # Comma-separated icon_name allowlist for overview (zoom < GIS_POINT_DETAIL_ZOOM).
    # Empty = all icons. Requires icon_uuid rows after sync/icons.
    gis_point_overview_icons: str = Field(default="", alias="GIS_POINT_OVERVIEW_ICONS")

    gis_max_line_vertices: int = Field(default=5000, alias="GIS_MAX_LINE_VERTICES")
    gis_line_simplify_meters: str = Field(
        default="0:50,12:20,14:8,16:2,18:0",
        alias="GIS_LINE_SIMPLIFY_METERS",
    )
    # LOD for line classes (PostGIS): below threshold — backbone only; then all.
    # Zoom breakpoints align with simplify table (12 overview → 13+ detail).
    gis_line_classes_by_zoom: str = Field(
        default="0:backbone,13:all",
        alias="GIS_LINE_CLASSES_BY_ZOOM",
    )
    gis_initial_radius_km: float = Field(default=20.0, alias="GIS_INITIAL_RADIUS_KM")

    line_render: str = Field(default="vector", alias="LINE_RENDER")
    tile_cache_dir: str = Field(
        default="/var/lib/mobilemap/tiles", alias="TILE_CACHE_DIR"
    )
    line_tile_poles_layer_name: str = Field(
        default="Линии столбов", alias="LINE_TILE_POLES_LAYER_NAME"
    )
    tile_z_min: int = Field(default=10, alias="TILE_Z_MIN")
    tile_z_max: int = Field(default=15, alias="TILE_Z_MAX")

    data_source: str = Field(default="gis", alias="DATA_SOURCE")
    database_url: str = Field(default="", alias="DATABASE_URL")
    sync_token: str = Field(default="", alias="SYNC_TOKEN")

    asset_cache_dir: str = Field(default="/tmp/mobilemap-assets", alias="ASSET_CACHE_DIR")

    # Vision naming for icons (same names as get_iconname.py)
    open_api_key: str = Field(default="", alias="OPEN_API_KEY")
    open_api_base: str = Field(default="", alias="OPEN_API_BASE")
    model_name: str = Field(default="", alias="MODEL_NAME")
    sync_icons: bool = Field(default=True, alias="SYNC_ICONS")
    line_class_tolerance_m: float = Field(default=20.0, alias="LINE_CLASS_TOLERANCE_M")

    @property
    def simplify_table(self) -> list[tuple[int, float]]:
        return parse_simplify_table(self.gis_line_simplify_meters)

    @property
    def point_overview_icons(self) -> list[str]:
        return parse_icon_name_list(self.gis_point_overview_icons)

    def overview_icon_filter_active(self, zoom: int) -> bool:
        """True when overview icon allowlist should restrict points."""
        return bool(self.point_overview_icons) and zoom < self.gis_point_detail_zoom

    @property
    def line_classes_table(self) -> list[tuple[int, LineClassMode]]:
        return parse_line_classes_table(self.gis_line_classes_by_zoom)

    @property
    def line_render_mode(self) -> str:
        mode = (self.line_render or "vector").strip().lower()
        return mode if mode in ("vector", "raster") else "vector"

    def simplify_tolerance_meters(self, zoom: int) -> float:
        table = self.simplify_table
        if not table:
            return 0.0
        chosen = table[0][1]
        for z, meters in table:
            if z <= zoom:
                chosen = meters
            else:
                break
        return chosen

    def line_class_mode(self, zoom: int) -> LineClassMode:
        """Which line classes to return at zoom. Default all if table empty."""
        table = self.line_classes_table
        if not table:
            return "all"
        chosen: LineClassMode = table[0][1]
        for z, mode in table:
            if z <= zoom:
                chosen = mode
            else:
                break
        return chosen


@lru_cache
def get_settings() -> Settings:
    return Settings()
