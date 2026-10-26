# Cola de revisión TRAIN

La cola es una propuesta de inspección, no un dataset ni una asignación de partición en el catálogo. El primer lote usa las primarias TRAIN de fase 2 y únicamente reservas activadas explícitamente en `execution-order-v11.json`. Conserva enero–junio de 2025, el embargo de 92 días y todos los grupos/pasadas/contextos reservados de CAL/TEST, incluidas sus reservas. No amplía ni modifica los planes anteriores.

```sh
PYTHONPATH=src .venv/bin/python scripts/build_review_queue.py \
  --per-stratum 2 --minimum-observable 0.5 \
  --output data/review-queues/lote-nuevo.json
```

El destino debe ser nuevo. Con iguales metadatos, archivos de configuración y parámetros, el manifest y su identificador son idénticos. Los hashes fijan selector, productor de previews, muestreo, orden, zonas y snapshot de metadatos. Los cambios posteriores requieren otra cola; nunca se sobrescribe la anterior.

## Selección

Se piden dos pasadas por cada combinación de los tres puertos y dos sensores. Solo se consideran zonas de muelles, fondeo y canal: esta primera cola busca más referencia marina; los patios no representan negativos marinos y no se usan para llenar cuotas. Cada puerto/pasada aporta como máximo una zona, aunque existan otras recetas, recortes o segmentos. Los datatakes S1 de una misma órbita absoluta se agrupan conservadoramente como una visita. STC y PBR pueden compartir una pasada física; el informe cuenta también pasadas físicas globales.

Entre los candidatos se favorecen meses todavía no representados, mayor observabilidad local y luego fecha, adquisición, zona, receta e ID en orden estable. La observabilidad procede del área de la zona, no del porcentaje de nube de toda la escena. Se excluyen áreas sin evidencia lista o con menos de 50% observable. Este filtro puede sesgar las condiciones: es para conseguir etiquetas TRAIN y no debe heredarse por CAL/TEST.

Una observación con revisión previa, completitud o abstención queda fuera de la primera inspección; la exclusión alcanza otras representaciones del mismo puerto/pasada/zona. También se excluyen las identidades de recibos de primera inspección ya guardados, incluidas las abstenciones, sin usar notas, decisiones ni geometrías. El checksum del recibo protege su integridad; un recibo corrupto aborta la selección. Esto no garantiza que una persona nunca haya visto la imagen anteriormente. No se usan notas, cajas ni decisiones de objetos para escoger candidatos. Toda exclusión tiene un motivo fijo. Si faltan candidatos, se informa déficit en lugar de fabricar escenas o activar reservas silenciosamente.

## Contrato para el visor

`puertogt.review_queue` expone:

- `build_review_queue(store, sampling_path, order_path, ports_path, *, per_stratum=2, minimum_observable=.5) -> dict`.
- `freeze_queue(manifest, path) -> sha256_del_archivo`, creación exclusiva.
- `read_queue(path, expected_sha256=None) -> manifest`, comprueba integridad e identidad.
- `blind_entry(manifest, observation_id) -> dict`, permite exclusivamente identidad, puerto/sensor/zona/fecha, estado, fracción observable, motivo, límites estáticos de zona y referencias seguras de preview.
- `blind_asset(store, manifest, observation_id, asset_id) -> Path`, exige pertenencia al lote y SHA actual idéntico; no permite activos numéricos ni propuestas arbitrarias.

Los previews admitidos son `preview.png`, `image/png`, `role=preview`, `display_only=true`, producidos por `worker.py` directamente de bandas numéricas. El productor no dibuja detecciones ni anotaciones. Solo sus IDs, hashes, tipo, CRS y límites nativos/WGS84 salen por `blind_entry`; notas, áreas previamente revisadas, conteos, scores y procedencia del modelo no se exponen. Se excluyen previews sin CRS reconocido o límites nativos finitos y con área positiva. Se verifican presencia/tamaño de evidencia registrada y SHA del preview; no se interpreta ningún píxel para elegir el lote. Esto no reemplaza la validación científica de la máscara ni certifica calidad visual.

El primer pase del visor debe usar exclusivamente estos helpers y la ruta ciega. Consultar después la observación ordinaria revela propuestas y revisiones anteriores: debe ser un paso distinto, explícito y auditado. El estado de la cola está congelado; las anotaciones posteriores no modifican su manifest.

