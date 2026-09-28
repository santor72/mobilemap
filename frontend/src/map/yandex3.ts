import type { AppConfig, FeaturesResponse, MapFeature } from "../api/client";
import { resolveIcon, type IconAsset } from "./icons";
import { isBackboneLine, resolveLineStroke, sortLinesForDraw } from "./lineStyle";
import { pointPixelSize, type PixelSize } from "./pointSize";
import type { MapProvider, Viewport } from "./types";

declare global {
  interface Window {
    ymaps3: any;
  }
}

function loadYmaps3(apiKey: string): Promise<any> {
  const existing = window.ymaps3;
  if (existing) return existing.ready.then(() => existing);

  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `https://api-maps.yandex.ru/v3/?apikey=${encodeURIComponent(apiKey)}&lang=ru_RU`;
    script.async = true;
    script.onload = () => {
      window.ymaps3.ready
        .then(() => resolve(window.ymaps3))
        .catch(reject);
    };
    script.onerror = () => reject(new Error("Failed to load Yandex Maps API v3"));
    document.head.appendChild(script);
  });
}

function hexToRgba(color: string, opacity = 1): string {
  if (color.startsWith("rgb")) return color;
  const hex = color.replace("#", "");
  const full =
    hex.length === 3
      ? hex
          .split("")
          .map((c) => c + c)
          .join("")
      : hex;
  if (full.length !== 6) return color;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${opacity})`;
}

function createMarkerElement(
  feature: MapFeature,
  iconUrl: string | null,
  size: PixelSize,
  interactive: boolean,
): HTMLElement {
  const style = feature.properties.style ?? {};
  const el = document.createElement("div");
  el.className = "mm-marker" + (interactive ? " mm-marker--interactive" : "");
  el.dataset.featureId = String(feature.id);
  el.style.width = `${size.width}px`;
  el.style.height = `${size.height}px`;

  if (iconUrl) {
    const img = document.createElement("img");
    img.src = iconUrl;
    img.width = size.width;
    img.height = size.height;
    img.alt = feature.properties.title ?? "";
    img.draggable = false;
    el.append(img);
  } else {
    el.classList.add("mm-marker--circle");
    el.style.background = style.iconColor ?? "#3388ff";
  }
  return el;
}

function patchMarkerElement(
  el: HTMLElement,
  feature: MapFeature,
  iconUrl: string | null,
  size: PixelSize,
  interactive: boolean,
): void {
  const style = feature.properties.style ?? {};
  el.className = "mm-marker" + (interactive ? " mm-marker--interactive" : "");
  el.style.width = `${size.width}px`;
  el.style.height = `${size.height}px`;
  let img = el.querySelector("img");
  if (iconUrl) {
    el.classList.remove("mm-marker--circle");
    el.style.background = "";
    if (!img) {
      img = document.createElement("img");
      img.alt = feature.properties.title ?? "";
      img.draggable = false;
      el.append(img);
    }
    img.src = iconUrl;
    img.width = size.width;
    img.height = size.height;
  } else {
    img?.remove();
    el.classList.add("mm-marker--circle");
    el.style.background = style.iconColor ?? "#3388ff";
  }
}

type PointEntry = {
  marker: any;
  element: HTMLElement;
  size: number;
  interactive: boolean;
  iconId: string | null;
  iconColor: string;
  iconUrl: string | null;
};

export function createYandex3Provider(): MapProvider {
  let map: any = null;
  let ymaps3: any = null;
  let linesCollection: any = null;
  let pointsCollection: any = null;
  let debounceTimer: number | null = null;
  let viewportHandler: ((viewport: Viewport) => void) | null = null;
  let activeGeneration = 0;
  let appConfig: AppConfig | null = null;
  let pointClick: ((id: string) => void) | null = null;
  let interactive = false;
  const renderedPoints = new Map<string, PointEntry>();

  const getViewport = (): Viewport => {
    const bounds = map.bounds as [[number, number], [number, number]];
    const [[lon1, lat1], [lon2, lat2]] = bounds;
    // v3 may return corners not ordered as SW/NE — normalize to west,south,east,north
    const west = Math.min(lon1, lon2);
    const east = Math.max(lon1, lon2);
    const south = Math.min(lat1, lat2);
    const north = Math.max(lat1, lat2);
    return {
      bbox: [west, south, east, north],
      zoom: Math.round(map.zoom),
    };
  };

  const scheduleViewport = () => {
    if (!viewportHandler) return;
    if (debounceTimer) window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(() => {
      viewportHandler?.(getViewport());
    }, 280);
  };

  const clearCollection = (collection: any) => {
    if (!collection || !map) return;
    map.removeChild(collection);
  };

  return {
    async init(container, config) {
      appConfig = config;
      ymaps3 = await loadYmaps3(config.yandex_maps_api_key);
      const {
        YMap,
        YMapDefaultSchemeLayer,
        YMapDefaultFeaturesLayer,
        YMapCollection,
        YMapControls,
        YMapListener,
      } = ymaps3;

      let YMapZoomControl: any;
      try {
        ({ YMapZoomControl } = await ymaps3.import("@yandex/ymaps3-controls@0.0.1"));
      } catch {
        try {
          ({ YMapZoomControl } = await ymaps3.import("@yandex/ymaps3-controls"));
        } catch {
          YMapZoomControl = null;
        }
      }

      map = new YMap(container, {
        location: { center: [37.62, 55.75], zoom: 11 },
      });

      map.addChild(new YMapDefaultSchemeLayer({}));
      map.addChild(new YMapDefaultFeaturesLayer({}));

      linesCollection = new YMapCollection({});
      pointsCollection = new YMapCollection({});
      map.addChild(linesCollection);
      map.addChild(pointsCollection);

      if (YMapZoomControl) {
        map.addChild(
          new YMapControls({ position: "right" }).addChild(new YMapZoomControl({})),
        );
      }

      map.addChild(
        new YMapListener({
          onActionEnd: () => scheduleViewport(),
        }),
      );
    },

    setBounds(bounds) {
      map.setLocation({
        bounds: [
          [bounds.xmin, bounds.ymin],
          [bounds.xmax, bounds.ymax],
        ],
        duration: 0,
      });
    },

    setViewport(viewport) {
      const [west, south, east, north] = viewport.bbox;
      map.setLocation({
        center: [(west + east) / 2, (south + north) / 2],
        zoom: viewport.zoom,
        duration: 0,
      });
    },

    onViewportSettled(handler) {
      viewportHandler = handler;
    },

    getViewport,

    setLineTiles(_config) {
      /* raster line tiles: yandex21 only in MVP */
    },

    cancelRender() {
      activeGeneration += 1;
    },

    async renderFeatures(data: FeaturesResponse, options) {
      if (!ymaps3 || !map || !appConfig || !pointsCollection) return;
      const generation = options.generation;
      activeGeneration = generation;
      interactive = options.interactivePoints;
      pointClick = options.onPointClick ?? null;

      const { YMapCollection, YMapFeature, YMapMarker } = ymaps3;
      const zoom = getViewport().zoom;
      const cfg = appConfig;

      const points = data.features.filter((f) => f.geometry.type === "Point");
      const lines = sortLinesForDraw(
        data.features.filter((f) => f.geometry.type === "LineString"),
      );

      type Desired = {
        feature: MapFeature;
        size: PixelSize;
        iconId: string | null;
        iconColor: string;
      };
      const newById = new Map<string, Desired>();
      for (const feature of points) {
        const style = feature.properties.style ?? {};
        const iconId = style.iconId ? String(style.iconId) : null;
        const size = pointPixelSize(
          zoom,
          cfg,
          style,
          iconId ? { width: 1, height: 1 } : null,
        );
        newById.set(String(feature.id), {
          feature,
          size,
          iconId,
          iconColor: style.iconColor ?? "#3388ff",
        });
      }

      const toRemove: string[] = [];
      for (const id of renderedPoints.keys()) {
        if (!newById.has(id)) toRemove.push(id);
      }
      const toAdd: Desired[] = [];
      const toPatch: Desired[] = [];
      for (const [id, desired] of newById) {
        const prev = renderedPoints.get(id);
        if (!prev) {
          toAdd.push(desired);
          continue;
        }
        if (
          prev.size !== desired.size.width ||
          prev.interactive !== interactive ||
          prev.iconId !== desired.iconId ||
          prev.iconColor !== desired.iconColor
        ) {
          toPatch.push(desired);
        }
      }

      const iconIds = new Set<string>();
      for (const d of toAdd) if (d.iconId) iconIds.add(d.iconId);
      for (const d of toPatch) {
        const prev = renderedPoints.get(String(d.feature.id));
        if (d.iconId && (!prev || prev.iconId !== d.iconId || !prev.iconUrl)) {
          iconIds.add(d.iconId);
        }
      }
      const iconMap = new Map<string, IconAsset>();
      await Promise.all(
        [...iconIds].map(async (id) => {
          try {
            iconMap.set(id, await resolveIcon(id, options.authHeader));
          } catch {
            /* circle fallback */
          }
        }),
      );
      if (generation !== activeGeneration) return;

      for (const id of toRemove) {
        const entry = renderedPoints.get(id);
        if (entry) {
          try {
            pointsCollection.removeChild(entry.marker);
          } catch {
            /* already gone */
          }
          renderedPoints.delete(id);
        }
      }

      for (const desired of toPatch) {
        if (generation !== activeGeneration) return;
        const id = String(desired.feature.id);
        const entry = renderedPoints.get(id);
        if (!entry) {
          toAdd.push(desired);
          continue;
        }
        const iconUrl = desired.iconId
          ? iconMap.get(desired.iconId)?.url ?? entry.iconUrl
          : null;
        patchMarkerElement(
          entry.element,
          desired.feature,
          iconUrl,
          desired.size,
          interactive,
        );
        entry.size = desired.size.width;
        entry.interactive = interactive;
        entry.iconId = desired.iconId;
        entry.iconColor = desired.iconColor;
        entry.iconUrl = iconUrl;
      }

      const chunkSize = 80;
      for (let i = 0; i < toAdd.length; i += chunkSize) {
        if (generation !== activeGeneration) return;
        const slice = toAdd.slice(i, i + chunkSize);
        for (const desired of slice) {
          const id = String(desired.feature.id);
          if (renderedPoints.has(id)) continue;
          const iconUrl = desired.iconId
            ? iconMap.get(desired.iconId)?.url ?? null
            : null;
          const element = createMarkerElement(
            desired.feature,
            iconUrl,
            desired.size,
            interactive,
          );
          element.addEventListener("click", (event) => {
            event.stopPropagation();
            if (!interactive || !pointClick) return;
            pointClick(id);
          });
          const coords = desired.feature.geometry.coordinates as number[];
          const marker = new YMapMarker({ id, coordinates: coords }, element);
          pointsCollection.addChild(marker);
          renderedPoints.set(id, {
            marker,
            element,
            size: desired.size.width,
            interactive,
            iconId: desired.iconId,
            iconColor: desired.iconColor,
            iconUrl,
          });
        }
        await new Promise<void>((r) => requestAnimationFrame(() => r()));
      }

      clearCollection(linesCollection);
      linesCollection = new YMapCollection({});
      map.addChild(linesCollection);

      for (let i = 0; i < lines.length; i += chunkSize) {
        if (generation !== activeGeneration) return;
        const slice = lines.slice(i, i + chunkSize);
        for (const feature of slice) {
          const style = feature.properties.style ?? {};
          const stroke = resolveLineStroke(style, isBackboneLine(feature));
          const color = hexToRgba(stroke.color, stroke.opacity);
          linesCollection.addChild(
            new YMapFeature({
              id: String(feature.id),
              geometry: {
                type: "LineString",
                coordinates: feature.geometry.coordinates,
              },
              style: {
                stroke: [{ width: stroke.width, color }],
              },
            }),
          );
        }
        await new Promise<void>((r) => requestAnimationFrame(() => r()));
      }
    },

    destroy() {
      if (debounceTimer) window.clearTimeout(debounceTimer);
      renderedPoints.clear();
      map?.destroy?.();
      map = null;
    },
  };
}
