# Protocolo de revisión y datasets

El objetivo de la revisión es construir referencia independiente para medir falsos positivos y omisiones, y entrenar modelos propios. Las propuestas automáticas orientan el trabajo, pero no son etiquetas verdaderas por haber sido aceptadas en lote.

## Preparar una escena

1. Comprueba puerto, sensor, adquisición, zona y versión de procesamiento.
2. Abre la evidencia registrada y revisa límites espaciales, resolución, máscara y área observable.
3. Marca la escena no observable cuando no permita decidir; conserva la razón en la revisión.
4. Recorre toda el área observable, también fuera de las propuestas del modelo.

Las fechas se muestran en UTC. Radar y óptico se evalúan por separado. No extrapoles visibilidad local desde el porcentaje de nubes de todo un tile. Las geometrías portuarias actuales requieren validación humana; no interpretes sus límites como deslindes oficiales.

## Revisar objetos

- Acepta una detección cuando la evidencia permita identificar un buque.
- Rechaza estelas, infraestructura, islas, reflejos y otros falsos positivos.
- Añade los buques omitidos mediante un recuadro. Corrige posición o extensión cuando haga falta.
- Deja familia y dimensiones indeterminadas si no hay referencia independiente suficiente.

La interfaz permite seleccionar, añadir, mover y redibujar geometrías sobre imágenes con referencia WGS84. Los atajos son `j`/`k` para navegar, `a` para aceptar, `r` para rechazar y `Esc` para salir de la herramienta. Cada acción se guarda con autor y fecha. Una revisión obsoleta recibe 409; actualiza y compara antes de aplicar una nueva corrección.

El estado «Revisado» registra una decisión atribuida al autor del historial. Antes de guardar, selecciona «Persona» o «Asistente de IA»; la procedencia conserva `human_visual_review` o `ai_visual_review` y el visor identifica las observaciones revisadas por IA. La revisión de IA debe declararse como tal en los datasets y las métricas; no acredita una revisión humana independiente. Ninguno de estos estados certifica identidad, tipo comercial o dimensiones. El score es una salida del modelo y una anotación manual carece de score. Conserva rechazos para analizar el error, aunque se oculten de las consultas ordinarias.

Eslora y manga físicas requieren una referencia verificable y correspondencia con el objeto observado. El tamaño de la caja de píxeles no basta. Familia admite carga, tanquero, pasajeros, remolcador, pesquero y otros; usa indeterminado fuera del alcance. Registra procedencia por cada campo, con clase de fuente, referencia y fecha cuando corresponda. Solo los campos respaldados contribuyen al entrenamiento de atributos.

## Cerrar y separar escenas

Delimita el área que permite decidir usando `reviewed_area` en la revisión de observación de la [API](api.md). Debe ser un polígono o multipolígono WGS84 dentro de la zona configurada; puede excluir sectores dudosos mediante huecos. El visor muestra esta área en verde y la zona configurada en amarillo. Los sectores excluidos no se etiquetan como ausencia de buques. Si no se especifica un área, el exportador usa la zona configurada completa; no uses ese valor por defecto cuando solo hayas revisado un recorte.

Registra «escena revisada» (`scene_complete: true`) después de buscar omisiones en toda el área declarada y resolver las propuestas provisionales que contiene. Cuando existe `reviewed_area`, la completitud se refiere a esa área, no a toda la zona portuaria. La exportación bloquea también un objeto provisional cuyo centroide quede fuera pero cuya geometría invada el área con superficie positiva; resuélvelo o excluye toda su huella dudosa. Un contacto de borde sin superficie no bloquea si el centroide queda fuera. Para objetos resueltos, la pertenencia al área se determina por el centroide.

La exportación exige esa marca de completitud; aceptar únicamente lo que detectó el modelo no permite medir recall. Un área sin buques es un ejemplo negativo útil solo si se revisó completa y era observable. Conserva el resto de la escena como no etiquetado.

Asigna las particiones `train`, `calibration` y `test` mediante revisión de observación en la [API](api.md), indicando `dataset_split` y `split_groups`. Planifica las particiones antes del entrenamiento. Mantén una misma adquisición y sus representaciones, recortes relacionados, pares temporales y eventos correlacionados en una sola partición. Usa `split_groups` para expresar relaciones adicionales que la clave de adquisición no detecta.

El exportador rechaza grupos relacionados presentes en particiones distintas. El revisor sigue siendo responsable de registrar los grupos pertinentes. Reserva `calibration` para fijar umbrales y `test` para la evaluación independiente. No ajustes modelos o umbrales viendo los errores del test final; si se usa para desarrollo, deja de ser prueba independiente.

`dataset` crea un directorio inmutable con manifiesto, etiquetas GeoJSON, evidencia y hashes. Un destino existente se rechaza. El entrenamiento conserva la versión y los pesos previos. Las etiquetas y el dataset necesitan una política de procedencia que permita su uso y redistribución; un bundle técnico no concede licencias adicionales.

## Revisar cambios físicos

Usa pares ópticos comparables, corregistrados y con observabilidad suficiente, centrados en áreas estáticas. Excluye agua, buques y fenómenos transitorios. Registra también pares negativos con variaciones de iluminación, nubosidad y estación.

Un candidato necesita una referencia sin cambio y una observación posterior con cambio. La persistencia exige otra adquisición óptica distinta y válida con el cambio presente. Nubes, datos inválidos o radar no confirman ni revierten el evento. Una observación óptica válida posterior sin el cambio puede registrar reversión. Persistente no significa permanente y el sistema no atribuye la causa.

La interfaz muestra antes/después solo cuando existen activos explícitamente vinculados. Aceptar una propuesta registra una revisión con autor y tipo de revisor; la persistencia se calcula con la evidencia temporal. Conserva eventos relacionados en la misma partición.

## Criterio de publicación

Mide precisión, recall, omisiones y falsos positivos por escena, con soporte por puerto, sensor y zona. Evalúa atributos solo donde haya referencia independiente y reporta abstenciones. Los promedios no sustituyen estratos sin evidencia suficiente.

Los modelos incluidos parten como experimentales o sin entrenar. El piloto de inferencia verifica que el flujo puede ejecutarse con datos reales. La publicación validada requiere pesos propios cuando corresponda, referencia independiente, test congelado, umbrales calibrados y métricas con soporte suficiente. Mantén fuera de alcance lo que aún no esté demostrado.
