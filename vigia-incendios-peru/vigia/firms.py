"""Descarga y normalización de focos de calor de NASA FIRMS (VIIRS y MODIS).

Dos modos de acceso:
  * API de área (requiere MAP_KEY gratuito): consulta solo la caja del Perú, hasta 5 días por llamada.
  * Archivos CSV públicos por región (sin clave): Sudamérica 24 h / 48 h / 7 días, filtrados al Perú.
"""

import csv
import io
import math
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from .errores import resumen
from .geo import PERU_BBOX, en_bbox_peru

FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov"
USER_AGENT = "VigiaIncendiosPeru/1.0 (monitoreo de focos de calor; uso humanitario)"

FUENTES = {
    "VIIRS_NOAA20": {
        "api": "VIIRS_NOAA20_NRT",
        "csv": "/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_South_America_{rango}.csv",
        "nombre": "VIIRS NOAA-20",
        "sensor": "VIIRS",
        "res_km": 0.375,
    },
    "VIIRS_NOAA21": {
        "api": "VIIRS_NOAA21_NRT",
        "csv": "/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_South_America_{rango}.csv",
        "nombre": "VIIRS NOAA-21",
        "sensor": "VIIRS",
        "res_km": 0.375,
    },
    "VIIRS_SNPP": {
        "api": "VIIRS_SNPP_NRT",
        "csv": "/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_South_America_{rango}.csv",
        "nombre": "VIIRS Suomi NPP",
        "sensor": "VIIRS",
        "res_km": 0.375,
    },
    "MODIS": {
        "api": "MODIS_NRT",
        "csv": "/data/active_fire/modis-c6.1/csv/MODIS_C6_1_South_America_{rango}.csv",
        "nombre": "MODIS Terra/Aqua",
        "sensor": "MODIS",
        "res_km": 1.0,
    },
}

RANGOS_HORAS = {"24h": 24, "48h": 48, "7d": 168}

_CONF_TEXTO = {"l": "baja", "low": "baja", "n": "nominal", "nominal": "nominal", "h": "alta", "high": "alta"}


class FirmsError(Exception):
    pass


def _confianza(valor: str) -> str:
    v = (valor or "").strip().lower()
    if v.isdigit():
        n = int(v)
        return "baja" if n < 30 else ("alta" if n >= 80 else "nominal")
    return _CONF_TEXTO.get(v, "nominal")


def _satelite(fuente: str, crudo: str) -> str:
    c = (crudo or "").strip().upper()
    if fuente == "MODIS":
        return "Aqua" if c.startswith("A") else "Terra"
    return FUENTES[fuente]["nombre"].replace("VIIRS ", "")


def _float(row, *claves, defecto=0.0):
    for k in claves:
        v = row.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except ValueError:
                pass
    return defecto


def parsear_csv(texto: str, fuente: str):
    """Convierte el CSV de FIRMS en una lista de detecciones normalizadas dentro de la caja del Perú."""
    primera = texto.lstrip()[:200].lower()
    if "latitude" not in primera:
        raise FirmsError(texto.strip()[:160] or "respuesta vacía")
    res_km = FUENTES[fuente]["res_km"]
    out = []
    for row in csv.DictReader(io.StringIO(texto)):
        try:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
        except (KeyError, ValueError):
            continue
        if not en_bbox_peru(lon, lat, margen=0.3):
            continue
        hhmm = str(row.get("acq_time", "0")).strip().zfill(4)
        try:
            t = datetime.strptime(row["acq_date"].strip() + hhmm, "%Y-%m-%d%H%M").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        out.append({
            "lat": lat,
            "lon": lon,
            "frp": _float(row, "frp"),
            "brillo": _float(row, "bright_ti4", "brightness"),
            "scan": _float(row, "scan", defecto=res_km) or res_km,
            "track": _float(row, "track", defecto=res_km) or res_km,
            "t": t,
            "satelite": _satelite(fuente, row.get("satellite", "")),
            "fuente": fuente,
            "sensor": FUENTES[fuente]["sensor"],
            "confianza": _confianza(row.get("confidence", "")),
            "dia_noche": (row.get("daynight") or "D").strip().upper()[:1] or "D",
        })
    return out


def _get(session, url, timeout=60):
    r = session.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    if r.status_code != 200:
        raise FirmsError(f"HTTP {r.status_code} en {url.split('?')[0][:120]}")
    return r.text


def _area_api(session, map_key, fuente, dias, fecha=None):
    w, s, e, n = PERU_BBOX
    url = f"{FIRMS_BASE}/api/area/csv/{map_key}/{FUENTES[fuente]['api']}/{w},{s},{e},{n}/{dias}"
    if fecha:
        url += f"/{fecha}"
    return parsear_csv(_get(session, url), fuente)


