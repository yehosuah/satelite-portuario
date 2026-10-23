# Registro ejecutable de Satélite Portuario

## Objetivo
Implementar el servicio acordado y publicar su código, con evidencia verificable y sin confundir pruebas operativas con validación científica.

## Meta verificable
Servicio verificable con integridad de restauración y estado científico explícito.
Se prueba con: `uv run --no-sync pytest -q` y `uv run --no-sync puertogt doctor --check-release`.
Comando base: `uv run --no-sync pytest -q`
Línea base al empezar: repositorio documental, sin paquete ni pruebas propias; pytest sin ruta recogió pruebas upstream temporales y falló por dependencias.

## Arquitectura
Contratos Pydantic, inventario público, SQLite/archivos, trabajador pesado separado, API de consulta, revisión y modelos versionados. Cada seam se prueba con datos deterministas; el piloto registra mediciones reales.

## Restricciones globales
- «Tres puertos: Quetzal, Santo Tomás de Castilla y Puerto Barrios.» (plan aprobado, sección Restricciones globales)
- «Solo fuentes gratuitas, sin acuerdos institucionales ni nueva capacidad de pago.» (plan aprobado, sección Restricciones globales)
- «Datos e imágenes; modelos especializados con pesos propios.» (plan aprobado, sección Restricciones globales)
- «Meta inicial de precisión de detección ≥90%; medir omisiones y desglosar resultados.» (plan aprobado, sección Restricciones globales)
- «Familias amplias; indeterminado y dimensiones nulas cuando falte evidencia.» (plan aprobado, sección Restricciones globales)
- «Cambios sin clasificación de causa; persistencia con t0 de referencia y t1/t2 con cambio.» (plan aprobado, sección Restricciones globales)
- «Histórico 2025 y 2026 hasta la última adquisición disponible al ejecutar; huecos documentados.» (plan aprobado, sección Restricciones globales)
- «Publicación provisional con trazabilidad; cruce oficial/AIS fuera del servicio.» (plan aprobado, sección Restricciones globales)
- «Primera instalación local, API localhost, copia del histórico y paquete instalable para el equipo.» (plan aprobado, sección Restricciones globales)
- «Sin comenzar construcción, descargar imágenes o entrenar por la sola existencia de este plan.» (plan aprobado, sección Restricciones globales)

## Mapa de archivos
| Archivo | Acción | Responsabilidad |
|---|---|---|
| `src/puertogt/catalog.py` | crear | T1: Inventario y geometrías |
| `config/ports.geojson` | crear | T1: Inventario y geometrías |
| `tests/test_catalog.py` | crear | T1: Inventario y geometrías |
| `src/puertogt/preprocess.py` | crear | T2: Receta y piloto numérico |
| `src/puertogt/assets.py` | crear | T2: Receta y piloto numérico |
| `src/puertogt/ml/detection.py` | crear | T2: Receta y piloto numérico |
| `tests/test_preprocess.py` | crear | T2: Receta y piloto numérico |
| `src/puertogt/store.py` | crear | T3: Persistencia recuperable |
| `src/puertogt/jobs.py` | crear | T3: Persistencia recuperable |
| `tests/test_jobs.py` | crear | T3: Persistencia recuperable |
| `src/puertogt/review.py` | crear | T4: Revisión y datasets |
| `src/puertogt/web/` | crear | T4: Revisión y datasets |
| `tests/test_review.py` | crear | T4: Revisión y datasets |
| `src/puertogt/ml/attributes.py` | crear | T5: Atributos y evaluación |
| `src/puertogt/ml/training.py` | crear | T5: Atributos y evaluación |
| `src/puertogt/ml/evaluation.py` | crear | T5: Atributos y evaluación |
| `tests/test_evaluation.py` | crear | T5: Atributos y evaluación |
| `src/puertogt/ml/change.py` | crear | T6: Cambios físicos |
| `tests/test_changes.py` | crear | T6: Cambios físicos |
| `src/puertogt/api.py` | crear | T7: Servicio integrado |
| `src/puertogt/cli.py` | crear | T7: Servicio integrado |
| `tests/test_api.py` | crear | T7: Servicio integrado |
| `tests/test_backfill.py` | crear | T8: Carga y continuidad |
| `reports/history/` | crear | T8: Carga y continuidad |
| `src/puertogt/export.py` | crear | T9: Portabilidad y publicación |
| `tests/test_restore.py` | crear | T9: Portabilidad y publicación |
| `Dockerfile` | crear | T9: Portabilidad y publicación |
| `compose.yaml` | crear | T9: Portabilidad y publicación |
| `docs/installation.md` | crear | T9: Portabilidad y publicación |
| `docs/api.md` | crear | T9: Portabilidad y publicación |

