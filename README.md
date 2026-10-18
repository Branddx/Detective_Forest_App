# Vigía de Incendios Perú

Mapa web de focos de calor satelitales en el Perú pensado para bomberos, brigadas forestales, grupos de ayuda y población. Agrupa las detecciones de NASA FIRMS en incendios y, para cada uno, muestra ubicación (lat/lon y UTM), área estimada, intensidad (FRP), viento, temperatura, humedad, días sin lluvia, dirección probable de avance, calidad del aire y los recursos más cercanos: compañías de bomberos, establecimientos de salud, fuentes de agua, vías de acceso, poblados y helipuertos.

![Mapa con incendios agrupados](docs/captura-mapa.png)

> Un foco de calor es una anomalía térmica detectada desde el espacio, no un incendio confirmado. Muchas detecciones son quemas agrícolas. Confirma en campo y reporta a la autoridad forestal regional, SERFOR o el COER.

## Inicio rápido

Requiere Python 3.10 o superior y conexión a internet (el servidor consulta las fuentes; el navegador carga mapas y Leaflet).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/macOS/Raspberry Pi: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # en Windows: copy .env.example .env
python app.py
```

Abre `http://localhost:8000`. Otros equipos de la misma red pueden entrar con la IP del servidor (`http://192.168.x.x:8000`).

**Clave de FIRMS (recomendada).** Solicita un MAP_KEY gratuito en <https://firms.modaps.eosdis.nasa.gov/api/map_key/> y colócalo en `.env` como `FIRMS_MAP_KEY=...`. Con clave el servidor consulta solo la caja del Perú. Sin clave descarga los CSV públicos de Sudamérica y los filtra, lo cual también funciona pero transfiere más datos (el de 7 días pesa varios MB).

**Modo demostración.** `DEMO=1` en `.env` (o el botón que aparece si FIRMS no responde) genera focos simulados para explorar la interfaz sin internet. La interfaz los marca como “DATOS DE DEMOSTRACIÓN”.

**Docker.**

```bash
docker build -t vigia-incendios .
docker run -p 8000:8000 --env-file .env vigia-incendios
```

## Qué muestra

| Sección | Contenido |
|---|---|
| Resumen | Focos, incendios agrupados, activos en las últimas 12 h, FRP total y hectáreas con calor en el periodo (24 h, 48 h o 7 días). |
| Mapa | Focos coloreados por antigüedad (como FIRMS), incendios por nivel, flechas de avance según el viento, límites departamentales y, al acercar, la huella real de cada píxel satelital. |
| Lista | Incendios ordenables por prioridad, recientes, FRP o área; filtros por región, nivel, estado y texto. |
| Detalle | Coordenadas y UTM con botón de copiar; enlaces a Google Maps, ruta, OpenStreetMap, FIRMS y NASA Worldview; magnitud; meteorología actual y pronóstico de 24 h; índice de Angström; peligro meteorológico; pronóstico de propagación con probabilidad de llegada a 3, 6, 12 y 24 h y poblados o vías que podría alcanzar; calidad del aire; distrito y área natural protegida; recursos cercanos con “ruta al fuego”. |
| Calidad del aire | Índice de Calidad del Aire (INCA) oficial del Perú en el incendio, en los poblados cercanos y en dos anillos a 25 y 50 km; contaminante dominante, máximo pronosticado en 24 h, gráfico de 48 h, dirección del humo y recomendaciones del MINAM, con acciones concretas ante humo cuando la calidad es Mala o llega al umbral de cuidado. |
| Compartir | Reporte de texto listo para WhatsApp o radio con lugar, coordenadas, magnitud, viento, calidad del aire y recursos. |
| Regiones | Focos, incendios, activos y FRP por departamento. |
| Capas | Mapa, relieve, satélite Esri, imagen diaria NASA (color real y falso color para quemas), anomalías térmicas NASA GIBS, eventos NASA EONET; filtros por satélite y confianza; descarga CSV y GeoJSON. |

