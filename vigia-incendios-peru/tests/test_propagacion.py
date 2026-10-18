import math
import struct
import zlib
from datetime import datetime, timedelta, timezone

import pytest

from vigia import propagacion, raster, rothermel

AHORA = datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc)
CH_H = 60 / 66  # ft/min -> cadenas por hora


# --------------------------------------------------------------------------------------
# Rothermel: casos de prueba de firelab/behave (GS4, humedad baja, viento 5 mph a 20 ft,
# pendiente 30 %, copa 50 % de 30 ft con razón 0,5). Viento expresado como procedencia.
# --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def gs4():
    lecho = rothermel.preparar(rothermel.MODELOS["GS4"], 0.06, 0.07, 0.08, 0.60, 0.90)
    u = 5 * 88 * rothermel.factor_viento(2.1, cobertura=0.5, altura_ft=30, razon_copa=0.5)
    return lecho, u


def _ros(gs4, desde_viento, aspecto):
    lecho, u = gs4
    return lecho.maxima(u, (desde_viento + 180) % 360, 0.3, (aspecto + 180) % 360)


def test_rothermel_igual_a_behave(gs4):
    lecho, u = gs4
    assert lecho.fuente_calor * (1 + lecho.phi_viento(u)) == pytest.approx(1164.267376, rel=1e-3)
    ros, _, lb = _ros(gs4, 0, 0)  # ladera orientada al norte y viento del norte: empuja cuesta arriba
    assert ros * CH_H == pytest.approx(8.876216, rel=1e-3)
    e = rothermel.excentricidad(lb)
    atras = ros * (1 - e) / (1 + e)
    assert atras * CH_H == pytest.approx(2.91614659, rel=1e-3)
    assert (ros + atras) / (2 * lb) * CH_H == pytest.approx(5.08766627, rel=1e-3)
    assert rothermel.en_direccion(ros, e, 180) == pytest.approx(atras)


def test_rothermel_viento_cruzado_y_orientaciones(gs4):
    assert _ros(gs4, 90, 0)[0] * CH_H == pytest.approx(7.091665, rel=1e-3)
    assert _ros(gs4, 45, 215)[0] * CH_H == pytest.approx(4.113265, rel=1e-3)
    assert _ros(gs4, 45, 5)[0] * CH_H == pytest.approx(8.503960, rel=1e-3)


def test_rothermel_humedad_viento_y_pendiente():
    gr2 = rothermel.MODELOS["GR2"]
    seco = rothermel.preparar(gr2, 0.05, 0.06, 0.07, 0.30, 0.60)
    humedo = rothermel.preparar(gr2, 0.16, 0.17, 0.18, 1.2, 1.5)  # sobre la humedad de extinción
    assert humedo.r0 == 0
    assert seco.r0 > 0
    sin_viento = seco.maxima(0, 0)[0]
    con_viento = seco.maxima(10 * rothermel.KMH_A_FT_MIN * 0.36, 90)
    assert con_viento[0] > 3 * sin_viento and con_viento[1] == 90 and con_viento[2] > 1
    assert seco.maxima(0, 0, 0.5, 200)[0] > sin_viento
    assert seco.maxima(0, 0, 0.5, 200)[1] == pytest.approx(200)


# --------------------------------------------------------------------------------------
# PNG
# --------------------------------------------------------------------------------------

def _png(ancho, alto, color, filas, filtros):
    canales = {0: 1, 2: 3}[color]
    crudo = b""
    previa = bytes(ancho * canales)
    for fila, f in zip(filas, filtros):
        fila = bytes(fila)
        out = bytearray()
        for i, x in enumerate(fila):
            a = fila[i - canales] if i >= canales else 0
            b = previa[i]
            c = previa[i - canales] if i >= canales else 0
            pred = {0: 0, 1: a, 2: b, 3: (a + b) // 2}.get(f)
            if f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
            out.append((x - pred) & 0xFF)
        crudo += bytes([f]) + bytes(out)
        previa = fila

    def bloque(tipo, datos):
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos))

    return (b"\x89PNG\r\n\x1a\n" + bloque(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 8, color, 0, 0, 0))
            + bloque(b"IDAT", zlib.compress(crudo)) + bloque(b"IEND", b""))


def test_png_todos_los_filtros():
    filas = [[(17 * i + 29 * j) % 256 for i in range(4 * 3)] for j in range(5)]
    w, h, canales, pix = raster.decodificar_png(_png(4, 5, 2, filas, [0, 1, 2, 3, 4]))
    assert (w, h, canales) == (4, 5, 3)
    assert list(pix) == [v for fila in filas for v in fila]
    gris = [[11, 200, 3], [250, 0, 7]]
    assert list(raster.decodificar_png(_png(3, 2, 0, gris, [4, 3]))[3]) == [11, 200, 3, 250, 0, 7]


