from datetime import datetime, timedelta, timezone

import pytest

from vigia import clustering, context, firms, geo, weather

AHORA = datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc)

# Encabezado real de los CSV públicos de FIRMS (VIIRS NOAA-20, Sudamérica)
CSV_VIIRS = """latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight
-12.85000,-72.72000,345.2,0.39,0.36,2026-10-15,0612,N20,nominal,2.0NRT,295.1,12.4,N
-12.85300,-72.72200,351.0,0.39,0.36,2026-10-15,0612,N20,high,2.0NRT,296.0,25.0,N
-12.85800,-72.71200,330.0,0.39,0.36,2026-10-15,1745,N20,low,2.0NRT,290.0,3.1,D
-5.92837,-35.62492,317.46,0.67,0.74,2026-10-15,0258,N20,nominal,2.0NRT,291.29,4.57,N
-16.40000,-71.53000,320.0,0.40,0.37,2026-10-13,0612,N20,nominal,2.0NRT,290.0,2.0,N
"""

CSV_MODIS = """latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,confidence,version,bright_t31,frp,daynight
-8.95,-75.50,330.1,1.2,1.1,2026-10-15,1530,Aqua,85,6.1NRT,300.1,40.5,D
-8.951,-75.502,320.0,1.2,1.1,2026-10-15,1530,Terra,20,6.1NRT,299.0,8.0,D
"""


def test_utm_lima():
    z, h, e, n = geo.latlon_a_utm(-12.0464, -77.0428)
    assert (z, h) == (18, "S")
    assert 277_000 < e < 278_500  # validado con pyproj (EPSG:32718)
    assert 8_666_000 < n < 8_669_000


def test_ubicacion_departamentos():
    lim = geo.limites()
    assert lim.ubicar(-71.97, -13.52)[0] == "Cusco"
    assert lim.ubicar(-77.03, -12.05)[0] == "Lima"
    assert lim.ubicar(-69.19, -12.59)[0] == "Madre de Dios"
    assert lim.ubicar(-60.0, -10.0) == (None, None)  # Brasil


def test_parseo_csv_viirs_filtra_peru():
    dets = firms.parsear_csv(CSV_VIIRS, "VIIRS_NOAA20")
    assert len(dets) == 4  # descarta el punto en Brasil
    d = dets[0]
    assert d["confianza"] == "nominal" and d["satelite"] == "NOAA-20"
    assert d["t"] == datetime(2026, 10, 15, 6, 12, tzinfo=timezone.utc)
    assert dets[1]["confianza"] == "alta" and dets[2]["confianza"] == "baja"


def test_parseo_csv_modis_confianza_numerica():
    dets = firms.parsear_csv(CSV_MODIS, "MODIS")
    assert [d["confianza"] for d in dets] == ["alta", "baja"]
    assert {d["satelite"] for d in dets} == {"Aqua", "Terra"}
    assert dets[0]["sensor"] == "MODIS"


def test_csv_con_error_de_clave():
    with pytest.raises(firms.FirmsError):
        firms.parsear_csv("Invalid MAP_KEY.", "VIIRS_NOAA20")


def test_agrupamiento_y_area():
    dets = firms.parsear_csv(CSV_VIIRS, "VIIRS_NOAA20") + firms.parsear_csv(CSV_MODIS, "MODIS")
    filtradas, incendios = clustering.procesar(dets, ahora=AHORA)
    assert len(incendios) == 3  # Cusco (3 focos), Ucayali (2 focos MODIS), Arequipa (1 foco)
    cusco = next(i for i in incendios if i["departamento"] == "Cusco")
    assert cusco["detecciones"] == 3
    assert cusco["frp_total"] == pytest.approx(40.5)
    assert cusco["nivel_texto"] == "Moderado"
    assert cusco["estado"] == "activo"
    # tres píxeles VIIRS (~0,14 km² c/u) con solapamiento parcial: entre 10 y 45 ha
    assert 10 < cusco["area_pixeles_ha"] < 45
    ucayali = next(i for i in incendios if i["departamento"] == "Ucayali")
    # dos píxeles MODIS casi coincidentes: la unión no duplica el área (~1,3 km²)
    assert 100 < ucayali["area_pixeles_ha"] < 200
    arequipa = next(i for i in incendios if i["departamento"] == "Arequipa")
    assert arequipa["estado"] == "anterior"
    assert all(d.get("_incendio") for d in filtradas)


def test_filtro_confianza():
    dets = firms.parsear_csv(CSV_VIIRS, "VIIRS_NOAA20")
    filtradas, _ = clustering.procesar(dets, ahora=AHORA, confianza_min="alta")
    assert len(filtradas) == 1


def test_niveles_frp():
    assert clustering.nivel_por_frp(5)[1] == "Bajo"
    assert clustering.nivel_por_frp(60)[1] == "Alto"
    assert clustering.nivel_por_frp(1500)[1] == "Extremo"


def test_demo_produce_incendios_en_varios_departamentos():
    dets, estado = firms.focos_demo("48h", list(firms.FUENTES), AHORA)
    _, incendios = clustering.procesar(dets, ahora=AHORA)
    deps = {i["departamento"] for i in incendios}
    assert len(deps) >= 8
    assert all(e["ok"] for e in estado)


