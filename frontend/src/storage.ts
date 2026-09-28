const MAP_KEY = "mobilemap.selectedMapId";
const LAYERS_PREFIX = "mobilemap.layers.";
const VIEWPORT_PREFIX = "mobilemap.viewport.";
const LINE_TILES_PREFIX = "mobilemap.lineTiles.";

export type StoredViewport = {
  bbox: [number, number, number, number]; // west,south,east,north
  zoom: number;
};

export type StoredLineTiles = {
  network: boolean;
  poles: boolean;
};

export function loadMapId(): string | null {
  return localStorage.getItem(MAP_KEY);
}

export function saveMapId(mapId: string): void {
  localStorage.setItem(MAP_KEY, mapId);
}

export function loadLayerIds(mapId: string): string[] | null {
  const raw = localStorage.getItem(LAYERS_PREFIX + mapId);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as string[];
    return Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function saveLayerIds(mapId: string, layerIds: string[]): void {
  localStorage.setItem(LAYERS_PREFIX + mapId, JSON.stringify(layerIds));
}

export function loadLineTiles(mapId: string): StoredLineTiles {
  const raw = localStorage.getItem(LINE_TILES_PREFIX + mapId);
  if (!raw) return { network: true, poles: false };
  try {
    const parsed = JSON.parse(raw) as Partial<StoredLineTiles>;
    return {
      network: parsed.network !== false,
      poles: parsed.poles === true,
    };
  } catch {
    return { network: true, poles: false };
  }
}

export function saveLineTiles(mapId: string, value: StoredLineTiles): void {
  localStorage.setItem(LINE_TILES_PREFIX + mapId, JSON.stringify(value));
}

export function loadViewport(mapId: string): StoredViewport | null {
  const raw = localStorage.getItem(VIEWPORT_PREFIX + mapId);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as StoredViewport;
    if (
      !Array.isArray(parsed.bbox) ||
      parsed.bbox.length !== 4 ||
      typeof parsed.zoom !== "number"
    ) {
      return null;
    }
    const [west, south, east, north] = parsed.bbox;
    if (!(west < east && south < north)) return null;
    return { bbox: [west, south, east, north], zoom: parsed.zoom };
  } catch {
    return null;
  }
}

export function saveViewport(mapId: string, viewport: StoredViewport): void {
  localStorage.setItem(VIEWPORT_PREFIX + mapId, JSON.stringify(viewport));
}
