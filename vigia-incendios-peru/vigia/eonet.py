"""Eventos de incendio curados por NASA EONET dentro del Perú y alrededores."""

import requests

from .cache import cache

EONET_URL = "https://eonet.gsfc.nasa.gov/api/v3/events"
# EONET usa bbox = oeste, norte, este, sur
BBOX = "-82,1,-68,-19"


def eventos(dias=30, session=None, ttl=1800):
    key = f"eonet:{dias}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    session = session or requests.Session()
    r = session.get(EONET_URL, params={"category": "wildfires", "bbox": BBOX, "days": dias, "status": "all"},
                    timeout=30, headers={"User-Agent": "VigiaIncendiosPeru/1.0"})
    r.raise_for_status()
    out = []
    for ev in r.json().get("events", []):
        geoms = ev.get("geometry") or []
        if not geoms:
            continue
        ult = geoms[-1]
        coords = ult.get("coordinates")
        if ult.get("type") != "Point" or not coords:
            continue
        out.append({
            "id": ev.get("id"),
            "titulo": ev.get("title"),
            "fecha": ult.get("date"),
            "lat": coords[1],
            "lon": coords[0],
            "magnitud": ult.get("magnitudeValue"),
            "unidad": ult.get("magnitudeUnit"),
            "cerrado": ev.get("closed"),
            "fuentes": [s.get("url") for s in ev.get("sources", []) if s.get("url")],
        })
    return cache.set(key, out, ttl)
