# Estado de la entrega

Esta versión entrega el servicio local y una base reproducible para desarrollar los modelos del [plan aprobado](superpowers/plans/2026-10-20-observatorio-satelital.md). La entrega científica completa sigue pendiente. La publicación del repositorio no declara cumplida la meta de precisión ni transforma las propuestas en un histórico validado.

El repositorio y el [piloto portable v0.1.0](https://github.com/yehosuah/satelite-portuario/releases/tag/v0.1.0) son públicos. La transferencia quedó completada: 34 partes del snapshot y cinco archivos de software y checksums, verificados por tamaño y SHA256. La descarga anónima de ambos manifiestos de hashes también se comprobó. **Ese snapshot conserva exclusivamente el piloto inicial; no contiene el estado actual de revisiones, entrenamiento o histórico.** [Verificación de publicación](../reports/pilot/release-upload-status.json).

## Corte operativo

<!-- BEGIN DYNAMIC_STATUS -->
La campaña original de 824 adquisiciones terminó: **818 completas y seis excepciones radar sin contexto compatible, sin tareas pendientes**. La [auditoría del 26 de octubre UTC](../reports/history/closure-20261026.md) verificó 121.451 archivos y reparó la evidencia necesaria para repetir tres capturas tempranas. Las cuatro capturas piloto también se completaron mediante 24 observaciones nuevas, conservando sus 22 revisiones originales. Después se procesaron cinco adquisiciones nuevas. El [cierre global](../reports/history/global-closure-20261026.json) concilia **827 adquisiciones procesadas + seis excepciones, cero pendientes y 5.018 observaciones**; verifica 122.894 archivos registrados, unos 31,06 GB. Se conservan 4.996 pares adquisición/zona distintos; las versiones del piloto no cuentan como visitas adicionales.

El visor incorpora [cobertura por pasada](coverage.md), área mínima por zona e intervalos medidos. Unir partes compatibles de la misma pasada recuperó 32 oportunidades suficientes en Puerto Barrios y 34 en Santo Tomás dentro del corte inicial. Esas ganancias se solapan entre puertos; no son nuevas órbitas.

La [calibración óptica](../reports/local-review/phase2/candidate-calibration-v1/README.md) conserva su resultado: **ninguno de 90 umbrales cumplió los requisitos**. A score 0,50 e IoU 0,50: 73 TP / 34 FP / 40 FN, precisión 68,22% y recall 64,60%. TEST del candidato sigue reservado. El ajuste de TRAIN de 94,12% no es validación independiente.

Los [atributos públicos](../reports/public-attributes/experiment-result-v1.json) y [cambios OSCD](../reports/change/public-data/oscd-bounded-results.md) conservan sus evaluaciones fallidas. Ningún resultado habilita publicación automática ni demuestra precisión local ≥90%.
<!-- END DYNAMIC_STATUS -->

La versión 0.3.1 permite dibujar y guardar un área parcial de revisión desde el visor, con recorte a la zona, previsualización y protección frente a revisiones concurrentes. Cambiar el ámbito deja pendiente su completitud. Esta mejora de revisión no añade etiquetas ni cambia pesos o particiones científicas. [Uso](labeling-protocol.md) y [siguientes mejoras](improvements.md).

| Parte del plan | Implementado y comprobado | Evidencia pendiente |
|---|---|---|
| T1: inventario y geometrías | Inventario inicial de 81 páginas, 1.267 productos y 863 adquisiciones. La actualización añade cinco: 868 en catálogo, 833 del período y 35 de contexto. Doce zonas con procedencia. | Confirmar límites operativos de las zonas provisionales. |
| T2: pilotos y recetas | Inferencia real S1/S2 en los tres puertos, nubes/sombras ópticas, contexto radar compatible, pasos supervisados públicos, pesos/hashes fijados, CPU y MPS. | Calidad representativa por tamaño, muelle/fondeo, nubosidad y geometría radar. |
| T3: persistencia | SQLite, cola idempotente por adquisición/zona/modelos, leases, reintentos, pausas y archivos atómicos. | Operación prolongada en el equipo de destino. |
| T4: revisión | Piloto inicial auditado y rondas posteriores de fase 2; scopes explícitos, omisiones, incertidumbres, procedencia IA y teselas completas con halos conservados para replay. | Revisión humana independiente y soporte suficiente de CAL/TEST por puerto. |
| T5: buques y atributos | Pesos propios de detección S1/S2; desarrollo óptico sobre 51 referencias/84 crops TRAIN, validación temporal interna y refit. Importador público de atributos, contexto fijo, cabezas, caché, evaluación y abstención. | Precisión local ≥90% no demostrada. Cerrar CAL sin elegir recetas con TEST; faltan referencias locales de dimensiones y familias. |
| T6: cambios | Encoder Satlas verificado y decoder propio entrenado con OSCD; evaluación pública por píxel ejecutada y fallida. Comparación estática y estados t0/t1/t2/reversión implementados. | Positivos/negativos portuarios independientes y un modelo que supere evaluación; resolver licencia antes de distribuir datos/pesos OSCD. No se han publicado eventos físicos reales. |
| T7: servicio | API localhost y visor; fechas UTC, filtros, paginación, cobertura por pasada y zona, intervalos medidos, modelos, trabajos y revisiones protegidas. | Ningún servicio remoto de producción desplegado. |
| T8: histórico y continuidad | Campaña original cerrada: 818 completas, seis excepciones verificadas y cero pendientes. Integridad de 121.451 archivos comprobada. Cuatro pilotos completados con 24 observaciones nuevas, conservando las 22 originales. Campañas incrementales con selección explícita. | Seis S1 carecen de contexto compatible confirmado en CDSE/ASF. Eso limita la evidencia disponible; no son observaciones de ausencia de barcos. |
| T9: portabilidad | Wheel/instalación aislada, Docker y exportación/restauración. Copia local v0.2.0 restaurada y continuidad estructural verificada. | La copia v0.2.0 conserva su corte anterior; no incorpora silenciosamente los datos nuevos. Reinicio en otro equipo y validación científica pendientes. |

## Hallazgos del piloto

Los seis recortes locales produjeron siete propuestas antes de repartirlas por zonas. La inspección mostró barcos junto a muelles que el detector base omitió y propuestas ambiguas en agua. Ni esas siete propuestas ni los ceros de otras escenas son conteos confirmados. Los estados `usable` y `partial` describen observabilidad del área, no exactitud del detector.

El snapshot conserva los resultados de la ejecución inicial: 10 zonas utilizables y 12 parciales. La revisión de CI detectó después un error al estimar cobertura con el número de píxeles del borde. El worker corregido usa la intersección geométrica. El [recálculo de las 22 observaciones](../reports/pilot/coverage-correction.json), sin modificar datos ni repetir inferencia, cambiaría cuatro estados de parcial a utilizable y como máximo 0.502 puntos porcentuales de observabilidad. Las detecciones no cambian. El snapshot conserva esa evidencia histórica y requiere reprocesamiento para adoptar el cálculo nuevo.

Los entrenamientos públicos fueron pruebas de una sola muestra: un positivo óptico y un negativo radar con cobertura de anotación verificada. Los pesos propios permiten comprobar el ciclo de entrenamiento, guardado y restauración. No constituyen entrenamiento ni evaluación local suficientes.

La [ronda local posterior](../reports/local-review/README.md) ya revisó las 22 observaciones y entrenó ambos sensores sobre negativos locales. S1 acertó los dos objetos de su pequeña prueba, con intervalo del 95 % de precisión de 34,2–100 %. S2 no alcanzó IoU 0,5: las cajas fueron demasiado amplias. Las métricas base y ajustado no cambiaron. No se eligió un ganador ni se modificó el criterio después de la prueba.

El [checkpoint anterior de fase 2](../reports/local-review/phase2/stable-checkpoint-20261021.md) conserva su estado histórico; después sí se entrenó el conjunto ampliado. La receta original volvió a fallar el ajuste. La [enmienda TRAIN](../reports/local-review/phase2/amendments/s2-train-temporal-v1-results.md) comparó recetas prefijadas usando enero para aprender y febrero como desarrollo interno; eligió 12 épocas, LR 0.0001 y orden mezclado por época. Conservó IoU 0.5, cajas visuales, encoder congelado y radiometría. No usó CAL/TEST para elegir receta.

El refit de 51 referencias logró 48 TP / 3 FP / 3 FN sobre el mismo TRAIN. **94.12% de precisión y recall de ajuste no es una medición independiente del objetivo del 90%.** PRQ aporta casi todos los aciertos; PBR tiene 1 TP / 1 FP / 1 FN y STC 0 TP / 0 FP / 1 FN. Se conservan los resultados fallidos y no se presenta este candidato como validado. La máscara de supervisión excluye negativos fuera de áreas revisadas sin borrar su contexto numérico.

El [subconjunto público OSCD](../reports/change/public-data/oscd-bounded-results.md) permitió entrenar pesos propios y comprobar el pipeline, pero obtuvo F1/IoU/recall cero y precisión indefinida en TEST. Se evaluaron píxeles rectificados, sin inventar CRS ni validación de polígonos locales. El DOI declara CC BY 4.0 y el README original CC BY-NC-SA/consulta comercial; se conserva esta restricción más estricta, sin redistribuir máscaras o pesos. Las tripletas TRAIN locales inspeccionadas no dieron cambios permanentes inequívocos: barcos, sombras y contenedores no se convirtieron en positivos.

Los [atributos públicos](public-attributes.md) conservan 36 muestras TRAIN, cinco CAL y siete TEST del split original, con pasadas separadas y procedencia por campo. Se desconoce la separación de identidades físicas y el solapamiento con preentrenamiento. No hay referencias locales suficientes de familias/dimensiones; los campos siguen sin validación para publicar automáticamente.

La proyección inicial del [piloto](../reports/pilot/inference.md) correspondía a recortes parciales. La campaña de zonas completas tiene [mediciones propias y límites](../reports/history/processing-plan.md) y un [cierre auditado](../reports/history/closure-20261026.md). La suma de intentos exitosos no incluye pausas ni fallos y no representa todo el tiempo de cómputo. Las propuestas procesadas siguen siendo experimentales.

## Para cerrar la entrega científica

El siguiente trabajo necesario es ampliar los positivos revisados de los tres puertos y ambas fuentes, mantener un test temporal reservado y aportar referencias independientes para familias y dimensiones. El [protocolo](labeling-protocol.md) incluye negativos y omisiones. La falta de una referencia conserva campos nulos.

El [diagnóstico posterior](../reports/local-review/phase3-diagnosis/README.md) revisa el tamaño de las cajas del detector y conserva un experimento acotado, exclusivamente sobre TRAIN. La revisión adicional de 32 observaciones produjo dos ámbitos completos y una referencia de buque IA; las otras 30 quedaron como abstenciones. Este soporte no es suficiente para una validación independiente. Las [opciones geográficas V2](../reports/coverage/geography-v2-candidates/README.md) cuantifican ampliaciones de agua de 250/500 m alrededor de muelles y el problema del fondeo de Santo Tomás; siguen inactivas y no cambian los datasets congelados.

La campaña histórica conserva propuestas explícitamente experimentales mientras se completa esa validación. Su cobertura y sus excepciones ya se conciliaron. `puertogt doctor --check-release` mantiene el estado científico pendiente. El panel y `doctor` exigen una declaración explícita `publication_policy.local_validation: passed`, además de pesos/dataset y estado validado; un benchmark público no cuenta como validación local. El panel muestra declaraciones de manifiestos, no calcula precisión.

## Preparar y reanudar el sistema

La [instalación](installation.md) prepara Python, dependencias, volumen y credencial local. Conserva `data/catalog.sqlite`, activos, modelos, revisiones y el directorio completo `data/history/full-v1`. No repitas `plan` para sustituir la campaña ni borres intentos, reservas de bytes o fuentes fijadas.

La campaña original ya terminó y no requiere reiniciarse. Para inspeccionarla:

```sh
PYTHONPATH=src .venv/bin/python scripts/process_history.py status \
  --plan-dir data/history/full-v1
```

Para fechas nuevas, crea un plan separado con `plan-selected` y una lista exacta de adquisiciones. El [procedimiento y los suplementos ejecutados](../reports/history/closure-20261026.md) explican la protección de evidencias anteriores. Mantén un solo proceso pesado y sus límites de descarga/CPU. `--resume-paused` corresponde únicamente a estados pausados por recursos después de corregir su causa; no elimina `PAUSE`. Las [seis excepciones radar](../reports/history/missing-context-investigation.json) permanecen documentadas, sin volver a programarlas en cada actualización.

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

El último comando mantiene salida 2 mientras falte evidencia científica; no se altera esa política para producir un paquete. La versión de software actual es 0.3.1. La restauración del corte v0.2.0 verificó 96.494 archivos del catálogo y su continuidad estructural; sus recibos conservan ese estado anterior en `reports/release-v0.2.0/`. No equivale a haber reiniciado el worker en otra máquina. La copia de datos es local; publicar código no implica subir sus imágenes ni sus pesos a GitHub. El release v0.1.0 permanece intacto.

## Archivos de evidencia

- [Inventario](../reports/pilot/inventory.json), [geometrías](../reports/pilot/geometries.md) y [procedencia de modelos](../reports/pilot/upstream.json).
- [Piloto e inferencia](../reports/pilot/inference-results.json), [máscaras](../reports/pilot/quality.json), [MPS](../reports/pilot/mps.json) y [pesos reproducibles](../reports/pilot/checkpoint-verification.json).
- [Entrenamiento óptico](../reports/pilot/training-roundtrip.json) y [entrenamiento radar](../reports/pilot/radar-training-roundtrip.json).
- [Servicio óptico](../reports/pilot/service-validation.json), [servicio radar](../reports/pilot/radar-service-validation.json) y [restauración](../reports/pilot/restore-validation.json).
- [Encoder Satlas](../reports/pilot/satlas-smoke.md), [instalación wheel](../reports/pilot/wheel-validation.json), [Docker CPU](../reports/pilot/docker-validation.json), [pruebas/CI](../reports/pilot/software-validation.json) y [empaquetado](../reports/pilot/package-validation.json).
- [Cobertura y excepciones](../reports/history/).
- [Corrección geométrica de cobertura e impacto sobre el snapshot](../reports/pilot/coverage-correction.json).
- [Desarrollo TRAIN del detector](../reports/local-review/phase2/amendments/s2-train-temporal-v1-results.md), [OSCD real y límites de licencia](../reports/change/public-data/oscd-bounded-results.md) y [atributos públicos](public-attributes.md).
