# Desarrollo después del fallo de calibración

26 de octubre de 2026. Diagnóstico y desarrollo de Codex (IA). **La precisión local ≥90% todavía no está demostrada.** El TEST reservado no se abrió. Esta carpeta conserva el fallo anterior, una intervención sobre TRAIN y una nueva revisión visual limitada; no reemplaza los resultados congelados de fase 2.

Las vistas RGB y comparaciones temporales de esta carpeta contienen datos modificados de Sentinel-2: **Contains modified Copernicus Sentinel data 2025**. Los PNG se usan para revisión visual; el entrenamiento utiliza las bandas numéricas fijadas.

## Evidencia que motivó la intervención

[localization.json](localization.json) reproduce exactamente los resultados con score 0,50 e IoU 0,50: TRAIN 48 TP / 3 FP / 3 FN; CAL 73 TP / 34 FP / 40 FN. De los 34 FP de CAL, 26 tienen algún solapamiento con una referencia; 18 de los 21 FP de PRQ son cajas demasiado grandes. En ese grupo, la mediana del área predicha es 2,47 veces el área de referencia y la distancia entre centros es 11,28 m. Estas son explicaciones del error bajo la métrica original, no aciertos alternativos. CAL ya expuesta queda como diagnóstico de desarrollo y no podrá validar la nueva receta.

El [sondeo geométrico de TRAIN](train-anchor-geometry.json) usa únicamente las 51 referencias anteriores, presentes en 108 instancias por el solapamiento de los parches. Compara anclas idealmente centradas, antes de entrenar:

| Tamaños de ancla, píxeles | IoU máximo mediano | Instancias con IoU ≥0,50 |
| --- | ---: | ---: |
| 32, 64, 128, 256 | 0,173 | 0 / 108 |
| 8, 16, 32, 64 | 0,667 | 104 / 108 |

La geometría de las anclas es una hipótesis plausible de mejora para barcos pequeños. El sondeo no mide precisión de un detector. El cambio conserva cuatro niveles y tres razones de aspecto por nivel, por lo que no cambia la forma de los pesos. El loader obtiene la receta del manifest tanto al entrenar como al inferir; los manifests anteriores conservan las anclas originales.

## Comparación prefijada, solo TRAIN

[anchor-comparison-plan.json](anchor-comparison-plan.json), SHA256 `0bd4d81311c5545532108dfaaa90cb6d1f9325300f0e4c8f3ee3eb8869c3ef5c`, fija exactamente dos recetas: anclas originales y anclas pequeñas. Ambas usan los mismos 40 parches de enero, 12 épocas, AdamW 0,0001, semilla 20261021, orden mezclado por época y encoder congelado. Febrero es desarrollo temporal interno de TRAIN, ya observado en fases anteriores. No se usa la nueva etiqueta de marzo para cambiar este comparador.

La selección exige una mejora absoluta de F1 ≥0,05 sin caída de recall, con score 0,50 e IoU 0,50 fijos. Se informa también cada puerto. El presupuesto conjunto es 1.200 segundos de trabajo pesado, CPU de cuatro hilos, un modelo a la vez y 5 GiB de reserva de disco. No hay búsqueda de umbral, recetas adaptativas, CAL, TEST ni refit automático.

Enero aporta 31 referencias (PRQ 29 / PBR 1 / STC 1); febrero aporta 20 (PRQ 19 / PBR 1 / STC 0). Por ello un resultado interno favorable permitiría comparar la geometría sobre todo en PRQ, pero no demostraría recall de STC ni generalización en el Caribe.

La fuente ejecutable está archivada en `data/experiments/train-anchor-comparison-v3-20261026/source`; el [recibo de congelado](anchor-comparison-freeze-v3.json) tiene SHA256 `340483bfb129d40362e611a6ec4815f1266531a71ad263c2b62a88be93bdc3c2`. Se comprobó el recibo ejecutando desde esa fuente. El primer congelado v1 falló su preflight porque reserializar el plan cambiaba sus bytes; no entrenó. v2 preservó los bytes y pasó preflight; tampoco entrenó. v3 añade rechazo explícito de reejecución antes de leer modelos y elimina una rama de refit inalcanzable. Solo v3 está destinado a ejecución; v1 y v2 se conservan intactos. El [registro previo a la ejecución](anchor-comparison-preflight-audit-v1.json) explica los tres recibos sin ocultar intentos.

