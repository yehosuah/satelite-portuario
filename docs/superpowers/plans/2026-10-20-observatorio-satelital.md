# Observatorio satelital: plan de construcción

> **Para quien implemente:** ejecutar por etapas con `superpowers:executing-plans`, verificando cada salida antes de avanzar. Este archivo no autoriza comenzar la implementación.

**Objetivo:** entregar un servicio local instalable, con modelos especializados propios, observaciones e imágenes de tres puertos para 2025 y 2026 hasta el corte de carga, más actualización futura.

**Arquitectura:** catálogo y cola persistentes en SQLite; imágenes y pesos en un volumen local; trabajadores de ML separados de FastAPI. Entrenamiento nativo y ruta de inferencia CPU portable; tres tareas de producto: detección, atributos y cambios físicos.

**Tecnologías:** Python, PyTorch, rslearn/rslearn_projects, FastAPI, SQLite, Rasterio/GDAL, PyProj, Shapely, GeoJSON y Parquet. Las versiones exactas se fijarán tras comprobar compatibilidad en el piloto, mediante lockfile y hashes.

**Diseño que rige este plan:** [Servicio satelital](../specs/2026-10-20-observatorio-satelital-design.md).

## Restricciones globales

- Tres puertos: Quetzal, Santo Tomás de Castilla y Puerto Barrios.
- Solo fuentes gratuitas, sin acuerdos institucionales ni nueva capacidad de pago.
- Datos e imágenes; modelos especializados con pesos propios.
- Meta inicial de precisión de detección ≥90%; medir omisiones y desglosar resultados.
- Familias amplias; indeterminado y dimensiones nulas cuando falte evidencia.
- Cambios sin clasificación de causa; persistencia con t0 de referencia y t1/t2 con cambio.
- Histórico 2025 y 2026 hasta la última adquisición disponible al ejecutar; huecos documentados.
- Publicación provisional con trazabilidad; cruce oficial/AIS fuera del servicio.
- Primera instalación local, API localhost, copia del histórico y paquete instalable para el equipo.
- Sin comenzar construcción, descargar imágenes o entrenar por la sola existencia de este plan.

## 1. Estructura prevista

Los siguientes archivos son futuros; no se crean en esta entrega de planificación.

```text
pyproject.toml                 # dependencias, herramientas y comandos
uv.lock                        # entorno fijado tras el piloto
Dockerfile                     # API y trabajador CPU
compose.yaml                   # volumen, API y trabajador local
.env.example                   # nombres de variables, sin secretos
config/ports.geojson           # puertos/zonas y versiones
config/runtime.yaml            # recursos, cuotas y almacenamiento
config/models/                 # recetas fijadas por tarea y sensor
src/puertogt/
  contracts.py                 # tipos de intercambio y estados
  cli.py                       # acciones locales explícitas
  catalog.py                   # búsqueda y deduplicación de adquisiciones
  assets.py                    # descargas, cache, hashes y activos
  preprocess.py                # adaptadores numéricos y máscaras
  store.py                     # persistencia y migraciones SQLite
  jobs.py                      # cola, reintentos y publicación atómica
  ml/detection.py               # adaptadores de detectores por sensor
  ml/attributes.py              # familias y dimensiones por sensor
  ml/change.py                  # segmentación temporal
  ml/training.py                # ajuste y exportación de pesos
  ml/evaluation.py              # métricas y política de publicación
  review.py                    # correcciones y dataset revisado
  api.py                       # consultas y revisión autenticada
  export.py                    # GeoJSON/Parquet y copia portable
  web/                         # pantalla local de revisión
tests/                         # pruebas de invariantes del servicio
reports/pilot/                 # medidas, ejemplos y condiciones del piloto
docs/installation.md           # instalar, ejecutar y restaurar
docs/api.md                    # contrato y ejemplos
docs/model_cards/              # métricas, límites y licencias por versión
```

`data/` será un volumen configurable fuera del código: catálogo, recortes, evidencia, datasets, modelos y temporales. Nunca se incluirá todo el histórico en Git.

## 2. Contratos entre módulos

Los tipos se definirán en `contracts.py`; las funciones consumidoras usarán estos nombres de forma consistente.

