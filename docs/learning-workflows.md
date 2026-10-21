# Entrenamiento y evaluación reproducibles

Estos comandos consumen referencias revisadas. Los pilotos públicos incluidos verifican la ejecución; no sustituyen etiquetas de los puertos. Instala primero `uv sync --frozen --no-editable --extra ml --extra quality`. Los directorios de destino deben ser nuevos para conservar versiones inmutables.

## Buques

Revisa escenas completas mediante el visor, añade los buques omitidos y resuelve todas las propuestas. Asigna grupos relacionados y un split a cada escena antes de exportar. Las geometrías y hashes del área revisada quedan congelados en el dataset; los píxeles fuera de esa área no se convierten en negativos de entrenamiento.

```sh
uv run --no-sync puertogt dataset data/datasets/train-v1 --split train --version train-v1
uv run --no-sync puertogt dataset data/datasets/calibration-v1 --split calibration --version calibration-v1
uv run --no-sync puertogt dataset data/datasets/test-v1 --split test --version test-v1
uv run --no-sync puertogt train-detector data/datasets/train-v1 data/trained/s2-local-v1 \
  --model config/models/s2.yaml --version s2-local-v1 \
  --reserved data/datasets/calibration-v1/manifest.json \
  --reserved data/datasets/test-v1/manifest.json
```

Usa datos del mismo sensor que el manifiesto. Repite el ciclo para S1 con su receta. Define el soporte mínimo y los estratos requeridos en el protocolo antes de mirar el test. `calibrate-detector` consume exclusivamente `calibration`; `evaluate-detector` consume exclusivamente `test`. Ambos exigen `--min-support`; la evaluación exige además uno o varios `--stratum 'puerto|sensor|zona'`. Consulta `--help` para los argumentos. El umbral elegido en calibración debe incorporarse a una nueva versión auditada del manifiesto antes de evaluar. Un informe no cambia automáticamente la política de publicación.

```sh
uv run --no-sync puertogt calibrate-detector --help
uv run --no-sync puertogt evaluate-detector --help
```

## Familias y dimensiones

Cada referencia necesita procedencia por campo. Una caja del detector no sirve como eslora o manga. La familia puede ser indeterminada y las dimensiones pueden permanecer nulas. Configura el encoder del sensor en el manifiesto de atributos, extrae características del conjunto train y entrena una nueva versión:

```sh
uv run --no-sync puertogt cache-features data/datasets/train-v1 --model config/models/s2.yaml
uv run --no-sync puertogt train-attributes --help
uv run --no-sync puertogt evaluate-attributes --help
```

`train-attributes` recibe dataset, destino, `--model`, `--version` y los manifiestos `--reserved`. `evaluate-attributes` recibe dataset test congelado, archivo de informe y `--model`; su umbral opcional debe haberse elegido fuera del test. El informe separa clasificación, abstención, error por dimensión y comparación con la mediana de entrenamiento. La publicación exige evidencia suficiente por campo y por estrato.

## Cambios físicos

El dataset de cambios usa `puertogt-reviewed-change-pairs-v1`, documentado con su esquema completo en [change_workflows.py](../src/puertogt/ml/change_workflows.py). Cada par contiene dos `RasterBundle`, hashes de todos los rásteres, identidad y fecha de ambas adquisiciones, grupos relacionados y una máscara revisada de cambio/no cambio. Se requieren positivos y negativos, máscaras de observabilidad y área estática, y medidas de alineación y comparabilidad radiométrica. No basta marcar dos imágenes como comparables.

El test añade `frozen: true`, puerto, sensor, zona y revisión humana atribuida. Ninguna adquisición ni grupo relacionado puede compartirse con entrenamiento. Los límites de calidad, umbral de cambio y superficie mínima se fijan con entrenamiento/calibración y quedan en el manifiesto del modelo antes de usar el test.

```sh
uv run --no-sync puertogt train-change data/change-pairs/train-v1 data/trained/change-v1 \
  --model config/models/change.yaml --version change-v1 \
  --reserved data/change-pairs/calibration-v1/manifest.json \
  --reserved data/change-pairs/test-v1/manifest.json
uv run --no-sync puertogt evaluate-change data/change-pairs/test-v1 reports/local-change-test.json \
  --model data/trained/change-v1/manifest.json
```

El manifiesto inicial está sin entrenar y no tiene umbrales validados; los comandos fallan explícitamente si falta evidencia requerida. La evaluación conserva el umbral del modelo, informa IoU, precisión, recall, áreas de error y falsos avisos en negativos por puerto/sensor/zona. No certifica persistencia temporal.

Para registrar candidatos y su evidencia posterior:

```sh
uv run --no-sync puertogt compare-changes data/pairs/t0.json data/pairs/t1.json \
  --model data/trained/change-v1/manifest.json
uv run --no-sync puertogt track-change --help
```

`track-change` recibe el identificador del candidato, un bundle de una adquisición posterior, `--changed true|false|unknown` y `--author`. Una observación óptica válida t2 con cambio confirma persistencia; una válida posterior sin cambio registra reversión. Nubes y radar no confirman ni revierten. Toda evidencia queda auditada.

## Histórico

`inventory` y `update` descubren metadatos; no descargan ni preparan escenas. Los scripts del [piloto](../reports/pilot/inference.md) descargan recortes públicos acotados y producen bundles numéricos. `enqueue --bundle` permite procesarlos de forma trazable. Sin un bundle preparado, `worker` exige activos numéricos locales en la escena; no contiene un descargador masivo implícito.

Antes de `backfill`, deben materializarse las áreas y escenas necesarias, medirse recursos para zonas completas y registrarse el gate técnico correspondiente. El gate incluido permanece cerrado porque solo se midieron recortes y faltan modelos validados. La cola y los informes permiten continuar el procesamiento en lotes; el histórico pendiente no se presenta como completado.
