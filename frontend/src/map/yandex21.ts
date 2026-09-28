import type { AppConfig, FeatureStyle, FeaturesResponse, MapFeature } from "../api/client";
import { resolveIconUrlAtSize } from "./icons";
import { isBackboneLine, resolveLineStroke, sortLinesForDraw } from "./lineStyle";
import { pointPixelSize, type PixelSize } from "./pointSize";
import type { LineTilesConfig, MapProvider, Viewport } from "./types";

export type { MapProvider, Viewport } from "./types";

declare global {
  interface Window {
    ymaps: any;
  }
}

function loadYmaps(apiKey: string): Promise<void> {
  if (window.ymaps) return window.ymaps.ready();
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `https://api-maps.yandex.ru/2.1/?apikey=${encodeURIComponent(apiKey)}&lang=ru_RU`;
    script.async = true;
    script.onload = () => {
      window.ymaps.ready(() => resolve());
    };
    script.onerror = () => reject(new Error("Failed to load Yandex Maps API"));
    document.head.appendChild(script);
  });
}

function toYandexCoords(lonLat: number[]): [number, number] {
  return [lonLat[1], lonLat[0]];
}

function circleIconHref(color: string, size: number): string {
  const radius = size / 2;
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}">` +
    `<circle cx="${radius}" cy="${radius}" r="${Math.max(0.5, radius - 1)}" fill="${color}" stroke="#ffffff" stroke-width="2"/>` +
    `</svg>`;
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

function sizeFallback(config: AppConfig): number {
  const n = Number(config.point_icon_size);
  return Number.isFinite(n) && n > 0 ? Math.round(n) : 32;
}

function applyPointIcon(
  options: Record<string, unknown>,
  style: FeatureStyle,
  iconHref: string | null,
  size: PixelSize,
): void {
  const w = Math.max(1, Math.round(size.width));
  const h = Math.max(1, Math.round(size.height));
  options.iconLayout = "default#image";
  options.iconImageSize = [w, h];
  options.iconImageOffset = [-w / 2, -h / 2];
  if (iconHref) {
    options.iconImageHref = iconHref;
    return;
  }
  const diameter = Math.min(w, h);
  options.iconImageHref = circleIconHref(style.iconColor ?? "#3388ff", diameter);
  options.iconImageSize = [diameter, diameter];
  options.iconImageOffset = [-diameter / 2, -diameter / 2];
}

type PointRenderState = {
  size: number;
  interactive: boolean;
  iconId: string | null;
  iconColor: string;
};

function featureToOmObject(feature: MapFeature, interactive: boolean) {
  const style = feature.properties.style ?? {};
  if (feature.geometry.type === "Point") {
    const coords = toYandexCoords(feature.geometry.coordinates as number[]);
    const options: Record<string, unknown> = {
      hasBalloon: false,
      hasHint: false,
      openBalloonOnClick: false,
      cursor: interactive ? "pointer" : "default",
      interactiveZIndex: interactive,
    };

    return {
      type: "Feature",
      id: feature.id,
      geometry: { type: "Point", coordinates: coords },
      properties: {
        featureId: feature.id,
        title: feature.properties.title ?? "",
        mmStyle: {
          iconScale: style.iconScale,
          iconColor: style.iconColor,
          iconId: style.iconId ?? null,
        },
        mmHasIcon: Boolean(style.iconId),
      },
      options,
      _style: style,
    };
  }

  const line = (feature.geometry.coordinates as number[][]).map(toYandexCoords);
  const stroke = resolveLineStroke(style, isBackboneLine(feature));
  return {
    type: "Feature",
    id: feature.id,
    geometry: { type: "LineString", coordinates: line },
    properties: {
      featureId: feature.id,
      title: feature.properties.title ?? "",
    },
    options: {
      strokeColor: stroke.color,
      strokeWidth: stroke.width,
      strokeOpacity: stroke.opacity,
      zIndex: isBackboneLine(feature) ? 100 : 10,
      hasBalloon: false,
      hasHint: false,
      cursor: "default",
    },
  };
}

function lineTileTemplate(mapId: string, layer: string, version: string, retina: boolean): string {
  const suffix = retina ? "@2x.webp" : ".webp";
  return `/api/maps/${mapId}/tiles/lines/${layer}/${version}/%z/%x/%y${suffix}`;
}

