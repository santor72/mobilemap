"""Icon asset resolve: composed PNG → compose from mask → GIS fetch."""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import Settings
from app.sync.icon_mask import compose_png

logger = logging.getLogger(__name__)


def composed_path(cache_dir: Path, asset_id: str) -> Path:
    return cache_dir / f"{asset_id}.png"


def source_path(cache_dir: Path, asset_id: str) -> Path:
    return cache_dir / "source" / f"{asset_id}.png"


def mask_path(cache_dir: Path, shape_hash: str) -> Path:
    return cache_dir / "masks" / f"{shape_hash}.png"


async def try_compose_from_db(settings: Settings, asset_id: str) -> bytes | None:
    """If icon_uuid has shape_hash + glyph_color and mask file exists, compose PNG."""
    try:
        from app.db import get_pool
    except Exception:
        return None
    try:
        pool = get_pool()
    except RuntimeError:
        return None

    try:
        row = await pool.fetchrow(
            """
            SELECT i.glyph_color, i.shape_hash, m.mask_path
            FROM icon_uuid i
            JOIN icon_masks m ON m.shape_hash = i.shape_hash
            WHERE i.id = $1::uuid
              AND i.glyph_color IS NOT NULL
              AND i.shape_hash IS NOT NULL
            """,
            asset_id,
        )
    except Exception:
        logger.exception("icon compose DB lookup failed for %s", asset_id)
        return None

    if row is None:
        return None

    mask_file = Path(row["mask_path"])
    if not mask_file.is_file():
        # Fallback to canonical cache path.
        mask_file = mask_path(Path(settings.asset_cache_dir), row["shape_hash"])
    if not mask_file.is_file():
        return None

    try:
        content = compose_png(mask_file.read_bytes(), row["glyph_color"])
    except Exception:
        logger.exception("compose_png failed for %s", asset_id)
        return None

    out = composed_path(Path(settings.asset_cache_dir), asset_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(content)
    try:
        await pool.execute(
            """
            UPDATE icon_uuid
               SET composed_path = $2
             WHERE id = $1::uuid
            """,
            asset_id,
            str(out.resolve()),
        )
    except Exception:
        logger.exception("failed to update composed_path for %s", asset_id)
    return content
