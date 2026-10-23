La revisión CAL S2 entregada está adjudicada: **180 observaciones**, sin adjudicaciones pendientes dentro de ese bloque. El calendario completo contiene 204 observaciones materializadas; las otras **24 corresponden a seis primarias PRQ todavía sin revisión**. Alcanzar la cuota de referencias en PRQ no completa todas las fechas previstas.

| Puerto | Observaciones adjudicadas / materializadas | Con ámbito revisado | Abstenciones | Primarias adjudicadas | Reservas activas adjudicadas |
|---|---:|---:|---:|---:|---:|
| PBR | 80 / 80 | 42 | 38 | 12 / 12 | 8 / 8 |
| STC | 76 / 76 | 44 | 32 | 12 / 12 | 7 / 7 |
| PRQ | 24 / 48 | 19 | 5 | 6 / 12 | 0 / 0 |

Los ámbitos de patios se conservan como controles terrestres. Hay ámbito marino revisado en 11 slots de PBR, 12 de STC y cinco de PRQ. Entre los slots ya adjudicados, PRQ 2025-10-05 y PBR 2025-11-26 quedaron sin ámbito marino revisable y sin reservas elegibles restantes; estas ausencias no representan cero barcos.

Las seis primarias PRQ restantes son 2025-11-19, 2025-11-29, 2025-12-04, 2025-12-09, 2025-12-19 y 2025-12-29. Sus 24 observaciones siguen provisionales, sin informe raw localizado. Las claves completas, los identificadores y los estados administrativos constan en el JSON y CSV de esta auditoría. No se abrieron esas imágenes ni se activaron nuevas reservas.

La congelación puede usar el bloque adjudicado, conservando las abstenciones, las referencias únicas y los ámbitos explícitos. Las exclusiones por nube, costa, interfaces ambiguas y geometría del detector siguen limitando la interpretación: una métrica sobre esos ámbitos no sería una precisión incondicional de todo el puerto. Esta auditoría no calcula desempeño, soporte de predicciones ni aprobación de gates.

La comprobación se hizo en una transacción SQLite de solo lectura: identidad, estado, revisión y geometrías de ámbitos. Se verificaron las revisiones y hashes de adjudicación. No se consultaron detecciones, puntuaciones ni métricas del modelo; tampoco se modificó la base de datos. La autoría de revisión es IA, no humana.

Fuentes inmutables del corte, 2026-10-23 04:41:56 UTC:

- `calibration-s2-slot-coverage-audit-v11.json`: SHA-256 `cfd2fc23e645963aedf8db2f401bca02f67e77e085c3bbba2339cf7e9c543b93`.
- `calibration-s2-slot-coverage-audit-v11.csv`: SHA-256 `eee58f41740f5917e0d4517046841e0a6b5c6d214c865f8c0258762b6fa0faac`.
- `execution-order-v11.json`: SHA-256 `a86bacc5a642bc9f4ab4c7049c5a8e4016d088c2311222f21238a5ca253d8f63`.

El JSON conserva además los hashes del plan de muestreo, protocolo, fuentes de adjudicación y geometrías. El cálculo de superficies proyecta las coordenadas de los anillos con subdivisión lineal y fórmula de área, sin reparar ni densificar topológicamente las geometrías originales.
