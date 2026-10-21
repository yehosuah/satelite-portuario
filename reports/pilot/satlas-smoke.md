# Comprobación del adaptador real Satlas para cambios

El adaptador existente `create_satlas_change_model` cargó correctamente el encoder público **Sentinel2_SwinT_SI_MS** y ejecutó el decoder de cambios. No fue necesario modificar `ml/change.py`. Esta comprobación no entrena el modelo ni demuestra capacidad para identificar cambios físicos.

Se descargó el checkpoint completo de 225,851,364 bytes dentro del límite aprobado de 250,000,000. Su SHA256 es `ea15889fd2feb8723def74b6a650d406296940d3b71cfca0ea50933a95bf2a11`; la revisión de Hugging Face es `b1f8de04dacf9f9ed9640fe32484947710b47ede`. La descarga tardó 494.33 s. La procedencia queda en `config/models/change.yaml` y [satlas-download.json](satlas-download.json).

Se usó un recorte numérico de 256×256 píxeles de Santo Tomás de Castilla, Sentinel-2 L1C del 30 de septiembre de 2026. Las dos ramas recibieron la **misma adquisición**, exclusivamente para comprobar el cálculo. El encoder produjo escalas de 96, 192, 384 y 768 canales con dimensiones espaciales 64, 32, 16 y 8. La salida del decoder fue `[1,1,256,256]`; todas las salidas fueron finitas.

Con PyTorch 2.14.1, CPU y cuatro hilos, cargar el modelo tardó 0.903 s, ejecutar el encoder 0.033 s y ambas ramas con decoder 0.063 s. El pico RSS fue 779,911,168 bytes. Una pérdida enmascarada con etiquetas **sintéticas** dio 0.721308 y su backward tardó 0.028 s: el encoder congelado no recibió gradientes y los del decoder fueron finitos. No se ejecutó ningún paso de optimización ni se exportaron pesos de la cabeza aleatoria. Los números completos y el hash del bundle están en [satlas-smoke.json](satlas-smoke.json).

La receta local usa RGB de B04/B03/B02 armonizadas dividido por 3000, más seis bandas divididas por 8160. Satlas documenta TCI/255 para sus primeros tres canales. Esta adaptación está explícita en el manifiesto y **no se presenta como reproducción radiométrica exacta del preentrenamiento**. Requiere ajuste y evaluación independientes antes de publicar resultados. Véase la [normalización oficial fijada](https://github.com/allenai/satlas/blob/a81422740e68447f732718d6d48ed0ce97671c57/Normalization.md).

`weights_path` y `weights_sha256` del modelo completo permanecen nulos, `training_state` exige pares revisados y la publicación automática sigue desactivada. Los pesos descargados corresponden únicamente al encoder público. El [README oficial del paquete fijado](https://github.com/allenai/satlaspretrain_models/blob/7b5cd45adc3cad70b3834d65956974af6f6bffd0/README.md) etiqueta los checkpoints ODC-BY, mientras la ficha general de Hugging Face declara Apache-2.0; se conservaron ambas referencias sin cambiar la licencia atribuida a los pesos.

Para repetir, disponer primero del bundle óptico del piloto e instalar `uv sync --frozen --no-editable --extra ml`. Luego ejecutar:

```sh
uv run --no-sync python scripts/pilot_satlas.py --download
```

El comando verifica el SHA256 y reutiliza el checkpoint local cuando está presente. Genera únicamente un informe, sin eventos ni pesos entrenados.
