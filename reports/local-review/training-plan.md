# Protocolo congelado del piloto local con etiquetas de IA

Fecha: 2026-10-21. Autor: Codex (IA). Aceptado por el coordinador antes de entrenar y antes de consultar las etiquetas o los resultados del test. Este documento fija las decisiones que no podrán adaptarse a los resultados de prueba.

## Unidad independiente y particiones

El catálogo local contiene 22 observaciones por zona, derivadas de seis recortes numéricos y cuatro adquisiciones objetivo. No son 22 escenas independientes. Cada sensor tiene únicamente dos adquisiciones objetivo:

| Sensor | Partición | Puertos | Adquisición objetivo |
| --- | --- | --- | --- |
| Sentinel-1 | train | STC y PBR | `S1D:IW:GRDH:1SDV:20261018T114526:20261018T114551:005069:009972` |
| Sentinel-1 | test | PRQ | `S1D:IW:GRDH:1SDV:20261019T001342:20261019T001407:005077:0099B2` |
| Sentinel-2 | train | STC y PBR | `S2B:MSIL1C:20260930T162059:R040:T16PCC` |
| Sentinel-2 | test | PRQ | `S2B:MSIL1C:20260910T161859:R040:T15PYR` |

STC y PBR comparten adquisición dentro de cada sensor. Todas sus zonas, revisiones y recortes relacionados pertenecen a train. PRQ completo pertenece a test. Los dos contextos históricos de radar son entradas del modelo temporal, no adquisiciones objetivo etiquetadas adicionales, y deben permanecer asociados a su objetivo. No se admiten observaciones, adquisiciones, grupos de partición ni rasters numéricos compartidos entre train y test.

No hay partición calibration: dividir dos adquisiciones entre tres funciones produciría fuga o un conjunto vacío. La evaluación es una retención geográfica de PRQ, no una evaluación temporal ni una estimación nacional. En S2 el test es anterior al train. No habrá selección de umbral, época, semilla, arquitectura o checkpoint por resultado del test.

## Referencias y alcance observable

Las referencias serán revisiones visuales de IA con procedencia `ai_visual_review`, autor explícito y polígonos `reviewed_area` congelados. No se presentarán como revisión humana o referencia independiente. Solo las áreas observables y efectivamente revisadas aportarán positivos o negativos; muelles ambiguos, sombras, nubes y regiones excluidas no se convertirán en negativos. Las etiquetas vacías son válidas únicamente cuando existe un área explícita revisada. Las zonas sin área revisable se omiten y se informan como ausencia de soporte.

El coordinador entregará datasets inmutables de train y test por sensor con hashes de manifiestos, etiquetas, bandas y máscaras. El script comprobará esos hashes y la separación antes de cualquier entrenamiento. La inspección previa del test se limita a metadatos, integridad y separación; no se utilizará su contenido para modificar el protocolo.

## Ajuste prefijado

- Pesos iniciales: únicamente los checkpoints base públicos fijados en `config/models/s1.yaml` y `config/models/s2.yaml`; no se reutilizan los modelos de smoke previos.
- SHA256 S1: `35645431fb58c410603ea3e4af4c7654218389fc8bf953fe3d7f83e5629b5d2a`.
- SHA256 S2: `99f9006a8f0d3ca1e5426567713ef03533d9378d1f70ecdb4990b8a98a69e9fe`.
- Tres épocas exactas; AdamW con learning rate `2e-5`, weight decay del adaptador existente (`0.01`), batch de una observación; orden del manifiesto congelado.
- Backbone Swin congelado en modo evaluación; FPN, RPN y cabeza Faster R-CNN ajustables. No se afirma que toda la pirámide esté congelada.
- CPU, cuatro hilos PyTorch, semilla `20261021` para Python, NumPy y PyTorch. MPS está disponible y CUDA no, pero no se cambiará de dispositivo según resultados. Los informes registrarán versiones, duración y pico de memoria.
- Recortes numéricos de hasta 512 × 512, receta radiométrica original por sensor, máscaras y área revisada; ninguna imagen PNG se usará como entrada de inferencia o entrenamiento.
- Se registrarán soporte positivo/negativo, pérdidas por paso y parámetros efectivamente modificados. Un ajuste sin positivos se identificará como ajuste solo de negativos y no demostrará aprendizaje de detección positiva.
- Una ejecución por sensor; no se ensayarán varias semillas ni se escogerá una época. Cualquier corrección por fallo técnico se documentará antes de reanudar, conservando los artefactos parciales.

