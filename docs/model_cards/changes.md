# Cambios físicos: estado y límites

La implementación incluye una red siamesa con encoder compartido Satlas Sentinel2_SwinT_SI_MS y un decoder multiescala de segmentación. El encoder se congela inicialmente; las características de ambas fechas y su diferencia alimentan el decoder. Se entrena cambio/no cambio con una pérdida que excluye regiones no observables. Deben incluirse pares negativos bajo distintas condiciones.

La receta `config/models/change.yaml` está sin entrenar. No hay pesos propios ni métricas locales de cambios entregados. `compare` rechaza la ejecución sin pesos propios y checksum, sin máscara observable/área estática o sin límites medidos de alineación, visibilidad, superficie y umbral. No usa una diferencia de píxeles como sustituto silencioso del modelo aprendido.

El adaptador crea el encoder directamente desde la [API de Satlas fijada](https://github.com/allenai/satlaspretrain_models/blob/7b5cd45adc3cad70b3834d65956974af6f6bffd0/satlaspretrain_models/model.py), leyendo solo pesos locales comprobados. No invoca el descargador de checkpoints mutables. La dependencia opcional está separada de la API de consulta. La prueba de entrenamiento con un encoder pequeño verifica que el decoder recibe gradiente y el encoder permanece congelado; no acredita rendimiento satelital.

Las comparaciones son ópticas, de igual producto y receta, corregistradas y radiométricamente comparables. Se limitan a recortes de hasta 512 píxeles. El área estática debe excluir agua y regiones transitorias; las máscaras de buques y temporales se aplican cuando están disponibles. Las superficies se calculan en UTM local, mientras la geometría de intercambio permanece WGS84. No se clasifica la causa del cambio.

Un par t0 sin cambio/t1 con cambio produce un candidato. Una adquisición óptica distinta t2, observable y alineada, con cambio lo confirma como persistente. Nubes, datos inválidos y radar no lo confirman ni lo revierten. Una observación óptica válida posterior sin cambio marca reversión y conserva el historial. Un evento revertido no se reactiva; un nuevo episodio requiere otra referencia y un nuevo evento. Persistente no significa permanente.

Quedan pendientes la anotación de pares locales, entrenamiento, prueba congelada por eventos/polígonos, recall, falsos avisos en pares negativos y límites mínimos útiles. Los checkpoints Satlas declaran licencia ODC-BY; atribución y condiciones del encoder deben acompañar los pesos derivados, sin extender esa licencia automáticamente a otros componentes.
