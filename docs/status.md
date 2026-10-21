# Estado de la entrega

Esta versión entrega el servicio local y una base reproducible para desarrollar los modelos del [plan aprobado](superpowers/plans/2026-10-20-observatorio-satelital.md). La entrega científica completa sigue pendiente. La publicación del repositorio no declara cumplida la meta de precisión ni transforma las propuestas en un histórico validado.

El repositorio y el [piloto portable v0.1.0](https://github.com/yehosuah/satelite-portuario/releases/tag/v0.1.0) son públicos. La transferencia quedó completada: 34 partes del snapshot y cinco archivos de software y checksums, verificados por tamaño y SHA256. La descarga anónima de ambos manifiestos de hashes también se comprobó. El snapshot conserva el piloto inicial; las revisiones posteriores y la campaña histórica se documentan por separado. [Verificación de publicación](../reports/pilot/release-upload-status.json).

| Parte del plan | Implementado y comprobado | Evidencia pendiente |
|---|---|---|
| T1: inventario y geometrías | 81 páginas, 1,267 productos, 863 adquisiciones únicas; 828 del período y 35 de contexto. Doce zonas con procedencia. | Confirmar límites operativos de las zonas provisionales. |
| T2: pilotos y recetas | Inferencia real S1/S2 en los tres puertos, nubes/sombras ópticas, contexto radar compatible, pasos supervisados públicos, pesos/hashes fijados, CPU y MPS. | Calidad representativa por tamaño, muelle/fondeo, nubosidad y geometría radar. |
| T3: persistencia | SQLite, cola idempotente por adquisición/zona/modelos, leases, reintentos, pausas y archivos atómicos. | Operación prolongada en el equipo de destino. |
| T4: revisión | 22 observaciones inspeccionadas en el visor y contrastadas por IA; 27 revisiones auditadas, 19 áreas congeladas, procedencia explícita y exclusiones de incertidumbre. | Revisión humana independiente y muestra temporal más amplia con calibración separada. |
| T5: buques y atributos | Primer ajuste local real por sensor y evaluación sobre adquisiciones separadas; pesos nuevos experimentales y métricas conservadas. Cabezas de atributos, caché, evaluación y abstención implementadas. | Entrenamiento local con positivos suficientes; precisión ≥90% no demostrada. Faltan referencias y evaluación de dimensiones y familias. |
| T6: cambios | Encoder Satlas real verificado, decoder siamés, entrenamiento con pares revisados, comparación en zona estática, evaluación de pares test congelados y estados t0/t1/t2/reversión auditados. | Pares locales positivos/negativos, pesos propios del decoder y evaluación independiente. No se han publicado eventos físicos reales. |
| T7: servicio | API localhost y visor; fechas UTC, filtros, paginación, activos, cobertura, modelos, trabajos y revisiones protegidas. | Ningún servicio remoto de producción desplegado. |
| T8: histórico y continuidad | Campaña reanudable de 824 adquisiciones programada con 4.907 teselas de zonas completas, presupuestos persistentes, reintentos y preservación del piloto. Prototipos de ambos sensores publicados. | Campaña aún en ejecución. Seis S1 carecen de contexto compatible confirmado en CDSE/ASF; se registran como excepciones. También faltan las áreas no cubiertas de las cuatro capturas piloto. |
| T9: portabilidad | Wheel/instalación aislada, Docker y exportación/restauración. El snapshot real verifica 270 archivos y reproduce inferencia de ambos detectores desde el volumen restaurado. | Validación científica y carga histórica completas antes de declarar una entrega operativa validada. |

## Hallazgos del piloto

Los seis recortes locales produjeron siete propuestas antes de repartirlas por zonas. La inspección mostró barcos junto a muelles que el detector base omitió y propuestas ambiguas en agua. Ni esas siete propuestas ni los ceros de otras escenas son conteos confirmados. Los estados `usable` y `partial` describen observabilidad del área, no exactitud del detector.

El snapshot conserva los resultados de la ejecución inicial: 10 zonas utilizables y 12 parciales. La revisión de CI detectó después un error al estimar cobertura con el número de píxeles del borde. El worker corregido usa la intersección geométrica. El [recálculo de las 22 observaciones](../reports/pilot/coverage-correction.json), sin modificar datos ni repetir inferencia, cambiaría cuatro estados de parcial a utilizable y como máximo 0.502 puntos porcentuales de observabilidad. Las detecciones no cambian. El snapshot conserva esa evidencia histórica y requiere reprocesamiento para adoptar el cálculo nuevo.

Los entrenamientos públicos fueron pruebas de una sola muestra: un positivo óptico y un negativo radar con cobertura de anotación verificada. Los pesos propios permiten comprobar el ciclo de entrenamiento, guardado y restauración. No constituyen entrenamiento ni evaluación local suficientes.

La [ronda local posterior](../reports/local-review/README.md) ya revisó las 22 observaciones y entrenó ambos sensores sobre negativos locales. S1 acertó los dos objetos de su pequeña prueba, con intervalo del 95 % de precisión de 34,2–100 %. S2 no alcanzó IoU 0,5: las cajas fueron demasiado amplias. Las métricas base y ajustado no cambiaron. No se eligió un ganador ni se modificó el criterio después de la prueba.

La [fase 2](../reports/local-review/phase2/stable-checkpoint-20261021.md) conserva 51 objetos ópticos de entrenamiento y 60 de calibración en datasets separados. La ronda de enero ya entrenó y falló la calibración; el test sigue reservado. El conjunto ampliado no se ha entrenado. La corrección del entrenador mantiene excluidos los lugares sin revisar. Los positivos radar del Caribe, la revisión humana independiente y el soporte suficiente de calibración siguen pendientes.

La proyección inicial del [piloto](../reports/pilot/inference.md) correspondía a recortes parciales. La campaña de zonas completas tiene [mediciones propias y límites](../reports/history/processing-plan.md); su tiempo total sigue siendo una proyección hasta terminar. El inventario y las propuestas procesadas no se presentan como un histórico validado.

## Para cerrar la entrega científica

El siguiente trabajo necesario es ampliar los positivos revisados de los tres puertos y ambas fuentes, mantener un test temporal reservado y aportar referencias independientes para familias y dimensiones. El [protocolo](labeling-protocol.md) incluye negativos y omisiones. La falta de una referencia conserva campos nulos.

La campaña histórica continúa con propuestas explícitamente experimentales mientras se completa esa validación. Se conciliará su cobertura y cada excepción al terminar. `puertogt doctor --check-release` mantiene este estado pendiente de forma explícita.

## Archivos de evidencia

- [Inventario](../reports/pilot/inventory.json), [geometrías](../reports/pilot/geometries.md) y [procedencia de modelos](../reports/pilot/upstream.json).
- [Piloto e inferencia](../reports/pilot/inference-results.json), [máscaras](../reports/pilot/quality.json), [MPS](../reports/pilot/mps.json) y [pesos reproducibles](../reports/pilot/checkpoint-verification.json).
- [Entrenamiento óptico](../reports/pilot/training-roundtrip.json) y [entrenamiento radar](../reports/pilot/radar-training-roundtrip.json).
- [Servicio óptico](../reports/pilot/service-validation.json), [servicio radar](../reports/pilot/radar-service-validation.json) y [restauración](../reports/pilot/restore-validation.json).
- [Encoder Satlas](../reports/pilot/satlas-smoke.md), [instalación wheel](../reports/pilot/wheel-validation.json), [Docker CPU](../reports/pilot/docker-validation.json), [pruebas/CI](../reports/pilot/software-validation.json) y [empaquetado](../reports/pilot/package-validation.json).
- [Cobertura y excepciones](../reports/history/).
- [Corrección geométrica de cobertura e impacto sobre el snapshot](../reports/pilot/coverage-correction.json).
