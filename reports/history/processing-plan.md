# Histórico reanudable de 2025–2026

Plan creado el 21 de octubre de 2026: `history-full-v1-3e4733ed6e0e7481`.
El catálogo contiene 828 adquisiciones objetivo y 35 contextos radar de 2024.
Las cuatro capturas ya representadas en las 22 observaciones del piloto se
excluyen completas: quedan **824 adquisiciones nuevas**, 4.973 pares
adquisición/zona y 4.907 teselas únicas. El plan y el progreso se guardan en
`data/history/full-v1/plan.json` y `progress.sqlite`, respectivamente.

## Cobertura y procedencia

La cuadrícula UTM tiene resolución de 10 m, teselas de 512 × 512 píxeles,
núcleos de 448 × 448 y margen de contexto de 32 píxeles por lado. Una tesela
se comparte entre todas las zonas que toca; los mosaicos usan exclusivamente
núcleos sin solapamiento. Esto cubre polígonos completos, incluido el canal de
Santo Tomás de unos 10,5 km. La cobertura de una escena y las nubes pueden
reducir la fracción observable: completar una tesela nunca implica cielo
despejado ni presencia de datos fuera de la huella del satélite.

S2 usa las nueve bandas numéricas L1C del bucket público GCS, fijadas por
generación, con armonización DN y vecino más cercano de la receta vigente.
S1 usa VV/VH originales GRDH de AWS público y un contexto de la misma órbita
relativa/dirección entre 30 y 90 días antes; conserva el warp GCP TPS y las dos
interpolaciones cúbicas del piloto. No se sustituyen por RTC, sigma0 ni RGB.
OmniCloudMask genera máscaras separadas de nube/sombra; la revisión local de
estas máscaras sigue pendiente. Los dos detectores conservan sus pesos
públicos y una identidad nueva `history-full-v1`, siempre **experimental**.

Se verificó que seis capturas S1D carecen de contexto compatible; la búsqueda
independiente en CDSE y ASF confirma el hueco. Quedarán con excepción
específica, nunca como cero barcos. Véase
[investigación de contexto](missing-context-investigation.json). Las otras
818 son candidatas para ejecutar; disponibilidad de catálogo no equivale a
descarga o inferencia completada.

## Mediciones reales iniciales

Las tres fechas se eligieron cronológicamente, antes de consultar scores.

| Adquisición | Región | Teselas | Datos descargados | Peticiones de rango | Materialización | Resultado inicial |
|---|---|---:|---:|---:|---:|---|
| S2A 2025-01-03, T15PYR | Quetzal | 6 | 32,16 MB | 541 | 233,44 s, I/O serial sin calidad | 4 observaciones parciales; 14 propuestas experimentales en fondeo |
| S1A 2025-01-01, 11:45:54 | Caribe | 5 | 7,78 MB | 12 | 21,94 s, I/O serial | 3 zonas intersectadas, todas parciales; 0 propuestas sobre su fracción observable |
| S2A 2025-01-03, T16PCC | Caribe | 6 | 29,21 MB | 286 | 47,15 s, I/O4 con calidad | 8 zonas no observables por la máscara; conteos NULL |

S2 Quetzal necesitó otros 16,61 s para calidad/inferencia/publicación desde
los datos cacheados; S1, 8,10 s. La segunda S2 completó descarga, calidad,
inferencia y publicación en 61,52 s. Los casos no son idénticos y esta tabla
no pretende aislar una mejora causal exacta de cuatro hilos. La reserva
contabilizada al concluir estos prototipos fue 69.287.936 bytes, incluidos
128 KiB de prueba de conexión. Los metadatos HTTP pequeños se limitan aparte
a 4 MiB por respuesta; no consumen unidades de procesamiento de proveedor.

