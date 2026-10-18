"""Vigía de Incendios Perú — servidor web.

Ejecuta:  python app.py      (abre http://localhost:8000)
Configura FIRMS_MAP_KEY en .env para usar la API de área de NASA FIRMS.
"""

import csv
import gzip
import io
import json
import os
from datetime import datetime, timezone

import requests
from flask import Flask, Response, jsonify, request, send_from_directory

from vigia import __version__, aire, clustering, contacto, context, eonet, firms, geo, propagacion, weather
from vigia.errores import resumen
from vigia.cache import cache

BASE = os.path.dirname(os.path.abspath(__file__))


def _cargar_env(ruta=os.path.join(BASE, ".env")):
    if not os.path.exists(ruta):
        return
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_cargar_env()

CONFIG = {
    "map_key": os.environ.get("FIRMS_MAP_KEY", "").strip(),
    "carto_key": os.environ.get("CARTO_API_KEY", "").strip(),
    "demo": os.environ.get("DEMO", "0") == "1",
    "cache_firms_min": float(os.environ.get("CACHE_FIRMS_MIN", "10")),
    "viento_top_n": int(os.environ.get("VIENTO_TOP_N", "150")),
    "radio_recursos_km": float(os.environ.get("RADIO_RECURSOS_KM", "30")),
    "host": os.environ.get("HOST", "0.0.0.0"),
    "port": int(os.environ.get("PORT", "8000")),
}
CONFIG["contacto"], _avisos_contacto = contacto.desde_entorno(os.environ)
for _aviso in _avisos_contacto:
    print(f"Aviso (Contáctenos): {_aviso}")

app = Flask(__name__, static_folder=os.path.join(BASE, "static"), static_url_path="/static")
sesion = requests.Session()


# --------------------------------------------------------------------------------------
# Lógica principal
# --------------------------------------------------------------------------------------

def _parametros():
    rango = request.args.get("rango", "24h")
    if rango not in firms.RANGOS_HORAS:
        rango = "24h"
    fuentes = [f for f in request.args.get("fuentes", ",".join(firms.FUENTES)).split(",") if f in firms.FUENTES]
    fuentes = fuentes or list(firms.FUENTES)
    confianza = request.args.get("confianza", "baja")
    if confianza not in ("baja", "nominal", "alta"):
        confianza = "baja"
    solo_peru = request.args.get("solo_peru", "1") != "0"
    demo = CONFIG["demo"] or request.args.get("demo") == "1"
    return rango, fuentes, confianza, solo_peru, demo


def _detecciones(rango, fuentes, demo):
    key = f"firms:{rango}:{','.join(sorted(fuentes))}:{'demo' if demo else ('api' if CONFIG['map_key'] else 'csv')}"
    hit = cache.get(key)
    if hit:
        return hit
    ahora = datetime.now(timezone.utc)
    if demo:
        dets, estado = firms.focos_demo(rango, fuentes, ahora)
        modo = "demo"
    else:
        dets, estado = firms.obtener_focos(rango, fuentes, CONFIG["map_key"], sesion, ahora)
        modo = "api" if CONFIG["map_key"] else "csv"
    valor = {"dets": dets, "estado": estado, "modo": modo, "descargado": ahora}
    # si todas las fuentes fallan, cachea poco tiempo para reintentar pronto
    ttl = CONFIG["cache_firms_min"] * 60 if any(e["ok"] for e in estado) else 60
    return cache.set(key, valor, ttl)


def _procesado():
    """Detecciones descargadas, filtradas y agrupadas según los parámetros de la consulta."""
    rango, fuentes, confianza, solo_peru, demo = _parametros()
    crudo = _detecciones(rango, fuentes, demo)
    ahora = datetime.now(timezone.utc)
    # el agrupamiento se reutiliza 60 s para atender a varios usuarios sin recalcular
    clave_proc = f"proc:{id(crudo)}:{crudo['descargado'].timestamp()}:{confianza}:{solo_peru}"
    procesado = cache.get(clave_proc)
    if procesado is None:
        procesado = cache.set(clave_proc, clustering.procesar(crudo["dets"], ahora=ahora, solo_peru=solo_peru,
                                                              confianza_min=confianza), 60)
    return crudo, procesado[0], procesado[1], ahora


