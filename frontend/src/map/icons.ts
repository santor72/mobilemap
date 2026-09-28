export type IconAsset = {
  url: string;
  width: number;
  height: number;
};

const iconCache = new Map<string, Promise<IconAsset>>();
const sizedCache = new Map<string, Promise<string>>();

async function loadIcon(assetId: string, authHeader: string): Promise<IconAsset> {
  const response = await fetch(`/api/assets/${assetId}`, {
    headers: { Authorization: authHeader, Accept: "image/png" },
  });
  if (!response.ok) throw new Error("icon fetch failed");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const bitmap = await createImageBitmap(blob);
  const asset = { url, width: bitmap.width, height: bitmap.height };
  bitmap.close();
  return asset;
}

export function resolveIcon(assetId: string, authHeader: string): Promise<IconAsset> {
  const cached = iconCache.get(assetId);
  if (cached) return cached;
  const pending = loadIcon(assetId, authHeader).catch((err) => {
    iconCache.delete(assetId);
    throw err;
  });
  iconCache.set(assetId, pending);
  return pending;
}

/**
 * Rasterize icon to exact CSS pixel size so ObjectManager cannot fall back to
 * native PNG dimensions (56/64) when iconImageSize is ignored.
 */
export function resolveIconUrlAtSize(
  assetId: string,
  authHeader: string,
  sizePx: number,
): Promise<string> {
  const size = Math.max(1, Math.round(sizePx));
  const key = `${assetId}@${size}`;
  const cached = sizedCache.get(key);
  if (cached) return cached;

  const pending = (async () => {
    const asset = await resolveIcon(assetId, authHeader);
    const srcBlob = await fetch(asset.url).then((r) => r.blob());
    const bitmap = await createImageBitmap(srcBlob);
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      bitmap.close();
      return asset.url;
    }
    ctx.clearRect(0, 0, size, size);
    ctx.drawImage(bitmap, 0, 0, size, size);
    bitmap.close();
    const out = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/png"),
    );
    if (!out) return asset.url;
    return URL.createObjectURL(out);
  })().catch((err) => {
    sizedCache.delete(key);
    throw err;
  });

  sizedCache.set(key, pending);
  return pending;
}
