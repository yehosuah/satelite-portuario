# Corroboración GFW: calidad de visitas TRAIN

La descarga contiene **1.504 eventos únicos**, después de deduplicar 2.402 entradas por ID de evento. La consulta de STC repitió los 898 eventos de PBR: al contrastar el punto representativo con V1 y el anclaje intermedio, quedan **541 PRQ, 898 PBR y 65 STC**. Los IDs, nombres y coordenadas de buques no se publican.

El [informe JSON](gfw-corroboration.json) fija los hashes del recibo, plan, V1 y cola TRAIN. Se comprobaron las 13 páginas, el plan y los bytes de V1. La auditoría usó los eventos y solo tiempos/geometrías de doce escenas TRAIN; no leyó credenciales, modelos, catálogo, etiquetas, CAL o TEST ni realizó llamadas adicionales.

## Calidad y atribución

| Consulta por envolvente | Entradas | PRQ exacto V1 | PBR exacto V1 | STC exacto V1 |
|---|---:|---:|---:|---:|
| PRQ | 541 | 541 | 0 | 0 |
| PBR | 898 | 0 | 898 | 0 |
| STC | 963 | 0 | 898 | 65 |

Los 1.504 puntos representativos coinciden con el puerto de su anclaje intermedio. En 75 eventos difieren los IDs de puerto de los anclajes inicial, intermedio o final. Los polígonos V1 son provisionales; esta atribución describe su geometría congelada y no certifica límites oficiales.

Los eventos se solicitaron por solapamiento con enero–junio de 2025: 77 cruzan algún límite de la ventana; 35 comienzan antes, 50 terminan después y ocho cruzan ambos. Todos solapan el intervalo solicitado, pero no todos son visitas contenidas en 2025. Hay 17 eventos de más de 181 días, 14 de más de un año y uno de 3.975 días. Las fechas extremas van de abril de 2015 a octubre de 2026.

La confianza GFW es 4 en 1.471 eventos, 3 en 14 y 2 en 19. Es una propiedad del evento GFW, no una garantía de ubicación a la hora de Sentinel ni de cobertura completa. La mediana de duración es 26,37 horas; 675 eventos duran hasta un día, 670 entre uno y siete días, 76 entre siete y treinta, 66 entre treinta y 181, y 17 más de 181.

## Coincidencia con las escenas

Se exige que la captura caiga dentro del intervalo del evento y que su punto representativo esté dentro de la zona V1 exacta de la escena. Son coincidencias posibles para investigar; no son posiciones observadas del buque en ese instante.

| Puerto / sensor | Fecha UTC | Zona | Intervalo en bbox | Intervalo + punto en zona | Confianza de coincidencias |
|---|---|---|---:|---:|---|
| prq / S1 | 2025-02-07 | prq_fondeo | 27 | 20 | 3: 2, 4: 18 |
| prq / S1 | 2025-03-03 | prq_fondeo | 24 | 18 | 3: 2, 4: 16 |
| prq / S2 | 2025-03-04 | prq_muelles | 24 | 6 | 2: 5, 3: 1 |
| prq / S2 | 2025-04-03 | prq_fondeo | 29 | 23 | 3: 2, 4: 21 |
| pbr / S1 | 2025-04-03 | pbr_canal | 14 | 0 | — |
| pbr / S1 | 2025-05-21 | pbr_canal | 12 | 0 | — |
| pbr / S2 | 2025-01-10 | pbr_canal | 11 | 0 | — |
| pbr / S2 | 2025-05-18 | pbr_canal | 16 | 0 | — |
| stc / S1 | 2025-04-03 | stc_fondeo | 15 | 0 | — |
| stc / S1 | 2025-05-21 | stc_fondeo | 13 | 0 | — |
| stc / S2 | 2025-01-10 | stc_fondeo | 12 | 0 | — |
| stc / S2 | 2025-05-18 | stc_muelles | 17 | 1 | 2: 1 |

La única coincidencia STC muelles del 18 de mayo es un evento de confianza 2 que dura **820 días**. Las seis coincidencias PRQ muelles del 4 de marzo tienen confianza 2/3 y duran entre 70 y 1.325 días. No corroboran por sí mismas los objetos visuales propuestos. Los ceros de PBR canal y STC fondeo indican que no coincide un punto representativo en esas zonas; **no permiten etiquetar ausencia de barcos**.

## Qué permiten estos datos

Estas respuestas no contienen muestras de posición AIS fechadas a la hora de Sentinel. Contienen una posición representativa y anclajes de un intervalo. Por tanto, **no hay una vía a referencias instantáneas verificables usando solo este conjunto**, ni a cajas verdaderas del casco. Los eventos pueden orientar una búsqueda privada de identidad y fechas.

El siguiente insumo útil son posiciones o trayectorias AIS fechadas cerca de las capturas TRAIN, con correspondencia de identidad e incertidumbre espacial, o una referencia visual/portuaria independiente y fechada. Se mantienen desconocidos los casos ambiguos. No se creó ninguna etiqueta ni se midió precisión o recall.
