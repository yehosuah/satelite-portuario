# S2 fase 2, ronda 1 — resultado real

El ajuste de tres épocas terminó y **la calibración falló; no se ejecutó el test**. La meta de precisión ≥90% continúa sin demostrarse.

Se usaron 40 parches de entrenamiento, derivados de 31 referencias únicas: PRQ 29, PBR 1 y STC 1. Hubo 120 pasos, 111 actualizaciones y 9 omisiones por falta de anclas/propuestas supervisadas. El backbone quedó congelado y se mantuvieron AdamW 2e-5, cuatro hilos CPU y la semilla prefijada.

La calibración contiene 22 referencias únicas: PRQ 19, PBR 3 y STC 0, una pasada por puerto. Ninguno de los 90 umbrales de 0.10 a 0.99 cumplió las reglas fijadas. Al umbral original S2 de 0.8 hubo 0 aciertos, 16 falsos positivos y 22 falsos negativos con IoU 0.5. Es un diagnóstico de desarrollo sobre este ámbito, no una estimación representativa de todos los puertos.

El diagnóstico por centro más cercano encuentra un área predicha mediana 4.09 veces mayor que la referencia. La distancia mediana entre centros es 12.18 m, pero la mediana de IoU es 0.244 y el máximo 0.311. Hay 19 predicciones y 18 referencias más cercanas distintas; esta asociación descriptiva no sustituye el emparejamiento oficial ni convierte predicciones en aciertos.

Las etiquetas siguen identificadas como revisión de IA. Se conservan todos los ámbitos, abstenciones, discrepancias de calidad y límites de soporte. Los pesos continúan experimentales; publicación automática deshabilitada. La ampliación de etiquetas y el diagnóstico del objetivo geométrico usarán las fechas de desarrollo ya seleccionadas.

[Resumen y hashes](summary.json) · [Calibración completa](calibration/report.json) · [Diagnóstico geométrico](calibration-localization-diagnostic.json) · [Protocolo congelado](../protocol.md)