HTTP persistente apenas cambió una prueba de cuatro rangos (1,374 s frente
a 1,464 s). Sobre los rangos reales de Quetzal, bloques de 256 KiB requerirían
585 bloques/146,25 MiB y bloques de 1 MiB 339/339 MiB, frente a 541 rangos y
30,67 MiB actuales. Por eso se eligió concurrencia acotada de bandas, con
máximo cuatro lectores y un solo modelo pesado. No se incrementa la lectura
con bloques especulativos.

La extrapolación inicial es de **20–60 GB de transferencia y aproximadamente
8–20 horas**, con incertidumbre alta por latencia, cobertura, órbitas,
reintentos y disponibilidad pública. No es una promesa de terminación ni una
medida de precisión. El ledger permitirá reemplazarla por tasas observadas
de más adquisiciones. No se ha alcanzado ni validado una precisión del 90 %.

## Límites y recuperación

Se conserva una reserva de disco de 5 GiB. La campaña tiene presupuesto de
200 GiB de transferencia, 1 GiB por adquisición y 100 GiB de almacenamiento
local. Al medir los primeros prototipos quedaban aproximadamente 325 GiB
libres. Cada rango reserva bytes en SQLite antes de acceder a la red; un
reintento vuelve a consumir reserva. Cachés y archivos se verifican con
SHA256; AWS fija además ETag con If-Match y GCS fija generación.

Hay un lock de campaña y el mismo lock pesado que usa el worker normal.
Una interrupción conserva bandas, teselas y predicciones verificadas; los
jobs publicados se reconocen por adquisición/zona/receta y no se duplican.
La reclamación del worker se restringe a la adquisición actual, además de
la receta. Se respetan leases activos y expiración; no se roba trabajo.
Los fallos transitorios tienen backoff y tres intentos; límites de recursos
pausan el trabajo sin convertirlo en un resultado científico. Las causas se
guardan por adquisición junto a sus zonas en el ledger.

La publicación usa las transacciones y activos atómicos existentes. Las
22 observaciones protegidas se excluyen en el plan y se rechazan de nuevo
al publicar cualquier captura protegida. El snapshot distribuido, sus
archivos y los datasets de revisión congelados no son salidas de este flujo.
Tras publicar una adquisición, se eliminan solamente sus cachés temporales;
la evidencia numérica, máscara, vista y procedencia permanecen en el Store.

### Teselas completas para evaluación reproducible

Antes de la inferencia y de limpiar cachés se registra una única copia
compartida de **todas** las teselas de cada adquisición, con sus 512 píxeles,
márgenes y máscaras. Se guarda en `assets/history/<hash-adquisición>/`, dentro
del volumen de datos, y forma parte del inventario normal de assets de SQLite.
El manifiesto `inference.json` fija las versiones de las fuentes, los
descriptores y orden completos del plan, los hashes de cada archivo, el pase
satelital y el contexto radar. También se incluyen las teselas de zonas que
no pertenezcan al subconjunto revisado: la deduplicación de centros a 100 m
opera sobre toda la adquisición.

`freeze_history_evidence(store, destination, samples)` copia esa evidencia
a `history/` dentro del nuevo dataset y devuelve un manifiesto
`history-tiles-v1`, con rutas relativas. Valida la integridad antes de copiar;
si falta una adquisición, tesela, banda, máscara o hash, falla. No descarga
sustitutos, no omite muestras y no aproxima márgenes a partir del mosaico.
Un reintento del procesador tampoco puede inferir con bytes distintos de
los ya registrados para esa adquisición.

La primera versión de la limpieza había conservado solo mosaicos de núcleos
para algunas fechas ya terminadas. Los tres prototipos conservaron sus
teselas originales. Para las prioridades de train/calibration/test afectadas
se usa una reparación separada desde `sources.json`, con generación GCS o
ETag AWS fijados. Se exige igualdad **exacta de cada píxel** de todas las
bandas y máscaras de los mosaicos publicados. Una discrepancia queda como
error y nunca reemplaza los archivos publicados. La procedencia distingue
`retained_original_prepared_bytes` de `reconstructed_from_pinned_sources`;
el segundo caso declara `original_halo_hash_available=false`: verificar los
núcleos no demuestra identidad con antiguos márgenes que no se conservaron.
La reparación no modifica observaciones, detecciones ni revisiones. Sus
resultados y errores quedan en `evidence-repair.json`.