| Contrato | Contenido mínimo |
|---|---|
| `Scene` | `scene_id`, `acquisition_key`, `sensor`, `product_level`, `captured_at`, `footprint`, `source_assets`. |
| `Observation` | `observation_id`, `acquisition_key`, `zone_id`, `recipe_version`, `status`, `observable_fraction`, `quality_flags`. |
| `RasterBundle` | rutas de bandas numéricas/máscaras, CRS, transformación, unidades y versión de preprocesamiento. |
| `Prediction` | geometría, `detection_score`, familia opcional, dimensiones opcionales, calidad por campo y evidencias. |
| `ChangeEvent` | geometría, referencia, evidencia t1/t2, intervalo, estado y modelo. |
| `ModelManifest` | hashes, receta, dataset/split, métricas, taxonomía, alcance de publicación y licencias. |
| `PortableBundle` | snapshot de catálogo, activos/modelos, configuración sin secretos y checksums. |

Interfaces previstas:

```python
discover(port_id: str, start: datetime, end: datetime) -> list[Scene]
prepare(scene: Scene, zone_id: str, recipe_version: str) -> RasterBundle
detect(bundle: RasterBundle, model: ModelManifest) -> list[Prediction]
predict_attributes(bundle: RasterBundle, detections: list[Prediction],
                   model: ModelManifest) -> list[Prediction]
compare(reference: RasterBundle, target: RasterBundle,
        model: ModelManifest) -> list[ChangeEvent]
enqueue(scene: Scene, zone_id: str, recipe_version: str) -> str
record_review(entity_id: str, correction: dict, author: str) -> str
export_bundle(destination: Path) -> PortableBundle
```

Estos contratos no fijan todavía operaciones internas de rslearn. El piloto resolverá esa compatibilidad y dejará la receta exacta, sin cambiar el contrato externo por proveedor.

## 3. Etapas ejecutables

### Tarea 1. Inventario, geometrías y ensayo de recursos

**Archivos:** crear `contracts.py`, `catalog.py`, `config/ports.geojson`, `reports/pilot/inventory.json`, `reports/pilot/resources.json`, `tests/test_catalog.py`.

**Entrada:** decisiones del diseño y muestras de catálogo existentes. **Salida:** `Scene`, áreas versionadas e inventario paginado de 2025/2026 por nivel de producto.

- [ ] Definir áreas de muelles, fondeo, canal y patios con evidencia cartográfica; resolver atribución entre Santo Tomás y Barrios.
- [ ] Inventariar S1 IW GRDH VV/VH y S2 L1C; separar contexto radar anterior a 2025 de observaciones publicables.
- [ ] Crear una clave de adquisición que agrupe representaciones SAFE/COG sin agrupar tomas distintas.
- [ ] Registrar paginación, proveedor, producto, nivel, fechas y huella; no usar cantidad de productos como cantidad de observaciones.
- [ ] Medir equipo, espacio disponible y capacidad real del entorno nativo/CPU/MPS. Probar Docker sin asumir aceleración GPU.
- [ ] Guardar manifiesto de versiones candidatas, licencias y checkpoints; no descargar datasets masivos.

**Verificación:** fixture con dos representaciones de una toma y una toma posterior; debe producir dos claves, no tres. Otro fixture con dos páginas debe recuperar ambas. Ejecutar `pytest tests/test_catalog.py -q`.

**Salida de etapa:** inventario reproducible y muestra elegida por puerto/sensor/zona. La cobertura del catálogo no se presenta como cobertura utilizable.

### Tarea 2. Entrada numérica compatible y primera inferencia

**Archivos:** crear `assets.py`, `preprocess.py`, `ml/detection.py`, `config/models/s1.yaml`, `config/models/s2.yaml`, `tests/test_preprocess.py`, `reports/pilot/inference.md`.

**Entrada:** `Scene`. **Salida:** `RasterBundle`, detecciones base y medidas reales por recorte.

