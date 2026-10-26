# Conectar Global Fishing Watch para corroborar visitas

Este conector consulta eventos de **visita portuaria** en los tres puertos, dentro de enero–junio de 2025 (TRAIN). Requiere una cuenta verificada y una credencial guardada localmente. La existencia del archivo no prueba que la cuenta tenga permiso para el dataset; `status` no hace red.

Una visita puede ayudar a contrastar presencia o identidad durante un intervalo. No proporciona por sí sola la posición instantánea a la hora de Sentinel, una caja verdadera del barco, eslora/manga verificadas ni un inventario completo. **Ausencia de eventos AIS no significa ausencia de buques.** La correspondencia entre visita y objeto observado necesita revisión independiente. Este paso no acredita precisión ≥90% ni recall ≥80%, no crea etiquetas y no modifica TRAIN, CAL o TEST.

## Credencial local

La autenticación oficial usa un token personal en el encabezado `Authorization: Bearer`. La cuenta debe estar registrada y verificada; los tokens se administran en el [portal de GFW](https://globalfishingwatch.org/our-apis/tokens). Consulta la [documentación de autenticación](https://globalfishingwatch.org/our-apis/documentation/docs/authentication).

Desde la raíz del repositorio:

```sh
PYTHONPATH=src .venv/bin/python scripts/gfw_reference.py status
```

Para configurar una credencial que todavía no exista, ejecuta en una terminal interactiva:

```sh
PYTHONPATH=src .venv/bin/python scripts/gfw_reference.py configure
```

El comando solicita el token con `getpass`, sin eco. No lo pegues en el chat, un argumento de shell o `.env`. Se conserva exclusivamente en `data/secrets/gfw-token`, con permisos `600`, bajo `data/`, que Git ignora. Una credencial existente se conserva; sustituirla requiere `configure --replace`. No hace falta volver a configurar la credencial ya guardada.

`status` devuelve `configured_not_verified` si el archivo es válido localmente. Sin token, tanto `status` como `fetch` terminan con código 2; `fetch` se detiene antes de crear un cliente HTTP.

## Congelar y ejecutar una consulta acotada

Primero crea el plan sin red:

```sh
PYTHONPATH=src .venv/bin/python scripts/gfw_reference.py plan
```

El resultado devuelve `path` y `sha256`. Conserva ambos. El plan se crea de forma exclusiva en `data/references/gfw/plans/`; no se sobrescribe. Fija tres polígonos envolventes calculados desde las zonas V1, la versión y hash del archivo fuente, fechas, dataset y presupuestos. Los envolventes son filtros de búsqueda; pueden incluir tierra, espacio fuera de la zona exacta y áreas compartidas entre puertos. No resuelven por sí mismos la atribución portuaria de un evento.

El intervalo predeterminado es `2025-01-01` inclusive a `2025-07-01` excluyente. `--start` y `--end-exclusive` permiten un subconjunto de ese TRAIN; CAL/TEST se rechazan. `--page-size`, `--max-pages`, `--max-events`, `--max-total-bytes` y `--max-seconds` solo pueden reducir los techos predeterminados. No se leen el catálogo científico ni sus etiquetas para construir el plan.

Cuando el plan haya sido verificado, ejecuta explícitamente, sustituyendo los dos valores por la salida anterior:

```sh
PYTHONPATH=src .venv/bin/python scripts/gfw_reference.py fetch \
  --plan data/references/gfw/plans/HASH_DEL_PLAN.json \
  --sha256 HASH_DEL_PLAN
```

La consulta usa únicamente `POST https://gateway.api.globalfishingwatch.org/v3/events`, el dataset fijado `public-global-port-visits-events:v5.0`, `startDate`, `endDate` y `geometry` de tipo `Polygon`. `offset` y `limit` son parámetros de paginación. Contrato consultado el 26 de octubre de 2026: [ejemplo oficial de visitas con polígono](https://globalfishingwatch.org/our-apis/documentation/docs/examples/events/post-example4), [especificación Events](https://globalfishingwatch.org/our-apis/documentation/docs/v3/events/get-all-events) y [versiones de datasets](https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/key-concepts).

## Límites y recibos

El presupuesto predeterminado es global para los tres puertos: hasta 30 páginas de 200 eventos, techo de 10.000 eventos, 1 MiB por página, 20 MiB acumulados y 120 segundos, con timeout de solicitud de 20 segundos. Rige el primer límite alcanzado; 30 páginas de 200 admiten como máximo 6.000 entradas. El conector acepta respuestas `200` y `201` y deshabilita redirecciones, proxies de entorno y reintentos automáticos. Una respuesta `401` pide verificar la credencial; `403` indica falta de permiso y `429` detiene la consulta por cuota.

Cada ejecución crea un directorio nuevo bajo `data/references/gfw/runs/`, con copia del plan, páginas JSON y recibo con hashes, ámbito, bytes, páginas y puertos completados. El código verifica avance de offsets, coherencia del total, tipo de evento y duplicados dentro de cada puerto. Los eventos repetidos entre polígonos portuarios no se cuentan automáticamente como visitas distintas. Las respuestas que reflejen la credencial se descartan; los errores de transporte y los cuerpos de error del servidor no se imprimen ni guardan.

`complete` significa que se agotó la paginación de las tres consultas. Un límite o error produce `stopped`, código de salida 2 y cobertura parcial explícita; no equivale a ausencia. Si el dataset o el contrato remoto cambian, se conserva el intento fallido y se prepara una nueva versión, sin ampliar silenciosamente el plan. Los recibos contienen datos externos sin adjudicar: no se importan al catálogo ni se publican automáticamente. Conserva las condiciones de uso y atribución de GFW al compartir resultados.

Las pruebas usan `httpx.MockTransport` y no acceden a la API real:

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_gfw_reference.py
```

## Caso observado: solapamiento temporal y puerto vecino

La [auditoría de la primera descarga TRAIN](../reports/local-review/scientific-closure-20261026/gfw-corroboration.md) comprobó 2.402 entradas y 1.504 eventos únicos. El envolvente STC incluyó los 898 eventos de PBR: el punto representativo dentro de V1 y el ID del anclaje intermedio atribuyeron solo 65 eventos a STC. El nombre del polígono de consulta no determina el puerto del evento.

La selección `OVERLAP` admite eventos cuyo intervalo atraviesa la ventana: 77 empezaron antes de enero de 2025 o terminaron después de junio, y el más largo duró 3.975 días. La coincidencia STC muelles del 18 de mayo de 2025 correspondía a un intervalo de 820 días, confianza 2. Antes de usar una visita como corroboración, hay que contrastar duración, confianza, posición representativa e identidad de los anclajes inicial, intermedio y final. El recibo conserva la consulta y su cobertura; no certifica esas correspondencias.

Estas posiciones agregadas no aportan muestras AIS instantáneas a la hora de captura. Un intervalo largo que contiene esa hora no prueba que el barco estuviera entonces en su punto representativo. Los eventos sirven para orientar una investigación; no se convierten automáticamente en verdad, cajas de referencia o negativos por ausencia.
