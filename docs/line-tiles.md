# Линии: растровые тайлы (принятая модель)

Точки — vector API (`/features`). Линии в режиме `LINE_RENDER=raster` — два нативных raster tile layer поверх Яндекса.

Статус: спецификация зафиксирована; MVP raster реализован (см. ниже). Default runtime `LINE_RENDER=vector` до переключения.

## Решения (locked)

| # | Тема | Решение |
|---|---|---|
| Слои | Два raster | `network` = все линии кроме «Линии столбов»; `poles` = только «Линии столбов» |
| Районы UI | Чекбоксы GIS-слоёв | Режут **только точки**; линии районов всегда в `network` |
| UI линий | Отдельно от районов | Два чекбокса: network / poles; **poles по умолчанию выключены** |
| Имя poles | Env | `LINE_TILE_POLES_LAYER_NAME=Линии столбов` |
| Auth тайлов | Периметр | «Открытые» versioned URL (без Basic/HMAC на tile). Доступ ограничивает сеть/периметр приложения |
| Хранение | MVP | Volume `TILE_CACHE_DIR`; отдаёт **api** (StaticFiles/route). Позже можно унести на nginx, URL не менять |
| Версии / GC | Каталоги | Генерация в новую `{version}/` → atomic switch `maps.lines_tile_version` → держать previous → удалить старше |
| Генерация | CLI | Только вручную: `python -m app.sync --tiles-only` (не в общем feature sync) |
| Runtime | Flag | `LINE_RENDER=vector\|raster`. Vector-код **сохраняем** для переключения |
| Рендерер | Cairo | `pycairo`; не Pillow/Mapnik на MVP |
| Стиль | Из style as-is | `lineColor` / `lineWidth` / `lineOpacity` из GIS без доп. множителей. Нет значения: poles → оранжевый/1.5px; backbone → `#1a5fb4`/2.5px; остальное → `#3388ff`/1.25px. Класс — LOD (+ fallback толщина), не ×1.75 поверх GIS width |
| LOD в тайле | Как vector | На низком z для `network` — только backbone; пороги из `GIS_LINE_CLASSES_BY_ZOOM` |
| Формат | WebP | Прозрачный WebP; логический размер тайла **256×256** CSS px |
| Retina | `@2x` пирамида | Вторая пирамида **512×512** device px на тот же z/x/y; URL-суффикс `@2x` (или отдельный сегмент). Клиент выбирает template по `devicePixelRatio` (≥1.5 → @2x) |
| `/features` | Линии | Эндпоинт живёт; при `LINE_RENDER=raster` в коллекции **линии = `[]`**, точки как обычно |
| Провайдер | Порядок | Сначала **yandex21**; yandex3 — вторым этапом |

## URL

```
/api/maps/{mapId}/tiles/lines/{layer}/{tileVersion}/{z}/{x}/{y}.webp
/api/maps/{mapId}/tiles/lines/{layer}/{tileVersion}/{z}/{x}/{y}@2x.webp
```

`layer` = `network` | `poles`. `@2x` — тот же тайл, растр 512×512.

Layout на диске:

```
{TILE_CACHE_DIR}/{mapId}/{layer}/{tileVersion}/{z}/{x}/{y}.webp
{TILE_CACHE_DIR}/{mapId}/{layer}/{tileVersion}/{z}/{x}/{y}@2x.webp
```

`--tiles-only` генерирует **обе** плотности (1x и @2x) для **обоих** layer в один `tileVersion` перед switch.

- Version в **path**. Meta/version: `Cache-Control: no-store`.
- Versioned tiles: `Cache-Control: public, max-age=31536000, immutable`.
- Клиент после смены version **пересоздаёт** tile layer.
- Один `lines_tile_version` на network + poles и 1x + @2x.

## Генерация (--tiles-only)

1. `version_new` = новый id.
2. Render: PostGIS (3857, simplify по z, class filter) → Cairo → WebP 256 и 512 (@2x) для `network` и `poles` (полная перегенерация map, не дельта). На @2x stroke в device px ×2 (scale surface).
3. Atomic: `maps.lines_tile_version = version_new`.
4. GC: удалить версии старше previous (не сразу в момент switch — дать догрузить in-flight).

Пустые / отсутствующие тайлы: GET делает **lazy-render** в дерево `tileVersion` (кэш на диск); если геометрии нет — прозрачный WebP. Pre-render `--tiles-only` прогревает z=`TILE_Z_MIN`…`TILE_Z_MAX` (дефолт 10–15); выше — по запросу.

## Клиент (yandex21 first)

- При `raster`: не класть линии в ObjectManager; два Layer с tileUrlTemplate и **`projection: sphericalMercator`** (тайлы mercantile/3857; без этого на Яндексе типичный сдвиг по широте).
- Чекбоксы network/poles отдельно; poles default off.
- Точки — как сейчас, с фильтром районов.
- Vector path остаётся за `LINE_RENDER=vector` без удаления кода.

## Env (образец)

```env
LINE_RENDER=vector
TILE_CACHE_DIR=/var/lib/mobilemap/tiles
LINE_TILE_POLES_LAYER_NAME=Линии столбов
TILE_Z_MIN=10
TILE_Z_MAX=15
```

## Статус

MVP реализован: Cairo/WebP, `--tiles-only`, volume→api, yandex21 layers, `LINE_RENDER`. Вне скоупа: yandex3 tiles, nginx/S3, HMAC, auto-tiles после sync.

## Вне скоупа

- yandex3 tile layers
- nginx/S3 раздача
- dirty-tile дельта
- HMAC на тайлы
- автоматический tiles после feature sync/classify
