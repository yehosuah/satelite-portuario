# Primer experimento local: el 90 % todavía no está demostrado

El detector base y el ajustado obtuvieron los mismos conteos bajo el protocolo fijado antes del test. S1 acertó dos referencias, con soporte insuficiente; S2 no alcanzó IoU 0,5 en ninguna referencia. El ajuste utilizó exclusivamente ejemplos negativos, por lo que no demuestra aprendizaje de cajas positivas. Estos resultados no justifican desplegar el ajuste ni afirmar una precisión general de al menos 90 %.

| Sensor | Modelo | TP / FP / FN por zona | Precisión | Recall | IC Wilson 95 % de precisión | Adquisiciones de test |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | Base | 2 / 0 / 0 | 100 % | 100 % | 34,2–100 % | 1 |
| S1 | Ajustado | 2 / 0 / 0 | 100 % | 100 % | 34,2–100 % | 1 |
| S2 | Base | 0 / 3 / 3 | 0 % | 0 % | 0–56,1 % | 1 |
| S2 | Ajustado | 0 / 3 / 3 | 0 % | 0 % | 0–56,1 % | 1 |

Los intervalos por objeto no corrigen la dependencia de objetos de una misma adquisición. Ningún estrato alcanzó el mínimo prefijado de 30 referencias y 30 predicciones. Las referencias son revisiones visuales de IA contrastadas por una segunda IA, no revisiones humanas, y el etiquetado inicial no fue ciego al detector. La procedencia permite calcular concordancia frente a estas referencias, pero limita su interpretación científica.

## Qué se contó

Las cinco instancias de etiqueta por zona representan **cuatro observaciones de objetos físicos en dos fechas**. S1 contiene dos objetos en el fondeo de PRQ. S2 contiene dos objetos; el objeto oriental pertenece tanto al canal como al fondeo. La tabla conserva las métricas por zona del protocolo original. Un control descriptivo que elimina duplicados geométricos exactos dentro de la misma adquisición produce, para S2, 0 TP, 2 FP y 2 FN. No se suman las zonas como si aportaran barcos o fechas independientes.

Las áreas ambiguas y no observables quedaron fuera de `reviewed_area`; no se transformaron en negativos. El train incluyó cinco observaciones por zona S1 y siete S2, todas sin referencias positivas. El test incluyó tres observaciones S1 y cuatro S2, exclusivamente de PRQ.

## Por qué falla S2 con el criterio prefijado

Las cajas base de los objetos oriental y occidental obtienen IoU de **0,331 y 0,363**, respectivamente. Sus centroides distan **19,34 m y 12,73 m** de las referencias, pero las cajas abarcan aproximadamente **3,02 y 2,76 veces** su área. Los centros próximos no compensan el incumplimiento de IoU 0,5. El ajuste negativo deja IoU de 0,328 y 0,360, sin corregir el problema.

Estas distancias son un diagnóstico posterior sobre predicciones ya guardadas: no sustituyen la métrica, no cambian umbrales y no motivaron ampliar las cajas de referencia. Las cajas visuales describen la respuesta observable a 10 m; no son mediciones de eslora o manga.

## Ejecución y trazabilidad

Se ejecutó una vez cada entrenamiento y una evaluación por modelo y sensor: 36 pasos de entrenamiento y 14 llamadas de inferencia en total. Los 36 pasos modificaron parámetros ajustables y conservaron congelado el backbone. FPN, RPN y cabeza fueron ajustables. No se calibraron umbrales ni se seleccionaron checkpoints con este test.

| Sensor | Pasos | Positivos de train | Tiempo de entrenamiento y evaluación | Pico RSS |
| --- | --- | --- | --- | --- |
| S1 | 15 | 0 | 18,80 s | 1.805.697.024 bytes |
| S2 | 21 | 0 | 16,38 s | 1.368.326.144 bytes |

CPU con cuatro hilos, semilla 20261021, tres épocas y learning rate 2e-5. Los tiempos excluyen la comprobación previa de integridad. Los pesos ajustados se guardaron como experimentales y sin publicación automática; no se entrenaron atributos ni cambios.

- [Resultados estructurados](summary.json), [comparación S1](s1-comparison.json), [comparación S2](s2-comparison.json) y [diagnóstico geométrico](geometry-diagnostics.json).
- [Protocolo original](../training-plan.md), SHA256 `003623ec7d2b352a21a418c1c1338183985895ddffb63b6652e242cafb755d8d`.
- [Datasets congelados](../frozen-datasets.json), con hashes y procedencia de etiquetas y evidencia.
- Script ejecutado: `scripts/train_local_pilot.py`, SHA256 `91ddef3f0e721eb5b37d1323ef34279daf2ea517072cfe45b443d0b66fe9918b`.

La siguiente acción útil es revisar nuevas fechas con positivos y negativos de los tres puertos, separar train/calibration/test por adquisición y contexto radar, y ajustar la regresión de cajas con positivos reales. El test de este experimento queda consumido y no servirá para escoger el siguiente modelo. La [selección de fase 2](../phase2/protocol.md) establece ese nuevo alcance.
