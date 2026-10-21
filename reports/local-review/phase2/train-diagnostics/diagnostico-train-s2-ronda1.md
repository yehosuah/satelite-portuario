# Diagnóstico de geometría del entrenamiento S2, ronda 1

La receta local está aprendiendo un objetivo geométrico distinto del checkpoint público: envolventes visuales del casco, frente a ventanas de 30 × 30 píxeles alrededor de posiciones. Las conversiones geográficas y el recorte pasan en el TRAIN congelado. Esto demuestra un cambio de tarea; todavía no demuestra la causa del resultado de calibración.

La revisión usó exclusivamente código fijado, etiquetas y metadatos TRAIN, y los registros de entrenamiento ya guardados. No se abrieron etiquetas, predicciones ni resultados de calibración/test en este diagnóstico. Se conocen sus rutas y hashes por el manifiesto del experimento. El autor había revisado previamente imágenes test para una tarea distinta; esa exposición no se usó aquí. No se cargaron pesos ni ejecutaron inferencias, entrenamiento o cambios de receta.

## Contratos comprobados

La [configuración pública fijada](https://github.com/allenai/rslearn_projects/blob/0bae49abb76d1deee9b6b6ca2a59212611ebc6fe/data/sentinel2_vessels/config.yaml#L62) configura `box_size: 15`. El [código de DetectionTask fijado](https://github.com/allenai/rslearn/blob/e0ec9c8f404f348de1b7d64521f7afc588c80e0a/rslearn/train/tasks/detection.py#L164) sustituye cualquier geometría de entrada por su centro entero ±15 píxeles y luego recorta al borde. Por tanto, esas ventanas no representan la longitud y anchura del casco. La configuración calcula F1 por distancia de centros de 15 píxeles; también calcula mAP y selecciona checkpoint mediante mAP contra las cajas artificiales.

El [artículo primario enlazado por Ai2, secciones 2.3 y 2.5](https://arxiv.org/html/2312.03207v1) describe puntos S2 anotados por expertos marítimos y una correlación posterior de detecciones con AIS. No hay fundamento para llamar a estos targets «cajas AIS». El artículo es de 2023: no verifica la procedencia individual de cada fila del dataset revisado de 2025. El contrato de cajas de la versión usada sí queda demostrado por su configuración y código.

El adaptador local preserva las cajas que devuelve Faster R-CNN; no vuelve a imponer 30 píxeles al inferir. En `src/puertogt/ml/workflows.py:79`, `pixel_boxes` transforma las etiquetas visuales WGS84 al CRS del raster, aplica la inversa afín y recorta al límite. `train_detector` pasa esas cajas al head. `NoopTransform` sólo rellena la imagen, sin escalado. El backbone queda congelado y FPN/RPN/RoI siguen entrenables; se suman las pérdidas de clasificación y regresión (`src/puertogt/ml/detection.py:170–223`).

## Prueba numérica ejecutada sobre TRAIN

La prueba reproducible está en `probe_train_box_contracts.py`; su resultado es `train-box-contract-probe-v2.json`. Terminó en 2,10 segundos, con un hilo, sin forward de red ni optimizador. Para repetir, elegir una ruta nueva:

```bash
PYTHONPATH=src .venv/bin/python reports/local-review/phase2/train-diagnostics/probe_train_box_contracts.py --output tmp/train-box-contract-probe-rerun.json
```

El script fija el SHA del TRAIN, verifica los archivos de etiquetas y compara con su fuente padre. Usa la función local real `pixel_boxes`, el generador/matcher RPN instalado y `NoopTransform`. Ejecuta el método upstream `process_inputs` extraído literalmente mediante AST para no instalar SciPy/torchmetrics, dependencias innecesarias para esta operación. No sustituye su lógica por una reimplementación. La primera sonda asumió que upstream conservaría todos los targets y detectó dos exclusiones por centro entero en borde; la sonda final las registra explícitamente.

| Comprobación | Resultado |
|---|---:|
| Crops TRAIN / crops con positivos | 40 / 17 |
| Instancias derivadas / objetos físicos únicos | 62 / 31 |
| Objetos únicos por puerto | PRQ 29, PBR 1, STC 1 |
| Error máximo de conversión a píxeles | 3,20 × 10⁻¹⁰ px |
| Diferencia máxima del recorte padre→crop | 2,03 × 10⁻⁷ m² |
| Targets preservados por padding | Sí |
| IoU visual vs ventana upstream, mediana / máximo | 0,214 / 0,492 |
| Targets upstream conservados con IoU ≥ 0,5 | 0 de 60 |
| Exclusiones upstream por centro entero en borde | 2 |
| Mejor IoU de anchor configurado por target, mediana / máximo | 0,186 / 0,459 |
| Targets con algún anchor a IoU ≥ 0,7 antes de regresión | 0 de 62 |

Estas IoU comparan geometrías de supervisión y anchors, no son precisión, recall ni evaluación de predicciones. Los resultados de conversión cubren los grids north-up actuales; no acreditan grids rotados o sesgados.

El [head fijado](https://github.com/allenai/rslearn/blob/e0ec9c8f404f348de1b7d64521f7afc588c80e0a/rslearn/models/faster_rcnn.py#L99) usa tamaños 32/64/128/256 y relaciones de aspecto 0,5/1/2. El matcher RPN instalado conserva los mejores emparejamientos aunque no alcancen 0,7 (`allow_low_quality_matches=True`): todos los crops positivos obtienen anchors positivos. RoI añade las cajas GT a sus propuestas antes de muestrear. Por ello, la escala desfavorable exige regresión pero no impide matemáticamente aprender ni equivale a cero supervisión.

Los registros existentes confirman 120 pasos y 111 actualizaciones: 36/37/38 por época, con 4/3/2 pasos omitidos sin supervisión. Las pérdidas de regresión RPN y RoI son no nulas en pasos positivos. La verificación de actualización implementada compara un tensor FPN; no demuestra por sí sola cuánto cambió cada tensor de regresión.

## Hipótesis y pruebas que las distinguirían

| Prioridad | Hipótesis falsable | Evidencia y prueba mínima |
|---|---|---|
| 1 | La adaptación todavía conserva tamaños de ventana mientras necesita cajas de casco. | Cambio de contrato demostrado. Comparar base y ajustado en el mismo primer crop positivo TRAIN: tamaño de caja, desplazamiento de centro e IoU contra la etiqueta visual intacta. Si el ajustado ya reproduce el casco, esta explicación pierde fuerza para ese caso. |
| 2 | La escala inicial de anchors dificulta la nueva regresión con pocos ejemplos. | Ningún target alcanza el umbral RPN natural; los matches de baja calidad sí proporcionan positivos. Una futura sonda de propuestas RPN en ese mismo crop permitiría separar error de propuesta y error de RoI. No cambiar anchors ahora. |
| 3 | La diversidad y ponderación efectiva son insuficientes. | 29/1/1 objetos por puerto, entre 1 y 6 apariciones por objeto por época. Orden fijo sin shuffle; termina con 14 crops sin positivos. Son hechos del TRAIN, no prueba de olvido. Primero ampliar fechas/orientaciones según muestreo y conservar la receta en un experimento nuevo. |
| 4 | Las cajas se desplazan por conversión, padding o clipping. | Descartada numéricamente para los grids actuales: errores próximos a redondeo y targets preservados. No hay evidencia para tocar reproyección como solución de esta ronda. |

## Inferencia diagnóstica mínima propuesta, aún no ejecutada

Queda preseleccionado el primer crop con positivos del orden TRAIN congelado, sin consultar calibración: `556e10c1-71d5-4003-afc1-a1b1aeef81c8:crop:0:0:512:448`, PRQ, 3 de enero de 2025. Tiene una etiqueta y 2.421 píxeles revisados. El plan exacto y hashes están en `minimal-train-inference-plan.json`.

La prueba requiere dos forwards CPU secuenciales, uno con el checkpoint público y otro con el ajustado, sobre los mismos datos numéricos y padding. Guardaría cajas, centros y scores crudos por encima del mínimo interno existente del head, sin buscar un umbral, y compararía únicamente contra esa referencia TRAIN. Separaría error de centro, tamaño e IoU. No produce evidencia de generalización ni modifica etiquetas, negativos, anchors, épocas, pesos o protocolo. Su ejecución espera que el coordinador libere una frontera segura del histórico.

Se mantienen las cajas visuales y el criterio IoU 0,5. Expandir las referencias a ventanas upstream cambiaría el objetivo después de observar el fallo. La ronda 1 y sus resultados permanecen inmutables; la precisión local sigue sin acreditarse.
