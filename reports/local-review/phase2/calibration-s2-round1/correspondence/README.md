Correspondencia de calibración S2, primera ronda
================================================

Los seis informes `*-review.json` enumeran las propuestas existentes y las referencias adicionales para consolidación, pendiente de adjudicación final. No se escribió en el catálogo ni se ejecutó inferencia, entrenamiento, calibración o evaluación. La autoría es de IA; no son etiquetas humanas.

Los seis primeros informes raw permanecen intactos. Sus 21 referencias se fijaron antes de leer propuestas. Después se consultaron únicamente IDs, geometrías y estado de revisión: 17 propuestas de Quetzal corresponden a referencias raw y cuatro referencias no tenían propuesta. No se leyeron scores ni se adaptaron cajas para mejorar coincidencias.

La segunda revisión raw identificó otra nave en el muelle oriental de Quetzal, antes de calcular métricas. Está documentada por separado en `../prq-20251010-s2-second-raw-addendum.json`, con referencia V27, caja nativa `[427,16,436,33]` en el grid de fondeo y contexto raw de enero. Pertenece solo a `prq_muelles`. No se presenta como parte de la primera pasada ciega, ni como cambio físico entre fechas. El total propuesto es **19 referencias en Quetzal y tres en Puerto Barrios: 17 aceptaciones existentes y cinco altas**. Santo Tomás no aporta positivos en el ámbito revisado de esta ronda; el resto de sus áreas sigue desconocido.

Se auditan tres cambios de ámbito, todos en observaciones sin propuestas catalogadas:

1. Santo Tomás fondeo, 9 de octubre: se retira la península terrestre y se conserva únicamente una franja de agua inequívoca de 52.708,90 m², dentro del AOI y de la máscara originales. Se retiran 1.213.373,41 m². El AOI provisional sigue siendo mayormente terrestre y no se modificó.
2. Quetzal patios, 10 de octubre: el ámbito inicial era un residuo geométrico de menos de 10⁻⁵ m² tras exclusiones, sin superficie interpretable. Se normaliza a `null`, como abstención.
3. Quetzal muelles, 10 de octubre: se añade una ventana de un píxel alrededor de V27. Aporta 20.417,78 m² al ámbito; no añade una afirmación negativa alrededor del casco y mantiene desconocido el resto de la interfaz.

Las tres ventanas positivas de Puerto Barrios que discrepan con la máscara automática se conservaron del informe raw. Las máscaras de calidad no se alteraron; requieren adjudicación explícita. Las cajas son localizaciones visuales, no mediciones físicas de eslora o manga. Familia y dimensiones permanecen nulas.

`coverage.json` es la contabilidad geométrica vigente. Sus áreas se calculan en UTM, densificando bordes geográficos antes de proyectar. Las filas por zona pueden superponerse; las uniones por puerto/pasada cuentan esas superposiciones una sola vez. La suma entre fechas mide área por pasada, no superficie geográfica única. Las diferencias menores de 0,01 m² son redondeo/proyección, no extensiones de máscara intencionales. El campo `coverage` y `proposed_scope_render` heredados dentro de cada observación conservan las anotaciones de la primera pasada: para el ámbito derivado se deben usar la geometría explícita, este archivo y `scope-renders.json`.

| Puerto | Pasadas elegidas | Referencias propuestas | Observaciones con ámbito / abstenciones | Cobertura de área por pasada, dos fechas |
| --- | ---: | ---: | ---: | ---: |
| Quetzal | 2 | 19 | 3 / 5 | 47,56 % |
| Santo Tomás | 2 | 0 | 2 / 6 | 3,20 % |
| Puerto Barrios | 2 | 3 | 4 / 4 | 34,67 % |

Las seis asignaciones puerto/pasada representan cuatro adquisiciones únicas; Santo Tomás y Puerto Barrios comparten las dos capturas del Caribe. La primera captura de cada puerto se conserva como abstención, no como cero barcos ni como sustitución. Ningún puerto alcanza 30 referencias y esta correspondencia no demuestra precisión ni recall.

`verification.json` fija hashes de informes, fuentes raw, snapshot del catálogo y cobertura. `scope-renders.json` fija las imágenes de los ámbitos derivados. `SHA256SUMS` permite verificar todos los archivos de esta carpeta. La verificación comprueba exhaustividad de IDs y referencias, centros positivos dentro del ámbito, geometrías válidas, archivos numéricos intactos y ausencia de cambios en los seis raw.
