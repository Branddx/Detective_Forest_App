"""Mensajes de error breves y comprensibles para mostrar en la interfaz."""

from urllib.parse import urlparse

import requests


def resumen(exc: Exception) -> str:
    host = ""
    req = getattr(exc, "request", None)
    if req is not None and getattr(req, "url", None):
        host = urlparse(req.url).netloc
    if isinstance(exc, requests.exceptions.Timeout):
        return f"tiempo de espera agotado{f' ({host})' if host else ''}"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"sin conexión con {host or 'el servidor'}"
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        return f"{host or 'el servidor'} respondió HTTP {exc.response.status_code}"
    texto = str(exc).strip() or exc.__class__.__name__
    return texto if len(texto) <= 180 else texto[:177] + "…"
