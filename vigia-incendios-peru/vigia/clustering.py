"""Agrupa focos de calor en incendios y calcula sus indicadores."""

import math
from collections import Counter, defaultdict
from datetime import datetime, timezone

from . import geo

# Niveles por potencia radiativa total (FRP, MW). Umbrales operativos de referencia, no oficiales.
NIVELES = [
    (1, "Bajo", 0.0),
    (2, "Moderado", 10.0),
    (3, "Alto", 50.0),
    (4, "Muy alto", 200.0),
    (5, "Extremo", 1000.0),
]


def nivel_por_frp(frp_total: float):
    nivel = NIVELES[0]
    for n in NIVELES:
        if frp_total >= n[2]:
            nivel = n
    return nivel[0], nivel[1]


def estado_por_edad(horas: float) -> str:
    if horas <= 12:
        return "activo"
    if horas <= 24:
        return "reciente"
    return "anterior"


class _UnionFind:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def agrupar(detecciones, eps_km: float = 1.5):
    """Enlace simple: dos focos a menos de eps_km pertenecen al mismo incendio.

    Devuelve una lista de listas de índices.
    """
    n = len(detecciones)
    if n == 0:
        return []
    cel_lat = eps_km / geo.KM_PER_DEG_LAT
    cel_lon = eps_km / (geo.KM_PER_DEG_LON_EQ * math.cos(math.radians(18.7)))  # celda conservadora para el Perú
    malla = defaultdict(list)
    for i, d in enumerate(detecciones):
        malla[(math.floor(d["lon"] / cel_lon), math.floor(d["lat"] / cel_lat))].append(i)
    uf = _UnionFind(n)
    eps2 = eps_km ** 2
    for (cx, cy), idxs in malla.items():
        vecinos = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                vecinos.extend(malla.get((cx + dx, cy + dy), ()))
        for i in idxs:
            di = detecciones[i]
            kx = geo.KM_PER_DEG_LON_EQ * math.cos(math.radians(di["lat"]))
            for j in vecinos:
                if j <= i:
                    continue
                dj = detecciones[j]
                ddx = (dj["lon"] - di["lon"]) * kx
                ddy = (dj["lat"] - di["lat"]) * geo.KM_PER_DEG_LAT
                if ddx * ddx + ddy * ddy <= eps2:
                    uf.union(i, j)
    grupos = defaultdict(list)
    for i in range(n):
        grupos[uf.find(i)].append(i)
    return list(grupos.values())


def resumir_incendio(dets, ahora: datetime, limites=None):
    """Indicadores de un grupo de detecciones."""
    n = len(dets)
    frp_total = sum(d["frp"] for d in dets)
    peso = [max(d["frp"], 0.1) for d in dets]
    pt = sum(peso)
    lat = sum(d["lat"] * w for d, w in zip(dets, peso)) / pt
    lon = sum(d["lon"] * w for d, w in zip(dets, peso)) / pt
    t_min = min(d["t"] for d in dets)
    t_max = max(d["t"] for d in dets)
    horas = max(0.0, (ahora - t_max).total_seconds() / 3600)

    esquinas = []
    for d in dets:
        esquinas.extend(geo.huella_pixel(d["lat"], d["lon"], d["scan"], d["track"]))
    hull = geo.envolvente_convexa([(round(x, 5), round(y, 5)) for x, y in esquinas])
    area_pix_km2 = geo.area_union_pixeles_km2(dets)
    area_env_km2 = geo.area_poligono_km2(hull)

    nivel, etiqueta = nivel_por_frp(frp_total)
    dep, prov = (None, None)
    if limites is not None:
        dep, prov = limites.ubicar(lon, lat)
        if dep is None:
            # centroide fuera (frontera o costa): usa la ubicación más frecuente de sus focos
            ubic = Counter(d.get("_dep_prov") for d in dets if d.get("_dep_prov") and d["_dep_prov"][0])
            if ubic:
                dep, prov = ubic.most_common(1)[0][0]

    recencia = math.exp(-horas / 24.0)
    prioridad = round((frp_total + 2.0 * n) * (0.25 + recencia), 2)

    conf = Counter(d["confianza"] for d in dets)
    sats = Counter(d["satelite"] for d in dets)
    sensores = sorted({d["sensor"] for d in dets})

    return {
        "id": f"F{abs(lat):.3f}{'S' if lat < 0 else 'N'}{abs(lon):.3f}W".replace(".", ""),
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "utm": geo.utm_texto(lat, lon),
        "departamento": dep,
        "provincia": prov,
        "detecciones": n,
        "frp_total": round(frp_total, 1),
        "frp_max": round(max(d["frp"] for d in dets), 1),
        "brillo_max": round(max(d["brillo"] for d in dets), 1),
        "area_pixeles_ha": round(area_pix_km2 * 100, 1),
        "area_envolvente_ha": round(area_env_km2 * 100, 1),
        "envolvente": [[round(y, 5), round(x, 5)] for x, y in hull],  # [lat, lon] para Leaflet
        "primera": t_min.isoformat().replace("+00:00", "Z"),
        "ultima": t_max.isoformat().replace("+00:00", "Z"),
        "horas_desde_ultima": round(horas, 1),
        "estado": estado_por_edad(horas),
        "nivel": nivel,
        "nivel_texto": etiqueta,
        "prioridad": prioridad,
        "satelites": dict(sats),
        "sensores": sensores,
        "confianza": {"baja": conf.get("baja", 0), "nominal": conf.get("nominal", 0), "alta": conf.get("alta", 0)},
        "nocturnas": sum(1 for d in dets if d["dia_noche"] == "N"),
    }


