# Viabilidad documental de referencias SAR de GFW

Consulta y actualización: 26 de octubre de 2026. Alcance ejecutado: una escena PRQ Sentinel-1 TRAIN del 7 de febrero de 2025; ningún otro mes o escena local. Esta nota no contiene resultados de CAL/TEST ni abre una evaluación nueva.

La sesión principal abrió el portal autenticado, revisó el esquema y descargó correctamente el archivo mensual `sar_vessel_detections_pipev3_202502.csv` (35,93 MB mostrados; 37.671.173 bytes reales), después de congelar un plan acotado. El CSV confirma detecciones individuales con escena, hora, coordenada y enlace AIS; no es la rejilla agregada de 4Wings Report. Esta descarga fue mensual global: no se ha demostrado un recorte espacial de servidor para Guatemala. Sólo se analizaron las filas del prefijo de escena fijado en el plan.

| Elemento requerido | Evidencia pública comprobada | Pendiente |
|---|---|---|
| Punto individual y precisión | CSV confirmado: `lat`, `lon`, coordenadas individuales usadas en orden longitud/latitud y proyectadas a EPSG:32615. | La cantidad de decimales no prueba exactitud geográfica; no se midió el error del proveedor. |
| Hora de detección | CSV confirmado: `timestamp`; las 55 filas de la escena tienen `2025-02-07 00:14:29 UTC`, con resolución de representación de un segundo. | No confundir representación con exactitud temporal; corresponde al interior de la adquisición, no al segundo inicial local. |
| Escena Sentinel-1 | CSV confirmado: `scene_id`; su prefijo incluye plataforma, modo/producto/polarización, inicio, fin, órbita y datatake. Coincide exactamente con la adquisición local congelada. | No se descargaron huellas adicionales ni se comprobó su clave de unión. |
| Vínculo AIS | CSV confirmado: `mmsi`, `matching_score`, `matched_category`. Cuatro correspondencias locales cumplen MMSI presente y score >=0.01. | `matching_score` no se interpreta como probabilidad calibrada: 34 filas de la escena superan uno. |
| Otros campos | Header completo de diez campos: `scene_id`, `timestamp`, `lat`, `lon`, `presence_score`, `length_m`, `mmsi`, `matching_score`, `fishing_score`, `matched_category`. | `length_m` es una estimación, no dimensión verdadera independiente. |
| Partición temporal | Descarga comprobada: febrero de 2025, archivo `pipev3`, 37.671.173 bytes. | No se descargaron ni verificaron los otros cinco meses del posible alcance enero–junio. |
| Recorte Guatemala | Se filtró localmente una escena congelada; no se consultaron otros meses. | Sigue sin comprobarse un filtro espacial del portal que evite la descarga mensual global. |

Fuentes primarias exactas:

- [Anuncio del dataset SAR, 31 de mayo de 2024](https://globalfishingwatch.org/platform-update/2024-may-data-download-portal-new-dataset-released-featuring-vessel-detections-from-sentinel-1-sar/): confirma detecciones, emparejamiento AIS, cuatro campos adicionales y huellas WKT.
- [Actualización AIS v3](https://globalfishingwatch.org/faqs/2024-aug-new-release-in-our-ais-data-pipeline-version-3/): documenta el nombre mensual de archivo y advierte incompatibilidad de identificadores entre versiones. La convención de 2024 no prueba el nombre vigente de archivos de 2025.
- [Actualización v5, 21 de octubre de 2026](https://globalfishingwatch.org/platform-update/version-5-data-pipeline-release/): confirma que las detecciones SAR del portal se alinean con v5; no especifica aquí una reescritura retroactiva de cada archivo de 2025.
- [Ejemplo oficial de Interaction SAR](https://api-doc.globalfishingwatch.org/our-apis/documentation/docs/examples/interaction/interaction-example2): `GET /v3/4wings/interaction/{z}/{x}/{y}/{cells}` devuelve `detections`, `timestamp`, `vessel_id`. Es una consulta por celda y no verifica el esquema del portal.
- [Bulk Download API](https://globalfishingwatch.org/our-apis/documentation/docs/v3/bulk-download): sólo documenta `public-fixed-infrastructure-data:latest`; no atribuir a esta API descarga de detecciones de buques.

URL comprobada por la sesión principal: [Data Download Portal — Sentinel-1](https://globalfishingwatch.org/data-download/datasets/public-sar-vessel-detections%3Av20231026). El lector web de la revisión inicial sólo expuso la carcasa JavaScript y el subagente no tenía navegador; la sesión principal resolvió el acceso mediante su navegador autenticado. La URL histórica contiene `v20231026`, pero el nombre del archivo descargado confirma `pipev3`; no se debe rebautizar como v5 por la versión actual de las APIs.

El [resultado del cruce](gfw-sar-corroboration.md) conserva **4 correspondencias 1:1 de 16 candidatos**, las cuatro con enlace AIS según el umbral congelado, a distancias de 33,996–46,270 m. Los 12 restantes no se convierten en negativos. El plan SHA256 `0774860b3d4dfc4f2b6e2decad8b1da3052a68a08df29c262ec978d8fe15a99a` precede a la descarga; el CSV SHA256 es `3085c2a4b72c08d69d9d586096b408bf9489da0851dd13ed55082206d75f3e51`. No se modificaron geometrías ni umbrales después de observar los datos.

El portal, según el esquema inspeccionado por la sesión principal, advierte exclusión de objetos a menos de 1 km de costa, resolución SAR aproximada de 20 m y omisiones de buques pequeños alrededor de 15 m o menos. Estas limitaciones impiden interpretar ausencia como negativo y deben revisarse antes de extender el uso a muelles. La corroboración proviene del mismo sensor y, cuando existe, de un emparejamiento AIS; no constituye referencia humana exhaustiva, cajas de verdad o certificación de precisión/recall.

Este subanálisis usó sólo archivos locales ya descargados: no usó credenciales, API de datos ni nuevas descargas, no creó cuentas, no aceptó condiciones y no modificó catálogo, etiquetas o particiones. No se utilizaron comentarios de Canny ni repositorios de terceros como evidencia del esquema. La descarga autenticada fue realizada por la sesión principal, y las correspondencias con MMSI permanecen privadas.