```sh
PUERTOGT_WORKSPACE=/Users/yhimp/Documents/ChatGPT/HE_PuertoGT \
PYTHONPATH=/Users/yhimp/Documents/ChatGPT/HE_PuertoGT/data/experiments/train-anchor-comparison-v3-20261026/source/src \
.venv/bin/python data/experiments/train-anchor-comparison-v3-20261026/source/scripts/train_anchor_comparison.py \
  run data/experiments/train-anchor-comparison-v3-20261026
```

El runner utiliza el lock pesado del workspace original y el código/lockfile archivados para reproducibilidad. Cada intento crea su inicio de forma exclusiva; una segunda ejecución se rechaza antes de entrenar.

### Resultado del comparativo interno

El [único comparativo ejecutado](anchor-comparison-result-v3.json) terminó en 323,34 segundos, con un pico RSS de 1,42 GB. Score e IoU se mantienen en 0,50:

| Receta | TP | FP | FN | Precisión | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Anclas originales | 16 | 4 | 4 | 80% | 80% | 0,80 |
| Anclas pequeñas | 17 | 3 | 3 | 85% | 85% | 0,85 |

La variante cumple exactamente la mejora mínima de F1 de 0,05 y el recall aumenta. La diferencia corresponde a **un objeto de PBR**: pasa de 0 TP / 1 FP / 1 FN a 1 TP / 0 FP / 0 FN. PRQ permanece en 16 TP / 3 FP / 3 FN y STC no tiene positivos que permitan medir recall. El 100% de PBR es un solo caso; su intervalo Wilson de precisión es aproximadamente 20,7–100%. Esto es una señal limitada para continuar el desarrollo, no evidencia de precisión local ≥90%.

El [plan de refit separado](anchor-refit-plan-v1.json) usa desde la misma inicialización pública las 51 referencias originales y sus 84 parches, con 12 épocas y la receta ganadora. Se congelaron nuevamente [fuentes y recibo](anchor-refit-freeze-v1.json), conservando 876,66 segundos del presupuesto conjunto de 1.200 segundos. El entrenamiento tiene techo de 696,66 segundos y reserva 180 segundos para medir ajuste e IO. No incorpora la etiqueta nueva de marzo ni consulta CAL/TEST. El inicio es exclusivo y cualquier fallo conserva el intento; la métrica resultante se clasifica como ajuste sobre TRAIN.

El [refit terminó](anchor-refit-result-v1.json) con 1.008 pasos y **49 TP / 1 FP / 2 FN sobre el mismo TRAIN: precisión 98% y recall 96,08%**. PRQ aporta 47 TP / 1 FP / 1 FN; PBR 2 TP / 0 FP / 0 FN; STC conserva 0 TP / 0 FP / 1 FN. Frente al ajuste anterior de 48/3/3, reduce errores de ajuste, pero **continúa sin detectar el único positivo STC y no demuestra generalización ni la meta de validación**. No se seleccionó otro umbral ni se reabrió CAL.

El refit y su medición tardaron 463,61 segundos; comparación más refit sumaron **786,95 segundos (13,12 minutos)**, por debajo de los 20 minutos fijados. El pico RSS del refit fue 1,46 GB. El [artefacto experimental](anchor-refit-artifact-v1.json) tiene pesos SHA256 `e0990d7d69750650a5f5175361fc901de882ad0be35ef8fcfd62a3c4b8e73c68`; mantiene `automatic_publication: false` y `local_validation: not_evaluated`. No reemplazó el modelo del servicio.

La [auditoría final](frozen-state-audit-v1.json) comprobó por bytes los **26 manifiestos previos intactos**, frente al baseline anterior al cierre histórico. También comprobó que el recibo y ambos outputs de la CAL fallida siguen idénticos, y que los nuevos experimentos no tienen etapas CAL/TEST ni existe registro de consumo de TEST de fase 2. La auditoría no leyó geometrías de etiquetas TEST; el nuevo snapshot TRAIN permanece separado.

## Etiquetas nuevas y límites de la revisión

Se seleccionaron las primeras cuatro primarias TRAIN disponibles del Caribe después de febrero, sin consultar scores, conteos ni propuestas: 1 y 16 de marzo, 3 y 18 de abril de 2025. Las 32 observaciones abarcan ocho zonas por fecha. Los RGB se reconstruyen con las bandas numéricas originales, incluyendo todos los mosaicos necesarios; no se interpreta una región sin datos como agua vacía. [selection.json](train-s2-mar-apr-raw/selection.json) y [evidence.json](train-s2-mar-apr-raw/evidence.json) fijan fuentes, transformaciones y hashes.

