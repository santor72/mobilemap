import { createYandex21Provider } from "./yandex21";
import { createYandex3Provider } from "./yandex3";
import type { MapProvider } from "./types";

export type { MapProvider, Viewport } from "./types";

export function createMapProvider(provider: string): MapProvider {
  switch (provider) {
    case "yandex21":
      return createYandex21Provider();
    case "yandex3":
      return createYandex3Provider();
    default:
      throw new Error(`Unsupported map provider: ${provider}`);
  }
}
