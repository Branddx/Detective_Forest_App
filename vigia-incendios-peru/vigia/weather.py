"""Meteorología y calidad del aire en el punto del incendio (Open-Meteo, sin clave)."""

from datetime import datetime, timezone

import requests

from . import geo
from .cache import cache

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIRE_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
USER_AGENT = "VigiaIncendiosPeru/1.0"
TZ = "America/Lima"

CODIGOS_TIEMPO = {
    0: "Despejado", 1: "Mayormente despejado", 2: "Parcialmente nublado", 3: "Nublado",
    45: "Niebla", 48: "Niebla con escarcha", 51: "Llovizna ligera", 53: "Llovizna", 55: "Llovizna intensa",
    61: "Lluvia ligera", 63: "Lluvia", 65: "Lluvia intensa", 66: "Lluvia helada", 67: "Lluvia helada intensa",
    71: "Nevada ligera", 73: "Nevada", 75: "Nevada intensa", 77: "Granizo fino",
    80: "Chubascos ligeros", 81: "Chubascos", 82: "Chubascos fuertes",
    85: "Chubascos de nieve", 86: "Chubascos de nieve fuertes",
    95: "Tormenta eléctrica", 96: "Tormenta con granizo", 99: "Tormenta fuerte con granizo",
}


def _clave(lat, lon, tipo):
    return f"{tipo}:{lat:.2f}:{lon:.2f}"


