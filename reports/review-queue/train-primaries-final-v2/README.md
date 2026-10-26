# Lote final de primera inspección TRAIN

Cola `train-bb18008ec1d937776f35`. 12 observaciones, dos por cada uno de seis estratos, **ocho pasadas físicas**. Ningún déficit. Todas las fracciones de observabilidad local son 1,0; esto no certifica calidad visual ni presencia de barcos.

[Manifest inmutable](queue.json) · [Auditoría de reproducción e integridad](audit.json)

SHA256 del archivo: `35c92bca87c79d64833edde6db1870f0b00fd2cc54a0177857e150a84a33d5bc`.

| Puerto | Sensor | Fecha UTC | Zona | Observación |
| --- | --- | --- | --- | --- |
| prq | sentinel-1 | 2025-02-07 | prq_fondeo | `096198a8-e91c-4444-b07c-1370aa4570ac` |
| prq | sentinel-1 | 2025-03-03 | prq_fondeo | `ab7d3fb2-e7a2-42d7-a5c4-47da00d5c2d8` |
| prq | sentinel-2 | 2025-03-04 | prq_muelles | `c2aaad3d-b82c-4b50-bdd8-91715124ce5c` |
| prq | sentinel-2 | 2025-04-03 | prq_fondeo | `09b37fd3-c189-4bae-aa85-06da102c023b` |
| pbr | sentinel-1 | 2025-04-03 | pbr_canal | `1d86f54e-70f7-4e2b-bcca-d8949ac00271` |
| pbr | sentinel-1 | 2025-05-21 | pbr_canal | `26c41fa8-08e3-4257-8c9c-c446f8cab360` |
| pbr | sentinel-2 | 2025-01-10 | pbr_canal | `4796c120-bd2d-42c8-9bbf-7bcfe8f467a0` |
| pbr | sentinel-2 | 2025-05-18 | pbr_canal | `3b7ff03b-5224-410b-86f7-18829eeb9a95` |
| stc | sentinel-1 | 2025-04-03 | stc_fondeo | `db36e4f9-e8aa-4419-be3b-972b46bdcbd1` |
| stc | sentinel-1 | 2025-05-21 | stc_fondeo | `e771fad3-7813-4297-913f-2cd1538058b5` |
| stc | sentinel-2 | 2025-01-10 | stc_fondeo | `d9f3dfe4-892b-49cc-80a6-58fe99fda67a` |
| stc | sentinel-2 | 2025-05-18 | stc_muelles | `05208f24-0534-4107-9bf1-700c46c84632` |

Este es el lote activo. Conserva las 12 observaciones de preparación y agrega georreferencia nativa validada y exclusión de primeras inspecciones anteriores por identidad. Las revisiones posteriores se guardan en recibos separados y no alteran este manifest; al preparar otro lote, excluyen todas las representaciones del mismo puerto/pasada/zona. Un recibo corrupto impide generar otra cola hasta corregir el problema de integridad.

Se reconstruyó la cola con idénticos metadatos y se obtuvo el mismo manifest. Los 26 manifiestos previos y el snapshot suplementario de fase 3 siguen intactos. Para seleccionar no se inspeccionaron píxeles, no se importaron etiquetas y no se entrenó. Los resultados de posteriores inspecciones no forman parte de esta auditoría de selección.

La ruta ciega solo expone previews crudos registrados, límites estáticos y metadatos permitidos. La proyección usa el CRS y límites nativos del preview; las áreas se recortan a la zona configurada. Las propuestas y revisiones anteriores requieren una continuación explícita después del recibo. Este filtro de claridad es para TRAIN; no es muestreo representativo de validación.

Los previews conservan la atribución **Contains modified Copernicus Sentinel data 2025**; aquí se fijan IDs/hashes, sin redistribuir nuevas imágenes.
