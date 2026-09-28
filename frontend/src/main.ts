import "./style.css";
import { clearAuth, loadAuth, saveAuth, authHeader, type BasicAuth } from "./auth";
import {
  createApi,
  ApiError,
  pointsInteractiveAtZoom,
  type Api,
  type AppConfig,
  type LayerRow,
} from "./api/client";
import { createMapProvider, type MapProvider, type Viewport } from "./map";
import {
  loadLayerIds,
  loadLineTiles,
  loadMapId,
  loadViewport,
  saveLayerIds,
  saveLineTiles,
  saveMapId,
  saveViewport,
  type StoredLineTiles,
} from "./storage";

const loginEl = document.querySelector<HTMLDivElement>("#login")!;
const shellEl = document.querySelector<HTMLDivElement>("#shell")!;
const loginForm = document.querySelector<HTMLFormElement>("#login-form")!;
const loginError = document.querySelector<HTMLParagraphElement>("#login-error")!;
const mapSelectWrap = document.querySelector<HTMLDivElement>("#map-select-wrap")!;
const mapSelect = document.querySelector<HTMLSelectElement>("#map-select")!;
const layersList = document.querySelector<HTMLDivElement>("#layers-list")!;
const lineTilesList = document.querySelector<HTMLDivElement>("#line-tiles-list")!;
const statusEl = document.querySelector<HTMLDivElement>("#status")!;
const cardEl = document.querySelector<HTMLElement>("#card")!;
const mapContainer = document.querySelector<HTMLDivElement>("#map")!;

let api: Api;
let config: AppConfig;
let mapProvider: MapProvider;
let currentMapId: string | null = null;
let layers: LayerRow[] = [];
let selectedLayerIds: Set<string> = new Set();
let lineTilePrefs: StoredLineTiles = { network: true, poles: false };
let lineTileVersion: string | null = null;
let fetchAbort: AbortController | null = null;
let renderGeneration = 0;
let auth: BasicAuth;
/** Skip saving viewport while we programmatically position the map. */
let suppressViewportPersist = false;

function setStatus(text: string, warn = false): void {
  statusEl.textContent = text;
  statusEl.classList.toggle("warn", warn);
}

function showLogin(message?: string): void {
  shellEl.classList.add("hidden");
  loginEl.classList.remove("hidden");
  loginError.hidden = !message;
  loginError.textContent = message ?? "";
}

function showShell(): void {
  loginEl.classList.add("hidden");
  shellEl.classList.remove("hidden");
}

function closeCard(): void {
  cardEl.classList.add("hidden");
  cardEl.innerHTML = "";
}

async function openCard(featureId: string): Promise<void> {
  try {
    const detail = await api.getFeature(featureId);
    cardEl.innerHTML = `
      <button type="button" class="close" aria-label="Закрыть">×</button>
      <h2>${escapeHtml(detail.title || detail.id)}</h2>
      <p>${escapeHtml(detail.layer_name || "")}${detail.number != null ? ` · №${detail.number}` : ""}</p>
      <p>${escapeHtml(detail.description || "Нет описания")}</p>
    `;
    cardEl.querySelector(".close")?.addEventListener("click", closeCard);
    cardEl.classList.remove("hidden");
  } catch (err) {
    setStatus(err instanceof Error ? err.message : "Ошибка карточки", true);
  }
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function boundsAround(
  lon: number,
  lat: number,
  radiusKm: number,
): { xmin: number; ymin: number; xmax: number; ymax: number } {
  const latDelta = radiusKm / 111.32;
  const lonDelta = radiusKm / (111.32 * Math.max(0.01, Math.cos((lat * Math.PI) / 180)));
  return {
    xmin: lon - lonDelta,
    ymin: lat - latDelta,
    xmax: lon + lonDelta,
    ymax: lat + latDelta,
  };
}

function getCurrentPosition(): Promise<GeolocationPosition> {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error("Geolocation is not available"));
      return;
    }
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: false,
      timeout: 2000,
      maximumAge: 300_000,
    });
  });
}

