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
| `GET /v1/coverage/passes` | Pasadas únicas, área observable e intervalos reales por puerto, zona y sensor. |
| `GET /v1/reviews/{entity_type}/{entity_id}` | Historial ordenado de revisiones de una entidad. |
| `GET /v1/review-queue` | Cola TRAIN congelada con campos seguros, sin predicciones ni revisiones anteriores. |
| `GET /v1/review-queue/observations/{observation_id}` | Evidencia cruda y zona proyectada en coordenadas normalizadas del preview. |
| `GET /v1/review-queue/assets/{observation_id}/{asset_id}` | Preview perteneciente al lote, con SHA verificado antes de servirlo. |
| `GET /v1/geography-proposal` | Resumen del piloto V2 independiente e inactivo, o `available: false`. |
| `GET /v1/geography-proposal/images/{name}` | `comparison.png` o `pilot.png`, previa verificación de hashes y geometrías. |

Los identificadores de puerto son `prq`, `stc` y `pbr`; los sensores son `sentinel-1` y `sentinel-2`. Observaciones y detecciones aceptan `port_id`, `sensor`, `start`, `end`, `recipe_version` e `include_rejected`. Detecciones también acepta `observation_id`; observaciones acepta `status`. Cambios acepta `port_id`, `start`, `end` e `include_rejected`. Cobertura acepta `port_id` y `recipe_version`.

Las fechas son ISO8601 con zona horaria. `start` es inclusivo y `end` exclusivo. Un intervalo inválido o una fecha sin zona produce 422. El servidor normaliza a UTC.

`/v1/coverage/passes` acepta `port_id`, `sensor`, `start`, `end`, `recipe_version` y `min_observable_fraction` (mayor que cero y hasta uno; 0,9 por defecto). Devuelve `items` por puerto/sensor, `zones` por zona/sensor, filtros, periodo y huellas de las fuentes. El inicio predeterminado es 2025-01-01 UTC y el fin es la consulta actual. No incluye adquisiciones auxiliares de contexto.

Una pasada suficiente para el puerto debe superar la fracción pedida en **cada zona configurada de esa misma pasada**. Se agrupan los granules ópticos por satélite, instante y órbita relativa; las partes radar por satélite y órbita absoluta. El grupo `combined` reúne oportunidades de ambos sensores y conserva sus instantes individuales. No suma detecciones ni fusiona imágenes de fechas distintas.

La consulta usa uniones de máscaras previamente calculadas y verificadas en `data/coverage/pass-unions-v1.json` cuando coinciden geometría, observaciones, receta y activos registrados. Una revisión que rechace o marque no observable una fuente invalida su uso. Sin una unión vigente, usa el máximo individual como cota conservadora; `conservative_sufficiently_observed_passes` conserva esa comparación. La API no lee rasters ni inicia procesamiento para regenerar el archivo. [Método y comando](coverage.md).

Los intervalos son diferencias entre capturas útiles reales, no pronósticos. Los intervalos entre días UTC distintos y los huecos inicial/final del periodo se entregan por separado. Menos de dos capturas produce intervalo nulo. El porcentaje de área y las métricas de frecuencia no evalúan precisión de detección.

Observaciones, detecciones, cambios y trabajos aceptan `limit` entre 1 y 500, y `cursor`. Repite los mismos filtros al seguir `next_cursor`; cambiar filtros con un cursor anterior produce 422. El orden es ascendente por fecha y un identificador estable. La última página devuelve `next_cursor: null`. Modelos, puertos, cobertura e historial no usan este cursor.

Las listas usan `items`; detecciones usa `features`. Las detecciones y sus geometrías se expresan en WGS84, orden longitud/latitud. Una vista previa puede incluir `metadata.wgs84_bounds` como `[oeste, sur, este, norte]`; los `bounds` nativos deben interpretarse con el `crs` registrado.

## Semántica de resultados

Una observación representa una adquisición, zona y receta de procesamiento. `detection_count` es el conteo producido por el modelo. `visible_detection_count` refleja detecciones actuales no rechazadas, incluidas las añadidas manualmente. Una escena no observable devuelve conteo nulo. Un cero en una escena utilizable representa cero detecciones del modelo, sin demostrar ausencia absoluta de buques.

