# Continuidad local del histórico: corte v0.2.0

Este complemento conserva el histórico en una frontera sin trabajos activos: 645 adquisiciones completas, 173 en `retry` y seis excepciones de contexto radar. La comprobación realizada es estructural. No se arrancó un worker ni se ejecutó inferencia desde la restauración.

## Contenido y verificación

`manifest.json` enumera el SHA256 y tamaño de los 61,363 archivos de datos: 8,189 del histórico, dos pesos de OmniCloudMask, 11,327 archivos de fase 2 congelados y 41,845 de atributos públicos. Los datos suman 6,122,525,395 bytes. `path-relocations.json` conserva 1,245 referencias numéricas de 91 bundles históricos y el destino relativo de cada una. `CONTROL.sha256` fija los manifiestos y esta política.

El histórico contiene su plan, prioridades, límites, registros de recuperación, logs, reservas de descarga, cachés y evidencias retenidas. `progress.sqlite` se creó con `sqlite3.Connection.backup`, leyendo el origen en modo de solo lectura. Se verificaron integridad, referencias y equivalencia de todas las filas del ledger y de reservas antes/después de copiar. El snapshot empaquetado usa `journal_mode=DELETE`; solo la copia operativa podrá volver a WAL al inicializarse. No se copiaron WAL/SHM, locks, el marcador `PAUSE` ni archivos de identidad de procesos activos; las exclusiones exactas están en el manifiesto. Los PID que aparezcan en logs o recibos históricos son datos de auditoría, no instrucciones para controlar procesos nuevos.

Los dos pesos de calidad no estaban registrados en el bundle del catálogo. Se añadieron desde los archivos públicos ya existentes, con hashes comprobados, para evitar una descarga implícita al retomar Sentinel-2. No se cargaron modelos para esta comprobación.

También contiene, sin alterar sus originales, los siguientes directorios congelados relativos al volumen de datos:

- `datasets/phase2-reviews/s2-january-february-v1`.
- `datasets/phase2-reviews/s2-calibration-reviewed-v1`.
- `datasets/phase2-reviews/s2-test-round1-v1`.
- `experiments/phase2-s2-train-temporal-v1-20261022`.
- `experiments/phase2-s2-candidate-calibration-v1-20261023`.

El manifiesto CAL se conserva con SHA256 `e7a5cac71247291d5e66eb889394deb69cf6c5196fee4ec116b361861c018729` y el freeze del experimento CAL con `2b8763ec25fb5f69c80ee5684e8639b6cc29c47289f900dda2fa302aabf24bc8`. Se comprobaron bytes, sin ejecutar inferencias, entrenamientos ni evaluaciones sobre estos conjuntos.

También conserva `public-attributes/v1` después del cierre de su experimento: 48 muestras materializadas, selección y elegibilidad fijadas, caché de features, fuentes, evaluación y modelo portable. El ledger se copió mediante `SQLite.backup` y conserva 48 estados completos; el catálogo portable, manifiestos y pesos del experimento se copiaron byte por byte. Se excluyeron seis archivos de proceso, lock y sidecars SQLite. La selección, el informe numérico `fe1af64b9c731443e3567b91f13e01622db71b16ad2a284a34687088fbc31140`, el manifiesto portable `e94a64a51ab6f96e0b5e612631e1f7255cef6ed7af5da9f8aeb1f67c60ee7065` y ambos pesos coinciden con el recibo final. Las dos muestras TRAIN inelegibles permanecen conservadas y el modelo mantiene su política experimental; no se repitió TEST.

No contiene credenciales, `.env`, materiales OSCD ni otros datasets/experimentos. Esas exclusiones no se presentan como una copia de todo el workspace científico. El catálogo y los activos publicados se entregan en el directorio hermano `catalog-bundle`.

## Restauración manual en otro volumen

