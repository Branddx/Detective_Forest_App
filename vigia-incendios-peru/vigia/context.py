"""Contexto territorial alrededor de un incendio con OpenStreetMap (API Overpass, sin clave).

Devuelve el distrito/provincia/región según OSM, áreas protegidas que contienen el punto y los
recursos más cercanos: compañías de bomberos, establecimientos de salud, comisarías, poblados,
fuentes de agua, aeródromos/helipuertos y la vía de acceso más próxima.
"""

import json
import math
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timezone

import requests

from . import geo
from .cache import cache
from .errores import resumen

# Servidores públicos de Overpass. Se pueden anteponer otros (por ejemplo, uno propio) con
# OVERPASS_URLS=url1,url2 en .env.
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
]
SIN_INDICE_DE_AREAS = {"overpass.openstreetmap.fr"}  # no resuelven is_in: solo se les piden recursos
USER_AGENT = "VigiaIncendiosPeru/1.0 (contexto de emergencias)"
ESPERA_ESCALONADA_S = 8.0    # si un servidor no contesta en 8 s se pregunta además al siguiente
PLAZO_TOTAL_S = 60.0
ESPERA_AREAS_S = 10.0        # tras obtener los recursos, cuánto más se espera por distrito y áreas protegidas
ENFRIAMIENTO_S = (60, 600)   # pausa de un servidor que falla: 1 min, se duplica hasta 10 min
RECUERDO_FALLO_S = 45        # no se repite una consulta fallida antes de 45 s
VIGENCIA_RESPALDO_S = 30 * 24 * 3600
CACHE_DIR = os.environ.get("CACHE_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")


def endpoints():
    propios = [u.strip() for u in os.environ.get("OVERPASS_URLS", "").split(",") if u.strip()]
    return list(dict.fromkeys(propios + ENDPOINTS))


class OverpassError(RuntimeError):
    pass


def _bbox_alrededor(lat, lon, km):
    dlat = km / geo.KM_PER_DEG_LAT
    dlon = km / (geo.KM_PER_DEG_LON_EQ * math.cos(math.radians(lat)))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def consulta_areas(lat, lon):
    """Distrito, provincia y áreas protegidas que contienen el punto (requiere el índice de áreas)."""
    p = f"{lat:.5f},{lon:.5f}"
    return f"""
[out:json][timeout:20];
is_in({p})->.a;
area.a[boundary~"^(administrative|protected_area|national_park)$"];
out tags;
area.a[leisure=nature_reserve];
out tags;
"""


def consulta_recursos(lat, lon, radio_km=30):
    r = int(radio_km * 1000)
    r_aereo = int(max(radio_km, 60) * 1000)
    r_pob = int(min(radio_km, 20) * 1000)
    r_agua = 10000
    s, w, n, e = _bbox_alrededor(lat, lon, 10)
    s2, w2, n2, e2 = _bbox_alrededor(lat, lon, 5)
    p = f"{lat:.5f},{lon:.5f}"
    return f"""
[out:json][timeout:25];
(
  nwr(around:{r},{p})[amenity=fire_station];
  nwr(around:{r},{p})[amenity~"^(hospital|clinic|doctors)$"];
  nwr(around:{r},{p})[healthcare~"^(hospital|clinic|centre|health_post)$"];
  nwr(around:{r},{p})[amenity=police];
  nwr(around:{r_aereo},{p})[aeroway~"^(aerodrome|airstrip|helipad)$"];
  node(around:{r_pob},{p})[place~"^(city|town|village|hamlet)$"];
  nwr(around:{r_agua},{p})[natural=water];
  nwr(around:{r_agua},{p})[landuse=reservoir];
);
out tags center 400;
way(around:{r_agua},{p})[waterway~"^(river|stream|canal)$"];
out tags geom({s:.5f},{w:.5f},{n:.5f},{e:.5f}) 80;
way(around:5000,{p})[highway~"^(trunk|primary|secondary|tertiary|unclassified|residential|track|service)$"];
out tags geom({s2:.5f},{w2:.5f},{n2:.5f},{e2:.5f}) 120;
"""


