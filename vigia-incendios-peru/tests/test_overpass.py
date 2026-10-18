import json

import pytest

from vigia import context
from vigia.cache import cache

AREAS = {"elements": [{"type": "area", "tags": {"boundary": "administrative", "admin_level": "8", "name": "Río Tambo"}}]}
RECURSOS = {"elements": [{"type": "node", "lat": -11.85, "lon": -73.86,
                          "tags": {"amenity": "fire_station", "name": "Cía. de Bomberos"}}]}


class _Resp:
    def __init__(self, status, datos=None, texto=None):
        self.status_code = status
        self._datos = datos
        self.text = texto if texto is not None else json.dumps(datos)

    def json(self):
        if self._datos is None:
            raise ValueError("no JSON")
        return self._datos


class _Sesion:
    """Responde según el servidor y el tipo de consulta (áreas o recursos)."""

    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.llamadas = []

    def post(self, url, data=None, timeout=None, headers=None):
        host = url.split("/")[2]
        tipo = "areas" if "is_in" in data["data"] else "recursos"
        self.llamadas.append((host, tipo))
        r = self.respuestas.get((host, tipo)) or self.respuestas.get(host)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def limpio(tmp_path, monkeypatch):
    cache.clear()
    context._estado.clear()
    monkeypatch.setattr(context, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(context, "ESPERA_ESCALONADA_S", 0.2)
    monkeypatch.setattr(context, "ENDPOINTS", ["https://a.test/api/interpreter", "https://b.test/api/interpreter",
                                               "https://overpass.openstreetmap.fr/api/interpreter"])
    monkeypatch.delenv("OVERPASS_URLS", raising=False)
    yield
    cache.clear()
    context._estado.clear()


def test_validar_detecta_errores_con_http_200():
    with pytest.raises(context.OverpassError, match="saturado"):
        context.validar(_Resp(502, texto="This Overpass API server is currently at capacity"))
    with pytest.raises(context.OverpassError, match="error del servidor"):
        context.validar(_Resp(200, {"elements": [], "remark": "runtime error: open64: 2 No such file area_blocks.bin"}))
    assert context.validar(_Resp(200, RECURSOS)) == RECURSOS


def test_pasa_al_siguiente_servidor_y_pausa_el_que_falla():
    ses = _Sesion({"a.test": _Resp(500, texto="Internal Server Error"), "b.test": _Resp(200, RECURSOS)})
    datos, servidor = context.pedir("[out:json];node(1);out;", ses, necesita_areas=False)
    assert servidor == "b.test" and datos == RECURSOS
    assert not context._disponible("a.test")       # queda en pausa
    ses.llamadas.clear()
    context.pedir("[out:json];node(1);out;", ses, necesita_areas=False)
    assert ("a.test", "recursos") not in ses.llamadas  # no se insiste mientras está en pausa


def test_servidor_sin_indice_de_areas_solo_recibe_recursos():
    ses = _Sesion({"a.test": _Resp(502, texto="capacity"), "b.test": _Resp(502, texto="capacity"),
                   "overpass.openstreetmap.fr": _Resp(200, RECURSOS)})
    r = context.contexto(-11.84, -73.863, 30, session=ses)
    assert r["recursos"]["bomberos"][0]["nombre"] == "Cía. de Bomberos"
    assert r["servidor"] == "overpass.openstreetmap.fr"
    assert ("overpass.openstreetmap.fr", "areas") not in ses.llamadas
    assert r["avisos"] and "Distrito" in r["avisos"][0]  # el resto se muestra igual


def test_respaldo_en_disco_cuando_todo_falla():
    bien = _Sesion({("a.test", "recursos"): _Resp(200, RECURSOS), ("a.test", "areas"): _Resp(200, AREAS)})
    r = context.contexto(-11.84, -73.863, 30, session=bien)
    assert r["admin"]["distrito"] == "Río Tambo" and not r["desactualizado"]

    cache.clear()
    context._estado.clear()
    caido = _Sesion({h: _Resp(500, texto="error") for h in ("a.test", "b.test", "overpass.openstreetmap.fr")})
    r = context.contexto(-11.84, -73.863, 30, session=caido)
    assert r["desactualizado"] and r["recursos"]["bomberos"] and "a.test" in r["error"]


def test_sin_respaldo_el_fallo_se_recuerda_unos_segundos():
    caido = _Sesion({h: _Resp(503, texto="busy") for h in ("a.test", "b.test", "overpass.openstreetmap.fr")})
    with pytest.raises(context.OverpassError, match="saturado"):
        context.contexto(-12.0, -75.0, 30, session=caido)
    n = len(caido.llamadas)
    with pytest.raises(context.OverpassError):
        context.contexto(-12.0, -75.0, 30, session=caido)
    assert len(caido.llamadas) == n  # no vuelve a molestar a los servidores enseguida


def test_servidores_propios_van_primero(monkeypatch):
    monkeypatch.setenv("OVERPASS_URLS", "http://localhost:12345/api/interpreter, https://a.test/api/interpreter")
    assert context.endpoints()[:3] == ["http://localhost:12345/api/interpreter", "https://a.test/api/interpreter",
                                      "https://b.test/api/interpreter"]
