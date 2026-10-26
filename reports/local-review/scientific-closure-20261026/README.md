# Conexión externa y prueba de pasadas nuevas

Global Fishing Watch quedó conectado y se importaron **43 referencias visuales de IA** en ocho ámbitos TRAIN. El refit óptico, probado en cuatro pasadas que no había visto, obtuvo **13 TP / 3 FP / 4 FN: precisión 81,25% y recall 76,47%**, a score e IoU 0,5. No cumple 90/80 ni constituye validación independiente. El modelo activo sigue igual y CAL/TEST permanecen cerrados.

## Referencias nuevas

La [primera inspección](../../improvements/20261026-increment/first-pass-findings.json) y la [segunda inspección ciega](second-pass.json) se congelaron antes de compararse. Los 41 candidatos iniciales tienen pareja geométrica; la mediana de IoU entre revisores es 0,7667. Los siete candidatos adicionales se resolvieron según la imagen y el alcance, sin consultar predicciones del refit. [Comparación](reviewer-comparison.json).

La [adjudicación](adjudicated-geometry.json) confirma además dos positivos ópticos junto al muelle. Tres áreas ópticas quedan vacías dentro de límites explícitos y cuatro escenas SAR del Caribe siguen como abstenciones. Las zonas excluidas permanecen desconocidas.

| Sensor / puerto | Áreas importadas | Referencias | Alcance |
|---|---:|---:|---|
| S1 / Quetzal | 2 | 26 | Agua revisada, excluyendo señales ambiguas |
| S2 / Quetzal | 2 | 16 | 19,56% del muelle y 99,96% del fondeo |
| S2 / Barrios | 2 | 0 | 100% y 89,90% del canal |
| S2 / Santo Tomás | 2 | 1 | 1,75% del fondeo y 47,74% del muelle |

La [importación](import-receipt.json) preservó literalmente las 43 geometrías y ocho ámbitos congelados. Añadió 51 revisiones: 41 correcciones, dos adiciones y ocho revisiones de observación. El catálogo tiene 5.026 observaciones, 4.430 detecciones y 623 revisiones. Las 572 revisiones y once modelos previos siguen idénticos; los 35 manifiestos congelados y V1 conservan sus hashes. [Verificación](preservation-verification.json).

Los [datasets exportados](dataset-export.json) son versiones inmutables con etiquetas, imágenes numéricas y teselas originales/halos. S2 contiene seis ámbitos y cuatro pasadas; S1, dos ámbitos y dos pasadas. Los archivos grandes siguen locales. Las cajas son localizaciones visuales, no esloras/mangas. Se declara la exposición previa de root a propuestas del modelo público; ninguna revisión se presenta como humana.

## Prueba óptica antes de otro entrenamiento

El [plan](refit-probe-plan.json) usa el checkpoint `e0990d7d69750650a5f5175361fc901de882ad0be35ef8fcfd62a3c4b8e73c68`, anclas 8/16/32/64 y seis ámbitos ópticos. Las pasadas del 10 de enero, 4 de marzo, 3 de abril y 18 de mayo de 2025 no aparecen en su ajuste original. Código, runtime, pesos, etiquetas y halos tienen hashes comprobados. Ejecutable: [probe_increment_refit.py](../../../scripts/probe_increment_refit.py).

| Puerto | TP | FP | FN | Precisión | Recall |
|---|---:|---:|---:|---:|---:|
| Quetzal | 13 | 2 | 3 | 86,67% | 81,25% |
| Barrios | 0 | 0 | 0 | No estimable | No estimable |
| Santo Tomás | 0 | 1 | 1 | 0% | 0% |
| Total | 13 | 3 | 4 | 81,25% | 76,47% |

[Métricas](refit-probe-report.json) y [recibo](refit-probe-completed.json). Consumió 12,21 segundos y un pico RSS de 1,32 GB, con cuatro hilos, lock compartido y presupuesto de 180 segundos. La guardia de 4 GiB se comprueba entre etapas; no es un límite duro del sistema operativo.

Es transferencia interna en TRAIN contra referencias de IA, no ajuste sobre las mismas imágenes ni evaluación independiente. No hubo búsqueda de umbral, entrenamiento nuevo, activación de pesos o cambio de geometría después del resultado. Barrios no aporta positivos y Santo Tomás sólo uno: este soporte no permite acreditar 90/80 por puerto. El CAL anterior fallido se conserva y TEST permanece reservado.

## Conexión externa comprobada

El [conector GFW](../../../docs/gfw-connection.md) usa credencial privada local, endpoint fijo, plan TRAIN y límites de páginas, bytes y tiempo. La consulta real obtuvo 2.402 entradas en 13 páginas y 31,26 segundos: **1.504 visitas únicas** después de deduplicar. Los 898 eventos de Barrios también aparecieron en el envolvente de Santo Tomás. [Calidad y cruce temporal](gfw-corroboration.md).

Las visitas no contienen posiciones instantáneas a la hora de Sentinel. El portal SAR ofrece otra fuente con escena, hora, coordenadas y emparejamiento AIS: [acceso y límites](gfw-sar-feasibility.md). Los originales y correspondencias de identidad permanecen privados. La ausencia de un registro GFW no demuestra ausencia de barco; sus dimensiones estimadas tampoco son verdad física independiente.

Se descargó el archivo SAR de febrero de 2025 (37,67 MB) bajo un segundo plan fijado de antemano. En la escena PRQ del 7 de febrero, **cuatro de 16 candidatos coinciden con detecciones GFW y enlace AIS**, con distancias de 34,00–46,27 m dentro del radio prefijado de 50 m. Los doce candidatos restantes y seis puntos GFW sin pareja son desacuerdos, no FP/FN demostrados. [Cruce SAR/AIS](gfw-sar-corroboration.md). No se cambió ninguna etiqueta con ese cruce.

## Software y límite científico

La versión 0.4.1 añade el conector y corrige una colisión de identidad entre recetas con anclas distintas. Las anclas predeterminadas conservan las claves históricas; un detector precargado incompatible ahora se rechaza. Un revisor independiente comprobó el conector, la corrección y el ejecutor. **454 pruebas Python, seis JavaScript y Ruff pasan.**

El próximo experimento de aprendizaje debe tratar el desequilibrio de positivos por puerto y compararse por pasadas completas. Para validar siguen faltando positivos suficientes del Caribe y referencias fechadas para los objetos ambiguos, especialmente junto a muelles: GFW SAR excluye objetos a menos de 1 km de costa. El consenso entre agentes, las métricas de ajuste y los intervalos de visita no sustituyen esas referencias.

`second-pass.json`, su freeze y `reviewer-comparison.json` son copias exactas de los artefactos locales citados en sus recibos. Las rutas de imágenes apuntan a evidencia privada del workspace. La adjudicación y los recibos no se reescribieron al publicarlos.