def _dist_segmento_km(lat, lon, a, b):
    """Distancia punto-segmento en un plano local (km) y punto más cercano (lat, lon)."""
    kx = geo.KM_PER_DEG_LON_EQ * math.cos(math.radians(lat))
    ax, ay = (a[1] - lon) * kx, (a[0] - lat) * geo.KM_PER_DEG_LAT
    bx, by = (b[1] - lon) * kx, (b[0] - lat) * geo.KM_PER_DEG_LAT
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
    px, py = ax + t * dx, ay + t * dy
    return math.hypot(px, py), (lat + py / geo.KM_PER_DEG_LAT, lon + px / kx)


def _punto_mas_cercano(lat, lon, geometria):
    mejor = (float("inf"), None)
    pts = [(g["lat"], g["lon"]) for g in geometria if g]
    if len(pts) == 1:
        return geo.haversine_km(lat, lon, *pts[0]), pts[0]
    for a, b in zip(pts, pts[1:]):
        d, p = _dist_segmento_km(lat, lon, a, b)
        if d < mejor[0]:
            mejor = (d, p)
    return mejor


TIPOS_VIA = {
    "trunk": "Carretera nacional", "primary": "Carretera principal", "secondary": "Carretera secundaria",
    "tertiary": "Carretera terciaria", "unclassified": "Camino vecinal", "residential": "Calle",
    "track": "Trocha", "service": "Vía de servicio",
}


SUPERFICIES = {
    "asphalt": "asfaltada", "paved": "asfaltada", "concrete": "concreto", "unpaved": "sin asfaltar",
    "gravel": "afirmado", "fine_gravel": "afirmado", "compacted": "afirmado", "dirt": "tierra",
    "ground": "tierra", "earth": "tierra", "mud": "barro", "sand": "arena",
}


def _categoria(tags):
    if tags.get("amenity") == "fire_station":
        return "bomberos"
    if tags.get("amenity") in ("hospital", "clinic", "doctors") or tags.get("healthcare"):
        return "salud"
    if tags.get("amenity") == "police":
        return "policia"
    if tags.get("aeroway"):
        return "aereo"
    if tags.get("place"):
        return "poblados"
    if tags.get("natural") == "water" or tags.get("landuse") == "reservoir" or tags.get("waterway"):
        return "agua"
    if tags.get("highway"):
        return "vias"
    return None


_NOMBRE_DEFECTO = {
    "bomberos": "Compañía de bomberos",
    "salud": "Establecimiento de salud",
    "policia": "Comisaría",
    "aereo": "Pista / helipuerto",
    "poblados": "Centro poblado",
    "agua": "Cuerpo de agua",
    "vias": "Vía sin nombre",
}


