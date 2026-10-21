# Fase 2: muestra temporal y rondas operativas prefijadas

Autor: Codex (IA), 21 de octubre de 2026. **Protocolo aceptado por el coordinador antes de entrenar, calibrar o evaluar fase 2.** La selección siguiente usa exclusivamente metadatos del inventario fijado; no utiliza predicciones, scores, porcentaje de nube ni conteos de barcos para ordenar fechas. Es una selección para materializar y revisar, **todavía no un dataset etiquetado congelado ni una validación completada**. No modifica el protocolo ni los resultados de fase 1. Las reglas de calibración, soporte y decisión quedan fijadas para la primera ejecución de fase 2; el dataset etiquetado todavía debe congelarse.

## Fechas, unidades y separación

| Uso | Ventana, extremo final excluido | Intervalos por mes | Primarias por puerto/sensor | Adquisiciones únicas |
| --- | --- | --- | --- | --- |
| Train | 2025-01-01 a 2025-07-01 | Comienzos días 1 y 16 | 12 | 48 |
| Calibration | 2025-10-01 a 2026-01-01 | Días 1, 9, 17 y 25 | 12 | 48 |
| Test | 2026-04-02 a 2026-09-01 | Abril: 2, 12, 22; otros meses: 1, 12, 22 | 15; S1 STC/PBR: 11 | 56 |

En cada intervalo, puerto y sensor se escoge la primera adquisición elegible por fecha; el desempate es `acquisition_key`. Las restantes son reservas en ese mismo orden. La selección completa contiene **152 adquisiciones primarias únicas y 226 asignaciones puerto/sensor/fecha**. STC y PBR comparten muchas adquisiciones; no se presentan como fechas independientes. Ocho intervalos de S1 Caribe —cuatro por cada puerto— no contienen un candidato elegible y permanecen vacíos.

Las ventanas abarcan distintos meses y condiciones potenciales; la estación del calendario no garantiza observabilidad. Los límites separan objetivos de particiones consecutivas por más de 92 y 91 días, respectivamente. Todo contexto S1 debe corresponder a la misma órbita relativa y sentido, tener huella solapada y encontrarse entre 30 y 90 días antes. Se elige el más próximo a 60 días, después mayor solapamiento y finalmente clave. La selección guardada comprueba que **ningún objetivo o contexto, incluidas las reservas, aparece en más de una partición**. Antes de entrenar se comprobarán también hashes de los rasters, no solo nombres de adquisición.

Se excluyen las cuatro adquisiciones del piloto inicial y sus dos contextos radar. Se incluye expresamente en train PRQ S2 del 3 de enero de 2025, elegida antes de consultar sus predicciones. La S2 Caribe de ese mismo día permanece en el registro aunque su máscara no permita observación. Su primera reserva cronológica, S2B del 5 de enero, se activa por esa falta documentada de observabilidad; esto no cambia las primarias ni utiliza scores.

Los archivos [sampling-plan.json](sampling-plan.json) y [primary-acquisitions.csv](primary-acquisitions.csv) contienen claves exactas, intervalos, reservas, contextos, exclusiones y hash del inventario. El inventario es `reports/pilot/inventory.json`, SHA256 `9f301f47ced709ee57dd1289242fd268e0a7097e5f291cf4304ce4f9901f79c3`.

## Orden práctico y ampliación

La primera ronda operativa intenta obtener **dos capturas observables de cada puerto/sensor/split**: primero un bloque train que represente los tres puertos y los dos sensores, después calibration y test. Es una ronda de preparación y revisión, no una muestra suficiente para afirmar 90 %. No se garantiza que las capturas contengan barcos. Se revisan tanto los positivos visibles como las áreas negativas; un train que siga sin positivos no puede entrenar la regresión positiva de cajas.

El orden inicial procesa las dos primeras primarias de cada estrato, intercaladas por rango temporal entre estratos; incorpora la reserva Caribe S2 ya activada. Luego repite bloques de dos primarias por estrato, en el orden train → calibration → test. Así ninguna ventana completa monopoliza el procesamiento. Cada bloque comparte las adquisiciones STC/PBR sin duplicarlas. [execution-order.json](execution-order.json) fija el orden, los hitos y el motivo de la reserva. Su copia operativa es `data/history/full-v1/priorities.json`; el runner la relee entre adquisiciones.