def test_indices_meteorologicos():
    a, _ = weather.indice_angstrom(32, 20)
    assert a == pytest.approx(0.5)
    nivel, txt = weather.peligro_meteorologico(a, 35, 50, 12, 0)
    assert txt == "Extremo"
    nivel, txt = weather.peligro_meteorologico(5.0, 5, 8, 0, 3.0)
    assert txt == "Bajo"
    assert weather.categoria_aqi(160) == "Dañina"


def test_cardinal_y_destino():
    assert geo.cardinal(0) == "N" and geo.cardinal(225) == "SO" and geo.cardinal(350) == "N"
    lat, lon = geo.destino(-12.0, -72.0, 90, 10)
    assert geo.haversine_km(-12.0, -72.0, lat, lon) == pytest.approx(10, rel=1e-3)


def test_interpretar_overpass():
    datos = {"elements": [
        {"type": "area", "tags": {"boundary": "administrative", "admin_level": "8", "name": "Echarati"}},
        {"type": "area", "tags": {"boundary": "administrative", "admin_level": "6", "name": "La Convención"}},
        {"type": "area", "tags": {"boundary": "protected_area", "name": "Santuario Nacional Megantoni"}},
        {"type": "node", "lat": -12.80, "lon": -72.70, "tags": {"amenity": "fire_station", "name": "Cía. B-1"}},
        {"type": "way", "center": {"lat": -12.90, "lon": -72.70}, "tags": {"amenity": "hospital"}},
        {"type": "way", "tags": {"waterway": "river", "name": "Río Urubamba"},
         "geometry": [{"lat": -12.84, "lon": -72.80}, {"lat": -12.84, "lon": -72.60}]},
        {"type": "way", "tags": {"highway": "track"},
         "geometry": [{"lat": -12.86, "lon": -72.73}, {"lat": -12.86, "lon": -72.71}]},
    ]}
    r = context.interpretar(datos, -12.85, -72.72)
    assert r["admin"] == {"distrito": "Echarati", "provincia": "La Convención"}
    assert r["protegidas"][0]["nombre"].startswith("Santuario")
    assert r["recursos"]["bomberos"][0]["rumbo_txt"] == "NNE"
    rio = r["recursos"]["agua"][0]
    assert rio["detalle"] == "Río" and rio["dist_km"] == pytest.approx(1.1, abs=0.05)
    assert r["recursos"]["vias"][0]["dist_km"] < 1.2
    assert r["recursos"]["salud"][0]["sin_nombre"] is True


class _Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def respuesta_open_meteo_falsa():
    """Estructura real de Open-Meteo con valores sintéticos (2 semanas pasadas + 3 días)."""
    from datetime import date, timedelta as td
    hoy = date(2026, 10, 15)
    dias = [(hoy - td(days=14 - k)).isoformat() for k in range(17)]
    lluvia = [0.0] * 17
    lluvia[2] = 6.5  # última lluvia hace 12 días
    horas = [f"{d}T{h:02d}:00" for d in dias for h in range(24)]
    n = len(horas)
    return {
        "elevation": 1040.0,
        "current": {"time": "2026-10-15T12:00", "temperature_2m": 31.0, "relative_humidity_2m": 22,
                    "apparent_temperature": 30.0, "precipitation": 0.0, "weather_code": 1, "cloud_cover": 20,
                    "wind_speed_10m": 24.0, "wind_direction_10m": 45.0, "wind_gusts_10m": 41.0},
        "hourly": {"time": horas, "temperature_2m": [25.0] * n, "relative_humidity_2m": [40] * n,
                   "wind_speed_10m": [10 + (k % 24) for k in range(n)], "wind_direction_10m": [45] * n,
                   "wind_gusts_10m": [20 + (k % 24) for k in range(n)], "precipitation_probability": [5] * n,
                   "vapour_pressure_deficit": [2.1] * n},
        "daily": {"time": dias, "precipitation_sum": lluvia, "temperature_2m_max": [32] * 17,
                  "relative_humidity_2m_min": [20] * 17, "wind_speed_10m_max": [30] * 17},
    }


class SesionFalsa:
    def get(self, url, params=None, **kw):
        if "air-quality" in url:
            return _Resp({"current": {"pm2_5": 48.0, "pm10": 60.0, "carbon_monoxide": 420.0, "us_aqi": 131}})
        return _Resp(respuesta_open_meteo_falsa())


def test_detalle_meteorologico():
    from vigia.cache import cache
    cache.clear()
    m = weather.detalle(-12.85, -72.72, session=SesionFalsa())
    assert m["dias_sin_lluvia"] == 11  # del 4 al 14 de octubre
    assert m["propagacion"]["rumbo_txt"] == "SO"  # viento del NE empuja hacia el SO
    assert m["propagacion"]["proyeccion_3h_km"] == pytest.approx(7.2)
    assert m["peligro_txt"] in ("Muy alto", "Extremo")
    assert len(m["pronostico"]["hora"]) == 24 and m["pronostico"]["hora"][0] == "12:00"
    assert m["viento_max_24h"]["valor"] == 33
    assert m["aire"]["aqi_txt"] == "Dañina para grupos sensibles"
