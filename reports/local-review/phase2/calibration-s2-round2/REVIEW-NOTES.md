# Segunda ronda CAL S2: revisión cruda de IA

Las seis asignaciones son las primarias ordinales 3 y 4 del orden de ejecución v4: Caribe 17 y 25 de octubre de 2025; PRQ 20 y 25 de octubre. No se eligieron por resultados del modelo ni por cantidad de barcos. Los seis informes `*-raw-review.json` y sus ámbitos iniciales están fijados en `SHA256SUMS`; la revisión todavía requiere adjudicación independiente y correspondencia. No importarlos como etiquetas vacías.

| Puerto y fecha | Objetos visuales propuestos | Instancias de zona | Ámbitos revisables | Abstenciones |
| --- | ---: | ---: | ---: | ---: |
| PBR 17-Oct | 2 | 2 | 1 | 3 |
| STC 17-Oct | 2 | 2 | 1 | 3 |
| PRQ 20-Oct | 13 | 14 | 4 | 0 |
| PRQ 25-Oct | 18 | 18 | 4 | 0 |
| PBR 25-Oct | 2 | 2 | 4 | 0 |
| STC 25-Oct | 2 | 2 | 4 | 0 |

Son 39 objetos en cuatro adquisiciones únicas, con 40 instancias porque PRQ 20-Oct V15 pertenece a muelles y canal. Se conserva la misma clave física en ambas zonas. Los conteos son propuestas de referencia de IA; no son precisión, recall, independencia de objetos entre fechas ni validación humana.

El Caribe del 17-Oct tiene máscara automática de observabilidad completamente nula. Los cuatro cascos visibles están aislados mediante ventanas manuales locales, sin negativos y con `requires_observation_status_adjudication=true`. Las máscaras y el estado original no se modificaron. Los ámbitos de fondeo STC excluyen la península terrestre. Patios conserva su función de control terrestre.

En PRQ 20-Oct, una firma bajo nube y algunas interfaces de muelle permanecen desconocidas. Un buque cuyo centro queda fuera del borde occidental del fondeo no cuenta como referencia local; su parte visible también se excluye del ámbito negativo. PRQ 25-Oct V12 sí tiene centro dentro del AOI aunque la caja cruce el borde: se conserva la caja completa sin confundir ese cruce con nubosidad. Las referencias no afirman familia ni dimensiones físicas.

La comprobación visual de los renders encontró que el ámbito inicial del canal PRQ aún rozaba parte del rompeolas. `prq-breakwater-scope-addendum.json` restringe 105616 m² por fecha antes de consultar cualquier propuesta, con razones y geometrías explícitas. Conserva los informes iniciales y todas las referencias; los dos renders `*-scope-corrected-zoom.png` muestran el ámbito efectivo propuesto. La adjudicación deberá aplicar este addendum, no el ámbito inicial de canal.

Se comprobaron los 384 hashes de activos y la validez de las geometrías. Ningún ámbito negativo intersecta las cajas positivas. `verification.json` fija las comprobaciones y los hashes de los seis informes. `scope-zoom-manifest.json` documenta ampliaciones para revisión. Los otros archivos crudos de inspección están en `tmp/local-review/phase2/calibration-s2-round2/`; su procedencia numérica está en cada informe.

Las comparaciones entre imágenes crudas de otras fechas se utilizaron únicamente para interpretar infraestructura de atraques. Están declaradas con hashes como contexto; no se consultaron etiquetas ni salidas de modelos de esas fechas, ni se afirmó identidad física entre barcos. No se inspeccionó TEST, no se entrenó, no se ejecutó nueva inferencia y no se cambiaron parámetros ni fuente.
