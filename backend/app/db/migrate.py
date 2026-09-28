from __future__ import annotations

from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


async def apply_migrations(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            row["filename"]
            for row in await conn.fetch("SELECT filename FROM schema_migrations")
        }
        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for path in files:
            if path.name in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations(filename) VALUES($1)",
                    path.name,
                )


async def main() -> None:
    from dotenv import load_dotenv
    from pathlib import Path as P

    load_dotenv(P(__file__).resolve().parents[3] / ".env")
    from app.config import get_settings
    from app.db import init_pool, close_pool

    settings = get_settings()
    pool = await init_pool(settings)
    if pool is None:
        raise SystemExit("DATABASE_URL is not configured")
    try:
        await apply_migrations(pool)
        print("migrations applied")
    finally:
        await close_pool()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
