"""Calidad del aire alrededor de un incendio con el Índice de Calidad del Aire (INCA) del Perú.

Escala, ecuaciones, periodos de promedio, colores y cuidados según la R. M. N.° 181-2016-MINAM.
Las concentraciones vienen del modelo CAMS global de Copernicus (vía Open-Meteo, sin clave), que incluye
las emisiones de los incendios detectados por satélite pero tiene celdas de ~40 km: junto al fuego y a
sotavento el humo puede ser mucho más denso de lo que indica el modelo.
"""

import math
from datetime import datetime, timezone

import requests

from . import geo, weather
from .cache import cache

AIRE_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
USER_AGENT = "VigiaIncendiosPeru/1.0 (calidad del aire)"

# Tabla 3 de la R. M. N.° 181-2016-MINAM. Clave de Open-Meteo: (nombre, horas del promedio móvil,
# divisor de la ecuación I = C·100/divisor, límites superiores en µg/m³ de Buena, Moderada y Mala (el
# último es el VUEC), decimales con que la tabla expresa la concentración).
CONTAMINANTES = {
    "pm2_5": ("PM2.5", 24, 25.0, (12.5, 25.0, 125.0), 1),
    "pm10": ("PM10", 24, 150.0, (75.0, 150.0, 250.0), 0),
    "ozone": ("O₃", 8, 120.0, (60.0, 120.0, 210.0), 0),
    "carbon_monoxide": ("CO", 8, 10000.0, (5049.0, 10049.0, 15049.0), 0),
    "nitrogen_dioxide": ("NO₂", 1, 200.0, (100.0, 200.0, 300.0), 0),
    "sulphur_dioxide": ("SO₂", 24, 20.0, (10.0, 20.0, 500.0), 0),
}

CATEGORIAS = {
    "buena": {"nombre": "Buena", "color": "#2e9e44", "banda": "Verde", "rango": "0–50", "orden": 0},
    "moderada": {"nombre": "Moderada", "color": "#f2d600", "banda": "Amarillo", "rango": "51–100", "orden": 1},
    "mala": {"nombre": "Mala", "color": "#f28c28", "banda": "Anaranjado", "rango": "101–VUEC", "orden": 2},
    "umbral": {"nombre": "Umbral de cuidado", "color": "#d7191c", "banda": "Rojo", "rango": "> VUEC", "orden": 3},
}

# Tabla 2 de la R. M. N.° 181-2016-MINAM (texto oficial)
CUIDADOS = {
    "buena": ("La calidad del aire es satisfactoria y no representa un riesgo para la salud.",
              "La calidad del aire es aceptable y cumple con el ECA de Aire. Puede realizar actividades al aire libre."),
    "moderada": ("La población sensible (niños, tercera edad, madres gestantes, personas con enfermedades respiratorias "
                 "crónicas y cardiovasculares) podrían experimentar algunos problemas de salud.",
                 "La calidad del aire es aceptable y cumple con el ECA de Aire. Puede realizar actividades al aire libre "
                 "con ciertas restricciones para la población sensible."),
    "mala": ("La población sensible podría experimentar problemas de salud. La población en general podría sentirse afectada.",
             "Mantenerse atento a los informes de calidad del aire. Evitar realizar ejercicio y actividades al aire libre."),
    "umbral": ("La concentración del contaminante puede causar efectos en la salud de cualquier persona y efectos serios en "
               "la población sensible, tales como niños, ancianos, madres gestantes, personas con enfermedades pulmonares "
               "obstructivas crónicas y cardiovasculares.",
               "Reportar a la Autoridad de Salud para que declare los Niveles de Estados de Alerta de acuerdo al Decreto "
               "Supremo N° 009-2003-SA y su modificatoria Decreto Supremo N° 012-2005-SA."),
}