Los [primeros pases separados de dos revisores IA](raw-independent-first-passes.json) preceden a la adjudicación y a la consulta de propuestas. La [adjudicación de áreas](raw-adjudicated-scopes-v1.json) se congeló antes de buscar correspondencias en el catálogo. Resultado:

| Ámbito del 16 de marzo | Decisión | Referencias |
| --- | --- | ---: |
| PBR fondeo, AOI completo | Negativo visual provisional de IA | 0 |
| STC fondeo, rectángulo global `[300,450,350,480]` ∩ AOI original | Positivo visual provisional de IA | 1 |
| Otras 30 observaciones | Abstención; sin área completa certificada | — |

El barco de STC usa la caja visual `[325,475,329,479]`; la discrepancia de aproximadamente un píxel entre revisores se conserva. Su familia y dimensiones físicas siguen nulas. Las máscaras originales confirman todos los centros de píxel de ambas áreas válidos y observables, sin nube ni sombra. Tras congelar las decisiones se encontró una sola propuesta solapada de STC, con caja original de aproximadamente 30 ×30 píxeles; se corrigió a la caja visual ya fijada. La consulta no cambió la etiqueta ni el ámbito. PBR fondeo no tenía propuestas. No se generaron negativos a partir de propuestas del modelo.

La importación dejó [33 revisiones auditadas](train-review-import-receipt-v1.json): 32 observaciones y una detección. El nuevo snapshot `data/datasets/phase3-reviews/s2-caribe-march-april-v1/source` conserva las dos áreas y todos los halos de esa pasada; manifest SHA256 `dea3522dd27c402442170cda1bbbb264e88e10553f8d990422a676e9a499ff92`. [new-train-eligibility-v1.json](new-train-eligibility-v1.json) verifica integridad y confirma que no es un TRAIN autónomo de tres puertos ni validación: contiene una pasada, un positivo STC, cero positivos PBR y ninguna observación PRQ. No modifica los datasets anteriores.

Las formas del muelle PBR permanecen desconocidas; el posible barco del muelle STC no alcanza confianza visual suficiente. Tampoco se certificaron completos los canales. Dos barcos en movimiento claramente visibles tienen su centro fuera de todas las AOI v1 y una tercera forma compatible con barco está en un espigón exterior. Se conservan como [contexto geográfico](raw-candidate-zone-membership.json), sin expandir las AOI para contarlos como aciertos ni agregarlos al entrenamiento.

## Qué falta para demostrar el objetivo

Esta ronda añade **una referencia IA útil**, y deja **30 observaciones sin etiquetas completas**. La base comparativa anterior sigue en 51 referencias, 48 de ellas PRQ. El siguiente trabajo con mayor valor es resolver las áreas de muelles y ampliar ejemplos del Caribe en fechas y condiciones diversas. Un especialista debe revisar los casos ambiguos sobre evidencia independiente suficiente, indicando también lo que no se puede identificar a 10 m; dos agentes de IA no sustituyen esa independencia. Ninguna caja visual aporta dimensiones reales o familia verificada.

Para cada puerto y sensor, el protocolo exige en TEST al menos 30 referencias, 30 predicciones y diez pasadas objetivo; para S2 son al menos 90 referencias distribuidas entre tres puertos, y para ambos sensores al menos 180 distribuidas entre seis estratos. Son pisos de soporte, no un déficit calculado del TEST reservado ni garantía de alcanzar 90%. No se abrió ese TEST para contar lo que falta. La precisión debe alcanzar 0,90 por puerto con recall ≥0,80 bajo IoU 0,50 y con cobertura/abstenciones declaradas; una afirmación de confianza estadística exige además un límite inferior adecuado y considerar la dependencia por pasada.

Antes de otra evaluación hace falta fijar un nuevo conjunto de CAL independiente del diagnóstico expuesto, comprobar la separación temporal y de contextos frente a TRAIN y TEST, congelar datos/receta y evaluar una vez. No se propone julio–septiembre como CAL hasta verificar que el TRAIN usado y los contextos cumplen el embargo del protocolo. El éxito limitado de la comparación interna permitió el refit separado documentado arriba; no habilita publicación automática ni demuestra la meta local.

Validación del cambio: 28 pruebas relevantes pasan, incluyendo receta propagada desde exportación hasta el constructor de inferencia, compatibilidad de carga estricta, rechazo de splits reservados, conservación exacta de los bytes del plan, bloqueo de reejecución y respeto del gate y presupuesto agregado antes del refit. Ruff limpio en los archivos nuevos o modificados de este trabajo.
