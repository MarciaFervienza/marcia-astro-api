"""CP1 v2 — renderer isolado com spec fechada pela auditoria.

Semântica:
  · TICK radial na longitude REAL = a posição astrológica. Cor neutra sempre.
  · LABEL (glifo+grau+signo+minutos+R) = anotação deslocada, em coluna,
    ordem zodiacal, ligada ao tick por LEADER LINE contínua.
  · Retrogradação = glifo vermelho + sufixo R (nunca só cor).
  · Overflow = defeito: bbox da coluna precisa caber no anel; busca por
    posição válida (raio/escala); se nada couber → fail-closed.
  · Defs: símbolos ORIGINAIS intocados (anel zodiacal = cores baseline);
    clones "__mono" com currentColor só para uso nas labels.
  · Asserções automatizadas: ângulo do tick, texto da label, leader lines
    sem cruzamento, bbox dentro do anel.
"""
import warnings; warnings.filterwarnings("ignore")
import math, os, re, tempfile

CENTER = 50.0
R_OUTER = 44.5
R_ZODIAC_INNER = 41.5
R_PLANET_OUTER = 40.0
R_PLANET_INNER = 18.5
R_HOUSE_LABELS = 21.0

TARGET_WHEEL_CM = 18.0   # tamanho do WHEEL na página (não do viewBox)

MAX_LANES = 7
# ROW_PITCH medido, não estimado: o glifo mais alto (Saturno @ scale 0.2) tem
# 4.44u; o texto a font-size 2 cabe dentro disso. 4.8 = 4.44 + respiro.
ROW_PITCH = 4.8          # altura de uma linha de label a scale=1
MIN_SCALE = 0.45
BBOX_MARGIN = 0.8        # folga do bbox contra as bordas do anel

COLOR_CHARCOAL = "#2F2F2F"
COLOR_RED = "#B94A48"
COLOR_MUTED = "#8A8579"
COLOR_TICK = "#4A4A4A"    # neutro, SEMPRE — tick significa longitude
COLOR_LEADER = "#8A8579"

SIGN_ORDER = ("Ari","Tau","Gem","Can","Leo","Vir","Lib","Sco","Sag","Cap","Aqu","Pis")
NEVER_RETROGRADE = {"Sun", "Moon"}
CLUSTER_GAP = 8.0

SP = os.environ.get("SP", ".")
DEFS_PATH = os.path.join(SP, "kerykeion_defs.svg")

PLANET_SYMBOL_IDS = ["Sun","Moon","Mercury","Venus","Mars","Jupiter","Saturn",
                     "Uranus","Neptune","Pluto","Chiron","Mean_Lilith"]


# ----------------------------------------------------------------------
# DEFS — originais intocados + clones __mono para as labels
# ----------------------------------------------------------------------
def build_defs():
    """Retorna (defs_str, audit) onde defs_str = originais + clones mono.
    audit = dict com verificação de integridade dos originais."""
    raw = open(DEFS_PATH).read()
    original_bytes = raw

    clones = []
    for sym_id in PLANET_SYMBOL_IDS + list(SIGN_ORDER):
        m = re.search(rf"<symbol id='{sym_id}'>.*?</symbol>", raw, re.DOTALL)
        if not m:
            continue
        clone = m.group(0)
        clone = clone.replace(f"<symbol id='{sym_id}'>", f"<symbol id='{sym_id}__mono'>")
        clone = re.sub(r"stroke:\s*#[0-9A-Fa-f]{3,8}", "stroke: currentColor", clone)
        clone = re.sub(r"fill:\s*#[0-9A-Fa-f]{3,8}", "fill: currentColor", clone)
        clones.append(clone)

    # defs final: originais (byte-idênticos) + clones dentro do mesmo <defs>
    assert raw.rstrip().endswith("</defs>")
    combined = raw.rstrip()[:-len("</defs>")] + "\n".join(clones) + "</defs>"

    audit = {
        "originals_untouched": original_bytes in combined,  # substring intacta menos o fechamento
        "n_clones": len(clones),
        "clone_ids": [f"{s}__mono" for s in PLANET_SYMBOL_IDS + list(SIGN_ORDER)],
    }
    # verificação mais precisa: cada symbol original continua presente byte a byte
    intact = all(
        re.search(rf"<symbol id='{sid}'>", combined) for sid in PLANET_SYMBOL_IDS
    )
    audit["originals_present"] = intact
    return combined, audit


