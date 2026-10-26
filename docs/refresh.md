# Actualización incremental y frescura

`scripts/refresh_catalog.py` conecta el catálogo paginado, el registro de metadatos, la selección exacta de nuevas adquisiciones y el procesador histórico existente. No instala una automatización. La configuración de zonas V1 sigue siendo la predeterminada; los experimentos geográficos V2 se ejecutan aparte.

## Revisar y ejecutar

Desde la raíz del repositorio, un ensayo consulta metadatos y escribe un recibo privado de descubrimiento y otro publicable. No registra adquisiciones, crea campañas, descarga píxeles ni avanza la fecha de frescura:

```sh
PYTHONPATH=src .venv/bin/python scripts/refresh_catalog.py dry-run \
  --run-id incremental-20261026 --lookback-days 90 --max-acquisitions 5 \
  --download-budget-gib 2 --acquisition-budget-gib 0.5 \
  --storage-budget-gib 100 --reserve-gib 5 --threads 4 --io-workers 4
```

`plan` vuelve a consultar con su propia fecha de corte, registra todas las novedades de metadatos y congela hasta cinco adquisiciones elegibles, ordenadas por captura y clave. Las que exceden el límite quedan registradas, sin propietario, para un nuevo ciclo. No procesa píxeles:

```sh
PYTHONPATH=src .venv/bin/python scripts/refresh_catalog.py plan \
  --run-id incremental-20261026 --lookback-days 90 --max-acquisitions 5 \
  --download-budget-gib 2 --acquisition-budget-gib 0.5 \
  --storage-budget-gib 100 --reserve-gib 5 --threads 4 --io-workers 4 \
  --output reports/refresh/incremental-20261026-plan.json
```

Revisar `selected_acquisition_keys`, `deferred_acquisitions`, las seis consultas completas y los límites del recibo. La ejecución posterior consume exclusivamente esa selección y esos límites:

```sh
PYTHONPATH=src .venv/bin/python scripts/refresh_catalog.py run \
  --run-id incremental-20261026 \
  --output reports/refresh/incremental-20261026-result.json
```

`refresh` combina `plan` y `run` con los mismos argumentos y límites explícitos. Es apropiado después de revisar la política incremental. `--port` y `--sensor` acotan las consultas; por defecto son tres puertos × dos sensores. `--end` permite fijar un corte exclusivo con zona horaria. Cada nueva búsqueda usa un identificador nuevo; repetir un identificador conserva la ventana original y rechaza parámetros distintos.

## Reanudación y límites

Repetir `run --run-id ...` continúa el mismo ledger. Una adquisición completa o no disponible no vuelve a procesarse. Reintentos transitorios conservan intentos, espera y bytes reservados. Si solo falla la reconstrucción de cobertura, el siguiente intento reconstruye cobertura sin repetir inferencia. Un estado `pending` o `coverage_incomplete` no se presenta como éxito final.

El bloqueo `refresh/refresh.lock` excluye otros ciclos incrementales simultáneos; el procesador reutiliza el bloqueo compartido `.heavy-worker.lock` por adquisición. La reconstrucción de cobertura también exige ese bloqueo. La concurrencia se limita a cuatro hilos CPU y cuatro lectores de rangos. Los límites predeterminados son 2 GiB por campaña, 0,5 GiB por adquisición, 100 GiB en el volumen y 5 GiB de reserva libre. Son límites de transferencias reservadas, no una estimación del tamaño final de los productos. Distintas campañas tienen presupuestos propios: no existe un presupuesto acumulado de red entre campañas.

Si una condición de recursos deja adquisiciones `paused`, corregir esa condición y utilizar `run --resume-paused` con el mismo identificador. Esto no aumenta límites ni borra intentos o consumo. Agotar un presupuesto congelado requiere una decisión explícita; crear otro identificador no recupera ni reprocesa las adquisiciones de esa campaña. El archivo `PAUSE` del procesador sigue disponible y debe retirarse deliberadamente para continuar. Los fallos definitivos siguen visibles en el ledger.