def viento_lote(puntos, session=None, ttl=1800):
    """Viento y condiciones actuales para muchos puntos [(lat, lon), ...] con pocas llamadas."""
    session = session or requests.Session()
    salida = [cache.get(_clave(la, lo, "act")) for la, lo in puntos]
    pendientes = [i for i, v in enumerate(salida) if v is None]
    for k in range(0, len(pendientes), 50):
        lote = pendientes[k:k + 50]
        params = {
            "latitude": ",".join(f"{puntos[i][0]:.3f}" for i in lote),
            "longitude": ",".join(f"{puntos[i][1]:.3f}" for i in lote),
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
            "wind_speed_unit": "kmh",
            "timezone": TZ,
        }
        r = session.get(FORECAST_URL, params=params, timeout=30, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        datos = r.json()
        if isinstance(datos, dict):
            datos = [datos]
        for i, d in zip(lote, datos):
            c = d.get("current") or {}
            v = {
                "temp": c.get("temperature_2m"),
                "hr": c.get("relative_humidity_2m"),
                "viento": c.get("wind_speed_10m"),
                "dir": c.get("wind_direction_10m"),
                "rafagas": c.get("wind_gusts_10m"),
            }
            if v["dir"] is not None:
                v["avance"] = round((v["dir"] + 180) % 360)
                v["avance_txt"] = geo.cardinal(v["avance"])
            salida[i] = cache.set(_clave(puntos[i][0], puntos[i][1], "act"), v, ttl)
    return salida


def indice_angstrom(temp_c, hr):
    if temp_c is None or hr is None:
        return None, None
    a = hr / 20.0 + (27.0 - temp_c) / 10.0
    if a < 2.0:
        txt = "Muy favorable al fuego"
    elif a < 2.5:
        txt = "Favorable al fuego"
    elif a < 4.0:
        txt = "Poco favorable"
    else:
        txt = "Desfavorable al fuego"
    return round(a, 2), txt


def peligro_meteorologico(angstrom, viento, rafagas, dias_sin_lluvia, lluvia_actual):
    """Índice compuesto orientativo (0-5). No reemplaza los avisos oficiales de SENAMHI."""
    puntos = 0
    if angstrom is not None:
        puntos += 3 if angstrom < 2.0 else 2 if angstrom < 2.5 else 1 if angstrom < 4.0 else 0
    v, g = viento or 0, rafagas or 0
    if v >= 30 or g >= 45:
        puntos += 2
    elif v >= 15 or g >= 30:
        puntos += 1
    if dias_sin_lluvia is not None and dias_sin_lluvia >= 10:
        puntos += 1
    if (lluvia_actual or 0) > 0.5:
        puntos -= 1
    puntos = max(0, min(5, puntos))
    etiquetas = ["Bajo", "Bajo", "Moderado", "Alto", "Muy alto", "Extremo"]
    return puntos, etiquetas[puntos]


def categoria_aqi(aqi):
    if aqi is None:
        return None
    for limite, txt in [(50, "Buena"), (100, "Moderada"), (150, "Dañina para grupos sensibles"),
                        (200, "Dañina"), (300, "Muy dañina")]:
        if aqi <= limite:
            return txt
    return "Peligrosa"


def detalle(lat, lon, session=None, ttl=1800):
    key = _clave(lat, lon, "det")
    hit = cache.get(key)
    if hit:
        return hit
    session = session or requests.Session()
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "current": ("temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,"
                    "cloud_cover,wind_speed_10m,wind_direction_10m,wind_gusts_10m"),
        "hourly": ("temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
                   "precipitation_probability,vapour_pressure_deficit"),
        "daily": "precipitation_sum,temperature_2m_max,relative_humidity_2m_min,wind_speed_10m_max",
        "past_days": 14,
        "forecast_days": 3,
        "wind_speed_unit": "kmh",
        "timezone": TZ,
    }
    r = session.get(FORECAST_URL, params=params, timeout=30, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    d = r.json()
    cur = d.get("current", {})
    hourly = d.get("hourly", {})
    daily = d.get("daily", {})

    # Próximas 24 horas desde la hora actual
    ahora_txt = (cur.get("time") or "")[:13]
    horas = hourly.get("time", [])
    inicio = next((i for i, t in enumerate(horas) if t[:13] >= ahora_txt), 0)
    fin = min(len(horas), inicio + 24)

    def serie(nombre):
        return (hourly.get(nombre) or [None] * len(horas))[inicio:fin]

    pronostico = {
        "hora": [t[11:16] for t in horas[inicio:fin]],
        "temp": serie("temperature_2m"),
        "hr": serie("relative_humidity_2m"),
        "viento": serie("wind_speed_10m"),
        "dir": serie("wind_direction_10m"),
        "rafagas": serie("wind_gusts_10m"),
        "prob_lluvia": serie("precipitation_probability"),
        "vpd": serie("vapour_pressure_deficit"),
    }

    # Días consecutivos sin lluvia (>= 1 mm), contando desde ayer hacia atrás
    hoy = (cur.get("time") or datetime.now(timezone.utc).isoformat())[:10]
    fechas = daily.get("time", [])
    lluvias = daily.get("precipitation_sum", [])
    pasados = [(f, p) for f, p in zip(fechas, lluvias) if f < hoy]
    dias_sin_lluvia = 0
    for _, p in reversed(pasados):
        if p is not None and p >= 1.0:
            break
        dias_sin_lluvia += 1
    lluvia_7d = round(sum((p or 0) for _, p in pasados[-7:]), 1)

    temp, hr = cur.get("temperature_2m"), cur.get("relative_humidity_2m")
    viento, direccion, rafagas = cur.get("wind_speed_10m"), cur.get("wind_direction_10m"), cur.get("wind_gusts_10m")
    angstrom, angstrom_txt = indice_angstrom(temp, hr)
    nivel_peligro, peligro_txt = peligro_meteorologico(angstrom, viento, rafagas, dias_sin_lluvia,
                                                       cur.get("precipitation"))

    vientos = [v for v in pronostico["viento"] if v is not None]
    idx_max = pronostico["viento"].index(max(vientos)) if vientos else None
    avance = (direccion + 180) % 360 if direccion is not None else None
    velocidad_avance = round(0.10 * viento, 2) if viento is not None else None  # regla del 10 %

    res = {
        "actual": {
            "hora": cur.get("time"),
            "temp": temp,
            "sensacion": cur.get("apparent_temperature"),
            "hr": hr,
            "precipitacion": cur.get("precipitation"),
            "nubosidad": cur.get("cloud_cover"),
            "cielo": CODIGOS_TIEMPO.get(cur.get("weather_code"), "—"),
            "viento": viento,
            "dir": direccion,
            "dir_txt": geo.cardinal(direccion),
            "rafagas": rafagas,
        },
        "elevacion_m": d.get("elevation"),
        "dias_sin_lluvia": dias_sin_lluvia,
        "dias_sin_lluvia_mas": dias_sin_lluvia >= len(pasados) and len(pasados) > 0,
        "lluvia_7d_mm": lluvia_7d,
        "angstrom": angstrom,
        "angstrom_txt": angstrom_txt,
        "peligro": nivel_peligro,
        "peligro_txt": peligro_txt,
        "propagacion": {
            "rumbo": round(avance) if avance is not None else None,
            "rumbo_txt": geo.cardinal(avance),
            "velocidad_kmh": velocidad_avance,
            "proyeccion_3h_km": round(velocidad_avance * 3, 1) if velocidad_avance is not None else None,
        },
        "viento_max_24h": {
            "valor": max(vientos) if vientos else None,
            "hora": pronostico["hora"][idx_max] if idx_max is not None else None,
        },
        "prob_lluvia_max_24h": max([p for p in pronostico["prob_lluvia"] if p is not None], default=None),
        "pronostico": pronostico,
    }

    try:
        ra = session.get(AIRE_URL, params={
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
            "current": "pm2_5,pm10,carbon_monoxide,us_aqi", "timezone": TZ,
        }, timeout=20, headers={"User-Agent": USER_AGENT})
        ra.raise_for_status()
        ca = ra.json().get("current", {})
        res["aire"] = {
            "pm2_5": ca.get("pm2_5"),
            "pm10": ca.get("pm10"),
            "co": ca.get("carbon_monoxide"),
            "aqi": ca.get("us_aqi"),
            "aqi_txt": categoria_aqi(ca.get("us_aqi")),
        }
    except Exception as exc:  # noqa: BLE001
        res["aire"] = {"error": str(exc)}

    return cache.set(key, res, ttl)
