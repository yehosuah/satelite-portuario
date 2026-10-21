# Estado de la entrega

Esta versión entrega el servicio local y una base reproducible para desarrollar los modelos del [plan aprobado](superpowers/plans/2026-10-20-observatorio-satelital.md). La entrega científica completa sigue pendiente. La publicación del repositorio no declara cumplida la meta de precisión ni transforma las propuestas en un histórico validado.

El repositorio es público. El paquete portable está completo y verificado localmente, pero su transferencia a GitHub quedó incompleta por fallos TCP/TLS: 11 de 34 partes confirmadas. El release `v0.1.0` permanece como borrador y no se presenta como una descarga pública disponible. [Estado de transferencia](../reports/pilot/release-upload-status.json).

| Parte del plan | Implementado y comprobado | Evidencia pendiente |
|---|---|---|
| T1: inventario y geometrías | 81 páginas, 1,267 productos, 863 adquisiciones únicas; 828 del período y 35 de contexto. Doce zonas con procedencia. | Confirmar límites operativos de las zonas provisionales. |
| T2: pilotos y recetas | Inferencia real S1/S2 en los tres puertos, nubes/sombras ópticas, contexto radar compatible, pasos supervisados públicos, pesos/hashes fijados, CPU y MPS. | Calidad representativa por tamaño, muelle/fondeo, nubosidad y geometría radar. |
| T3: persistencia | SQLite, cola idempotente por adquisición/zona/modelos, leases, reintentos, pausas y archivos atómicos. | Operación prolongada en el equipo de destino. |
| T4: revisión | Visor probado con imágenes reales; revisión auditada, omitidos, procedencia, grupos de split y exportación de área revisada. | Revisión humana local completa y conjuntos train/calibration/test congelados. |
| T5: buques y atributos | Entrenamiento/exportación/restauración de detectores; dos pesos propios de prueba derivados de muestras públicas. Cabezas por sensor, caché de características, evaluación independiente, abstención por campo y estrato. | Detectores y cabezas entrenados con referencias locales; precisión ≥90%, recall, errores de dimensiones y clasificación por estrato. |
| T6: cambios | Encoder Satlas real verificado, decoder siamés, entrenamiento con pares revisados, comparación en zona estática, evaluación de pares test congelados y estados t0/t1/t2/reversión auditados. | Pares locales positivos/negativos, pesos propios del decoder y evaluación independiente. No se han publicado eventos físicos reales. |
| T7: servicio | API localhost y visor; fechas UTC, filtros, paginación, activos, cobertura, modelos, trabajos y revisiones protegidas. | Ningún servicio remoto de producción desplegado. |
| T8: histórico y continuidad | Inventario completo al corte, actualización solapada, lotes acotados y conciliación de cada adquisición/zona. Se procesaron 22 observaciones de zona de cuatro adquisiciones. | 824 adquisiciones del período siguen sin programar. También faltan las áreas no cubiertas de los recortes piloto. No se interpolaron esos huecos. |
| T9: portabilidad | Wheel/instalación aislada, Docker y exportación/restauración. El snapshot real verifica 270 archivos y reproduce inferencia de ambos detectores desde el volumen restaurado. | Validación científica y carga histórica completas antes de declarar una entrega operativa validada. |

## Hallazgos del piloto

Los seis recortes locales produjeron siete propuestas antes de repartirlas por zonas. La inspección mostró barcos junto a muelles que el detector base omitió y propuestas ambiguas en agua. Ni esas siete propuestas ni los ceros de otras escenas son conteos confirmados. Los estados `usable` y `partial` describen observabilidad del área, no exactitud del detector.

El snapshot conserva los resultados de la ejecución inicial: 10 zonas utilizables y 12 parciales. La revisión de CI detectó después un error al estimar cobertura con el número de píxeles del borde. El worker corregido usa la intersección geométrica. El [recálculo de las 22 observaciones](../reports/pilot/coverage-correction.json), sin modificar datos ni repetir inferencia, cambiaría cuatro estados de parcial a utilizable y como máximo 0.502 puntos porcentuales de observabilidad. Las detecciones no cambian. El snapshot conserva esa evidencia histórica y requiere reprocesamiento para adoptar el cálculo nuevo.

Los entrenamientos públicos fueron pruebas de una sola muestra: un positivo óptico y un negativo radar con cobertura de anotación verificada. Los pesos propios permiten comprobar el ciclo de entrenamiento, guardado y restauración. No constituyen entrenamiento ni evaluación local suficientes.

La proyección de recursos en [el piloto](../reports/pilot/inference.md) corresponde a un recorte de 512 píxeles por puerto/adquisición. Estima 14.29–20.24 GB de transferencia y 21.9–28.8 horas seriales; no cubre todas las zonas ni incluye el trabajo de etiquetado. Por eso no se convirtió en una autorización técnica de carga completa.

## Para cerrar la entrega científica

El siguiente trabajo necesario es revisar escenas locales completas con el visor y aportar referencias independientes para familias y dimensiones. El [protocolo](labeling-protocol.md) fija separación entre entrenamiento, calibración y prueba, e incluye negativos y omisiones. La falta de una referencia conserva campos nulos.

Después se ejecutan las rutas de entrenamiento y evaluación ya implementadas, se fijan políticas por estrato y se dimensiona el procesamiento de zonas completas. Solo entonces corresponde completar el histórico y volver a comprobar su cobertura. `puertogt doctor --check-release` mantiene este estado pendiente de forma explícita.

## Archivos de evidencia

- [Inventario](../reports/pilot/inventory.json), [geometrías](../reports/pilot/geometries.md) y [procedencia de modelos](../reports/pilot/upstream.json).
- [Piloto e inferencia](../reports/pilot/inference-results.json), [máscaras](../reports/pilot/quality.json), [MPS](../reports/pilot/mps.json) y [pesos reproducibles](../reports/pilot/checkpoint-verification.json).
- [Entrenamiento óptico](../reports/pilot/training-roundtrip.json) y [entrenamiento radar](../reports/pilot/radar-training-roundtrip.json).
- [Servicio óptico](../reports/pilot/service-validation.json), [servicio radar](../reports/pilot/radar-service-validation.json) y [restauración](../reports/pilot/restore-validation.json).
- [Encoder Satlas](../reports/pilot/satlas-smoke.md), [instalación wheel](../reports/pilot/wheel-validation.json), [Docker CPU](../reports/pilot/docker-validation.json), [pruebas/CI](../reports/pilot/software-validation.json) y [empaquetado](../reports/pilot/package-validation.json).
- [Cobertura y excepciones](../reports/history/).
- [Corrección geométrica de cobertura e impacto sobre el snapshot](../reports/pilot/coverage-correction.json).
