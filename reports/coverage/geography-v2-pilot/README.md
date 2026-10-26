# Piloto geográfico V2 sobre TRAIN

26 de octubre de 2026. **La V2 está inactiva.** La configuración separada conserva doce zonas y corrige el fondeo de Santo Tomás, incorpora el candidato de 250 m alrededor de cada muelle y cierra el hueco de 136,87 m entre muelles y canal STC. El piloto mide máscaras ya conservadas; no ejecuta inferencia, entrenamiento, descargas ni escrituras en la base de datos.

La [comparación geográfica](comparison.png) muestra V1, V2, costa, ampliaciones y exclusiones. La [imagen del piloto](pilot.png) muestra la primera captura óptica TRAIN de cada puerto, elegida por fecha antes de conocer su observabilidad. Ninguna figura usa posiciones de buques.

## Qué cambia y cuánto

| Puerto | Agua OSM dentro de V1 (km²) | Agua OSM dentro de V2 (km²) | Agua añadida junto a muelles (km², aprox.) | Aumento neto de agua (km²) |
| --- | ---: | ---: | ---: | ---: |
| Quetzal | 15,333443 | 15,581424 | 0,247981 | 0,247981 |
| Santo Tomás | 5,464117 | 5,718135 | 0,422401 | 0,254018 |
| Barrios | 3,099875 | 3,477918 | 0,378042 | 0,378042 |
| Total, sin sumar solapes | 23,897436 | 24,777477 | 1,048424 | 0,880041 |

El fondeo STC pasa de **8,538066 a 2,817302 km²**. V1 contenía 5,552381 km² de tierra según la costa OSM fijada, el 65,03% de su superficie. Además de esa tierra se excluye una franja analítica de 20 m junto a la costa, que retira aproximadamente **0,168383 km² de agua**. Por eso 1,048 km² de ampliación no equivale al aumento neto de 0,880 km². El margen de 20 m sigue el criterio conservador del candidato; no es una distancia de seguridad ni una incertidumbre geodésica medida.

Las tres ampliaciones se asignan a `*_muelles`. Se excluyen todas las zonas V1 antes de asignarlas, y cualquier conflicto nuevo se resolvería por orden lexicográfico de `port_id`. STC y PBR tienen **cero solapamiento de área** en la V2 generada. Los solapes que V1 ya tenía entre zonas de un mismo puerto siguen documentados: las métricas agregadas usan la unión espacial, nunca la suma de zonas. Patios y los demás límites mantienen su forma; los muelles siguen incluyendo partes de las envolventes mixtas de V1.

El generador normaliza únicamente las cuatro geometrías modificadas a una cuadrícula métrica de 1 mm para eliminar microintersecciones numéricas al transformar entre UTM y WGS84. Comprueba validez en ambos sistemas y limita la variación a la tolerancia del redondeo. Esta operación cambia unos pocos metros cuadrados de borde y **no expresa exactitud de la costa**. El [recibo geométrico](geometry-audit.json) conserva el área de esa normalización y las áreas completas por zona; la tabla redondea las ampliaciones a sus candidatos originales.

## Evidencia numérica consumida

Se leyeron dos manifiestos congelados que declaran TRAIN: `s2-january-february-v1/source` y `s1-january-march-v1/source`. Aportan **15 adquisiciones, 12 pasadas físicas, 87 teselas y 174 máscaras** `observable`/`valid`, con 452.530 bytes verificados por SHA256. No se abrieron archivos de etiquetas, predicciones, pesos ni evidencia CAL/TEST. Las fechas objetivo van del 1 de enero al 22 de marzo de 2025; los hashes de los manifiestos y de cada máscara están en [pilot.json](pilot.json).

Se usan los centros de píxel de 10 m de los núcleos originales de 448 × 448, sin remuestreo. Las teselas de 512 × 512 conservan su halo, que no se suma al área. Partes compatibles de una misma pasada se unen con OR binario para contar cada píxel una vez. La geometría de agua procede de OSM; la máscara observable procede del procesamiento previo. La validez numérica de esas máscaras se comprobó, pero su calidad científica no se valida de nuevo aquí.

| Puerto | Sensor | Pasadas TRAIN del puerto | Pasadas con ≥90% del agua añadida observable | Agua añadida observable por pasada: mediana [mín., máx.] (km²) |
| --- | --- | ---: | ---: | ---: |
| Quetzal | Radar | 2 | 2 | 0,2482 [0,2482; 0,2482] |
| Quetzal | Óptico | 4 | 2 | 0,1278 [0,0005; 0,2482] |
| Santo Tomás | Radar | 6 | 3 | 0,2112 [0; 0,4223] |
| Santo Tomás | Óptico | 3 | 1 | 0,0151 [0; 0,4223] |
| Barrios | Radar | 6 | 3 | 0,1889 [0; 0,3778] |
| Barrios | Óptico | 3 | 2 | 0,3748 [0,0124; 0,3778] |