async function applyInitialView(mapId: string): Promise<void> {
  const saved = loadViewport(mapId);
  suppressViewportPersist = true;
  try {
    if (saved) {
      mapProvider.setViewport(saved);
      return;
    }

    try {
      const pos = await getCurrentPosition();
      const radiusKm = config.initial_radius_km || 20;
      mapProvider.setBounds(
        boundsAround(pos.coords.longitude, pos.coords.latitude, radiusKm),
      );
      const viewport = mapProvider.getViewport();
      saveViewport(mapId, viewport);
      return;
    } catch {
      setStatus("Геолокация недоступна, показываем границы карты", true);
    }

    try {
      const bounds = await api.getBounds(mapId);
      mapProvider.setBounds(bounds);
      saveViewport(mapId, mapProvider.getViewport());
    } catch (err) {
      if (err instanceof ApiError && err.code === "MAP_EMPTY") {
        setStatus("Карта пуста", true);
      } else {
        throw err;
      }
    }
  } finally {
    // Allow map actionend from setBounds/setViewport to settle first.
    window.setTimeout(() => {
      suppressViewportPersist = false;
    }, 400);
  }
}

function renderLayers(): void {
  layersList.innerHTML = "";
  for (const layer of layers) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = layer.id;
    input.checked = selectedLayerIds.has(layer.id);
    input.addEventListener("change", () => {
      if (input.checked) selectedLayerIds.add(layer.id);
      else selectedLayerIds.delete(layer.id);
      if (currentMapId) saveLayerIds(currentMapId, [...selectedLayerIds]);
      void refreshFeatures();
    });
    label.append(input, document.createTextNode(` ${layer.name}`));
    layersList.append(label);
  }
}

function isRasterLines(): boolean {
  return config.line_render === "raster";
}

function applyLineTilesToMap(): void {
  if (!isRasterLines() || !currentMapId || !lineTileVersion) {
    mapProvider.setLineTiles(null);
    return;
  }
  mapProvider.setLineTiles({
    mapId: currentMapId,
    version: lineTileVersion,
    networkVisible: lineTilePrefs.network,
    polesVisible: lineTilePrefs.poles,
  });
}

function renderLineTileControls(): void {
  lineTilesList.innerHTML = "";
  if (!isRasterLines()) {
    lineTilesList.classList.add("hidden");
    return;
  }
  lineTilesList.classList.remove("hidden");
  const items: { key: keyof StoredLineTiles; label: string }[] = [
    { key: "network", label: "Сеть (линии)" },
    { key: "poles", label: "Линии столбов" },
  ];
  for (const item of items) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = lineTilePrefs[item.key];
    input.addEventListener("change", () => {
      lineTilePrefs = { ...lineTilePrefs, [item.key]: input.checked };
      if (currentMapId) saveLineTiles(currentMapId, lineTilePrefs);
      applyLineTilesToMap();
    });
    label.append(input, document.createTextNode(` ${item.label}`));
    lineTilesList.append(label);
  }
  if (!lineTileVersion) {
    const hint = document.createElement("p");
    hint.className = "line-tiles-hint";
    hint.textContent = "Тайлы ещё не сгенерированы (--tiles-only)";
    lineTilesList.append(hint);
  }
}

async function loadLayersForMap(mapId: string): Promise<void> {
  const { rows } = await api.listLayers(mapId);
  layers = rows;
  const saved = loadLayerIds(mapId);
  if (saved && saved.length) {
    const known = new Set(rows.map((r) => r.id));
    selectedLayerIds = new Set(saved.filter((id) => known.has(id)));
    if (selectedLayerIds.size === 0) {
      selectedLayerIds = new Set(rows.map((r) => r.id));
    }
  } else {
    selectedLayerIds = new Set(rows.map((r) => r.id));
  }
  lineTilePrefs = loadLineTiles(mapId);
  renderLayers();
  renderLineTileControls();
}

async function selectMap(mapId: string): Promise<void> {
  currentMapId = mapId;
  saveMapId(mapId);
  closeCard();
  await loadLayersForMap(mapId);
  if (isRasterLines()) {
    try {
      const meta = await api.getTilesMeta(mapId);
      lineTileVersion = meta.lines_tile_version;
    } catch {
      lineTileVersion = null;
    }
  } else {
    lineTileVersion = null;
  }
  renderLineTileControls();
  applyLineTilesToMap();
  await applyInitialView(mapId);
  await refreshFeatures();
}