## Lote inicial y límite científico

El lote final se guarda en `reports/review-queue/train-primaries-final-v2/queue.json`, con resultados y distribución en el README adyacente. El lote final contiene 12 observaciones con georreferencia válida. Los manifests de preparación de esta sesión se conservaron localmente sin sobrescribirlos; no forman parte del lote publicado. No se revisaron imágenes ni se importaron etiquetas para seleccionar. No se tocaron pesos ni datasets congelados.

## Primera inspección en la aplicación

La pantalla `/review-queue` carga únicamente `/v1/review-queue/*`. Presenta la imagen cruda, la zona fija, el área delimitada y los candidatos dibujados durante esa sesión. No consulta las propuestas del detector. El aumento permite arrastrar rectángulos con precisión; también hay controles de porcentaje para dibujar con teclado.

`POST /v1/review-queue/geometry` transforma los rectángulos normalizados desde el CRS nativo del preview a WGS84 y devuelve también la geometría normalizada del contorno. El área se recorta a la zona configurada; al guardar, la API exige que los centros de candidatos queden dentro del área inspeccionada. Los porcentajes no se interpolan sobre una caja WGS84.

El resultado puede ser candidatos, área sin embarcaciones visibles o incertidumbre con explicación. El autor, tipo de revisor, duración declarada y hallazgos se fijan mediante `POST /v1/review-queue/first-pass`. La credencial permanece solo en memoria. Los reintentos conservan exactamente el mismo UUID y cuerpo, incluido el tiempo; una respuesta perdida no crea otro registro. Las respuestas asíncronas de escenas anteriores se descartan. Tras recibir el recibo correspondiente se habilita la continuación explícita al visor de esa observación. Un recibo no crea etiquetas ni demuestra precisión; una revisión de IA conserva esa procedencia.

Los borradores se conservan al cambiar de escena dentro de la página. Cerrar o recargar la página pierde los borradores que no llegaron al servidor; la aplicación avisa antes de salir. Los registros confirmados siguen en el almacenamiento de recibos. La separación de la pantalla evita propuestas durante este flujo; no puede certificar que el revisor nunca haya visto la escena por otra vía.

Pruebas locales: `PYTHONPATH=src .venv/bin/pytest tests/test_review_queue.py tests/test_review_intake.py -q` y `node --test tests/review-queue.test.cjs`. Cubren exclusiones reservadas, deduplicación de pasadas, consulta ciega, geometría exacta, inmutabilidad, reintentos y respuesta asíncrona obsoleta. La inspección visual de la interfaz se realiza además en el navegador.

La cola facilita obtener etiquetas; no demuestra precisión. El control interno previo fue 85% sobre 20 referencias y el 98% posterior es ajuste sobre TRAIN; Santo Tomás todavía omitía su único positivo. Faltan más ejemplos del Caribe, referencias independientes y una nueva CAL no expuesta que cumpla los cortes temporales, antes de abrir TEST.

Existe una opción pública para explorar: Global Fishing Watch ofrece [clave API gratuita](https://globalfishingwatch.org/our-apis/) y [presencia AIS global](https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/key-concepts). Es una fuente candidata independiente de este detector, pero aquí no se verificaron cobertura local, correspondencias a la hora de las capturas ni derechos para redistribuir cada producto. Sus detecciones SAR/S2 son salidas de otros modelos; no constituyen automáticamente verdad de referencia. Ninguna ausencia AIS se convierte en etiqueta negativa. No se conectó ni descargó otra fuente durante esta tarea.


## Primera inspección realizada

El lote inicial ya conserva doce recibos IA, con 41 candidatos, dos áreas sin embarcaciones visibles y cinco abstenciones. No se modificaron las revisiones SQL ni se importaron etiquetas al entrenamiento. [Hallazgos, tiempos y alcance](../reports/improvements/20261026-increment/README.md). La cola activa conserva sus doce entradas congeladas; reconstruir una nueva cola excluye esas primeras inspecciones. Mantener la cola original permite otra revisión explícita de sus imágenes, sin revelar sus hallazgos anteriores en esta pantalla.
