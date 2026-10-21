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
- Las tareas se separan por archivos para trabajo paralelo autorizado; los gates científicos siguen vigentes antes de carga masiva/publicación validada.
- Commits y push autorizados como parte de publicar el repositorio. No se publica output/tmp ni secretos.
- Las geometrías provisionales, muestras sintéticas y estados sin validar se identifican explícitamente.

## Cierre de implementación y límites

Las casillas de implementación verifican software y evidencia operativa. No certifican completitud científica de T1–T9. Las comprobaciones RED que no cuentan con un registro conservado permanecen sin marcar; no se reconstruyó evidencia retrospectiva.

Se ejecutaron pilotos numéricos S1/S2 en los tres puertos, máscaras ópticas, entrenamiento y exportación de dos detectores con muestras públicas, encoder real Satlas y restauración de un volumen real. La restauración verificó 270 archivos, 22 observaciones y volvió a inferir con ambos detectores usando únicamente rutas restauradas. El [estado por tarea](status.md) separa esos resultados de lo pendiente y enlaza sus informes.

La revisión independiente detectó y corrigió conflictos de adquisición compartida entre puertos, identidad insuficiente de procesamiento, cálculo de cobertura parcial, exportación de metadatos necesarios para restaurar, conservación de procedencia por campo, filtrado por zona revisada y atomicidad de exportación. Las regresiones correspondientes están en la suite. El CLI expone entrenamiento y evaluación de detección, atributos y cambios; [los flujos](learning-workflows.md) documentan sus entradas.

El inventario completo incluye 828 adquisiciones desde 2025; cuatro tienen observaciones piloto y 824 siguen sin programar. Faltan etiquetas locales independientes, pesos locales validados, evaluación ≥90% por el protocolo y una proyección de zonas completas. `doctor --check-release` y el gate de backfill siguen cerrados por esa evidencia ausente. La autorización para publicar código está resuelta; no equivale a aprobar métricas no medidas.

El lint estructural pasó antes de ejecutar. Al cerrar, ese lint rechaza las casillas marcadas porque está diseñado para planes sin iniciar; las marcas se conservan como registro de ejecución.