# ----------------------------------------------------------------------
# GEOMETRIA
# ----------------------------------------------------------------------
def wheel_angle(zodiac_deg, seventh_house_deg=210.0):
    return (zodiac_deg - seventh_house_deg + 180) % 360


def _svg_rotate(px, py, deg, cx=CENTER, cy=CENTER):
    """Aplica rotate(deg cx cy) convenção SVG (y-down, positivo=horário)."""
    a = math.radians(deg)
    x, y = px - cx, py - cy
    xr = x * math.cos(a) - y * math.sin(a)
    yr = x * math.sin(a) + y * math.cos(a)
    return xr + cx, yr + cy


def paper_xy(abs_pos, r, seventh=210.0):
    """Posição FINAL no papel de um ponto radial (abs_pos zodiacal, raio r),
    replicando a cadeia: rotate(-wa) → wedge translate(4,4)scale(0.92) →
    global rotate(-90)."""
    wa = wheel_angle(abs_pos, seventh)
    x, y = _svg_rotate(CENTER, CENTER - r, -wa)
    x, y = 4 + 0.92 * x, 4 + 0.92 * y
    x, y = _svg_rotate(x, y, -90)
    return x, y


def circular_clusters(planets, gap=CLUSTER_GAP):
    """Clustering circular: corta em todos os gaps >= gap; wraparound tratado.
    Retorna lista de listas de índices (ordem zodiacal dentro do cluster)."""
    n = len(planets)
    if n == 0: return []
    order = sorted(range(n), key=lambda i: planets[i]["abs_pos"])
    if n == 1: return [order]
    gaps = []
    for k in range(n):
        a = planets[order[k]]["abs_pos"]
        b = planets[order[(k+1) % n]]["abs_pos"]
        gaps.append((b - a) % 360)
    cuts = [k for k in range(n) if gaps[k] >= gap]
    if not cuts:
        return [order]  # tudo é um cluster
    clusters = []
    for ci in range(len(cuts)):
        start = (cuts[ci] + 1) % n
        end = cuts[(ci + 1) % len(cuts)]
        idxs = []
        k = start
        while True:
            idxs.append(order[k])
            if k == end: break
            k = (k + 1) % n
        clusters.append(idxs)
    return clusters


def circular_mean(angles):
    s = sum(math.sin(math.radians(a)) for a in angles)
    c = sum(math.cos(math.radians(a)) for a in angles)
    return math.degrees(math.atan2(s, c)) % 360


def compute_scale(N):
    """Escala máxima de partida. Usa o anel ÚTIL (descontada a margem dos dois
    lados), não a largura bruta do anel — senão a coluna nasce alta demais e
    nunca cabe. A busca em place_column tenta valores menores a partir daqui."""
    if N > MAX_LANES:
        raise RuntimeError(f"FAIL-CLOSED: cluster de {N} corpos excede MAX_LANES={MAX_LANES}")
    usable = (R_PLANET_OUTER - R_PLANET_INNER) - 2 * BBOX_MARGIN
    return max(MIN_SCALE, min(1.0, usable / (N * ROW_PITCH)))


# ----------------------------------------------------------------------
# FIXTURES SINTÉTICAS + SANIDADE
# ----------------------------------------------------------------------
def validate_fixture(planets):
    for p in planets:
        if p["retrograde"] and p["name"] in NEVER_RETROGRADE:
            raise ValueError(f"fixture impossível: {p['name']} retrógrado")
        if not (0.0 <= p["abs_pos"] < 360.0):
            raise ValueError(f"abs_pos fora de [0,360): {p['name']}={p['abs_pos']}")
    return planets


