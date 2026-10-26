# Consultar el piloto geográfico sin reinterpretar V1

La [V2 de 250 m](../reports/coverage/geography-v2-pilot/README.md) tiene una configuración independiente y un recibo de medición sobre TRAIN. Conserva los doce `zone_id`, pero su significado espacial también necesita la versión `2026-10-26-geography-v2-train-pilot` y el SHA256 del GeoJSON. **Permanece inactiva.**

El mecanismo mínimo de consulta es un endpoint y panel separados que lean `summary.json`, sus imágenes verificadas y `config/ports-v2.geojson`. Deben mostrar «Propuesta V2, no activa» y fallar de forma explícita si faltan los archivos o no coinciden los hashes. No deben unir esos contornos a observaciones, detecciones, revisiones ni exportaciones V1. El servicio principal sigue usando `config/ports.geojson`.

El resumen usa `schema: geography-v2-pilot-summary-v1` y entrega:

- `ports`: `v1_water_union_area_m2`, `v2_water_union_area_m2`, `additional_water_m2`, solapes, distancias muelles/canal y teselas. El aumento neto se calcula como agua V2 menos agua V1; no equivale a toda el agua añadida cuando también se excluye agua del margen costero.
- `zones`: doce filas con versión en el documento, tipo de cambio, áreas de V1/V2, tierra/agua OSM y efecto numérico de la normalización topológica.
- `train_coverage`: seis filas puerto/sensor con `train_passes`, `additional_water_observable_m2_per_pass` (`min`, `median`, `max`), `passes_with_added_water_ge90pct_observable`, `passes_with_added_water_outside_frame`, `passes_with_v2_water_outside_frame` y `maximum_v2_water_outside_frame_m2`.
- `assets`: claves `comparison.png` y `pilot.png`; cada descriptor contiene `sha256`, `size_bytes` y `mime_type`. Solo esos basenames se sirven, desde el directorio configurado explícitamente.
- `active: false`, `v1_sha256`, `v2_sha256`, `pilot_sha256` y las declaraciones de que no se abrieron CAL/TEST ni se ejecutó inferencia.

El [piloto completo](../reports/coverage/geography-v2-pilot/pilot.json) conserva el detalle por pasada y zona. `frame_pixel_count` mide encuadre retenido, `valid_pixel_count` disponibilidad numérica y `observable_pixel_count` calidad según la máscara existente; el denominador es el agua de cada geometría rasterizada en centros de píxel. Una fracción de área no es precisión de detección.

## Condiciones para procesar V2 después

El catálogo actual identifica observaciones por adquisición, zona y receta; no tiene una clave de versión geométrica independiente. API, revisiones y exportación también reciben una configuración única. Mezclar V1 y V2 en ese volumen sin ampliar estos contratos permitiría interpretar observaciones antiguas con límites nuevos.

Hay dos alternativas explícitas:

1. **Piloto aislado.** Preparar `data-v2/` con un catálogo nuevo que contenga únicamente metadatos de las adquisiciones TRAIN seleccionadas y sus contextos, sin observaciones ni revisiones V1. Copiar la configuración V2 como `data-v2/config/ports.geojson`; conservar modelos y evidencia como dependencias verificadas y con rutas propias. El servicio aislado, si se llega a iniciar, necesita otro puerto y credencial. No se ha creado ese volumen ni se ha ejecutado inferencia para él.
2. **Integración multiversión.** Registrar geometrías inmutables por versión/hash, fijar esa identidad en observaciones, trabajos, recetas, revisiones y exportaciones, y exigirla al consultar cobertura. La migración debe asignar V1 a todas las observaciones existentes. Las uniones de máscaras y los nuevos datasets necesitan sus propios hashes de geometría; cambiar solo un nombre de zona o el archivo global es insuficiente.

El primer camino permite un experimento acotado antes de una migración general. Tras preparar y verificar el volumen aislado, el siguiente comando **solo construiría un plan** con la selección TRAIN del piloto; se documenta como siguiente paso y no se ejecutó:

```sh
PYTHONPATH=src .venv/bin/python scripts/process_history.py plan-selected \
  --data-dir data-v2 --plan-dir data-v2/history/train-v2-250m \
  --config-dir data-v2/config \
  --selection-file reports/coverage/geography-v2-pilot/train-selection.json
```

No debe aplicarse ese comando al catálogo V1. Crear un plan tampoco prueba que el materializador reutilice automáticamente los archivos existentes: eso requiere una ruta de evidencia compatible, validación de hashes y una prueba de reanudación. Una futura ejecución pesada debe coordinarse con el único presupuesto de trabajo del sistema; los locks relativos a volúmenes distintos no garantizan exclusión mutua entre ambos. El siguiente experimento seguirá siendo TRAIN, y necesitará revisión de las áreas nuevas antes de producir etiquetas o entrenar.

La V2 conserva el margen costero analítico de 20 m del candidato. En STC retira aproximadamente 0,168 km² de agua además de la tierra; no representa una restricción oficial. El fondeo, los corredores y la atribución entre puertos siguen necesitando referencias actuales de autoridad portuaria antes de llamarlos límites oficiales.

## Márgenes alrededor de fondeos

La V2 anterior amplía muelles, pero no añade margen a los fondeos de Quetzal y Barrios. La [propuesta de búsqueda periférica](../reports/coverage/anchorage-search-margins/README.md) mide alternativas de 250, 500 y 1.000 m sobre agua para los tres fondeos. En Quetzal, 500 m añade 8,252 km² fuera del fondeo (7,981 km² fuera de todas las zonas V1) y cabe en el encuadre de la escena TRAIN del 7 de febrero de 2025. Las alternativas permanecen inactivas. El área de búsqueda debe separarse de la atribución al sector; un polígono provisional no determina por sí solo si el barco está fondeado.
