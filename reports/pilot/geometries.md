# Geometrías para el piloto

Versión `2026-10-20-provisional-v1`, preparada el 20 de octubre de 2026 en Guatemala
(consultas realizadas el 21 de octubre en UTC). Se incluyen 12 zonas en
[`config/ports.geojson`](../../config/ports.geojson), cuatro por puerto. Todas conservan
`geometry_status=provisional`, `review_required=true` y `not_for_navigation=true`.

Son áreas de observación para buscar escenas y preparar recortes. La geometría pasa
validación topológica, pero falta revisión local de límites, agua, tierra y alcance de
los patios sobre píxeles georreferenciados. Los rectángulos que rodean un muelle incluyen
agua e infraestructura; el área observable requiere su propia máscara.

| Puerto/zona | Evidencia y construcción | Límite de uso |
|---|---|---|
| Quetzal/muelles | Envolvente de la dársena comercial y terminal APM, apoyada en la cartografía OSM y localización del operador. | Delimitación aproximada; contiene píxeles terrestres. |
| Quetzal/fondeo | Cuatro vértices WGS84 del polígono principal del plano de ayudas a la navegación publicado por EPQ, recuperados del índice de búsqueda. | La URL del PDF redirige hoy al inicio; la vigencia del plano y la transcripción requieren cotejo. |
| Quetzal/canal | Corredor analítico de aproximación a la dársena. | Los vértices aproximados no representan un canal navegable autorizado. |
| Quetzal/patios | Polígono industrial OSM de APM Terminals, `way/704380412`. | Es una primera zona de infraestructura; faltan los demás patios del puerto. |
| Santo Tomás/muelles | Franja junto al borde noroeste del polígono OSM de la portuaria. | Buffer aproximado de observación, pendiente de máscara de costa. |
| Santo Tomás/fondeo | Área de agua abierta al oeste de la aproximación. | El operador confirma el servicio; las coordenadas de este AOI son una elección analítica provisional. |
| Santo Tomás/canal | Corredor aproximado al nor-noreste, orientado con la información del operador. | Ancho de observación mayor que el canal; falta carta vigente. |
| Santo Tomás/patios | Polígono industrial OSM de Portuaria Santo Tomás, `way/715624206`. | Contiene infraestructura general, sin separar cada patio. |
| Puerto Barrios/muelles | Envolvente del muelle comercial delineado por `way/638933275` de OSM. | Excluye el muelle municipal al sur; límites de agua por revisar. |
| Puerto Barrios/fondeo | Área de agua abierta al norte de la terminal. | Las coordenadas no están confirmadas como límites oficiales de fondeo. |
| Puerto Barrios/canal | Corredor analítico desde el muelle hacia el norte. | Pendiente de carta vigente y separación agua/tierra. |
| Puerto Barrios/patios | Envolvente de infraestructura apoyada en los polígonos Parqueo Cobigua de OSM. | Primera cobertura, sin afirmar límites catastrales ni totalidad de patios. |

## Evidencia cartográfica

La consulta directa de la interfaz de OpenStreetMap devolvió las coordenadas de estos
elementos. Se conservaron sus identificadores en cada zona para poder revisar cambios:

- [APM Terminals Quetzal, way 704380412](https://www.openstreetmap.org/way/704380412).
- [Portuaria Santo Tomás, way 715624206](https://www.openstreetmap.org/way/715624206).
- [Costa y muelle comercial de Puerto Barrios, way 638933275](https://www.openstreetmap.org/way/638933275).
- [Parqueo Cobigua, way 132451928](https://www.openstreetmap.org/way/132451928) y
  [way 132451927](https://www.openstreetmap.org/way/132451927).

Se pueden repetir las lecturas con `/api/0.6/way/<id>/full` en
`https://api.openstreetmap.org`. Los datos derivados de OSM llevan atribución
© OpenStreetMap contributors y [licencia ODbL](https://www.openstreetmap.org/copyright).
Esta atribución es independiente de la licencia del código.

Se consultaron además el [sitio de Terminal Ferroviaria Puerto Barrios](https://puertobarrios.gt/),
la [descripción de instalaciones de Santo Tomás](https://santotomasport.com.gt/datospuerto/)
y el [plano de ayudas a la navegación de Quetzal](https://www.puertoquetzal.gob.gt/wp-content/themes/total-child-theme/files/_1531162445_1471_PLANO2.pdf).
Santo Tomás describe un canal de 10.7 km y rumbo de señalización de 4°53′; esto orienta
el corredor provisional sin acreditar sus vértices. El plano de Quetzal aportó la
tabla de esquinas indexada, pero su URL redirigió a la portada durante la verificación.
Por eso ni siquiera el polígono transcrito se presenta como validado.

## Atribución entre puertos

Los polígonos de Santo Tomás y Puerto Barrios tienen intersección de área cero. Las
zonas abiertas que queden fuera de ambos se mantienen sin atribución. La función
`attribute_point` asigna cada punto observado a un puerto como máximo. Si un límite
futuro fuese compartido, el empate se resuelve de forma estable por `port_id`.

Una adquisición que cubra ambos puertos conserva una sola clave y los dos `port_ids`.
La observación sigue siendo por adquisición, zona y receta. Las zonas dentro de un
puerto pueden solaparse, por lo que los conteos de zonas requieren deduplicación espacial
antes de producir un total del puerto.

El punto PortWatch usado en la investigación previa para Puerto Barrios está entre
las dos instalaciones y su registro no separaba Santo Tomás. No se usó ese punto como
límite ni para asignar detecciones. La nueva zona de muelles de Barrios se apoya en el
muelle comercial situado aproximadamente en 15.7348 N, 88.608 W.

Las geometrías de intercambio usan longitud/latitud WGS84. Para calcular distancias o
superficies se debe proyectar Quetzal a UTM 15N (`EPSG:32615`) y los puertos atlánticos
a UTM 16N (`EPSG:32616`). No se interpreta el área en grados cuadrados como metros.

## Reproducción y verificación

```sh
uv run --no-sync pytest tests/test_catalog.py -q
PYTHONPATH=src uv run --no-sync python scripts/inventory.py
```

La prueba de deduplicación falló primero con tres nombres tomados como tres
adquisiciones: `Expected 2 sensing events, got 3 product representations`. Tras
normalizar la identidad, produce dos adquisiciones y conserva los dos activos de la
toma que tiene SAFE y COG. La suite también comprueba dos páginas completas, rechazo
de un catálogo truncado, separación de órbitas/plataformas/tomas, unión de huellas
parciales ópticas y atribución sin duplicación entre los puertos atlánticos.

El siguiente control cartográfico es revisar las máscaras locales sobre los recortes
del piloto, completar los patios y sustituir las aproximaciones de fondeo/canal con
límites verificables. Una nueva versión de geometrías debe generar observaciones con
esa versión explícita; no debe reinterpretar silenciosamente resultados existentes.
