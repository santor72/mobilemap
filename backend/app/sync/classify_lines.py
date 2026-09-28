from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.db import get_pool

logger = logging.getLogger(__name__)


class LineClassifyPipeline:
    """Assign multi-label classes to lines from nearby classified points."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def classify_map(self, map_id: str) -> dict[str, Any]:
        pool = get_pool()
        tol = float(self._settings.line_class_tolerance_m)
        async with pool.acquire() as conn:
            async with conn.transaction():
                del_status = await conn.execute(
                    """
                    DELETE FROM feature_classes fc
                    USING features f
                    WHERE fc.feature_id = f.id
                      AND f.map_id = $1::uuid
                      AND ST_GeometryType(f.geom) IN ('ST_LineString', 'ST_MultiLineString')
                    """,
                    map_id,
                )
                deleted = int(del_status.split()[-1]) if del_status else 0

                inserted = await conn.fetch(
                    """
                    WITH hits AS (
                        SELECT DISTINCT
                            l.id AS feature_id,
                            m.class_name
                        FROM features l
                        JOIN features p
                          ON p.map_id = l.map_id
                         AND ST_GeometryType(p.geom) = 'ST_Point'
                         AND p.style ? 'iconId'
                         AND COALESCE(p.style->>'iconId', '') <> ''
                         AND ST_DWithin(
                               l.geom::geography,
                               p.geom::geography,
                               $2::float8
                             )
                        JOIN icon_uuid i
                          ON i.id::text = p.style->>'iconId'
                        JOIN line_class_map m
                          ON m.icon_name = i.icon_name
                        WHERE l.map_id = $1::uuid
                          AND ST_GeometryType(l.geom) IN ('ST_LineString', 'ST_MultiLineString')
                    )
                    INSERT INTO feature_classes(feature_id, class_name)
                    SELECT feature_id, class_name FROM hits
                    ON CONFLICT DO NOTHING
                    RETURNING feature_id, class_name
                    """,
                    map_id,
                    tol,
                )

        line_count = await pool.fetchval(
            """
            SELECT count(*) FROM features
            WHERE map_id = $1::uuid
              AND ST_GeometryType(geom) IN ('ST_LineString', 'ST_MultiLineString')
            """,
            map_id,
        )
        classified_lines = await pool.fetchval(
            """
            SELECT count(DISTINCT fc.feature_id)
            FROM feature_classes fc
            JOIN features f ON f.id = fc.feature_id
            WHERE f.map_id = $1::uuid
              AND ST_GeometryType(f.geom) IN ('ST_LineString', 'ST_MultiLineString')
            """,
            map_id,
        )
        class_rows = await pool.fetchval(
            """
            SELECT count(*)
            FROM feature_classes fc
            JOIN features f ON f.id = fc.feature_id
            WHERE f.map_id = $1::uuid
            """,
            map_id,
        )
        return {
            "tolerance_m": tol,
            "lines_total": int(line_count or 0),
            "lines_classified": int(classified_lines or 0),
            "class_assignments": int(class_rows or 0),
            "deleted_previous": deleted,
            "inserted_now": len(inserted),
        }
