# Satélite Portuario

Observatorio local de Puerto Quetzal, Santo Tomás de Castilla y Puerto Barrios. Inventaría adquisiciones Sentinel-1 y Sentinel-2, procesa recortes numéricos, conserva evidencia y permite revisar propuestas de buques y cambios físicos.

**Versión experimental.** El servicio procesa imágenes reales de ambos sensores, conserva revisiones y entrena pesos propios. Todavía no hay precisión local de detección ≥90% demostrada ni atributos o cambios físicos validados para estos puertos. Un conteo cero no confirma ausencia. [Estado y evidencia](docs/status.md).

<!-- BEGIN DYNAMIC_STATUS -->
La campaña original de 824 adquisiciones terminó: **818 completas y seis excepciones radar sin contexto compatible, sin tareas pendientes**. La [auditoría del 26 de octubre UTC](reports/history/closure-20261026.md) verificó 121.451 archivos y reparó la evidencia necesaria para repetir tres capturas tempranas. Las cuatro capturas piloto también se completaron mediante 24 observaciones nuevas, conservando sus 22 revisiones originales. Después se procesaron cinco adquisiciones nuevas. El [cierre global](reports/history/global-closure-20261026.json) concilia **827 adquisiciones procesadas + seis excepciones, cero pendientes y 5.018 observaciones**; verifica 122.894 archivos registrados, unos 31,06 GB. Se conservan 4.996 pares adquisición/zona distintos; las versiones del piloto no cuentan como visitas adicionales.

El visor incorpora [cobertura por pasada](docs/coverage.md), área mínima por zona e intervalos medidos. Unir partes compatibles de la misma pasada recuperó 32 oportunidades suficientes en Puerto Barrios y 34 en Santo Tomás dentro del corte inicial. Esas ganancias se solapan entre puertos; no son nuevas órbitas.

La [calibración óptica](reports/local-review/phase2/candidate-calibration-v1/README.md) conserva su resultado: **ninguno de 90 umbrales cumplió los requisitos**. A score 0,50 e IoU 0,50: 73 TP / 34 FP / 40 FN, precisión 68,22% y recall 64,60%. TEST del candidato sigue reservado. El ajuste de TRAIN de 94,12% no es validación independiente.

Los [atributos públicos](reports/public-attributes/experiment-result-v1.json) y [cambios OSCD](reports/change/public-data/oscd-bounded-results.md) conservan sus evaluaciones fallidas. Ningún resultado habilita publicación automática ni demuestra precisión local ≥90%.
<!-- END DYNAMIC_STATUS -->

## Paquete experimental

La versión de software **0.3.0** incorpora cobertura por pasada, recortes completos de los pilotos y campañas incrementales explícitas. Su [verificación y alcance](reports/release-v0.3.0/README.md) documentan 306 pruebas con ML, instalación aislada y visor. La [entrega anterior](reports/release-v0.2.0/README.md) conserva su copia local restaurada; el repositorio publica código y recibos. Las imágenes numéricas y los pesos posteriores al piloto público permanecen locales.

