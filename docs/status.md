# Estado de la entrega

Esta versión entrega el servicio local y una base reproducible para desarrollar los modelos del [plan aprobado](superpowers/plans/2026-10-20-observatorio-satelital.md). La entrega científica completa sigue pendiente. La publicación del repositorio no declara cumplida la meta de precisión ni transforma las propuestas en un histórico validado.

El repositorio y el [piloto portable v0.1.0](https://github.com/yehosuah/satelite-portuario/releases/tag/v0.1.0) son públicos. La transferencia quedó completada: 34 partes del snapshot y cinco archivos de software y checksums, verificados por tamaño y SHA256. La descarga anónima de ambos manifiestos de hashes también se comprobó. **Ese snapshot conserva exclusivamente el piloto inicial; no contiene el estado actual de revisiones, entrenamiento o histórico.** [Verificación de publicación](../reports/pilot/release-upload-status.json).

## Corte operativo

<!-- BEGIN DYNAMIC_STATUS -->
Al corte **2026-10-23T05:03:55.799342Z**, la campaña está reanudada: **645/824 adquisiciones completas, 173 por resolver y seis excepciones de contexto radar**. El worker conserva plan, reintentos y presupuestos.

La [calibración óptica](../reports/local-review/phase2/candidate-calibration-v1/README.md) terminó: **ninguno de 90 umbrales cumplió los requisitos**. Con umbral 0,50: 73 TP / 34 FP / 40 FN, precisión 68,22% y recall 64,60%; TEST del candidato sigue cerrado. TRAIN 94,12% era ajuste, no validación. CAL contiene 105 ámbitos y 113 referencias IA (PRQ 87 / PBR 16 / STC 10), con 24 observaciones PRQ posteriores aún fuera del corte.

El [experimento público de atributos](../reports/public-attributes/experiment-result-v1.json) terminó: 48 muestras materializadas,34 TRAIN elegibles de 36, cinco CAL sin usar y siete TEST evaluadas una vez. Familias: 3/7 correctas; MAE eslora 71,75 m y manga 11,43 m. Campos y publicación automática continúan deshabilitados. OSCD público también conserva su prueba fallida. Ninguno de estos resultados demuestra precisión local ≥90%.
<!-- END DYNAMIC_STATUS -->

| Parte del plan | Implementado y comprobado | Evidencia pendiente |
|---|---|---|
| T1: inventario y geometrías | 81 páginas, 1,267 productos, 863 adquisiciones únicas; 828 del período y 35 de contexto. Doce zonas con procedencia. | Confirmar límites operativos de las zonas provisionales. |
| T2: pilotos y recetas | Inferencia real S1/S2 en los tres puertos, nubes/sombras ópticas, contexto radar compatible, pasos supervisados públicos, pesos/hashes fijados, CPU y MPS. | Calidad representativa por tamaño, muelle/fondeo, nubosidad y geometría radar. |
| T3: persistencia | SQLite, cola idempotente por adquisición/zona/modelos, leases, reintentos, pausas y archivos atómicos. | Operación prolongada en el equipo de destino. |
| T4: revisión | Piloto inicial auditado y rondas posteriores de fase 2; scopes explícitos, omisiones, incertidumbres, procedencia IA y teselas completas con halos conservados para replay. | Revisión humana independiente y soporte suficiente de CAL/TEST por puerto. |
| T5: buques y atributos | Pesos propios de detección S1/S2; desarrollo óptico sobre 51 referencias/84 crops TRAIN, validación temporal interna y refit. Importador público de atributos, contexto fijo, cabezas, caché, evaluación y abstención. | Precisión local ≥90% no demostrada. Cerrar CAL sin elegir recetas con TEST; faltan referencias locales de dimensiones y familias. |
| T6: cambios | Encoder Satlas verificado y decoder propio entrenado con OSCD; evaluación pública por píxel ejecutada y fallida. Comparación estática y estados t0/t1/t2/reversión implementados. | Positivos/negativos portuarios independientes y un modelo que supere evaluación; resolver licencia antes de distribuir datos/pesos OSCD. No se han publicado eventos físicos reales. |
| T7: servicio | API localhost y visor; fechas UTC, filtros, paginación, activos, cobertura, modelos, trabajos y revisiones protegidas. | Ningún servicio remoto de producción desplegado. |
| T8: histórico y continuidad | 824 adquisiciones ya programadas, 4,907 teselas de zonas completas, presupuestos persistentes, reintentos, recuperación auditada y transporte reutilizable. Avance en el corte operativo. | Completar los reintentos. Seis S1 carecen de contexto compatible confirmado en CDSE/ASF; también faltan áreas no cubiertas de las cuatro capturas piloto. |
| T9: portabilidad | Wheel/instalación aislada, Docker y exportación/restauración. El snapshot inicial verifica 270 archivos y reproduce ambos detectores desde rutas restauradas. | Generar y restaurar una entrega nueva del catálogo actual; copiar aparte el estado de campaña necesario para reanudarlo. Validación científica aún pendiente. |

## Hallazgos del piloto

Los seis recortes locales produjeron siete propuestas antes de repartirlas por zonas. La inspección mostró barcos junto a muelles que el detector base omitió y propuestas ambiguas en agua. Ni esas siete propuestas ni los ceros de otras escenas son conteos confirmados. Los estados `usable` y `partial` describen observabilidad del área, no exactitud del detector.

El snapshot conserva los resultados de la ejecución inicial: 10 zonas utilizables y 12 parciales. La revisión de CI detectó después un error al estimar cobertura con el número de píxeles del borde. El worker corregido usa la intersección geométrica. El [recálculo de las 22 observaciones](../reports/pilot/coverage-correction.json), sin modificar datos ni repetir inferencia, cambiaría cuatro estados de parcial a utilizable y como máximo 0.502 puntos porcentuales de observabilidad. Las detecciones no cambian. El snapshot conserva esa evidencia histórica y requiere reprocesamiento para adoptar el cálculo nuevo.

Los entrenamientos públicos fueron pruebas de una sola muestra: un positivo óptico y un negativo radar con cobertura de anotación verificada. Los pesos propios permiten comprobar el ciclo de entrenamiento, guardado y restauración. No constituyen entrenamiento ni evaluación local suficientes.

La [ronda local posterior](../reports/local-review/README.md) ya revisó las 22 observaciones y entrenó ambos sensores sobre negativos locales. S1 acertó los dos objetos de su pequeña prueba, con intervalo del 95 % de precisión de 34,2–100 %. S2 no alcanzó IoU 0,5: las cajas fueron demasiado amplias. Las métricas base y ajustado no cambiaron. No se eligió un ganador ni se modificó el criterio después de la prueba.

El [checkpoint anterior de fase 2](../reports/local-review/phase2/stable-checkpoint-20261021.md) conserva su estado histórico; después sí se entrenó el conjunto ampliado. La receta original volvió a fallar el ajuste. La [enmienda TRAIN](../reports/local-review/phase2/amendments/s2-train-temporal-v1-results.md) comparó recetas prefijadas usando enero para aprender y febrero como desarrollo interno; eligió 12 épocas, LR 0.0001 y orden mezclado por época. Conservó IoU 0.5, cajas visuales, encoder congelado y radiometría. No usó CAL/TEST para elegir receta.

El refit de 51 referencias logró 48 TP / 3 FP / 3 FN sobre el mismo TRAIN. **94.12% de precisión y recall de ajuste no es una medición independiente del objetivo del 90%.** PRQ aporta casi todos los aciertos; PBR tiene 1 TP / 1 FP / 1 FN y STC 0 TP / 0 FP / 1 FN. Se conservan los resultados fallidos y no se presenta este candidato como validado. La máscara de supervisión excluye negativos fuera de áreas revisadas sin borrar su contexto numérico.

El [subconjunto público OSCD](../reports/change/public-data/oscd-bounded-results.md) permitió entrenar pesos propios y comprobar el pipeline, pero obtuvo F1/IoU/recall cero y precisión indefinida en TEST. Se evaluaron píxeles rectificados, sin inventar CRS ni validación de polígonos locales. El DOI declara CC BY 4.0 y el README original CC BY-NC-SA/consulta comercial; se conserva esta restricción más estricta, sin redistribuir máscaras o pesos. Las tripletas TRAIN locales inspeccionadas no dieron cambios permanentes inequívocos: barcos, sombras y contenedores no se convirtieron en positivos.

Los [atributos públicos](public-attributes.md) conservan 36 muestras TRAIN, cinco CAL y siete TEST del split original, con pasadas separadas y procedencia por campo. Se desconoce la separación de identidades físicas y el solapamiento con preentrenamiento. No hay referencias locales suficientes de familias/dimensiones; los campos siguen sin validación para publicar automáticamente.

La proyección inicial del [piloto](../reports/pilot/inference.md) correspondía a recortes parciales. La campaña de zonas completas tiene [mediciones propias y límites](../reports/history/processing-plan.md); su tiempo total sigue siendo una proyección hasta terminar. El inventario y las propuestas procesadas no se presentan como un histórico validado.

## Para cerrar la entrega científica

El siguiente trabajo necesario es ampliar los positivos revisados de los tres puertos y ambas fuentes, mantener un test temporal reservado y aportar referencias independientes para familias y dimensiones. El [protocolo](labeling-protocol.md) incluye negativos y omisiones. La falta de una referencia conserva campos nulos.

La campaña histórica conserva propuestas explícitamente experimentales mientras se completa esa validación. Se conciliará su cobertura y cada excepción al terminar. `puertogt doctor --check-release` mantiene este estado pendiente. El panel y `doctor` exigen una declaración explícita `publication_policy.local_validation: passed`, además de pesos/dataset y estado validado; un benchmark público no cuenta como validación local. El panel muestra declaraciones de manifiestos, no calcula precisión.

## Preparar y reanudar el sistema

La [instalación](installation.md) prepara Python, dependencias, volumen y credencial local. Conserva `data/catalog.sqlite`, activos, modelos, revisiones y el directorio completo `data/history/full-v1`. No repitas `plan` para sustituir la campaña ni borres intentos, reservas de bytes o fuentes fijadas.

Mientras exista el importador I/O4, conserva `PAUSE`. Al terminar descarga, features y evaluación de atributos, comprueba su recibo y la liberación de `campaign.lock` y `.heavy-worker.lock`. Solo entonces retira el marcador de pausa y continúa con los mismos límites. La siguiente invocación está contrastada con la ayuda y el código actuales; **no se ejecutó en esta actualización documental**:

```sh
PYTHONPATH=src .venv/bin/python scripts/process_history.py run \
  --max-acquisitions 824 --priority-file data/history/full-v1/priorities.json \
  --threads 4 --io-workers 4 --reuse-connections
```

El comando conserva fuentes, teselas verificadas, revisiones, deduplicación y presupuestos. Adquiere los locks antes de procesar. `--resume-paused` solo corresponde a estados pausados por recursos después de corregir su causa; no elimina el archivo `PAUSE`. En macOS, mantén el proceso despierto con `caffeinate` ligado a su PID y conserva su log. Las [seis excepciones radar](../reports/history/missing-context-investigation.json) se mantienen como falta de contexto, nunca como cero buques.

## Preparar una entrega nueva

El bundle del CLI incluye un snapshot SQLite consistente, configuración sanitizada, activos y pesos **registrados** con checksums. No incluye el ledger/plan/prioridades de la campaña, cachés/halos externos, datasets congelados ni experimentos no registrados. Por tanto, exportar el catálogo permite consultar su evidencia, pero no basta para continuar el trabajo histórico en otra máquina.

El [corte v0.2.0](../reports/release-v0.2.0/README.md) ya cuenta con catálogo restaurado y complemento local verificado. Para preparar otro corte, detén los escritores en frontera y conserva una copia complementaria verificable de esos componentes necesarios para reanudar. Las bases auxiliares requieren snapshots SQLite consistentes; copiar solo su archivo principal durante escritura puede perder el WAL. Conserva los manifiestos de fuentes y dependencias. No agregues archivos arbitrarios dentro del bundle: su restaurador rechaza entradas ajenas al catálogo. Excluye credenciales y los datos/pesos OSCD restringidos de una publicación pública.

Comandos mínimos para el catálogo, con destinos nuevos. Sus opciones fueron comprobadas con `--help`; esta actualización no exportó ni restauró datos:

```sh
uv build --out-dir dist/release-new
PYTHONPATH=src .venv/bin/python -m puertogt.cli --data-dir data export dist/release-new/catalog-bundle
PYTHONPATH=src .venv/bin/python -m puertogt.cli restore dist/release-new/catalog-bundle data-restored-new
PYTHONPATH=src .venv/bin/python -m puertogt.cli --data-dir data-restored-new doctor --check-release
```

El último comando mantiene salida 2 mientras falte evidencia científica; no se altera esa política para producir un paquete. La versión de software es 0.2.0. Se restauró el catálogo actual con 96.494 archivos verificados y se comprobó la continuidad estructural del histórico; sus recibos están en `reports/release-v0.2.0/`. La continuidad no equivale a haber reiniciado el worker en otra máquina. La copia de datos es local; la publicación de código no implica que sus 26,56 GB se hayan subido a GitHub. El release v0.1.0 permanece intacto. Los pasos para preparar un borrador nuevo, sin ejecutarlos aquí, están en el [registro de implementación](implementation-log.md#entrega-nueva-comandos-y-condiciones).

## Archivos de evidencia

- [Inventario](../reports/pilot/inventory.json), [geometrías](../reports/pilot/geometries.md) y [procedencia de modelos](../reports/pilot/upstream.json).
- [Piloto e inferencia](../reports/pilot/inference-results.json), [máscaras](../reports/pilot/quality.json), [MPS](../reports/pilot/mps.json) y [pesos reproducibles](../reports/pilot/checkpoint-verification.json).
- [Entrenamiento óptico](../reports/pilot/training-roundtrip.json) y [entrenamiento radar](../reports/pilot/radar-training-roundtrip.json).
- [Servicio óptico](../reports/pilot/service-validation.json), [servicio radar](../reports/pilot/radar-service-validation.json) y [restauración](../reports/pilot/restore-validation.json).
- [Encoder Satlas](../reports/pilot/satlas-smoke.md), [instalación wheel](../reports/pilot/wheel-validation.json), [Docker CPU](../reports/pilot/docker-validation.json), [pruebas/CI](../reports/pilot/software-validation.json) y [empaquetado](../reports/pilot/package-validation.json).
- [Cobertura y excepciones](../reports/history/).
- [Corrección geométrica de cobertura e impacto sobre el snapshot](../reports/pilot/coverage-correction.json).
- [Desarrollo TRAIN del detector](../reports/local-review/phase2/amendments/s2-train-temporal-v1-results.md), [OSCD real y límites de licencia](../reports/change/public-data/oscd-bounded-results.md) y [atributos públicos](public-attributes.md).
