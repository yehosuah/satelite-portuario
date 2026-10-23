# Diagnóstico TRAIN y receta original ampliada

El ajuste de tres épocas aprendió cajas visuales, pero todavía no cubre el conjunto de entrenamiento. No se ejecutó inferencia CAL/TEST en este diagnóstico y no se acredita precisión local.

En el crop fijo de enero, el modelo ajustado sitúa una caja con IoU **0.7344** sobre la referencia visual, score **0.4789** y error de centro **0.94 píxeles**. El modelo público no solapa esa referencia. Esto contradice que la cabeza sea incapaz de abandonar las ventanas de 30 píxeles de su preentrenamiento; no demuestra generalización.

La receta original sobre 84 crops y 51 objetos realizó 252 pasos, 214 actualizaciones y 38 abstenciones de supervisión, en 78.98 segundos con cuatro hilos CPU. La evaluación de ajuste usó los 42 tiles originales de siete adquisiciones, con sus halos y el mismo recorrido histórico de inferencia.

| Modelo | Score fijo | TP | FP | FN | Precisión | Recall |
|---|---:|---:|---:|---:|---:|---:|
| base | 0.10 | 0 | 48 | 51 | 0.000 | 0.000 |
| base | 0.80 | 0 | 45 | 51 | 0.000 | 0.000 |
| adjusted | 0.10 | 27 | 34 | 24 | 0.443 | 0.529 |
| adjusted | 0.80 | 22 | 3 | 29 | 0.880 | 0.431 |

Todos los aciertos del modelo ajustado pertenecen a PRQ. PBR contiene dos referencias y STC una; ni siquiera su ajuste quedó demostrado. El siguiente experimento conserva las cajas visuales e IoU0.50 y separa enero de febrero para estudiar orden, duración y tasa de aprendizaje. La exposición previa al ajuste agregado de febrero se declara expresamente: esa partición es desarrollo interno, nunca el TEST reservado.

Los hashes y recibos completos están en `expanded-original-recipe-results.json`. Los experimentos y resultados previos permanecen sin sobrescritura.
