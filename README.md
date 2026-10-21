# Satélite Portuario

Observatorio local de Puerto Quetzal, Santo Tomás de Castilla y Puerto Barrios. Inventaría adquisiciones Sentinel-1 y Sentinel-2, procesa recortes numéricos, conserva evidencia y permite revisar propuestas de buques y cambios físicos.

**Versión experimental.** El servicio funciona con pilotos reales de ambos sensores en los tres puertos. Todavía no hay precisión local de detección ≥90% demostrada, atributos locales entrenados ni un histórico completo procesado. El detector base omitió barcos visibles junto a muelles: un conteo cero no confirma ausencia. [Estado y evidencia](docs/status.md).

## Paquete experimental

El código y el [piloto portable v0.1.0](https://github.com/yehosuah/satelite-portuario/releases/tag/v0.1.0) están publicados. El paquete contiene imágenes, catálogo y cuatro modelos del piloto inicial: sus 270 archivos fueron verificados y restaurados en otra carpeta. Se verificaron los 39 archivos de la versión pública, incluidas las 34 partes del snapshot. Consulta la [verificación de publicación](reports/pilot/release-upload-status.json) y las [instrucciones de restauración](docs/restore-pilot.md). El histórico sigue en procesamiento.

## Arranque local

```sh
git clone https://github.com/yehosuah/satelite-portuario.git
cd satelite-portuario
uv sync --frozen --no-editable
uv run --no-sync puertogt init
uv run --no-sync puertogt import-scenes reports/pilot/inventory.json
uv run --no-sync puertogt serve
```

Abre [localhost:8000](http://127.0.0.1:8000/). La instalación base sirve el catálogo, la API y el visor; las imágenes y los pesos se obtienen con los comandos explícitos del [piloto](reports/pilot/inference.md) o mediante un bundle exportado. La importación del inventario solo incorpora metadatos.

Para procesamiento y entrenamiento:

```sh
uv sync --frozen --no-editable --extra ml --extra quality
uv run --no-sync puertogt --help
```

La revisión escrita requiere `PUERTOGT_REVIEW_TOKEN` en el entorno del servidor. [Instalación, Docker, credencial local y restauración](docs/installation.md).

## Qué incluye

- Catálogo paginado y deduplicado, con captura/publicación/descubrimiento separados; doce zonas provisionales con fuentes registradas.
- Cola persistente de un trabajador pesado, leases, reintentos, pausas por recursos y evidencia publicada de forma atómica.
- Detectores públicos fijados por commit y SHA256, máscaras de nubes/sombras, recortes radar con contexto histórico y entrenamiento supervisado que genera pesos propios versionados.
- Revisión visual auditada, corrección de cajas, buques omitidos y datasets separados por adquisición y grupos relacionados.
- Cabezas de atributos por sensor y decoder siamés de cambios; las recetas sin etiquetas suficientes permanecen sin validar. Tipos y dimensiones pueden ser nulos.
- API localhost, filtros UTC, paginación, cobertura sin interpolar huecos, GeoJSON/Parquet y snapshots verificables para otro equipo.

El [protocolo de etiquetas](docs/labeling-protocol.md) y las [tarjetas de modelos](docs/model_cards/vessels.md) explican qué referencias hacen falta. El score del modelo nunca se interpreta como precisión medida. La API no inicia inferencia, descargas ni entrenamiento.

## Evidencia incluida

El inventario al **21 de octubre de 2026, 01:40:47 UTC** reúne 1,267 productos en 81 páginas, agrupados en 863 adquisiciones: **828 desde enero de 2025 y 35 de contexto anterior**. Los puertos atlánticos comparten algunas adquisiciones; sus conteos no deben sumarse para obtener el total global.

Hay pilotos numéricos reales en las seis combinaciones puerto/sensor, pruebas de entrenamiento con muestras públicas y 22 observaciones de zonas procesadas por el servicio. Son pruebas de operación; no constituyen una evaluación independiente de los puertos. [Mediciones](reports/pilot/inference-results.json), [servicio óptico](reports/pilot/service-validation.json), [servicio radar](reports/pilot/radar-service-validation.json).

Las 22 observaciones ya tienen revisión visual de IA auditada, con 19 áreas etiquetadas y cinco cajas de zona que representan cuatro observaciones de buques. Se ejecutó un primer ajuste por sensor y prueba separada: la muestra radar es insuficiente y óptico falla el criterio de IoU. [Etiquetas, protocolo y resultados](reports/local-review/README.md). El procesamiento de las 824 adquisiciones restantes dispone de una campaña reanudable; [alcance, recursos y excepciones](reports/history/processing-plan.md).

## Verificación y documentación

```sh
uv run --no-sync pytest -q
uv run --no-sync ruff check src tests scripts
uv build
uv run --no-sync puertogt doctor
```

`doctor --check-release` devuelve código 2 mientras falte evidencia científica. Las pruebas de software que pasan no cambian ese estado.

- [API](docs/api.md), [operación](docs/installation.md) y [entrenamiento/evaluación](docs/learning-workflows.md).
- [Plan original](docs/superpowers/plans/2026-10-20-observatorio-satelital.md) y [registro de implementación](docs/implementation-log.md).
- [Avisos de terceros y atribuciones](THIRD_PARTY_NOTICES.md). La licencia del código propio queda pendiente de decisión del titular.

Los datos de trabajo, credenciales, entornos y pesos quedan fuera de Git. Los manifiestos conservan la procedencia de datos Copernicus, modelos Ai2 y geometrías basadas en © OpenStreetMap contributors.
