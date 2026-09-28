# Контракт получения данных: сервер PWA → GIS

Описание по текущему коду в репозитории на 25 сентября 2026 года, без проверки развёрнутого GIS.

Все пути ниже указаны относительно `${GIS_BASE_URL}/integration/v1`.

## Подключение и авторизация

Каждый запрос содержит:

```http
Authorization: Bearer <GIS_API_TOKEN>
Accept: application/json
```

Для получения значка используется `Accept: image/png`.

На стороне PWA нужны настройки `GIS_BASE_URL` и `GIS_API_TOKEN`. Таймаут по умолчанию — 20 секунд (`GIS_TIMEOUT_SECONDS`). Токен серверный, в браузер не передаётся. GIS ограничивает доступ списком разрешённых карт; удалённые слои и объекты не возвращаются.

## Методы чтения

| Метод и путь | Назначение | Ответ |
|---|---|---|
| `GET /maps` | Доступные карты | `{ rows: Map[] }` |
| `GET /maps/{map_id}/layers` | Слои карты | `{ rows: Layer[] }` |
| `GET /maps/{map_id}/bounds` | Границы объектов карты | `{ xmin, ymin, xmax, ymax }` |
| `GET /maps/{map_id}/features?bbox=…&layers=…` | Объекты в заданной области | GeoJSON `FeatureCollection` |
| `GET /maps/{map_id}/search?q=…` | Поиск объектов карты | `{ rows: SearchResult[] }` |
| `GET /features/{feature_id}` | Полная карточка объекта | Объект с геометрией, описанием и стилем |
| `GET /assets/{asset_id}` | Значок объекта | Бинарный PNG |
| `GET /reports/by-external-id/{external_report_id}` | Квитанция ранее отправленного отчёта | ID отчёта и срок хранения |
| `GET /openapi.json` | Машиночитаемое описание API | OpenAPI 3.1 |

Все идентификаторы в путях — UUID. Успешные запросы чтения возвращают `200`.

## Карты, слои и границы

Формы ответов:

```ts
type Map = {
  id: string;
  name: string;
  created_at: string; // дата и время
  report: {
    total: number;    // количество неудалённых объектов карты, не отчётов
  };
};

type Layer = {
  id: string;
  name: string;
  position: number;
  count: number;
  version: number;
};

type Bounds = {
  xmin: number; // западная долгота
  ymin: number; // южная широта
  xmax: number; // восточная долгота
  ymax: number; // северная широта
};
```

Карты сортируются по `created_at DESC`, слои — по `position`, затем `id`. Если у карты нет объектов с корректными координатами, `/bounds` возвращает `404` с кодом `MAP_EMPTY`.

## Получение объектов в области

```http
GET /integration/v1/maps/{map_id}/features?bbox=37.50,55.70,37.70,55.85&layers={layer_id_1},{layer_id_2}
```

| Параметр | Обязательность | Правила |
|---|---|---|
| `bbox` | Обязательный | Четыре числа через запятую: `west,south,east,north` |
| `layers` | Необязательный | До 32 UUID через запятую; отсутствие или пустое значение означает все слои карты |

Координаты — WGS84 / EPSG:4326. Долгота находится в диапазоне `[-180, 180]`, широта — `[-90, 90]`; требуется `west < east`, `south < north`.

Пример ответа:

```json
{
  "type": "FeatureCollection",
  "truncated": false,
  "limit": 40000,
  "features": [
    {
      "type": "Feature",
      "id": "11111111-1111-4111-8111-111111111111",
      "geometry": {
        "type": "Point",
        "coordinates": [37.61, 55.75]
      },
      "properties": {
        "id": "11111111-1111-4111-8111-111111111111",
        "layer_id": "22222222-2222-4222-8222-222222222222",
        "kind": "Point",
        "number": 42,
        "title": "Узел связи",
        "iconColor": "#3388ff"
      }
    }
  ]
}
```

Особенности контракта:

