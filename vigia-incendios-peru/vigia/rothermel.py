"""Modelo de propagación superficial de Rothermel (1972) con los ajustes de Albini (1976).

Es el núcleo de BehavePlus, FARSITE y FlamMap. Trabaja en unidades inglesas, como las ecuaciones
originales: cargas en lb/ft², SAV en 1/ft, profundidad en ft, viento en ft/min, velocidad en ft/min.

Modelos de combustible de Scott y Burgan (2005, RMRS-GTR-153). La forma elíptica sigue a
Anderson (1983) y el límite de viento a Andrews, Cruz y Rothermel (2013).
"""

import math

TON_ACRE_A_LB_FT2 = 2000.0 / 43560.0
FT_MIN_A_M_MIN = 0.3048
KMH_A_FT_MIN = 1000.0 / 0.3048 / 60.0
SAV_10H, SAV_100H = 109.0, 30.0
DENSIDAD_PARTICULA = 32.0   # lb/ft³
MINERAL_TOTAL = 0.0555
MINERAL_EFECTIVO = 0.010
CALOR = 8000.0              # BTU/lb


class Modelo:
    """Modelo de combustible estándar. Cargas en t/acre como en la publicación; se convierten al crear."""

    def __init__(self, codigo, nombre, h1, h10, h100, hierba, lenosa, sav1, sav_hierba, sav_lenosa,
                 profundidad_ft, mx_pct, dinamico):
        self.codigo = codigo
        self.nombre = nombre
        k = TON_ACRE_A_LB_FT2
        self.cargas = (h1 * k, h10 * k, h100 * k, hierba * k, lenosa * k)
        self.sav = (sav1, SAV_10H, SAV_100H, sav_hierba, sav_lenosa)
        self.profundidad = profundidad_ft
        self.mx = mx_pct / 100.0
        self.dinamico = dinamico


# Subconjunto de los 40 modelos que se usa en el Perú (ver propagacion.combustible_por_cobertura)
MODELOS = {m.codigo: m for m in [
    Modelo("GR1", "Pasto corto y ralo (cultivos, rastrojo)", 0.10, 0, 0, 0.30, 0, 2200, 2000, 9999, 0.4, 15, True),
    Modelo("GR2", "Pastizal seco de carga baja (pajonal, ichu)", 0.10, 0, 0, 1.00, 0, 2000, 1800, 9999, 1.0, 15, True),
    Modelo("GS2", "Pastizal con arbustos (matorral)", 0.50, 0.50, 0, 0.60, 1.00, 2000, 1800, 1800, 1.5, 15, True),
    Modelo("GS4", "Pastizal con arbustos húmedo, carga alta", 1.90, 0.30, 0.10, 3.40, 7.10, 1800, 1600, 1600, 2.1, 40, True),
    Modelo("TU1", "Hojarasca con pasto y arbustos bajo árboles", 0.20, 0.90, 1.50, 0.20, 0.90, 2000, 1800, 1600, 0.6, 20, True),
    Modelo("TL2", "Hojarasca de latifoliadas, carga baja (bosque húmedo)", 1.40, 2.30, 2.20, 0, 0, 2000, 9999, 9999, 0.2, 25, False),
]}


def _clase_albini(sav):
    """Grupo de tamaño para ponderar cargas netas (Albini 1976)."""
    for i, limite in enumerate((1200.0, 192.0, 96.0, 48.0, 16.0)):
        if sav >= limite:
            return i
    return 5


def _amortiguamiento(m, mx):
    if mx <= 0 or m >= mx:
        return 0.0
    r = m / mx
    return max(0.0, 1.0 - 2.59 * r + 5.11 * r * r - 3.52 * r ** 3)


def factor_viento(profundidad_ft, cobertura=0.0, altura_ft=0.0, razon_copa=0.5):
    """Fracción del viento a 20 ft que llega a media llama (Albini y Baughman 1979, como BehavePlus)."""
    relleno = cobertura * razon_copa / 3.0
    if relleno > 0.05 and altura_ft > 0:
        return 0.555 / (math.sqrt(relleno * altura_ft) * math.log((20 + 0.36 * altura_ft) / (0.13 * altura_ft)))
    if profundidad_ft <= 0:
        return 0.0
    return 1.83 / math.log((20 + 0.36 * profundidad_ft) / (0.13 * profundidad_ft))


class Lecho:
    """Comportamiento de un modelo con humedades dadas, listo para combinar viento y pendiente."""

    __slots__ = ("r0", "ir", "fuente_calor", "c", "b", "e", "razon", "coef_pendiente", "u_limite")

    def phi_viento(self, u_ft_min):
        return self.c * u_ft_min ** self.b * self.razon ** -self.e if u_ft_min > 0 else 0.0

    def maxima(self, u_ft_min, rumbo_viento, tan_pendiente=0.0, rumbo_subida=0.0):
        """Velocidad máxima (ft/min), su rumbo (grados, hacia donde avanza) y la razón largo/ancho.

        rumbo_viento es hacia donde sopla el viento; rumbo_subida, la dirección cuesta arriba.
        """
        if self.r0 <= 0:
            return 0.0, rumbo_viento, 1.0
        phi_w = self.phi_viento(u_ft_min)
        phi_s = self.coef_pendiente * tan_pendiente * tan_pendiente
        if phi_s <= 0:
            phi_e, rumbo = phi_w, rumbo_viento
        else:
            ang = math.radians(rumbo_viento - rumbo_subida)
            x = phi_s + phi_w * math.cos(ang)
            y = phi_w * math.sin(ang)
            phi_e = math.hypot(x, y)
            rumbo = (rumbo_subida + math.degrees(math.atan2(y, x))) % 360
        u_e = (phi_e * self.razon ** self.e / self.c) ** (1.0 / self.b) if phi_e > 0 else 0.0
        if u_e > self.u_limite:
            u_e = self.u_limite
            phi_e = self.phi_viento(u_e)
        u_mph = u_e / 88.0
        lb = 0.936 * math.exp(0.1147 * u_mph) + 0.461 * math.exp(-0.0692 * u_mph) - 0.397
        return self.r0 * (1.0 + phi_e), rumbo, max(1.0, min(8.0, lb))