export function createYandex21Provider(): MapProvider {
  let map: any = null;
  let pointsOm: any = null;
  let linesOm: any = null;
  let networkLayer: any = null;
  let polesLayer: any = null;
  let debounceTimer: number | null = null;
  let viewportHandler: ((viewport: Viewport) => void) | null = null;
  let activeGeneration = 0;
  let clickHandlerAttached = false;
  let pointClick: ((id: string) => void) | null = null;
  let interactive = false;
  let appConfig: AppConfig | null = null;
  let lineTiles: LineTilesConfig | null = null;
  /** Points currently in ObjectManager — source of truth for incremental diff. */
  const renderedPoints = new Map<string, PointRenderState>();

  const getViewport = (): Viewport => {
    const b = map.getBounds();
    const zoom = map.getZoom();
    const south = b[0][0];
    const west = b[0][1];
    const north = b[1][0];
    const east = b[1][1];
    return { bbox: [west, south, east, north], zoom: Math.round(zoom) };
  };

  const removeRasterLayers = () => {
    if (!map) return;
    if (networkLayer) {
      map.layers.remove(networkLayer);
      networkLayer = null;
    }
    if (polesLayer) {
      map.layers.remove(polesLayer);
      polesLayer = null;
    }
  };

  const applyRasterLayers = () => {
    if (!map || !window.ymaps) return;
    removeRasterLayers();
    if (!lineTiles) return;
    const retina = window.devicePixelRatio >= 1.5;
    const { mapId, version, networkVisible, polesVisible } = lineTiles;
    if (networkVisible) {
      networkLayer = new window.ymaps.Layer(
        lineTileTemplate(mapId, "network", version, retina),
        {
          tileTransparent: true,
          // Our tiles are mercantile/EPSG:3857; Yandex default projection mismatches → N/S shift
          projection: window.ymaps.projection.sphericalMercator,
        },
      );
      map.layers.add(networkLayer);
    }
    if (polesVisible) {
      polesLayer = new window.ymaps.Layer(
        lineTileTemplate(mapId, "poles", version, retina),
        {
          tileTransparent: true,
          projection: window.ymaps.projection.sphericalMercator,
        },
      );
      map.layers.add(polesLayer);
    }
  };

  return {
    async init(container, config: AppConfig) {
      appConfig = config;
      await loadYmaps(config.yandex_maps_api_key);
      map = new window.ymaps.Map(
        container,
        {
          center: [55.75, 37.62],
          zoom: 11,
          controls: ["zoomControl", "geolocationControl"],
        },
        { yandexMapDisablePoiInteractivity: true },
      );

      pointsOm = new window.ymaps.ObjectManager({
        clusterize: false,
        // syncOverlayInit:true throws "e is not a constructor" in ymaps 2.1.79
        // (_createOverlay) when OM is on the map — icons never appear.
        syncOverlayInit: false,
      });
      linesOm = new window.ymaps.ObjectManager({
        clusterize: false,
        syncOverlayInit: false,
      });
      map.geoObjects.add(linesOm);
      map.geoObjects.add(pointsOm);

      map.events.add("actionend", () => {
        if (!viewportHandler) return;
        if (debounceTimer) window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(() => {
          viewportHandler?.(getViewport());
        }, 280);
      });

      // Keep marker pixel size stable across zoom (re-assert after camera settles).
      map.events.add("boundschange", (e: any) => {
        try {
          const oldZoom = e.get("oldZoom");
          const newZoom = e.get("newZoom");
          const cfg = appConfig;
          if (oldZoom === newZoom || !pointsOm || !cfg) return;
          const zoom = Math.round(Number(newZoom));
          pointsOm.objects.each((obj: any) => {
            const style = (obj?.properties?.mmStyle ?? {}) as FeatureStyle;
            const hasIcon = Boolean(obj?.properties?.mmHasIcon);
            const size = pointPixelSize(
              zoom,
              cfg,
              style,
              hasIcon ? { width: 1, height: 1 } : null,
            );
            const href = pointsOm.objects.getById(obj.id)?.options?.iconImageHref;
            pointsOm.objects.setObjectOptions(String(obj.id), {
              iconLayout: "default#image",
              ...(href ? { iconImageHref: href } : {}),
              iconImageSize: [size.width, size.height],
              iconImageOffset: [-size.width / 2, -size.height / 2],
            });
          });
        } catch {
          /* ObjectManager may be empty mid-refresh */
        }
      });
    },

    setBounds(bounds) {
      map.setBounds(
        [
          [bounds.ymin, bounds.xmin],
          [bounds.ymax, bounds.xmax],
        ],
        { checkZoomRange: true, duration: 0 },
      );
    },

    setViewport(viewport) {
      const [west, south, east, north] = viewport.bbox;
      const centerLat = (south + north) / 2;
      const centerLon = (west + east) / 2;
      map.setCenter([centerLat, centerLon], viewport.zoom, { duration: 0 });
    },

    onViewportSettled(handler) {
      viewportHandler = handler;
    },

    getViewport,

    setLineTiles(config) {
      lineTiles = config;
      applyRasterLayers();
      if (config) {
        linesOm?.removeAll();
      }
    },

    cancelRender() {
      activeGeneration += 1;
    },

    async renderFeatures(data: FeaturesResponse, options) {
      if (!appConfig) return;
      const cfg = appConfig;
      const generation = options.generation;
      activeGeneration = generation;
      interactive = options.interactivePoints;
      pointClick = options.onPointClick ?? null;
      const zoom = getViewport().zoom;
      const rasterLines = Boolean(lineTiles);

      if (!clickHandlerAttached) {
        pointsOm.objects.events.add("click", (e: any) => {
          if (!interactive || !pointClick) return;
          const objectId = e.get("objectId");
          const obj = pointsOm.objects.getById(objectId);
          const featureId = obj?.properties?.featureId ?? objectId;
          pointClick(String(featureId));
        });
        clickHandlerAttached = true;
      }

      const prepared: any[] = [];
      const lineFeatures: MapFeature[] = [];
      for (const f of data.features) {
        if (f.geometry.type === "Point") prepared.push(featureToOmObject(f, interactive));
        else if (!rasterLines) lineFeatures.push(f);
      }
      const lines = sortLinesForDraw(lineFeatures).map((f) =>
        featureToOmObject(f, interactive),
      );

      const newById = new Map<string, any>();
      for (const p of prepared) {
        const style = (p._style ?? {}) as FeatureStyle;
        const size = pointPixelSize(
          zoom,
          cfg,
          style,
          style.iconId ? { width: 1, height: 1 } : null,
        );
        p.properties.mmSize = size.width;
        p._desired = {
          size: size.width,
          interactive,
          iconId: style.iconId ? String(style.iconId) : null,
          iconColor: style.iconColor ?? "#3388ff",
        } satisfies PointRenderState;
        newById.set(String(p.id), p);
      }

      const toRemove: string[] = [];
      for (const id of renderedPoints.keys()) {
        if (!newById.has(id)) toRemove.push(id);
      }

      const toAdd: any[] = [];
      const toPatch: any[] = [];
      for (const [id, p] of newById) {
        const prev = renderedPoints.get(id);
        const onMap = Boolean(pointsOm.objects.getById(id));
        if (!prev || !onMap) {
          toAdd.push(p);
          continue;
        }
        const desired = p._desired as PointRenderState;
        const sizeChanged = prev.size !== desired.size;
        const interactiveChanged = prev.interactive !== desired.interactive;
        const styleChanged =
          prev.iconId !== desired.iconId || prev.iconColor !== desired.iconColor;
        if (sizeChanged || interactiveChanged || styleChanged) toPatch.push(p);
        else {
          delete p._style;
          delete p._desired;
        }
      }

      const iconJobs = new Map<string, { id: string; size: number }>();
      const considerIcon = (p: any) => {
        const style = (p._style ?? {}) as FeatureStyle;
        const iconId = style.iconId ? String(style.iconId) : null;
        if (!iconId) return;
        const size = Number(p.properties.mmSize) || sizeFallback(cfg);
        iconJobs.set(`${iconId}:${size}`, { id: iconId, size });
      };
      for (const p of toAdd) considerIcon(p);
      for (const p of toPatch) {
        const prev = renderedPoints.get(String(p.id));
        const desired = p._desired as PointRenderState;
        if (!prev || prev.size !== desired.size || prev.iconId !== desired.iconId) {
          considerIcon(p);
        }
      }

      const hrefByKey = new Map<string, string>();
      await Promise.all(
        [...iconJobs.values()].map(async ({ id, size }) => {
          try {
            const href = await resolveIconUrlAtSize(id, options.authHeader, size);
            hrefByKey.set(`${id}:${size}`, href);
          } catch {
            /* circle fallback */
          }
        }),
      );
      if (generation !== activeGeneration) return;

      const finishAdd = (p: any) => {
        const style = (p._style ?? {}) as FeatureStyle;
        const sizePx = Number(p.properties.mmSize) || sizeFallback(cfg);
        const size: PixelSize = { width: sizePx, height: sizePx };
        const href = style.iconId
          ? hrefByKey.get(`${style.iconId}:${sizePx}`) ?? null
          : null;
        applyPointIcon(p.options, style, href, size);
        delete p._style;
      };
      const finishPatch = (p: any) => {
        const style = (p._style ?? {}) as FeatureStyle;
        const sizePx = Number(p.properties.mmSize) || sizeFallback(cfg);
        const size: PixelSize = { width: sizePx, height: sizePx };
        const id = String(p.id);
        let href: string | null = null;
        if (style.iconId) {
          href =
            hrefByKey.get(`${style.iconId}:${sizePx}`) ??
            pointsOm.objects.getById(id)?.options?.iconImageHref ??
            null;
        }
        applyPointIcon(p.options, style, href, size);
        delete p._style;
      };
      for (const p of toAdd) finishAdd(p);
      for (const p of toPatch) finishPatch(p);

      if (toRemove.length) {
        pointsOm.remove(toRemove);
        for (const id of toRemove) renderedPoints.delete(id);
      }

      for (const p of toPatch) {
        if (generation !== activeGeneration) return;
        const id = String(p.id);
        const desired = p._desired as PointRenderState;
        const w = desired.size;
        pointsOm.objects.setObjectOptions(id, {
          cursor: desired.interactive ? "pointer" : "default",
          interactiveZIndex: desired.interactive,
          iconLayout: "default#image",
          iconImageHref: p.options.iconImageHref,
          iconImageSize: [w, w],
          iconImageOffset: [-w / 2, -w / 2],
        });
        pointsOm.objects.setObjectProperties(id, {
          mmSize: w,
          mmHasIcon: Boolean(desired.iconId),
          mmStyle: {
            iconScale: (p.properties.mmStyle as FeatureStyle | undefined)?.iconScale,
            iconColor: desired.iconColor,
            iconId: desired.iconId,
          },
        });
        renderedPoints.set(id, desired);
        delete p._desired;
      }

      const chunkSize = 80;
      for (let i = 0; i < toAdd.length; i += chunkSize) {
        if (generation !== activeGeneration) return;
        const slice = toAdd.slice(i, i + chunkSize);
        pointsOm.add(slice);
        for (const p of slice) {
          const id = String(p.id);
          const desired = p._desired as PointRenderState;
          const w = desired.size;
          pointsOm.objects.setObjectOptions(id, {
            iconLayout: "default#image",
            iconImageHref: p.options.iconImageHref,
            iconImageSize: [w, w],
            iconImageOffset: [-w / 2, -w / 2],
          });
          renderedPoints.set(id, desired);
          delete p._desired;
        }
        await new Promise<void>((r) => requestAnimationFrame(() => r()));
      }

      // Vector lines: still full replace (cheaper than point churn; not in raster mode).
      linesOm.removeAll();
      if (!rasterLines && lines.length) {
        for (let i = 0; i < lines.length; i += chunkSize) {
          if (generation !== activeGeneration) return;
          linesOm.add(lines.slice(i, i + chunkSize));
          await new Promise<void>((r) => requestAnimationFrame(() => r()));
        }
      }
    },

    destroy() {
      if (debounceTimer) window.clearTimeout(debounceTimer);
      renderedPoints.clear();
      removeRasterLayers();
      map?.destroy();
      map = null;
    },
  };
}
