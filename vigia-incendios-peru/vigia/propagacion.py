"""Pronóstico de propagación de un incendio a partir de sus focos FIRMS.

Pasos:
  1. Meteorología horaria de Open-Meteo desde 5 días antes de las detecciones hasta el horizonte.
  2. Humedad del combustible muerto: equilibrio de Simard (NFDRS) con desfase de 1, 10 y 100 h, ajuste
     por sol y lluvia; curado del pasto según los días sin lluvia.
  3. Relieve (Terrarium) y cobertura del suelo (Esri Land Cover 10 m) -> pendiente y modelo de
     combustible de Scott y Burgan en cada celda.
  4. Rothermel + elipse de Anderson por celda y hora; avance por tiempo mínimo de recorrido (Finney 2002):
     Dijkstra dependiente del tiempo sobre 16 vecinos, desde cada píxel detectado en su hora de detección.
  5. Calibración: si hay varias pasadas satelitales, factor de velocidad que reproduce los píxeles nuevos,
     contraído hacia 1 cuando hay pocas observaciones.
  6. Conjunto de miembros que perturban viento, dirección, humedad y factor -> probabilidad de llegada.

Es una herramienta de apoyo: no considera vientos de ladera, focos secundarios, fuego de copas ni
acciones de control.
"""

import heapq
import math
import random
import statistics
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from . import geo, raster, rothermel
from .cache import cache
from .errores import resumen

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "VigiaIncendiosPeru/1.0 (pronóstico de propagación)"

HORIZONTES = (0, 3, 6, 12, 24)
N_MALLA = 65
MIEMBROS = 10
PERSISTENCIA_H = 16.0        # una celda encendida puede seguir propagando hasta 16 h (pasa la noche)
VENTANA_H = 24.0             # detecciones usadas como frente: las de las 24 h previas a la última
MAX_SIN_DETECCION_H = 36.0
SPINUP_H = 120               # horas previas para estabilizar la humedad de 100 h
SEMILADO_MAX_M = 25000.0     # el dominio llega a lo más a 25 km del centro del incendio
ROS_MIN_M_H = 0.5
ALTURA_PUNA = 3300.0
COSTA_NORTE = {"Tumbes", "Piura", "Lambayeque"}

# Copas supuestas para el factor de viento a media llama: (cobertura, altura en ft)
COPAS = {"TU1": (0.5, 49.0), "TL2": (0.8, 82.0)}


def combustible_por_cobertura(clase, elevacion_m, departamento=None):
    """Modelo de Scott y Burgan para una clase de Esri Land Cover. None = no combustible."""
    if clase in (1, 7, 8, 9):            # agua, urbano, suelo desnudo, nieve
        return None
    if clase == 2:                       # árboles
        if elevacion_m < 1000 and departamento not in COSTA_NORTE:
            return "TL2"                 # bosque húmedo amazónico: hojarasca, avance lento
        return "TU1"                     # bosque seco, montano y plantaciones
    if clase in (4, 5):                  # vegetación inundable, cultivos
        return "GR1"
    return "GR2" if elevacion_m >= ALTURA_PUNA else "GS2"   # pastizal/matorral, nubes o sin dato


# --------------------------------------------------------------------------------------
# Meteorología y humedad del combustible
# --------------------------------------------------------------------------------------

