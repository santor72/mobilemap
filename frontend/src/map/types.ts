import type { AppConfig, FeaturesResponse } from "../api/client";

export type Viewport = {
  bbox: [number, number, number, number]; // west,south,east,north
  zoom: number;
};

export type LineTilesConfig = {
  mapId: string;
  version: string;
  networkVisible: boolean;
  polesVisible: boolean;
};

export type MapProvider = {
  init(container: HTMLElement, config: AppConfig): Promise<void>;
  setBounds(bounds: { xmin: number; ymin: number; xmax: number; ymax: number }): void;
  setViewport(viewport: Viewport): void;
  onViewportSettled(handler: (viewport: Viewport) => void): void;
  getViewport(): Viewport;
  /** Raster line overlays (yandex21). Pass null to remove. No-op on providers without support. */
  setLineTiles(config: LineTilesConfig | null): void;
  renderFeatures(
    data: FeaturesResponse,
    options: {
      interactivePoints: boolean;
      onPointClick?: (featureId: string) => void;
      generation: number;
      authHeader: string;
    },
  ): Promise<void>;
  cancelRender(generation?: number): void;
  destroy(): void;
};