## Tareas

### T1 — Inventario y geometrías (riesgo: alto — integra invariantes y evidencia)
Archivos: `src/puertogt/catalog.py`, `config/ports.geojson`, `tests/test_catalog.py`
Bloqueada por: nada
Seam de prueba: discover/normalize_products
- [ ] Comprobar conducta requerida antes de implementarla. Esperado: falla reproducible en el seam de esta tarea.
- [x] Implementar y correr `uv run --no-sync pytest tests/test_catalog.py -q`. Esperado: pruebas pasan.
- [x] Registrar evidencia real y límites en el informe correspondiente. Esperado: ninguna validación científica inferida de tests sintéticos.

### T2 — Receta y piloto numérico (riesgo: alto — integra invariantes y evidencia)
Archivos: `src/puertogt/preprocess.py`, `src/puertogt/assets.py`, `src/puertogt/ml/detection.py`, `tests/test_preprocess.py`
Bloqueada por: T1
Seam de prueba: prepare/detect
- [ ] Comprobar conducta requerida antes de implementarla. Esperado: falla reproducible en el seam de esta tarea.
- [x] Implementar y correr `uv run --no-sync pytest tests/test_preprocess.py -q`. Esperado: pruebas pasan.
- [x] Registrar evidencia real y límites en el informe correspondiente. Esperado: ninguna validación científica inferida de tests sintéticos.

### T3 — Persistencia recuperable (riesgo: alto — integra invariantes y evidencia)
Archivos: `src/puertogt/store.py`, `src/puertogt/jobs.py`, `tests/test_jobs.py`
Bloqueada por: nada
Seam de prueba: JobQueue
- [ ] Comprobar conducta requerida antes de implementarla. Esperado: falla reproducible en el seam de esta tarea.
- [x] Implementar y correr `uv run --no-sync pytest tests/test_jobs.py -q`. Esperado: pruebas pasan.
- [x] Registrar evidencia real y límites en el informe correspondiente. Esperado: ninguna validación científica inferida de tests sintéticos.

### T4 — Revisión y datasets (riesgo: alto — integra invariantes y evidencia)
Archivos: `src/puertogt/review.py`, `src/puertogt/web/`, `tests/test_review.py`
Bloqueada por: T3
Seam de prueba: record_review/export_dataset
- [ ] Comprobar conducta requerida antes de implementarla. Esperado: falla reproducible en el seam de esta tarea.
- [x] Implementar y correr `uv run --no-sync pytest tests/test_review.py -q`. Esperado: pruebas pasan.
- [x] Registrar evidencia real y límites en el informe correspondiente. Esperado: ninguna validación científica inferida de tests sintéticos.

### T5 — Atributos y evaluación (riesgo: alto — integra invariantes y evidencia)
Archivos: `src/puertogt/ml/attributes.py`, `src/puertogt/ml/training.py`, `src/puertogt/ml/evaluation.py`, `tests/test_evaluation.py`
Bloqueada por: T2, T4
Seam de prueba: evaluation/publication
- [ ] Comprobar conducta requerida antes de implementarla. Esperado: falla reproducible en el seam de esta tarea.
- [x] Implementar y correr `uv run --no-sync pytest tests/test_evaluation.py -q`. Esperado: pruebas pasan.
- [x] Registrar evidencia real y límites en el informe correspondiente. Esperado: ninguna validación científica inferida de tests sintéticos.

