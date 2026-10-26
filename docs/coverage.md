# Cobertura de las pasadas

El panel **Cobertura de las pasadas** del visor muestra cuánto de las zonas configuradas se observó y cuánto tiempo pasó entre oportunidades útiles. Respeta los filtros de puerto, sensor y fechas. El selector de 50%, 90% o 100% cambia el requisito de área para consultar la cobertura; no modifica imágenes, etiquetas ni la meta de precisión del modelo.

## Área, frecuencia y resolución

Son medidas diferentes. Sentinel-2 captura una franja de 290 km, con bandas de 10, 20 y 60 m y una configuración nominal de revisita de cinco días en el ecuador. Procesar un recorte más grande aprovecha más área de esa captura, pero no produce una visita adicional ni mejora su resolución. La nubosidad local reduce la frecuencia aprovechable. [Especificación de Copernicus](https://sentiwiki.copernicus.eu/web/s2-mission).

El radar aporta observaciones sin depender de luz solar o nubosidad. Su calendario real depende de la órbita y del escenario de adquisición; los tiempos nominales no sustituyen las fechas del catálogo local. [Misión Sentinel-1](https://sentiwiki.copernicus.eu/web/s1-mission).

El inventario del proyecto ya incluye S1A/C/D, S2A/B/C y las direcciones radar disponibles, sin descartar escenas por su porcentaje global de nubes. Para el detector se preservan sus productos numéricos L1C y GRDH, sin intercambiarlos por otro producto o una imagen RGB.

## Partes de una misma pasada

Algunas capturas radar del Caribe llegan divididas en dos productos contiguos. Contar cada producto como revisita exageraría la frecuencia. Usar solo la mejor fracción de cada producto también puede subestimar el área cubierta.

El sistema agrupa productos de una misma pasada y une sus máscaras observables con una operación binaria sobre la cuadrícula original. Verifica hashes, tamaño, CRS, resolución, alineación y receta antes de calcular. No interpola píxeles y no suma fracciones solapadas. Las observaciones originales y sus revisiones permanecen intactas.

En el corte previo a los suplementos del 25 de octubre, las uniones recuperaron 32 pasadas suficientes de Puerto Barrios y 34 de Santo Tomás, usando datos ya descargados. Esas mejoras se solapan entre puertos y no representan 66 nuevas órbitas. El [informe del corte](../reports/coverage/README.md) conserva cifras, método y limitaciones; el visor consulta el estado actual.

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_pass_coverage.py \
  --rebuild-masks --output data/coverage/latest.json
```

El comando permite generar explícitamente el archivo de uniones y el informe. La API usa el resultado solo mientras sus fuentes registradas coincidan. Si falta o queda obsoleto, conserva una estimación con las fracciones individuales. Las observaciones rechazadas o no observables no elevan la cobertura mediante un resultado guardado anteriormente.

## Ampliar el área observada

Las [dos propuestas geográficas](../reports/coverage/geography-v2-candidates/README.md) añaden agua próxima a los muelles, usando costa OSM independiente de las detecciones. La opción de 250 m añade aproximadamente 1,05 km² entre los tres puertos; la de 500 m, 2,41 km². Ambas caben en las doce teselas conjuntas previstas por la cuadrícula actual. Eso permite estudiar reutilización de datos existentes, sin garantizar que cada archivo retenido cubra el área con calidad suficiente.

Las candidatas están inactivas. Su mapa documenta también un problema en la configuración actual: aproximadamente 65% del fondeo de Santo Tomás cae en el lado terrestre de la costa OSM. Corregir ese fondeo y adoptar una ampliación requiere una geometría nueva, revisada y versionada; los límites y datasets anteriores conservan su identidad. Estos polígonos analíticos no son límites oficiales de navegación.

## Límites de interpretación

- Una oportunidad útil no prueba presencia ni ausencia de barcos. La precisión del detector se mide por separado.
- Radar y óptico se reúnen para describir oportunidades, manteniendo cada fecha. No constituyen una fotografía simultánea.
- El mayor intervalo entre observaciones no incluye los huecos en los extremos de la ventana; la API los informa aparte.
- Las zonas actuales son provisionales. El [análisis de Santo Tomás](../reports/local-review/phase2/stc-quay-coverage-audit.md) documenta agua próxima fuera de sus límites. Una ampliación requiere nueva geometría versionada, nuevas observaciones y una evaluación que identifique ese alcance; las referencias congeladas conservan el anterior.
- Las seis excepciones de radar carecen del contexto compatible que requiere el modelo. No se rellenan con ceros ni se cuentan como observación sin barcos.
