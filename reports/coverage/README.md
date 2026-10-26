# Cobertura útil de las pasadas

El reporte mide oportunidades de observación en las zonas configuradas. **90% de área observable no significa 90% de precisión del detector.** Las zonas v1 siguen siendo provisionales; no representan necesariamente toda el agua operativa de cada puerto.

## Actualización tras completar pilotos y cinco capturas nuevas

El [corte final del 26 de octubre UTC](pass-coverage-20261026-final.json) reúne
833 adquisiciones objetivo y 5.018 observaciones, conservando las versiones
originales del piloto. Al exigir al menos 90% de área en cada zona, las
oportunidades suficientes combinadas son 289 para Quetzal, 223 para Barrios y
193 para Santo Tomás. Las medianas entre esas oportunidades son 1,67, 2,10 y
2,68 días, respectivamente; no son un pronóstico de revisita.

La [reconstrucción final](mask-unions-20261026-final.json) usó 118 uniones válidas, verificó 1.038.233 bytes y no
reportó errores. Las entradas con observaciones rechazadas o no observables
se excluyen; no heredan cobertura de un resultado guardado anteriormente.
El corte inicial de abajo se conserva como evidencia de la mejora original.

Las [opciones geográficas de 250 y 500 metros](geography-v2-candidates/README.md)
añaden aproximadamente 1,05 o 2,41 km² de agua próxima a muelles entre los tres
puertos. Están inactivas y requieren revisión de alcance antes de procesarse.

## Corte del histórico

El [reporte JSON](pass-coverage-20261025.json) conserva el intervalo UTC `[2025-01-01, 2026-10-21)`, con 828 adquisiciones objetivo y 4.962 observaciones. Las 35 adquisiciones auxiliares de 2024 no son revisitas objetivo. Los reportes fijan las huellas del catálogo consultado, las geometrías y las máscaras usadas. Es un corte histórico: nuevas observaciones o revisiones del catálogo pueden cambiar un recálculo, incluso sobre las mismas fechas.

Para considerar una pasada suficiente exigimos que **cada una de las cuatro zonas de un puerto alcance al menos 90% de observabilidad en esa misma pasada**. Los huecos y sus medianas usan horas reales entre capturas. También se reportan días UTC distintos, la separación entre esos días y los huecos desde los extremos del intervalo.

| Puerto | Pasadas radar suficientes | Pasadas ópticas suficientes | Oportunidades suficientes combinadas | Mediana de separación combinada | Mayor intervalo interior |
|---|---:|---:|---:|---:|---:|
| Quetzal | 194 / 195 | 91 / 157 | 285 / 352 | 1,67 días | 7,51 días |
| Barrios | 113 / 125 | 108 / 307 | 221 / 432 | 2,10 días | 18,00 días |
| Santo Tomás | 115 / 125 | 77 / 307 | 192 / 432 | 2,68 días | 18,00 días |

Combinar oportunidades significa consultar radar y óptico en sus fechas respectivas. No se suman buques observados en fechas diferentes ni se fabrican imágenes de momentos sin captura. La tabla no garantiza esa frecuencia en el futuro ni una distribución regular; las nubes, el contexto radar y el área visible importan.

## Mejora comprobada con los datos existentes

Las 169 adquisiciones radar del Caribe corresponden a **125 pasadas**; 44 adquisiciones son segmentos adicionales de esas mismas pasadas. Contarlas como 169 revisitas exageraría la frecuencia. A la vez, conservar solamente el mejor segmento de cada zona subestima su superficie cubierta.

Se unieron las máscaras de observabilidad de segmentos de **la misma plataforma, órbita absoluta, zona y receta** en su cuadrícula original. Por ejemplo, dos segmentos del 1 de enero de 2025 cubren por separado aproximadamente 30,44% y 69,53% del canal de Santo Tomás. La unión binaria recupera la superficie complementaria sin contar dos veces el solapamiento.

Se verificaron 240 archivos, 1.049.147 bytes, para construir 120 uniones de zona/pasada. El cálculo local tomó 3,13 segundos y no requirió descargas o inferencia. El [recibo de máscaras](mask-unions-20261025.json) conserva cada hash y resultado; las observaciones originales permanecen intactas.

- Barrios pasa de 81 a **113 pasadas radar suficientes**, 32 oportunidades cuya cobertura estaba dividida entre segmentos.
- Santo Tomás pasa de 81 a **115**, 34 oportunidades adicionales. Estos incrementos son por puerto y se solapan entre sí; no son 66 órbitas distintas.
- Quetzal no tiene esa fragmentación y conserva 194 pasadas suficientes.

La unión mide cobertura a la resolución de la máscara. No reconcilia propuestas de buques duplicadas entre segmentos ni demuestra calidad del modelo. El reporte conserva el conteo conservador anterior junto al corregido.

## Qué ya está aprovechado y qué queda por mejorar

El catálogo ya contiene Sentinel-1 A/C/D, Sentinel-2 A/B/C y las direcciones radar ascendentes y descendentes. La búsqueda no descarta productos por nubosidad global; se usan máscaras locales. Habilitar otra vez esas opciones no aumenta el alcance.

La huella de catálogo cubre prácticamente todo el polígono configurado en cada pasada radar una vez reunidos sus segmentos, y todo el polígono en las escenas ópticas presentes. Sin embargo, esto no implica píxeles útiles: la cobertura óptica media conocida de las zonas es aproximadamente 49–53% en el Caribe frente a 68–70% en Quetzal. La nubosidad reduce las oportunidades ópticas suficientes a 108/307 en Barrios y 77/307 en Santo Tomás.

Ampliar polígonos en una **versión geográfica nueva** puede observar más agua con las mismas capturas cuando ésta está dentro de la huella. La auditoría anterior de [Santo Tomás](../local-review/phase2/stc-quay-coverage-audit.md) ya documentó una separación de 136,87 m entre muelles y canal. Ese hueco se debe al alcance de las zonas analíticas; no se debe a la órbita. No se alteraron los límites congelados ni sus etiquetas para favorecer un resultado.

Para mayor frecuencia harían falta capturas adicionales, no más recortes de la misma escena. Las fechas disponibles y las pérdidas por zona ahora son visibles y separadas de las limitaciones del detector. Una fuente nueva debe conservar resolución, compatibilidad de modelos, licencia y coste como criterios explícitos.

## Reproducir y actualizar

Desde la raíz del repositorio:

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_pass_coverage.py \
  --data-dir data --rebuild-masks \
  --start 2025-01-01T00:00:00Z --end 2026-10-21T00:00:00Z \
  --output data/coverage/recalculated.json
```

La reconstrucción solo escribe `data/coverage/pass-unions-v1.json` y el reporte elegido. Verifica los bytes originales antes de unirlos. La API puede leer ese cache sin abrir rasters en cada consulta; solo aplica entradas cuyo hash de geometrías y conjunto exacto de observaciones/activos registrados siguen coincidiendo. Nuevas observaciones, máscaras o geometrías obligan a reconstruir la entrada correspondiente; mientras tanto se usa la cota conservadora. Cuadrículas distintas o desalineadas se rechazan, no se reproyectan silenciosamente.
