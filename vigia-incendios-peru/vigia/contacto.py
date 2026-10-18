"""Enlaces del apartado «Contáctenos» (redes sociales, WhatsApp, correo y web) leídos de .env."""

import re
from urllib.parse import urlsplit

# (variable de entorno, nombre visible, tipo). El orden es el de la pantalla.
REDES = [
    ("CONTACTO_FACEBOOK", "Facebook", "red"),
    ("CONTACTO_INSTAGRAM", "Instagram", "red"),
    ("CONTACTO_TIKTOK", "TikTok", "red"),
    ("CONTACTO_X", "X (Twitter)", "red"),
    ("CONTACTO_YOUTUBE", "YouTube", "red"),
    ("CONTACTO_LINKEDIN", "LinkedIn", "red"),
    ("CONTACTO_WHATSAPP", "WhatsApp", "whatsapp"),
    ("CONTACTO_CORREO", "Correo", "correo"),
    ("CONTACTO_WEB", "Sitio web", "web"),
]

_CORREO = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[A-Za-z]{2,}$")


def _url_web(valor):
    """Acepta solo http(s); a «facebook.com/pagina» le antepone https://."""
    v = valor.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", v, re.I):
        v = "https://" + v
    partes = urlsplit(v)
    if partes.scheme.lower() not in ("http", "https") or "." not in partes.netloc or re.search(r"[\s<>\"']", v):
        return None
    return v


def _detalle(url):
    partes = urlsplit(url)
    texto = (partes.netloc + partes.path).removeprefix("www.").rstrip("/")
    return texto if len(texto) <= 40 else texto[:39] + "…"


def enlace(tipo, valor):
    """Devuelve (url, detalle) o None si el valor no es válido para ese tipo."""
    v = (valor or "").strip()
    if not v:
        return None
    if tipo == "correo":
        v = v.removeprefix("mailto:")
        return (f"mailto:{v}", v) if _CORREO.match(v) else None
    if tipo == "whatsapp":
        digitos = re.sub(r"[\s\-().]", "", v).removeprefix("+")
        if digitos.isdigit() and 8 <= len(digitos) <= 15:
            return f"https://wa.me/{digitos}", f"+{digitos}"
        url = _url_web(v)  # también se acepta un enlace (wa.me, chat.whatsapp.com)
        return (url, _detalle(url)) if url else None
    url = _url_web(v)
    return (url, _detalle(url)) if url else None


def desde_entorno(env):
    """Lee las variables CONTACTO_* y devuelve el texto, los enlaces válidos y los avisos de los inválidos."""
    enlaces, avisos = [], []
    for variable, nombre, tipo in REDES:
        valor = env.get(variable, "")
        if not valor.strip():
            continue
        r = enlace(tipo, valor)
        if r is None:
            avisos.append(f"{variable}: «{valor.strip()}» no es válido y no se mostrará")
            continue
        enlaces.append({"red": variable.removeprefix("CONTACTO_").lower(), "nombre": nombre, "tipo": tipo,
                        "url": r[0], "detalle": r[1]})
    return {"texto": env.get("CONTACTO_TEXTO", "").strip(), "enlaces": enlaces}, avisos
