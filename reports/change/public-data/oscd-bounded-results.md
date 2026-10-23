# Cambios físicos: experimento público OSCD

El flujo de importación, entrenamiento de decoder propio y evaluación independiente se ejecutó con datos reales. **El modelo falló la prueba pública: TP 0, FP 0 y FN 9,749 píxeles; F1 e IoU 0, recall 0 y precisión indefinida.** No se registró ni desplegó como modelo válido. El conjunto TEST quedó consumido y no se ajustaron receta ni umbral a partir de sus resultados.

La fuente fue el [sitio de los autores de OSCD](https://rcdaudt.github.io/oscd/) y su archivo oficial, con pares Sentinel-2 anteriores a 2022 y máscaras manuales. La licencia tiene una contradicción conservada: el [registro DOI](https://doi.org/10.21227/asqe-7s69) declara CC BY 4.0, mientras el README dentro del ZIP especifica **CC BY-NC-SA y consulta a autores para uso comercial**. Se aplica aquí la restricción del archivo: investigación local, sin redistribuir máscaras ni pesos ni autorizar uso comercial. Las dos evidencias primarias están en esta carpeta.

Se fijaron cuatro ciudades TRAIN y dos TEST por menor tamaño de sus nueve bandas rectificadas, respetando el split oficial antes de abrir las máscaras seleccionadas o inferir. Bercy y Bordeaux se excluyeron por insuficiente evidencia de alineación según el control prefijado; no se relajó el umbral ni se eligieron reemplazos por resultados. Quedaron París y Rennes para TRAIN (10 cores: 9 positivos, 1 negativo) y Norcia y Rio para TEST (6 cores positivos). Los grupos de ciudad y adquisición, y la no intersección de AOI entre splits, se verificaron. Este es un subconjunto de factibilidad condicionado por disponibilidad/alineación, **no el benchmark OSCD completo**.

Los TIFF rectificados y las máscaras oficiales no contienen CRS ni transformación afín. Se conservaron coordenadas de imagen; no se reconstruyeron ubicaciones con la AOI aproximada. Por tanto, esta evaluación mide segmentación por píxel, no exactitud geográfica de polígonos portuarios. Los TIFF usan etiquetas 1/2 pese a que el README dice 0/1: se verificó la correspondencia exacta con el PNG oficial 0/255 antes de convertir. Los halos de 32 px permanecen como contexto y se excluyen de la pérdida/métricas; cores de 256 px no se solapan.

La receta se fijó en el protocolo v3, antes de entrenar: Satlas SwinT congelado y verificado por SHA256, decoder propio de 32 canales, AdamW 1e-4, tres épocas, seed 20261022, umbral 0.5 y filtro de área nominal 100 m². El peso positivo 20 de BCE se calculó solo desde TRAIN (5,326 positivos y 318,945 negativos válidos); no se calibró con TEST. Fueron 30 updates reales y seis forwards de prueba, con pesos exportados antes de la primera inferencia de prueba. Duración total 4.95 s con 4 hilos CPU, pico 836 MB; las fuentes exactas quedaron archivadas con el experimento.

| Estrato público | TP | FP | FN | IoU | Recall |
|---|---:|---:|---:|---:|---:|
| Norcia | 0 | 0 | 1,227 | 0 | 0 |
| Rio | 0 | 0 | 8,522 | 0 | 0 |

No hubo cores TEST totalmente negativos, por lo que no se estima una tasa de falsas alarmas por par negativo. Las máscaras de soporte cubren datos numéricos y tierra según NDWI prefijado; **no constituyen validación independiente de nubes**. El control de alineación mide desplazamiento entero de parches de gradiente estables y no certifica precisión subpíxel. Dos fechas no prueban persistencia temporal.

Se inspeccionaron únicamente tripletas locales TRAIN de patios: PRQ 18 de enero/4 de marzo/2 de junio de 2025; PBR 18 de enero/18 de mayo/17 de junio de 2025; STC 18 de enero/3 de abril/2 de junio de 2025. No se identificó construcción/demolición persistente inequívoca a 10 m. Reordenamientos de contenedores, barcos, sombras o cambios de tono no se convirtieron en positivos. No se crearon etiquetas locales ni se modificó el catálogo.

Artefactos locales: `data/experiments/oscd-bounded-v1-20261023`, con manifiesto, pesos, fuentes fijadas, evaluación, recibo previo a TEST y disposición fallida. SHA256 de pesos: `acab5ad44a51b9ab2b78bb0290dfc27322291a9e038f4a88b47ebfb3dcbcd24f`. El recibo JSON contiguo contiene hashes completos, exclusiones, recursos y procedencia. La importación recuperó 73.3 MB conocidos, incluidos intentos/metadatos, dentro de 750 MiB; repetir la preparación desde las fuentes fijadas reprodujo exactamente ambos manifiestos y hashes numéricos.

Para reproducir preparación y ejecución desde la raíz del repositorio:

```sh
PYTHONPATH=src .venv/bin/python scripts/import_oscd.py \
  --protocol reports/change/public-data/oscd-bounded-protocol-v2.json \
  --destination tmp/oscd-replay
PYTHONPATH=src .venv/bin/python scripts/train_public_change.py \
  --dataset tmp/oscd-replay \
  --protocol reports/change/public-data/oscd-bounded-protocol-v3.json \
  --output data/experiments/oscd-replay-new-id
```

El segundo comando requiere coordinar la pausa del histórico y toma su lock pesado; nunca se ejecuta automáticamente desde HTTP. Reproducir el experimento no convierte el TEST consumido en evidencia nueva. El siguiente avance de aprendizaje requiere un protocolo nuevo basado en TRAIN/validación interna, mayor soporte y una reserva independiente todavía no utilizada; también resolver la licencia y obtener referencias portuarias de cambios persistentes antes de cualquier uso local validado.