### T6 — Cambios físicos (riesgo: alto — integra invariantes y evidencia)
Archivos: `src/puertogt/ml/change.py`, `tests/test_changes.py`
Bloqueada por: T2, T4
Seam de prueba: compare/advance_event
- [ ] Comprobar conducta requerida antes de implementarla. Esperado: falla reproducible en el seam de esta tarea.
- [x] Implementar y correr `uv run --no-sync pytest tests/test_changes.py -q`. Esperado: pruebas pasan.
- [x] Registrar evidencia real y límites en el informe correspondiente. Esperado: ninguna validación científica inferida de tests sintéticos.

### T7 — Servicio integrado (riesgo: alto — integra invariantes y evidencia)
Archivos: `src/puertogt/api.py`, `src/puertogt/cli.py`, `tests/test_api.py`
Bloqueada por: T3, T4
Seam de prueba: HTTP/CLI
- [ ] Comprobar conducta requerida antes de implementarla. Esperado: falla reproducible en el seam de esta tarea.
- [x] Implementar y correr `uv run --no-sync pytest tests/test_api.py -q`. Esperado: pruebas pasan.
- [x] Registrar evidencia real y límites en el informe correspondiente. Esperado: ninguna validación científica inferida de tests sintéticos.

### T8 — Carga y continuidad (riesgo: alto — integra invariantes y evidencia)
Archivos: `tests/test_backfill.py`, `reports/history/`
Bloqueada por: T5, T6, T7
Seam de prueba: backfill/coverage
- [ ] Comprobar conducta requerida antes de implementarla. Esperado: falla reproducible en el seam de esta tarea.
- [x] Implementar y correr `uv run --no-sync pytest tests/test_backfill.py -q`. Esperado: pruebas pasan.
- [x] Registrar evidencia real y límites en el informe correspondiente. Esperado: ninguna validación científica inferida de tests sintéticos.

### T9 — Portabilidad y publicación (riesgo: alto — integra invariantes y evidencia)
Archivos: `src/puertogt/export.py`, `tests/test_restore.py`, `Dockerfile`, `compose.yaml`, `docs/installation.md`, `docs/api.md`
Bloqueada por: T7
Seam de prueba: export_bundle/restore_bundle
- [ ] Comprobar conducta requerida antes de implementarla. Esperado: falla reproducible en el seam de esta tarea.
- [x] Implementar y correr `uv run --no-sync pytest tests/test_restore.py -q`. Esperado: pruebas pasan.
- [x] Registrar evidencia real y límites en el informe correspondiente. Esperado: ninguna validación científica inferida de tests sintéticos.

## Registro
- Autoridad: usuario solicitó implementar el plan y luego el alcance íntegro, con subagentes y publicación pública en yehosuah/satelite-portuario. Esto resuelve la autorización posterior exigida por el documento.
- El lint del documento original reportó 17 diferencias de formato; este registro adapta la estructura sin cambiar el diseño ni las restricciones.
- Las tareas se separan por archivos para trabajo paralelo autorizado. La autorización posterior permite procesar el histórico con propuestas experimentales; los gates científicos siguen vigentes antes de declarar publicación validada.
- Commits y push autorizados como parte de publicar el repositorio. No se publica output/tmp ni secretos.
- Las geometrías provisionales, muestras sintéticas y estados sin validar se identifican explícitamente.

## Cierre de implementación y límites

Las casillas de implementación verifican software y evidencia operativa. No certifican completitud científica de T1–T9. Las comprobaciones RED que no cuentan con un registro conservado permanecen sin marcar; no se reconstruyó evidencia retrospectiva.

Se ejecutaron pilotos numéricos S1/S2 en los tres puertos, máscaras ópticas, entrenamiento y exportación de dos detectores con muestras públicas, encoder real Satlas y restauración de un volumen real. La restauración verificó 270 archivos, 22 observaciones y volvió a inferir con ambos detectores usando únicamente rutas restauradas. El [estado por tarea](status.md) separa esos resultados de lo pendiente y enlaza sus informes.

