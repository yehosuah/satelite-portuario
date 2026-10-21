# Corrección de supervisión antes del primer entrenamiento de fase 2

Fecha: 2026-10-21. Autor: Codex, revisión y pruebas de software asistidas por IA.

La auditoría encontró que el entrenador anterior ponía en cero los píxeles fuera de
`reviewed_area`, pero enviaba a Faster R-CNN solamente las cajas positivas. Por ello,
RPN y RoI seguían asignando fondo a propuestas sin referencia fuera del área revisada,
incluido el relleno del tensor. Una reproducción con los módulos de torchvision
instalados asignó etiqueta 0 tanto a un ancla interior como a otra exterior y produjo
gradiente de objectness 0,25 en ambas. Ocultar la imagen no excluía esa supervisión.

La corrección mantiene el contexto numérico de la imagen y pasa una máscara booleana
de supervisión en su cuadrícula nativa. Se comprobó en el código de rslearn fijado por
`uv.lock` que `NoopTransform` solamente añade relleno y no cambia la escala. Un
negativo de RPN o RoI solo participa si todo su rectángulo, incluidos los píxeles
fraccionarios del borde, está dentro del área revisada. El resto recibe `-1` y queda
fuera del muestreo de pérdida. Los matches positivos contra referencias conocidas
se conservan aunque su caja cruce el límite. El relleno siempre queda excluido.

Los métodos se sustituyen temporalmente en una instancia del modelo, sin modificar
torchvision globalmente, y se restauran aun cuando falla el paso. Cuando una rama
no tiene ninguna muestra supervisable, su pérdida es cero con gradiente cero. Si
ambas ramas están vacías, se registra el paso omitido y no se ejecuta el optimizador.
Un entrenamiento compuesto enteramente por pasos omitidos no exporta pesos.

Las regresiones usan RPN/RoI reales: negativo interior, negativo exterior, caja que
cruza el límite, positivo parcial, relleno, gradientes, restauración tras excepción y
paso completo con una red pequeña. No descargan pesos ni estiman precisión. El
entrenador conserva en el artefacto la política `reviewed_footprint_negatives_v1`.

Esto documenta una limitación técnica del ajuste de fase 1: sus áreas desconocidas
no estaban excluidas correctamente de la pérdida. Sus pesos, resultados y recibos
históricos se conservan; no se reejecutaron ni se sustituyeron sus métricas. La
corrección ocurre antes del primer entrenamiento de fase 2 y no constituye una
validación del detector ni evidencia de que alcance 90 %.
