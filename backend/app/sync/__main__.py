from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")


async def _run(
    map_id: str | None,
    *,
    icons_only: bool,
    classify_only: bool,
    tiles_only: bool,
    skip_icons: bool,
) -> None:
    from app.config import get_settings
    from app.db import close_pool, init_pool
    from app.db.migrate import apply_migrations
    from app.sync.classify_lines import LineClassifyPipeline
    from app.sync.icons import IconSyncPipeline
    from app.sync.job import MapSyncService
    from app.tiles.render import generate_map_tiles

    settings = get_settings()
    pool = await init_pool(settings)
    if pool is None:
        raise SystemExit("DATABASE_URL is not configured")
    await apply_migrations(pool)
    try:
        if icons_only:
            pipeline = IconSyncPipeline(settings)
            if map_id:
                # Map candidates + force mask backfill for those UUIDs.
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
                ids = [str(r["icon_id"]) for r in rows if r["icon_id"]]
                result = await pipeline.sync_icon_ids(ids, backfill=True)
                print(
                    json.dumps(
                        {"map_id": map_id, "icons": result},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                result = await pipeline.sync_all_cached_icons()
                print(json.dumps({"icons": result}, ensure_ascii=False, indent=2))
            return

        if classify_only:
            pipeline = LineClassifyPipeline(settings)
            if map_id:
                result = await pipeline.classify_map(map_id)
                print(
                    json.dumps(
                        {"map_id": map_id, "line_classes": result},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                maps = await pool.fetch("SELECT id::text AS id FROM maps ORDER BY name")
                out = []
                for row in maps:
                    mid = row["id"]
                    out.append(
                        {
                            "map_id": mid,
                            "line_classes": await pipeline.classify_map(mid),
                        }
                    )
                print(json.dumps(out, ensure_ascii=False, indent=2))
            return

        if tiles_only:
            if map_id:
                result = await generate_map_tiles(pool, settings, map_id)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                maps = await pool.fetch("SELECT id::text AS id FROM maps ORDER BY name")
                out = []
                for row in maps:
                    mid = row["id"]
                    try:
                        out.append(await generate_map_tiles(pool, settings, mid))
                    except Exception as exc:  # noqa: BLE001 — report per map
                        out.append({"map_id": mid, "error": str(exc)})
                print(json.dumps(out, ensure_ascii=False, indent=2))
            return

        service = MapSyncService(
            settings,
            sync_icons=settings.sync_icons and not skip_icons,
        )
        if map_id:
            result = await service.sync_map(map_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            results = await service.sync_all_maps()
            print(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        await close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync GIS maps into PostGIS")
    parser.add_argument("--map-id", default=None, help="UUID of a single map; omit to sync all")
    parser.add_argument(
        "--icons-only",
        action="store_true",
        help="Extract/backfill icon masks + names (no feature sync)",
    )
    parser.add_argument(
        "--classify-only",
        action="store_true",
        help="Rebuild feature_classes for lines (separate from feature sync)",
    )
    parser.add_argument(
        "--tiles-only",
        action="store_true",
        help="Rebuild raster line tile pyramids (network + poles, 1x + @2x)",
    )
    parser.add_argument(
        "--skip-icons",
        action="store_true",
        help="Skip vision icon pipeline during feature sync",
    )
    args = parser.parse_args()
    exclusive = sum(
        bool(x) for x in (args.icons_only, args.classify_only, args.tiles_only)
    )
    if exclusive > 1:
        parser.error("Use only one of --icons-only / --classify-only / --tiles-only")
    asyncio.run(
        _run(
            args.map_id,
            icons_only=args.icons_only,
            classify_only=args.classify_only,
            tiles_only=args.tiles_only,
            skip_icons=args.skip_icons,
        )
    )


if __name__ == "__main__":
    main()