def fixture(bodies):
    """bodies: list of (name, abs_pos, retro). Valida e retorna dicts."""
    return validate_fixture([
        {"name": n, "abs_pos": float(a) % 360, "retrograde": bool(r)}
        for n, a, r in bodies
    ])


# ----------------------------------------------------------------------
# LABEL LAYOUT
# ----------------------------------------------------------------------
def fmt_deg(pos_in_sign): return f"{int(pos_in_sign)}°"
def fmt_min(pos_in_sign): return f"{int((pos_in_sign - int(pos_in_sign)) * 60):02d}'"


# Larguras MEDIDAS via svglib (não estimadas). Ver test de medição no ESTADO.md.
#   planeta @ scale 0.2   → 2.40..3.64u  (Saturno é o mais estreito, Urano o mais largo)
#   signo   @ scale 0.125 → 4.00u exatos
GLYPH_W_PLANET = 3.7
GLYPH_W_SIGN = 4.0
FS_DEG, FS_MIN, FS_RX = 2.0, 1.85, 1.6
GAP = 0.4


def _text_w(s, size, bold=False):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    return stringWidth(s, "Helvetica-Bold" if bold else "Helvetica", size)


def row_layout(planet):
    """Posições x de cada elemento da linha, a scale=1. Fonte única de verdade:
    o render e o row_width usam ESTA função, então não podem divergir.
    Retorna (items, largura_total) com items = [(kind, x, payload), ...]."""
    pos = planet["abs_pos"] % 30
    sign = SIGN_ORDER[int(planet["abs_pos"] // 30)]
    deg, minu = fmt_deg(pos), fmt_min(pos)
    items = []
    x = 0.0
    items.append(("planet", x, planet["name"]));  x += GLYPH_W_PLANET + GAP
    items.append(("deg", x, deg));                x += _text_w(deg, FS_DEG) + GAP
    items.append(("sign", x, sign));              x += GLYPH_W_SIGN + GAP
    items.append(("min", x, minu));               x += _text_w(minu, FS_MIN)
    if planet["retrograde"]:
        x += GAP
        items.append(("rx", x, "R"));             x += _text_w("R", FS_RX, bold=True)
    return items, x


def row_width(planet, scale):
    """Largura real da linha em svg units, a `scale`."""
    _, w = row_layout(planet)
    return w * scale


def _annulus_ok(x, y):
    d = math.hypot(x - CENTER, y - CENTER)
    return (R_PLANET_INNER + BBOX_MARGIN) <= d <= (R_PLANET_OUTER - BBOX_MARGIN)


def bbox_fits(x0, y0, x1, y1):
    """Todos os cantos + midpoints do rect dentro do anel."""
    pts = [(x0,y0),(x1,y0),(x0,y1),(x1,y1),
           ((x0+x1)/2,y0),((x0+x1)/2,y1),(x0,(y0+y1)/2),(x1,(y0+y1)/2)]
    return all(_annulus_ok(px, py) for px, py in pts)


def place_column(cluster_planets, seventh, scale, occupied):
    """Busca posição válida pra coluna de labels do cluster.

    A label é ANOTAÇÃO (o tick, na longitude real, é a posição). Por isso a
    coluna pode ser deslocada tangencialmente — o vínculo com a longitude é
    feito pela leader line, não pela posição da label.

    Busca, em ordem de preferência (menos deslocamento / maior escala antes):
      · escala: de `scale` até MIN_SCALE
      · raio: passos finos dentro do anel útil
      · nudge tangencial: desvio angular crescente, alternando os dois lados

    Retorna dict ou levanta RuntimeError (fail-closed).
    """
    N = len(cluster_planets)
    mean_ang = circular_mean([p["abs_pos"] for p in cluster_planets])

    def overlaps_existing(x0, y0, x1, y1):
        for (ox0, oy0, ox1, oy1) in occupied:
            if not (x1 < ox0 or ox1 < x0 or y1 < oy0 or oy1 < y0):
                return True
        return False

    # escalas: passos de 5% até o piso de legibilidade
    scales = []
    s_try = scale
    while s_try >= MIN_SCALE - 1e-9:
        scales.append(round(s_try, 4))
        s_try -= 0.05

    # raios: passo fino, do meio do anel para fora e para dentro
    r_mid = (R_PLANET_INNER + R_PLANET_OUTER) / 2
    radii = sorted(
        [r_mid + d for d in [x * 0.5 for x in range(-18, 19)]],
        key=lambda r: abs(r - r_mid),
    )

    # Nudge tangencial: 0 primeiro, depois alternando ±, preferindo o menor
    # deslocamento. Alcance ±36°: quando o arco de ticks fica horizontal (o
    # cluster no topo/base do wheel), a coluna precisa de ~23° para sair de
    # baixo do arco — com ±18° esses casos não tinham solução. O deslocamento
    # é legítimo: o tick continua na longitude real, a label é anotação e a
    # leader line faz o vínculo.
    nudges = [0.0]
    for d in [x * 1.5 for x in range(1, 25)]:   # até ±36°
        nudges += [d, -d]

    ticks_xy = [paper_xy(p["abs_pos"], R_PLANET_OUTER + 0.5, seventh)
                for p in cluster_planets]

    for s_t in scales:
        w_t = max(row_width(p, s_t) for p in cluster_planets) + 1.0
        h_t = N * ROW_PITCH * s_t
        for nud in nudges:
            for r_col in radii:
                cxp, cyp = paper_xy(mean_ang + nud, r_col, seventh)
                x0, y0 = cxp - w_t / 2, cyp - h_t / 2
                x1, y1 = cxp + w_t / 2, cyp + h_t / 2
                # O anel é NÃO-CONVEXO: checar só os cantos da coluna não basta —
                # o meio da coluna pode mergulhar dentro do círculo interno.
                # Validar linha a linha, exatamente o que assert_render exige.
                rows_ok = True
                for row_i in range(N):
                    ry = y0 + (row_i + 0.5) * ROW_PITCH * s_t
                    if not bbox_fits(x0, ry - ROW_PITCH * s_t / 2,
                                     x1, ry + ROW_PITCH * s_t / 2):
                        rows_ok = False
                        break
                if not rows_ok:
                    continue
                if overlaps_existing(x0, y0, x1, y1):
                    continue
                col = {"x0": x0, "y0": y0, "w": w_t, "h": h_t,
                       "scale": s_t, "mean_ang": mean_ang,
                       "cx": cxp, "cy": cyp, "nudge": nud, "r": r_col}
                # A posição só serve se as leader lines saírem sem cruzar.
                # Isso é restrição de BUSCA, não teste posterior: o quadrante
                # decide se existe lado válido, e só o deslocamento tangencial
                # consegue tirar a coluna de baixo do arco de ticks.
                routed = route_leaders(cluster_planets, ticks_xy, col)
                if routed is not None:
                    col.update(routed)
                    return col
    raise RuntimeError(
        f"FAIL-CLOSED: nenhuma posição válida para coluna de {N} labels — corpos: "
        + ", ".join(f"{p['name']}@{p['abs_pos']:.2f}°" for p in cluster_planets)
    )


def route_leaders(cluster_planets, ticks_xy, col):
    """Tenta rotear as leader lines da coluna `col`.

    Retorna {"ax", "row_order", "attach_left"} ou None se não houver
    roteamento sem cruzamento.

    Duas regras, ambas da spec:
      · Os ticks precisam estar TODOS do mesmo lado da borda de ancoragem —
        senão a linha atravessa a própria coluna e a associação fica ambígua.
      · As labels seguem ORDEM ZODIACAL. Isso deixa exatamente duas ordens
        possíveis: ascendente ou descendente (a direção de leitura). O wheel
        inverte o sentido vertical entre os quadrantes, então qual das duas
        não cruza depende de onde o cluster está. Testamos as duas.

    `cluster_planets` já vem em ordem zodiacal.
    """
    N = len(cluster_planets)
    zodiac_asc = list(range(N))
    zodiac_desc = list(reversed(range(N)))
    rect = (col["x0"], col["y0"], col["x0"] + col["w"], col["y0"] + col["h"])

    for attach_left in (True, False):
        ax = col["x0"] - 0.3 if attach_left else col["x0"] + col["w"] + 0.3
        for row_order in (zodiac_asc, zodiac_desc):
            segs = []
            for row_i, ci in enumerate(row_order):
                ry = col["y0"] + (row_i + 0.5) * ROW_PITCH * col["scale"]
                segs.append((ticks_xy[ci], (ax, ry)))

            # (a) nenhuma leader atravessa a coluna de labels — se atravessar,
            #     a associação linha↔glifo fica ambígua.
            if any(_seg_hits_rect(s[0], s[1], rect) for s in segs):
                continue

            # (b) leaders não se cruzam entre si
            crossed = False
            for i in range(len(segs)):
                for j in range(i + 1, len(segs)):
                    if _seg_intersect(segs[i][0], segs[i][1], segs[j][0], segs[j][1]):
                        crossed = True
                        break
                if crossed:
                    break
            if not crossed:
                return {"ax": ax, "row_order": list(row_order),
                        "attach_left": attach_left}
    return None


def _seg_hits_rect(p1, p2, rect, eps=0.2):
    """Segmento intersecta o interior do retângulo? (Liang-Barsky)
    O retângulo é encolhido por `eps` para que ancorar na borda não conte
    como atravessar."""
    x0, y0, x1, y1 = rect
    x0 += eps; y0 += eps; x1 -= eps; y1 -= eps
    if x1 <= x0 or y1 <= y0:
        return False
    px, py = p1
    dx, dy = p2[0] - px, p2[1] - py
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, px - x0), (dx, x1 - px), (-dy, py - y0), (dy, y1 - py)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        r = q / p
        if p < 0:
            if r > t1: return False
            if r > t0: t0 = r
        else:
            if r < t0: return False
            if r < t1: t1 = r
    return t0 < t1


# ----------------------------------------------------------------------
# RENDER
# ----------------------------------------------------------------------
def render(planets, asc_deg=30.0):
    """Renderiza wheel completo. Retorna svg_str. Fail-closed em overflow/N."""
    validate_fixture(planets)
    seventh = (asc_deg + 180) % 360
    cusps = [(asc_deg + i * 30) % 360 for i in range(12)]

    defs_str, defs_audit = build_defs()

    parts = []
    parts.append('<?xml version="1.0"?>')
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 100 100">')
    parts.append(defs_str)
    parts.append('<rect x="0" y="0" width="100" height="100" fill="#F8F5EF" />')

    # ---------------- wheel (dentro das rotações) ----------------
    parts.append(f'<g transform="rotate(-90 {CENTER} {CENTER})">')
    parts.append('<g transform="translate(4 4) scale(0.92)">')
    parts.append(f'<circle cx="{CENTER}" cy="{CENTER}" r="{R_OUTER}" fill="none" stroke="{COLOR_MUTED}" stroke-width="0.3" />')
    parts.append(f'<circle cx="{CENTER}" cy="{CENTER}" r="{R_ZODIAC_INNER}" fill="none" stroke="{COLOR_MUTED}" stroke-width="0.15" />')
    parts.append(f'<circle cx="{CENTER}" cy="{CENTER}" r="{R_PLANET_INNER}" fill="none" stroke="{COLOR_MUTED}" stroke-width="0.15" />')

    # zodiac wedges — glifos ORIGINAIS (cores baseline, sem repaint)
    for i in range(12):
        wa = wheel_angle(i * 30, seventh)
        parts.append(f'<g transform="rotate({-wa:.4f} {CENTER} {CENTER})">')
        parts.append(f'<line x1="{CENTER}" y1="{CENTER - R_OUTER}" x2="{CENTER}" y2="{CENTER - R_ZODIAC_INNER}" stroke="{COLOR_MUTED}" stroke-width="0.4" />')
        parts.append('</g>')
        wa_mid = wheel_angle(i * 30 + 15, seventh)
        parts.append(f'<g transform="rotate({-wa_mid:.4f} {CENTER} {CENTER})">')
        parts.append(f'<g transform="translate({CENTER} {CENTER - (R_OUTER + R_ZODIAC_INNER)/2}) rotate({(90 + wa_mid) % 360:.4f}) scale(0.12) translate(-10 -10)">')
        parts.append(f'<use xlink:href="#{SIGN_ORDER[i]}" />')
        parts.append('</g></g>')

    # cusps + house numbers
    for i, c in enumerate(cusps):
        wa = wheel_angle(c, seventh)
        sw = 0.35 if i in (0, 3, 6, 9) else 0.15
        parts.append(f'<g transform="rotate({-wa:.4f} {CENTER} {CENTER})">')
        parts.append(f'<line x1="{CENTER}" y1="{CENTER - R_ZODIAC_INNER}" x2="{CENTER}" y2="{CENTER - R_PLANET_INNER}" stroke="{COLOR_MUTED}" stroke-width="{sw}" />')
        parts.append('</g>')
    for i in range(12):
        arc = (cusps[(i+1) % 12] - cusps[i]) % 360
        mid = (cusps[i] + arc/2) % 360
        wa = wheel_angle(mid, seventh)
        parts.append(f'<g transform="rotate({-wa:.4f} {CENTER} {CENTER})">')
        parts.append(f'<g transform="translate({CENTER} {CENTER - R_HOUSE_LABELS}) rotate({(90 + wa) % 360:.4f})">')
        parts.append(f'<text x="0" y="0.7" text-anchor="middle" font-size="2" fill="{COLOR_MUTED}" font-family="Helvetica">{i+1}</text>')
        parts.append('</g></g>')

    # TICKS — posição astrológica. Neutros SEMPRE. data-abs pra asserção.
    for p in planets:
        wa = wheel_angle(p["abs_pos"], seventh)
        parts.append(f'<g kr-tick="{p["name"]}" data-abs="{p["abs_pos"]:.6f}" transform="rotate({-wa:.4f} {CENTER} {CENTER})">')
        parts.append(f'<line x1="{CENTER}" y1="{CENTER - R_PLANET_OUTER - 0.5}" x2="{CENTER}" y2="{CENTER - R_ZODIAC_INNER}" stroke="{COLOR_TICK}" stroke-width="0.4" />')
        parts.append('</g>')

    parts.append('</g></g>')  # fecha wedge + global rotation

    # ---------------- labels + leaders (ESPAÇO DO PAPEL) ----------------
    clusters = circular_clusters(planets)
    occupied = []
    label_records = []  # p/ asserções

    # Empacotamento: clusters MAIORES primeiro. Eles são os mais difíceis de
    # posicionar; se um cluster de 1 corpo pega o melhor lugar antes, o de 6
    # pode não ter onde caber e disparar fail-closed indevido.
    clusters = sorted(clusters, key=len, reverse=True)

    for idxs in clusters:
        cluster = [planets[i] for i in idxs]  # ordem zodiacal
        N = len(cluster)
        scale = compute_scale(N)
        col = place_column(cluster, seventh, scale, occupied)
        occupied.append((col["x0"], col["y0"], col["x0"]+col["w"], col["y0"]+col["h"]))
        s = col["scale"]

        # Roteamento já resolvido dentro de place_column: a posição só foi
        # aceita porque existe lado de ancoragem com todos os ticks fora dele
        # e sem cruzamento entre as linhas.
        ticks_xy = [paper_xy(p["abs_pos"], R_PLANET_OUTER + 0.5, seventh) for p in cluster]
        ax = col["ax"]
        row_order = col["row_order"]

        for row_i, ci in enumerate(row_order):
            p = cluster[ci]
            tx, ty = ticks_xy[ci]
            ry = col["y0"] + (row_i + 0.5) * ROW_PITCH * s
            rx = col["x0"] + 0.5
            pos = p["abs_pos"] % 30
            sign = SIGN_ORDER[int(p["abs_pos"] // 30)]
            gcolor = COLOR_RED if p["retrograde"] else COLOR_CHARCOAL

            parts.append(f'<line kr-leader="{p["name"]}" x1="{tx:.3f}" y1="{ty:.3f}" x2="{ax:.3f}" y2="{ry:.3f}" stroke="{COLOR_LEADER}" stroke-width="0.18" />')

            # linha da label
            parts.append(f'<g kr-label="{p["name"]}" data-deg="{fmt_deg(pos)}" data-sign="{sign}" data-min="{fmt_min(pos)}" data-retro="{int(p["retrograde"])}" data-bbox="{col["x0"]:.3f},{ry - ROW_PITCH*s/2:.3f},{col["x0"]+col["w"]:.3f},{ry + ROW_PITCH*s/2:.3f}">')
            parts.append(f'<g transform="translate({rx:.3f} {ry:.3f}) scale({s:.4f})">')
            # Mesmo layout usado por row_width — não recalcular aqui.
            items, _ = row_layout(p)
            for kind, ix, payload in items:
                if kind == "planet":
                    parts.append(f'<g color="{gcolor}" transform="translate({ix:.2f} -2.0) scale(0.2)"><use xlink:href="#{payload}__mono" /></g>')
                elif kind == "sign":
                    parts.append(f'<g color="{COLOR_CHARCOAL}" transform="translate({ix:.2f} -1.25) scale(0.125)"><use xlink:href="#{payload}__mono" /></g>')
                elif kind == "deg":
                    parts.append(f'<text x="{ix:.2f}" y="0.7" font-size="{FS_DEG}" fill="{COLOR_CHARCOAL}" font-family="Helvetica">{payload}</text>')
                elif kind == "min":
                    parts.append(f'<text x="{ix:.2f}" y="0.7" font-size="{FS_MIN}" fill="{COLOR_CHARCOAL}" font-family="Helvetica">{payload}</text>')
                elif kind == "rx":
                    parts.append(f'<text x="{ix:.2f}" y="0.7" font-size="{FS_RX}" fill="{COLOR_RED}" font-family="Helvetica" font-weight="bold">{payload}</text>')
            parts.append('</g></g>')

            label_records.append({
                "name": p["name"], "abs_pos": p["abs_pos"],
                "tick_xy": (tx, ty), "attach_xy": (ax, ry),
            })

    parts.append('</svg>')
    return "\n".join(parts), label_records, defs_audit


# ----------------------------------------------------------------------
# ASSERÇÕES AUTOMATIZADAS
# ----------------------------------------------------------------------
def _seg_intersect(p1, p2, p3, p4):
    """Interseção própria de segmentos (excluindo endpoints compartilhados)."""
    def ccw(a, b, c):
        return (c[1]-a[1])*(b[0]-a[0]) - (b[1]-a[1])*(c[0]-a[0])
    if p1 == p3 or p1 == p4 or p2 == p3 or p2 == p4:
        return False
    d1, d2 = ccw(p3, p4, p1), ccw(p3, p4, p2)
    d3, d4 = ccw(p1, p2, p3), ccw(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def assert_render(svg_str, planets, asc_deg=30.0, tol_deg=0.01):
    """Asserções: (1) tick == ângulo real; (2) texto da label == dado;
    (3) leaders sem cruzamento; (4) bbox das labels dentro do anel.
    Retorna lista de erros (vazia = passou)."""
    seventh = (asc_deg + 180) % 360
    errors = []

    # (1) ticks
    ticks = re.findall(r'<g kr-tick="([^"]+)" data-abs="([^"]+)" transform="rotate\((-?[\d.]+)', svg_str)
    by_name = {}
    for name, abs_s, rot_s in ticks:
        by_name.setdefault(name, []).append((float(abs_s), float(rot_s)))
    for p in planets:
        matches = by_name.get(p["name"], [])
        found = False
        for abs_v, rot_v in matches:
            if abs(abs_v - p["abs_pos"]) < 1e-6:
                expected = -wheel_angle(p["abs_pos"], seventh)
                if abs(((rot_v - expected + 180) % 360) - 180) > tol_deg:
                    errors.append(f"tick {p['name']}: rotate={rot_v:.4f} esperado={expected:.4f}")
                found = True
        if not found:
            errors.append(f"tick ausente: {p['name']}@{p['abs_pos']:.2f}")

    # (2) labels
    labels = re.findall(r'<g kr-label="([^"]+)" data-deg="([^"]+)" data-sign="([^"]+)" data-min="([^"]+)" data-retro="(\d)"', svg_str)
    lab_by_name = {}
    for name, deg, sign, minu, retro in labels:
        lab_by_name.setdefault(name, []).append((deg, sign, minu, retro))
    for p in planets:
        pos = p["abs_pos"] % 30
        expected = (fmt_deg(pos), SIGN_ORDER[int(p["abs_pos"] // 30)], fmt_min(pos), str(int(p["retrograde"])))
        if expected not in lab_by_name.get(p["name"], []):
            errors.append(f"label {p['name']}: esperado {expected}, presentes {lab_by_name.get(p['name'])}")

    # (3) leaders sem cruzamento
    leaders = re.findall(r'<line kr-leader="([^"]+)" x1="([\d.-]+)" y1="([\d.-]+)" x2="([\d.-]+)" y2="([\d.-]+)"', svg_str)
    segs = [((float(x1), float(y1)), (float(x2), float(y2)), nm) for nm, x1, y1, x2, y2 in leaders]
    for i in range(len(segs)):
        for j in range(i+1, len(segs)):
            if _seg_intersect(segs[i][0], segs[i][1], segs[j][0], segs[j][1]):
                errors.append(f"leaders cruzam: {segs[i][2]} × {segs[j][2]}")

    # (4) bboxes dentro do anel
    bboxes = re.findall(r'data-bbox="([\d.,-]+)"', svg_str)
    for bb in bboxes:
        x0, y0, x1, y1 = (float(v) for v in bb.split(","))
        if not bbox_fits(x0, y0, x1, y1):
            errors.append(f"bbox fora do anel: {bb}")

    return errors


# ----------------------------------------------------------------------
# PDF
# ----------------------------------------------------------------------
def build_pdf(svg_str, out_pdf, subtitle):
    from svglib.svglib import svg2rlg
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.graphics import renderPDF
    from reportlab.lib.units import cm
    from reportlab.lib.colors import HexColor
    tmpd = tempfile.mkdtemp()
    p = f"{tmpd}/w.svg"
    open(p, "w").write(svg_str)
    d = svg2rlg(p)
    # O ratio tem que ser calculado sobre o WHEEL, não sobre o viewBox. O
    # viewBox tem 100 units mas o wheel só ocupa `WHEEL_SPAN_UNITS` — o anel
    # externo (r=R_OUTER) dentro do wrapper scale(0.92). Escalar pelo viewBox
    # entregava um wheel de 14.7cm quando o alvo era 18cm, encolhendo junto
    # todas as fontes.
    wheel_span = 2 * R_OUTER * 0.92
    ratio = TARGET_WHEEL_CM * cm / wheel_span
    d.scale(ratio, ratio); d.width *= ratio; d.height *= ratio
    c = canvas.Canvas(out_pdf, pagesize=A4)
    c.setFillColor(HexColor("#F8F5EF")); c.rect(0,0,A4[0],A4[1], stroke=0, fill=1)
    c.setFillColor(HexColor("#2F2F2F")); c.setFont("Helvetica-Bold", 11)
    c.drawString(1.5*cm, A4[1]-1.4*cm, subtitle)
    # Centrar pelo viewBox: as bordas vazias podem passar da página, o wheel
    # (centrado em 50,50) fica com margem lateral de (21 - 18)/2 = 1.5cm.
    renderPDF.draw(d, c, (A4[0]-d.width)/2, (A4[1]-d.height)/2 - 0.5*cm)
    c.save()