1. Conserva esta copia como archivo inmutable. Comprueba `CONTROL.sha256` y todos los archivos de `manifest.json` antes de copiar.
2. Restaura `catalog-bundle` con `puertogt restore` hacia un directorio vacío. Usa el código y la configuración del mismo corte.
3. Copia `history/full-v1` dentro de ese nuevo volumen de datos, sin sobrescribir una campaña existente.
4. Copia `models/omnicloudmask` al mismo volumen. Verifica de nuevo los hashes de sus dos pesos.
5. Copia los directorios científicos enumerados y `public-attributes/v1` al volumen nuevo solo si necesitas conservar ese trabajo, sin mezclar versiones existentes. Sus manifiestos y recibos permanecen inmutables; los experimentos no se reejecutan automáticamente.
6. Crea un marcador `PAUSE` nuevo en la copia operativa mientras compruebas rutas y dependencias. Los locks se crean en el host de destino; no se transporta propiedad de procesos.
7. Reubica las referencias de la copia operativa con el mapa descrito abajo. No edites los originales del paquete.
8. Comprueba integridad SQLite, estados 645/173/6, 824 filas, plan y presupuestos. Confirma pesos de detección, calidad y dependencias instaladas.
9. Solo con ambos locks libres y sin otro entrenamiento o importador activo, retira la pausa operativa y ejecuta el runner con los mismos límites y prioridades. Registra esa nueva ejecución por separado.

El plan esperado es `history-full-v1-3e4733ed6e0e7481`; SHA256 de `plan.json`: `28095b6d61477bff3147ac5ec3a72d9397f97b1c117beab480c78113ae3a56ae`. El campo documental `source_database` conserva la procedencia original. No se modifica el plan para adaptar una ruta local.

## Reubicación de bundles

Los 91 JSON conservan sus rutas originales para no alterar evidencia silenciosamente. Para cada entrada de `path-relocations.json`, abre el JSON indicado por `bundle` dentro del volumen restaurado. Sustituye exclusivamente `bundle[section][name]` por la ruta absoluta formada por el nuevo volumen y `portable_path`. Antes de hacerlo, verifica el archivo numérico contra el SHA256 de la entrada. No cambies matrices, máscaras, transformaciones ni `history_file_hashes`.

Al guardar cada JSON derivado, crea un recibo de reubicación con ruta, hash original del manifiesto, hash nuevo, prefijo original, nuevo volumen y fecha. Conserva el mapa y los JSON originales. Esto permite distinguir una adaptación de rutas de un cambio de contenido numérico. Los tiles preparados se contrastaron con hashes anteriores al empaquetado; los mosaicos de zona se contrastaron por igualdad de bytes entre origen y copia y se fijaron con un hash nuevo. El mapa declara esa diferencia y no atribuye a los mosaicos un hash previo que no tenían.

El comando de continuación debe usar el checkout correspondiente y los argumentos guardados en `limits.json`; este corte emplea cuatro hilos CPU, cuatro trabajadores I/O y transporte reutilizable. No se debe volver a crear el plan ni resetear intentos o bytes reservados. Las seis excepciones permanecen como ausencia de contexto disponible, no como ausencia de buques.

El mapa cubre los bundles históricos, no transforma los archivos científicos congelados ni los contextos públicos de atributos. El modelo de atributos en `public-attributes/v1/experiment-v1/portable` sí incluye head y encoder con referencias relativas y hashes comprobados. Los manifiestos de modelos y freezes pueden conservar rutas absolutas del equipo original. Para usarlos en otro equipo se necesita resolverlas en una copia operativa, conservar el hash original y registrar la adaptación; no se alteran los recibos del experimento ni se presenta una repetición de TEST como evidencia nueva. El replay de los experimentos no forma parte de esta verificación.

## Alcance comprobado

Se verificaron archivos, hashes, snapshot SQLite, coincidencia del plan y todas las referencias numéricas del histórico. El catálogo se restauró por separado y se comprobaron consultas y activos de la API. No se verificó un reinicio del histórico, inferencia sin red, disponibilidad futura de proveedores ni exactitud científica. Una instalación totalmente sin red requiere además las dependencias de ejecución del mismo corte.
