"""Utilidades geográficas: límites del Perú, punto en polígono, distancias, UTM y envolventes."""

import json
import math
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# Caja envolvente del Perú (oeste, sur, este, norte) con un pequeño margen
PERU_BBOX = (-81.6, -18.6, -68.4, 0.2)

KM_PER_DEG_LAT = 110.574
KM_PER_DEG_LON_EQ = 111.320

DEPARTAMENTOS_BONITOS = {
    "AMAZONAS": "Amazonas", "ANCASH": "Áncash", "APURIMAC": "Apurímac", "AREQUIPA": "Arequipa",
    "AYACUCHO": "Ayacucho", "CAJAMARCA": "Cajamarca", "CALLAO": "Callao", "CUSCO": "Cusco",
    "HUANCAVELICA": "Huancavelica", "HUANUCO": "Huánuco", "ICA": "Ica", "JUNIN": "Junín",
    "LA LIBERTAD": "La Libertad", "LAMBAYEQUE": "Lambayeque", "LIMA": "Lima", "LORETO": "Loreto",
    "MADRE DE DIOS": "Madre de Dios", "MOQUEGUA": "Moquegua", "PASCO": "Pasco", "PIURA": "Piura",
    "PUNO": "Puno", "SAN MARTIN": "San Martín", "TACNA": "Tacna", "TUMBES": "Tumbes", "UCAYALI": "Ucayali",
}


def nombre_bonito(nombre: str) -> str:
    if not nombre:
        return nombre
    n = nombre.strip().upper()
    if n in DEPARTAMENTOS_BONITOS:
        return DEPARTAMENTOS_BONITOS[n]
    # Provincias: capitalización simple preservando conectores
    pequenas = {"DE", "DEL", "LA", "LAS", "LOS", "Y"}
    partes = []
    for i, p in enumerate(n.split()):
        partes.append(p.lower() if (p in pequenas and i > 0) else p.capitalize())
    return " ".join(partes)


# --------------------------------------------------------------------------------------
# Punto en polígono
# --------------------------------------------------------------------------------------

def _ring_contains(ring, x, y):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


def _polygon_contains(poly, x, y):
    if not poly or not _ring_contains(poly[0], x, y):
        return False
    for hole in poly[1:]:
        if _ring_contains(hole, x, y):
            return False
    return True


def _bbox_of(coords):
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for cc in c:
                walk(cc)

    walk(coords)
    return (min(xs), min(ys), max(xs), max(ys))


class Region:
    __slots__ = ("nombre", "padre", "polys", "bbox")

    def __init__(self, nombre, padre, geometry):
        self.nombre = nombre
        self.padre = padre
        if geometry["type"] == "Polygon":
            self.polys = [geometry["coordinates"]]
        else:
            self.polys = geometry["coordinates"]
        self.bbox = _bbox_of(geometry["coordinates"])

    def contains(self, lon, lat):
        b = self.bbox
        if lon < b[0] or lon > b[2] or lat < b[1] or lat > b[3]:
            return False
        return any(_polygon_contains(p, lon, lat) for p in self.polys)


class Limites:
    """Departamentos y provincias del Perú (geometría simplificada)."""

    def __init__(self, data_dir: str = DATA_DIR):
        with open(os.path.join(data_dir, "peru_departamentos.json"), encoding="utf-8") as f:
            self.dep_geojson = json.load(f)
        with open(os.path.join(data_dir, "peru_provincias.json"), encoding="utf-8") as f:
            prov = json.load(f)
        self.departamentos = [
            Region(nombre_bonito(ft["properties"]["d"]), None, ft["geometry"])
            for ft in self.dep_geojson["features"]
        ]
        self.provincias = [
            Region(nombre_bonito(ft["properties"]["p"]), nombre_bonito(ft["properties"]["d"]), ft["geometry"])
            for ft in prov["features"]
        ]

    def ubicar(self, lon, lat):
        """Devuelve (departamento, provincia) o (None, None) si el punto está fuera del Perú."""
        dep = next((r.nombre for r in self.departamentos if r.contains(lon, lat)), None)
        if dep is None:
            return None, None
        prov = next(
            (r.nombre for r in self.provincias if r.padre == dep and r.contains(lon, lat)),
            None,
        )
        if prov is None:  # la geometría simplificada puede dejar huecos mínimos entre provincias
            prov = next((r.nombre for r in self.provincias if r.contains(lon, lat)), None)
        return dep, prov

    def nombres_departamentos(self):
        return sorted(r.nombre for r in self.departamentos)


_limites = None


def limites() -> Limites:
    global _limites
    if _limites is None:
        _limites = Limites()
    return _limites


def en_bbox_peru(lon, lat, margen=0.0):
    w, s, e, n = PERU_BBOX
    return (w - margen) <= lon <= (e + margen) and (s - margen) <= lat <= (n + margen)


# --------------------------------------------------------------------------------------
# Distancias, rumbos, proyecciones
# --------------------------------------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def rumbo_grados(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


PUNTOS_CARDINALES = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                     "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]


