# Atributos con referencias públicas

El experimento `public-attributes-v1` entrena nuestros cabezales de familia,
eslora y manga con atributos publicados por Ai2. Su evaluación es pública y
externa: no valida los puertos de Guatemala ni habilita atributos automáticos en
el visor. El plan y la selección se fijaron antes de extraer features o ajustar
el modelo. Las 48 muestras se materializaron y el experimento terminó. El recibo
congelado es `reports/public-attributes/experiment-result-v1.json`; los recibos
operativos permanecen en `data/public-attributes/v1/`.

El ajuste usó 34 referencias TRAIN elegibles y 150 actualizaciones durante
50 épocas. TEST se evaluó una sola vez: tres de siete familias correctas
(42,86 %; macro F1 0,267), MAE de eslora 71,75 m y de manga 11,43 m. Las medianas
calculadas solo en TRAIN dieron errores de 96,21 m y 14,00 m, respectivamente,
sobre esos mismos siete objetos. El bloque completo de features, ajuste y
evaluación tomó 11,18 segundos con cuatro hilos CPU. Son resultados de un
conjunto público pequeño; la política sigue `experimental`, sin ningún campo
validado y con `automatic_publication: false`.

Las referencias proceden de [metadata.sqlite3 en el repositorio oficial
vessel-detection-sentinels](https://github.com/allenai/vessel-detection-sentinels/blob/60b003d059937a9fcddbf6e1c9b64c00c33c0279/data/metadata.sqlite3),
commit `60b003d059937a9fcddbf6e1c9b64c00c33c0279`. Su SHA256 es
`d3a8bbd6c6685a6f8bb685f122b37fda613649d6da4c450e3240cace353ca2ac`.
Cada campo conserva el identificador de ventana, etiqueta, referencia pública y
hash de esos metadatos. No se presenta como una revisión humana local ni se
atribuye a AIS un campo cuya procedencia específica no está demostrada.

La selección conserva los splits originales: 36 ejemplos TRAIN, cinco de
calibración y siete TEST. Excluye las pasadas de adquisición que el conjunto
original compartía entre splits. Los MMSI no están publicados; la separación de
identidades físicas y el posible solapamiento con el preentrenamiento del encoder
siguen desconocidos. TEST no contiene las familias pasajeros o pesquero. Su
tamaño y cobertura no permiten sostener una precisión general del 90 %.

La enmienda `numeric-eligibility-amendment-v1.json` se fijó antes de entrenar,
tras encontrar dos centros TRAIN sin datos en las imágenes originales. Las 48
referencias permanecen seleccionadas, con sus productos y evidencia intactos.
Una muestra resulta numéricamente elegible si su máscara válida no está vacía,
el píxel central está observado y sus nueve bandas son finitas y distintas de
nodata en ese punto. Un contexto parcialmente válido se conserva cuando el
centro sí es válido, informando su fracción observada. La auditoría no abre
etiquetas ni predicciones para decidir elegibilidad. Las filas TRAIN no
elegibles se excluyen del ajuste sin reemplazarlas; los casos CAL/TEST se
conservan como abstenciones en los denominadores, nunca como aciertos.

Se descargan las nueve bandas numéricas del producto SAFE original, fijadas por
generación de objeto en GCS: B04, B03, B02, B05, B06, B07, B08, B11 y B12. Cada
contexto ocupa 128 × 128 píxeles a 10 m en UTM local, centrado en la ventana
pública original. Su polígono es un ámbito de contexto, no una caja del buque ni
una etiqueta de presencia. Las dimensiones de referencia nunca se usan para
construir esa geometría. La inferencia local de este modelo usa exactamente la
misma política `fixed_center_context_v1`; si el raster no contiene el contexto
completo, se abstiene. Los modelos anteriores conservan su política de ROI.

El importador procesa una muestra a la vez, sin detector ni modelo de nubes.
Conserva máscaras de datos válidos y no sustituye observaciones ausentes por
ceros. La ausencia de una máscara de nubes calculada no demuestra cielo limpio.
Los límites son 2 GiB transferidos en total, 128 MiB por ejemplo, 4 GiB de volumen
y 10 GiB libres reservados. Un ledger independiente registra cada reserva antes
del I/O. Las reanudaciones conservan límites, selección y productos fijados.

La ejecución original usó una banda a la vez mientras corría el histórico. La
enmienda `performance-amendment-v1.json`, fijada antes de entrenar o evaluar TEST,
autoriza cuatro bandas concurrentes y conserva todos los demás límites. Esta
modalidad exige pausar el histórico en frontera y mantiene su `campaign.lock`
durante la importación para que el máximo global siga siendo cuatro. El recibo
de interrupción conserva los rangos parciales, intentos y reservas previos; el
cambio de concurrencia tiene un journal propio y no sustituye muestras.

Desde la raíz del repositorio, con las dependencias de ML ya instaladas y la
pausa del histórico confirmada, estos comandos reanudan la versión enmendada:

```bash
PYTHONPATH=src .venv/bin/python scripts/import_public_attributes.py materialize \
  --selection reports/public-attributes/selection-v1.json \
  --selection-sha256 a84191e3a86bb883cef816afce4576e8cf9c97fb8ccfb7afeee96347ee79d7df \
  --destination data/public-attributes/v1 --io-workers 4 \
  --performance-amendment reports/public-attributes/performance-amendment-v1.json \
  --amendment-sha256 8afe88d06ab24d9cd5d63508d99a391bf04594f8924f7d21c310f2495f243628

PYTHONPATH=src .venv/bin/python scripts/import_public_attributes.py finalize \
  --selection reports/public-attributes/selection-v1.json \
  --selection-sha256 a84191e3a86bb883cef816afce4576e8cf9c97fb8ccfb7afeee96347ee79d7df \
  --destination data/public-attributes/v1
```

No ejecutar dos importadores contra el mismo destino. `finalize` exige los 48
ejemplos completos y produce datasets portables e inmutables. Si hay fallos, se
revisan sus recibos y se reanuda la misma selección; no se reemplazan muestras
porque su imagen o resultado resulte desfavorable.

Después de `finalize`, este comando congela la elegibilidad de los 48 ejemplos
sin modificar el dataset original. Rechaza sobrescribir un informe existente:

```bash
PYTHONPATH=src .venv/bin/python scripts/audit_public_attribute_eligibility.py \
  --selection reports/public-attributes/selection-v1.json \
  --amendment reports/public-attributes/numeric-eligibility-amendment-v1.json \
  --amendment-sha256 86edf951bcdf3594f8b0fe67dd748869b87a4a3a60d6451f6a29de9b52de737c \
  --dataset-root data/public-attributes/v1/datasets \
  --destination reports/public-attributes/numeric-eligibility-report-v1.json
```

Tras coordinar la pausa del histórico en frontera y verificar que liberó ambos
locks, se puede ejecutar el experimento ya fijado:

```bash
PYTHONPATH=src OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 \
  .venv/bin/python scripts/train_public_attributes.py \
  --destination data/public-attributes/v1/experiment-v1 \
  --performance-amendment reports/public-attributes/performance-amendment-v1.json \
  --amendment-sha256 8afe88d06ab24d9cd5d63508d99a391bf04594f8924f7d21c310f2495f243628 \
  --numeric-amendment reports/public-attributes/numeric-eligibility-amendment-v1.json \
  --numeric-amendment-sha256 86edf951bcdf3594f8b0fe67dd748869b87a4a3a60d6451f6a29de9b52de737c \
  --eligibility-report reports/public-attributes/numeric-eligibility-report-v1.json \
  --eligibility-report-sha256 fe1af64b9c731443e3567b91f13e01622db71b16ad2a284a34687088fbc31140
```

El hash corresponde al informe congelado: 34 de 36 referencias TRAIN elegibles,
cinco de cinco de calibración y siete de siete TEST. El runner comprueba ese hash
y vuelve a verificar todos los rasters, decisiones y fuentes fijadas antes de
extraer features.

El runner exige `PAUSE`, adquiere los locks, comprueba los hashes del plan y las
fuentes, conserva una copia del código y limita el bloque a 900 segundos y cuatro
hilos CPU. Extrae features únicamente de TRAIN, congela el encoder y entrena
50 épocas, LR 0.001, batch 16 y seed 0. No busca hiperparámetros. Después congela
los pesos y evalúa TEST una vez con argmax fijo y medianas TRAIN como baseline de
dimensiones. Los cinco ejemplos de calibración quedan reservados; no se ajusta
un umbral con este conjunto pequeño.

El intento conserva `status.json`, fuentes, manifiestos, pesos, encoder portátil
y `public-test-evaluation.json`. Ante un fallo no sobrescribe el intento ni
repite TEST automáticamente. La política del artefacto mantiene
`automatic_publication: false` y ningún campo validado. La reanudación del
histórico se coordina aparte, conservando su receta y caffeinate ligado al PID.