- Геометрия: `Point`, `LineString`, `Polygon`; порядок координат — `[longitude, latitude]`.
- В `properties` находятся основные поля объекта и поля его `style`, объединённые в один объект.
- Максимум — 40 000 объектов. `truncated: true` означает, что часть результата не вошла; пагинации нет. Для получения остальных данных нужно уменьшать область или выбирать отдельные слои.
- Порядок объектов: позиция слоя, номер объекта, ID объекта.
- Отбор выполняется по пересечению bounding box геометрии с областью запроса. Геометрия возвращается целиком, без обрезки.
- Параметра `zoom` в этом контракте нет: сервер PWA его в GIS не отправляет.

Поля стиля, которые понимает текущий клиент PWA:

```ts
type Style = {
  iconId?: string | null;
  iconColor?: string;
  iconScale?: number;
  markerShape?: 'pin' | 'circle';
  recolorIcon?: boolean;
  lineColor?: string;
  lineWidth?: number;
  lineOpacity?: number;
  fillColor?: string;
  fillOpacity?: number;
};
```

## Поиск и карточка объекта

```http
GET /integration/v1/maps/{map_id}/search?q=Узел
```

`q` обрезается по краям и должен содержать от 1 до 200 символов. Поиск выполняется без учёта регистра по вхождению в `title`, `source_name`, `description`, а также по точному совпадению номера объекта.

Ответ — `{ rows: [...] }`, максимум 100 элементов, без пагинации и признака усечения. Каждый элемент содержит следующие поля (ниже перечислены имена полей, а не полная схема типов):

```text
{
  id,
  layer_id,
  kind,
  number,
  title,
  layer_name,
  map_layer_id, // дублирует layer_id
  style
}
```

Геометрии в поисковой выдаче нет. Для неё и полного описания нужно вызвать:

```http
GET /integration/v1/features/{feature_id}
```

Карточка возвращается обычным JSON-объектом, без обёртки `Feature` или `rows`, со следующими полями:

```text
{
  id,
  layer_id,
  map_id,
  layer_name,
  number,
  kind,
  title,
  description,
  geometry,
  style,
  version
}
```

Здесь `style` — отдельное поле, в отличие от `/features?bbox=…`, где стиль развёрнут в `properties`. История изменений и фотоотчёты в карточку этим методом не включаются.

## Значки и квитанции отчётов

`GET /assets/{asset_id}` возвращает PNG с заголовками:

```http
Content-Type: image/png
Cache-Control: private, max-age=3600
```

`asset_id` берётся из `iconId` стиля. Сервер PWA дополнительно кеширует исходный PNG на диске; срок по умолчанию — 365 дней. Перекрашивание выполняется внутри PWA и не является параметром GIS API.

`GET /reports/by-external-id/{external_report_id}` возвращает:

```ts
{
  id: string;                     // ID отчёта в GIS
  external_report_id: string;
  retention_until: string | null;
}
```

Если отчёт не найден — `404 REPORT_NOT_FOUND`. Метод PWA `report_by_external_id()` преобразует этот `404` в `None`. В текущей реализации GET-ответа поле `repeated` отсутствует, хотя общая OpenAPI-схема квитанции помечает его обязательным.

## Ошибки и поведение 

GIS возвращает ошибки в формате:

```json
{
  "code": "FEATURE_NOT_FOUND",
  "error": "Объект не найден"
}
```

Поле `code` может отсутствовать. Важные коды: `INTEGRATION_UNAUTHORIZED`, `MAP_NOT_FOUND`, `MAP_EMPTY`, `FEATURE_NOT_FOUND`, `ASSET_NOT_FOUND`, `REPORT_NOT_FOUND`.

Клиент PWA обрабатывает ответы так:

| Ситуация | Результат на стороне PWA |
|---|---|
| Не заданы URL или токен | `503 GIS_NOT_CONFIGURED` |
| GIS вернул `401` | `502 GIS_AUTH_FAILED` |
| GIS вернул `400`, `404`, `409`, `422` | Сохраняет HTTP-статус, берёт код из `code`, сообщение из `error` |
| GIS вернул `429` или `5xx`, произошла сетевая ошибка/таймаут | Ошибка временной недоступности |
| Другая HTTP-ошибка | `502 GIS_HTTP_ERROR` |
| Ответ не JSON-объект или вместо значка пришёл некорректный PNG | `502 GIS_RESPONSE_INVALID` |

