from __future__ import annotations

from typing import Any

from app.config import Settings
from app.data.gis_reader import GisReader
from app.data.postgis_reader import PostgisReader


def create_reader(settings: Settings) -> GisReader | PostgisReader:
    source = (settings.data_source or "gis").strip().lower()
    if source == "postgis":
        if not settings.database_url:
            raise ValueError("DATA_SOURCE=postgis requires DATABASE_URL")
        return PostgisReader(settings)
    if source == "gis":
        return GisReader(settings)
    raise ValueError(f"Unsupported DATA_SOURCE: {settings.data_source}")