## Evaluación prefijada

Una sola inferencia del detector base y una sola del detector ajustado sobre el mismo test congelado. Umbral de score S1 `0.7`, S2 `0.8`; IoU `0.5`; emparejamiento uno a uno en orden descendente de score. Se mantienen los filtros y la fusión de centros del adaptador, incluida su limitación de 10 píxeles para buques próximos. No se aplica un borrado adicional por cercanía al muelle.

Se guardarán predicciones, TP, FP, FN, precisión, recall, falsos positivos por observación, intervalos Wilson del 95 %, soporte por puerto/sensor/zona y número de adquisiciones independientes. Los intervalos por objeto no corrigen la dependencia de objetos de una misma adquisición y, con una adquisición de test por sensor, no constituyen intervalos de generalización entre escenas. Una métrica sin denominador será `null`, nunca un cero o un cien por ciento inventado.

El umbral de soporte descriptivo se fija en 30 predicciones y 30 referencias por estrato; por debajo se informará `insufficient_support`. Ese mínimo no habilita publicación. Los resultados serán siempre experimentales: la referencia es IA, existe una sola adquisición de test por sensor, y las áreas ambiguas quedan fuera. Ningún valor observado, aun si supera 90 %, autoriza afirmar precisión independiente ≥90 % o publicación automática. El reporte comparará base y ajuste sin seleccionar el ganador para desplegarlo.

Aclaración de alcance solicitada por el coordinador antes de ejecutar: sí se puede informar precisión ≥90 % **frente a las etiquetas de IA revisadas** si los conteos reales y el soporte prefijado lo sostienen; su procedencia no impide calcular esa métrica. Esta afirmación acotada es distinta de validación humana o generalización científica entre adquisiciones. La ampliación posterior del usuario al procesamiento de 824 adquisiciones no altera este primer experimento. Si el test falla, las mejoras posteriores requieren datos de desarrollo adicionales y un nuevo test reservado; este test no se reutilizará para escoger el ajuste.

## Atributos y cambios

No se entrenarán familias, eslora o manga a partir de apariencia incierta ni de las dimensiones de cajas de detección. Requieren fuentes independientes para cada atributo, procedencia por objeto y soporte por clase. La resolución de 10 m y las etiquetas visuales de IA actuales no aportan esa evidencia.

No se entrenará ni evaluará un modelo de cambios con estos datos: existe una sola fecha objetivo por puerto y sensor, sin pares revisados con clases de cambio. Los contextos históricos S1 utilizados por el detector no son etiquetas de cambio. No se generarán cambios sintéticos para presentarlos como validación local.

## Ejecución y resultados

`scripts/train_local_pilot.py` recibirá un sensor, los dos directorios congelados, sus hashes esperados, el hash de este protocolo y un directorio nuevo de salida. La ejecución usará `uv run --no-sync python`; no sincronizará ni reinstalará el entorno. No escribirá en la base de datos, la interfaz ni el release. Los modelos exportados serán versiones nuevas marcadas como experimentales, con autoría IA y sin publicación automática.

El siguiente requisito para una validación independiente es reunir y revisar más adquisiciones por sensor, con referencias verificables ajenas al modelo y una partición de calibración separada. No se sustituye ese requisito por el número de zonas o cajas de este piloto.