![Detalle de un incendio](docs/captura-detalle.png)

## Fuentes de datos

### Integradas en la aplicación

| Fuente | Aporta | Acceso | Clave | Actualización |
|---|---|---|---|---|
| [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) — API de área | Focos VIIRS (NOAA-20, NOAA-21, Suomi NPP, 375 m) y MODIS (1 km): posición, FRP, temperatura de brillo, tamaño de píxel, confianza, día/noche | `/api/area/csv/{MAP_KEY}/{FUENTE}/{oeste,sur,este,norte}/{días}` | MAP_KEY gratuito (5000 transacciones cada 10 min) | Casi tiempo real, ~3 h tras la pasada |
| NASA FIRMS — CSV públicos | Los mismos focos para Sudamérica en ventanas de 24 h, 48 h y 7 días | `/data/active_fire/<satélite>/csv/..._South_America_{24h,48h,7d}.csv` | No | Igual |
| [NASA GIBS](https://nasa-gibs.github.io/gibs-api-docs/) | Imagen diaria VIIRS (color real y bandas M11-I2-I1 para cicatrices de quema) y capa de anomalías térmicas | WMTS / WMS | No | Diaria |
| [NASA EONET](https://eonet.gsfc.nasa.gov/docs/v3) | Eventos de incendio curados (pocos en el Perú) | REST JSON | No | Continua |
| [Open-Meteo](https://open-meteo.com/) | Viento, ráfagas, dirección, temperatura, humedad, lluvia de 14 días, déficit de presión de vapor, pronóstico 24 h, altitud | REST JSON | No (uso no comercial) | Horaria |
| [Open-Meteo Air Quality](https://open-meteo.com/en/docs/air-quality-api) | PM2.5, PM10, CO, O₃, NO₂ y SO₂ horarios del modelo CAMS global (incluye emisiones de incendios), 1 día atrás y 2 adelante | REST JSON | No | Horaria |
| [OpenStreetMap / Overpass](https://overpass-api.de/) | Distrito, provincia, áreas protegidas, bomberos, salud, comisarías, poblados, agua, vías, helipuertos | Overpass QL | No | Continua (colaborativa) |
| [Esri Land Cover 10 m](https://livingatlas.arcgis.com/landcover/) (Sentinel-2, Impact Observatory, CC BY 4.0) | Cobertura del suelo (árboles, pastizal y matorral, cultivos, agua, urbano, suelo desnudo, nieve) para asignar el combustible | ImageServer `exportImage` | No | Anual |
| [Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) (AWS Open Data, Mapzen) | Relieve para calcular pendiente y orientación | Teselas PNG Terrarium | No | Estático |

### Recomendadas para ampliar

| Fuente | Por qué sirve | Acceso |
|---|---|---|
| [INPE Programa Queimadas](https://terrabrasilis.dpi.inpe.br/queimadas/portal/) | Focos de toda Sudamérica incluido el satélite geoestacionario GOES (detección cada 10 min), riesgo de fuego | Datos abiertos CSV/GeoJSON, WMS y WFS |
| [NOAA GOES-19 FDC](https://www.ospo.noaa.gov/) | Fuego, temperatura, área y FRP cada 10 min sobre todo el Perú (GOES-Este) | NetCDF en AWS Open Data (`noaa-goes19`), Google Earth Engine |
| [GWIS – Copernicus](https://gwis.jrc.ec.europa.eu/) | Pronóstico de peligro de incendio (FWI) a 10 días, área quemada, emisiones | Visor y servicios WMS |
| [Copernicus Data Space](https://dataspace.copernicus.eu/) | Sentinel-2 (10 m) para delimitar cicatrices de quema; producto global de área quemada | Sentinel Hub API (cuenta gratuita) |
| [SERFOR – Focos de calor](https://appweb.serfor.gob.pe/FocosCalor/Default.aspx) | Alertas oficiales de incendio forestal remitidas a autoridades regionales y COER | Web |
| [SENAMHI – Incendios](https://www.senamhi.gob.pe/site/incendio/) | Avisos y boletines de condiciones atmosféricas de incendios forestales | Web / PDF |
| INDECI – SINPAD / COEN | Registro oficial de emergencias por incendio forestal | Web |

## Cómo se calculan los indicadores

- **Incendio agrupado.** Dos focos a menos de 1,5 km pertenecen al mismo incendio (enlace simple sobre una malla espacial). Así se unen las detecciones de varios satélites y pasadas.
- **Área con calor (ha).** Unión de las huellas de píxel (`scan × track` de FIRMS) rasterizada en una malla de ~90 m, para no contar dos veces el mismo terreno. Sobrestima el área ardiendo: un píxel VIIRS cubre ~14 ha.
- **Envolvente (ha).** Polígono convexo de las esquinas de todos los píxeles. Aproxima la extensión del frente en el periodo.
- **Nivel.** Por FRP total del incendio: Bajo < 10 MW, Moderado 10–50, Alto 50–200, Muy alto 200–1000, Extremo ≥ 1000. Son umbrales de referencia de esta herramienta, no oficiales. Se ajustan en `vigia/clustering.py`.
- **Estado.** Activo si la última detección tiene menos de 12 h; reciente entre 12 y 24 h; anterior si es más antigua.
- **Prioridad.** `(FRP total + 2 × detecciones) × (0,25 + e^(−horas/24))`: favorece incendios intensos y recientes.
- **Flecha de viento.** Opuesta a la procedencia del viento: hacia dónde empuja el fuego y el humo. Se muestra para todos los incendios de la vista general.
- **Pronóstico de propagación** (`vigia/propagacion.py`, al abrir un incendio). Ver la sección siguiente.

### Pronóstico de propagación

| Paso | Método | Datos |
|---|---|---|
| Comportamiento del fuego | Modelo de superficie de Rothermel (1972) con ponderación de Albini (1976), elipse de Anderson (1983) y límite de viento de Andrews et al. (2013): el mismo núcleo de BehavePlus, FARSITE y FlamMap. Validado contra las pruebas de [firelab/behave](https://github.com/firelab/behave) (diferencia < 0,1 %). | — |
| Combustible | Modelos de Scott y Burgan (2005) según cobertura y altitud: pastizal ≥ 3300 m → GR2 (pajonal, ichu); pastizal o matorral más bajo → GS2; árboles en selva baja → TL2; árboles en bosque seco, montano o plantaciones → TU1; cultivos y humedales → GR1; agua, urbano, suelo desnudo y nieve → sin combustible. | Esri Land Cover 10 m |
| Humedad del combustible | Equilibrio de Simard (NFDRS) con ajuste diurno por nubosidad, desfase de 1, 10 y 100 h y saturación con lluvia, hora a hora desde 5 días antes. El pasto se cura según los días sin lluvia (60 días de historial). | Open-Meteo horario |
| Relieve | Pendiente y orientación por diferencias centradas. | Terrarium |
| Avance en el espacio | Tiempo mínimo de recorrido (Finney 2002): Dijkstra dependiente del tiempo en una malla de 65 × 65 celdas con 16 vecinos. Parte de cada píxel detectado en las últimas 24 h, a su hora de detección. Una celda encendida puede esperar hasta 16 h a que mejoren las condiciones, por ejemplo de la noche al día. Agua, roca y zonas urbanas son barreras. | — |
| Calibración con satélites | Si hay varias pasadas, se mide cuándo aparecieron píxeles nuevos y se simula desde la primera pasada. La mediana de la razón entre la llegada simulada y la observada ajusta la velocidad, contraída hacia 1 con peso n/(n+4). Es la idea de los factores de ajuste de FARSITE asimilados con datos satelitales (Srivas et al. 2017). | FIRMS |
| Incertidumbre | 10 escenarios que perturban la velocidad del viento (±25 %), su dirección (±20°), la humedad (±15 %) y el factor de velocidad. La probabilidad es la fracción de escenarios en que el fuego llega. Si el escenario mediano toca el borde, el dominio se duplica una vez (máximo 25 km del centro). | — |

El estudio de Podschwit et al. (2023, *NHESS*) sobre el crecimiento de incendios en el Perú encontró que el componente de propagación del NFDRS, derivado de Rothermel, era el mejor predictor en los Andes y la Amazonía. Eso respalda usar este modelo aquí. Los modelos de aprendizaje automático del tipo *Next Day Wildfire Spread* (Huot et al. 2022) necesitan años de perímetros diarios de entrenamiento, que no existen para el Perú; quedan como mejora futura.

Limitaciones: no modela vientos de ladera ni de valle, saltos de pavesas, fuego de copas, focos nuevos ni el trabajo de las brigadas. La cobertura es del año anterior: un bosque talado y quemado este año puede figurar aún como árboles, y la calibración compensa en parte esa diferencia.
- **Calidad del aire (INCA)** (`vigia/aire.py`). Escala oficial de la [R. M. N.° 181-2016-MINAM](https://sinia.minam.gob.pe/sites/default/files/sinia/archivos/public/docs/rm-n-181-2016-minam.pdf): Buena 0–50 (verde), Moderada 51–100 (amarillo), Mala de 101 al valor umbral del estado de cuidado, VUEC (anaranjado), y Umbral de cuidado (rojo). Por contaminante: `I = C·100/divisor`, con promedio móvil de 24 h para PM2.5 (÷25), PM10 (÷150) y SO₂ (÷20), de 8 h para CO (÷10 000) y O₃ (÷120) y de 1 h para NO₂ (÷200). La calificación sale de los intervalos de concentración de la tabla 3, con su misma precisión. Así se evita un error de la tabla: la columna de índice del SO₂ dice 101–625, mientras que su ecuación da 2500 en el VUEC. El INCA general es el del contaminante en peor categoría. Se evalúa en el incendio, en 16 puntos a 25 y 50 km y en los poblados de OpenStreetMap. Se marcan «humo» los que están a ±35° del rumbo del viento de las próximas 6 h y a menos de 60 km. El nivel de alerta es el peor entre el incendio (ahora y en 24 h) y los poblados o puntos a sotavento a menos de 25 km. Muestra los cuidados y la recomendación oficial de esa categoría. Cuando es Mala o peor agrega acciones concretas ante humo: interiores, mascarilla N95/KN95/FFP2, grupos sensibles, aire interior, síntomas de alerta, SAMU 106 y línea 113. Los datos vienen de un modelo con celdas de ~40 km, no de estaciones: junto al fuego el humo puede ser mucho más denso.
- **Índice de Angström.** `HR/20 + (27 − T)/10`. Menor a 2,0: muy favorable al fuego; mayor a 4,0: desfavorable.
- **Peligro meteorológico.** Suma propia de Angström, viento, ráfagas y días sin lluvia (≥ 1 mm), de 0 a 5. Para avisos oficiales usar SENAMHI.
- **Ubicación.** Departamento y provincia con límites simplificados incluidos en `data/`; distrito y áreas protegidas desde OpenStreetMap.

## API del servidor

| Ruta | Descripción |
|---|---|
| `GET /api/incendios?rango=24h\|48h\|7d&fuentes=VIIRS_NOAA20,...&confianza=baja\|nominal\|alta&solo_peru=1` | Focos, incendios agrupados (con viento), resumen por departamento y estado de cada fuente |
| `GET /api/meteo?lat=&lon=` | Meteorología, índices, pronóstico 24 h y calidad del aire |
| `GET /api/contexto?lat=&lon=&radio=30` | Distrito, áreas protegidas y recursos cercanos |
| `GET /api/aire?lat=&lon=&lugares=Nombre\|lat\|lon;...` | INCA actual y pronosticado en el incendio, anillos de 25 y 50 km y lugares indicados; dirección del humo, serie de 48 h y recomendaciones |
| `GET /api/propagacion?id=&horizonte=24` (+ parámetros de `/api/incendios`) | Pronóstico de propagación: malla, probabilidad de llegada por horizonte, llegada mediana por celda, isolíneas, resumen, serie horaria, calibración y avance observado |
| `GET /api/eonet` | Eventos EONET en la zona |
| `GET /api/exportar.csv` / `GET /api/exportar.geojson` | Incendios con los mismos parámetros que `/api/incendios` |
| `GET /api/estado` | Configuración, fuentes y niveles |

Las respuestas se guardan en caché en memoria: FIRMS 10 min, meteorología y calidad del aire 30 min, pronóstico de propagación 15 min, OpenStreetMap 6 h, relieve y cobertura 24 h. Un pronóstico tarda de 1 a 3 s la primera vez.

## Configuración

Variables en `.env` (ver `.env.example`): `FIRMS_MAP_KEY`, `CARTO_API_KEY` (mapa base claro/oscuro de CARTO; sin ella se usa OpenStreetMap), `OVERPASS_URLS` (servidores Overpass propios, se consultan primero), `CACHE_DIR` (respaldo en disco de OpenStreetMap; por defecto `.cache`), `DEMO`, `CACHE_FIRMS_MIN`, `VIENTO_TOP_N` (incendios que reciben viento en la vista general), `RADIO_RECURSOS_KM`, `HOST`, `PORT`.

**Contáctenos.** La pestaña Ayuda muestra un apartado de contacto con los enlaces que definas en `.env`: `CONTACTO_FACEBOOK`, `CONTACTO_INSTAGRAM`, `CONTACTO_TIKTOK`, `CONTACTO_X`, `CONTACTO_YOUTUBE`, `CONTACTO_LINKEDIN`, `CONTACTO_WEB` (enlace https), `CONTACTO_WHATSAPP` (número con código de país, p. ej. `51987654321`, o enlace) y `CONTACTO_CORREO`. `CONTACTO_TEXTO` reemplaza el mensaje de presentación. Si todas están vacías, el apartado no aparece. Los valores no válidos se ignoran y se avisan en la consola al arrancar. Tras editar `.env`, reinicia el servidor.

Los nombres de las capas de NASA GIBS están en el objeto `CONFIG` al inicio de `static/app.js`. Si NASA renombra una capa, basta con cambiarla ahí (lista oficial en el documento de capacidades de GIBS).

### Si OpenStreetMap no responde

Los recursos cercanos, el distrito y los poblados que podría alcanzar el fuego vienen de OpenStreetMap a través de Overpass, un servicio público y gratuito que a menudo se satura (HTTP 429/502/504 o «at capacity») o se cae (HTTP 500). Ese error no viene de la app ni de tu conexión. Para tolerarlo, `vigia/context.py`:

- separa la consulta en dos: recursos (cualquier servidor) y distrito o áreas protegidas (solo servidores con índice de áreas). Si falla la segunda, los recursos se muestran igual;
- consulta varios servidores escalonados: si uno no contesta en 8 s, pregunta al siguiente y usa la primera respuesta válida;
- detecta respuestas HTTP 200 que en realidad son errores (`remark: runtime error…`) y no las confunde con «no hay recursos»;
- pausa el servidor que falla (1 min, luego 2, 4… hasta 10) para no insistir mientras está saturado, y no repite una consulta fallida antes de 45 s;
- guarda en disco (`.cache/osm/`) la última consulta buena de cada zona y la usa hasta 30 días si todos fallan, avisando la fecha;
- en la interfaz muestra «Reintentar ahora» y vuelve a intentar sola al minuto.

Para un uso institucional lo más robusto es una instancia propia de Overpass solo con el Perú, con las áreas habilitadas y actualización diaria de Geofabrik:

```bash
docker run -e OVERPASS_MODE=init \
  -e OVERPASS_PLANET_URL=https://download.geofabrik.de/south-america/peru-latest.osm.pbf \
  -e OVERPASS_PLANET_PREPROCESS='mv /db/planet.osm.bz2 /db/planet.osm.pbf && osmium cat -o /db/planet.osm.bz2 /db/planet.osm.pbf && rm /db/planet.osm.pbf' \
  -e OVERPASS_DIFF_URL=https://download.geofabrik.de/south-america/peru-updates/ \
  -e OVERPASS_USE_AREAS=true -e OVERPASS_META=no -e OVERPASS_UPDATE_SLEEP=86400 \
  -v /datos/overpass_peru:/db -p 12345:80 --name overpass_peru wiktorn/overpass-api
docker start overpass_peru   # el contenedor se detiene al terminar la carga inicial
```

y en `.env`: `OVERPASS_URLS=http://localhost:12345/api/interpreter`. Se consulta primero y los servidores públicos quedan de respaldo.

## Pruebas

```bash
pip install pytest
python -m pytest -q
```

Cubren lectura de los CSV de FIRMS (VIIRS y MODIS), ubicación por departamento, conversión UTM (validada con pyproj), agrupamiento, cálculo de áreas, niveles, índices meteorológicos y la interpretación de respuestas de Open-Meteo y Overpass. Para la propagación cubren Rothermel contra los valores de referencia de behave, el decodificador PNG, la humedad del combustible, el avance observado entre pasadas, la simulación (isotropía, viento, barreras, espera nocturna), las isolíneas y un pronóstico completo con servicios simulados. Para la calidad del aire cubren cada borde de las tablas del INCA, los promedios móviles, el contaminante dominante, la dirección del humo, las recomendaciones y una evaluación completa sin red.

## Limitaciones

- Los satélites polares pasan pocas veces al día; un incendio puede no aparecer hasta la siguiente pasada y las nubes o el humo denso ocultan focos.
- Coordenadas con error de unos cientos de metros (VIIRS) a ~1 km (MODIS).
- La cobertura de OpenStreetMap en zonas rurales es incompleta: un “ninguno registrado” no significa que no exista.
- La calidad del aire viene de un modelo global con celdas de ~45 km.
- Open-Meteo es gratuito para uso no comercial; para un despliegue institucional revisa sus condiciones.

## Estructura

```
app.py                 servidor Flask y rutas
vigia/firms.py         descarga y lectura de FIRMS (API y CSV) + datos de demostración
vigia/clustering.py    agrupamiento, áreas, niveles, resumen por región
vigia/weather.py       Open-Meteo: viento, índices, pronóstico, calidad del aire
vigia/context.py       Overpass: distrito, áreas protegidas y recursos cercanos
vigia/aire.py          Índice de Calidad del Aire (INCA, MINAM) alrededor del incendio y recomendaciones
vigia/rothermel.py     modelo de Rothermel y modelos de combustible de Scott y Burgan
vigia/raster.py        decodificador PNG, relieve Terrarium y cobertura Esri Land Cover
vigia/propagacion.py   humedad del combustible, avance observado, simulación, calibración y conjunto
vigia/eonet.py         eventos NASA EONET
vigia/geo.py           límites, punto en polígono, UTM, distancias, envolventes
data/                  departamentos y provincias del Perú (GeoJSON simplificado)
static/                interfaz (HTML, CSS, JavaScript con Leaflet)
tests/                 pruebas
```

Límites departamentales y provinciales: [juaneladio/peru-geojson](https://github.com/juaneladio/peru-geojson), simplificados.
