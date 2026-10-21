# Piloto numérico de detección

Se ejecutaron los dos detectores públicos sobre GeoTIFF numéricos reales de Puerto Quetzal, Santo Tomás de Castilla y Puerto Barrios. Ambos también completaron un paso supervisado con encoder congelado sobre muestras públicas. Esto prueba la operación y el consumo del programa. **No constituye validación local ni autoriza publicar conteos automáticos, familias o dimensiones.**

Las mediciones completas, hashes de cada banda, coordenadas y propuestas están en [inference-results.json](inference-results.json). [upstream.json](upstream.json) fija las fuentes, licencias y versiones; los manifiestos ejecutables están en `config/models/s1.yaml` y `s2.yaml`. Fecha de ejecución: 20 de octubre de 2026 en Guatemala, 21 de octubre UTC.

## Recetas comprobadas

| Entrada | Sentinel-2 | Sentinel-1 |
|---|---|---|
| Producto | MSI L1C | IW GRDH 1SDV, VV/VH de amplitud cruda |
| Canales | B04, B03, B02, B05, B06, B07, B08, B11, B12 | VV/VH actuales, VV/VH históricos |
| Corrección | Baseline ≥04: restar 1000 DN; conservar nodata=0 y mínimo 1 para píxeles originalmente válidos | GCP a UTM con thin-plate spline y remuestreo cúbico; materialización cúbica a 10 m |
| Normalización | RGB /3000; otras seis bandas /8160; limitar a [0,1] | /250; limitar a [0,4] |
| Contexto | Una adquisición | Histórico 30–90 días anterior, mismo sentido orbital; pilotos locales a 60 días y misma órbita relativa |
| Umbral de propuesta | 0.8 | 0.7, valor por defecto de la API upstream; configuración de entrenamiento usa 0.9 |

Se rechazan bandas ausentes, unidades inesperadas, L2A sustituido por L1C y radar calibrado gamma0/sigma0/dB. Un PNG se genera para inspección después de inferir; nunca entra al detector. La máscara PNG de cobertura de anotación del dataset público S1 únicamente verifica que el recorte negativo está anotado por completo.

