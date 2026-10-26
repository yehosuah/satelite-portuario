# Avisos de terceros

Verificación realizada el 21 de octubre de 2026 sobre las versiones fijadas y las fuentes oficiales enlazadas. Este archivo identifica componentes y datos de terceros; no les cambia la licencia.

**La licencia del código propio de Satélite Portuario está pendiente de decisión del titular.** La publicación del repositorio y las licencias de sus dependencias no asignan por sí mismas una licencia al código propio. Los pesos experimentales propios también deben conservar la procedencia de sus inicializaciones y datos, y no se presentan como modelos validados.

## Código, modelos y anotaciones

| Componente | Versión examinada | Licencia declarada y fuente |
|---|---|---|
| rslearn, Allen Institute for AI | `e0ec9c8f404f348de1b7d64521f7afc588c80e0a` | Apache-2.0. [LICENSE](https://github.com/allenai/rslearn/blob/e0ec9c8f404f348de1b7d64521f7afc588c80e0a/LICENSE) y [NOTICE](https://github.com/allenai/rslearn/blob/e0ec9c8f404f348de1b7d64521f7afc588c80e0a/NOTICE). |
| rslearn_projects, Ai2 | `0bae49abb76d1deee9b6b6ca2a59212611ebc6fe` | Código y pesos: Apache-2.0. Anotaciones: CC-BY-4.0. La distinción está expresamente declarada en su [README](https://github.com/allenai/rslearn_projects/blob/0bae49abb76d1deee9b6b6ca2a59212611ebc6fe/README.md); véase [LICENSE](https://github.com/allenai/rslearn_projects/blob/0bae49abb76d1deee9b6b6ca2a59212611ebc6fe/LICENSE). |
| OmniCloudMask, DPIRD-DMA | `d7ca21c686ab2ea63fd0f7e6a662a3179169f7e6` | MIT, Copyright (c) 2025 DPIRD-DMA. [LICENSE](https://github.com/DPIRD-DMA/OmniCloudMask/blob/d7ca21c686ab2ea63fd0f7e6a662a3179169f7e6/LICENSE) y [README](https://github.com/DPIRD-DMA/OmniCloudMask/blob/d7ca21c686ab2ea63fd0f7e6a662a3179169f7e6/README.md). |
| satlaspretrain_models, Ai2 | `7b5cd45adc3cad70b3834d65956974af6f6bffd0` | Código: Apache-2.0 según [LICENSE](https://github.com/allenai/satlaspretrain_models/blob/7b5cd45adc3cad70b3834d65956974af6f6bffd0/LICENSE). Checkpoints: ODC-BY según [README, Available Pretrained Models](https://github.com/allenai/satlaspretrain_models/blob/7b5cd45adc3cad70b3834d65956974af6f6bffd0/README.md#available-pretrained-models), con [texto de licencia de datos](https://github.com/allenai/satlas/blob/main/DataLicense). |

Los archivos de licencia y avisos de rslearn, rslearn_projects y OmniCloudMask se contrastaron con los clones locales usados para el piloto. Los clones, entornos y caches no forman parte del repositorio publicado.

El NOTICE de rslearn incluye avisos adicionales de DETR (Facebook, Apache-2.0), CROMA (Anthony Fuller, MIT), Galileo y Presto (Presto Authors, MIT), y Prithvi-WxC (Inter Agency Implementation and Advanced Concepts, MIT). Conserva el NOTICE de esa distribución y sus textos completos cuando distribuyas el paquete o código derivado. Esta lista no declara que todos esos módulos participen en la inferencia del piloto.

Las dependencias instaladas conservan sus propios archivos de licencia. Al redistribuir código, imágenes de contenedor, pesos o bundles, conserva los avisos aplicables de cada componente. Los manifiestos en `config/models/` y los modelos registrados documentan fuentes, hashes y licencias del modelo correspondiente. Una licencia de código no debe aplicarse automáticamente a las anotaciones, imágenes o checkpoints de otra fuente.

El experimento de atributos utiliza metadatos de [vessel-detection-sentinels, commit 60b003d](https://github.com/allenai/vessel-detection-sentinels/tree/60b003d059937a9fcddbf6e1c9b64c00c33c0279), con procedencia y licencias registradas en [el plan de compatibilidad](reports/public-attributes/compatibility-plan-v1.json). No identifica buques locales ni acredita una separación por identidad entre particiones.

El experimento de cambios utiliza [OSCD](https://rcdaudt.github.io/oscd/). El registro DOI declara CC BY 4.0, mientras el README del archivo original declara CC BY-NC-SA y consulta comercial. Se conserva esta discrepancia en [el informe de fuentes](reports/change/public-data/oscd-bounded-results.md) y se aplica la restricción del archivo: no se redistribuyen sus máscaras ni los pesos derivados en esta entrega pública.

## Datos Copernicus Sentinel

Las adquisiciones S1 y S2 proceden del programa Copernicus. El [aviso oficial de datos Sentinel](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice) establece su acceso abierto y la atribución al distribuir datos originales o modificados. Los [términos de Copernicus Data Space](https://dataspace.copernicus.eu/terms-and-conditions) remiten a ese aviso para los datos Sentinel y distinguen los demás contenidos del portal.

Para datos originales, conserva `Copernicus Sentinel data [Year]`. Para recortes, reproyecciones, combinaciones RGB y otros datos modificados, conserva `Contains modified Copernicus Sentinel data [Year]`, sustituyendo el año por el de adquisición. Los previews locales de 2026 requieren ese año. La fuente de descarga, producto, fecha y transformaciones permanecen en los metadatos; la disponibilidad de un espejo no elimina la atribución de Copernicus.

Los RGB y comparaciones temporales publicados en `reports/local-review/phase3-diagnosis/`
contienen datos de 2025: **Contains modified Copernicus Sentinel data 2025**.
Sus fuentes y transformaciones están fijadas en los recibos de esa carpeta.

El inventario público contiene metadatos y huellas de productos, no una copia del portal ni de su contenido editorial. El uso de Sentinel no implica respaldo de ESA, la Unión Europea o los proveedores a las conclusiones del proyecto.

## Geometrías basadas en OpenStreetMap

`config/ports.geojson` contiene geometrías basadas en datos de **© OpenStreetMap contributors**, disponibles bajo [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/). La [página oficial de copyright de OpenStreetMap](https://www.openstreetmap.org/copyright) describe la atribución y las condiciones de reutilización y distribución de bases derivadas.

Los identificadores de objetos, fuentes consultadas y ajustes están registrados en cada geometría y en [el informe de geometrías](reports/pilot/geometries.md). Conserva esos créditos y la referencia a ODbL al distribuir esta capa o una base derivada de ella. Esta atribución no asigna ODbL al código de la aplicación.

Las fuentes de costa, geometrías candidatas y mapa de
`reports/coverage/geography-v2-candidates/` también derivan de **© OpenStreetMap
contributors, ODbL 1.0**. La carpeta conserva las consultas del 25 de octubre de
2026, versiones de objetos, hashes y construcción de las áreas. Las alternativas
están inactivas y no establecen jurisdicción ni límites de navegación.

También se consultaron referencias portuarias oficiales para orientación y coordenadas. Su publicación en una web no se interpreta como una licencia abierta de toda la carta o documento. No se distribuyen copias de esas cartas en este repositorio. Las geometrías son provisionales, requieren validación y no son aptas para navegación.

## Límites de este inventario

Este archivo cubre las fuentes de datos y las bases ML principales utilizadas o configuradas. Las dependencias transitivas se identifican en `uv.lock` y conservan las licencias de sus distribuciones. La licencia de cualquier nueva etiqueta, imagen, peso o referencia incorporada debe registrarse antes de redistribuirla. La referencia a una licencia no acredita calidad, exactitud ni validación científica de los resultados.