def _descargar_fuente(session, fuente, rango, map_key, ahora):
    if map_key:
        if rango == "24h":
            return _area_api(session, map_key, fuente, 2)
        if rango == "48h":
            return _area_api(session, map_key, fuente, 3)
        # 7 días: la API admite como máximo 5 días por consulta
        inicio = (ahora - timedelta(days=7)).strftime("%Y-%m-%d")
        return _area_api(session, map_key, fuente, 5, inicio) + _area_api(session, map_key, fuente, 3)
    url = FIRMS_BASE + FUENTES[fuente]["csv"].format(rango=rango)
    return parsear_csv(_get(session, url, timeout=90), fuente)


def obtener_focos(rango: str, fuentes, map_key: str = "", session=None, ahora=None):
    """Devuelve (detecciones, estado_por_fuente)."""
    if rango not in RANGOS_HORAS:
        raise ValueError("rango inválido")
    ahora = ahora or datetime.now(timezone.utc)
    session = session or requests.Session()
    desde = ahora - timedelta(hours=RANGOS_HORAS[rango])
    estado = []
    detecciones = []

    def tarea(f):
        try:
            return f, _descargar_fuente(session, f, rango, map_key, ahora), None
        except Exception as exc:  # noqa: BLE001 - se informa al usuario por fuente
            return f, [], resumen(exc)

    with ThreadPoolExecutor(max_workers=4) as ex:
        for f, dets, err in ex.map(tarea, [f for f in fuentes if f in FUENTES]):
            dets = [d for d in dets if d["t"] >= desde]
            detecciones.extend(dets)
            estado.append({
                "fuente": f,
                "nombre": FUENTES[f]["nombre"],
                "ok": err is None,
                "focos": len(dets),
                "error": err,
                "modo": "api" if map_key else "csv",
            })
    return detecciones, estado


# --------------------------------------------------------------------------------------
# Datos de demostración (para explorar la interfaz sin conexión a FIRMS)
# --------------------------------------------------------------------------------------

_ZONAS_DEMO = [
    (-12.86, -72.72, 34, 1.4),   # La Convención, Cusco
    (-13.62, -71.95, 9, 0.6),    # Quispicanchi, Cusco
    (-14.18, -69.47, 18, 1.0),   # Sandia, Puno
    (-8.96, -75.52, 26, 1.2),    # Padre Abad, Ucayali
    (-12.74, -69.33, 22, 1.6),   # Tambopata, Madre de Dios
    (-5.66, -78.44, 7, 0.5),     # Bagua, Amazonas
    (-6.06, -76.96, 12, 0.8),    # Moyobamba, San Martín
    (-10.57, -75.24, 5, 0.4),    # Oxapampa, Pasco
    (-11.27, -74.63, 15, 0.9),   # Satipo, Junín
    (-13.69, -73.41, 4, 0.3),    # Andahuaylas, Apurímac
    (-12.92, -74.26, 3, 0.3),    # Huanta, Ayacucho
    (-7.16, -78.56, 2, 0.2),     # Cajamarca
    (-9.56, -77.62, 6, 0.4),     # Huaraz, Áncash
    (-8.38, -74.55, 40, 2.0),    # Coronel Portillo, Ucayali
]


def focos_demo(rango: str, fuentes, ahora=None):
    ahora = ahora or datetime.now(timezone.utc)
    horas = RANGOS_HORAS[rango]
    rnd = random.Random(int(ahora.timestamp() // 3600))  # estable durante una hora
    fuentes = [f for f in fuentes if f in FUENTES] or list(FUENTES)
    dets = []
    for i, (lat, lon, n, radio_km) in enumerate(_ZONAS_DEMO):
        edad_h = rnd.uniform(0.5, horas * 0.9) if i % 3 else rnd.uniform(0.5, 10)
        intensidad = rnd.lognormvariate(1.6, 0.9)
        for _ in range(n):
            ang = rnd.uniform(0, 2 * math.pi)
            r = abs(rnd.gauss(0, radio_km / 2))
            dlat = (r * math.cos(ang)) / 110.574
            dlon = (r * math.sin(ang)) / (111.32 * math.cos(math.radians(lat)))
            f = rnd.choice(fuentes)
            res = FUENTES[f]["res_km"]
            dets.append({
                "lat": lat + dlat,
                "lon": lon + dlon,
                "frp": round(max(0.3, rnd.lognormvariate(math.log(intensidad + 1), 0.8)), 2),
                "brillo": round(rnd.uniform(310, 367), 1),
                "scan": round(res * rnd.uniform(1.0, 1.6), 2),
                "track": round(res * rnd.uniform(1.0, 1.3), 2),
                "t": ahora - timedelta(hours=max(0.2, edad_h + rnd.uniform(-2, 2))),
                "satelite": _satelite(f, rnd.choice("TA")),
                "fuente": f,
                "sensor": FUENTES[f]["sensor"],
                "confianza": rnd.choice(["nominal", "nominal", "alta", "baja"]),
                "dia_noche": rnd.choice("DN"),
            })
    estado = [{"fuente": f, "nombre": FUENTES[f]["nombre"], "ok": True,
               "focos": sum(1 for d in dets if d["fuente"] == f), "error": None, "modo": "demo"}
              for f in fuentes]
    return dets, estado