El adaptador reproduce Swin V2 B, FPN y Faster R-CNN de los [proyectos Ai2 fijados](https://github.com/allenai/rslearn_projects/tree/0bae49abb76d1deee9b6b6ca2a59212611ebc6fe). Se copiaron todos los 479 tensores de cada checkpoint sin estados de Adam: 386,642,035 bytes para S2 y 424,089,772 para S1. Las versiones originales de 1.15/1.26 GB quedan identificadas por generación GCS y MD5 del proveedor. Se verificó CRC32 de cada miembro descargado y SHA256 del archivo resultante; **no se recalculó el MD5 del checkpoint completo**. La extracción usa un intérprete limitado de metadatos pickle, sin ejecutar clases del archivo.

Se reconstruyeron ambos archivos y se comprobó que reproducen exactamente los SHA256 fijados; el nombre interno del ZIP de PyTorch también está fijado. Una reconstrucción que difiera se rechaza antes de publicar el archivo. Véase [checkpoint-verification.json](checkpoint-verification.json).

## Resultado local

Cada recorte mide 512×512 píxeles, aproximadamente 5.12×5.12 km, e incluye agua abierta y muelles. Las fracciones corresponden al recorte; el servicio debe intersectarlas con cada zona analítica.

| Sensor / puerto | Captura UTC | Propuestas | Fracción observable estimada | Inferencia CPU | Descarga medida |
|---|---|---:|---:|---:|---:|
| S2 / Quetzal | 2026-09-10 16:18:59 | 2 | 99.61% | 0.339 s | Primer recorte sin contador de red |
| S2 / Santo Tomás | 2026-09-30 16:20:59 | 0 | 95.33% | 0.362 s | 11.11 MB, 93.86 s |
| S2 / Barrios | 2026-09-30 16:20:59 | 3 | 99.28% | 0.360 s | 11.11 MB, 87.23 s |
| S1 / Quetzal | 2026-10-19 00:13:42 | 2 | 100% | 1.068 s | 21.91 MB, 58.65 s |
| S1 / Santo Tomás | 2026-10-18 11:45:26 | 0 | 100% | 0.591 s | 21.74 MB, 33.83 s |
| S1 / Barrios | 2026-10-18 11:45:26 | 0 | 84.13% | 0.585 s | 10.75 MB, 21.62 s |

S2 se obtuvo por rangos de JP2 públicos GCS con generación fijada. S1 se obtuvo por rangos HTTP anónimos del bucket AWS `sentinel-s1-l1c`, con `If-Match` sobre el ETag observado. Las cuatro bandas radar incluyen el contexto. La falta de datos al borde de la escena en Barrios S1 permanece en la máscara; no se rellenó con una observación de cero barcos. El piloto no consumió unidades de Sentinel Hub ni usó servicios pagados.

Las nubes y sombras S2 se estimaron con los dos modelos OmniCloudMask v4, 57.81 MB en total y SHA256 verificado. La ejecución repetida en un proceso tardó 1.845/0.525/0.554 s para Quetzal/Santo Tomás/Barrios. Se inspeccionaron overlays de nubes y sombras contra el RGB numérico sin desplazamientos gruesos evidentes. Es una comprobación visual, no una evaluación independiente de precisión de máscara. Los datos de calidad y tiempos están en [quality.json](quality.json).

La inspección visual de detecciones reveló omisiones junto a muelles y propuestas ambiguas sobre agua con estelas en Barrios. Cero propuestas no demuestra cero barcos. Los rectángulos son ventanas de detección de unos cientos de metros, no dimensiones físicas de embarcaciones. Las familias, esloras y mangas quedan sin evaluar.

Se comparó la eliminación upstream por cercanía a infraestructura marina usando su GeoJSON del 12 de octubre: 50 m en S2 y 200 m en S1. Eliminó **0 de las 7 propuestas locales**. Las estructuras más próximas de ese archivo quedan a aproximadamente 189 km de las propuestas de Quetzal y 3.9–4.3 km de las de Barrios; ese inventario no equivale a un mapa de todos los muelles. El ensayo no demuestra que quitar el filtro mejore precisión o recuperación. La deduplicación upstream de centros separados por menos de 10 píxeles también puede fusionar barcos cercanos: requiere evaluación específica en puerto.

## Muestras públicas y recursos

Se descargaron únicamente los primeros 8 MiB de cada tar público, con SHA256 de ese prefijo. El tar S2 completo tiene 239.96 GB y el S1 9.59 GB; no se descargaron completos. La muestra S2 de Corea contiene una etiqueta de barco y produjo una propuesta de score 0.9992657, idéntico al repetir la entrada. La muestra S1 de Cuba contiene una anotación negativa y cobertura de anotación del 100% del recorte; produjo una propuesta 0.726, un desacuerdo con esa anotación. El split `train` de S1 proviene de `metadata.options.olmoearth_evals_split`, aunque su carpeta heredada se llame `val_ascending`.

Ambas muestras se usaron exclusivamente para demostrar operación. Un paso AdamW con encoder congelado actualizó FPN/cabeza: pérdida 0.040097 en S2, 0.003804 en S1, aproximadamente 0.744 y 0.746 s en la repetición. No se usaron predicciones como etiquetas y estas muestras no aportan métricas locales independientes.

Equipo ARM64 de 24 GiB, Python 3.11.15, PyTorch 2.14.1, cuatro hilos CPU. El pico RSS observado por proceso fue aproximadamente 1.95 GB con calidad S2 e inferencia. Los pasos de entrenamiento pequeños no anticipan la memoria de un dataset completo. MPS ejecutó la muestra S2 sin fallback, con el mismo score, y tardó 2.889 s en su primer pase; no resultó más rápido que CPU en esta prueba. Véase [mps.json](mps.json).

## Proyección y límites

El inventario contiene 771 combinaciones puerto/adquisición S2 y 533 S1 desde enero de 2025 hasta el corte. **Si se procesara un solo recorte de 512×512 por combinación**, extrapolar los recortes medidos daría 14.29–20.24 GB de transferencia y 21.9–28.8 horas de descarga serial, más preparación, carga e inferencia. Los rasters preparados ocuparían aproximadamente 3.85 GB, además de originales, copias de respaldo, evidencias y 0.87 GB de pesos de detección/calidad.

Es un escenario, no un presupuesto garantizado: sólo dos recortes de la misma adquisición S2 tienen contador, la velocidad de red varía, faltan búsquedas y fallos de disponibilidad, y las zonas completas pueden necesitar varios recortes. La cubierta nubosa tampoco se puede extrapolar desde estas muestras. La descarga histórica masiva sigue deshabilitada.

Los límites del ensayo son un trabajador pesado, lado numérico máximo de 4096 píxeles, recorte habitual de 512, 80 MiB por recorte óptico, 192 MiB por par radar y 550 MiB por extracción de checkpoint. El siguiente paso con mayor efecto es reunir etiquetas locales revisadas, incluidos negativos difíciles y muelles, y reservar particiones independientes antes de ajustar y medir modelos propios.

## Reproducción

Desde la raíz, instalar `uv sync --extra ml --extra quality`. Mantener los archivos grandes en `data/`, excluido de Git. Las licencias de código/pesos Ai2 son Apache-2.0; anotaciones Ai2, CC-BY-4.0; OmniCloudMask y su ficha de pesos, MIT. Los datos Sentinel mantienen los [términos de Copernicus](https://dataspace.copernicus.eu/terms-and-conditions). Atribución: Contains modified Copernicus Sentinel data (2021, 2026).

```sh
uv run --no-sync python scripts/pilot_inference.py weights --model config/models/s2.yaml
uv run --no-sync python scripts/pilot_inference.py weights --model config/models/s1.yaml
uv run --no-sync python scripts/pilot_inference.py quality-weights
uv run --no-sync python scripts/pilot_inference.py public-sample
uv run --no-sync python scripts/pilot_inference.py public-sample-s1
uv run --no-sync python scripts/pilot_inference.py infer --bundle data/pilot/upstream-prepared/bundle.json --train-boxes data/pilot/upstream-prepared/reference_boxes.json
uv run --no-sync python scripts/pilot_inference.py infer --bundle data/pilot/s1-upstream-prepared/bundle.json --model config/models/s1.yaml --train-boxes data/pilot/s1-upstream-prepared/reference_boxes.json
uv run --no-sync python scripts/pilot_inference.py crop --scene S2B_MSIL1C_20260930T162059_N0513_R040_T16PCC_20260930T212702 --port stc --lon -88.616 --lat 15.697 --product-uuid 5fb37d9f-340d-4f34-a78a-3c64835312d7
uv run --no-sync python scripts/pilot_inference.py infer --bundle data/pilot/stc-prepared/bundle.json --quality
uv run --no-sync python scripts/pilot_inference.py crop-s1 --scene S1D_IW_GRDH_1SDV_20261019T001342_20261019T001407_005077_0099B2_9247 --historical S1D_IW_GRDH_1SDV_20260820T001340_20260820T001405_004202_007B37_42E9 --port prq --lon -90.787 --lat 13.927
uv run --no-sync python scripts/pilot_inference.py infer --bundle data/pilot/prq-s1-prepared/bundle.json --model config/models/s1.yaml
uv run --no-sync python scripts/pilot_inference.py compare-infrastructure --bundle data/pilot/prq-s1-prepared/bundle.json
uv run --no-sync pytest tests/test_preprocess.py -q
```

Para los otros recortes, nombres de escenas, centros y fuentes exactas están en `data/pilot/*-source/scene.json` y en los registros de `inference-results.json`. `infer` escribe `scene.json` con bandas y máscaras locales, listo para la ingesta del servicio; también produce `bundle.json`, `predictions.json`, `preview.png` y `inference.json`. Puede usarse `--device mps` para repetir esa comprobación.
