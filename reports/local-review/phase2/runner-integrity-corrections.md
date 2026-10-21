# Correcciones de integridad previas a fase 2

El 21 de octubre de 2026 se corrigieron dos comprobaciones del runner antes de entrenar o evaluar.

Tres teselas históricas S1 de distintas adquisiciones contienen exclusivamente el valor declarado como nodata. Sus GeoTIFF idénticos activaban una falsa alarma de fuga entre particiones. El runner ahora comprueba todas las bandas y ventanas del archivo: solo exime del cotejo entre particiones un raster enteramente nodata. Un cero sin nodata declarado, un raster parcialmente válido o una imagen constante con señal siguen sujetos al cotejo. Los SHA de todos los archivos se verifican siempre, incluidas las teselas vacías, y sus exenciones quedan enumeradas en el recibo del experimento. Objetivos, contextos, pasadas y observaciones siguen separados.

Estar listado como reserva en el marco de muestreo tampoco basta para entrar en un experimento. `freeze --execution-order` fija una copia del orden inmutable y solo admite reservas activadas para su puerto, sensor, partición y primaria. Las versiones anteriores del orden permanecen intactas. La activación debe declarar su causa de acceso u observabilidad y que no usó predicciones ni scores.

Las regresiones reproducen la falsa colisión, comprueban que alterar una tesela vacía sigue fallando por checksum y rechazan activaciones de otro puerto o partición. La [comprobación de los conjuntos reales](runner-validation-20261021-update.json) enumera las tres exenciones S1 y confirma el soporte único; no calcula precisión.