def excentricidad(lb):
    return math.sqrt(lb * lb - 1.0) / lb if lb > 1.0 else 0.0


def en_direccion(ros_max, excentric, angulo_desde_rumbo):
    """Velocidad hacia un ángulo medido desde el rumbo de máxima propagación (elipse con foco en el origen)."""
    return ros_max * (1.0 - excentric) / (1.0 - excentric * math.cos(math.radians(angulo_desde_rumbo)))


def preparar(modelo, m1, m10, m100, m_hierba, m_lenosa):
    """Calcula el lecho de combustible para humedades en fracción (0,06 = 6 %)."""
    h1, h10, h100, hierba, lenosa = modelo.cargas
    s1, s10, s100, s_hierba, s_lenosa = modelo.sav
    curado = 0.0
    if modelo.dinamico and hierba > 0:
        curado = 1.0 - max(0.0, min(1.0, m_hierba / 0.9 - 1.0 / 3.0))
    muertos = [(h1, s1, m1), (h10, s10, m10), (h100, s100, m100), (hierba * curado, s_hierba, m1)]
    vivos = [(hierba * (1 - curado), s_hierba, m_hierba), (lenosa, s_lenosa, m_lenosa)]
    muertos = [c for c in muertos if c[0] > 0]
    vivos = [c for c in vivos if c[0] > 0]

    lecho = Lecho()
    if not muertos or modelo.profundidad <= 0:
        lecho.r0 = lecho.ir = lecho.fuente_calor = 0.0
        lecho.c = lecho.b = lecho.e = lecho.razon = 1.0
        lecho.coef_pendiente = lecho.u_limite = 0.0
        return lecho

    categorias = []
    for clases in (muertos, vivos):
        areas = [w * s / DENSIDAD_PARTICULA for w, s, _ in clases]
        categorias.append((clases, areas, sum(areas)))
    area_total = sum(a for _, _, a in categorias)

    sigma = 0.0
    datos = []
    for clases, areas, area in categorias:
        if area <= 0:
            datos.append(None)
            continue
        f = [a / area for a in areas]
        grupos = {}
        for fij, (_, s, _) in zip(f, clases):
            grupos[_clase_albini(s)] = grupos.get(_clase_albini(s), 0.0) + fij
        neta = sum(grupos[_clase_albini(s)] * w * (1 - MINERAL_TOTAL) for (w, s, _) in clases)
        humedad = sum(fij * m for fij, (_, _, m) in zip(f, clases))
        sigma_cat = sum(fij * s for fij, (_, s, _) in zip(f, clases))
        sumidero = sum(fij * math.exp(-138.0 / s) * (250.0 + 1116.0 * m) for fij, (_, s, m) in zip(f, clases))
        fi = area / area_total
        sigma += fi * sigma_cat
        datos.append((fi, neta, humedad, sumidero))

    carga_total = sum(w for w, _, _ in muertos + vivos)
    densidad = carga_total / modelo.profundidad
    beta = densidad / DENSIDAD_PARTICULA
    beta_op = 3.348 * sigma ** -0.8189
    razon = beta / beta_op
    s15 = sigma ** 1.5
    gamma_max = s15 / (495.0 + 0.0594 * s15)
    a = 133.0 * sigma ** -0.7913
    gamma = gamma_max * razon ** a * math.exp(a * (1.0 - razon))

    # Humedad de extinción de los vivos según la relación de cargas finas muertas/vivas
    mx_vivo = modelo.mx
    if vivos:
        fino_muerto = sum(w * math.exp(-138.0 / s) for w, s, _ in muertos)
        fino_vivo = sum(w * math.exp(-500.0 / s) for w, s, _ in vivos)
        m_fino = sum(w * math.exp(-138.0 / s) * m for w, s, m in muertos) / fino_muerto
        mx_vivo = max(modelo.mx, 2.9 * (fino_muerto / fino_vivo) * (1.0 - m_fino / modelo.mx) - 0.226)

    eta_s = 0.174 * MINERAL_EFECTIVO ** -0.19
    ir = 0.0
    sumidero = 0.0
    for d, mx in zip(datos, (modelo.mx, mx_vivo)):
        if d is None:
            continue
        fi, neta, humedad, sum_cat = d
        ir += neta * CALOR * _amortiguamiento(humedad, mx) * eta_s
        sumidero += fi * sum_cat
    ir *= gamma
    xi = math.exp((0.792 + 0.681 * math.sqrt(sigma)) * (beta + 0.1)) / (192.0 + 0.2595 * sigma)

    lecho.ir = ir
    lecho.fuente_calor = ir * xi
    lecho.r0 = lecho.fuente_calor / (densidad * sumidero) if sumidero > 0 else 0.0
    lecho.c = 7.47 * math.exp(-0.133 * sigma ** 0.55)
    lecho.b = 0.02526 * sigma ** 0.54
    lecho.e = 0.715 * math.exp(-3.59e-4 * sigma)
    lecho.razon = razon
    lecho.coef_pendiente = 5.275 * beta ** -0.3
    lecho.u_limite = 96.8 * ir ** (1.0 / 3.0) if ir > 0 else 0.0
    return lecho
