# Primera revisión de calibración S2, ronda 1

Autor: Codex / model_pilot (IA). Se revisaron las dos primeras primarias prefijadas por puerto: seis asignaciones, cuatro adquisiciones únicas y 24 observaciones de zona. Los 384 archivos registrados coinciden con sus hashes. No se consultaron propuestas, conteos ni scores del detector; no se abrió test, ejecutó inferencia o entrenamiento, ni modificó la base de datos.

| Puerto | Fecha | Ámbitos revisables / 4 | Referencias visuales propuestas |
| --- | --- | ---: | ---: |
| PBR | 2025-10-02 | 0 | No estimable por nube; no es cero barcos |
| STC | 2025-10-02 | 0 | No estimable por nube/bruma; no es cero barcos |
| PRQ | 2025-10-05 | 0 | No estimable por nube; no es cero barcos |
| PBR | 2025-10-09 | 4, con alcance limitado | 3, pendientes de adjudicación de muelle |
| STC | 2025-10-09 | 2 | 0 inequívocos dentro del ámbito limitado |
| PRQ | 2025-10-10 | 4, con exclusiones | 18: 17 en fondeo y 1 en dársena |

Cada puerto tiene solo una adquisición con algún ámbito revisable. STC y PBR comparten ambas fechas y no son pasadas independientes entre sí. Ningún puerto alcanza las 30 referencias requeridas. Estas cifras describen soporte de anotación, no precisión del modelo.

Los archivos `*-raw-review.json` fijan cajas, áreas y exclusiones antes de propuestas. **No están listos para importar directamente:** las referencias están en `reference_objects` e `independent_visual_vessels`; las listas vacías `existing_detections` y `confirmed_additional_vessels` significan correspondencia pendiente, no ausencia de etiquetas. La segunda revisión debe comenzar con evidencia cruda, adjudicar las referencias y producir un documento derivado con la correspondencia al catálogo. Los originales y sus hashes se conservan.

PBR del 9 de octubre contiene tres propuestas de casco junto al espigón, bajo bruma. Se usó únicamente un raw de enero como contexto de la estructura; no se leyeron sus etiquetas. Sus ventanas visuales se añadieron explícitamente al ámbito pese a la máscara automática de nube, sin modificar el raster. La segunda revisión debe confirmar o corregir estas presencias y cajas; no se adoptan por un resultado del detector.

El fondeo configurado de STC es mayormente terrestre. Sus áreas negativas no prueban cobertura de un fondeo marino. En la captura del 2 de octubre, los 241 píxeles automáticos restantes son un pequeño remanente bajo bruma y no permiten anotación fiable. En la del 9 se excluyó una señal diminuta con posible estela, antes de propuestas. Muelles y patios nublados permanecen desconocidos.

En PRQ del 10, V01 toca el borde occidental del fondeo: su centro está dentro y se conserva la caja completa. Esto es un caso de frontera de AOI, no un desacuerdo de nube. Las señales pequeñas y las franjas ambiguas de atraque quedan fuera del ámbito negativo.

`support-summary.json` fija las rutas y hashes de los seis informes. `verification.json` registra las comprobaciones de integridad y geometría. El siguiente paso es adjudicar y ampliar con el orden ya prefijado, preservando las primarias nubladas; no elegir fechas según la precisión o el número de predicciones.
