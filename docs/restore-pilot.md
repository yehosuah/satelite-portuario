# Restaurar el piloto publicado

La [versión v0.1.0](https://github.com/yehosuah/satelite-portuario/releases/tag/v0.1.0) incluye un snapshot experimental de los tres puertos. Contiene catálogo, 22 observaciones de zonas, evidencia numérica y cuatro detectores: dos públicos y dos derivados de pruebas supervisadas con muestras públicas. No incluye el histórico completo ni modelos locales validados.

El snapshot ocupa 836,751,228 bytes comprimidos y se distribuye en 34 partes de hasta 25 MB para permitir reintentos de transferencia. La extracción requiere aproximadamente 513 MiB de RAM. El contenido ocupa unos 1.69 GB por copia; considera también la copia restaurada y la reserva de disco configurada para el trabajador.

En una carpeta nueva, descarga `chunk000` a `chunk033`, `CHUNKS.sha256` y `BUNDLE.sha256` desde la versión. Conserva los nombres originales y ejecuta:

```sh
shasum -a 256 -c CHUNKS.sha256
cat satelite-portuario-pilot-v0.1.0.tar.xz.chunk[0-9][0-9][0-9] > satelite-portuario-pilot-v0.1.0.tar.xz
shasum -a 256 -c BUNDLE.sha256
tar -xJf satelite-portuario-pilot-v0.1.0.tar.xz
```

En Linux también puedes usar `sha256sum -c`. Continúa solo si todos los hashes coinciden. El SHA256 del archivo reconstruido es `e73e129c94082546594ecfb6bb5c02354d416d36e027765bfda7ba97571f2871`. La compresión conserva exactamente el tar original; se comprobaron sus 270 archivos de snapshot y el manifiesto, los avisos y las licencias acompañantes. [Informe de empaquetado](../reports/pilot/package-validation.json).

Con el paquete instalado según [instalación](installation.md):

```sh
puertogt restore pilot-bundle data-restored
puertogt --data-dir data-restored doctor
puertogt --data-dir data-restored serve
```

La restauración exige un destino nuevo y vuelve a verificar hashes y referencias. Abre `http://127.0.0.1:8000/`. Para guardar revisiones, configura una credencial local `PUERTOGT_REVIEW_TOKEN` antes de iniciar el servicio. El snapshot no contiene credenciales.

Si usas `uv` desde el repositorio, antepone `uv run --no-sync` a los comandos y ajusta las rutas al directorio donde extrajiste los archivos. La instalación base basta para consultar y revisar; volver a inferir requiere el extra ML. La prueba de restauración ya ejecutó ambos detectores usando únicamente los archivos restaurados.

El snapshot es una copia congelada de la ejecución inicial. Una corrección posterior del worker elimina falsos estados parciales debidos a la rasterización del borde: el recálculo cambiaría cuatro de los 22 estados, con un ajuste máximo de 0.502 puntos porcentuales de observabilidad. La restauración conserva los valores originales; no los reescribe ni modifica detecciones. Consulta el [informe de impacto](../reports/pilot/coverage-correction.json) antes de comparar resultados reprocesados.

Las geometrías son provisionales y los resultados son propuestas. Los ceros no certifican ausencia de buques, la observabilidad no mide precisión y las dimensiones sin referencia permanecen nulas. Sigue el [protocolo de etiquetas](labeling-protocol.md) antes de construir conjuntos de entrenamiento, calibración y prueba.