def meteo_horaria(lat, lon, desde, hasta, ahora, session):
    """Serie horaria entre desde y hasta (UTC) y lluvia diaria de 60 días."""
    h0, h1 = int(desde.timestamp() // 3600), int(hasta.timestamp() // 3600)
    clave = f"mh:{lat:.2f}:{lon:.2f}:{h0}:{h1}"
    hit = cache.get(clave)
    if hit:
        return hit
    pasadas = max(1, math.ceil((ahora - desde).total_seconds() / 3600) + 1)
    futuras = max(1, math.ceil((hasta - ahora).total_seconds() / 3600) + 2)
    r = session.get(FORECAST_URL, params={
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation,cloud_cover,is_day",
        "daily": "precipitation_sum", "past_days": 60, "forecast_days": 2,
        "past_hours": pasadas, "forecast_hours": futuras,
        "wind_speed_unit": "kmh", "timezone": "GMT", "timeformat": "unixtime",
    }, timeout=30, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    d = r.json()
    h = d.get("hourly") or {}
    n = len(h.get("time", []))
    if n < 2:
        raise RuntimeError("Open-Meteo no devolvió datos horarios")

    def serie(nombre, defecto):
        return [defecto if v is None else v for v in (h.get(nombre) or [None] * n)]

    res = {
        "t": h["time"], "temp": serie("temperature_2m", 20.0), "hr": serie("relative_humidity_2m", 60.0),
        "viento": serie("wind_speed_10m", 0.0), "dir": serie("wind_direction_10m", 0.0),
        "lluvia": serie("precipitation", 0.0), "nubes": serie("cloud_cover", 50.0), "dia": serie("is_day", 0),
        "lluvia_diaria": list(zip((d.get("daily") or {}).get("time", []), (d.get("daily") or {}).get("precipitation_sum", []))),
        "elevacion": d.get("elevation"),
    }
    return cache.set(clave, res, 1800)


def emc_simard(temp_c, hr):
    """Humedad de equilibrio del combustible muerto (fracción), ecuaciones de Simard (1968) usadas en NFDRS."""
    t = temp_c * 9.0 / 5.0 + 32.0
    h = max(0.0, min(100.0, hr))
    if h < 10:
        v = 0.03229 + 0.281073 * h - 0.000578 * h * t
    elif h < 50:
        v = 2.22749 + 0.160107 * h - 0.01478 * t
    else:
        v = 21.0606 + 0.005565 * h * h - 0.00035 * h * t - 0.483199 * h
    return max(0.5, v) / 100.0


# Corrección diurna en la interfaz combustible-aire según nubosidad: (hasta %, +°F, ×HR)
_AJUSTE_SOL = ((10, 25.0, 0.75), (50, 19.0, 0.83), (90, 12.0, 0.91), (101, 5.0, 0.97))


def humedad_muerta(meteo):
    """Lista horaria de (m1, m10, m100) en fracción."""
    salida = []
    m = None
    for temp, hr, nubes, dia, lluvia in zip(meteo["temp"], meteo["hr"], meteo["nubes"], meteo["dia"], meteo["lluvia"]):
        if dia:
            for limite, dt_f, fhr in _AJUSTE_SOL:
                if nubes < limite:
                    temp, hr = temp + dt_f * 5.0 / 9.0, hr * fhr
                    break
        objetivo = 0.35 if lluvia >= 0.5 else emc_simard(temp, hr)
        if m is None:
            m = [objetivo] * 3
        m = [objetivo + (mi - objetivo) * math.exp(-1.0 / tau) for mi, tau in zip(m, (1.0, 10.0, 100.0))]
        salida.append(tuple(m))
    return salida


def dias_sin_lluvia(lluvia_diaria, hoy):
    dias = 0
    for fecha, mm in reversed([x for x in lluvia_diaria if x[0] < hoy]):
        if mm is not None and mm >= 1.0:
            break
        dias += 1
    return dias


def humedad_viva(dias):
    """Humedad del pasto y de las leñosas vivas (fracción) según los días sin lluvia: curado progresivo."""
    return 0.30 + 0.90 * math.exp(-dias / 15.0), 0.60 + 0.90 * math.exp(-dias / 30.0)


# --------------------------------------------------------------------------------------
# Progresión observada por los satélites
# --------------------------------------------------------------------------------------

def _dist_km(a, b):
    kx = geo.KM_PER_DEG_LON_EQ * math.cos(math.radians((a["lat"] + b["lat"]) / 2))
    return math.hypot((a["lon"] - b["lon"]) * kx, (a["lat"] - b["lat"]) * geo.KM_PER_DEG_LAT)


def pasadas(dets, brecha_h=0.5):
    """Agrupa las detecciones en pasadas satelitales (sin huecos mayores a brecha_h)."""
    orden = sorted(dets, key=lambda d: d["t"])
    grupos = []
    for d in orden:
        if grupos and (d["t"] - grupos[-1][-1]["t"]).total_seconds() / 3600 <= brecha_h:
            grupos[-1].append(d)
        else:
            grupos.append([d])
    return grupos


def progresion_observada(dets, dt_min_h=2.0, ros_max_m_min=100.0):
    """Píxeles nuevos de cada pasada respecto a las anteriores y su velocidad aparente.

    Un píxel es nuevo si no se superpone con ningún foco previo. Su fuente es el foco previo más
    cercano; si este tiene menos de dt_min_h de antigüedad la medición se descarta (ruido de posición).
    """
    grupos = pasadas(dets)
    obs = []
    previos = []
    for k, grupo in enumerate(grupos):
        if k > 0:
            t_previa = grupos[k - 1][-1]["t"]
            for d in grupo:
                tam = max(d["scan"], d["track"])
                mejor = None
                for p in previos:
                    dist = _dist_km(d, p)
                    if mejor is None or dist < mejor[0]:
                        mejor = (dist, p)
                dist, p = mejor
                if dist <= 0.5 * (tam + max(p["scan"], p["track"])):
                    continue
                dt = (d["t"] - p["t"]).total_seconds() / 3600
                if dt < dt_min_h:
                    continue
                ros = dist * 1000 / (dt * 60)
                if ros > ros_max_m_min:
                    continue  # demasiado rápido: probablemente otro foco de ignición
                obs.append({"det": d, "fuente": p, "dist_km": dist, "dt_h": dt, "ros": ros,
                            "rumbo": geo.rumbo_grados(p["lat"], p["lon"], d["lat"], d["lon"]), "t_previa": t_previa})
        previos.extend(grupo)
    res = {"pasadas": len(grupos), "n": len(obs), "observaciones": obs}
    if obs:
        ros = sorted(o["ros"] for o in obs)
        sx = sum(o["dist_km"] * math.sin(math.radians(o["rumbo"])) for o in obs)
        sy = sum(o["dist_km"] * math.cos(math.radians(o["rumbo"])) for o in obs)
        res.update({
            "ros_mediana_m_min": round(statistics.median(ros), 2),
            "ros_p90_m_min": round(ros[min(len(ros) - 1, int(0.9 * len(ros)))], 2),
            "rumbo": round((math.degrees(math.atan2(sx, sy)) + 360) % 360),
            "coherencia": round(math.hypot(sx, sy) / sum(o["dist_km"] for o in obs), 2),
        })
        res["rumbo_txt"] = geo.cardinal(res["rumbo"])
    return res


# --------------------------------------------------------------------------------------
# Malla y simulación
# --------------------------------------------------------------------------------------

def _vecinos():
    salida = []
    for di, dj in [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1),
                   (1, -2), (2, -1), (2, 1), (1, 2), (-1, 2), (-2, 1), (-2, -1), (-1, -2)]:
        az = math.atan2(di, -dj)  # columnas hacia el este, filas hacia el sur
        if abs(di) == abs(dj):
            cruce, todos = [(di, 0), (0, dj)], True      # diagonal: bloquea si ambas celdas son barrera
        elif abs(di) + abs(dj) == 3:
            if abs(di) == 2:
                cruce = [(di // 2, 0), (di // 2, dj)]
            else:
                cruce = [(0, dj // 2), (di, dj // 2)]
            todos = False                                 # salto de caballo: bloquea si alguna lo es
        else:
            cruce, todos = [], False
        salida.append((di, dj, math.hypot(di, dj), math.cos(az), math.sin(az), cruce, todos))
    return salida


VECINOS = _vecinos()


class Malla:
    """Malla cuadrada de n×n celdas centrada en (lat0, lon0); índice = fila * n + columna, fila 0 al norte."""

    def __init__(self, lat0, lon0, n, celda_m):
        self.n, self.celda = n, celda_m
        self.dlat = celda_m / (geo.KM_PER_DEG_LAT * 1000)
        self.dlon = celda_m / (geo.KM_PER_DEG_LON_EQ * 1000 * math.cos(math.radians(lat0)))
        self.lats = [lat0 + (n // 2 - j) * self.dlat for j in range(n)]
        self.lons = [lon0 + (i - n // 2) * self.dlon for i in range(n)]
        self.norte, self.sur = self.lats[0] + self.dlat / 2, self.lats[-1] - self.dlat / 2
        self.oeste, self.este = self.lons[0] - self.dlon / 2, self.lons[-1] + self.dlon / 2

    def indice(self, lat, lon):
        j = round((self.lats[0] - lat) / self.dlat)
        i = round((lon - self.lons[0]) / self.dlon)
        return j * self.n + i if 0 <= i < self.n and 0 <= j < self.n else None

    def celdas_de_pixel(self, d):
        """Celdas cuyo centro cae en la huella del píxel (o la más cercana si el píxel es menor)."""
        dy = (d["track"] / 2.0) / geo.KM_PER_DEG_LAT
        dx = (d["scan"] / 2.0) / (geo.KM_PER_DEG_LON_EQ * math.cos(math.radians(d["lat"])))
        j0 = max(0, math.ceil((self.lats[0] - (d["lat"] + dy)) / self.dlat))
        j1 = min(self.n - 1, math.floor((self.lats[0] - (d["lat"] - dy)) / self.dlat))
        i0 = max(0, math.ceil((d["lon"] - dx - self.lons[0]) / self.dlon))
        i1 = min(self.n - 1, math.floor((d["lon"] + dx - self.lons[0]) / self.dlon))
        salida = [j * self.n + i for j in range(j0, j1 + 1) for i in range(i0, i1 + 1)]
        if not salida:
            c = self.indice(d["lat"], d["lon"])
            salida = [c] if c is not None else []
        return salida


def pendientes(elev, n, celda_m, tan_max=1.2):
    """Tangente de la pendiente y rumbo cuesta arriba (grados) por celda, por diferencias centradas."""
    tan, az = [0.0] * (n * n), [0.0] * (n * n)
    for j in range(n):
        for i in range(n):
            i0, i1 = max(0, i - 1), min(n - 1, i + 1)
            j0, j1 = max(0, j - 1), min(n - 1, j + 1)
            dzdx = (elev[j * n + i1] - elev[j * n + i0]) / ((i1 - i0) * celda_m)
            dzdy = (elev[j0 * n + i] - elev[j1 * n + i]) / ((j1 - j0) * celda_m)  # fila menor = más al norte
            c = j * n + i
            tan[c] = min(tan_max, math.hypot(dzdx, dzdy))
            az[c] = (math.degrees(math.atan2(dzdx, dzdy)) + 360) % 360
    return tan, az


class Escenario:
    """Entradas de una simulación: combustible y pendiente por celda; lechos y viento por hora."""

    def __init__(self, n, celda_m, comb, tan, az, lechos, viento_medio, rumbo_viento, factor=1.0):
        self.n, self.celda = n, celda_m
        self.comb, self.tan, self.az = comb, tan, az
        self.lechos = lechos                  # [hora] -> {modelo: Lecho}
        self.viento_medio = viento_medio      # [hora] -> {modelo: ft/min a media llama}
        self.rumbo_viento = rumbo_viento      # [hora] -> grados hacia donde sopla
        self.horas = len(lechos)
        self.factor = factor
        self._elipses = {}

    def elipse(self, c, h):
        """(a, cos rumbo, sin rumbo, e) con a = ROSmax·(1−e) en m/h; None si no propaga."""
        h = min(h, self.horas - 1)
        clave = c * self.horas + h
        v = self._elipses.get(clave, False)
        if v is not False:
            return v
        m = self.comb[c]
        ros, rumbo, lb = self.lechos[h][m].maxima(self.viento_medio[h][m], self.rumbo_viento[h], self.tan[c], self.az[c])
        ros_m_h = ros * 0.3048 * 60.0 * self.factor
        if ros_m_h < ROS_MIN_M_H:
            v = None
        else:
            e = rothermel.excentricidad(lb)
            r = math.radians(rumbo)
            v = (ros_m_h * (1.0 - e), math.cos(r), math.sin(r), e)
        self._elipses[clave] = v
        return v


def simular(esc, encendidos, t_fin, persistencia_h=PERSISTENCIA_H):
    """Tiempo mínimo de llegada (h desde el origen) a cada celda; inf si no llega antes de t_fin.

    Dijkstra dependiente del tiempo: el fuego puede esperar en una celda encendida a que mejoren las
    condiciones (por ejemplo, de la noche al día) durante persistencia_h.
    """
    n = esc.n
    inf = math.inf
    llegada = [inf] * (n * n)
    monticulo = []
    for c, t in encendidos.items():
        if t < llegada[c]:
            llegada[c] = t
            monticulo.append((t, c))
    heapq.heapify(monticulo)
    hecho = bytearray(n * n)
    comb = esc.comb
    celda = esc.celda
    while monticulo:
        t, c = heapq.heappop(monticulo)
        if hecho[c] or t > llegada[c]:
            continue
        hecho[c] = 1
        if t > t_fin:
            break
        limite = min(t_fin, t + persistencia_h)
        h0 = int(t)
        el0 = esc.elipse(c, h0)
        while el0 is None and h0 + 1 <= limite:
            h0 += 1
            el0 = esc.elipse(c, h0)
        if el0 is None:
            continue
        inicio0 = t if h0 == int(t) else float(h0)
        a0, ch0, sh0, e0 = el0
        ci, cj = c % n, c // n
        for di, dj, paso, ca, sa, cruce, todos in VECINOS:
            ni, nj = ci + di, cj + dj
            if not (0 <= ni < n and 0 <= nj < n):
                continue
            nc = nj * n + ni
            if hecho[nc] or comb[nc] is None:
                continue
            if cruce:
                barreras = [comb[(cj + b) * n + ci + a] is None for a, b in cruce]
                if (all(barreras) if todos else any(barreras)):
                    continue
            d_m = paso * celda
            mejor = min(llegada[nc], inicio0 + d_m * (1.0 - e0 * (ch0 * ca + sh0 * sa)) / a0)
            h = h0 + 1
            while h < mejor and h <= limite:  # esperar a una hora más favorable puede llegar antes
                el = esc.elipse(c, h)
                if el is not None:
                    a, ch, sh, e = el
                    llega = h + d_m * (1.0 - e * (ch * ca + sh * sa)) / a
                    if llega < mejor:
                        mejor = llega
                h += 1
            if mejor < llegada[nc]:
                llegada[nc] = mejor
                heapq.heappush(monticulo, (mejor, nc))
    return llegada


# --------------------------------------------------------------------------------------
# Isolíneas (marching squares)
# --------------------------------------------------------------------------------------

def isolineas(valores, n, nivel):
    """Polilíneas (fila, columna) del contorno valores = nivel. Valores en los centros de celda."""
    segmentos = []
    for j in range(n - 1):
        for i in range(n - 1):
            a, b = valores[j * n + i], valores[j * n + i + 1]
            c, d = valores[(j + 1) * n + i + 1], valores[(j + 1) * n + i]
            caso = (a >= nivel) << 3 | (b >= nivel) << 2 | (c >= nivel) << 1 | (d >= nivel)
            if caso in (0, 15):
                continue

            def f(p, q):
                return 0.5 if p == q else (nivel - p) / (q - p)

            arriba, derecha = (j, i + f(a, b)), (j + f(b, c), i + 1)
            abajo, izquierda = (j + 1, i + f(d, c)), (j + f(a, d), i)
            centro = (a + b + c + d) / 4 >= nivel
            tabla = {
                1: [(izquierda, abajo)], 2: [(abajo, derecha)], 3: [(izquierda, derecha)], 4: [(arriba, derecha)],
                5: [(izquierda, arriba), (abajo, derecha)] if centro else [(arriba, derecha), (izquierda, abajo)],
                6: [(arriba, abajo)], 7: [(izquierda, arriba)], 8: [(izquierda, arriba)], 9: [(arriba, abajo)],
                10: [(arriba, derecha), (izquierda, abajo)] if centro else [(izquierda, arriba), (abajo, derecha)],
                11: [(arriba, derecha)], 12: [(izquierda, derecha)], 13: [(abajo, derecha)], 14: [(izquierda, abajo)],
            }
            segmentos.extend(tabla[caso])
    return _unir(segmentos)


def _unir(segmentos):
    def k(p):
        return (round(p[0], 6), round(p[1], 6))

    adyacentes = {}
    for idx, (p, q) in enumerate(segmentos):
        adyacentes.setdefault(k(p), []).append((idx, q))
        adyacentes.setdefault(k(q), []).append((idx, p))
    usado = [False] * len(segmentos)
    lineas = []
    for idx, (p, q) in enumerate(segmentos):
        if usado[idx]:
            continue
        usado[idx] = True
        linea = [p, q]
        for extremo in (1, 0):  # crece hacia adelante y luego hacia atrás
            while True:
                actual = linea[-1] if extremo else linea[0]
                sig = next(((i2, o) for i2, o in adyacentes.get(k(actual), []) if not usado[i2]), None)
                if sig is None:
                    break
                usado[sig[0]] = True
                if extremo:
                    linea.append(sig[1])
                else:
                    linea.insert(0, sig[1])
        lineas.append(linea)
    return lineas


def suavizar(valores, n):
    """Filtro 3×3 (1-2-1) para mostrar probabilidades de un conjunto pequeño sin escalones."""
    salida = [0.0] * (n * n)
    for j in range(n):
        for i in range(n):
            s = w = 0.0
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    jj, ii = j + dj, i + di
                    if 0 <= jj < n and 0 <= ii < n:
                        p = (2 - abs(di)) * (2 - abs(dj))
                        s += p * valores[jj * n + ii]
                        w += p
            salida[j * n + i] = s / w
    return salida


# --------------------------------------------------------------------------------------
# Pronóstico completo
# --------------------------------------------------------------------------------------

def _horas_desde(t, origen):
    return (t - origen).total_seconds() / 3600.0


def _preparar_horas(meteo, i0, horas, modelos, vivas, f_viento=1.0, d_dir=0.0, f_humedad=1.0):
    muertas = meteo["_muertas"]
    lechos, vientos, rumbos = [], [], []
    for h in range(horas):
        k = min(i0 + h, len(meteo["t"]) - 1)
        m1, m10, m100 = (min(0.6, m * f_humedad) for m in muertas[k])
        u20 = meteo["viento"][k] * f_viento / 1.15 * rothermel.KMH_A_FT_MIN
        lechos.append({m: rothermel.preparar(rothermel.MODELOS[m], m1, m10, m100, *vivas) for m in modelos})
        vientos.append({m: u20 * _waf(m) for m in modelos})
        rumbos.append((meteo["dir"][k] + 180.0 + d_dir) % 360)
    return lechos, vientos, rumbos


def _waf(m):
    cob, alt = COPAS.get(m, (0.0, 0.0))
    return rothermel.factor_viento(rothermel.MODELOS[m].profundidad, cob, alt)


def _encender(malla, dets, origen):
    enc = {}
    for d in dets:
        t = _horas_desde(d["t"], origen)
        for c in malla.celdas_de_pixel(d):
            if t < enc.get(c, math.inf):
                enc[c] = t
    return enc


def _semilla(texto):
    return zlib.crc32(texto.encode("utf-8"))


def pronosticar(incendio, dets, ahora=None, session=None, horizonte_h=24, miembros=MIEMBROS, n=N_MALLA):
    """Pronóstico probabilístico de propagación para un incendio agrupado y sus detecciones."""
    ahora = ahora or datetime.now(timezone.utc)
    session = session or requests.Session()
    horizontes = [h for h in HORIZONTES if h <= horizonte_h]
    if not dets:
        return {"disponible": False, "motivo": "El incendio no tiene detecciones."}
    t_ultima = max(d["t"] for d in dets)
    sin_det = _horas_desde(ahora, t_ultima)
    if sin_det > MAX_SIN_DETECCION_H:
        return {"disponible": False, "motivo": f"La última detección tiene {sin_det:.0f} h. Sin focos recientes no "
                                                "se puede pronosticar: el fuego pudo apagarse o quedar oculto."}
    clave = f"prop:{incendio['id']}:{t_ultima.timestamp()}:{len(dets)}:{horizonte_h}:{n}:{miembros}"
    hit = cache.get(clave)
    if hit:
        return hit

    avisos = []
    frente = [d for d in dets if _horas_desde(t_ultima, d["t"]) <= VENTANA_H]
    consumidos = [d for d in dets if _horas_desde(t_ultima, d["t"]) > VENTANA_H]
    t_ini = min(d["t"] for d in frente)
    origen = t_ini.replace(minute=0, second=0, microsecond=0)
    fin = ahora.timestamp() + horizonte_h * 3600
    horas = math.ceil((fin - origen.timestamp()) / 3600) + 1
    rel_ahora = _horas_desde(ahora, origen)

    # 1-2. Meteorología y humedades
    desde = datetime.fromtimestamp(origen.timestamp() - SPINUP_H * 3600, tz=timezone.utc)
    hasta = datetime.fromtimestamp(fin + 3600, tz=timezone.utc)
    meteo = dict(meteo_horaria(incendio["lat"], incendio["lon"], desde, hasta, ahora, session))
    meteo["_muertas"] = humedad_muerta(meteo)
    i0 = next((k for k, t in enumerate(meteo["t"]) if t >= origen.timestamp()), 0)
    dias = dias_sin_lluvia(meteo["lluvia_diaria"], int(ahora.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()))
    vivas = humedad_viva(dias)
    elev_ref = meteo.get("elevacion") or 0.0

    # Tamaño del dominio: avance posible en el combustible de referencia con viento y pendiente a favor
    modelo_ref = "GR2" if elev_ref >= ALTURA_PUNA else "GS2"
    lechos_ref, vientos_ref, _ = _preparar_horas(meteo, i0, horas, [modelo_ref], vivas)
    avance_m = sum(lc[modelo_ref].maxima(v[modelo_ref], 0.0, 0.35, 0.0)[0] * 0.3048 * 60
                   for lc, v in zip(lechos_ref, vientos_ref))
    radio_inc = max(geo.haversine_km(incendio["lat"], incendio["lon"], d["lat"], d["lon"]) for d in dets) * 1000
    semilado = min(SEMILADO_MAX_M, max(3000.0, radio_inc + 1.5 * avance_m + 1000.0))

    obs = progresion_observada(frente)
    grupos = pasadas(frente)
    rnd = random.Random(_semilla(f"{incendio['id']}:{t_ultima.isoformat()}"))
    azar = [(rnd.gauss(0, 1), rnd.gauss(0, 1), rnd.gauss(0, 1), rnd.gauss(0, 1)) for _ in range(miembros - 1)]
    for intento in range(2):
        dom = _dominio(incendio, n, semilado, elev_ref, frente, consumidos, origen, session)
        calib = _calibrar(dom, meteo, i0, horas, vivas, obs, grupos, origen)
        # 6. Conjunto: el miembro 0 es el control sin perturbar
        sigma_k = 0.25 + 0.35 * 4.0 / (calib["n"] + 4.0)
        perturbaciones = [(1.0, 0.0, 1.0, 1.0)] + [
            (math.exp(0.25 * a), 20.0 * b, math.exp(0.15 * c), math.exp(sigma_k * d)) for a, b, c, d in azar]
        llegadas, control = [], None
        for fv, dd, fh, fk in perturbaciones:
            lechos, vientos, rumbos = _preparar_horas(meteo, i0, horas, dom["modelos"], vivas, fv, dd, fh)
            esc = Escenario(n, dom["malla"].celda, dom["comb"], dom["tan"], dom["az"], lechos, vientos, rumbos,
                            calib["factor"] * fk)
            llegadas.append(simular(esc, dom["encendidos"], horas - 1.0))
            control = control or esc
        if intento == 0 and semilado < SEMILADO_MAX_M and _toca_borde(llegadas, n, rel_ahora + horizonte_h):
            semilado = min(SEMILADO_MAX_M, semilado * 2)  # la mediana sale del dominio: se amplía una vez
            continue
        break

    res = _resumir(incendio, dom, llegadas, control, meteo, i0, rel_ahora, horizontes, horizonte_h, ahora,
                   origen, vivas, dias, obs, calib, avisos + dom["avisos"], miembros, sin_det)
    return cache.set(clave, res, 900)


def _toca_borde(llegadas, n, limite, fraccion=0.5):
    borde = [c for c in range(n * n) if c // n in (0, n - 1) or c % n in (0, n - 1)]
    return any(sum(1 for ll in llegadas if ll[c] <= limite) >= fraccion * len(llegadas) for c in borde)


def _dominio(incendio, n, semilado, elev_ref, frente, consumidos, origen, session):
    """Malla, relieve, cobertura, combustible y celdas encendidas para un tamaño de dominio."""
    avisos = []
    celda = 2 * semilado / (n - 1)
    malla = Malla(incendio["lat"], incendio["lon"], n, celda)
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_elev = ex.submit(raster.malla_elevacion, malla.lats, malla.lons, celda, session)
        f_cob = ex.submit(raster.malla_cobertura, malla.oeste, malla.sur, malla.este, malla.norte, n, n, session)
        try:
            elev = f_elev.result()
        except Exception as exc:  # noqa: BLE001
            elev = [elev_ref] * (n * n)
            avisos.append(f"Sin relieve ({resumen(exc)}): se asumió terreno plano.")
        try:
            cob = f_cob.result()
        except Exception as exc:  # noqa: BLE001
            cob = {"clases": [0] * (n * n), "anio": None}
            avisos.append(f"Sin cobertura del suelo ({resumen(exc)}): combustible estimado por la altitud.")
    tan, az = pendientes(elev, n, celda)
    dep = incendio.get("departamento")
    comb = [combustible_por_cobertura(k, z, dep) for k, z in zip(cob["clases"], elev)]
    for d in consumidos:  # quemado hace más de un día: sin combustible
        for c in malla.celdas_de_pixel(d):
            comb[c] = None
    encendidos = _encender(malla, frente, origen)
    for c in encendidos:
        if comb[c] is None:
            comb[c] = "GR2" if elev[c] >= ALTURA_PUNA else "GS2"  # si ardió, había combustible
    return {"malla": malla, "comb": comb, "tan": tan, "az": az, "cob": cob, "encendidos": encendidos,
            "modelos": sorted({m for m in comb if m}), "avisos": avisos}


def _calibrar(dom, meteo, i0, horas, vivas, obs, grupos, origen):
    """Factor de velocidad que reproduce cuándo aparecieron los píxeles nuevos, simulando desde la 1.ª pasada.

    Para cada píxel nuevo: razón = llegada simulada / llegada observada (punto medio entre su pasada y la
    anterior). Se toma la mediana y se contrae hacia 1 con peso n / (n + 4).
    """
    if obs["n"] < 3:
        return {"aplicada": False, "factor": 1.0, "n": 0}
    malla = dom["malla"]
    lechos, vientos, rumbos = _preparar_horas(meteo, i0, horas, dom["modelos"], vivas)
    esc = Escenario(malla.n, malla.celda, dom["comb"], dom["tan"], dom["az"], lechos, vientos, rumbos)
    t0 = _horas_desde(grupos[0][-1]["t"], origen)
    enc0 = _encender(malla, grupos[0], origen)
    t_obs_max = max(_horas_desde(o["det"]["t"], origen) for o in obs["observaciones"])
    llegada = simular(esc, enc0, min(horas - 1, t0 + 3 * (t_obs_max - t0) + 1))
    razones = []
    for o in obs["observaciones"]:
        c = malla.indice(o["det"]["lat"], o["det"]["lon"])
        if c is None or c in enc0:
            continue
        t_obs = (_horas_desde(o["t_previa"], origen) + _horas_desde(o["det"]["t"], origen)) / 2 - t0
        if t_obs <= 0.25:
            continue
        r = 5.0 if llegada[c] == math.inf else (llegada[c] - t0) / t_obs
        razones.append(max(0.2, min(5.0, r)))
    if len(razones) < 3:
        return {"aplicada": False, "factor": 1.0, "n": 0}
    crudo = statistics.median(razones)
    peso = len(razones) / (len(razones) + 4.0)
    return {"aplicada": True, "factor": round(math.exp(peso * math.log(crudo)), 2),
            "factor_crudo": round(crudo, 2), "n": len(razones), "peso": round(peso, 2)}


def _resumir(incendio, dom, llegadas, control, meteo, i0, rel_ahora, horizontes, horizonte_h, ahora,
             origen, vivas, dias, obs, calib, avisos, miembros, sin_det):
    malla, comb, tan, cob, encendidos = dom["malla"], dom["comb"], dom["tan"], dom["cob"], dom["encendidos"]
    n, celda = malla.n, malla.celda
    ha_celda = celda * celda / 1e4
    prob, prob_s, isos, por_horizonte = {}, {}, [], []
    for h in horizontes:
        lim = rel_ahora + h
        p = [100.0 * sum(1 for ll in llegadas if ll[c] <= lim) / len(llegadas) for c in range(n * n)]
        prob[h] = p
        prob_s[h] = suavizar(p, n)

    def a_latlon(linea):
        return [[round(malla.lats[0] - f * malla.dlat, 5), round(malla.lons[0] + c * malla.dlon, 5)] for f, c in linea]

    base = {c for c in range(n * n) if prob[0][c] >= 50}
    for h in horizontes:
        lineas = [a_latlon(li) for li in isolineas(prob_s[h], n, 50) if len(li) >= 3]
        isos.append({"h": h, "nivel": 50, "lineas": lineas})
        celdas = [c for c in range(n * n) if prob[h][c] >= 50]
        nuevas = [c for c in celdas if c not in base]
        alcance = max((geo.haversine_km(incendio["lat"], incendio["lon"], malla.lats[c // n], malla.lons[c % n])
                       for c in celdas), default=0.0)
        rumbo = None
        if nuevas:
            sx = sum((malla.lons[c % n] - incendio["lon"]) * math.cos(math.radians(incendio["lat"])) for c in nuevas)
            sy = sum(malla.lats[c // n] - incendio["lat"] for c in nuevas)
            if math.hypot(sx, sy) > 0:
                rumbo = round((math.degrees(math.atan2(sx, sy)) + 360) % 360)
        por_horizonte.append({
            "h": h, "area_ha": round(len(celdas) * ha_celda, 1), "crecimiento_ha": round(len(nuevas) * ha_celda, 1),
            "area_posible_ha": round(sum(1 for v in prob[h] if v >= 10) * ha_celda, 1),
            "alcance_km": round(alcance, 2), "rumbo": rumbo, "rumbo_txt": geo.cardinal(rumbo),
        })
    lineas10 = [a_latlon(li) for li in isolineas(prob_s[horizonte_h], n, 10) if len(li) >= 3]
    isos.append({"h": horizonte_h, "nivel": 10, "lineas": lineas10})

    borde = any(prob[horizonte_h][c] >= 10 for c in range(n * n)
                if c // n in (0, n - 1) or c % n in (0, n - 1))
    if borde:
        avisos.append(f"El fuego podría salir del área simulada ({celda * (n - 1) / 2000:.0f} km alrededor): "
                      "el alcance real puede ser mayor.")
    if sin_det > 12:
        avisos.append(f"La última detección tiene {sin_det:.0f} h: el pronóstico supone que el fuego sigue activo.")

    # Mediana de llegada por celda (h desde ahora)
    eta = []
    k_med = len(llegadas) // 2
    for c in range(n * n):
        v = sorted(ll[c] for ll in llegadas)[k_med] - rel_ahora
        eta.append(None if v > horizonte_h or v == math.inf else round(v, 1))

    # Combustibles del dominio y del frente pronosticado
    conteo = {}
    for m in comb:
        conteo[m] = conteo.get(m, 0) + 1
    combustibles = [{"codigo": m or "NB", "nombre": rothermel.MODELOS[m].nombre if m else "Sin combustible (agua, urbano, roca, nieve)",
                     "pct": round(100 * v / (n * n), 1)} for m, v in sorted(conteo.items(), key=lambda x: -x[1])]
    nuevas_h = [c for c in range(n * n) if prob[horizonte_h][c] >= 50 and prob[0][c] < 50 and comb[c]]
    frente = {}
    for c in nuevas_h or [c for c in encendidos if comb[c]]:
        frente[comb[c]] = frente.get(comb[c], 0) + 1
    modelo_frente = max(frente, key=frente.get) if frente else next((m for m in comb if m), "GS2")

    # Serie horaria del frente (control calibrado, terreno plano)
    serie = []
    h_ahora = int(rel_ahora)
    for h in range(h_ahora, min(control.horas, h_ahora + horizonte_h + 1)):
        k = min(i0 + h, len(meteo["t"]) - 1)
        ros = control.lechos[h][modelo_frente].maxima(control.viento_medio[h][modelo_frente], control.rumbo_viento[h])[0]
        serie.append({
            "t": datetime.fromtimestamp(meteo["t"][k], tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "ros_m_min": round(ros * 0.3048 * control.factor, 2), "viento": meteo["viento"][k],
            "dir": meteo["dir"][k], "hr": meteo["hr"][k], "m1_pct": round(meteo["_muertas"][k][0] * 100, 1),
        })
    pico = max(serie, key=lambda s: s["ros_m_min"]) if serie else None

    cerca = [tan[c] for c in range(n * n)
             if geo.haversine_km(incendio["lat"], incendio["lon"], malla.lats[c // n], malla.lons[c % n]) <= 3]
    m_ahora = meteo["_muertas"][min(i0 + h_ahora, len(meteo["t"]) - 1)]
    curado = 1.0 - max(0.0, min(1.0, vivas[0] / 0.9 - 1.0 / 3.0))
    final = por_horizonte[-1]
    observado = {k: v for k, v in obs.items() if k != "observaciones"}
    return {
        "disponible": True,
        "id": incendio["id"],
        "generado": ahora.isoformat().replace("+00:00", "Z"),
        "origen": origen.isoformat().replace("+00:00", "Z"),
        "horizonte_h": horizonte_h,
        "horizontes": horizontes,
        "miembros": miembros,
        "malla": {"n": n, "celda_m": round(celda, 1), "norte": malla.norte, "sur": malla.sur,
                  "oeste": malla.oeste, "este": malla.este},
        "prob": {str(h): [round(v) for v in prob_s[h]] for h in horizontes},
        "eta": eta,
        "isolineas": isos,
        "por_horizonte": por_horizonte,
        "resumen": {
            "rumbo": final["rumbo"], "rumbo_txt": final["rumbo_txt"], "alcance_km": final["alcance_km"],
            "area_ha": final["area_ha"], "crecimiento_ha": final["crecimiento_ha"],
            "ros_pico_m_min": pico["ros_m_min"] if pico else None, "ros_pico_t": pico["t"] if pico else None,
            "modelo_frente": modelo_frente, "modelo_frente_nombre": rothermel.MODELOS[modelo_frente].nombre,
            "pendiente_media_pct": round(100 * statistics.mean(cerca), 0) if cerca else None,
            "pendiente_max_pct": round(100 * max(cerca), 0) if cerca else None,
            "humedad_1h_pct": round(m_ahora[0] * 100, 1), "humedad_10h_pct": round(m_ahora[1] * 100, 1),
            "pasto_curado_pct": round(curado * 100), "dias_sin_lluvia": dias,
        },
        "serie": serie,
        "combustibles": combustibles,
        "cobertura_anio": cob.get("anio"),
        "calibracion": calib,
        "observado": observado,
        "avisos": avisos,
    }