La cuota se refiere a capturas **observables**, no simplemente descargadas. Solo se activa una reserva para un objetivo sin acceso tras los reintentos del pipeline, sin bandas/contexto compatibles, sin máscara de observabilidad válida o sin área marina observable que pueda revisarse. Una escena parcial con área revisable se conserva con esa área explícita. Nubes, sombras, muelles ambiguos y fallos de acceso quedan registrados como abstenciones o exclusiones, nunca como cero barcos. Una ausencia de barcos no permite sustituir una escena. Las reservas se activan por orden cronológico dentro del intervalo y cada activación registra causa y fecha; no se recogen scores para esta decisión.

Si la primera ronda no aporta soporte suficiente, se continúa automáticamente con las siguientes primarias ya listadas en rondas equilibradas de dos por estrato. La siguiente ronda se activa por soporte de etiquetas y cobertura, antes de consultar resultados del test. Al agotarse las primarias, cualquier ampliación toma una oleada completa de reservas por rango entre los seis estratos, documentada y congelada antes de inferir el test. No se agregan fechas porque la precisión observada sea mala ni para buscar un resultado favorable. La selección puede materializarse completa aunque una ronda acotada sea suficiente para iniciar revisión o entrenamiento de desarrollo.

## Etiquetas y soporte

El objetivo práctico de soporte es al menos **30 observaciones únicas de barcos y 30 predicciones únicas por puerto/sensor**, con al menos diez pasadas objetivo distintas de test por estrato. Las treinta referencias no tienen que existir en cada tipo de zona. Canal, fondeo y áreas de muelle efectivamente revisables pueden aportar positivos. Los patios terrestres sirven como controles negativos: se informa FP por observación y área revisada, sin exigirles barcos. Se informan por separado las abstenciones y superficies excluidas de los muelles.

La ampliación previa al test puede depender de cuántas referencias haya, pero no del número de predicciones del modelo reservado. Si al evaluar hay menos de 30 predicciones, el resultado se informa con soporte insuficiente; no se añade un nuevo lote al mismo test para aumentar su denominador después de ver precisión. El mínimo de 30 tampoco garantiza un intervalo de confianza favorable.

Dos revisiones de IA deben comenzar por evidencia numérica/vistas sin superposición del detector, registrando autor, herramientas, versión, alcance revisado y cualquier exposición previa a propuestas. Una adjudicación explícita resuelve diferencias. La procedencia sigue siendo IA; se puede medir precisión frente a estas referencias sin llamarla revisión humana. La fase 1 no fue ciega y esa limitación no se hereda como afirmación falsa de ceguera para las nuevas etiquetas.

Cada objeto mantiene `unique_physical_object_key` común a las zonas solapadas de una misma pasada. Las etiquetas se dibujan alrededor de la respuesta visible del barco, sin agrandarlas para coincidir con predicciones. Las métricas globales cuentan cada objeto una vez; las pertenencias y resultados por zona se presentan aparte. En S1, segmentos de una misma órbita absoluta y datatake son una pasada; en S2, tiles del mismo satélite, instante y órbita relativa también se agrupan. La misma embarcación en fechas distintas es una nueva observación, pero no demuestra diversidad de identidad del buque. Las cajas visuales no aportan eslora, manga ni familia verificadas.

## Entrenamiento y camino de inferencia

El ajuste parte nuevamente de los checkpoints públicos S1/S2 fijados, no de los ajustes de fase 1 entrenados solo con negativos. Mantiene backbone congelado, FPN/RPN/cabeza ajustables, tres épocas, AdamW `2e-5`, CPU cuatro hilos y semilla `20261021`. Se necesita al menos un positivo real en cada puerto del train, además de áreas negativas revisadas; ninguna etiqueta se sintetiza para completar cuotas. Un fallo técnico se documenta antes de reanudar. No se selecciona una semilla o época usando test.

Solo train se deriva en parches de **512 × 512 con solapamiento de 64 píxeles**, preservando `reviewed_area`, adquisición y observación padre. Los parches no crean unidades independientes ni cruzan particiones. Calibration y test conservan sus mosaicos completos. La receta radiométrica, bandas, unidades y contexto temporal siguen siendo numéricos y compatibles con el manifest; PNG sirve exclusivamente para revisión.