Toda adquisición que ya pertenece a cualquier campaña queda excluida, independientemente de su estado. Así se preservan las seis excepciones del histórico y se evita duplicar trabajo pendiente. Cualquier adquisición con observaciones previas queda protegida, incluidos los cuatro pilotos. No se alteran sus filas, revisiones, etiquetas ni datasets. Las filas existentes del catálogo se conservan; un metadato sin observaciones ni campaña puede recibir exclusivamente el marcador `public_history` necesario para su primera planificación.

Una búsqueda incompleta no registra resultados parciales ni avanza el watermark. Reintentar el mismo plan conserva las consultas completas y repite solamente las fallidas. Una vez completo, el descubrimiento y su hash quedan congelados. Un ledger histórico ilegible bloquea la planificación para evitar duplicados. Los hashes de zonas, selección, descubrimiento, plan y límites se comprueban antes de ejecutar; la configuración de modelos también se verifica y los pesos mantienen las comprobaciones existentes.

## Recibos y frescura

Archivos locales por ejecución:

- `data/refresh/runs/<id>/request.json`: parámetros y hashes congelados.
- `discovery.json`: evidencia privada del catálogo, productos, páginas, membresía y errores tipados.
- `selection.json`: selección exacta, exclusiones y novedades de registro.
- `receipt.json`: recibo publicable con conteos, claves, hashes, límites, estados y resumen de cobertura; sin URLs de origen, rutas privadas ni texto arbitrario de errores.
- `processing.log`: diagnósticos privados del procesador, separados de la consola y del recibo público.
- `data/history/refresh-<id>/`: plan y ledger reanudables. Estos archivos son privados porque el plan incluye origen y rutas.
- `data/refresh/previews/<id>/`: ensayos separados de las ejecuciones.

`--output` copia exclusivamente el recibo publicable. Un error de consola muestra su tipo; el recibo persistido conserva la fase interrumpida. Código de salida 0 significa ensayo/plan disponible o ejecución completa; 2 indica error o trabajo pendiente. Un ciclo sin adquisiciones nuevas también reconstruye cobertura.

```sh
PYTHONPATH=src .venv/bin/python scripts/refresh_catalog.py status --run-id incremental-20261026
```

`/v1/system` expone `freshness.items`, con una fila por puerto/sensor:

| Campo | Significado |
| --- | --- |
| `last_complete_search_at` | Fecha de finalización de una búsqueda completa para el ámbito solicitado; `null` si no existe recibo verificable de este circuito. |
| `searched_capture_start`, `searched_capture_end_exclusive` | Ventana de capturas consultada, conservada junto al watermark. |
| `last_catalog_capture_at` | Captura más reciente registrada para ese puerto/sensor. |
| `last_processed_capture_at` | Captura más reciente con una observación terminada, incluso inutilizable. |
| `last_processed_at` | Última fecha efectiva de procesamiento de una observación terminada. |
| `last_useful_capture_at` | Captura más reciente con al menos una zona no rechazada, usable/parcial y fracción observable ≥0,90. No certifica toda el área del puerto ni precisión de detección. |
| `pending_acquisitions` | Adquisiciones sin resultado terminado o con campaña/trabajo pendiente; excluye las declaradas no disponibles. |
| `unavailable_acquisitions` | Excepciones de fuente sin una campaña completada para esa adquisición. No representan cero buques. |

Una consulta completa es completa dentro de sus filtros y de su ventana. Publicaciones tardías fuera de los 90 días requieren un inventario explícito más amplio; `--lookback-days` admite hasta 730 días. Una búsqueda fallida posterior conserva la fecha de la última completa. Los recibos anteriores del histórico no se reinterpretan como búsquedas de este nuevo circuito.

La validación automatizada usa catálogos y ledgers sintéticos; no procesa ni cambia el corpus científico local. La primera ejecución real debe coordinarse con otros trabajos que empleen el bloqueo compartido.
