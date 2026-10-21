# Revisión S1 TRAIN Caribe: mayo y junio

Selección fijada antes de imágenes mediante `execution-order-v5.json`, rangos 9–12 de PBR/STC: 1 y 21 de mayo, 2 y 18 de junio de 2025. `selection.json` conserva las claves exactas, el hash del orden y el estado inicial. No se sustituyó ninguna fecha ni se cambió una AOI.

Se completó la revisión RAW de **mayo 1**. Las otras tres adquisiciones siguen `pending`, con cero intentos en la última comprobación de `readiness.json`. No se activó ninguna fuente ni se inspeccionaron sus imágenes, datos de calibración o datos de test. Este lote aún no permite cerrar la revisión de todos los primarios TRAIN Caribe.

La evidencia de mayo 1 pasó 32 verificaciones de assets publicados, 42 verificaciones de archivos retenidos y la verificación del manifiesto. Los mosaicos numéricos publicados son idénticos a sus cores aplicables. Se inspeccionaron a resolución nativa los cuatro paneles con datos de VV/VH y contexto, las máscaras y cuatro detalles ampliados. Los dos paneles del sur no contienen datos; se verificó numéricamente. Contexto compatible: 2 de marzo, órbita relativa 128 DESCENDING, 60 días anteriores. La escena de contexto ya se había revisado en TRAIN; no se afirma independencia frente a esa revisión anterior.

No hay cascos inequívocos dentro de las zonas de observación de mayo 1. Dos firmas VV contiguas en el canal PBR y un contacto débil en canal/fondeo permanecen indeterminados, con cajas exactas y geometrías WGS84 en los informes. Se excluyeron de negativos. El segundo contacto está fuera de la máscara común con contexto; no se borró de la evidencia por ese motivo. No tiene definición suficiente para proponer una referencia positiva ni una extensión de máscara.

| Observación | Ámbito negativo propuesto, pendiente de adjudicación |
|---|---:|
| PBR canal | 6.267 píxeles centrales, excluyendo la vecindad ambigua |
| PBR fondeo | 105 píxeles centrales; fragmento de 0,69% del área configurada, no representativo del fondeo |
| STC canal | 6.755 píxeles centrales |
| STC fondeo | Nulo; ninguna superficie común observable |

Los muelles y patios no están cubiertos en esta adquisición; no se infiere ausencia en ellos. Los ámbitos se limitaron a la máscara observable y válida, con dos píxeles de guarda en el borde interno sin datos. Las geometrías serializadas se comprobaron contra AOIs y exclusiones, y se verificó su número de píxeles centrales. Los cuatro overlays fueron revisados visualmente.

La primera pasada está congelada en `20250501-raw-first-pass.json`. Los informes `pbr-20250501-s1-review.json` y `stc-20250501-s1-review.json` conservan el esquema 1.1 y procedencia `codex-ai-reviewer-s1`, expresamente IA y no humana. `family`, `length_m` y `width_m` permanecen nulos. No se consultaron conteos, scores, geometrías ni overlays de predicciones; los campos vacíos de propuestas no equivalen a cero propuestas del modelo. No se escribió en DB, configuración o código compartido.

Para adjudicación rápida: `tmp/local-review/phase2/train-s1-may-june/may1-scopes-contact.png` y `may1-raw-context-contact.png`. Los originales a resolución nativa y los detalles están en la subcarpeta `20250501/`.

Mayo 1 no añade positivos para entrenar S1 en PBR/STC. La próxima acción es materializar el primario de mayo 21 y continuar en el orden congelado; una ausencia o ambigüedad visual no autoriza cambiar la selección.
