from __future__ import annotations

import base64
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.config import Settings
from app.data.gis_reader import GisReader
from app.data.icon_assets import composed_path, mask_path, source_path
from app.db import get_pool
from app.sync.icon_mask import compose_png, extract_mask_and_color

logger = logging.getLogger(__name__)

VISION_PROMPT = (
    "Identify the underlying core symbol or object shape in this icon "
    "(e.g., 'home', 'settings', 'user', 'arrow_left'). "
    "Completely IGNORE the color of the icon. "
    "Respond with ONLY the name of the symbol in 1-2 words. "
    "Do not write full sentences, do not mention colors."
)


def _normalize_icon_name(raw: str) -> str:
    name = raw.strip().lower().replace(" ", "_").replace(".", "")
    name = re.sub(r"[^a-z0-9_]+", "_", name).strip("_")
    return name or "unknown"


class IconSyncPipeline:
    """Download GIS icons, extract local masks + color, name via vision."""

    def __init__(self, settings: Settings, gis: GisReader | None = None) -> None:
        self._settings = settings
        self._gis = gis or GisReader(settings)
        self._cache_dir = Path(settings.asset_cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        (self._cache_dir / "source").mkdir(parents=True, exist_ok=True)
        (self._cache_dir / "masks").mkdir(parents=True, exist_ok=True)
        self._client: OpenAI | None = None
        if (
            settings.open_api_key
            and settings.open_api_base
            and settings.model_name
        ):
            self._client = OpenAI(
                api_key=settings.open_api_key,
                base_url=settings.open_api_base,
            )

    @property
    def vision_configured(self) -> bool:
        return self._client is not None

    async def sync_map_icons(self, map_id: str) -> dict[str, Any]:
        pool = get_pool()
        rows = await pool.fetch(
            """
            SELECT DISTINCT style->>'iconId' AS icon_id
            FROM features
            WHERE map_id = $1::uuid
              AND style ? 'iconId'
              AND COALESCE(style->>'iconId', '') <> ''
            """,
            map_id,
        )
        candidates = [str(r["icon_id"]) for r in rows if r["icon_id"]]
        return await self.sync_icon_ids(candidates)

    async def sync_all_cached_icons(self) -> dict[str, Any]:
        """Backfill masks for every icon_uuid row and every PNG in the cache."""
        pool = get_pool()
        rows = await pool.fetch("SELECT id FROM icon_uuid")
        ids = [str(r["id"]) for r in rows]

        # Also pick up composed/source files not yet in icon_uuid.
        seen = {uuid.UUID(x) for x in ids}
        for folder in (self._cache_dir, self._cache_dir / "source"):
            if not folder.is_dir():
                continue
            for path in folder.glob("*.png"):
                try:
                    uid = uuid.UUID(path.stem)
                except ValueError:
                    continue
                if uid not in seen:
                    ids.append(str(uid))
                    seen.add(uid)

        return await self.sync_icon_ids(ids, backfill=True)

    async def sync_icon_ids(
        self,
        icon_ids: list[str],
        *,
        backfill: bool = False,
    ) -> dict[str, Any]:
        stats = {
            "candidates": len(icon_ids),
            "already_present": 0,
            "added": 0,
            "updated_mask": 0,
            "failed": 0,
            "skipped_no_vision": 0,
            "masks_created": 0,
            "masks_reused": 0,
        }
        if not icon_ids:
            return stats

        pool = get_pool()
        valid: list[uuid.UUID] = []
        for raw in icon_ids:
            try:
                valid.append(uuid.UUID(str(raw)))
            except ValueError:
                stats["failed"] += 1

        if not valid:
            return stats

        existing_rows = await pool.fetch(
            """
            SELECT id, shape_hash, icon_name
              FROM icon_uuid
             WHERE id = ANY($1::uuid[])
            """,
            valid,
        )
        existing = {row["id"]: row for row in existing_rows}
        stats["already_present"] = len(existing)

        for icon_id in valid:
            row = existing.get(icon_id)
            needs_mask = row is None or row["shape_hash"] is None or backfill
            needs_name = row is None
            if not needs_mask and not needs_name:
                continue
            try:
                result = await self._process_one(
                    icon_id,
                    existing_name=row["icon_name"] if row else None,
                    force_mask=needs_mask,
                )
                if row is None:
                    stats["added"] += 1
                elif result.get("mask_updated"):
                    stats["updated_mask"] += 1
                if result.get("mask_created"):
                    stats["masks_created"] += 1
                if result.get("mask_reused"):
                    stats["masks_reused"] += 1
                if result.get("skipped_no_vision"):
                    stats["skipped_no_vision"] += 1
            except Exception:
                logger.exception("Failed to process icon %s", icon_id)
                stats["failed"] += 1
        return stats

    async def _ensure_source_png(self, icon_id: uuid.UUID) -> Path:
        """Return path to original GIS PNG (source/ or legacy composed root)."""
        src = source_path(self._cache_dir, str(icon_id))
        if src.is_file():
            return src

        legacy = composed_path(self._cache_dir, str(icon_id))
        if legacy.is_file():
            src.parent.mkdir(parents=True, exist_ok=True)
            # Treat existing cache as source for backfill (pre-mask era).
            if not src.is_file():
                src.write_bytes(legacy.read_bytes())
            return src

        content = await self._gis.fetch_asset_from_gis(str(icon_id))
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(content)
        return src

    async def _process_one(
        self,
        icon_id: uuid.UUID,
        *,
        existing_name: str | None,
        force_mask: bool,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "mask_updated": False,
            "mask_created": False,
            "mask_reused": False,
            "skipped_no_vision": False,
        }
        src = await self._ensure_source_png(icon_id)
        png_bytes = src.read_bytes()

        icon_name = existing_name
        if icon_name is None:
            if not self.vision_configured:
                # Still extract mask; name stays unknown until vision is configured.
                icon_name = "unknown"
                out["skipped_no_vision"] = True
            else:
                icon_name = self._name_icon(src)

        if force_mask:
            extract = extract_mask_and_color(png_bytes)
            pool = get_pool()
            mpath = mask_path(self._cache_dir, extract.shape_hash)
            mpath.parent.mkdir(parents=True, exist_ok=True)
            mask_existed = await pool.fetchval(
                "SELECT 1 FROM icon_masks WHERE shape_hash = $1",
                extract.shape_hash,
            )
            await pool.execute(
                """
                INSERT INTO icon_masks(shape_hash, mask_path, width, height)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (shape_hash) DO UPDATE SET
                    mask_path = EXCLUDED.mask_path,
                    width = EXCLUDED.width,
                    height = EXCLUDED.height
                """,
                extract.shape_hash,
                str(mpath.resolve()),
                extract.width,
                extract.height,
            )
            mpath.write_bytes(extract.mask_png)
            if mask_existed:
                out["mask_reused"] = True
            else:
                out["mask_created"] = True

            composed = compose_png(extract.mask_png, extract.glyph_color)
            cpath = composed_path(self._cache_dir, str(icon_id))
            cpath.write_bytes(composed)

            await pool.execute(
                """
                INSERT INTO icon_uuid(
                    id, icon_name, icon_path,
                    shape_hash, glyph_color, source_path, composed_path
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (id) DO UPDATE SET
                    shape_hash = EXCLUDED.shape_hash,
                    glyph_color = EXCLUDED.glyph_color,
                    source_path = EXCLUDED.source_path,
                    composed_path = EXCLUDED.composed_path,
                    icon_path = EXCLUDED.icon_path
                    -- icon_name is append-only: never overwritten
                """,
                icon_id,
                icon_name,
                str(cpath.resolve()),
                extract.shape_hash,
                extract.glyph_color,
                str(src.resolve()),
                str(cpath.resolve()),
            )
            out["mask_updated"] = True
        elif existing_name is None:
            # Name-only insert should not happen without mask in this pipeline.
            pass

        return out

    def _name_icon(self, path: Path) -> str:
        assert self._client is not None
        b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
        response = self._client.chat.completions.create(
            model=self._settings.model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            max_tokens=10,
            temperature=0.0,
        )
        raw = (response.choices[0].message.content or "").strip()
        return _normalize_icon_name(raw)
