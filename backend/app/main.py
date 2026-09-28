from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

_root = Path(__file__).resolve().parents[2]
load_dotenv(_root / ".env")

from app.api.routes import router  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import close_pool, init_pool  # noqa: E402
from app.db.migrate import apply_migrations  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    pool = await init_pool(settings)
    if pool is not None:
        await apply_migrations(pool)
    yield
    await close_pool()


app = FastAPI(title="MobileMap API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
