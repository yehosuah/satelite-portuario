# Calibración del candidato óptico — 23 octubre 2026

**Falló la calibración: ninguno de los 90 umbrales prefijados cumplió precisión ≥90%, recall ≥80% y soporte por puerto. No se abrió TEST ni se desplegó el candidato.**

El refit TRAIN de 94.12% no se reprodujo en CAL. Los pesos y los 105 ámbitos / 113 referencias quedaron congelados antes de inferir. El umbral 0.50 se muestra como comparación descriptiva prefijada en TRAIN, no como selección posterior para producción.

| Puerto | TP | FP | FN | Precisión | Recall | Referencias | Pasadas |
|---|---:|---:|---:|---:|---:|---:|---:|
| PBR | 5 | 6 | 11 | 45.45% | 31.25% | 16 | 11 |
| PRQ | 66 | 21 | 21 | 75.86% | 75.86% | 87 | 5 |
| STC | 2 | 7 | 8 | 22.22% | 20.00% | 10 | 13 |

Global: 73 TP / 34 FP / 40 FN; precisión 68.22% y recall 64.60%.

Las referencias proceden de dos revisiones IA y adjudicación; no son verdad humana/AIS. Algunas áreas se redujeron tras ver las huellas de propuestas anteriores para conservar respuestas ambiguas como desconocidas. El resultado solo aplica a esas superficies explícitas. PBR tiene 16 referencias y STC 10: ambos están por debajo del mínimo 30, aun antes de observar precisión. PRQ conserva seis primarias posteriores sin revisar, fuera de este corte. Los límites geográficos provisionales siguen sin confirmación.

La prueba cerrada conserva IoU 0.5, la cuadrícula 0.10–0.99, deduplicación por objeto/pasada y teselas históricas originales con halos. Se conservan todos los umbrales fallidos en [threshold-grid.json](threshold-grid.json); no se repite CAL con otra receta sobre este mismo corte.

[Resultado legible por máquina](result.json) · [Fuentes y entorno congelados](freeze.json) · [Auditoría de revisión](../calibration-s2-slot-coverage-audit-v11.md)