- [ ] Fijar versiones por commit/hash y descargar únicamente pesos y escenas necesarios para el ensayo.
- [ ] Reproducir una entrada conocida del modelo público; registrar bandas, unidades, normalización y correcciones.
- [ ] Verificar compatibilidad de radar crudo frente a productos calibrados; rechazar recetas incompatibles.
- [ ] Crear máscaras locales de observabilidad para L1C y comprobarlas visualmente; separar datos de inferencia y PNG.
- [ ] Ejecutar detección en zonas abiertas y junto al muelle; medir el efecto del filtro de infraestructura sin asumir que quitarlo mejora calidad.
- [ ] Medir RAM máxima, tiempo, almacenamiento y consumo de API; hacer un paso de entrenamiento con encoder congelado.
- [ ] Proyectar el coste del histórico con los recortes reales y fijar límites operativos antes de continuar.

**Verificación:** una banda ausente o unidad inesperada debe bloquear el trabajo, no generar resultados. Para una entrada válida, guardar hash, resumen numérico y geometría resultante; revisar evidencia y coordenadas. Ejecutar `pytest tests/test_preprocess.py -q` y repetir el ejemplo fijado.

**Salida de etapa:** receta reproducible, límites medidos y evidencia de que inferencia y ajuste inicial son operativos. Si no lo son, detener carga masiva y presentar alternativas dentro de las restricciones.

### Tarea 3. Catálogo persistente, cola y recuperación

**Archivos:** crear `store.py`, `jobs.py`, `config/runtime.yaml`, `tests/test_jobs.py`.

**Entrada:** `Scene`, zona y receta. **Salida:** trabajos identificados y observaciones recuperables.

- [ ] Definir tablas de adquisiciones, observaciones, trabajos, activos, modelos y revisiones con migración inicial.
- [ ] Hacer única la combinación adquisición/zona/receta y publicar archivos completos de forma atómica.
- [ ] Recuperar trabajos interrumpidos al reiniciar; distinguir pendientes, fallos transitorios y fallos de entrada.
- [ ] Limitar a un trabajador pesado, respetar cuotas y pausar por espacio insuficiente.
- [ ] Guardar fechas de captura, publicación, descubrimiento y procesamiento por separado.

**Prueba de aceptación prevista:**

```python
def test_retry_keeps_one_job(scene, job_store):
    first = job_store.enqueue(scene, "prq_muelles", "s1-v1")
    second = job_store.enqueue(scene, "prq_muelles", "s1-v1")
    assert first == second
    assert job_store.count_jobs() == 1
```

Añadir un ensayo de interrupción antes de publicar el archivo: al recuperar no debe aparecer un activo parcial como utilizable. Ejecutar `pytest tests/test_jobs.py -q`.

### Tarea 4. Revisión local y dataset propio

**Archivos:** crear `review.py`, `web/`, `tests/test_review.py`, `reports/pilot/label_protocol.md`.

**Entrada:** propuestas y evidencia; etiquetas públicas documentadas. **Salida:** correcciones auditadas y versiones de dataset.

- [ ] Implementar revisión visual de recuadros, familias y pares antes/después, con atajos y guardado por acción.
- [ ] Conservar propuesta y corrección; permitir añadir buques omitidos y marcar no observable.
- [ ] Registrar procedencia de cada atributo; no rellenar tipo o dimensiones sin referencia suficiente.
- [ ] Muestrear escenas completas independientes de las propuestas para poder medir recall.
- [ ] Construir grupos de separación por tomas relacionadas, solapamiento y buque conocido; congelar prueba local.
- [ ] Exportar dataset rslearn compatible y manifiesto con splits/hashes.

**Verificación:** corregir una detección, recargar la pantalla y comprobar conservación y auditoría. Un ejemplo reservado para prueba debe quedar fuera de la exportación de entrenamiento. Ejecutar `pytest tests/test_review.py -q` y recorrer una escena real de cada puerto.

### Tarea 5. Detectores y atributos especializados

**Archivos:** crear `ml/attributes.py`, `ml/training.py`, `ml/evaluation.py`, `config/models/attributes-s1.yaml`, `config/models/attributes-s2.yaml`, `docs/model_cards/vessels.md`, `tests/test_evaluation.py`.

