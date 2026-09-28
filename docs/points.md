Переменные окружения и поведение слоев точек

- `GIS_POINT_OVERVIEW_ICONS=` — CSV `icon_name` (из `icon_uuid`). Эти точки видны на **любом** zoom (пока фильтр ещё режет набор — только они; после полного detail — как часть всех). Кликабельны, пока отдаются allowlist’ом. Пусто = нет overview-фильтра ниже detail. Нужен sync/icons.
- `GIS_POINT_DETAIL_ZOOM=15` — zoom ≥ порога → **все** точки, все кликабельны.
- `GIS_POINT_DETAIL_ZOOM_SECOND=` + `GIS_POINT_OVERVIEW_ICONS_SECOND=` — опциональная средняя ступень (оба нужны, **SECOND < DETAIL**):
  - `zoom < SECOND` — только первый список;
  - `SECOND ≤ zoom < DETAIL` — первый ∪ второй (первый не пропадает);
  - `zoom ≥ DETAIL` — все точки.
  Без SECOND — как раньше: ниже DETAIL только первый список (если задан), с DETAIL — все.
- `GIS_POINT_ICON_SIZE=32` — размер значков на экране (px), квадрат. Не из пикселей PNG.
- `GIS_POINT_CIRCLE_SIZE=22` — диаметр маркеров без `iconId` (px).
- `GIS_POINT_FIXED_SIZE_MAX_ZOOM=15` — пока zoom ≤ порога, `iconScale` из GIS игнорируется. Выше — база × `iconScale` (clamp 0.5–3). Не используется, если включён `GIS_POINT_ICON_FIXED`.
- `GIS_POINT_ICON_FIXED=false` — в API как `icon_fixed`. Если `true`, масштабирование отключено полностью: всегда `GIS_POINT_ICON_SIZE` / `GIS_POINT_CIRCLE_SIZE` на любом zoom, `iconScale` не влияет.
- `GIS_MAX_POINT_COUNT=300` — бюджет точек при zoom ≤ `GIS_POINT_FIXED_SIZE_MAX_ZOOM`. `0` — без лимита. Viewport делится на сетку (~cols×rows ≤ лимита, пропорции bbox); в каждой клетке остаётся одна точка — ближайшая к центру клетки. PostGIS делает отбор в SQL; GIS-прокси — в Python на уже скачанном ответе.