El contrato de evaluación conserva la diferencia entre los dos caminos: el histórico usa teselas de 512 con **núcleos 448 y márgenes 32**; el detector genérico recorre ventanas de 512 con desplazamientos de 460. Calibration y test deben ejecutar exactamente el camino que se desplegará, fijando cuadrícula, propiedad de bordes, máscara, fusión y deduplicación. No se usarán métricas obtenidas con ventanas genéricas para certificar otra estrategia de mosaico. Una comparación entre caminos, si se realiza, será diagnóstico de desarrollo separado. El runner invoca directamente `history.infer_tiles` sobre las teselas completas, una vez por adquisición; las vistas y mosaicos se usan para etiquetas y alcance, no para reconstruir márgenes. Las pruebas sintéticas de la ruta no equivalen a resultados de precisión local.

## Métrica y calibración fijadas

Se conserva **IoU 0,5**, emparejamiento uno a uno por score, precisión y recall, FP por observación, soporte por puerto/sensor, conteos por zona y por objetos únicos, y cobertura/abstención. La distancia entre centroides es un diagnóstico separado y nunca cambia la decisión de acierto. Se presentan intervalos Wilson por objeto y dependencia por pasada; la estimación de variación entre pasadas requiere suficiente número de grupos.

Se calibrará un umbral por sensor exclusivamente en calibration sobre la cuadrícula `0.10, 0.11, …, 0.99`, conservando el mismo checkpoint de tres épocas. Elegir el umbral con mayor recall que alcance precisión puntual ≥0,90 en cada uno de los tres puertos, con al menos 30 referencias y 30 predicciones únicas por puerto y recall ≥0,80; empates por mayor precisión y luego menor umbral. Si ningún candidato cumple, registrar el fallo de calibración y continuar desarrollo sin abrir el test. La cuadrícula y el guardarraíl de recall evitan declarar éxito mediante abstención casi total. El comparador base conserva los umbrales originales S1 0,7 y S2 0,8.

Antes del test se congelan manifests de datos, etiquetas, áreas revisadas, pesos, umbrales, pipeline y hashes. Se evalúan una vez base y ajustado. Se puede afirmar una precisión puntual ≥90 % **frente a las etiquetas de IA**, únicamente en los estratos que realmente la alcancen con soporte y cobertura declarados. Una afirmación más fuerte de que la precisión supera 90 % con 95 % de confianza requiere además un límite inferior compatible y una evaluación de dependencia entre pasadas; treinta aciertos sin fallos todavía tienen un límite Wilson inferior cercano a 88,6 %. No se confunde el piso de soporte con garantía estadística.

El test se consume al conocer sus resultados. Un fallo no se corrige cambiando IoU, cajas, exclusiones, umbral o checkpoint sobre ese mismo test. Las rondas posteriores usan más desarrollo y otro test reservado. La autoría IA permite medir concordancia, pero la validación humana y generalización científica siguen siendo afirmaciones distintas. Publicación automática y entrenamiento de atributos/cambios quedan deshabilitados hasta contar con evidencia específica suficiente.

## Requisitos antes de entrenar o evaluar

1. Materializar las rondas con máscaras, bandas, contexto S1 y hashes verificables; conservar el registro de acceso, observabilidad y exclusiones.
2. Revisar positivos y negativos de los seis estratos, resolver desacuerdos y congelar áreas, claves de objeto y manifests. Las referencias de test no orientarán decisiones del entrenador.
3. Completar el exportador de parches de train y verificar que el evaluador de mosaicos reproduce el camino final del histórico.
4. Congelar el protocolo aceptado, datos y entorno con el runner antes de entrenar. Calibración cerrada en este documento; no se modifica al conocer resultados.
5. Entrenar con positivos reales, calibrar fuera del test y evaluar una vez. Informar las ausencias o fallos sin sustituirlos por el número de zonas, parches o detecciones duplicadas.


## Evidencia y runner reproducible

El manifest raíz incluye `history_inference.version = history-tiles-v1` y `acquisitions`. Cada adquisición fija `acquisition_key`, `pass_id`, contexto S1, `expected_tile_ids`, `plan_tiles_sha256`, procedencia y **todas** las teselas que participaron en la inferencia de la adquisición. Cada entrada contiene descriptor de cuadrícula, `RasterBundle` con rutas relativas y SHA256 de cada banda/máscara. Se conservan los halos completos de 512, incluidos los de zonas sin etiquetas, porque afectan la fusión de candidatos en bordes. Un archivo alterado, tesela omitida, orden modificado o máscara de observabilidad ausente impide evaluar antes de cargar el modelo.

