# Enmienda TRAIN: resultado de la comparación temporal

La receta elegida por la regla prefijada fue **12 épocas, LR 0.0001 y orden mezclado por época**. Se eligió usando únicamente enero como entrenamiento y febrero como desarrollo interno. Los conjuntos CAL y TEST no se usaron para seleccionar la receta. No se acreditó el objetivo de precisión del sistema.

| Receta sobre enero | TP febrero | FP | FN | F1 fijo |
|---|---:|---:|---:|---:|
| original3_manifest | 0 | 18 | 20 | 0.000 |
| shuffle3 | 0 | 18 | 20 | 0.000 |
| shuffle12 | 13 | 6 | 7 | 0.667 |
| shuffle12_lr1e4 | 16 | 4 | 4 | 0.800 |

Todas las comparaciones conservaron **score 0.50, IoU 0.50, cajas visuales, encoder congelado, anchors y radiometría**. Cada candidato nuevo empezó desde los mismos pesos públicos. El cambio de orden por sí solo no produjo aciertos al umbral fijo; ampliar el entrenamiento y luego aumentar la tasa sí mejoró esta muestra interna. No se probaron alternativas adicionales tras observar los resultados.

Febrero tiene 19 referencias PRQ, 1 PBR y ninguna STC, repartidas en dos pasadas. En la receta elegida, PRQ dio 16 TP / 3 FP / 3 FN y PBR 0 TP / 1 FP / 1 FN; STC solo aportó ámbitos negativos. Además, el ajuste agregado previo ya había expuesto febrero. Estas cifras describen desarrollo interno y no justifican generalización ni validación por puerto.

El refit único sobre los 84 crops de todo TRAIN realizó 1008 pasos. Al pasar por los 42 tiles históricos originales, obtuvo 48 TP / 3 FP / 3 FN a score 0.50: precisión y recall 94.12%. Ese resultado mide ajuste sobre datos usados para aprender. Por puerto: PRQ 47/2/1, PBR 1/1/1, STC 0/0/1. El soporte y el ajuste de PBR/STC siguen siendo insuficientes.

Todo el bloque usó 719.39 segundos y pico RSS 1.46 GB, con cuatro hilos CPU y un solo modelo a la vez. Los datasets originales, pesos de referencia, protocolo y recibos previos se conservaron. Las fuentes exactas de la ejecución están archivadas dentro del experimento.

El artefacto para integrar es `data/experiments/phase2-s2-train-temporal-v1-20261022/integration-manifest.json`, con identidad única y pesos SHA256 `45c1a890173fc654d17b91e0391fb52d6fef8c4daa8b9eefdd53c5c9ba5a9cfb`. Sigue siendo experimental y no está desplegado. El manifiesto original del entrenamiento no fue sobrescrito.

El cierre técnico corrigió por separado la exportación genérica para que los próximos entrenamientos no hereden la identidad pública y colisionen al registrarse. Pruebas de registro preservan simultáneamente modelo base y modelo ajustado. Los hashes y el desglose completo están en `s2-train-temporal-v1-results.json`.