# --------------------------------------------------------------------------------------
# Humedad del combustible y progresión observada
# --------------------------------------------------------------------------------------

def test_humedad_de_equilibrio_y_lluvia():
    assert propagacion.emc_simard(30, 20) == pytest.approx(0.0416, abs=0.001)
    assert propagacion.emc_simard(10, 95) > propagacion.emc_simard(30, 20)
    meteo = {"temp": [25] * 6, "hr": [30] * 6, "nubes": [0] * 6, "dia": [1, 1, 1, 0, 0, 0],
             "lluvia": [0, 0, 3.0, 0, 0, 0]}
    m = propagacion.humedad_muerta(meteo)
    assert m[2][0] > 0.2 > m[1][0]          # la lluvia moja el combustible fino
    assert m[5][0] < m[3][0]                # y se seca en pocas horas
    assert m[5][2] > m[1][2]                # el de 100 h tarda más
    assert propagacion.humedad_viva(0)[0] == pytest.approx(1.2)
    assert propagacion.humedad_viva(60)[0] < 0.35


def _det(lat, lon, horas_antes, scan=0.4):
    return {"lat": lat, "lon": lon, "frp": 5.0, "brillo": 330.0, "scan": scan, "track": scan,
            "t": AHORA - timedelta(hours=horas_antes), "satelite": "NOAA-20", "fuente": "VIIRS_NOAA20",
            "sensor": "VIIRS", "confianza": "nominal", "dia_noche": "D"}


def test_progresion_observada():
    dets = [_det(-13.6, -71.95, 8), _det(-13.6 + 1.0 / 110.574, -71.95, 2),
            _det(-13.6 - 0.002, -71.95, 7)]  # contigua a la primera: no es avance
    obs = propagacion.progresion_observada(dets)
    assert obs["pasadas"] == 3 and obs["n"] == 1
    assert obs["ros_mediana_m_min"] == pytest.approx(1000 / 360, rel=0.02)
    assert obs["rumbo_txt"] == "N"
    # dos pasadas separadas por menos de 2 h: posición demasiado ruidosa para medir
    assert propagacion.progresion_observada([_det(-13.6, -71.95, 3), _det(-13.59, -71.95, 2)])["n"] == 0


# --------------------------------------------------------------------------------------
# Simulación por tiempo mínimo de recorrido
# --------------------------------------------------------------------------------------

def _escenario(n=41, celda=50.0, viento_kmh=0.0, desde=270.0, horas=120, comb=None, humedo_hasta=0):
    seco = rothermel.preparar(rothermel.MODELOS["GR2"], 0.05, 0.06, 0.07, 0.3, 0.6)
    humedo = rothermel.preparar(rothermel.MODELOS["GR2"], 0.3, 0.3, 0.3, 1.2, 1.5)
    u = viento_kmh / 1.15 * rothermel.KMH_A_FT_MIN * propagacion._waf("GR2")
    lechos = [{"GR2": humedo if h < humedo_hasta else seco} for h in range(horas)]
    comb = comb or ["GR2"] * (n * n)
    return propagacion.Escenario(n, celda, comb, [0.0] * (n * n), [0.0] * (n * n), lechos,
                                 [{"GR2": u}] * horas, [(desde + 180) % 360] * horas)


def test_simulacion_isotropa_sin_viento():
    n = 41
    esc = _escenario(n)
    c = n // 2
    llegada = propagacion.simular(esc, {c * n + c: 0.0}, 119)
    r = 15
    tiempos = [llegada[(c + dj) * n + c + di] / math.hypot(di, dj)
               for di, dj in [(r, 0), (0, r), (-r, 0), (0, -r), (r, r), (-r, r), (r, -r), (-r, -r), (r, 8)]]
    assert max(tiempos) / min(tiempos) < 1.08


def test_simulacion_viento_alarga_hacia_sotavento():
    n = 41
    esc = _escenario(n, viento_kmh=20, desde=270)  # viento del oeste: avanza al este
    c = n // 2
    llegada = propagacion.simular(esc, {c * n + c: 0.0}, 119)
    este, oeste, norte = llegada[c * n + c + 10], llegada[c * n + c - 10], llegada[(c - 10) * n + c]
    assert este < norte < oeste < math.inf
    assert oeste / este > 4