# Acciones prácticas ante humo de incendios (guías de salud pública sobre humo: CDC/EPA AirNow,
# ministerios de salud de Chile y España). Se muestran cuando la calidad es mala o peor.
ACCIONES_MALA = [
    "Permanece en interiores el mayor tiempo posible. Cierra puertas y ventanas y tapa las rendijas con trapos húmedos.",
    "Evita el ejercicio y el trabajo físico al aire libre. Reprograma clases de educación física, deportes y ferias.",
    "Si tienes que salir, usa una mascarilla N95, KN95 o FFP2 bien ajustada. Las de tela, las quirúrgicas y los pañuelos no protegen del humo.",
    "Niños, mayores de 65 años, gestantes y personas con asma, EPOC o enfermedades del corazón: quédense en casa y tengan a mano sus medicamentos.",
    "No empeores el aire de la casa: no fumes ni cocines con leña o kerosene, no prendas velas ni incienso y limpia con trapo húmedo en vez de barrer.",
    "Si tienes un purificador con filtro HEPA, úsalo en una habitación cerrada. Bebe agua con frecuencia.",
    "Al manejar, sube las ventanas y pon la ventilación en recirculación.",
]
ACCIONES_UMBRAL = [
    "Toda la población debe evitar estar al aire libre. Suspende clases y actividades al aire libre.",
    "Prepárate para evacuar si la autoridad lo indica o si el humo entra a la vivienda y no puedes protegerte: ve a un lugar con aire limpio.",
    "Busca atención médica (SAMU 106) ante dificultad para respirar, dolor u opresión en el pecho, tos persistente, mareo o palpitaciones.",
    "Informa a la autoridad de salud de tu región (DIRESA o GERESA, línea 113 del MINSA) para que evalúe declarar un estado de alerta.",
]
ACCIONES_HUMO_CERCANO = [
    "Si ves o hueles humo, actúa como si la calidad fuera Mala aunque el índice diga otra cosa: el modelo no resuelve el humo junto al fuego.",
    "Brigadistas y bomberos: protección respiratoria con filtro para partículas, turnos cortos y descanso fuera del humo.",
]
SINTOMAS = ("Irritación de ojos y garganta, tos, dolor de cabeza, falta de aire o dolor en el pecho. Si los síntomas son "
            "fuertes o no ceden al alejarte del humo, busca atención médica.")

RADIOS_KM = (25, 50)
SECTOR_HUMO_GRADOS = 35.0
ALCANCE_HUMO_KM = 60.0
CERCANIA_KM = 25.0


# --------------------------------------------------------------------------------------
# Cálculo del INCA
# --------------------------------------------------------------------------------------

def _redondear(x, decimales):
    f = 10 ** decimales
    return math.floor(x * f + 0.5) / f


def categoria(clave, conc):
    """Calificación según los intervalos de concentración de la tabla 3 (con su misma precisión)."""
    _, _, _, limites, decimales = CONTAMINANTES[clave]
    c = _redondear(conc, decimales)
    for cat, limite in zip(("buena", "moderada", "mala"), limites):
        if c <= limite:
            return cat
    return "umbral"


def inca(clave, conc):
    """(valor del INCA, calificación) de un contaminante para su concentración promedio en µg/m³.

    El valor sale de la ecuación oficial I = C·100/divisor y se acota a la banda de su calificación
    (Buena 0–50, Moderada 51–100, Mala 101–VUEC, Umbral por encima), porque la tabla redondea los bordes.
    """
    _, _, divisor, limites, decimales = CONTAMINANTES[clave]
    cat = categoria(clave, conc)
    valor = math.floor(_redondear(conc, decimales) * 100.0 / divisor + 0.5)
    tope_mala = max(101, math.floor(limites[2] * 100.0 / divisor + 0.5))
    minimo, maximo = {"buena": (0, 50), "moderada": (51, 100), "mala": (101, tope_mala),
                      "umbral": (tope_mala + 1, None)}[cat]
    valor = max(minimo, valor)
    return (valor if maximo is None else min(maximo, valor)), cat


def promedio_movil(valores, k, horas):
    """Media de las horas k-horas+1 .. k; None si falta más de la cuarta parte de los datos."""
    tramo = [v for v in valores[max(0, k - horas + 1):k + 1] if v is not None]
    if not tramo or len(tramo) < 0.75 * min(horas, k + 1):
        return None
    return sum(tramo) / len(tramo)


def evaluar(horario, k):
    """INCA de todos los contaminantes disponibles en la hora k; el general es el máximo."""
    detalle = {}
    for clave, (nombre, horas, _, _, _) in CONTAMINANTES.items():
        conc = promedio_movil(horario.get(clave) or [], k, horas)
        if conc is None:
            continue
        valor, cat = inca(clave, conc)
        detalle[clave] = {"nombre": nombre, "horas": horas, "conc": round(conc, 1), "inca": valor, "categoria": cat}
    if not detalle:
        return None
    clave = max(detalle, key=lambda c: (CATEGORIAS[detalle[c]["categoria"]]["orden"], detalle[c]["inca"]))
    d = detalle[clave]
    return {"inca": d["inca"], "categoria": d["categoria"], "contaminante": d["nombre"],
            "conc": d["conc"], "horas": d["horas"], "detalle": detalle}


