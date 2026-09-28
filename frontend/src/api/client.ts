import { authHeader, type BasicAuth } from "../auth";

export type AppConfig = {
  map_provider: string;
  yandex_maps_api_key: string;
  point_detail_zoom: number;
  /** Mid LOD zoom: SECOND icons appear from here; must be < point_detail_zoom. */
  point_detail_zoom_second?: number | null;
  point_icon_size: number;
  point_circle_size: number;
  point_fixed_size_max_zoom: number;
  /** When true, icons/circles always use GIS_POINT_*_SIZE (no zoom/iconScale). */
  icon_fixed?: boolean;
  /** GIS_POINT_OVERVIEW_ICONS; visible at every zoom (alone below mid/full detail). */
  point_overview_icons?: string[];
  /** GIS_POINT_OVERVIEW_ICONS_SECOND; visible from point_detail_zoom_second upward. */
  point_overview_icons_second?: string[];
  max_point_count: number;
  max_line_vertices: number;
  initial_radius_km: number;
  data_source?: string;
  line_render?: "vector" | "raster";
  tile_z_min?: number;
  tile_z_max?: number;
  line_tile_poles_layer_name?: string;
};

/** Mirror backend Settings.point_overview_allowlist / points_interactive_at_zoom. */
function pointOverviewAllowlist(
  config: AppConfig,
  zoom: number,
): string[] | null {
  const zDetail = config.point_detail_zoom;
  if (zoom >= zDetail) return null;

  const z2 = config.point_detail_zoom_second;
  const first = config.point_overview_icons ?? [];
  const second = config.point_overview_icons_second ?? [];
  const secondActive =
    second.length > 0 && z2 != null && Number.isFinite(z2) && z2 < zDetail;

  if (secondActive && zoom >= z2!) {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const name of [...first, ...second]) {
      if (!seen.has(name)) {
        seen.add(name);
        out.push(name);
      }
    }
    return out.length ? out : null;
  }
  return first.length > 0 ? first : null;
}

export function pointsInteractiveAtZoom(config: AppConfig, zoom: number): boolean {
  if (pointOverviewAllowlist(config, zoom) != null) return true;
  return zoom >= config.point_detail_zoom;
}

export type MapRow = {
  id: string;
  name: string;
  created_at: string;
  lines_tile_version?: string | null;
};

export type TilesMeta = {
  map_id: string;
  lines_tile_version: string | null;
  layers: string[];
  z_min: number;
  z_max: number;
};

export type LayerRow = {
  id: string;
  name: string;
  position: number;
  count: number;
  version: number;
};

export type Bounds = {
  xmin: number;
  ymin: number;
  xmax: number;
  ymax: number;
};

export type FeatureStyle = {
  iconId?: string | null;
  iconColor?: string;
  iconScale?: number;
  markerShape?: "pin" | "circle";
  recolorIcon?: boolean;
  lineColor?: string;
  lineWidth?: number;
  lineOpacity?: number;
};

export type MapFeature = {
  type: "Feature";
  id: string;
  geometry: {
    type: "Point" | "LineString";
    coordinates: number[] | number[][];
  };
  properties: {
    id: string;
    layer_id: string;
    kind: string;
    number?: number;
    title?: string;
    style?: FeatureStyle;
    classes?: string[];
  };
};

export type FeaturesResponse = {
  type: "FeatureCollection";
  features: MapFeature[];
  truncated: boolean;
  thinned: boolean;
  meta?: Record<string, unknown>;
};

export type FeatureDetail = {
  id: string;
  title?: string;
  description?: string;
  layer_name?: string;
  number?: number;
  kind?: string;
  geometry?: unknown;
  style?: FeatureStyle;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(
  auth: BasicAuth,
  path: string,
  init: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", authHeader(auth));
  if (!headers.has("Accept")) headers.set("Accept", "application/json");

  const response = await fetch(path, { ...init, headers, signal });
  if (!response.ok) {
    let code = "HTTP_ERROR";
    let message = response.statusText;
    try {
      const body = await response.json();
      const detail = body.detail ?? body;
      if (typeof detail === "object" && detail) {
        code = detail.code ?? code;
        message = detail.error ?? detail.message ?? message;
      }
    } catch {
      /* ignore */
    }
    throw new ApiError(response.status, code, message);
  }

  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return (await response.json()) as T;
  }
  return (await response.arrayBuffer()) as T;
}

export function createApi(auth: BasicAuth) {
  return {
    getConfig: (signal?: AbortSignal) =>
      request<AppConfig>(auth, "/api/config", {}, signal),
    listMaps: (signal?: AbortSignal) =>
      request<{ rows: MapRow[] }>(auth, "/api/maps", {}, signal),
    listLayers: (mapId: string, signal?: AbortSignal) =>
      request<{ rows: LayerRow[] }>(auth, `/api/maps/${mapId}/layers`, {}, signal),
    getBounds: (mapId: string, signal?: AbortSignal) =>
      request<Bounds>(auth, `/api/maps/${mapId}/bounds`, {}, signal),
    getFeatures: (
      mapId: string,
      bbox: string,
      zoom: number,
      layers: string[] | null,
      signal?: AbortSignal,
    ) => {
      const params = new URLSearchParams({ bbox, zoom: String(zoom) });
      if (layers && layers.length) params.set("layers", layers.join(","));
      return request<FeaturesResponse>(
        auth,
        `/api/maps/${mapId}/features?${params}`,
        {},
        signal,
      );
    },
    getFeature: (featureId: string, signal?: AbortSignal) =>
      request<FeatureDetail>(auth, `/api/features/${featureId}`, {}, signal),
    getTilesMeta: (mapId: string, signal?: AbortSignal) =>
      request<TilesMeta>(auth, `/api/maps/${mapId}/tiles/meta`, {}, signal),
    assetUrl: (assetId: string) => `/api/assets/${assetId}`,
  };
}

export type Api = ReturnType<typeof createApi>;
