# API local

La API sirve resultados del catálogo local. No inicia descargas, inferencia, entrenamiento ni backfill. La especificación generada por el código está en `/openapi.json` y su interfaz interactiva en `/docs`.

## Lectura

| Método y ruta | Respuesta |
|---|---|
| `GET /health` | Salud del catálogo, versión del esquema y ausencia de inferencia en HTTP. |
| `GET /v1/system` | Estado del volumen completo: catálogo, revisiones, campañas históricas y declaración de validación local por tarea/sensor. No depende de filtros ni ejecuta modelos. |
| `GET /v1/ports` | FeatureCollection WGS84 de puertos y zonas, con procedencia y estado provisional. |
| `GET /v1/observations` | Observaciones paginadas con calidad, estado, versión y enlaces a activos. |
| `GET /v1/observations/{observation_id}` | Una observación y sus activos registrados. |
| `GET /v1/detections` | FeatureCollection de detecciones de observaciones utilizables o parciales. |
| `GET /v1/changes` | Eventos de cambio con estado, fechas y evidencia temporal. |
| `GET /v1/jobs` | Estado operativo de la cola, intentos y códigos de error. |
| `GET /v1/models` | Manifiestos de modelos registrados. |
| `GET /v1/assets/{asset_id}` | Archivo registrado disponible dentro del volumen, con ETag SHA256. |
| `GET /v1/assets/{asset_id}?metadata=true` | Metadatos y enlace del activo, sin ruta local del archivo. |
| `GET /v1/coverage` | Conciliación entre catálogo y procesamiento, por periodo y ámbito. |
| `GET /v1/reviews/{entity_type}/{entity_id}` | Historial ordenado de revisiones de una entidad. |

Los identificadores de puerto son `prq`, `stc` y `pbr`; los sensores son `sentinel-1` y `sentinel-2`. Observaciones y detecciones aceptan `port_id`, `sensor`, `start`, `end`, `recipe_version` e `include_rejected`. Detecciones también acepta `observation_id`; observaciones acepta `status`. Cambios acepta `port_id`, `start`, `end` e `include_rejected`. Cobertura acepta `port_id` y `recipe_version`.

Las fechas son ISO8601 con zona horaria. `start` es inclusivo y `end` exclusivo. Un intervalo inválido o una fecha sin zona produce 422. El servidor normaliza a UTC.

Observaciones, detecciones, cambios y trabajos aceptan `limit` entre 1 y 500, y `cursor`. Repite los mismos filtros al seguir `next_cursor`; cambiar filtros con un cursor anterior produce 422. El orden es ascendente por fecha y un identificador estable. La última página devuelve `next_cursor: null`. Modelos, puertos, cobertura e historial no usan este cursor.

Las listas usan `items`; detecciones usa `features`. Las detecciones y sus geometrías se expresan en WGS84, orden longitud/latitud. Una vista previa puede incluir `metadata.wgs84_bounds` como `[oeste, sur, este, norte]`; los `bounds` nativos deben interpretarse con el `crs` registrado.

## Semántica de resultados

Una observación representa una adquisición, zona y receta de procesamiento. `detection_count` es el conteo producido por el modelo. `visible_detection_count` refleja detecciones actuales no rechazadas, incluidas las añadidas manualmente. Una escena no observable devuelve conteo nulo. Un cero en una escena utilizable representa cero detecciones del modelo, sin demostrar ausencia absoluta de buques.

`observable_fraction` estima la fracción de la zona observable a la resolución de la máscara. El worker combina la proporción de píxeles observables dentro de la zona con el área geométrica que el recorte realmente cubre. Los estados `usable`, `partial` y `unusable` describen esa observabilidad, no la precisión del detector. Las observaciones ya guardadas conservan el cálculo de su ejecución; actualizar el software no modifica retrospectivamente su evidencia.