def gravedad(e):
    """Clave para ordenar evaluaciones: primero la categoría y luego el valor del INCA."""
    return (-1, -1) if e is None else (CATEGORIAS[e["categoria"]]["orden"], e["inca"])


# --------------------------------------------------------------------------------------
# Datos
# --------------------------------------------------------------------------------------

def _clave(lat, lon):
    return f"aq:{lat:.2f}:{lon:.2f}"


def consultar(puntos, session=None, ttl=1800):
    """Series horarias (1 día atrás, 2 adelante) para [(lat, lon), ...] en una sola llamada."""
    session = session or requests.Session()
    salida = [cache.get(_clave(la, lo)) for la, lo in puntos]
    faltan = [k for k, v in enumerate(salida) if v is None]
    for inicio in range(0, len(faltan), 40):
        lote = faltan[inicio:inicio + 40]
        r = session.get(AIRE_URL, params={
            "latitude": ",".join(f"{puntos[k][0]:.3f}" for k in lote),
            "longitude": ",".join(f"{puntos[k][1]:.3f}" for k in lote),
            "hourly": ",".join(CONTAMINANTES), "past_days": 1, "forecast_days": 2,
            "timezone": "GMT", "timeformat": "unixtime", "domains": "cams_global",
        }, timeout=40, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        datos = r.json()
        if isinstance(datos, dict):
            datos = [datos]
        for k, d in zip(lote, datos):
            salida[k] = cache.set(_clave(*puntos[k]), d.get("hourly") or {}, ttl)
    return salida


def parsear_lugares(texto, maximo=10):
    """'Nombre|lat|lon;Nombre|lat|lon' -> [(nombre, lat, lon)] (lugares cercanos enviados por la interfaz)."""
    lugares = []
    for parte in (texto or "").split(";"):
        trozos = parte.rsplit("|", 2)
        if len(trozos) != 3:
            continue
        try:
            la, lo = float(trozos[1]), float(trozos[2])
        except ValueError:
            continue
        if -90 <= la <= 90 and -180 <= lo <= 180 and trozos[0].strip():
            lugares.append((trozos[0].strip()[:80], la, lo))
    return lugares[:maximo]


def rumbo_humo(meteo, horas=6):
    """Hacia dónde va el humo en las próximas horas: media vectorial del viento ponderada por su velocidad."""
    p = (meteo or {}).get("pronostico") or {}
    sx = sy = 0.0
    for d, v in list(zip(p.get("dir") or [], p.get("viento") or []))[:horas]:
        if d is None or v is None:
            continue
        hacia = math.radians((d + 180) % 360)
        sx += v * math.sin(hacia)
        sy += v * math.cos(hacia)
    if math.hypot(sx, sy) < 0.5:
        return None  # viento en calma o variable: el humo se queda alrededor del fuego
    return (math.degrees(math.atan2(sx, sy)) + 360) % 360


def _diferencia_angular(a, b):
    return abs((a - b + 180) % 360 - 180)


# --------------------------------------------------------------------------------------
# Evaluación alrededor del incendio
# --------------------------------------------------------------------------------------

def puntos_alrededor(lat, lon, lugares=()):
    puntos = [{"nombre": "Incendio", "tipo": "foco", "lat": lat, "lon": lon, "dist_km": 0.0, "rumbo": None}]
    for radio in RADIOS_KM:
        for rumbo in range(0, 360, 45):
            la, lo = geo.destino(lat, lon, rumbo, radio)
            puntos.append({"nombre": f"{radio} km al {geo.cardinal(rumbo)}", "tipo": "anillo", "lat": la, "lon": lo,
                           "dist_km": float(radio), "rumbo": float(rumbo)})
    for nombre, la, lo in lugares:
        puntos.append({"nombre": nombre, "tipo": "lugar", "lat": la, "lon": lo,
                       "dist_km": geo.haversine_km(lat, lon, la, lo), "rumbo": geo.rumbo_grados(lat, lon, la, lo)})
    return puntos


def recomendaciones(cat):
    cuidados, oficial = CUIDADOS[cat]
    acciones = []
    if cat in ("mala", "umbral"):
        acciones += ACCIONES_MALA
    if cat == "umbral":
        acciones = ACCIONES_UMBRAL + acciones
    return {"categoria": cat, "cuidados": cuidados, "oficial": oficial, "acciones": acciones,
            "humo_cercano": ACCIONES_HUMO_CERCANO, "sintomas": SINTOMAS}


def calidad_alrededor(lat, lon, lugares=(), session=None, ahora=None, meteo=None):
    """INCA actual y pronosticado en el incendio, en dos anillos (25 y 50 km) y en los lugares indicados."""
    session = session or requests.Session()
    ahora = ahora or datetime.now(timezone.utc)
    if meteo is None:
        try:
            meteo = weather.detalle(lat, lon, session=session)
        except Exception:  # noqa: BLE001 - sin viento no se marca el sector del humo
            meteo = None
    humo = rumbo_humo(meteo)

    puntos = puntos_alrededor(lat, lon, lugares)
    series = consultar([(p["lat"], p["lon"]) for p in puntos], session)
    t_ahora = ahora.timestamp()
    serie_foco = []
    alerta = motivo = None
    prioridad_alerta = -1
    for p, h in zip(puntos, series):
        p["a_sotavento"] = (humo is not None and p["tipo"] != "foco" and p["dist_km"] <= ALCANCE_HUMO_KM
                            and _diferencia_angular(p["rumbo"], humo) <= SECTOR_HUMO_GRADOS)
        p["rumbo_txt"] = geo.cardinal(p["rumbo"])
        p["dist_km"] = round(p["dist_km"], 1)
        p["actual"] = p["pronostico"] = None
        tiempos = h.get("time") or []
        pasadas = [k for k, t in enumerate(tiempos) if t <= t_ahora]
        if not pasadas:
            continue
        k = pasadas[-1]
        p["actual"] = evaluar(h, k)
        futuro = None
        for kk in range(k + 1, min(len(tiempos), k + 25)):
            e = evaluar(h, kk)
            if e and gravedad(e) > gravedad(futuro):
                futuro = dict(e, t=_iso(tiempos[kk]))
        p["pronostico"] = futuro
        if p["tipo"] == "foco":
            for kk in range(max(0, k - 24), min(len(tiempos), k + 25)):
                e = evaluar(h, kk)
                if e:
                    serie_foco.append({"t": _iso(tiempos[kk]), "inca": e["inca"], "categoria": e["categoria"],
                                       "contaminante": e["contaminante"], "pronostico": kk > k})
        # Nivel de alerta: el incendio (ahora y próximas 24 h) y lo poblado o a sotavento cercano
        candidatos = []
        if p["tipo"] == "foco":
            candidatos = [(p["actual"], "en el incendio ahora"),
                          (futuro, f"pronóstico en el incendio para las {_hora_lima(futuro['t'])}" if futuro else None)]
        elif p["dist_km"] <= CERCANIA_KM and (p["tipo"] == "lugar" or p["a_sotavento"]):
            candidatos = [(p["actual"], f"{p['nombre']} ahora")]
        prioridad = {"foco": 2, "lugar": 1, "anillo": 0}[p["tipo"]]  # en empate se nombra lo más concreto
        for e, texto in candidatos:
            if e and (gravedad(e), prioridad) > (gravedad(alerta), prioridad_alerta):
                alerta, motivo, prioridad_alerta = e, texto, prioridad

    for p in puntos:
        p["lat"], p["lon"] = round(p["lat"], 4), round(p["lon"], 4)
        p.pop("rumbo", None)
    validos = [p for p in puntos if p["actual"]]
    if not validos or not puntos[0]["actual"]:
        raise RuntimeError("el modelo CAMS no devolvió datos para la zona")
    peor_punto = max(validos, key=lambda p: gravedad(p["actual"]))
    cat = alerta["categoria"]  # existe: el foco tiene datos
    return {
        "generado": ahora.isoformat().replace("+00:00", "Z"),
        "fuente": "Modelo CAMS global (Copernicus) vía Open-Meteo; índice INCA según R. M. N.° 181-2016-MINAM",
        "foco": puntos[0],
        "puntos": puntos,
        "peor": {"nombre": peor_punto["nombre"], "dist_km": peor_punto["dist_km"], **peor_punto["actual"]},
        "alerta": {"categoria": cat, "inca": alerta["inca"], "contaminante": alerta["contaminante"], "motivo": motivo},
        "humo_rumbo": round(humo) if humo is not None else None,
        "humo_rumbo_txt": geo.cardinal(humo),
        "sector_humo": {"grados": SECTOR_HUMO_GRADOS, "alcance_km": ALCANCE_HUMO_KM},
        "serie_foco": serie_foco,
        "recomendaciones": recomendaciones(cat),
        "categorias": CATEGORIAS,
    }


def _iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _hora_lima(iso):
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return f"{(t.hour - 5) % 24:02d}:{t.minute:02d}"