La revisión independiente detectó y corrigió conflictos de adquisición compartida entre puertos, identidad insuficiente de procesamiento, cálculo de cobertura parcial, exportación de metadatos necesarios para restaurar, conservación de procedencia por campo, filtrado por zona revisada y atomicidad de exportación. Las regresiones correspondientes están en la suite. El CLI expone entrenamiento y evaluación de detección, atributos y cambios; [los flujos](learning-workflows.md) documentan sus entradas.

El inventario completo incluye 828 adquisiciones desde 2025; cuatro tienen observaciones piloto y las otras 824 ya están programadas en una campaña reanudable de 4,907 teselas. Sus mediciones de zonas completas, presupuestos, reintentos y excepciones están en [reports/history](../reports/history/). Faltan pesos localmente validados y evaluación ≥90% por el protocolo. `doctor --check-release` conserva ese incumplimiento; no impide la campaña experimental autorizada. La autorización para publicar código no equivale a aprobar métricas no medidas.

El lint estructural pasó antes de ejecutar. Al cerrar, ese lint rechaza las casillas marcadas porque está diseñado para planes sin iniciar; las marcas se conservan como registro de ejecución.

La verificación de instalación detectó y corrigió rutas de modelos y bandas preparadas al montar un volumen en `/data` desde otro directorio de trabajo. Las regresiones observaron el fallo antes del cambio; la suite instalada posterior pasó 99 pruebas.

La CI posterior encontró sensibilidad del cálculo de cobertura a bordes sobre centros de píxel. Se reprodujeron dos fallos antes de cambiar el worker a intersección geométrica, incluida una huella inclinada. La suite instalada final pasó 101 pruebas; CI en Linux y macOS pasó 94 con siete omisiones de ML opcional. El impacto sobre el piloto se midió sin alterar su catálogo y se conserva en [coverage-correction.json](../reports/pilot/coverage-correction.json).

## Estado de publicación del paquete portable

La carga inicial tuvo fallos TCP/TLS y se reanudó sin sustituir el archivo. El [recibo final](../reports/pilot/release-upload-status.json), del 21 de octubre de 2026, confirma el release v0.1.0 publicado: 34/34 segmentos y 39/39 archivos verificados, con 836,751,228 bytes de snapshot y cero bytes pendientes. Este paquete corresponde al piloto inicial; no contiene las revisiones, pesos y campaña posteriores. No se actualizará silenciosamente para representar el estado actual.

## Desarrollo posterior al piloto

Se conservaron las revisiones iniciales y se ampliaron ámbitos temporales de fase 2. La inferencia histórica usa teselas completas con halos, deduplicación entre zonas y evidencias congeladas para replay. Se corrigió el tratamiento de áreas desconocidas en la supervisión ROI/RPN y la geometría parcial de los datasets, manteniendo las mediciones anteriores sin reescribirlas.

El detector óptico se entrenó con 51 referencias y 84 crops TRAIN. La receta original falló; una [enmienda fijada antes de comparar](../reports/local-review/phase2/amendments/s2-train-temporal-v1-results.md) separó enero y febrero dentro de TRAIN, sin usar CAL/TEST para elegir receta. El refit seleccionado obtuvo 48 TP / 3 FP / 3 FN, precisión y recall 94.12% sobre TRAIN. Es ajuste al conjunto usado para aprender; no acredita precisión local del 90%. PBR/STC siguen teniendo soporte insuficiente. Los pesos tienen identidad única y no reemplazan ni validan el modelo público.

El [experimento OSCD](../reports/change/public-data/oscd-bounded-results.md) ejecutó 30 updates reales de decoder propio y seis inferencias TEST, con encoder Satlas congelado, máscaras manuales y splits de ciudades separados. Falló: TP 0 / FP 0 / FN 9,749 píxeles; TEST queda consumido. Las imágenes rectificadas carecen de georreferencia y no se transformaron en supuestos polígonos portuarios. Se aplica la licencia más restrictiva del archivo fuente —CC BY-NC-SA/consulta comercial— frente a la discrepancia del DOI. Datos y pesos derivados se conservan para investigación local, sin publicación o validación portuaria.

