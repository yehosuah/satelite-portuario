# Búsqueda de 500 m en fondeo de Puerto Quetzal

Activada localmente el 26 de octubre de 2026 con Satélite Portuario 0.4.2. La cola `train-a855416abd9aae0249d7` conserva las doce observaciones, orden, activos y zonas de referencia del lote padre. Tres entradas PRQ fondeo admiten además el anillo de agua de 500 m: radar del 7 de febrero y 3 de marzo, y óptico del 3 de abril de 2025. El margen aporta aproximadamente 8.252 km² fuera de la zona de referencia y cabe completo en los previews existentes.

El visor muestra referencia amarilla y búsqueda azul; se puede delimitar y guardar un área en el margen. Los centros de candidatos en el margen se registran como `search_margin` y se muestran como «Fuera de la zona de referencia». Su ubicación no acredita actividad de fondeo. La observabilidad ampliada sigue sin medirse; el porcentaje visible se refiere expresamente a la zona original.

| Evidencia | Identidad |
|---|---|
| Manifest derivado | [queue.json](queue.json) |
| SHA256 del archivo | `6a25a26d3a4b965a30d661767c3a24330c16e7a60bd07114c4d628b8e1514838` |
| Cola padre | `train-bb18008ec1d937776f35` |
| SHA256 del padre | `35c92bca87c79d64833edde6db1870f0b00fd2cc54a0177857e150a84a33d5bc` |
| SHA256 de propuesta | `a3bafdf405d49047eecb20abb6c942e4c32597082af2da54afed74971efdef25` |

La [propuesta](../../coverage/anchorage-search-margins/README.md) permanece como fuente inmutable; la activación afecta únicamente al ámbito de esta cola. El constructor fija también su propio SHA. No cambia la configuración V1 del catálogo ni el detector y no define un límite oficial de fondeo. No amplía todavía las entradas del Caribe. Es TRAIN previamente revisado, no una muestra nueva ni evidencia de precisión ≥90%.

Los manifests original y derivado se conservaron en el archivo local por ID antes de sustituir atómicamente el manifest activo. Los GET y POST siguen resolviendo la cola original para las pestañas abiertas. No se recargó la pestaña de revisión del usuario. Sus doce recibos previos, 35 manifests científicos congelados y las cantidades del catálogo permanecieron iguales: 5,026 observaciones, 4,430 detecciones, 623 revisiones, 11 modelos y 123,001 activos.

Verificación: 490 pruebas Python sobre el paquete instalado y 13 pruebas JavaScript, incluyendo geometría, inmutabilidad, pertenencia de colas y compatibilidad con revisiones abiertas; inspección del contorno en navegador; dibujo, distintivo exterior y guardado de recibo en un catálogo temporal con imagen sintética. No se guardaron hallazgos de prueba en el catálogo real. El diagnóstico operativo pasa; la liberación científica sigue pendiente por falta de validación local independiente. [Contrato y operación](../../../docs/review-queue.md).