La evidencia retenida distingue `retained_original_prepared_bytes` de `reconstructed_from_pinned_sources`. Una reconstrucción necesita fuentes fijadas y comprobación exacta de los píxeles de todos los mosaicos publicados; declara `original_halo_hash_available=false`. No prueba identidad histórica de los halos eliminados. El experimento conserva esta procedencia y mide la nueva reproducción fijada, sin atribuirle una verificación de bytes originales inexistente.

`pass_id` es plataforma:órbita-absoluta:datatake en S1 y plataforma:timestamp:órbita-relativa en S2. Si calibration/test contienen más de una adquisición por puerto/pasada, el runner rechaza el conjunto hasta una adjudicación explícita entre segmentos. Se registra la cobertura pendiente; no se realiza una fusión silenciosa para aumentar soporte o precisión.

`scripts/train_phase2.py` separa cuatro comandos:

1. `freeze`: exige SHA explícito de los tres manifests y de este protocolo; verifica bandas, máscaras, etiquetas por bytes, separación de objetivos/contextos/pasadas/rasters y pertenencia al marco de muestreo. No interpreta geometrías de etiquetas test. Si train es derivado, exige `--train-source` y el SHA padre coincidente, sin duplicar sus halos dentro de cada versión de parches. Fija código, entorno, checkpoints y procedencia.
2. `train`: comprueba nuevamente la integridad, exige positivos por puerto y ejecuta las tres épocas. Nunca abre etiquetas de test para entrenamiento.
3. `calibrate`: requiere el recibo verificado de train; infiere calibration una vez con piso 0,10 y recorre la cuadrícula fijada. La fusión es descendente por score, por lo que eliminar scores menores después mantiene los candidatos que produciría un umbral mayor. Si ningún umbral cumple todos los puertos, el test permanece cerrado.
4. `evaluate`: exige calibración aprobada, pesos/umbral congelados y ≥30 referencias y ≥10 pasadas por puerto antes de inferir. Registra consumo del test por sensor/pasada, incluso si después hay un fallo técnico. Ejecuta base y ajustado una vez por separado sobre el mismo test. Menos de 30 predicciones o un resultado adverso se reporta; no se amplía retrospectivamente para mejorar la métrica.

Cada etapa registra inicio, final, hashes, tiempo y memoria; un inicio previo impide repetirla silenciosamente. El registro compartido de consumo en `data/experiments/phase2-test-consumption` impide eludir la reserva creando otro directorio o cambiando el manifest de un test ya evaluado. Los cachés de `infer_tiles` se escriben en copias dentro del experimento; los datasets congelados permanecen intactos. Se conserva el lock de trabajo pesado compartido y se pausa la campaña histórica en una frontera antes de entrenar o inferir.

Ejemplo de comandos, una vez entregados los tres datasets congelados y sus hashes reales:

```sh
PYTHONPATH=src uv run --no-sync python scripts/train_phase2.py freeze --help
PYTHONPATH=src uv run --no-sync python scripts/train_phase2.py freeze \
  --sensor s2 --train RUTA_TRAIN_CROPS --train-source RUTA_TRAIN_ORIGINAL \
  --train-sha256 SHA_TRAIN --calibration RUTA_CAL --calibration-sha256 SHA_CAL \
  --test RUTA_TEST --test-sha256 SHA_TEST --protocol-sha256 SHA_PROTOCOLO \
  --output RUTA_NUEVA_EXPERIMENTO
PYTHONPATH=src uv run --no-sync python scripts/train_phase2.py train RUTA_NUEVA_EXPERIMENTO
PYTHONPATH=src uv run --no-sync python scripts/train_phase2.py calibrate RUTA_NUEVA_EXPERIMENTO
PYTHONPATH=src uv run --no-sync python scripts/train_phase2.py evaluate RUTA_NUEVA_EXPERIMENTO
```

La existencia de este runner no significa que fase 2 haya sido entrenada o evaluada. Los datasets, soporte y ejecución reales siguen siendo requisitos que deben verificarse.