`observable_fraction` estima la fracción de la zona observable a la resolución de la máscara. El worker combina la proporción de píxeles observables dentro de la zona con el área geométrica que el recorte realmente cubre. Los estados `usable`, `partial` y `unusable` describen esa observabilidad, no la precisión del detector. Las observaciones ya guardadas conservan el cálculo de su ejecución; actualizar el software no modifica retrospectivamente su evidencia.

`detection_score` no equivale a precisión validada. Los atributos sin soporte suficiente permanecen nulos y una anotación manual no recibe score inventado. Las revisiones no convierten automáticamente un modelo en validado. Los rechazos se ocultan de las consultas ordinarias; usa `include_rejected=true` para revisión.

Estos resultados no expresan llegadas, salidas, carga, TEU, identidad de buques ni actividad entre adquisiciones. Las geometrías portuarias actuales son provisionales y no son cartas de navegación.

## Revisiones autenticadas

Los POST requieren `Authorization: Bearer <credencial-local>` y el servidor debe tener `PUERTOGT_REVIEW_TOKEN`. Sin configuración responde 503; una credencial incorrecta produce 401. Las peticiones de navegador deben mantener el mismo origen. La lectura local no requiere token.

`POST /v1/review-queue/geometry` recibe `queue_id`, `observation_id`, `asset_id`, `kind` (`area` o `candidate`) y `rectangle: [left, top, right, bottom]`, normalizado entre 0 y 1 respecto de la imagen. Convierte con el CRS y bounds nativos congelados; para un área interseca la zona. Devuelve `geometry` WGS84 y `pixel_geometry`, sin persistir hallazgos.

`POST /v1/review-queue/first-pass` recibe identidad de cola/observación, `inspection_id` UUID, `author`, `reviewer_kind`, `decision` (`candidates`, `empty`, `uncertain`), `candidates`, `reviewed_area`, `note` y `elapsed_seconds`. Un candidato requiere un área explícita y su centro debe quedar dentro; incertidumbre requiere una nota. Escribe un recibo inmutable en `review-intake/`, separado de etiquetas y revisiones SQL. Repetir el mismo UUID y cuerpo devuelve el mismo recibo; cambiar el contenido produce 409. La interfaz reutiliza exactamente el cuerpo tras un resultado de red ambiguo. Los indicadores `is_training_label` e `is_independent_validation` permanecen falsos. [Flujo de revisión](review-queue.md).

`/v1/system` añade `freshness.items`, con búsqueda completa verificable, capturas catalogadas/procesadas/útiles y pendientes por puerto/sensor. Sus definiciones están en [actualización incremental](refresh.md). El panel V2 nunca sustituye `/v1/ports` ni reinterpreta observaciones V1.

`POST /v1/reviews/scope-preview` recibe `observation_id` y `geometry` (Polygon o MultiPolygon WGS84). Aunque usa POST y requiere credencial, es una operación de lectura: devuelve `reviewed_area`, `clipped`, `zone_id`, `observation_id` y `previous_revision_id` sin guardar una revisión. Interseca el dibujo con la zona configurada del puerto; conserva huecos y partes desconectadas, y elimina intersecciones de borde sin superficie. Exige una escena `usable` o `partial` y una intersección válida con área positiva. No consulta píxeles ni decide si el área está libre de nubes o buques.

Para guardar el resultado, envía una revisión de observación con `reviewed_area`, `scene_complete: false`, `review_status: provisional` y el `previous_revision_id` devuelto. El servicio vuelve a comprobar que el área queda dentro de la zona antes de escribir. Una revisión concurrente produce 409. Especificar `reviewed_area` sin `scene_complete` fuerza `false`, y ese valor efectivo queda en la auditoría; no hereda la completitud del ámbito anterior. Las importaciones que revisaron toda el área pueden declarar explícitamente `scene_complete: true` en la misma corrección. La exportación sigue rechazando áreas fuera de zona o propuestas sin resolver.

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
| `observation` | `status` únicamente para marcar `unusable`, `observable_fraction`, `dataset_split`, `split_groups`, `scene_complete`, `reviewed_area`. |
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
