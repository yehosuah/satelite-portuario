La investigación encontró dos geometrías de fondeo independientes de las posiciones de barcos. Su vigencia actual no quedó verificada; no se cambió la configuración, la base de datos, las etiquetas ni el plan histórico. Investigación del 21 de octubre de 2026, autor Codex IA.

| Evidencia primaria | Resultado verificable | Límite |
|---|---|---|
| [NGA, Summary of Corrections 2013](https://msi.nga.mil/api/publications/download?key=16694412%2FSFH00000%2F2013%2FSummaryVolume1.pdf&type=view), página PDF 577 / impresa 28-12 | Aviso 48/09 para carta 28164, atribuido a Guatemala 3/09: cuatro vértices del fondeo exterior para calados de 6–11 m. | Corrección histórica. Carta base de 1995 con datum rotulado WGS, sin especificación explícita WGS84. |
| [NGA, catálogo público de cartas](https://dnc.nga.mil/dncp/PCA.php?page=6), carta 28165, edición 22 del 16-04-2011 | Carta Mercator WGS84, 1:15.000; dibuja fondeo interior para calados <6 m, canal a Santo Tomás y ramal a Puerto Barrios. | La copia dice corregida hasta NM 16/11; NM 23/11 anuncia la edición, no es la fecha de corrección impresa. |
| [NGA, Pub. 148](https://msi.nga.mil/api/publications/download?key=16694491%2FSFH00000%2FPub148bk.pdf), secciones 6.40–6.41, actualización a agosto de 2026 | Describe fondeo al suroeste/sur del muelle de Puerto Barrios, fuera del canal, y otra ubicación al norte de Villedo para mayor calado. | Descripciones narrativas sin límites poligonales. Conserva una advertencia de desalineación de enfilación reportada en 2010. |

Los vértices exteriores transcritos del aviso, en orden, son:

| Vértice | Latitud N | Longitud W |
|---|---|---|
| 1 | 15°49′30″ | 88°35′12″ |
| 2 | 15°48′43″ | 88°35′48″ |
| 3 | 15°51′21″ | 88°38′51″ |
| 4 | 15°52′10″ | 88°38′10″ |

El punto 15°50′24″ N, 88°36′56″ W coloca la leyenda; no es un quinto vértice. No se verificó una asignación administrativa exclusiva a cada puerto.

El fondeo interior se digitalizó del límite vectorial de la carta WGS84, ajustándolo a su graticulado. Estos son vértices aproximados derivados del dibujo, no coordenadas numéricas publicadas por la autoridad:

| Vértice | Longitud | Latitud |
|---|---|---|
| NW | -88.627771 | 15.720013 |
| NE | -88.619166 | 15.718335 |
| SE | -88.620834 | 15.707506 |
| SW | -88.629459 | 15.709181 |

Los decimales permiten reproducir el ajuste; no garantizan exactitud métrica ni vigencia. El JSON conserva hashes, controles del graticulado, segmentos vectoriales y comprobación visual. Los canales quedan documentados por la carta oficial y sus recortes; no se fabricó un corredor actual a partir de una dirección o de posiciones de prácticos.

El siguiente paso útil es obtener una carta o plano vigente de IGN/DIGEMAR o la autoridad portuaria, con límites georreferenciados y avisos aplicables. La [nota oficial MAGA de mayo de 2025](https://guatemala.gob.gt/sabia-que-el-mar-tambien-se-mapea/) confirma que existen cartas digitales locales, pero no entrega su geometría. El Manual BOP 2024 de CPN apareció como pista relevante; su figura no se recuperó y no se usó como evidencia geométrica. No se contactó a ninguna entidad.