def calcular():
    rango, _, confianza, solo_peru, _ = _parametros()
    crudo, dets, incendios, ahora = _procesado()

    avisos = [f"{e['nombre']}: {e['error']}" for e in crudo["estado"] if not e["ok"]]
    top = incendios[: CONFIG["viento_top_n"]]
    fallo_previo = cache.get("viento:fallo")
    if top and fallo_previo:
        avisos.append(f"Viento en el mapa no disponible: {fallo_previo}")
    elif top:
        try:
            vientos = weather.viento_lote([(i["lat"], i["lon"]) for i in top], sesion)
            for inc, v in zip(top, vientos):
                inc["meteo"] = v
        except Exception as exc:  # noqa: BLE001
            msg = resumen(exc)
            cache.set("viento:fallo", msg, 120)  # evita reintentar en cada consulta durante 2 min
            avisos.append(f"Viento en el mapa no disponible (Open-Meteo): {msg}")

    activos = [i for i in incendios if i["estado"] == "activo"]
    totales = {
        "focos": len(dets),
        "incendios": len(incendios),
        "activos": len(activos),
        "frp_total": round(sum(i["frp_total"] for i in incendios), 1),
        "area_ha": round(sum(i["area_pixeles_ha"] for i in incendios), 1),
        "nivel_max": max((i["nivel"] for i in incendios), default=0),
        "ultima_deteccion": max((i["ultima"] for i in incendios), default=None),
    }
    return {
        "version": __version__,
        "generado": ahora.isoformat().replace("+00:00", "Z"),
        "descargado": crudo["descargado"].isoformat().replace("+00:00", "Z"),
        "rango": rango,
        "modo": crudo["modo"],
        "confianza_min": confianza,
        "solo_peru": solo_peru,
        "fuentes": crudo["estado"],
        "avisos": avisos,
        "totales": totales,
        "departamentos": clustering.resumen_departamentos(incendios, dets),
        "incendios": incendios,
        "focos": [
            {
                "lat": round(d["lat"], 5), "lon": round(d["lon"], 5), "frp": d["frp"], "brillo": d["brillo"],
                "scan": d["scan"], "track": d["track"],
                "fecha": d["t"].isoformat().replace("+00:00", "Z"),
                "satelite": d["satelite"], "sensor": d["sensor"], "fuente": d["fuente"],
                "confianza": d["confianza"], "dia_noche": d["dia_noche"], "incendio": d.get("_incendio"),
            }
            for d in dets
        ],
    }


# --------------------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------------------

@app.get("/")
def inicio():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/estado")
def api_estado():
    return jsonify({
        "version": __version__,
        "map_key": bool(CONFIG["map_key"]),
        # La clave de CARTO viaja al navegador (las teselas se piden desde ahí); restríngela por dominio en su panel.
        "carto_key": CONFIG["carto_key"],
        "demo": CONFIG["demo"],
        "fuentes": {k: v["nombre"] for k, v in firms.FUENTES.items()},
        "niveles": [{"nivel": n, "texto": t, "frp_desde": f} for n, t, f in clustering.NIVELES],
        "radio_recursos_km": CONFIG["radio_recursos_km"],
        "contacto": CONFIG["contacto"],
    })


@app.get("/api/limites")
def api_limites():
    resp = jsonify(geo.limites().dep_geojson)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.get("/api/incendios")
def api_incendios():
    return jsonify(calcular())