def interpretar(datos, lat, lon):
    admin = {}
    protegidas = []
    recursos = {k: [] for k in _NOMBRE_DEFECTO}
    for el in datos.get("elements", []):
        tags = el.get("tags", {}) or {}
        if el.get("type") == "area":
            nombre = tags.get("name:es") or tags.get("name")
            if not nombre:
                continue
            if tags.get("boundary") == "administrative":
                nivel = tags.get("admin_level")
                if nivel == "4":
                    admin["region"] = nombre
                elif nivel == "6":
                    admin["provincia"] = nombre
                elif nivel == "8":
                    admin["distrito"] = nombre
            else:
                protegidas.append({
                    "nombre": nombre,
                    "tipo": tags.get("protection_title") or tags.get("designation") or "Área protegida",
                })
            continue
        cat = _categoria(tags)
        if not cat:
            continue
        if "geometry" in el:
            dist, punto = _punto_mas_cercano(lat, lon, el["geometry"])
        else:
            c = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
            if c.get("lat") is None:
                continue
            punto = (c["lat"], c["lon"])
            dist = geo.haversine_km(lat, lon, *punto)
        if punto is None:
            continue
        nombre = tags.get("name") or tags.get("official_name") or tags.get("ref")
        detalle = None
        if cat == "vias":
            detalle = TIPOS_VIA.get(tags.get("highway"), "Vía")
            sup = SUPERFICIES.get(tags.get("surface"))
            if sup:
                detalle += f" · {sup}"
        elif cat == "poblados":
            detalle = {"city": "Ciudad", "town": "Pueblo", "village": "Centro poblado", "hamlet": "Caserío"}.get(tags["place"])
            if tags.get("population"):
                detalle += f" · {tags['population']} hab."
        elif cat == "salud":
            detalle = tags.get("healthcare") or tags.get("amenity")
            detalle = {"hospital": "Hospital", "clinic": "Centro de salud", "centre": "Centro de salud",
                       "doctors": "Consultorio", "health_post": "Puesto de salud"}.get(detalle, "Salud")
        elif cat == "aereo":
            detalle = {"aerodrome": "Aeródromo", "airstrip": "Pista de aterrizaje", "helipad": "Helipuerto"}.get(tags["aeroway"])
        elif cat == "agua":
            detalle = {"river": "Río", "stream": "Quebrada", "canal": "Canal"}.get(tags.get("waterway"), "Laguna / reservorio")
        rumbo = geo.rumbo_grados(lat, lon, punto[0], punto[1])
        recursos[cat].append({
            "nombre": nombre or _NOMBRE_DEFECTO[cat],
            "sin_nombre": not nombre,
            "detalle": detalle,
            "telefono": tags.get("phone") or tags.get("contact:phone"),
            "lat": round(punto[0], 5),
            "lon": round(punto[1], 5),
            "dist_km": round(dist, 2),
            "rumbo_txt": geo.cardinal(rumbo),
        })
    limites_por_cat = {"bomberos": 5, "salud": 5, "policia": 3, "aereo": 3, "poblados": 6, "agua": 4, "vias": 2}
    for cat, lista in recursos.items():
        lista.sort(key=lambda x: (x["dist_km"], x["sin_nombre"]))
        # elimina duplicados por nombre (ej. un río dividido en varios tramos)
        vistos, unicos = set(), []
        for item in lista:
            k = (item["nombre"], item["detalle"]) if not item["sin_nombre"] else (item["lat"], item["lon"])
            if k in vistos:
                continue
            vistos.add(k)
            unicos.append(item)
        recursos[cat] = unicos[: limites_por_cat[cat]]
    return {"admin": admin, "protegidas": protegidas, "recursos": recursos}


# --------------------------------------------------------------------------------------
# Consultas tolerantes a fallos de los servidores públicos
# --------------------------------------------------------------------------------------

_estado = {}            # servidor -> (pausado_hasta, fallos seguidos)
_estado_lock = threading.Lock()


def _host(url):
    return url.split("/")[2]


def _disponible(host):
    with _estado_lock:
        return _estado.get(host, (0, 0))[0] <= time.time()


def _anotar(host, ok):
    with _estado_lock:
        if ok:
            _estado.pop(host, None)
            return
        fallos = _estado.get(host, (0, 0))[1] + 1
        pausa = min(ENFRIAMIENTO_S[1], ENFRIAMIENTO_S[0] * 2 ** (fallos - 1))
        _estado[host] = (time.time() + pausa, fallos)


def validar(r):
    """JSON de una respuesta de Overpass, o OverpassError si el servidor falló aunque responda HTTP 200."""
    if r.status_code != 200:
        texto = (r.text or "").lower()
        motivo = "saturado" if r.status_code in (429, 502, 503, 504) or "capacity" in texto else "error interno"
        raise OverpassError(f"HTTP {r.status_code} ({motivo})")
    try:
        datos = r.json()
    except ValueError as exc:
        raise OverpassError("respuesta que no es JSON") from exc
    remark = (datos.get("remark") or "").lower()
    if "error" in remark or "timed out" in remark or "out of memory" in remark:
        raise OverpassError(f"error del servidor: {datos['remark'][:90]}")
    return datos