async function refreshFeatures(): Promise<void> {
  if (!currentMapId) return;
  fetchAbort?.abort();
  const controller = new AbortController();
  fetchAbort = controller;
  mapProvider.cancelRender();
  const generation = ++renderGeneration;

  const viewport: Viewport = mapProvider.getViewport();
  if (!suppressViewportPersist) {
    saveViewport(currentMapId, viewport);
  }
  const bbox = viewport.bbox.join(",");
  const layerParam =
    selectedLayerIds.size === layers.length ? null : [...selectedLayerIds];

  setStatus("Загрузка…");
  try {
    const data = await api.getFeatures(
      currentMapId,
      bbox,
      viewport.zoom,
      layerParam,
      controller.signal,
    );
    if (controller.signal.aborted || generation !== renderGeneration) return;

    // Allowlisted overview points are clickable; full set after detail / second threshold.
    const interactive = pointsInteractiveAtZoom(config, viewport.zoom);
    await mapProvider.renderFeatures(data, {
      interactivePoints: interactive,
      onPointClick: (id) => void openCard(id),
      generation,
      authHeader: authHeader(auth),
    });

    if (generation !== renderGeneration) return;
    const incomplete = data.truncated || data.thinned;
    const linePart = isRasterLines()
      ? lineTileVersion
        ? `тайлы ${lineTilePrefs.network || lineTilePrefs.poles ? "вкл" : "выкл"}`
        : "тайлы нет"
      : `${data.meta?.line_count ?? "?"} линий`;
    if (incomplete) {
      setStatus("Данные неполные (обрезка/прореживание)", true);
    } else {
      setStatus(`${data.meta?.point_count ?? "?"} точек · ${linePart}`);
    }
  } catch (err) {
    if ((err as Error).name === "AbortError") return;
    if (err instanceof ApiError && err.status === 401) {
      clearAuth();
      showLogin("Сессия истекла");
      return;
    }
    setStatus(err instanceof Error ? err.message : "Ошибка загрузки", true);
  }
}

async function bootstrap(session: BasicAuth): Promise<void> {
  auth = session;
  api = createApi(auth);
  config = await api.getConfig();
  if (!config.yandex_maps_api_key) {
    throw new Error("YANDEX_MAPS_API_KEY не задан");
  }

  mapProvider = createMapProvider(config.map_provider || "yandex21");
  await mapProvider.init(mapContainer, config);
  mapProvider.onViewportSettled(() => {
    void refreshFeatures();
  });

  const { rows: maps } = await api.listMaps();
  if (!maps.length) throw new Error("Нет доступных карт");

  const saved = loadMapId();
  const initial = maps.find((m) => m.id === saved)?.id ?? maps[0].id;

  if (maps.length > 1) {
    mapSelectWrap.classList.remove("hidden");
    mapSelect.innerHTML = "";
    for (const m of maps) {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.name;
      if (m.id === initial) opt.selected = true;
      mapSelect.append(opt);
    }
    mapSelect.onchange = () => {
      void selectMap(mapSelect.value);
    };
  } else {
    mapSelectWrap.classList.add("hidden");
  }

  showShell();
  await selectMap(initial);
}

loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const username = (
    document.querySelector<HTMLInputElement>("#login-user")?.value ?? ""
  ).trim();
  const password = document.querySelector<HTMLInputElement>("#login-pass")?.value ?? "";
  const next = { username, password };
  saveAuth(next);
  loginError.hidden = true;
  void bootstrap(next).catch((err) => {
    clearAuth();
    showLogin(err instanceof Error ? err.message : "Ошибка входа");
  });
});

const existing = loadAuth();
if (existing) {
  void bootstrap(existing).catch((err) => {
    clearAuth();
    showLogin(err instanceof Error ? err.message : "Ошибка входа");
  });
} else {
  showLogin();
}