El importador de [atributos públicos](public-attributes.md) conserva 48 ventanas fijadas, splits originales, procedencia por campo y generaciones de nueve bandas. Los contextos 128×128 a 10 m se construyen sin usar eslora/manga objetivo. La enmienda I/O4 cambia únicamente concurrencia de descarga; conserva selección, recetas, límites de bytes e intentos y pausa la campaña histórica para no competir por recursos. El entrenamiento/evaluación tiene receta y presupuesto prefijados, campos sin validar y TEST separado.

La revisión independiente del corte técnico pasó 56 pruebas acotadas y Ruff. Cubrió estado/doctor/API, calibración, contexto de atributos, importador, transporte y recuperación; no ejecutó modelos ni certificó exactitud científica. El panel exige `local_validation: passed` explícito para mostrar validación local declarada; un resultado público no basta.

<!-- BEGIN DYNAMIC_STATUS -->
Al corte **2026-10-23T05:03:55.799342Z**, la campaña está reanudada: **645/824 adquisiciones completas, 173 por resolver y seis excepciones de contexto radar**. El worker conserva plan, reintentos y presupuestos.

La [calibración óptica](../reports/local-review/phase2/candidate-calibration-v1/README.md) terminó: **ninguno de 90 umbrales cumplió los requisitos**. Con umbral 0,50: 73 TP / 34 FP / 40 FN, precisión 68,22% y recall 64,60%; TEST del candidato sigue cerrado. TRAIN 94,12% era ajuste, no validación. CAL contiene 105 ámbitos y 113 referencias IA (PRQ 87 / PBR 16 / STC 10), con 24 observaciones PRQ posteriores aún fuera del corte.

El [experimento público de atributos](../reports/public-attributes/experiment-result-v1.json) terminó: 48 muestras materializadas,34 TRAIN elegibles de 36, cinco CAL sin usar y siete TEST evaluadas una vez. Familias: 3/7 correctas; MAE eslora 71,75 m y manga 11,43 m. Campos y publicación automática continúan deshabilitados. OSCD público también conserva su prueba fallida. Ninguno de estos resultados demuestra precisión local ≥90%.
<!-- END DYNAMIC_STATUS -->

## Entrega nueva: comandos y condiciones

El exportador cubre catálogo, activos y pesos registrados, no todo el workspace científico. Una copia capaz de continuar la campaña necesita también sus planes, prioridades, ledger consistente, fuentes fijadas y evidencias externas, más los datasets/experimentos que se decida entregar. Esos componentes van en un complemento con checksums, fuera del bundle estricto. Detener escritores y usar snapshots SQLite evita perder WAL; las credenciales y materiales OSCD restringidos se excluyen de la publicación pública.

El [estado de entrega](status.md#preparar-una-entrega-nueva) contiene la secuencia `uv build`, exportación y restauración en destinos nuevos. Se comprobaron sus opciones mediante `--help`, sin construir ni exportar una entrega nueva en esta actualización. El corte ahora declara 0.2.0; las verificaciones reales de software, visor, restauración y continuidad se conservan en `reports/release-v0.2.0/`. Los ejemplos siguientes son instrucciones históricas de preparación, no recibos de publicación.

Para publicar solo después de esas comprobaciones, esta es la plantilla mínima; **no se ejecutó**. Sustituye los marcadores por un tag nuevo ya creado y archivos explícitos del corte. La sintaxis de `gh release create` se verificó localmente:

```sh
gh release create NUEVO_TAG ARCHIVO_WHEEL ARCHIVO_SDIST ARCHIVO_BUNDLE ARCHIVO_CHECKSUMS \
  --repo yehosuah/satelite-portuario --verify-tag --draft --prerelease \
  --title "Satélite Portuario — corte experimental" --notes-file NOTAS_DE_ENTREGA.md
```

El borrador permite verificar todos los archivos descargados y sus SHA256 antes de hacerlo público. Si el tamaño requiere partes, conserva un manifiesto del archivo completo y otro de cada parte. El paquete no incluye dependencias Python del sistema: una instalación completamente sin red necesita un entorno/contenedor y dependencias preparados además de wheel, fuentes, configuración y datos. Publicar una entrega estable de software no cierra los requisitos científicos pendientes.