Los nuevos archivos compartidos cuentan dentro del mismo límite de 100 GiB,
además de respetar la reserva de 5 GiB. A las 15:31 UTC del 21 de octubre,
las 16 prioridades ya terminadas tenían evidencia completa: tres originales
y trece reconstrucciones verificadas. Ocupaban 180,72 MB en 1.106 archivos
registrados; la reparación consumió 362,41 MB adicionales de transferencia.
Esta medición temprana no sustituye el control de volumen en cada escritura.
Tres adquisiciones ya terminadas que no estaban en las prioridades conservan
sus mosaicos pero todavía carecen de halos; el exportador las rechaza hasta
que se reparen explícitamente. Las nuevas adquisiciones retienen todas sus
teselas desde el principio.

La auditoría `data/history/full-v1/evidence-checkpoint.json` confirmó que
la reparación dejó idénticas las tablas completas de observaciones,
detecciones y revisiones; verificó los 264 archivos de las 22 observaciones
protegidas, los 1.560 assets publicados y los cuatro datasets congelados
anteriores. La campaña se reanudó entonces con 19 adquisiciones completas,
804 pendientes y una en reintento; quedaban 324,23 GiB libres. Estos números
son un checkpoint, no una declaración de que el histórico está terminado.

## Ejecución y supervisión

Desde el repositorio y su entorno con dependencias ML:

```sh
PYTHONPATH=src .venv/bin/python scripts/process_history.py plan
PYTHONPATH=src .venv/bin/python scripts/process_history.py run \
  --max-acquisitions 824 --threads 4 --io-workers 4 \
  --priority-file data/history/full-v1/priorities.json
PYTHONPATH=src .venv/bin/python scripts/process_history.py status
```

Con la campaña pausada, las prioridades ya publicadas que carezcan de halos
pueden repararse de forma explícita, sin ejecutar el detector ni cambiar sus
resultados:

```sh
PYTHONPATH=src .venv/bin/python scripts/process_history.py repair-evidence \
  --threads 4 --io-workers 4 \
  --priority-file data/history/full-v1/priorities.json
```

`priorities.json` puede contener `{"acquisition_keys": ["clave", "otra"]}`;
se vuelve a leer entre adquisiciones y permite priorizar fechas fijadas
para train/calibration/test sin cambiar la selección geométrica del plan.
Las prioridades no deben elegirse por predicciones ni por resultados de
test. `--acquisition` selecciona una clave exacta del plan para diagnóstico.
`--materialize-only --skip-quality` permite I/O sin un modelo pesado.

Crear `data/history/full-v1/PAUSE` hace salir limpiamente al terminar la
adquisición actual. Para continuar, se elimina ese archivo y se relanza el
mismo comando; no se reinicia el histórico. `--resume-paused` requiere que
se haya resuelto o aumentado explícitamente el recurso correspondiente.
`run.log`, `process.json`, `status.json` y el ledger guardan el avance del
proceso continuo. El estado del ledger es la fuente de verdad ante una
interrupción entre la transacción y la escritura de `status.json`.

## Verificación

Pruebas específicas cubren geometría completa, deduplicación de solapamientos,
contexto por órbita/fecha, exclusión del piloto, reserva persistente antes de
I/O, caché corrupta, recuperación, mosaicos sin doble conteo, prioridades y
separación de predicciones entre adquisiciones. La regresión de separación
falló antes del cambio y pasó después: un job anterior de la misma receta
permanece pendiente mientras solo se publican predicciones de la captura
solicitada. Se añaden casos de halos ausentes, checksums alterados, plan
incompleto, zonas adicionales, rutas inseguras, reintentos con bytes distintos
y reconstrucción con un solo píxel cambiado. En conjunto con cola, worker y
el evaluador de fase 2: 40 pruebas pasaron; Ruff limpio.
