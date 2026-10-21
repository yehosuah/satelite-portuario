# Punto estable del 21 de octubre de 2026

El servicio está operativo y la entrega científica sigue pendiente. Se cierra esta sesión por el margen de uso semanal solicitado; el trabajador histórico local continúa. Esta es una captura de estado, no un contador en vivo.

- **Software:** 168 pruebas aprobadas, Ruff y sintaxis JavaScript correctos. La consolidación elimina únicamente líneas y puntos sin superficie tras el recorte; una regresión verifica que conserva todos los polígonos y su área. La recuperación de la aplicación parcial quedó auditada, sin duplicar revisiones.
- **Etiquetas ópticas congeladas:** entrenamiento con 51 objetos físicos, 53 instancias de zona, 32 observaciones y 84 recortes en 7 adquisiciones; calibración con 60 objetos, 61 instancias y 33 observaciones en 6 adquisiciones. Todos tienen procedencia de revisión visual de IA.
- **Entrenamiento/validación ejecutados:** la ronda de enero terminó con 3 épocas y 111 actualizaciones. Su calibración falló: a umbral 0,80 hubo 0 aciertos, 16 falsas detecciones y 22 omisiones bajo IoU0,5. Ningún umbral de la cuadrícula pasó. El test sigue sin consumirse. El conjunto ampliado todavía no se entrenó.
- **Histórico:** 123 de 824 adquisiciones adicionales completas; 1 en proceso y 700 pendientes al corte. El procesamiento persistente mantiene límites y reintentos. Las propuestas siguen siendo experimentales.

No está demostrada la precisión ≥90%. El soporte de calibración sigue siendo insuficiente en Puerto Barrios (6 barcos) y Santo Tomás (4); Quetzal aporta 50. Los recortes solapados y las instancias de zona no aumentan el número de objetos independientes. Las referencias de familias, dimensiones y cambios siguen pendientes.

## Continuación exacta

1. Consultar el estado del trabajador antes de lanzar otro. `data/history/full-v1/PAUSE` solicita detenerse al finalizar una adquisición; retirar ese archivo y usar el comando guardado en el JSON permite continuar cuando el trabajador anterior ya salió.
2. Terminar la segunda revisión de la reserva Caribe del 20 de octubre. Su correspondencia está [guardada como pendiente](calibration-s2-reserve-20251020/correspondence/pending-receipt.json); no está aplicada ni forma parte de la calibración congelada. La ronda 3 solo tiene readiness.
3. Usar los manifiestos [TRAIN ampliado](s2-january-february-frozen.json) y [CAL ampliado](s2-calibration-rounds1-2-frozen.json) para un experimento nuevo. Conservar la [ronda 1 fallida](experiment-s2-round1-20261021/results.md) y mantener cerrado el test hasta cumplir la calibración. El diagnóstico mínimo TRAIN está preparado y aún no se ejecutó.

Las zonas provisionales y los ámbitos reducidos por incertidumbre limitan las conclusiones. Estas etiquetas no sustituyen una revisión humana independiente. Los informes públicos conservan procedencia y hashes; el paquete portable v0.1.0 contiene el piloto inicial y no los datasets ampliados ni los nuevos pesos locales.

[Estado estructurado, rutas y hashes](stable-checkpoint-20261021.json).
