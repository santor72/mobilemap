# PostGIS как источник чтения

## Режим

- `DATA_SOURCE=gis` — UI читает напрямую GIS (прокси), как раньше.
- `DATA_SOURCE=postgis` — UI читает карты/слои/bounds/features/карточку из PostGIS.
- Иконки (`/api/assets`): локальный composed PNG из маски+цвета; GIS только для новых UUID.
- Sync всегда ходит в GIS и пишет в PG.

## Compose

Сервис `db` (PostGIS 16). API получает `DATABASE_URL=postgresql://mobilemap:mobilemap@db:5432/mobilemap`.

Миграции применяются при старте API (`schema_migrations` + `001_init.sql`).

## Синхронизация (ручная)

Полный replace карты через staging:

```bash
# все карты
docker compose exec api python -m app.sync

# одна карта
docker compose exec api python -m app.sync --map-id <UUID>
```

HTTP (Basic auth; если задан `SYNC_TOKEN` — ещё заголовок `X-Sync-Token`):

```http
POST /api/sync
POST /api/sync/{map_id}
```

Алгоритм: bounds карты → квадрантное дробление bbox при `truncated` → сырые features в `features_staging` → swap в `features` + обновление `maps`/`layers`. Полигоны отбрасываются.

## Чтение features из PG

- `ST_Intersects` + GIST
- точки: при zoom < `GIS_POINT_DETAIL_ZOOM` и непустом `GIS_POINT_OVERVIEW_ICONS` — только `icon_name` из списка (`icon_uuid`). При zoom ≤ `GIS_POINT_FIXED_SIZE_MAX_ZOOM` и `GIS_MAX_POINT_COUNT` > 0 — сетка по bbox (cols×rows ≤ лимита): одна точка на клетку, ближайшая к центру; GeoJSON только у отобранных; `thinned`, если кандидатов больше ответа. `0` — без лимита. GIS-прокси режет уже полученный ответ той же сеткой в Python.
- линии: `ST_Simplify` по той же таблице метров/`zoom`, затем бюджет вершин

## Иконки (`icon_uuid` + `icon_masks`)

Таблицы:

- `icon_masks(shape_hash PK, mask_path, width, height)` — дедуп форм (цвет не входит в hash).
- `icon_uuid(id, icon_name, icon_path, shape_hash, glyph_color, source_path, composed_path)` — UUID GIS-ассета → имя + цвет глифа. `icon_name` append-only; mask-поля обновляются при backfill.

Каталоги в `ASSET_CACHE_DIR`:

- `source/{uuid}.png` — оригинал GIS
- `masks/{shape_hash}.png` — RGBA маска: **R = glyph coverage**, **G = plate coverage**, B=0, A=alpha
- `{uuid}.png` — composed PNG (то, что отдаёт `/api/assets`)

После sync карты (если `SYNC_ICONS=true`) или `python -m app.sync --icons-only`:

1. Собираются уникальные `style.iconId` (или все кэш/БД при `--icons-only` без `--map-id`)
2. PNG скачивается в `source/` (если ещё нет)
3. Extract: plate = near-white opaque, glyph = остальное opaque; `glyph_color` = moda RGB глифа; `shape_hash` = sha256 битов формы
4. Маска пишется в `icon_masks` (reuse при том же hash)
5. Compose → `{uuid}.png` (белый plate + `glyph_color`)
6. Vision-модель (если `OPEN_API_*` заданы) пишет `icon_name`; иначе для новых — `unknown`

Отдача `GET /api/assets/{id}`: composed файл → compose из mask+DB → GIS fetch.

Цвет на сервере всегда из PNG (`glyph_color`). `style.iconColor` / `recolorIcon` на asset endpoint не влияют (follow-up: tint на фронте).

```bash
# backfill масок по всем известным UUID / файлам кэша
docker compose exec api python -m app.sync --icons-only
```

Без vision-переменных маски всё равно извлекаются; новые имена без vision — `unknown` (в stats: `skipped_no_vision`).

## Классы линий

Таблицы:

- `line_class_map(icon_name, class_name)` — UNIQUE `(icon_name, class_name)`. Seed: `arrow_down_left→backbone`, `camera→br`, `atom→access`.
- `feature_classes(feature_id, class_name)` — multi-label для линий.

Порядок: сначала обычный sync (features → icons), затем отдельно classify.

Классификация **не** входит в общий sync. Запуск только командой:

```bash
docker compose exec api python -m app.sync --classify-only
docker compose exec api python -m app.sync --map-id <UUID> --classify-only
```

Классификация: точки с `icon_name` из маппинга в буфере `LINE_CLASS_TOLERANCE_M` метров вдоль **всей** линии (`ST_DWithin` geography). Старые классы линий карты удаляются и пишутся заново.
В ответе `/features` (PostGIS) у линий: `properties.classes: ["backbone", "br", ...]`.

LOD отдачи линий по zoom: `GIS_LINE_CLASSES_BY_ZOOM` (docs/lines.md). На обзоре SQL-фильтр только `backbone`; бюджет вершин и пресэмпл тоже предпочитают backbone.

## Переключение после первого sync

1. Запустить sync.
2. В `.env`: `DATA_SOURCE=postgis`
3. Перезапустить `api`
4. Сравнить latency `GET /api/maps/{id}/features?...&zoom=11` с прежним gis-режимом

Локальный smoke на синтетических 700 объектах: `PostgisReader.get_features` ~15 ms (zoom 11). Реальный выигрыш против GIS — на полном bbox города, где раньше ждали upstream.
