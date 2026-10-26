# Instalación y operación local

Satélite Portuario separa el catálogo, el procesamiento y la revisión. La API consulta resultados registrados; descargar, preparar, entrenar e inferir son operaciones explícitas fuera de HTTP.

## Requisitos

- Python 3.11, recomendado y probado. El paquete admite Python 3.11 a 3.13.
- `uv` y Git. Las dependencias opcionales incluyen repositorios fijados a commits concretos.
- Espacio para el catálogo, recortes numéricos, evidencias y pesos. Dimensiona el volumen con el piloto antes del histórico.
- Docker con Compose si se usa el contenedor. Las imágenes base y ML se probaron en ARM64, con inferencia CPU real de ambos sensores. ML en x86_64 sigue sin comprobarse.

Ejecuta desde la raíz del repositorio:

```sh
uv sync --frozen --no-editable
uv run --no-sync puertogt --data-dir data init
uv run --no-sync puertogt --data-dir data doctor
uv run --no-sync puertogt --data-dir data serve
```

Abre `http://127.0.0.1:8000/`. La API está en `/docs` y la salud en `/health`. Una instalación vacía muestra que no hay observaciones. `doctor` distingue servicio operativo de evidencia científica suficiente; `doctor --check-release` devuelve código 2 mientras falte evidencia de entrega.

Para inferencia o entrenamiento, instala los extras:

```sh
uv sync --frozen --no-editable --extra ml --extra quality
```

Se usa instalación no editable para que el paquete funcione también en workspaces macOS que marcan archivos `.pth` como ocultos. Después de modificar fuentes, repite `uv sync --frozen --no-editable --reinstall-package satelite-portuario` con los extras que utilices.

`ml` incluye PyTorch, rslearn y Satlas; `quality` añade OmniCloudMask. El CLI permite `--device cpu` o `--device mps`. La disponibilidad de MPS depende del equipo. El HTTP básico funciona sin estos extras.

## Configuración

| Nombre | Propósito |
|---|---|
| `PUERTOGT_DATA_DIR` | Directorio persistente del catálogo, activos, pesos y trabajos. `--data-dir` lo puede sustituir. |
| `PUERTOGT_CONFIG_DIR` | Directorio que contiene geometrías, recetas y límites de ejecución. |
| `PUERTOGT_REVIEW_TOKEN` | Credencial Bearer para guardar revisiones. Sin ella, los POST de revisión responden 503. |
| `PUERTOGT_REVIEW_QUEUE` | Manifest congelado de la cola TRAIN; por defecto `<data-dir>/review-queue.json`. |
| `PUERTOGT_GEOGRAPHY_PROPOSAL_DIR` | Directorio opcional del piloto V2; se consulta separado de las zonas activas. |
| `PUERTOGT_CONTAINER` | Marca interna del contenedor para permitir su enlace de red. |

La aplicación lee el entorno del proceso; no carga `.env` automáticamente. `.env.example` documenta los nombres. Define la credencial fuera del repositorio antes de iniciar el servicio y no la incluyas en capturas, reportes o argumentos públicos. En la interfaz se introduce como contraseña y permanece únicamente en memoria hasta recargar o cerrar el acceso.

`config/runtime.yaml` contiene leases, reintentos, intentos máximos y reserva de almacenamiento. La cola ejecuta un trabajo pesado a la vez. Ajusta el límite del volumen con las mediciones del piloto. Los errores de recursos pausan el trabajo; `resume` permite retomarlo después de corregir la causa.

La configuración predeterminada se empaqueta con la aplicación. Las doce geometrías de `config/ports.geojson` son provisionales; su procedencia y límites están en [geometries.md](../reports/pilot/geometries.md).

## Catálogo y actualización

Importa el inventario público ya incluido:

```sh
uv run --no-sync puertogt --data-dir data import-scenes reports/pilot/inventory.json
uv run --no-sync puertogt --data-dir data history-report reports/local-history
```

El inventario al corte `2026-10-21T01:40:47.815329Z` contiene 863 adquisiciones únicas: 828 desde 2025 y 35 de contexto previo. Importarlas no genera observaciones. Las representaciones SAFE y COG se agrupan por adquisición; radar y óptico permanecen separados.

`inventory` consulta metadatos actuales y acepta `--start`, `--end`, `--port` repetible y `--output`. Las fechas requieren zona horaria, el inicio es inclusivo y el final exclusivo. `update` vuelve a consultar una ventana solapada para captar publicaciones tardías; una adquisición aún más antigua necesita `inventory` explícito. El catálogo público no requiere credenciales; descargar productos completos puede depender del proveedor y de su autenticación.