def procesar(detecciones, ahora=None, solo_peru=True, confianza_min="baja", limites=None, eps_km=1.5):
    """Filtra, ubica y agrupa. Devuelve (detecciones_filtradas, incendios)."""
    ahora = ahora or datetime.now(timezone.utc)
    orden = {"baja": 0, "nominal": 1, "alta": 2}
    umbral = orden.get(confianza_min, 0)
    limites = limites or geo.limites()

    dets = []
    for d in detecciones:
        if orden.get(d["confianza"], 1) < umbral:
            continue
        dep, prov = limites.ubicar(d["lon"], d["lat"])
        if solo_peru and dep is None:
            continue
        d = dict(d)
        d["_dep_prov"] = (dep, prov)
        dets.append(d)

    grupos = agrupar(dets, eps_km=eps_km)
    incendios = []
    vistos = Counter()
    for idxs in grupos:
        miembros = [dets[i] for i in idxs]
        inc = resumir_incendio(miembros, ahora, limites)
        if inc["departamento"] is None:
            inc["departamento"] = "Fuera del Perú"
        vistos[inc["id"]] += 1
        if vistos[inc["id"]] > 1:  # IDs únicos aunque dos centroides redondeen igual
            inc["id"] = f"{inc['id']}-{vistos[inc['id']]}"
        for d in miembros:
            d["_incendio"] = inc["id"]
        incendios.append(inc)

    incendios.sort(key=lambda x: x["prioridad"], reverse=True)
    return dets, incendios


def resumen_departamentos(incendios, detecciones):
    tabla = defaultdict(lambda: {"focos": 0, "incendios": 0, "activos": 0, "frp_total": 0.0,
                                 "area_ha": 0.0, "nivel_max": 0})
    for d in detecciones:
        dep = (d.get("_dep_prov") or (None, None))[0] or "Fuera del Perú"
        tabla[dep]["focos"] += 1
    for inc in incendios:
        t = tabla[inc["departamento"]]
        t["incendios"] += 1
        t["activos"] += 1 if inc["estado"] == "activo" else 0
        t["frp_total"] += inc["frp_total"]
        t["area_ha"] += inc["area_pixeles_ha"]
        t["nivel_max"] = max(t["nivel_max"], inc["nivel"])
    filas = []
    for dep, t in tabla.items():
        filas.append({"departamento": dep, **t, "frp_total": round(t["frp_total"], 1),
                      "area_ha": round(t["area_ha"], 1)})
    filas.sort(key=lambda r: (r["frp_total"], r["focos"]), reverse=True)
    return filas
