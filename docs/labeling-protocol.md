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

El estado «Revisado» registra una decisión humana. No certifica identidad, tipo comercial o dimensiones. El score es una salida del modelo y una anotación manual carece de score. Conserva rechazos para analizar el error, aunque se oculten de las consultas ordinarias.

Eslora y manga físicas requieren una referencia verificable y correspondencia con el objeto observado. El tamaño de la caja de píxeles no basta. Familia admite carga, tanquero, pasajeros, remolcador, pesquero y otros; usa indeterminado fuera del alcance. Registra procedencia por cada campo, con clase de fuente, referencia y fecha cuando corresponda. Solo los campos respaldados contribuyen al entrenamiento de atributos.

## Cerrar y separar escenas

Registra «escena revisada» después de buscar omisiones en toda el área observable y resolver todas las propuestas provisionales. La exportación del dataset exige esa marca; aceptar únicamente lo que detectó el modelo no permite medir recall. Escenas sin buques también son ejemplos negativos útiles si están completas y observables.

Asigna las particiones `train`, `calibration` y `test` mediante revisión de observación en la [API](api.md), indicando `dataset_split` y `split_groups`. Planifica las particiones antes del entrenamiento. Mantén una misma adquisición y sus representaciones, recortes relacionados, pares temporales y eventos correlacionados en una sola partición. Usa `split_groups` para expresar relaciones adicionales que la clave de adquisición no detecta.

El exportador rechaza grupos relacionados presentes en particiones distintas. El revisor sigue siendo responsable de registrar los grupos pertinentes. Reserva `calibration` para fijar umbrales y `test` para la evaluación independiente. No ajustes modelos o umbrales viendo los errores del test final; si se usa para desarrollo, deja de ser prueba independiente.

`dataset` crea un directorio inmutable con manifiesto, etiquetas GeoJSON, evidencia y hashes. Un destino existente se rechaza. El entrenamiento conserva la versión y los pesos previos. Las etiquetas y el dataset necesitan una política de procedencia que permita su uso y redistribución; un bundle técnico no concede licencias adicionales.

## Revisar cambios físicos

Usa pares ópticos comparables, corregistrados y con observabilidad suficiente, centrados en áreas estáticas. Excluye agua, buques y fenómenos transitorios. Registra también pares negativos con variaciones de iluminación, nubosidad y estación.

Un candidato necesita una referencia sin cambio y una observación posterior con cambio. La persistencia exige otra adquisición óptica distinta y válida con el cambio presente. Nubes, datos inválidos o radar no confirman ni revierten el evento. Una observación óptica válida posterior sin el cambio puede registrar reversión. Persistente no significa permanente y el sistema no atribuye la causa.

La interfaz muestra antes/después solo cuando existen activos explícitamente vinculados. Aceptar una propuesta registra revisión humana; la persistencia se calcula con la evidencia temporal. Conserva eventos relacionados en la misma partición.

## Criterio de publicación

Mide precisión, recall, omisiones y falsos positivos por escena, con soporte por puerto, sensor y zona. Evalúa atributos solo donde haya referencia independiente y reporta abstenciones. Los promedios no sustituyen estratos sin evidencia suficiente.

Los modelos incluidos parten como experimentales o sin entrenar. El piloto de inferencia verifica que el flujo puede ejecutarse con datos reales. La publicación validada requiere pesos propios cuando corresponda, referencia independiente, test congelado, umbrales calibrados y métricas con soporte suficiente. Mantén fuera de alcance lo que aún no esté demostrado.