**Entrada:** dataset versionado y recetas verificadas. **Salida:** pesos propios y `ModelManifest` por tarea/sensor.

- [ ] Medir las bases públicas con el protocolo local antes de ajustar.
- [ ] Ajustar detectores por sensor con encoder congelado y negativos difíciles; evaluar antes de descongelar más capas.
- [ ] Adaptar familias y entrenar atributos solo con etiquetas válidas; enmascarar la pérdida donde falte un atributo.
- [ ] Calibrar umbrales en calibración, sin consultar el conjunto de prueba para escogerlos.
- [ ] Medir precisión/recall por puerto, sensor y zona, con tamaños de muestra e incertidumbre.
- [ ] Medir clasificación con abstenciones y dimensiones contra referencias, incluyendo comparación simple y error por tamaño.
- [ ] Registrar alcance validado y política por campo; conservar pesos anteriores y manifiesto para reversión.

**Prueba del cálculo de precisión, usando `precision_at_counts` definida en `ml/evaluation.py`:**

```python
def test_precision_counts_false_reports():
    assert precision_at_counts(true_positives=90, false_positives=10) == 0.90
    assert precision_at_counts(true_positives=0, false_positives=0) is None
```

Además de pruebas de cálculo, ejecutar evaluación real congelada. Alcanzar score ≥0.90 no sustituye medir precisión ≥0.90. Si falla una tarea o estrato, documentarlo y resolver la decisión de salida; no usar el promedio para ocultarlo.

### Tarea 6. Cambios físicos y persistencia

**Archivos:** crear `ml/change.py`, `config/models/change.yaml`, `docs/model_cards/changes.md`, `tests/test_changes.py`.

**Entrada:** pares ópticos comparables, máscaras y etiquetas revisadas. **Salida:** `ChangeEvent` candidato/persistente con evidencia.

- [ ] Entrenar el decodificador con encoder Satlas compartido y congelado inicialmente; incluir pares sin cambio y condiciones distintas.
- [ ] Rechazar comparaciones sin alineación/visibilidad suficientes; excluir regiones temporales o inválidas.
- [ ] Evaluar eventos y polígonos, medir superficie mínima útil y falsos avisos; documentar aporte complementario radar.
- [ ] Implementar t0/t1/t2 y reversión posterior sin perder el historial.

**Verificación:** t0 sin cambio y t1 con cambio producen candidato; t2 válido con cambio produce persistente. Una escena nublada entre t1 y t2 no confirma persistencia. Ejecutar `pytest tests/test_changes.py -q` y revisar eventos reales y pares negativos.

### Tarea 7. API, imágenes y exportaciones

**Archivos:** crear `api.py`, `export.py`, `cli.py`, `tests/test_api.py`, `docs/api.md`.

**Entrada:** observaciones, activos y modelos registrados. **Salida:** rutas v1 del diseño, GeoJSON/Parquet e imágenes consultables.

- [ ] Implementar consultas paginadas con periodo `[start, end)` en UTC y versiones explícitas.
- [ ] Separar estados de procesamiento, revisión y cambio; mostrar calidad por campo.
- [ ] Servir activos registrados con georreferenciación/atribución; bloquear rutas arbitrarias.
- [ ] Proteger revisión; mantener ingesta y trabajos pesados en CLI/trabajador.
- [ ] Implementar cobertura mensual que distinga catálogo, procesadas, utilizables y pendientes.

**Verificación:** escena inutilizable devuelve conteo nulo; escena utilizable sin detecciones devuelve cero del modelo. Comprobar paginación sin duplicados, acceso a evidencia, exclusión de rechazados y ausencia de secretos. Ejecutar `pytest tests/test_api.py -q` y una consulta local con datos reales.

### Tarea 8. Histórico y actualización continua

**Archivos:** ampliar `cli.py`, `jobs.py`, `export.py`; crear `reports/history/coverage.parquet`, `reports/history/exceptions.json`, `tests/test_backfill.py`.

**Entrada:** inventario, modelos con política de publicación y presupuesto medido. **Salida:** histórico 2025/2026, cobertura y actualización idempotente.