El script `scripts/inventory.py` genera el informe detallado con páginas, hashes, representaciones y cobertura mensual. Usa consultas mensuales S2 y reanuda con `--resume` solo si coinciden geometrías, intervalo y corte. El inventario completo se ejecutó sin descargar escenas masivas.

## Procesamiento y aprendizaje

Consulta las opciones vigentes antes de preparar un trabajo:

```sh
uv run --no-sync puertogt enqueue --help
uv run --no-sync puertogt worker --help
uv run --no-sync puertogt dataset --help
uv run --no-sync puertogt cache-features --help
uv run --no-sync puertogt train-detector --help
uv run --no-sync puertogt train-attributes --help
uv run --no-sync puertogt evaluate-detector --help
uv run --no-sync puertogt calibrate-detector --help
```

`enqueue` recibe un archivo `Scene`, una zona y un manifiesto de modelo; puede incorporar un `RasterBundle` numérico preparado. `worker` procesa una cantidad acotada de trabajos. Ambos admiten `--attributes-model`, cuya versión entra en la identidad de procesamiento. El modelo experimental exige `--allow-experimental` al ejecutar el worker; sus resultados siguen siendo propuestas para revisión.

`dataset` congela escenas revisadas completas. `cache-features` extrae características del encoder fijado. `train-detector` y `train-attributes` entrenan con el conjunto `train`, guardan una versión nueva y admiten manifiestos reservados mediante `--reserved`. La calibración usa `calibration`; la evaluación independiente usa `test` y exige soporte mínimo y estratos explícitos. Consulta [el protocolo](labeling-protocol.md) y las [tarjetas de modelos](model_cards/vessels.md).

La ayuda de estos comandos se verificó. El entrenamiento con etiquetas locales y la precisión científica requieren completar la revisión independiente; las pruebas automatizadas con datos de prueba no sustituyen ese trabajo. No cambies una política a `validated` por haber ejecutado el programa.

`backfill` exige un informe de piloto con mediciones por escena y autorización registrada para procesamiento masivo. Procesa lotes acotados y conserva adquisiciones no programadas en el informe de cobertura. El estado publicado del piloto se encuentra en `reports/pilot/`.

## Exportar y restaurar

Usa directorios nuevos para cada snapshot y restauración:

```sh
uv run --no-sync puertogt --data-dir data export backups/snapshot-01
uv run --no-sync puertogt restore backups/snapshot-01 data-restored
uv run --no-sync puertogt --data-dir data-restored doctor
uv run --no-sync puertogt --data-dir data-restored serve --port 8001
```

El bundle es un directorio con catálogo consistente, configuración sanitizada, activos y pesos registrados, y manifiesto SHA256. La restauración verifica archivos y referencias antes de publicar el destino. Rechaza sobrescribir datos existentes. Los trabajos que estaban ejecutándose se convierten en reintentables; ningún lease del equipo original se reutiliza.

`config.json` conserva la configuración exportada como referencia; el arranque sigue leyendo `PUERTOGT_CONFIG_DIR` o la configuración empaquetada. Si restauras geometrías o límites personalizados, repón los archivos correspondientes en un directorio de configuración y apunta esa variable a él. La credencial de revisión se configura nuevamente.

Las exportaciones de análisis aceptan `--format geojson` o `--format parquet`. Parquet guarda la geometría como texto GeoJSON WGS84; no se presenta como GeoParquet. Estas salidas excluyen registros rechazados y no sustituyen el bundle de restauración.

## Docker

Define `PUERTOGT_REVIEW_TOKEN` en el entorno de Compose y ejecuta:

```sh
docker compose up --build api
```

El puerto del host queda ligado a localhost y el volumen `observatorio` conserva `/data`. El perfil opcional `worker` instala ML y espera un manifiesto de modelo activo dentro del volumen. Su ejecución requiere pesos, entradas numéricas y máscaras de calidad válidas; no descarga ni entrena automáticamente al arrancar la API. No uses `docker compose down -v` si necesitas conservar el volumen.

Los modelos incluidos en el piloto son experimentales. Con el manifiesto en `/data/active-model.yaml`, sus pesos, entradas y trabajos ya preparados en el volumen, ejecuta un trabajo de prueba con la habilitación explícita:

```sh
docker compose --profile worker run --rm worker puertogt worker \
  --model /data/active-model.yaml --allow-experimental --max-jobs 1
```

Sin `--allow-experimental`, el worker conserva el bloqueo de modelos sin validar. Habilitarlo no cambia la política de publicación del modelo ni certifica las propuestas.

