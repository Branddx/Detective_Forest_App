from datetime import datetime, timezone

import pytest

from vigia import aire

AHORA = datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc)


# Bordes de los intervalos de la tabla 3 de la R. M. N.° 181-2016-MINAM: (contaminante, µg/m³, INCA, calificación)
@pytest.mark.parametrize("clave, conc, valor, cat", [
    ("pm2_5", 12.5, 50, "buena"), ("pm2_5", 12.6, 51, "moderada"), ("pm2_5", 25.0, 100, "moderada"),
    ("pm2_5", 25.1, 101, "mala"), ("pm2_5", 125.0, 500, "mala"), ("pm2_5", 125.1, 501, "umbral"),
    ("pm10", 75, 50, "buena"), ("pm10", 76, 51, "moderada"), ("pm10", 150, 100, "moderada"),
    ("pm10", 151, 101, "mala"), ("pm10", 250, 167, "mala"), ("pm10", 251, 168, "umbral"),
    ("carbon_monoxide", 5049, 50, "buena"), ("carbon_monoxide", 5050, 51, "moderada"),
    ("carbon_monoxide", 10049, 100, "moderada"), ("carbon_monoxide", 15049, 150, "mala"),
    ("carbon_monoxide", 15050, 151, "umbral"),
    ("ozone", 60, 50, "buena"), ("ozone", 120, 100, "moderada"), ("ozone", 210, 175, "mala"), ("ozone", 211, 176, "umbral"),
    ("nitrogen_dioxide", 100, 50, "buena"), ("nitrogen_dioxide", 201, 101, "mala"), ("nitrogen_dioxide", 301, 151, "umbral"),
    ("sulphur_dioxide", 10, 50, "buena"), ("sulphur_dioxide", 20, 100, "moderada"), ("sulphur_dioxide", 21, 105, "mala"),
    ("sulphur_dioxide", 501, 2505, "umbral"),
])
def test_inca_segun_tabla_oficial(clave, conc, valor, cat):
    assert aire.inca(clave, conc) == (valor, cat)


def test_promedio_movil_y_contaminante_dominante():
    serie = [10.0] * 30
    assert aire.promedio_movil(serie, 29, 24) == pytest.approx(10.0)
    assert aire.promedio_movil([None] * 10 + [5.0] * 4, 13, 8) is None  # faltan más de la cuarta parte
    horario = {"pm2_5": [5.0] * 6 + [40.0] * 24, "ozone": [130.0] * 30, "carbon_monoxide": [300.0] * 30}
    e = aire.evaluar(horario, 29)
    assert e["contaminante"] == "PM2.5" and e["categoria"] == "mala" and e["inca"] == 160
    assert e["detalle"]["ozone"]["categoria"] == "mala" and e["detalle"]["ozone"]["inca"] == 108
    assert e["detalle"]["carbon_monoxide"]["categoria"] == "buena"


def test_rumbo_del_humo():
    meteo = {"pronostico": {"dir": [270, 280, 260, 270, 270, 270, 90], "viento": [10, 12, 8, 10, 10, 10, 40]}}
    assert aire.rumbo_humo(meteo) == pytest.approx(90, abs=2)  # viento del oeste: humo al este
    assert aire.rumbo_humo({"pronostico": {"dir": [0, 180], "viento": [5, 5]}}) is None  # se anula: calma


def test_lugares_y_recomendaciones():
    lugares = aire.parsear_lugares("Paruro|-13.761|-71.845;malo|x|y;Cusco|-13.52|-71.97")
    assert lugares == [("Paruro", -13.761, -71.845), ("Cusco", -13.52, -71.97)]
    assert aire.recomendaciones("buena")["acciones"] == []
    mala = aire.recomendaciones("mala")
    assert "Evitar realizar ejercicio" in mala["oficial"] and any("N95" in a for a in mala["acciones"])
    umbral = aire.recomendaciones("umbral")
    assert "009-2003-SA" in umbral["oficial"] and any("106" in a for a in umbral["acciones"])


def test_calidad_alrededor_sin_red(monkeypatch):
    t0 = int(AHORA.timestamp()) - 30 * 3600

    def consultar_falso(puntos, session=None):
        salida = []
        for la, lo in puntos:
            humo = 60.0 if lo > -72.0 else 8.0  # humo al este del incendio
            salida.append({"time": [t0 + 3600 * k for k in range(60)], "pm2_5": [humo] * 60,
                           "pm10": [humo * 1.3] * 60, "ozone": [50.0] * 30 + [130.0] * 30,
                           "carbon_monoxide": [200.0] * 60, "nitrogen_dioxide": [1.0] * 60, "sulphur_dioxide": [1.0] * 60})
        return salida

    monkeypatch.setattr(aire, "consultar", consultar_falso)
    meteo = {"pronostico": {"dir": [270] * 6, "viento": [15] * 6}}
    r = aire.calidad_alrededor(-13.7, -72.1, lugares=[("Paruro", -13.75, -71.95)], ahora=AHORA, meteo=meteo)  # ~17 km
    assert r["humo_rumbo_txt"] == "E"
    assert r["foco"]["actual"]["categoria"] == "buena"
    paruro = next(p for p in r["puntos"] if p["nombre"] == "Paruro")
    assert paruro["a_sotavento"] and paruro["actual"]["categoria"] == "mala"
    assert r["peor"]["categoria"] == "mala"
    assert r["alerta"]["categoria"] == "mala" and r["alerta"]["motivo"] == "Paruro ahora"
    assert r["recomendaciones"]["acciones"]
    assert r["foco"]["pronostico"]["categoria"] == "mala" and r["foco"]["pronostico"]["contaminante"] == "O₃"
    assert len(r["puntos"]) == 1 + 16 + 1
    assert [s["pronostico"] for s in r["serie_foco"]].count(False) == 25
