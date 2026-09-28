import type { FeatureStyle, MapFeature } from "../api/client";

/** Backbone is the only elevated class; br/access/unclassified share the rest. */
export function isBackboneLine(feature: MapFeature): boolean {
  return (feature.properties.classes ?? []).includes("backbone");
}

/** Draw others first so backbone ends up on top. */
export function sortLinesForDraw(lines: MapFeature[]): MapFeature[] {
  return [...lines].sort((a, b) => {
    const ab = isBackboneLine(a) ? 1 : 0;
    const bb = isBackboneLine(b) ? 1 : 0;
    return ab - bb;
  });
}

export type ResolvedLineStroke = {
  color: string;
  width: number;
  opacity: number;
};

export function resolveLineStroke(style: FeatureStyle | undefined, backbone: boolean): ResolvedLineStroke {
  const baseWidth = style?.lineWidth ?? 2;
  const baseOpacity = style?.lineOpacity ?? 0.85;
  const color = style?.lineColor ?? (backbone ? "#1a5fb4" : "#3388ff");
  if (backbone) {
    return {
      color,
      width: Math.max(baseWidth * 1.75, baseWidth + 1.5),
      opacity: Math.min(1, Math.max(baseOpacity, 0.95)),
    };
  }
  return {
    color,
    width: style?.lineWidth != null ? baseWidth : 1.75,
    opacity: style?.lineOpacity != null ? baseOpacity : 0.75,
  };
}
