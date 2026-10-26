# Evidencia del histórico

El estado operativo actual se concilia por campañas congeladas:

- [Conciliación global](global-closure-20261026.json): 833 adquisiciones objetivo,
  827 procesadas y seis excepciones, sin pendientes; 5.018 observaciones publicadas
  corresponden a 4.996 pares únicos de adquisición/zona.
- [Campaña original](closure-20261026.md): 818 adquisiciones procesadas y seis
  excepciones documentadas. [Recibo de integridad](closure-20261026.json).
- [Complemento de los cuatro pilotos](pilot-completion-20261026.json): 24
  observaciones nuevas, conservando las 22 revisadas.
  [Comparación de cobertura e inmutabilidad](pilot-completion-comparison-20261026.json).
- [Cinco adquisiciones del 22–25 de octubre](incremental-20261025-closure.json), con [inventario de fuentes](incremental-inventory-20261025.json):
  32 observaciones nuevas, con 14 utilizables, tres parciales y 15 no observables.
  Este corte verificó SHA256 de los 122.894 activos registrados (31.062.920.947 bytes).
- [Reparación de tres juegos de márgenes](closure-evidence-repair-20261026.json):
  igualdad de los mosaicos originales antes de registrar evidencia completa.
- [Medición del control de almacenamiento](storage-scan-benchmark-20261026.json):
  recorrido más rápido con los mismos bytes, límites y exclusión de enlaces.

`processing-plan.md` conserva el diseño y las mediciones iniciales. Los archivos
`exceptions.json` y `coverage.parquet` son cortes anteriores, identificados por
su fecha de generación. Evalúan cada receta por separado y sus filas
`not_scheduled` no representan el trabajo pendiente de las campañas actuales.
No se reescriben esos cortes para aparentar una ejecución distinta.

La investigación de `missing-context-investigation.json` explica las seis
excepciones radar originales. Ninguna se convierte en una observación de cero
barcos. Integridad, procesamiento y cobertura tampoco validan la precisión de
los modelos.