`detection_score` no equivale a precisión validada. Los atributos sin soporte suficiente permanecen nulos y una anotación manual no recibe score inventado. Las revisiones no convierten automáticamente un modelo en validado. Los rechazos se ocultan de las consultas ordinarias; usa `include_rejected=true` para revisión.

Estos resultados no expresan llegadas, salidas, carga, TEU, identidad de buques ni actividad entre adquisiciones. Las geometrías portuarias actuales son provisionales y no son cartas de navegación.

## Revisiones autenticadas

Los POST requieren `Authorization: Bearer <credencial-local>` y el servidor debe tener `PUERTOGT_REVIEW_TOKEN`. Sin configuración responde 503; una credencial incorrecta produce 401. Las peticiones de navegador deben mantener el mismo origen. La lectura local no requiere token.

`POST /v1/reviews` recibe:

```json
{
  "entity_type": "detection",
  "entity_id": "identificador-existente",
  "previous_revision_id": null,
  "author": "identificador-del-revisor",
  "correction": {
    "review_status": "reviewed",
    "note": "Revisión visual sobre evidencia registrada"
  }
}
```

El ejemplo muestra la forma del mensaje; reemplaza los identificadores por registros existentes. La respuesta contiene `revision_id`. Envía el `latest_revision_id` de la entidad como `previous_revision_id` para la siguiente corrección. Si alguien revisó antes, el servidor devuelve 409 y no aplica la escritura. Recarga, compara y decide nuevamente; no repitas ciegamente la corrección. La propuesta anterior, la corrección, el autor y la fecha quedan auditados.

| Entidad | Campos de corrección admitidos |
|---|---|
| Todas | `review_status` (`reviewed`, `rejected`, `provisional`), `note`, `provenance`. |
| `detection` | `geometry`, `family`, `length_m`, `width_m`. |
| `observation` | `status` únicamente para marcar `unusable`, `observable_fraction`, `dataset_split`, `split_groups`, `scene_complete`. |
| `change` | `geometry` y `status` únicamente para marcar `rejected`. |

La familia admite `carga`, `tanquero`, `pasajeros`, `remolcador`, `pesquero`, `otros` o `null`. Las dimensiones deben ser metros positivos, finitos y menores que 1,000. Cada atributo no nulo exige procedencia independiente por campo:

```json
{
  "family": "carga",
  "provenance": {
    "family": {
      "kind": "independent_reference",
      "reference": "Referencia verificable, fecha y correspondencia con la escena"
    }
  }
}
```

También se admite `public_dataset` como clase de procedencia. Una referencia escrita no certifica su calidad; el revisor debe comprobarla. El recuadro de detección no es una referencia de eslora o manga. Usa `null` para abstenerte.

`POST /v1/reviews/detections` añade un objeto omitido. Recibe `observation_id`, una `geometry` GeoJSON WGS84 y `author`; devuelve `detection_id`. Solo admite observaciones utilizables o parciales y registra la adición sin score de modelo.

Una revisión manual puede aceptar un evento de cambio, pero no asignar persistencia ni reversión. Esos estados requieren evidencia temporal evaluada por el flujo de cambios.

## Errores y operación

404 indica una entidad o activo no disponible. 422 indica entrada inválida, procedencia insuficiente o cursor incompatible. 409 protege contra revisiones obsoletas. Los activos se resuelven por identificador registrado y no aceptan rutas arbitrarias.

El servidor está diseñado para localhost. Para exponerlo fuera del equipo se necesita una arquitectura de autenticación y transporte adicional; el token de revisión no protege todas las consultas. El contenedor declara su puerto interno, mientras Compose publica únicamente en localhost.

Las rutas de lectura de esta referencia se comprobaron contra un servicio local. Los POST de aceptar, rechazar, añadir y modificar geometría se probaron desde la interfaz sobre una copia temporal de evidencia real. La API conserva el contrato de datos; esa comprobación funcional no acredita calidad científica.
