"""Rásteres web sin dependencias externas: decodificación PNG, relieve y cobertura del suelo.

* Relieve: teselas Terrarium (AWS Open Data, Mapzen; Copernicus/SRTM), sin clave.
* Cobertura: Esri Land Cover 10 m (Sentinel-2, Impact Observatory; CC BY 4.0), sin clave.
"""

import json
import math
import struct
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from .cache import cache

TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
COBERTURA_URL = ("https://ic.imagery1.arcgis.com/arcgis/rest/services/"
                 "Sentinel2_10m_LandCover/ImageServer/exportImage")
USER_AGENT = "VigiaIncendiosPeru/1.0 (pronóstico de propagación)"

# Clases de Esri Land Cover (Impact Observatory)
COBERTURAS = {
    1: "Agua", 2: "Árboles", 4: "Vegetación inundable", 5: "Cultivos", 7: "Zona urbana",
    8: "Suelo desnudo", 9: "Nieve o hielo", 10: "Nubes", 11: "Pastizal y matorral",
}

_CANALES = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


# --------------------------------------------------------------------------------------
# PNG
# --------------------------------------------------------------------------------------

def _desfiltrar(crudo, ancho, alto, bpp):
    paso = ancho * bpp
    salida = bytearray(alto * paso)
    previa = bytearray(paso)
    pos = 0
    for fila in range(alto):
        tipo = crudo[pos]
        linea = bytearray(crudo[pos + 1:pos + 1 + paso])
        pos += 1 + paso
        if tipo == 1:
            for i in range(bpp, paso):
                linea[i] = (linea[i] + linea[i - bpp]) & 0xFF
        elif tipo == 2:
            for i in range(paso):
                linea[i] = (linea[i] + previa[i]) & 0xFF
        elif tipo == 3:
            for i in range(paso):
                izq = linea[i - bpp] if i >= bpp else 0
                linea[i] = (linea[i] + ((izq + previa[i]) >> 1)) & 0xFF
        elif tipo == 4:
            for i in range(paso):
                a = linea[i - bpp] if i >= bpp else 0
                b = previa[i]
                c = previa[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                linea[i] = (linea[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        elif tipo != 0:
            raise ValueError(f"filtro PNG desconocido: {tipo}")
        salida[fila * paso:(fila + 1) * paso] = linea
        previa = linea
    return salida


def decodificar_png(datos: bytes):
    """Devuelve (ancho, alto, canales, bytes) para PNG de 8 bits sin entrelazar. La paleta se expande a RGB."""
    if datos[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("no es un PNG")
    pos, idat, paleta = 8, [], None
    ancho = alto = color = None
    while pos < len(datos):
        n, tipo = struct.unpack(">I4s", datos[pos:pos + 8])
        cuerpo = datos[pos + 8:pos + 8 + n]
        if tipo == b"IHDR":
            ancho, alto, prof, color, _, _, entrelazado = struct.unpack(">IIBBBBB", cuerpo)
            if prof != 8 or entrelazado or color not in _CANALES:
                raise ValueError(f"PNG no soportado (bits={prof}, color={color}, entrelazado={entrelazado})")
        elif tipo == b"PLTE":
            paleta = cuerpo
        elif tipo == b"IDAT":
            idat.append(cuerpo)
        elif tipo == b"IEND":
            break
        pos += 12 + n
    canales = _CANALES[color]
    pix = _desfiltrar(zlib.decompress(b"".join(idat)), ancho, alto, canales)
    if color == 3:
        rgb = bytearray(len(pix) * 3)
        for i, k in enumerate(pix):
            rgb[3 * i:3 * i + 3] = paleta[3 * k:3 * k + 3]
        return ancho, alto, 3, rgb
    return ancho, alto, canales, pix


# --------------------------------------------------------------------------------------
# Relieve (Terrarium)
# --------------------------------------------------------------------------------------

def _pixel_global(lat, lon, z):
    n = 256 * 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def zoom_para(celda_m, lat, max_teselas, oeste, sur, este, norte):
    """Zoom con píxel de a lo más media celda, sin superar max_teselas."""
    z = math.ceil(math.log2(156543.03 * math.cos(math.radians(lat)) / max(celda_m / 2.0, 1.0)))
    z = max(5, min(13, z))
    while z > 5:
        x0, y0 = _pixel_global(norte, oeste, z)
        x1, y1 = _pixel_global(sur, este, z)
        if (int(x1 // 256) - int(x0 // 256) + 1) * (int(y1 // 256) - int(y0 // 256) + 1) <= max_teselas:
            break
        z -= 1
    return z


def _tesela_terrarium(z, x, y, session):
    clave = f"dem:{z}:{x}:{y}"
    hit = cache.get(clave)
    if hit is not None:
        return hit
    r = session.get(TERRARIUM_URL.format(z=z, x=x, y=y), timeout=30, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    ancho, alto, canales, pix = decodificar_png(r.content)
    elev = [0.0] * (ancho * alto)
    for i in range(ancho * alto):
        k = i * canales
        elev[i] = pix[k] * 256.0 + pix[k + 1] + pix[k + 2] / 256.0 - 32768.0
    return cache.set(clave, elev, 24 * 3600)


def malla_elevacion(lats, lons, celda_m, session=None):
    """Elevación (m) en cada nodo de la malla (filas = lats de norte a sur, columnas = lons). Interpolación bilineal."""
    session = session or requests.Session()
    lat_c = sum(lats) / len(lats)
    z = zoom_para(celda_m, lat_c, 9, min(lons), min(lats), max(lons), max(lats))
    px = [_pixel_global(lat_c, lo, z)[0] for lo in lons]
    py = [_pixel_global(la, lons[0], z)[1] for la in lats]
    necesarias = {(int(x // 256), int(y // 256)) for x in (min(px) - 1, max(px) + 1) for y in (min(py) - 1, max(py) + 1)}
    xs = range(min(t[0] for t in necesarias), max(t[0] for t in necesarias) + 1)
    ys = range(min(t[1] for t in necesarias), max(t[1] for t in necesarias) + 1)
    pedidos = [(x, y) for x in xs for y in ys]
    with ThreadPoolExecutor(max_workers=4) as ex:
        teselas = dict(zip(pedidos, ex.map(lambda t: _tesela_terrarium(z, t[0], t[1], session), pedidos)))

    def valor(gx, gy):
        t = teselas[(gx // 256, gy // 256)]
        return t[(gy % 256) * 256 + (gx % 256)]

    salida = []
    for y in py:
        y0 = int(math.floor(y - 0.5))
        fy = y - 0.5 - y0
        for x in px:
            x0 = int(math.floor(x - 0.5))
            fx = x - 0.5 - x0
            a, b = valor(x0, y0), valor(x0 + 1, y0)
            c, d = valor(x0, y0 + 1), valor(x0 + 1, y0 + 1)
            salida.append((a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy)
    return salida


# --------------------------------------------------------------------------------------
# Cobertura del suelo (Esri Land Cover)
# --------------------------------------------------------------------------------------

def _rango_anio(anio):
    ini = datetime(anio, 1, 1, tzinfo=timezone.utc)
    fin = datetime(anio + 1, 1, 1, tzinfo=timezone.utc)
    return f"{int(ini.timestamp() * 1000)},{int(fin.timestamp() * 1000) - 1}"


def malla_cobertura(oeste, sur, este, norte, nx, ny, session=None, sub=3):
    """Clase de cobertura dominante por celda (lista fila a fila desde el norte; 0 = sin dato).

    Pide la imagen con sub×sub píxeles por celda y toma la moda, sin contar nubes si hay otra clase.
    Usa el mosaico anual más reciente disponible.
    """
    session = session or requests.Session()
    clave = f"lc:{oeste:.4f}:{sur:.4f}:{este:.4f}:{norte:.4f}:{nx}:{ny}"
    hit = cache.get(clave)
    if hit is not None:
        return hit
    ancho, alto = min(nx * sub, 1500), min(ny * sub, 1500)
    anio = datetime.now(timezone.utc).year - 1
    ultimo_error = None
    for a in (anio, anio - 1, None):
        params = {
            "bbox": f"{oeste},{sur},{este},{norte}", "bboxSR": 4326, "imageSR": 4326,
            "size": f"{ancho},{alto}", "format": "png", "pixelType": "U8",
            "interpolation": "RSP_NearestNeighbor", "noData": 0,
            "renderingRule": json.dumps({"rasterFunction": "None"}), "f": "image",
        }
        if a:
            params["time"] = _rango_anio(a)
        r = session.get(COBERTURA_URL, params=params, timeout=60, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200 or not r.content.startswith(b"\x89PNG"):
            ultimo_error = f"HTTP {r.status_code}"
            continue
        w, h, canales, pix = decodificar_png(r.content)
        valores = pix[::canales]
        if not any(valores):
            ultimo_error = "imagen vacía"
            continue
        salida = []
        for fila in range(ny):
            j0, j1 = fila * h // ny, max(fila * h // ny + 1, (fila + 1) * h // ny)
            for col in range(nx):
                i0, i1 = col * w // nx, max(col * w // nx + 1, (col + 1) * w // nx)
                cuenta = Counter(valores[j * w + i] for j in range(j0, j1) for i in range(i0, i1))
                cuenta.pop(0, None)
                if len(cuenta) > 1:
                    cuenta.pop(10, None)
                salida.append(cuenta.most_common(1)[0][0] if cuenta else 0)
        res = {"clases": salida, "anio": a}
        return cache.set(clave, res, 24 * 3600)
    raise RuntimeError(f"Esri Land Cover no respondió ({ultimo_error})")
