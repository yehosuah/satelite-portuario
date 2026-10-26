# Auditoría de particiones por metadatos

La acción siguiente es adjudicar las seis áreas S2 de la cola nueva y medir el refit e099 sobre ellas como desarrollo TRAIN. **CAL permanece deshabilitada hasta declarar ese gate; TEST sigue cerrado.** Esta auditoría no entrenó, no infirió, no cambió splits y no abrió etiquetas, imágenes, propuestas ni métricas del TEST reservado.

El [JSON](partition-audit.json) conserva hashes de fuentes, IDs, exclusiones por pasada, consultas proyectadas y el marco de reservas para una futura enmienda. La base SQLite se abrió en modo de lectura; se hashó la proyección de metadatos, sin hashear ni exportar la base entera.

## Conjunto de desarrollo para e099

El plan del refit fija `s2-january-february-v1/source`; su hash coincide. Sus siete adquisiciones por tile representan cuatro pasadas físicas (3 y 18 de enero; 2 y 17 de febrero), sin contexto S1. Ninguna de las siguientes seis entradas comparte una pasada objetivo o de contexto con ese TRAIN. Son cuatro pasadas nuevas; PBR y STC comparten las del 10 de enero y 18 de mayo.

| Puerto | Fecha UTC | Zona | Observation ID | Clase |
|---|---|---|---|---|
| prq | 2025-03-04 | prq_muelles | `c2aaad3d-b82c-4b50-bdd8-91715124ce5c` | Pasada TRAIN no usada por e099 |
| prq | 2025-04-03 | prq_fondeo | `09b37fd3-c189-4bae-aa85-06da102c023b` | Pasada TRAIN no usada por e099 |
| pbr | 2025-01-10 | pbr_canal | `4796c120-bd2d-42c8-9bbf-7bcfe8f467a0` | Pasada TRAIN no usada por e099 |
| pbr | 2025-05-18 | pbr_canal | `3b7ff03b-5224-410b-86f7-18829eeb9a95` | Pasada TRAIN no usada por e099 |
| stc | 2025-01-10 | stc_fondeo | `d9f3dfe4-892b-49cc-80a6-58fe99fda67a` | Pasada TRAIN no usada por e099 |
| stc | 2025-05-18 | stc_muelles | `05208f24-0534-4107-9bf1-700c46c84632` | Pasada TRAIN no usada por e099 |

Las otras seis entradas de la cola son S1 y no sirven para evaluar un refit S2. Este probe mide desarrollo sobre fechas no entrenadas; la selección por claridad y las referencias IA impiden llamarlo validación final independiente.

## Soporte existente y procedencia

| Fuente | Objetos únicos PRQ / PBR / STC | Áreas fuente | Parches |
|---|---:|---:|---:|
| S1 enero–marzo | 30 / 0 / 0 | 38 | 82 |
| S2 enero–febrero, usado por e099 | 48 / 2 / 1 | 32 | 84 |
| Suplemento S2 marzo, separado | 0 / 0 / 1 | 2 | No derivado en este recibo |

Los parches y las instancias por zona no son objetos independientes. Las versiones anteriores son subconjuntos; no se suman. Las fuentes auditadas tienen **cero referencias humanas y cero referencias AIS**. El usuario confirmó que no tiene acceso GFW/AIS. La revisión independiente de áreas ambiguas del Caribe necesita una persona o evidencia externa fechada con correspondencia verificable; una nueva cifra de IA no resuelve esa carencia. S1 no tiene positivos PBR/STC adjudicados y su CAL debe permanecer cerrada.

## CAL S2 disponible sin activarla

La exposición se cruza globalmente por pasada entre puertos usando existencia de revisión en catálogo, estado de revisión/completitud, manifests históricos de CAL y reservas ya activadas. Son 21 pasadas expuestas o activadas. El marco conservador excluye también todas las primarias anteriores, aunque no tengan recibo, y los días UTC completos de cualquier pasada excluida. Esto no declara expuestas las primarias sin evidencia; las retira preventivamente.

| Puerto | Reservas sin exposición registrada por pasada | Marco conservador: adquisiciones / días | Pasadas con alguna área marina observable / días |
|---|---:|---:|---:|
| prq | 10 | 9 / 9 | 9 / 9 |
| pbr | 26 | 22 / 19 | 7 / 6 |
| stc | 26 | 22 / 19 | 10 / 9 |

Las listas exactas están en `cal_s2_availability.future_amendment_candidate_acquisitions`. Todas conservan su pertenencia original a CAL y orden cronológico de reserva. El marco incluye escenas no observables; las fracciones se informan para dimensionar el trabajo y no filtran la selección de validación. No hay colisión de claves/pasadas con objetivos o contextos TRAIN/TEST del marco de muestreo.

El soporte de 30 referencias y 30 predicciones por puerto sigue desconocido. El límite de diez pasadas del protocolo corresponde a TEST; no se usa para inventar un déficit de CAL. Las dos pasadas del 21 de noviembre están separadas por minutos y deben tratarse como potencialmente correlacionadas. La independencia de eventos no se acredita solo con claves distintas.

El protocolo actual activa reservas por problemas de acceso, bandas/contexto, máscara o ausencia de área revisable, nunca por rendimiento adverso. Un ciclo nuevo sobre estas fechas necesita una enmienda preregistrada, congelada antes de revisar etiquetas o inferir, que deje intacto el fallo anterior. Este informe no activa esa enmienda.

## Ventanas y TRAIN adicional

Con TRAIN enero–junio y TEST abril–agosto congelados, la ventana de CAL que respeta los embargos de 92 y 91 días sigue siendo octubre–diciembre de 2025. Julio–septiembre de 2025 y enero–marzo de 2026 no son ventanas adicionales válidas con esos extremos. S1 exige además contexto compatible 30–90 días antes y grupos compartidos dentro de una partición.

Hay 52 candidatos de zona sobre pasadas TRAIN sin primera revisión conocida, con fracción observable ≥0,5 y activos crudos registrados, dentro de la selección activada. En el Caribe, las nuevas fechas más directas son S2 2 y 17 de junio y S1 2 de junio. Los píxeles y bytes de esos activos no se inspeccionaron en esta auditoría.

El selector puede volver a proponer cuatro áreas S1 del 3 de abril ya inspeccionadas en informes RAW aún no importados. Sus IDs y fuentes están en el JSON; deben excluirse de cualquier afirmación de primera inspección. El segmento adicional del 18 de junio pertenece a la misma órbita que el primario, no aporta otra visita y requiere una decisión explícita sobre fuentes.

La ampliación de TRAIN y el probe e099 pueden ejecutarse ahora dentro de su alcance de desarrollo. La independencia de referencia humana o externa y el soporte de una nueva CAL siguen pendientes. No se hicieron commits.
