# Cambios físicos: estado y límites

La implementación incluye una red siamesa con encoder compartido Satlas Sentinel2_SwinT_SI_MS y un decoder multiescala de segmentación. El encoder se congela inicialmente; las características de ambas fechas y su diferencia alimentan el decoder. Se entrena cambio/no cambio con una pérdida que excluye regiones no observables. Deben incluirse pares negativos bajo distintas condiciones.

La receta `config/models/change.yaml` está sin entrenar. No hay pesos propios ni métricas locales de cambios entregados. `compare` rechaza la ejecución sin pesos propios y checksum, sin máscara observable/área estática o sin límites medidos de alineación, visibilidad, superficie y umbral. No usa una diferencia de píxeles como sustituto silencioso del modelo aprendido.

El adaptador crea el encoder directamente desde la [API de Satlas fijada](https://github.com/allenai/satlaspretrain_models/blob/7b5cd45adc3cad70b3834d65956974af6f6bffd0/satlaspretrain_models/model.py), leyendo solo pesos locales comprobados. No invoca el descargador de checkpoints mutables. La dependencia opcional está separada de la API de consulta. La prueba de entrenamiento con un encoder pequeño verifica que el decoder recibe gradiente y el encoder permanece congelado; no acredita rendimiento satelital.

También se verificó el encoder real de 225.85 MB con SHA256 fijado: ambas ramas y el decoder produjeron salidas finitas, y el backward sintético dejó el encoder congelado. La [evidencia Satlas](../../reports/pilot/satlas-smoke.md) registra tiempos, memoria y la adaptación radiométrica entre TCI/255 y RGB numérico/3000. Esa prueba usa la misma adquisición en ambas ramas y no demuestra detección de cambios.

Las comparaciones son ópticas, de igual producto y receta, corregistradas y radiométricamente comparables. Se limitan a recortes de hasta 512 píxeles. El área estática debe excluir agua y regiones transitorias; las máscaras de buques y temporales se aplican cuando están disponibles. Las superficies se calculan en UTM local, mientras la geometría de intercambio permanece WGS84. No se clasifica la causa del cambio.

Un par t0 sin cambio/t1 con cambio produce un candidato. Una adquisición óptica distinta t2, observable y alineada, con cambio lo confirma como persistente. Nubes, datos inválidos y radar no lo confirman ni lo revierten. Una observación óptica válida posterior sin cambio marca reversión y conserva el historial. Un evento revertido no se reactiva; un nuevo episodio requiere otra referencia y un nuevo evento. Persistente no significa permanente.

Quedan pendientes la anotación de pares locales, entrenamiento, prueba congelada por eventos/polígonos, recall, falsos avisos en pares negativos y límites mínimos útiles. Los checkpoints Satlas declaran licencia ODC-BY; atribución y condiciones del encoder deben acompañar los pesos derivados, sin extender esa licencia automáticamente a otros componentes.

## Experimento público de factibilidad (2026-10-23 UTC)

Se entrenó un decoder propio con el encoder Satlas congelado sobre un subconjunto
manual OSCD fijado por presupuesto, sin modificar esta receta base ni activar
publicación. El artefacto experimental permanece fuera del registro operativo:
`data/experiments/oscd-bounded-v1-20261023`. La prueba independiente pública falló
(TP0, FP0, FN9749; IoU/F1=0). El TEST está consumido; no se ajustó el umbral después.

Los TIFF OSCD no tienen georreferenciación: se evaluaron coordenadas de píxel,
sin inventar polígonos WGS84. Esta ejecución no acredita precisión portuaria,
control local de nubes ni persistencia. El README oficial restringe las máscaras
a CC BY-NC-SA, en conflicto con CC BY4.0 del DOI; se conserva la restricción y no
se autoriza redistribución comercial ni despliegue de los pesos resultantes.
El informe reproducible está en
`reports/change/public-data/oscd-bounded-results.md` y su recibo JSON contiguo.
