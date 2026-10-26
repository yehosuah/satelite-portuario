# Mejoras priorizadas

26 de octubre de 2026. El objetivo sigue siendo detectar buques con precisión local ≥90% y recall ≥80%, con resultados por puerto y sensor, y mantener un sistema local reproducible. La [entrega operativa](../reports/release-v0.3.0/README.md) ya cerró el histórico; las mejoras siguientes atacan los límites científicos y la operación continua.

## Prioridad y resultado comprobable

| Orden | Mejora | Evidencia del problema | Cómo comprobar que mejora el sistema |
| --- | --- | --- | --- |
| 1 | Obtener más etiquetas utilizables del Caribe, con revisión independiente | El TRAIN comparado tiene 51 referencias: 48 de Quetzal, dos de Barrios y una de Santo Tomás. El refit sigue omitiendo la única referencia STC. La revisión posterior de 32 observaciones produjo una referencia nueva y 30 abstenciones. | Registrar positivos, negativos completos, omisiones, abstenciones y tiempo de revisión por puerto/sensor/pasada. Resolver casos claros y conservar desconocidos; la IA no sustituye la referencia humana o externa. |
| 2 | Corregir y versionar las zonas antes de ampliar procesamiento | La auditoría OSM estima 65,03% de tierra en el fondeo provisional STC y un hueco de 136,87 m entre muelles/canal. | Validar límites y atribución STC/PBR, conservar V1 congelada y probar una V2 independiente sobre TRAIN. Medir agua observable, exclusiones y duplicados. Las opciones de 250/500 m siguen inactivas. |
| 3 | Completar la actualización incremental y mostrar frescura | `update` descubre/registra metadatos; `plan-selected` procesa selecciones explícitas. Todavía son pasos separados. | Una ejecución acotada descubre y procesa solo novedades, reconstruye cobertura y deja recibo. Repetirla no duplica trabajo. Mostrar por puerto/sensor última búsqueda, última captura, última observación útil y pendientes; conservar las seis excepciones conocidas. |
| 4 | Evaluar mejoras del detector con muestras suficientes | Las anclas pequeñas mejoraron el desarrollo interno de 80% a 85%, por un solo caso de Barrios. El 98% posterior es ajuste sobre TRAIN. | Congelar un experimento después de ampliar TRAIN, comparar con la misma base y declarar resultados por puerto/sensor. Una nueva CAL debe ser independiente del diagnóstico expuesto y respetar la separación temporal/contextual. El TEST se evalúa una vez tras fijar receta y umbrales. |
| 5 | Añadir referencias externas donde los píxeles no permiten decidir | Muelles, barcos pequeños, familias y dimensiones siguen siendo ambiguos. | Un piloto de referencias fechadas demuestra correspondencia espacial/temporal, cobertura, procedencia y derechos de uso antes de integrar una fuente. Ausencia de AIS no se transforma en ausencia de buques. |

No se puede pronosticar cuánto aumentará la precisión con estas intervenciones. El protocolo requiere, por puerto y sensor, al menos 30 referencias, 30 predicciones y diez pasadas objetivo en TEST. Son pisos de soporte, no garantía de éxito ni un conteo del conjunto reservado. Hay que reportar incertidumbre y dependencia por pasada si se pretende una afirmación de confianza estadística.

## Primera mejora implementada

El visor permite **Delimitar área**, previsualizar el recorte a la zona y guardarlo con auditoría. Guardar deja la revisión provisional; completar el área requiere una acción posterior. Descartar conserva el ámbito anterior. La API impide guardar una geometría que exceda la zona y rechaza una revisión obsoleta. La exportación mantiene los controles de completitud y omisiones. [Uso y límites](labeling-protocol.md).

Esta mejora permite aprovechar un sector claro de una imagen parcialmente dudosa. No crea etiquetas automáticamente, no certifica calidad de píxeles ni altera límites, modelos o particiones congeladas. La verificación de interfaz utiliza una base sintética aislada; sus anotaciones no entran al catálogo científico.

## Siguiente incremento recomendado

Construir una cola reproducible **solo TRAIN**, con balance por puerto/sensor y diversidad de pasadas, priorizando claridad local para reducir revisiones que terminan en abstención. Debe mostrar las razones de selección y ofrecer una primera inspección sin predicciones ni scores. La selección no puede consultar conteos del modelo ni abrir CAL/TEST. El muestreo de validación necesita su propio diseño representativo: no debe heredar silenciosamente el filtro de escenas claras utilizado para conseguir etiquetas TRAIN.

El primer lote medirá rendimiento de etiquetado y desacuerdos antes de decidir otra ejecución de entrenamiento. El editor de áreas ya está disponible; la cola y la inspección sin propuestas siguen pendientes.

## Alcance de las pasadas y referencias

Las bandas ópticas de mayor resolución de Sentinel-2 tienen píxeles de 10 m. Ampliar el recorte o el zoom no añade detalle físico; para resolver objetos ambiguos puede hacer falta otra referencia. La repetición nominal tampoco garantiza una imagen libre de nubes en el puerto. [Copernicus: Sentinel-2](https://sentiwiki.copernicus.eu/web/s2-mission).

Sentinel-1 aporta observación radar diurna/nocturna independiente de nubosidad, pero la calidad y el detector deben evaluarse por separado. Ya se aprovechan segmentos de una misma pasada; nuevas uniones no crean visitas orbitales adicionales. [Copernicus: Sentinel-1](https://sentiwiki.copernicus.eu/web/s1-mission).

AIS puede aportar posición e identidad como referencia cuando hay correspondencia y cobertura. Sus requisitos de equipamiento no abarcan todas las embarcaciones, de modo que no constituye por sí solo un inventario completo de negativos. No se ha contratado ni conectado una nueva fuente AIS. [IMO: AIS](https://www.imo.org/en/ourwork/safety/pages/ais.aspx).

Fuentes locales: [diagnóstico y experimentos](../reports/local-review/phase3-diagnosis/README.md), [alternativas geográficas](../reports/coverage/geography-v2-candidates/README.md), [cobertura por pasada](coverage.md) y [protocolo de revisión](labeling-protocol.md).
