from vigia import contacto


def test_sin_variables_no_hay_enlaces():
    datos, avisos = contacto.desde_entorno({})
    assert datos == {"texto": "", "enlaces": []}
    assert avisos == []


def test_redes_whatsapp_correo_y_orden():
    env = {
        "CONTACTO_WEB": "vigia.pe",
        "CONTACTO_CORREO": "mailto:hola@vigia.pe",
        "CONTACTO_WHATSAPP": "+51 987 654 321",
        "CONTACTO_FACEBOOK": "https://www.facebook.com/vigiaincendios/",
        "CONTACTO_TEXTO": "  Escríbenos  ",
    }
    datos, avisos = contacto.desde_entorno(env)
    assert avisos == []
    assert datos["texto"] == "Escríbenos"
    assert [e["red"] for e in datos["enlaces"]] == ["facebook", "whatsapp", "correo", "web"]
    fb, wa, correo, web = datos["enlaces"]
    assert fb["url"] == "https://www.facebook.com/vigiaincendios/" and fb["detalle"] == "facebook.com/vigiaincendios"
    assert wa["url"] == "https://wa.me/51987654321" and wa["detalle"] == "+51987654321"
    assert correo["url"] == "mailto:hola@vigia.pe" and correo["detalle"] == "hola@vigia.pe"
    assert web["url"] == "https://vigia.pe"


def test_valores_invalidos_se_descartan_con_aviso():
    env = {
        "CONTACTO_INSTAGRAM": "javascript:alert(1)",
        "CONTACTO_CORREO": "sin-arroba",
        "CONTACTO_WHATSAPP": "123",
        "CONTACTO_X": "https://x.com/vigia\" onmouseover=\"x",
    }
    datos, avisos = contacto.desde_entorno(env)
    assert datos["enlaces"] == []
    assert len(avisos) == 4


def test_whatsapp_acepta_enlace():
    assert contacto.enlace("whatsapp", "https://wa.me/51987654321")[0] == "https://wa.me/51987654321"
