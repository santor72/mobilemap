Переменные окружения и поведение слоев точек

- `GIS_POINT_DETAIL_ZOOM=15` — zoom ≥ порога: все точки интерактивны (карточка). Ниже и без `GIS_POINT_OVERVIEW_ICONS` — только показ.
- `GIS_POINT_OVERVIEW_ICONS=` — CSV `icon_name` (из `icon_uuid`). Если не пусто и zoom < `GIS_POINT_DETAIL_ZOOM`, в `/features` отдаются только точки с этими именами; они кликабельны на любом zoom (порог `GIS_POINT_DETAIL_ZOOM` для них не действует). Пусто = без фильтра и без клика ниже detail. Нужен sync/icons.
- `GIS_POINT_ICON_SIZE=32` — размер значков на экране (px), квадрат. Не из пикселей PNG.
- `GIS_POINT_CIRCLE_SIZE=22` — диаметр маркеров без `iconId` (px).
- `GIS_POINT_FIXED_SIZE_MAX_ZOOM=15` — пока zoom ≤ порога, `iconScale` из GIS игнорируется. Выше — база × `iconScale` (clamp 0.5–3). Не используется, если включён `GIS_POINT_ICON_FIXED`.
- `GIS_POINT_ICON_FIXED=false` — в API как `icon_fixed`. Если `true`, масштабирование отключено полностью: всегда `GIS_POINT_ICON_SIZE` / `GIS_POINT_CIRCLE_SIZE` на любом zoom, `iconScale` не влияет.
- `GIS_MAX_POINT_COUNT=300` — бюджет точек при zoom ≤ `GIS_POINT_FIXED_SIZE_MAX_ZOOM`. `0` — без лимита. Viewport делится на сетку (~cols×rows ≤ лимита, пропорции bbox); в каждой клетке остаётся одна точка — ближайшая к центру клетки. PostGIS делает отбор в SQL; GIS-прокси — в Python на уже скачанном ответе.