def cardinal(grados):
    if grados is None:
        return None
    return PUNTOS_CARDINALES[int(((grados % 360) + 11.25) // 22.5) % 16]


def destino(lat, lon, rumbo, dist_km):
    """Punto a una distancia y rumbo dados (esfera)."""
    r = 6371.0088
    d = dist_km / r
    b = math.radians(rumbo)
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def latlon_a_utm(lat, lon):
    """Convierte WGS84 a UTM. Devuelve (zona, hemisferio, este_m, norte_m)."""
    zona = int((lon + 180) / 6) + 1
    lon0 = math.radians((zona - 1) * 6 - 180 + 3)
    a = 6378137.0
    f = 1 / 298.257223563
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    k0 = 0.9996
    phi = math.radians(lat)
    lam = math.radians(lon)
    sin_phi, cos_phi, tan_phi = math.sin(phi), math.cos(phi), math.tan(phi)
    n = a / math.sqrt(1 - e2 * sin_phi ** 2)
    t = tan_phi ** 2
    c = ep2 * cos_phi ** 2
    aa = cos_phi * (lam - lon0)
    m = a * (
        (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
        - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
        + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
        - (35 * e2 ** 3 / 3072) * math.sin(6 * phi)
    )
    este = k0 * n * (aa + (1 - t + c) * aa ** 3 / 6 + (5 - 18 * t + t ** 2 + 72 * c - 58 * ep2) * aa ** 5 / 120) + 500000.0
    norte = k0 * (m + n * tan_phi * (aa ** 2 / 2 + (5 - t + 9 * c + 4 * c ** 2) * aa ** 4 / 24
                                     + (61 - 58 * t + t ** 2 + 600 * c - 330 * ep2) * aa ** 6 / 720))
    hemisferio = "S" if lat < 0 else "N"
    if lat < 0:
        norte += 10000000.0
    return zona, hemisferio, este, norte


def utm_texto(lat, lon):
    z, h, e, n = latlon_a_utm(lat, lon)
    return f"{z}{h} {e:,.0f} E {n:,.0f} N".replace(",", " ")


# --------------------------------------------------------------------------------------
# Envolvente convexa y áreas
# --------------------------------------------------------------------------------------

def envolvente_convexa(puntos):
    """Monotone chain. puntos: lista de (lon, lat). Devuelve el anillo sin cerrar."""
    pts = sorted(set(puntos))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def area_poligono_km2(anillo):
    """Área de un anillo (lon, lat) proyectado localmente (equirectangular)."""
    if len(anillo) < 3:
        return 0.0
    lat0 = sum(p[1] for p in anillo) / len(anillo)
    lon0 = sum(p[0] for p in anillo) / len(anillo)
    kx = KM_PER_DEG_LON_EQ * math.cos(math.radians(lat0))
    xy = [((p[0] - lon0) * kx, (p[1] - lat0) * KM_PER_DEG_LAT) for p in anillo]
    s = 0.0
    for i in range(len(xy)):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % len(xy)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def huella_pixel(lat, lon, scan_km, track_km):
    """Rectángulo aproximado del píxel satelital (lon, lat) alrededor del centro del foco."""
    dx = (scan_km / 2.0) / (KM_PER_DEG_LON_EQ * math.cos(math.radians(lat)))
    dy = (track_km / 2.0) / KM_PER_DEG_LAT
    return [(lon - dx, lat - dy), (lon + dx, lat - dy), (lon + dx, lat + dy), (lon - dx, lat + dy)]


def area_union_pixeles_km2(detecciones, celda_deg=0.0008):
    """Área de la unión de las huellas de píxel, por rasterización en una malla fina.

    Evita contar dos veces el mismo terreno cuando varios satélites detectan el mismo fuego.
    """
    celdas = set()
    lat_ref = None
    for d in detecciones:
        lat, lon = d["lat"], d["lon"]
        lat_ref = lat if lat_ref is None else lat_ref
        dx = (d["scan"] / 2.0) / (KM_PER_DEG_LON_EQ * math.cos(math.radians(lat)))
        dy = (d["track"] / 2.0) / KM_PER_DEG_LAT
        i0, i1 = math.floor((lon - dx) / celda_deg), math.floor((lon + dx) / celda_deg)
        j0, j1 = math.floor((lat - dy) / celda_deg), math.floor((lat + dy) / celda_deg)
        marcadas = 0
        for i in range(i0, i1 + 1):
            cx = (i + 0.5) * celda_deg
            if not (lon - dx <= cx <= lon + dx):
                continue
            for j in range(j0, j1 + 1):
                cy = (j + 0.5) * celda_deg
                if lat - dy <= cy <= lat + dy:
                    celdas.add((i, j))
                    marcadas += 1
        if not marcadas:
            # píxel más pequeño que la celda: cuenta la celda del centro
            celdas.add((math.floor(lon / celda_deg), math.floor(lat / celda_deg)))
    if lat_ref is None:
        return 0.0
    area_celda = (celda_deg * KM_PER_DEG_LON_EQ * math.cos(math.radians(lat_ref))) * (celda_deg * KM_PER_DEG_LAT)
    return len(celdas) * area_celda
