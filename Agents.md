# Описание

Проект разработки WEB приложения карты для мобильных устройств. На карте отражаются данные оптической сети оператора Интренте. Данные получаются из стороннего GIS сервиса по API или из локального хранилища POSTGis

# Работа с данными

В клиентском приложении отображаются только точки и линии, остальные типы данных клиенту не передаются.

## Истоники данных

Данные могут быть полученны илибо из внешнего API либо из развернутого вместе с приложением сервиса postgis.
Выбор: `DATA_SOURCE=gis|postgis`. Получение данных — модуль `NetworkReader` (`GisReader` / `PostgisReader`).
Подробности: docs/postgis.md

### Внешнее API

Контаркт внешнего API и документация в @docs/gisapi.md

### Postgis

Реализован как источник чтения. Заполнение — ручной sync из GIS (CLI / POST /api/sync). Иконки остаются в GIS.

# Картография

Картографическая основа проектируется и реализуется как модуль приложения предоставляющий стандартный интерфейс.
В приложении может быть несколько таких модулей. Выбор модуля определяется настройкой из .env файла.
Для производительности и читаемости 
 - Слои точек: размер GIS_POINT_ICON_SIZE / CIRCLE; до GIS_POINT_FIXED_SIZE_MAX_ZOOM без iconScale; либо GIS_POINT_ICON_FIXED=true — всегда фиксированный размер (docs/points.md)
 - До определенного зума точки не интерактивные (исключение: GIS_POINT_OVERVIEW_ICONS — клик всегда)
 - Точки: полный refetch по viewport; на клиенте diff по id (add/remove/patch size·interactive), без removeAll
 - Точки и линии добавляются порциями с отменой при смене viewport / новом запросе; векторные линии пока full replace
 - Данные запрашиваются в рамках текущего zoom + bounds; debounce жеста; abort устаревшего fetch
 - Два ObjectManager (точки и линии), clusterize: false; иконки по iconId (уникальных ~десяток, кэш по id); локальные маски+glyph_color → composed PNG (docs/postgis.md)
 - Точки при малом zoom: лимит GIS_MAX_POINT_COUNT, отбор сеткой по bbox (одна точка на клетку); опционально GIS_POINT_OVERVIEW_ICONS (docs/points.md)
 - Линии (текущий runtime): упрощение по zoom + бюджет GIS_MAX_LINE_VERTICES; приоритет backbone → остальное; PostGIS LOD классов GIS_LINE_CLASSES_BY_ZOOM (docs/lines.md)
 - Линии (целевая модель, docs/line-tiles.md): `LINE_RENDER=vector|raster`; raster = WebP 256 + @2x 512, Cairo, volume→api, два слоя network/poles (poles default off), versioned URL, `--tiles-only` вручную + lazy-render на GET; /features при raster отдаёт линии `[]`; сначала yandex21
 - Включение raster: сгенерировать `python -m app.sync --tiles-only`, в `.env` `LINE_RENDER=raster` и `MAP_PROVIDER=yandex21`, перезапуск api/web
 - Таблица simplify настраивается через GIS_LINE_SIMPLIFY_METERS для подбора на реальных данных

## Соглашение о типах объектов

На карте отражаем только точки и линии.
Полигоны отбрасываем. 
Для MVP: линии всегда (simplify + budget + class LOD в postgis); карточка по клику у точек при zoom ≥ GIS_POINT_DETAIL_ZOOM, а также у GIS_POINT_OVERVIEW_ICONS на любом zoom.

## Провайдера

Реализованы: Yandex API v2.1 (`yandex21`) и Yandex API v3 (`yandex3`).
Выбор: `MAP_PROVIDER` в `.env` (по умолчанию для сравнения скорости — `yandex3`).

# Настройки приложения

Хранятся в .env файле, этот файл никуда не отправляе, его нельзя показывать и редактировать напрямую. Для сохранения новых настроек и ознакомления с существующими используем env.sample

# Архитектура и используемый стек

Проект реализуется на основе модульной структуру. Слои такие как frontend, backend, api, получение данных 
реализуем отдельно.
Vite для frontend
backend python FastApi

# Деплой

Только Docker + Docker Compose. Сервисы: `db` (PostGIS), `api` (FastAPI), `web` (nginx + Vite static). Секреты и тюнинг — через `.env` на хосте, в образ не копировать.

# Аудитория и доступ

карта будет доступна везде. после MVP  Авторизация - локальная база пользователей. На первом этапе без ролей. Для настройки - отдддбельный модуль админки по /admin/
Все сетевые ограничение - не скоуп проекта.

# Офлайн
На старте достаточно  онлайн-браузера

# Источник данных
`DATA_SOURCE=gis` (прокси) или `postgis` (чтение из PG после sync). См. docs/postgis.md.


# Картопровайдер
Переключение: MAP_PROVIDER=yandex21|yandex3.

Границы MVP
Минимум: карта, слои, точки/линии в viewport, карточка, индикатор обрезки при truncated.
Поиск не реализуем.

UI (MVP):
- Слои: компактный раскрывающийся список с чекбоксами; выбор слоёв в localStorage.
- Карта: выпадающий список, если карт > 1 (если одна — без селектора); выбор карты в localStorage.
- Старт вида: геолокация ± GIS_INITIAL_RADIUS_KM (по умолчанию 20 км). При отказе геолокации — bounds карты из GIS.
- Далее bbox и zoom сохраняются в localStorage (на карту) и восстанавливаются при следующем входе / смене карты.

# Стартовые условие
MAP_PROVIDER=yandex21 или yandex3 (оба реализованы)
DATA_SOURCE=gis или postgis (оба реализованы; postgis требует sync)
при малом zoom точки для отображения отбираются сеткой по viewport (бюджет GIS_MAX_POINT_COUNT).
Авторизация: HTTP Basic, логин/пароль из .env (AUTH_USERNAME / AUTH_PASSWORD). Модуль пользователей и админку не разрабатываем.
