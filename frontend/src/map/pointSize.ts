import type { AppConfig, FeatureStyle } from "../api/client";

export type PixelSize = { width: number; height: number };

export type IconPixels = { width: number; height: number };

/** Fixed screen size applies while zoom is at or below the configured threshold. */
export function usesFixedPointSize(zoom: number, maxZoom: number): boolean {
  const z = Number(zoom);
  const max = Number(maxZoom);
  if (!Number.isFinite(z) || !Number.isFinite(max)) return true;
  return z <= max;
}

function scaleFactor(style: FeatureStyle, fixed: boolean): number {
  if (fixed) return 1;
  const raw = style.iconScale;
  const value = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(value) || value <= 0) return 1;
  return Math.min(3, Math.max(0.5, value));
}

function readSize(value: unknown, fallback: number): number {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) && n > 0 ? Math.round(n) : fallback;
}

/**
 * Screen size always from env config, never PNG pixels.
 * - icon_fixed / GIS_POINT_ICON_FIXED: always ICON/CIRCLE size (scale=1).
 * - else below GIS_POINT_FIXED_SIZE_MAX_ZOOM: scale=1; above: × iconScale (clamped).
 */
export function pointPixelSize(
  zoom: number,
  config: AppConfig,
  style: FeatureStyle,
  icon: IconPixels | null,
): PixelSize {
  const forceFixed = Boolean(config.icon_fixed);
  const fixed =
    forceFixed || usesFixedPointSize(zoom, config.point_fixed_size_max_zoom);
  const scale = scaleFactor(style, fixed);
  const base = icon
    ? readSize(config.point_icon_size, 32)
    : readSize(config.point_circle_size, 22);
  const size = Math.max(1, Math.round(base * scale));
  return { width: size, height: size };
}
