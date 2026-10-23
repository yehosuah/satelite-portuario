# Entrega operativa v0.2.0

El servicio local, el visor, la revisión auditada, los experimentos reproducibles y la continuación del histórico están implementados. **La entrega científica sigue incompleta: precisión local ≥90% no demostrada, atributos sin validar y cambios públicos fallidos.** El histórico conserva seis excepciones radar y adquisiciones por resolver.

## Verificación de software

- 254 pruebas con extras ML; 238 pruebas y 13 omisiones esperadas en instalación base aislada.
- Ruff, sintaxis JavaScript y comprobación de diferencias correctos.
- Wheel 0.2.0 instalada fuera del entorno de desarrollo; HTML, JavaScript y API responden desde el paquete instalado.
- Panel del visor comprobado a 537 píxeles, actualización de estado y consola sin errores. No se afirma comprobación de escritorio.
- `doctor --check-release` conserva código 2 por la evidencia científica pendiente.

[Recibo de software](software-validation.json) · [Instalación wheel](wheel-smoke.json) · [Captura del visor](visor-status.png)

## Restauración y continuidad

El catálogo se exportó y restauró en destinos nuevos: **96.494 archivos con hashes verificados, 3.921 observaciones y aproximadamente 26,56 GB**. Se comprobaron ocho páginas API de observaciones y catorce activos representativos por sensor, puerto y formato. Todas las rutas y bytes del bundle se verificaron por separado.

La copia local está en `dist/release-v0.2.0/catalog-bundle`; su complemento en `dist/release-v0.2.0/continuity` conserva el ledger histórico, cachés, halos, modelos de calidad y datasets/experimentos congelados. El paquete local completo, incluidos atributos públicos, suma 32,70 GB y 157.862 archivos. Los manifiestos enumeran SHA256 y tamaño por archivo. El snapshot histórico preserva el corte **645 completas / 173 retry / seis unavailable**, anterior a reanudar el worker vivo. Las nuevas adquisiciones no se agregan silenciosamente a esa copia.

[Restauración comprobada](restore-validation.json) · [Continuidad estructural](continuity-validation.json) · [Procedimiento de reubicación](continuity-restoration.md)

La continuidad se verificó estructuralmente; **no se ejecutó el histórico desde otro equipo ni se verificó una instalación totalmente sin red**. Las rutas originales de bundles y experimentos necesitan reubicación auditable. Las dependencias Python también deben instalarse. No se incluyen credenciales ni los materiales OSCD restringidos.

## Alcance público

El repositorio publica código, configuración, pruebas y recibos. La entrega v0.2.0 distribuye wheel, fuentes y checksums. **El snapshot actual y los pesos experimentales permanecen locales; no se afirma que sus datos se hayan subido a GitHub.** El bundle público v0.1.0 conserva únicamente el piloto inicial.

Los resultados científicos están en la [calibración óptica](../local-review/phase2/candidate-calibration-v1/README.md), [atributos públicos](../public-attributes/experiment-result-v1.json) y [cambios OSCD](../change/public-data/oscd-bounded-results.md). Ningún resultado habilita publicación automática.