- [ ] Cargar por periodos pequeños reanudables; priorizar evidencia completa de cada observación antes de avanzar.
- [ ] Registrar una razón para cada adquisición no procesada; no ocultar faltantes mediante interpolación.
- [ ] Incorporar publicaciones tardías y procesar una adquisición nueva mediante la misma cola.
- [ ] Ensayar pausa/reinicio y comprobar que no vuelven a generarse resultados para la misma versión.
- [ ] Generar corte UTC, última captura por sensor/zona y tabla mensual de cobertura.

**Verificación:** dos ejecuciones sobre el mismo intervalo deben conservar el número de observaciones para la misma versión. Una adquisición nueva debe añadir únicamente sus observaciones correspondientes. Ejecutar `pytest tests/test_backfill.py -q` y conciliar inventario contra cobertura/excepciones.

### Tarea 9. Instalación y entrega portable

**Archivos:** crear `Dockerfile`, `compose.yaml`, `.env.example`, `docs/installation.md`, `tests/test_restore.py`; generar bundle fuera de Git.

**Entrada:** servicio, histórico, pesos y manifiestos. **Salida:** paquete que otro operador puede instalar y restaurar.

- [ ] Fijar dependencias verificadas y construir ruta CPU en ARM64; comprobar x86_64 antes de declarar soporte probado para esa plataforma.
- [ ] Documentar entrenamiento nativo separado de la instalación del servicio y requisitos medidos del piloto.
- [ ] Exportar snapshot consistente, activos, modelos y checksums, excluyendo credenciales y temporales prescindibles.
- [ ] Restaurar en un directorio limpio; verificar todos los checksums y consultar API sin depender de rutas originales.
- [ ] Demostrar una observación, una evidencia antes/después, cobertura histórica y una actualización reanudable.
- [ ] Entregar licencias, límites de modelo y guía de migración a un único host interno o EC2 futuro.

**Verificación:** restauración íntegra y consulta sobre histórico real; ausencia de secretos en la copia; revisión autenticada. Ejecutar `pytest tests/test_restore.py -q` y el recorrido de instalación documentado.

## 4. Controles de salida y dependencias

| Control | Requisito para continuar |
|---|---|
| Piloto → construcción completa | Acceso gratuito y receta compatibles; recursos medidos; estrategia para etiquetas y revisión. |
| Dataset → evaluación final | Prueba congelada y referencias independientes; suficiencia documentada por tarea. |
| Modelo → publicación validada | Precisión de detección y límites de atributos/cambios medidos; manifiesto de alcance. |
| Servicio → carga masiva | Reanudación, idempotencia y proyección de almacenamiento/cuotas verificadas. |
| Histórico → entrega | Conciliación catálogo/resultados/excepciones y restauración comprobada. |

Tareas 1 y 2 resuelven incertidumbres antes del trabajo masivo. La revisión de la tarea 4 alimenta tanto buques como cambios. La tarea 3 sostiene procesamiento y API. El histórico depende de calidad y operación; no exige que todas las escenas sean utilizables.

No se asigna una duración ficticia. El primer presupuesto de esfuerzo y tiempo saldrá de bytes/escena, minutos/escena, recursos de entrenamiento y velocidad de revisión observados en el piloto.

## 5. Revisión de cobertura de este plan

| Requisito acordado | Tareas |
|---|---|
| Tres puertos y fuentes gratuitas | 1, 2, 8 |
| Pesos especializados propios | 4, 5, 6 |
| Tipos y dimensiones con límites | 4, 5 |
| Cambios y dos observaciones posteriores | 4, 6 |
| Revisión sencilla por nosotros | 4, 7 |
| Precisión objetivo 90% y omisiones | 4, 5 |
| Datos e imágenes para integración | 2, 7, 9 |
| Histórico 2025 y 2026, continuidad | 8 |
| Computadora local, sin contratar capacidad | 1, 2, 3, 9 |
| Instalación interna futura y copia del histórico | 7, 9 |
| Resultados provisionales y cruce externo | 5, 7, contrato del diseño |

El plan queda listo para revisión. La siguiente acción técnica, cuando se autorice implementar, será el piloto de las tareas 1 y 2; no la descarga completa del histórico.
