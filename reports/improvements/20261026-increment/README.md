# Incremento operativo 0.4.0

26 de octubre de 2026. Se ejecutaron las mejoras de revisión, geografía y actualización del [plan](../../../docs/improvements.md). La meta científica de precisión ≥90% sigue pendiente; esta entrega no la declara cumplida.

## Primera inspección TRAIN

El [lote congelado](../../review-queue/train-primaries-final-v2/README.md) contiene doce observaciones de ocho pasadas, dos por puerto/sensor. Su selección usa metadatos y máscaras, sin resultados del detector. La interfaz permite inspeccionar el preview crudo, delimitar el área y guardar candidatos o una abstención antes de abrir propuestas. Los recibos son inmutables y las reejecuciones idénticas no duplican escrituras.

La [inspección IA](first-pass-findings.json) registró **41 candidatos en cinco observaciones, dos áreas sin embarcaciones visibles y cinco abstenciones**. Cuarenta candidatos son PRQ y uno STC. Se dedicaron aproximadamente 694 segundos de tiempo de pared, incluyendo herramientas; no es tiempo de trabajo humano. Los ámbitos parciales no describen el puerto completo. Se crearon **cero etiquetas de entrenamiento** y no hubo reentrenamiento.

`model_proposals_read: false` en ese recibo describe la inspección inicial. Después de congelarla se abrió una [comparación separada](comparison-after-freeze.json), únicamente para los siete ámbitos no abstencionistas. La exposición histórica fuera de este flujo no está certificada; ninguna de las dos fuentes constituye una referencia independiente.

| Ámbito | Candidatos visuales | Propuestas dentro del área | Coincidencias de contorno IoU ≥0,5 |
| --- | ---: | ---: | ---: |
| PRQ radar | 26 | 26 | 22 |
| PRQ óptico | 14 | 14 | 0 |
| PBR óptico | 0 | 0 | 0 |
| STC óptico | 1 | 1 | 0 |

Los 15 candidatos ópticos tienen una propuesta solapada con centro dentro de su recuadro, pero las propuestas son mucho mayores. El candidato STC tiene una propuesta a 2,75 m de su centro e IoU 0,0671: esto no demuestra una omisión. Los tamaños son extensiones de anotación, no dimensiones medidas de buques. Los 19 elementos sin pareja por cada lado requieren adjudicación; no son conteos de errores ni una medición de precisión. La novedad frente al corpus TRAIN previo no se ha establecido.

## Geografía y actualización

La [V2 de 250 m](../../coverage/geography-v2-pilot/README.md) corrige tierra incluida en el fondeo STC, cierra su hueco de 136,87 m hacia el canal y añade **0,880 km² netos de agua** entre los tres puertos. Permanece inactiva y se consulta en un panel separado. El piloto reutilizó 174 máscaras de 15 adquisiciones TRAIN, sin inferencia ni nuevas descargas. Tres pasadas radar conservan 0,7948 km² de agua STC fuera de su encuadre; ampliar límites no crea nuevas pasadas ni completa ese encuadre.

El [ciclo incremental](../../refresh/incremental-20261026-14d/result.json) consultó seis combinaciones puerto/sensor en catorce días. Descubrió 17 adquisiciones: 16 ya pertenecían a campañas y una era nueva. Procesó esa adquisición en ocho zonas con 25.215.992 bytes descargados. Reconstruyó cobertura sin errores y la [repetición](../../refresh/incremental-20261026-14d/repeat.json) devolvió el mismo recibo.

El volumen queda en **869 adquisiciones de catálogo: 828 procesadas, seis excepciones conocidas y 35 de contexto**. Hay cero adquisiciones pendientes, 5.026 observaciones, 4.428 detecciones y 123.001 activos. Los diez manifiestos anteriores y las 572 revisiones conservan sus hashes lógicos; la nueva campaña registra un undécimo manifiesto con los mismos pesos. Los 26 manifiestos científicos congelados y V1 siguen idénticos. [Verificación](verification.json) e [aislamiento](isolation-check.json).

## Comprobación y uso

Pasaron 402 pruebas Python con ML, 385 en la instalación base (14 omisiones por PyTorch opcional), seis pruebas de interfaz, Ruff y el control de espacios de Git. La revisión independiente de código reprodujo y cerró cuatro defectos de exclusión de recibos, recuperación de ledger, pendientes y comprobación de frescura. La [instalación limpia del wheel](wheel-install-smoke.json) funciona sin Torch ni Matplotlib. Crear las figuras geográficas requiere Matplotlib; consultar el sistema no.

Se probó en navegador el flujo de dibujo, guardado y continuación con una [base sintética](first-pass-fixture.png). También se comprobaron el [lote real](train-queue.png), el [panel V2](geography-panel.png) y la [frescura](freshness-panel.png). Los datos sintéticos permanecen separados. La actualización se ejecuta explícitamente; no se instaló un temporizador.

`doctor --check-release` mantiene salida 2 por falta de validación local de detección, atributos y cambios. El siguiente trabajo científico es adjudicar los candidatos con revisión humana o evidencia externa fechada, ampliar especialmente el Caribe y fijar una convención de contornos antes del nuevo entrenamiento. La CAL expuesta conserva su resultado fallido y TEST sigue reservado. [Protocolo](../../../docs/labeling-protocol.md).