def test_barrera_detiene_el_fuego():
    n = 21
    comb = ["GR2"] * (n * n)
    for j in range(n):
        comb[j * n + 12] = None  # río de una celda de ancho
    llegada = propagacion.simular(_escenario(n, viento_kmh=20, comb=comb), {10 * n + 5: 0.0}, 119)
    assert llegada[10 * n + 11] < math.inf
    assert all(llegada[j * n + i] == math.inf for j in range(n) for i in range(13, n))


def test_espera_a_condiciones_favorables_y_persistencia():
    n = 21
    c = 10 * n + 10
    seco = propagacion.simular(_escenario(n), {c: 0.0}, 119)[c + 1]
    llegada = propagacion.simular(_escenario(n, humedo_hasta=5), {c: 0.0}, 119)
    assert llegada[c + 1] == pytest.approx(5.0 + seco)       # espera a que seque y luego avanza
    llegada = propagacion.simular(_escenario(n, humedo_hasta=30), {c: 0.0}, 119, persistencia_h=16)
    assert llegada[c + 1] == math.inf                        # se apaga antes de que mejore


def test_isolineas_de_un_circulo():
    n = 41
    valores = [100.0 if math.hypot(i - 20, j - 20) <= 10 else 0.0 for j in range(n) for i in range(n)]
    lineas = propagacion.isolineas(valores, n, 50)
    assert len(lineas) == 1
    linea = lineas[0]
    assert linea[0] == linea[-1]  # cerrada
    radios = [math.hypot(f - 20, c - 20) for f, c in linea]
    assert 9.5 < min(radios) and max(radios) < 11


# --------------------------------------------------------------------------------------
# Pronóstico completo sin red (servicios simulados)
# --------------------------------------------------------------------------------------

def test_pronostico_completo_sin_red(monkeypatch):
    def meteo_falsa(lat, lon, desde, hasta, ahora, session):
        t0 = int(desde.timestamp() // 3600 * 3600)
        n = int((hasta.timestamp() - t0) // 3600) + 1
        horas = [t0 + 3600 * k for k in range(n)]
        dia = [1 if 11 <= datetime.fromtimestamp(t, tz=timezone.utc).hour <= 23 else 0 for t in horas]
        dias = [int(AHORA.timestamp() // 86400 * 86400) - 86400 * k for k in range(60, -2, -1)]
        return {"t": horas, "temp": [24.0] * n, "hr": [25.0] * n, "viento": [15.0] * n, "dir": [270.0] * n,
                "lluvia": [0.0] * n, "nubes": [5.0] * n, "dia": dia, "lluvia_diaria": [(d, 0.0) for d in dias],
                "elevacion": 3900.0}

    monkeypatch.setattr(propagacion, "meteo_horaria", meteo_falsa)
    monkeypatch.setattr(raster, "malla_elevacion", lambda lats, lons, celda, session=None: [3900.0] * (len(lats) * len(lons)))
    monkeypatch.setattr(raster, "malla_cobertura", lambda *a, **k: {"clases": [11] * (a[4] * a[5]), "anio": 2025})
    dets = [_det(-13.6 + a * 0.0035, -71.95 + b * 0.0035, 9) for a in (-1, 0, 1) for b in (-1, 0, 1)]
    dets += [_det(-13.6 + a * 0.0035, -71.95 + 0.012 + b * 0.0035, 3) for a in (-1, 0, 1) for b in (0, 1)]
    inc = {"id": "FPRUEBA", "lat": -13.6, "lon": -71.95, "departamento": "Cusco"}

    r = propagacion.pronosticar(inc, dets, AHORA, horizonte_h=12, miembros=6, n=41)
    assert r["disponible"]
    assert r["horizontes"] == [0, 3, 6, 12]
    areas = [p["area_ha"] for p in r["por_horizonte"]]
    assert areas == sorted(areas) and areas[-1] > areas[0]
    assert r["resumen"]["rumbo_txt"] in ("ENE", "E", "ESE")       # viento del oeste
    assert r["resumen"]["modelo_frente"] == "GR2"                 # pastizal sobre 3300 m
    assert r["calibracion"]["aplicada"] and r["calibracion"]["n"] >= 3
    assert r["observado"]["rumbo_txt"] in ("ENE", "E", "ESE")
    assert len(r["prob"]["12"]) == 41 * 41 and len(r["eta"]) == 41 * 41
    assert all(a >= b for a, b in zip(r["prob"]["12"], r["prob"]["3"]))
    assert any(i["h"] == 12 and i["nivel"] == 50 and i["lineas"] for i in r["isolineas"])

    viejo = [_det(-13.6, -71.95, 50)]
    assert not propagacion.pronosticar(inc, viejo, AHORA)["disponible"]