def _coordenadas():
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
    except (KeyError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


@app.get("/api/meteo")
def api_meteo():
    c = _coordenadas()
    if not c:
        return jsonify({"error": "Indica lat y lon válidos."}), 400
    try:
        return jsonify(weather.detalle(*c, session=sesion))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Open-Meteo no respondió ({resumen(exc)})"}), 502


@app.get("/api/contexto")
def api_contexto():
    c = _coordenadas()
    if not c:
        return jsonify({"error": "Indica lat y lon válidos."}), 400
    radio = min(80.0, max(5.0, float(request.args.get("radio", CONFIG["radio_recursos_km"]))))
    try:
        return jsonify(context.contexto(*c, radio_km=radio, session=sesion))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": resumen(exc)}), 502


@app.get("/api/aire")
def api_aire():
    c = _coordenadas()
    if not c:
        return jsonify({"error": "Indica lat y lon válidos."}), 400
    lugares = aire.parsear_lugares(request.args.get("lugares", ""))
    try:
        return jsonify(aire.calidad_alrededor(*c, lugares=lugares, session=sesion))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Calidad del aire no disponible ({resumen(exc)})"}), 502


@app.get("/api/propagacion")
def api_propagacion():
    _, dets, incendios, ahora = _procesado()
    inc = next((i for i in incendios if i["id"] == request.args.get("id")), None)
    if inc is None:
        return jsonify({"error": "El incendio ya no está en los datos actuales. Actualiza la lista."}), 404
    try:
        horizonte = int(request.args.get("horizonte", 24))
    except ValueError:
        horizonte = 24
    horizonte = min(24, max(3, horizonte))
    miembros = [d for d in dets if d.get("_incendio") == inc["id"]]
    try:
        return jsonify(propagacion.pronosticar(inc, miembros, ahora, sesion, horizonte_h=horizonte))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"No se pudo simular la propagación ({resumen(exc)})"}), 502


@app.get("/api/eonet")
def api_eonet():
    try:
        return jsonify({"eventos": eonet.eventos(session=sesion)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"eventos": [], "error": resumen(exc)}), 200


CAMPOS_CSV = ["id", "nivel", "nivel_texto", "estado", "departamento", "provincia", "lat", "lon", "utm",
              "detecciones", "frp_total", "frp_max", "area_pixeles_ha", "area_envolvente_ha",
              "primera", "ultima", "horas_desde_ultima", "viento_kmh", "viento_dir", "avance_hacia"]


@app.get("/api/exportar.csv")
def exportar_csv():
    datos = calcular()
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CAMPOS_CSV, extrasaction="ignore")
    w.writeheader()
    for inc in datos["incendios"]:
        m = inc.get("meteo") or {}
        w.writerow({**inc, "viento_kmh": m.get("viento"), "viento_dir": m.get("dir"), "avance_hacia": m.get("avance_txt")})
    nombre = f"incendios_peru_{datos['rango']}_{datos['generado'][:10]}.csv"
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@app.get("/api/exportar.geojson")
def exportar_geojson():
    datos = calcular()
    feats = []
    for inc in datos["incendios"]:
        props = {k: v for k, v in inc.items() if k not in ("envolvente",)}
        anillo = [[p[1], p[0]] for p in inc["envolvente"]]
        if len(anillo) >= 3:
            anillo.append(anillo[0])
            geom = {"type": "Polygon", "coordinates": [anillo]}
        else:
            geom = {"type": "Point", "coordinates": [inc["lon"], inc["lat"]]}
        feats.append({"type": "Feature", "properties": props, "geometry": geom})
    cuerpo = json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False)
    nombre = f"incendios_peru_{datos['rango']}_{datos['generado'][:10]}.geojson"
    return Response(cuerpo, mimetype="application/geo+json",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@app.after_request
def comprimir(resp):
    if (resp.direct_passthrough or resp.status_code < 200 or resp.status_code >= 300
            or "gzip" not in request.headers.get("Accept-Encoding", "")
            or resp.headers.get("Content-Encoding")):
        return resp
    if not (resp.mimetype or "").startswith(("application/json", "application/geo+json", "text/csv")):
        return resp
    datos = resp.get_data()
    if len(datos) < 1024:
        return resp
    resp.set_data(gzip.compress(datos, compresslevel=5))
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Content-Length"] = str(len(resp.get_data()))
    resp.headers.add("Vary", "Accept-Encoding")
    return resp


if __name__ == "__main__":
    modo = "DEMO" if CONFIG["demo"] else ("API FIRMS (MAP_KEY)" if CONFIG["map_key"] else "CSV públicos FIRMS")
    print(f"Vigía de Incendios Perú {__version__} · fuente de focos: {modo}")
    print(f"Abre http://localhost:{CONFIG['port']}")
    try:
        from waitress import serve
        serve(app, host=CONFIG["host"], port=CONFIG["port"], threads=8)
    except ImportError:
        app.run(host=CONFIG["host"], port=CONFIG["port"], threaded=True)