def pedir(consulta, session, necesita_areas=True, servidores=None):
    """Envía la consulta a los servidores disponibles, escalonados: el primero en responder bien gana.

    Un servidor que falla se pausa (1 min, luego 2, 4… hasta 10) para no insistir mientras está saturado.
    Devuelve (datos, servidor); lanza OverpassError con el detalle de cada intento si todos fallan.
    """
    candidatos = [u for u in (servidores or endpoints())
                  if not (necesita_areas and _host(u) in SIN_INDICE_DE_AREAS)]
    activos = [u for u in candidatos if _disponible(_host(u))] or candidatos[:1]
    pausados = len(candidatos) - len(activos)
    errores = []
    inicio = time.time()

    def intento(url):
        try:
            r = session.post(url, data={"data": consulta}, timeout=(8, 45), headers={"User-Agent": USER_AGENT})
            datos = validar(r)
        except Exception as exc:  # noqa: BLE001
            _anotar(_host(url), False)
            raise OverpassError(f"{_host(url)}: {resumen(exc) if not isinstance(exc, OverpassError) else exc}") from exc
        _anotar(_host(url), True)
        return datos, _host(url)

    ex = ThreadPoolExecutor(max_workers=len(activos) or 1)
    try:
        pendientes = set()
        cola = list(activos)
        while cola or pendientes:
            if cola:
                pendientes.add(ex.submit(intento, cola.pop(0)))
            restante = PLAZO_TOTAL_S - (time.time() - inicio)
            if restante <= 0:
                errores.append("plazo agotado")
                break
            hechos, pendientes = wait(pendientes, timeout=min(ESPERA_ESCALONADA_S, restante) if cola else restante,
                                      return_when=FIRST_COMPLETED)
            for f in hechos:
                try:
                    return f.result()
                except OverpassError as exc:
                    errores.append(str(exc))
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    if pausados:
        errores.append(f"{pausados} en pausa por fallos recientes")
    raise OverpassError("; ".join(errores) or "sin servidores disponibles")


def _archivo_respaldo(key):
    return os.path.join(CACHE_DIR, "osm", key.replace(":", "_") + ".json")


def _guardar_respaldo(key, res):
    try:
        os.makedirs(os.path.dirname(_archivo_respaldo(key)), exist_ok=True)
        with open(_archivo_respaldo(key), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
    except OSError:
        pass  # sin disco escribible se sigue funcionando con la caché en memoria


def _leer_respaldo(key):
    try:
        if time.time() - os.path.getmtime(_archivo_respaldo(key)) > VIGENCIA_RESPALDO_S:
            return None
        with open(_archivo_respaldo(key), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def contexto(lat, lon, radio_km=30, session=None, ttl=6 * 3600):
    """Recursos y límites alrededor del punto. Si Overpass no responde, usa la última consulta buena
    guardada en disco (hasta 30 días) y lo indica con "desactualizado"."""
    key = f"ctx:{lat:.3f}:{lon:.3f}:{radio_km}"
    hit = cache.get(key)
    if hit:
        return hit
    fallo = cache.get("fallo:" + key)
    if fallo:
        raise OverpassError(fallo)
    session = session or requests.Session()
    ex = ThreadPoolExecutor(max_workers=2)
    try:
        f_rec = ex.submit(pedir, consulta_recursos(lat, lon, radio_km), session, False)
        f_area = ex.submit(pedir, consulta_areas(lat, lon), session, True)
        try:
            datos, servidor = f_rec.result()
        except OverpassError as exc:
            respaldo = _leer_respaldo(key)
            if respaldo:
                respaldo.update({"desactualizado": True, "error": str(exc)})
                return cache.set(key, respaldo, 600)
            mensaje = f"Overpass no respondió ({exc})"
            cache.set("fallo:" + key, mensaje, RECUERDO_FALLO_S)
            raise OverpassError(mensaje) from exc
        avisos = []
        try:
            areas, _ = f_area.result(timeout=ESPERA_AREAS_S)  # con los recursos listos no se espera mucho más
            datos = {"elements": areas.get("elements", []) + datos.get("elements", [])}
        except OverpassError as exc:
            avisos.append(f"Distrito y áreas protegidas no disponibles ({exc}).")
        except FuturesTimeout:
            avisos.append("Distrito y áreas protegidas no disponibles (los servidores tardan demasiado).")
    finally:
        ex.shutdown(wait=False)
    res = interpretar(datos, lat, lon)
    res.update({"radio_km": radio_km, "servidor": servidor, "avisos": avisos, "desactualizado": False,
                "consultado": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")})
    if not avisos:
        _guardar_respaldo(key, res)
    return cache.set(key, res, ttl if not avisos else 900)
