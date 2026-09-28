from __future__ import annotations

import asyncpg

from app.config import Settings

_pool: asyncpg.Pool | None = None


async def init_pool(settings: Settings) -> asyncpg.Pool | None:
    global _pool
    if not settings.database_url:
        return None
    if _pool is not None:
        return _pool
    # asyncpg wants postgresql:// not postgresql+asyncpg://
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialized")
    return _pool