El código y el [piloto portable v0.1.0](https://github.com/yehosuah/satelite-portuario/releases/tag/v0.1.0) están publicados. **Ese paquete es el piloto inicial, no una copia del estado actual.** Contiene imágenes, catálogo y cuatro modelos iniciales: sus 270 archivos fueron restaurados y verificados. Los 39 archivos públicos, incluidas las 34 partes del snapshot, se comprobaron. Las revisiones, pesos y campaña posteriores requieren una nueva entrega. Consulta la [verificación de publicación](reports/pilot/release-upload-status.json) y las [instrucciones de restauración](docs/restore-pilot.md).

## Arranque local

Usa Python 3.11, Git y `uv`. La instalación base permite consultar y revisar; ML requiere los extras indicados abajo.

```sh
git clone https://github.com/yehosuah/satelite-portuario.git
cd satelite-portuario
uv sync --frozen --no-editable --python 3.11
uv run --no-sync puertogt init
uv run --no-sync puertogt import-scenes reports/pilot/inventory.json
uv run --no-sync puertogt serve
```

Abre [localhost:8000](http://127.0.0.1:8000/). La instalación base sirve el catálogo, la API y el visor; las imágenes y los pesos se obtienen con los comandos explícitos del [piloto](reports/pilot/inference.md) o mediante un bundle exportado. La importación del inventario solo incorpora metadatos.

Para procesamiento y entrenamiento:

```sh
uv sync --frozen --no-editable --extra ml --extra quality
uv run --no-sync puertogt --help
```

La revisión escrita requiere `PUERTOGT_REVIEW_TOKEN` en el entorno del servidor. `PUERTOGT_DATA_DIR` selecciona el volumen y `PUERTOGT_CONFIG_DIR` la configuración. [Instalación, Docker, credencial local y restauración](docs/installation.md).

Para continuar una instalación existente, conserva su volumen y consulta [la secuencia de reanudación](docs/status.md#preparar-y-reanudar-el-sistema). No vuelvas a crear el plan ni reemplaces sus recibos. La API consulta el estado persistido y nunca inicia trabajos pesados por sí sola.

## Qué incluye

- Catálogo paginado y deduplicado, con captura/publicación/descubrimiento separados; doce zonas provisionales con fuentes registradas.
- Cola persistente de un trabajador pesado, leases, reintentos, pausas por recursos y evidencia publicada de forma atómica.
- Detectores públicos fijados por commit y SHA256, máscaras de nubes/sombras, recortes radar con contexto histórico y entrenamiento supervisado que genera pesos propios versionados.
- Revisión visual auditada, corrección de cajas, buques omitidos y datasets separados por adquisición y grupos relacionados.
- Cabezas de atributos por sensor, importador público con contexto numérico fijo y decoder siamés de cambios con entrenamiento/evaluación reales. Sus resultados experimentales no habilitan campos ni eventos validados.
- API localhost, filtros UTC, paginación, cobertura por pasada y por zona sin interpolar huecos, GeoJSON/Parquet y snapshots verificables para otro equipo.

El [protocolo de etiquetas](docs/labeling-protocol.md) y las [tarjetas de modelos](docs/model_cards/vessels.md) explican qué referencias hacen falta. El score del modelo nunca se interpreta como precisión medida. La API no inicia inferencia, descargas ni entrenamiento.

El visor permite delimitar un área de revisión sobre la imagen, previsualizar su recorte a la zona y guardarla sin darla por revisada. Los sectores excluidos permanecen desconocidos. Las [mejoras priorizadas](docs/improvements.md) explican el siguiente trabajo para aumentar las etiquetas útiles, corregir geometrías y completar la actualización incremental.

## Evidencia incluida

El inventario al **21 de octubre de 2026, 01:40:47 UTC** reúne 1,267 productos en 81 páginas, agrupados en 863 adquisiciones: **828 desde enero de 2025 y 35 de contexto anterior**. Los puertos atlánticos comparten algunas adquisiciones; sus conteos no deben sumarse para obtener el total global.

El piloto inicial tiene recortes numéricos reales en las seis combinaciones puerto/sensor y 22 observaciones de zonas. Es una prueba de operación; sus conteos no describen el catálogo histórico actual ni constituyen evaluación independiente de los puertos. [Mediciones](reports/pilot/inference-results.json), [servicio óptico](reports/pilot/service-validation.json), [servicio radar](reports/pilot/radar-service-validation.json).

Las 22 observaciones ya tienen revisión visual de IA auditada, con 19 áreas etiquetadas y cinco cajas de zona que representan cuatro observaciones de buques. Se ejecutó un primer ajuste por sensor y prueba separada: la muestra radar es insuficiente y óptico falla el criterio de IoU. [Etiquetas, protocolo y resultados](reports/local-review/README.md). La campaña resolvió las otras 824 adquisiciones: 818 completas y seis excepciones documentadas. [Cierre y conservación de evidencia](reports/history/closure-20261026.md).

La fase 2 ya entrenó sobre **51 referencias ópticas y 84 crops TRAIN**. Una [enmienda fijada antes de comparar recetas](reports/local-review/phase2/amendments/s2-train-temporal-v1-results.md) usó solo desarrollo interno de TRAIN; el refit obtuvo 48 TP / 3 FP / 3 FN a IoU 0.5 y score 0.5. Las rondas anteriores fallidas permanecen conservadas. Puerto Barrios y Santo Tomás todavía tienen soporte insuficiente.

El [diagnóstico posterior y la revisión adicional](reports/local-review/phase3-diagnosis/README.md) separan errores de tamaño de caja, incertidumbre visual y problemas del alcance geográfico. Las [propuestas de ampliación de área](reports/coverage/geography-v2-candidates/README.md) están dibujadas y cuantificadas, pendientes de revisión antes de activarse.

El [experimento público de cambios OSCD](reports/change/public-data/oscd-bounded-results.md) entrenó un decoder propio y falló TEST: **TP 0 / FP 0 / FN 9,749 píxeles**. Ese TEST ya fue consumido; no es validación portuaria ni ausencia de cambios. Se aplica la licencia conservadora del archivo fuente: investigación local, sin redistribuir sus máscaras o pesos.

Los [atributos públicos](docs/public-attributes.md) tienen 48 muestras fijadas y descarga reanudable; el estado de ejecución está en [docs/status.md](docs/status.md). Los splits originales, la procedencia por campo y los pesos del encoder se conservan. Ni el pequeño conjunto público ni una ejecución terminada demuestran precisión local del 90%.

## Verificación y documentación

```sh
uv run --no-sync pytest -q
uv run --no-sync ruff check src tests scripts
uv build
uv run --no-sync puertogt doctor
```

`doctor --check-release` devuelve código 2 mientras falte evidencia científica. Las pruebas de software que pasan no cambian ese estado.

- [API](docs/api.md), [cobertura de las pasadas](docs/coverage.md), [operación](docs/installation.md) y [entrenamiento/evaluación](docs/learning-workflows.md).
- [Plan original](docs/superpowers/plans/2026-10-20-observatorio-satelital.md) y [registro de implementación](docs/implementation-log.md).
- [Avisos de terceros y atribuciones](THIRD_PARTY_NOTICES.md). La licencia del código propio queda pendiente de decisión del titular.

Los datos de trabajo, credenciales, entornos y pesos quedan fuera de Git. Los manifiestos conservan la procedencia de datos Copernicus, modelos Ai2 y geometrías basadas en © OpenStreetMap contributors.
