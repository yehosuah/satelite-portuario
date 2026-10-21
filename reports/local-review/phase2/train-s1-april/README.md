Revisión cruda independiente de IA, pendiente de adjudicación del coordinador. Las primarias TRAIN 7 y 8 de PBR/STC son las del 3 y 19 de abril de 2025, conforme a `execution-order-v4.json`; no se sustituyeron fechas. Ambas estaban completas. Se comprobaron 88 activos publicados, 77 archivos retenidos y dos manifiestos, además de igualdad entre mosaicos y núcleos aplicables.

Se inspeccionaron VV/VH objetivo y contexto, máscaras y AOI antes de fijar la primera pasada. No se consultaron conteos, puntuaciones, geometrías ni superposiciones del detector durante esta revisión. Por eso `detection_count` es null y `existing_detections: []` no significa que el modelo no tenga propuestas.

Hay cuatro informes compatibles con el esquema anterior y once observaciones. No se confirma ningún buque dentro de las AOI actuales. Las firmas VV aisladas fuera de las zonas quedan como incertidumbres contextuales, sin convertirse en etiquetas. Familia, eslora y manga permanecen null.

Los dos muelles del 3 de abril quedan con ámbito null por interfaz ambigua; el residuo de STC tampoco tiene interior después de una erosión de 5 m. Los patios conservan controles negativos terrestres interiores. STC fondeo conserva solo la franja marina explícita de 2.400 píxeles, sin atribuir representatividad al polígono que mayormente cubre la península. PBR fondeo del 19 de abril conserva apenas 46 píxeles después del margen de dos píxeles frente a nodata: área revisable mínima, pendiente de adjudicación, que no representa un fondeo completo.

Los demás ámbitos propuestos se limitan al agua observable, excluyendo la interfaz terminal incierta de PBR. Todos son propuestas para adjudicación; antes de activar `scene_complete` hay que resolver las propuestas provisionales que los invadan. Un ámbito null no debe reemplazarse por la zona completa.

Las láminas están en `tmp/local-review/phase2/train-s1-april/`: `april-11-scopes-contact.png`, `april-raw-context-contact.png` y `april-uncertainty-context-contact.png`. Cada adquisición tiene paneles nativos y detalles. `scope-verification.json` conserva los hashes finales de los informes.

No se cambió código, configuración, AOI, plan ni base de datos. No se ejecutó inferencia ni se activaron fuentes fuera del orden.

La independencia declarada se refiere a las predicciones de los objetivos de abril. Los contextos de febrero ya habían sido revisados en trabajo TRAIN anterior; no se afirma desconocimiento de aquella revisión previa. No se reabrieron sus resultados del detector durante esta tarea.