Los conteos de la tabla se solapan entre puertos; no son 24 visitas diferentes. Los valores rasterizados difieren ligeramente de las áreas geométricas porque usan centros de píxel de 100 m². Un área observable cero no implica ausencia de buques. El umbral del 90% es área, no precisión del detector.

**Toda el agua añadida está dentro del encuadre retenido en estas pasadas.** En cambio, 0,7948 km² del fondeo STC V2 quedan fuera de los núcleos disponibles en tres pasadas radar: 1 de enero (`057250`), 18 de febrero (`057950`) y 2 de marzo (`058125`). El informe separa esos píxeles fuera de encuadre de los píxeles inválidos dentro de él, y de los válidos que la máscara considera no observables. El conjunto V2 utiliza las mismas doce teselas previstas por la cuadrícula V1; eso no garantiza calidad suficiente ni disponibilidad para todas las fechas.

La selección procede de desarrollo TRAIN revisado y no es una muestra representativa del calendario. Estos resultados prueban que el área puede medirse con los datos conservados y revelan sus límites; no validan detección, navegación ni frecuencia orbital general.

## Archivos y consulta separada

- [Configuración V2](../../../config/ports-v2.geojson), con versión `2026-10-26-geography-v2-train-pilot`, procedencia, estado inactivo y doce identidades estables.
- [summary.json](summary.json), resumen pequeño para el panel; fija hashes de geometrías, piloto e imágenes.
- [geometry-audit.json](geometry-audit.json), métricas por zona, solapes, cierre del hueco y teselas.
- [pilot.json](pilot.json), cada pasada y zona, encuadre/validez/observabilidad y hashes de las máscaras.
- [train-selection.json](train-selection.json), selección exacta de adquisiciones TRAIN para un posible plan futuro; no se ha ejecutado ese plan.
- [comparison.png](comparison.png) y [pilot.png](pilot.png), figuras numéricas sin detecciones.

El panel debe presentar este recibo como **propuesta V2, no activa**. Cambiar el `ports_path` del servicio actual por V2 reinterpretaría observaciones V1, por lo que no es una vía de activación válida. [Integración y condiciones del siguiente piloto](../../../docs/geography-v2-pilot.md).

## Reproducción

Requiere el entorno del proyecto, `matplotlib` para las figuras y los dos datasets TRAIN locales. Las funciones geométricas y sus nueve pruebas funcionan con las dependencias base, sin `matplotlib`; este se carga únicamente al dibujar, con el backend `Agg`, sin interfaz gráfica. Desde la raíz, este ejemplo escribe en un directorio nuevo y conserva el recibo publicado:

```sh
PYTHONPATH=src .venv/bin/python scripts/build_geography_v2.py build \
  --v2 /tmp/ports-v2-repro.geojson --output /tmp/geography-v2-repro
PYTHONPATH=src .venv/bin/python scripts/build_geography_v2.py pilot \
  --v2 /tmp/ports-v2-repro.geojson --output /tmp/geography-v2-repro \
  --train-root data/datasets/phase2-reviews/s2-january-february-v1/source \
  --train-root data/datasets/phase2-reviews/s1-january-march-v1/source
```

El generador rechaza V1, candidatos o recibos OSM con hashes distintos de los fijados; el piloto rechaza manifiestos ajenos a TRAIN, máscaras alteradas, cuadrículas incompatibles o una V2 distinta de la construcción reproducible. Nueve pruebas focalizadas cubren esas protecciones, topología, atribución, unión de núcleos, casos fuera de encuadre y reproducibilidad entre procesos.

## Procedencia y límites geográficos

Los [datos OSM fijados](../geography-v2-candidates/sources/) contienen 32 vías de costa obtenidas el 26 de octubre de 2026 UTC. La [convención dirigida de OSM](https://wiki.openstreetmap.org/wiki/Tag:natural%3Dcoastline) permite separar tierra y agua. OSM es una referencia cartográfica independiente de las detecciones, no una autoridad portuaria. No confirma jurisdicción STC/PBR, batimetría ni límites de navegación, y su costa de 2026 no reconstruye necesariamente cada fecha de 2025. Las atribuciones operativas anteriores permanecen provisionales.

Geometrías derivadas: © OpenStreetMap contributors, ODbL-1.0. Máscaras: Contains modified Copernicus Sentinel data 2025. Estas atribuciones son distintas de la licencia del código propio. Los informes anteriores, la configuración V1, las observaciones y los datasets congelados permanecen intactos.
