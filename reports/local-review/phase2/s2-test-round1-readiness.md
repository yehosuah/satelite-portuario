La primera ronda S2 TEST está materializada y su revisión visual independiente está fijada para adjudicación. No se ha congelado un dataset de test a partir de estas propuestas.

Estado comprobado: 2026-10-21T16:23:22.050352Z. Las fracciones y estados de calidad proceden del snapshot de metadatos previo; no se consultaron detecciones ni scores para producir este informe.

| Split | Sensor | Asignaciones | Adquisiciones | Observaciones | Calidad registrada |
|---|---|---:|---:|---:|---|
| calibration | sentinel-1 | 6 | 4 | 19 | 3 partial, 16 usable |
| calibration | sentinel-2 | 6 | 4 | 24 | 7 partial, 13 unusable, 4 usable |
| test | sentinel-1 | 6 | 4 | 14 | 6 partial, 8 usable |
| test | sentinel-2 | 6 | 4 | 24 | 9 partial, 15 usable |

S2 TEST: PRQ y Caribe del 3 y 13 de abril de 2026; 6 asignaciones de puerto y 4 adquisiciones. Las 24 observaciones conservan 384 assets con SHA256 comprobado. El 3 de abril las 12 zonas son utilizables; el 13, PRQ tiene 3 utilizables y 1 parcial, y Caribe 8 parciales.

Los cuatro informes raw-first y sus 24 renders de ámbito están fijados en `s2-test-round1-raw-reference-lock.json`. Se requiere una segunda adjudicación independiente antes de aplicar revisiones y congelar test por separado. No se ha calculado precisión, recall, F1 ni métrica de detector. Los hiperparámetros y umbrales no deben modificarse usando estas referencias.

Ambigüedad explícita: STC 3 de abril muestra dos lóbulos claros junto al muelle; esta revisión no puede distinguir con suficiente seguridad un casco, dos cascos o infraestructura. Se excluye el rectángulo nativo [441,1074,460,1108] de positivos y negativos, junto con una franja del borde del muelle. Se conserva el raw para segunda revisión. El 13 de abril, los muelles STC/PBR y patios PBR no sostienen un ámbito claro suficiente: abstención, sin convertirlos en cero barcos.

La zona provisional de fondeo STC contiene mucha tierra. Solo se proponen subáreas marinas inspeccionadas; la tierra no se utiliza como evidencia de ausencia marina. Los previews de fondeo STC se revisaron directamente porque el mosaico del canal no incluye su tesela noroeste; las otras zonas compartidas se verificaron por igualdad RGB en los píxeles de zona.

CALIBRATION S2: el Caribe del 2 de octubre y PRQ del 5 de octubre tienen observabilidad insuficiente; Caribe del 9 de octubre es parcial y PRQ del 10 utilizable. La primera reserva cronológica Caribe del 5 de octubre ya fue activada por el agente de protocolo mediante execution-order-v4.json; se preservan v1–v3. La activación es por visibilidad y no utiliza detecciones.

Exposición previa declarada: estructura de informes TRAIN y distribución portuaria; durante auditoría de código se inspeccionaron hashes y nodata de S1 test, sin labels. Esta revisión no consultó geometrías de otro revisor de test ni propuestas del modelo. La posterior correspondencia autorizada se realizará en addenda separados, solo con IDs/geometrías, sin scores ni métricas.

Ninguna escritura al catálogo, al ledger o a los assets originales. Las propuestas de etiquetas y los renders de este informe son de IA, no ground truth humano.
