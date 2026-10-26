# Corroboración SAR/AIS de GFW — PRQ S1, 7 de febrero de 2025

El cruce congelado corrobora **4 de los 16 candidatos visuales** de esta escena con detecciones SAR de GFW. Los cuatro tienen un MMSI presente y `matching_score >= 0.01`. Es corroboración TRAIN de centros; no estima precisión o recall, no valida las cajas y no convierte los otros 12 candidatos en negativos.

| Candidato local | Distancia entre centros, m | Enlace AIS según regla congelada |
|---|---:|---|
| second-pass-01-11 | 45.419869 | Sí |
| second-pass-01-13 | 33.995584 | Sí |
| second-pass-01-14 | 40.825876 | Sí |
| second-pass-01-15 | 46.270271 | Sí |

La observación `096198a8-e91c-4444-b07c-1370aa4570ac` es PRQ fondeo, Sentinel-1, entrada 1 de la geometría adjudicada. El prefijo completo de plataforma, modo, producto, polarización, inicio, fin, órbita absoluta y datatake selecciona **una escena y 55 filas** del CSV mensual. Las 55 superan `presence_score >= 0.7`; 10 puntos están dentro del área revisada. Todos registran `2025-02-07 00:14:29 UTC`, dentro de la adquisición iniciada a `00:14:16.275856Z` y finalizada a `00:14:41`. No se exigió igualdad artificial con el segundo inicial de la adquisición.

Se proyectaron los polígonos visuales y puntos GFW a **EPSG:32615**, con orden longitud/latitud explícito, y se comparó el centroide del polígono completo con el punto GFW. El radio máximo permaneció en **50 m**. Las cuatro aristas elegibles tienen grado uno en ambos lados: no hay empates, conflictos ni decisiones oportunistas de asignación. Todos los puntos emparejados están dentro del área revisada. No hay filas exactas duplicadas en la escena.

Quedan **12 candidatos locales sin correspondencia** y **6 detecciones GFW dentro del área sin candidato local a 50 m**. Ambas cantidades son desacuerdos del cruce, no falsos positivos ni falsos negativos demostrados. Los otros **27 de los 43 candidatos** del documento adjudicado no fueron examinados en este análisis.

El plan se creó antes de descargar el archivo. Se comprobaron hashes antes y después del cálculo:

| Fuente | SHA256 |
|---|---|
| `plan.json` privado | `0774860b3d4dfc4f2b6e2decad8b1da3052a68a08df29c262ec978d8fe15a99a` |
| `sar_vessel_detections_pipev3_202502.csv`, 37.671.173 bytes | `3085c2a4b72c08d69d9d586096b408bf9489da0851dd13ed55082206d75f3e51` |
| `adjudicated-geometry.json` | `fb757ae6f09a3f3e95ce873359d81d833293afa8d07e672d3f80f5d23c92f565` |
| Resultado público `gfw-sar-corroboration.json` | `3ffd06331869b3cd66cc951826c89b444a0eef2293a74e1771c2300a82cf629f` |
| Correspondencias privadas completas | `b817b04004f56acc57c1805b2dcfff2d1d68c07e4f545fbd5fcd6ef03e190206` |

La correspondencia privada queda en `data/references/gfw/sar-train-202502/correspondences-prq-s1-20250207.json`, con permisos 0600. Los entregables públicos no incluyen MMSI, identidades de buques, filas completas ni coordenadas individuales.

GFW aplica otro detector a la misma imagen Sentinel-1: esa coincidencia no es independiente del sensor. El emparejamiento AIS aporta una fuente adicional, pero no demuestra por sí solo cada contorno visual ni constituye adjudicación humana. `length_m` es una estimación del modelo, no eslora verdadera independiente. Hay 34 valores de `matching_score` superiores a uno en la escena; no se interpreta como probabilidad calibrada.

Según las advertencias del esquema del portal inspeccionado por la sesión principal, se excluyen objetos a menos de 1 km de costa, la resolución SAR es aproximadamente 20 m y pueden omitirse buques pequeños alrededor de 15 m o menos. Aquí no se calculó distancia a costa ni se midió la exactitud geográfica del proveedor. No se reajustaron umbrales, no se consultaron modelos locales ni CAL/TEST y no se modificaron etiquetas.

Fuente: Global Fishing Watch, [Data Download Portal — detecciones Sentinel-1](https://globalfishingwatch.org/data-download/datasets/public-sar-vessel-detections%3Av20231026), archivo mensual `pipev3` de febrero de 2025, descargado por la sesión principal el 26 de octubre de 2026. Licencia registrada en el recibo: CC BY-NC 4.0. La siguiente acción útil es revisar independientemente los desacuerdos conservando este resultado fijo, antes de proponer otro experimento TRAIN.