Para construir explícitamente la imagen de consulta y la imagen CPU con ML en ARM64:

```sh
docker build --platform linux/arm64 -t satelite-portuario:local .
docker build --platform linux/arm64 --build-arg INSTALL_ML=true -t satelite-portuario:ml .
```

Las imágenes base de Python y `uv` están fijadas por digest; la aplicación se instala sin modo editable y empaqueta su configuración e interfaz. La variante ML instala `build-essential` y `libgdal-dev`, porque Fiona puede necesitar compilar contra GDAL en Linux ARM64. Esos paquetes no se añaden a la variante de consulta. La instalación desde fuente requiere headers y `gdal-config`, según la [documentación de Fiona](https://fiona.readthedocs.io/en/stable/install.html).

En la primera compilación ARM64 registrada, esa capa descargó 183 MB de paquetes e incorporó 883 MB de dependencias del sistema antes de instalar Python. Tardó 20,5 minutos con la red disponible; las recompilaciones posteriores reutilizan la caché. Los compiladores permanecen en la imagen ML, por lo que su espacio requerido es mayor que el de la imagen de consulta.

La construcción ML verificada reutilizó la caché del commit público fijado de `rslearn`, después de dos cortes de transporte al descargarlo desde GitHub. Por tanto, la instalación desde una caché totalmente vacía no quedó comprobada en esta red. La imagen resultante sí pasó la prueba sin red con ambos modelos reales y las rutas de datos de Compose; pesa 2,63 GB, frente a 0,72 GB de la imagen de consulta.

Los pesos y las imágenes satelitales permanecen fuera de la imagen Docker, en el volumen `/data`. La CLI y el worker resuelven sus rutas relativas en ese volumen, incluido el prefijo `data/` de las recetas originales. Durante una prueba de inferencia puede montarse en solo lectura. El contenedor usa CPU y el usuario sin privilegios `app`. La compilación y las mediciones de inferencia verificadas se registran en `reports/pilot/docker-validation.json`; su alcance es operatividad, independiente de la precisión científica local.

El smoke reproducible usa la instalación empaquetada, una base temporal y la red deshabilitada. La segunda ejecución requiere los pesos y recortes del piloto en `data/models/` y `data/pilot/`:

```sh
docker run --rm --network none --platform linux/arm64 \
  -v "$PWD/scripts/validate_container.py:/tmp/validate_container.py:ro" \
  satelite-portuario:local python /tmp/validate_container.py --api-only
docker run --rm --network none --platform linux/arm64 \
  -v "$PWD/scripts/validate_container.py:/tmp/validate_container.py:ro" \
  -v "$PWD/data:/data:ro" \
  satelite-portuario:ml python /tmp/validate_container.py
```

## Verificación realizada

La importación del inventario, `init`, `doctor`, `history-report`, exportación GeoJSON/Parquet, bundle y restauración se ejecutaron en un volumen temporal. La restauración recuperó las 863 adquisiciones. La API y la interfaz se probaron con evidencia S2 real copiada a otro volumen temporal; aceptar, rechazar, añadir, mover, historial y recuperación de selección funcionaron. Las imágenes base y ML de Docker fueron comprobadas en ARM64, incluyendo inferencia CPU sin red. La validación local de modelos y el histórico procesado completo siguen siendo requisitos independientes.


## Cola de primera inspección y propuesta V2

La interfaz `/review-queue.html` (también `/review-queue`) utiliza `PUERTOGT_REVIEW_QUEUE`, cuyo valor predeterminado es `<data-dir>/review-queue.json`. Debe apuntar a un manifest creado por `scripts/build_review_queue.py`. El lote y sus previews se verifican por hash; copiar solo el JSON sin los activos correspondientes no permite revisar. Los recibos de primera inspección se conservan en `<data-dir>/review-intake/`. Incluye ambos en una copia complementaria del volumen, junto con `refresh/` y los ledgers de `history/`; no forman parte automáticamente de un bundle de catálogo.

`PUERTOGT_GEOGRAPHY_PROPOSAL_DIR` puede apuntar a `reports/coverage/geography-v2-pilot`. El servidor exige que sus hashes V1/V2 coincidan con `ports.geojson` y `ports-v2.geojson` del directorio de configuración. Sin esa variable el panel informa que no hay propuesta configurada. Nunca sustituyas la configuración V1 del volumen histórico por V2: el [piloto](geography-v2-pilot.md) utiliza una configuración y un volumen separados antes de cualquier procesamiento nuevo.

El [ciclo incremental](refresh.md) se ejecuta explícitamente desde el repositorio. No se ha instalado un temporizador ni un servicio de actualización automática.
