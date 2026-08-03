"""Property tests da mandala — conferem o SVG contra o MODELO do Kerykeion.

Regra R2: nenhuma propriedade lê premissa do renderer. As duas fontes são:
  · o modelo   — AstrologicalSubject (point.house, point.sign, abs_pos, cúspides)
  · o desenho  — o SVG que saiu

Se as duas discordam, o desenho mente. É o único critério.

Cada propriedade tem um teste negativo em `prove_bite()`: reintroduz o defeito,
mostra que a propriedade grita, restaura. Propriedade que não morde não vale nada.
"""
import warnings; warnings.filterwarnings("ignore")
import math, re, tempfile

KR = "https://www.kerykeion.net/"

# A LISTA VEM DE PRODUÇÃO. Não é cópia: é o mesmo objeto do app.py.
#
# Em 16/07/2026 o censo rodou com uma lista própria de 18 pontos enquanto
# produção desenhava 19 — sem Nodo Sul, com Ascendente e Meio-do-Céu. Ou seja:
# validei um desenho que ninguém gera e deixei dois corpos sem nenhum teste.
# É a mesma armadilha das duas listas de símbolos (12 vs 18) que fez os glifos
# sumirem do PDF sem erro nenhum. Uma lista, um dono.
#
# O fallback existe só para o caso de o app.py não importar (falta de env var,
# Flask ausente); ele avisa alto em vez de mentir baixo.
def _active_points_de_producao():
    import os, sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    from app import ACTIVE_POINTS as P
    return list(P)


try:
    ACTIVE_POINTS = _active_points_de_producao()
except Exception as _e:      # pragma: no cover
    raise RuntimeError(
        f"props.py não conseguiu ler ACTIVE_POINTS do app.py ({_e}). "
        "Sem isso os testes validariam uma lista de corpos que produção não "
        "desenha — foi exatamente esse o defeito de 16/07/2026. Corrigir o "
        "import em vez de duplicar a lista aqui.")

H_NUM = {'First_House':1,'Second_House':2,'Third_House':3,'Fourth_House':4,
         'Fifth_House':5,'Sixth_House':6,'Seventh_House':7,'Eighth_House':8,
         'Ninth_House':9,'Tenth_House':10,'Eleventh_House':11,'Twelfth_House':12}
SIGN_NUM = {'Ari':0,'Tau':1,'Gem':2,'Can':3,'Leo':4,'Vir':5,
            'Lib':6,'Sco':7,'Sag':8,'Cap':9,'Aqu':10,'Pis':11}
def _attr_of(slug):
    """Atributo do subject para um slug. Derivado, não tabelado: uma tabela é
    uma segunda lista para sair de sincronia com a primeira — foi assim que
    Ascendente e Meio-do-Céu ficaram sem teste nenhum."""
    return slug.lower()


# Ascendente (= cúspide 1) e Meio-do-Céu (= cúspide 10) são corpos desenhados
# na roda em produção. Caem EXATAMENTE sobre a sua cúspide, logo encostam no
# piso da própria caixa casa ∩ signo e só podem ser empurrados para a frente.
_ANGLES = {"Ascendant", "Medium_Coeli"}


# ---------------------------------------------------------------- MODELO
def read_model(subject):
    """Verdade absoluta, direto do Kerykeion. Nada derivado, nada fabricado."""
    H = [subject.first_house, subject.second_house, subject.third_house,
         subject.fourth_house, subject.fifth_house, subject.sixth_house,
         subject.seventh_house, subject.eighth_house, subject.ninth_house,
         subject.tenth_house, subject.eleventh_house, subject.twelfth_house]
    cusps = [float(h.abs_pos) for h in H]
    bodies = {}
    for slug in ACTIVE_POINTS:
        p = getattr(subject, _attr_of(slug), None)
        if p is None:
            continue
        bodies[slug] = {
            "abs_pos": float(p.abs_pos),
            "house": H_NUM[str(p.house)],
            "sign": SIGN_NUM[str(p.sign)],
            "retrograde": bool(getattr(p, "retrograde", False)),
        }
    return {"cusps": cusps, "bodies": bodies, "seventh": cusps[6]}


# ---------------------------------------------------------------- DESENHO
def read_svg(svg):
    """O que o SVG realmente desenhou. Só lê; não interpreta."""
    pts = {}
    for m in re.finditer(
            r"<g[^>]*kr:node='ChartPoint'[^>]*>", svg):
        tag = m.group(0)
        slug = re.search(r"kr:slug='([^']+)'", tag)
        ap = re.search(r"kr:absoluteposition='([\d.]+)'", tag)
        rot = re.search(r"transform='rotate\(([-\d.]+)", tag)
        if not (slug and ap):
            continue
        pts[slug.group(1)] = {
            "abs_attr": float(ap.group(1)),
            "display_wa": ((-float(rot.group(1))) % 360) if rot else None,
        }
    cusps = []
    for m in re.finditer(r"<g[^>]*kr:node='Cusp'[^>]*>", svg):
        tag = m.group(0)
        ap = re.search(r"kr:absoluteposition='([\d.]+)'", tag)
        rot = re.search(r"transform='rotate\(([-\d.]+)", tag)
        if ap:
            cusps.append({"abs_attr": float(ap.group(1)),
                          "wa": ((-float(rot.group(1))) % 360) if rot else None})
    ticks = []
    for m in re.finditer(r"<g[^>]*kr:node='Indicator'[^>]*transform='rotate\(([-\d.]+)", svg):
        ticks.append((-float(m.group(1))) % 360)
    # o SVG cru fica disponível para as propriedades de cúspide, que leem as
    # <line> de divisão de casa (não são grupos kr:node, então não cabem nos
    # dicionários acima). Continua sendo só leitura do desenho.
    return {"points": pts, "cusps": cusps, "ticks": ticks, "_svg": svg}


def wheel_angle(z, seventh):
    return (z - seventh + 180) % 360


def zodiac_of(wa, seventh):
    """Inverso: o ângulo de wheel volta a ser longitude."""
    return (wa + seventh - 180) % 360


def house_of(cusps, pos):
    for i in range(12):
        a, b = cusps[i], cusps[(i + 1) % 12]
        if ((pos - a) % 360) < ((b - a) % 360):
            return i + 1
    return None


def _dd(a, b):
    return abs(((a - b + 180) % 360) - 180)


# ---------------------------------------------------------------- PROPRIEDADES
def prop_all_bodies_rendered(model, drawn):
    """rendered_slugs == active_slugs"""
    want = set(model["bodies"])
    got = set(drawn["points"])
    errs = []
    for s in sorted(want - got):
        errs.append(f"[corpo ausente] {s} está no modelo e NÃO foi desenhado")
    for s in sorted(got - want):
        errs.append(f"[corpo extra] {s} foi desenhado e não está no modelo")
    return errs


def prop_abs_pos_matches(model, drawn):
    """O kr:absoluteposition do SVG é a longitude real do modelo."""
    errs = []
    for slug, b in model["bodies"].items():
        d = drawn["points"].get(slug)
        if not d:
            continue
        if _dd(d["abs_attr"], b["abs_pos"]) > 0.01:
            errs.append(f"[abs_pos] {slug}: SVG diz {d['abs_attr']:.3f}°, "
                        f"modelo diz {b['abs_pos']:.3f}°")
    return errs


def prop_display_in_sign(model, drawn):
    """display_angle dentro do SIGNO real de cada corpo."""
    errs = []
    for slug, b in model["bodies"].items():
        d = drawn["points"].get(slug)
        if not d or d["display_wa"] is None:
            continue
        sug = zodiac_of(d["display_wa"], model["seventh"])
        if int(sug // 30) != b["sign"]:
            names = "Ári Tou Gêm Cân Leã Vir Lib Esc Sag Cap Aqu Pei".split()
            errs.append(
                f"[signo] {slug}: real {b['abs_pos']:.2f}° ({names[b['sign']]}), "
                f"desenhado em {sug:.2f}° ({names[int(sug//30)]}) — "
                f"desvio {_dd(sug, b['abs_pos']):.1f}°")
    return errs


def prop_display_in_house(model, drawn):
    """display_angle dentro da CASA real de cada corpo."""
    errs = []
    for slug, b in model["bodies"].items():
        d = drawn["points"].get(slug)
        if not d or d["display_wa"] is None:
            continue
        sug = zodiac_of(d["display_wa"], model["seventh"])
        h = house_of(model["cusps"], sug)
        if h != b["house"]:
            errs.append(
                f"[casa] {slug}: real {b['abs_pos']:.2f}° (casa {b['house']}), "
                f"desenhado em {sug:.2f}° (casa {h}) — "
                f"desvio {_dd(sug, b['abs_pos']):.1f}°")
    return errs


def prop_tick_is_true(model, drawn):
    """Todo tick/indicador está numa longitude REAL de corpo.

    Aplica-se ao SVG CRU do Kerykeion, que é onde o packing atua. O app.py
    apaga as linhas-guia inteiras depois (decisão da Márcia, comportamento
    Astro Gold: o grau ao lado do glifo revela a posição, sem o fio). Num SVG
    já pós-processado não sobra indicador nenhum e a propriedade não tem o que
    dizer — dizer "violação" ali seria acusar uma decisão de produto.

    A remoção é atacadista: ou estão todos, ou nenhum. Some UM só e isso é
    defeito — é o caso que a propriedade continua pegando.
    """
    errs = []
    if not drawn["ticks"]:
        return []   # SVG pós-processado: linhas-guia removidas de propósito
    trues = {slug: wheel_angle(b["abs_pos"], model["seventh"])
             for slug, b in model["bodies"].items()}
    for slug, twa in trues.items():
        if not any(_dd(t, twa) < 0.05 for t in drawn["ticks"]):
            errs.append(f"[tick] {slug}: nenhum indicador na longitude real "
                        f"{model['bodies'][slug]['abs_pos']:.2f}°")
    return errs


def prop_cusps_match(model, drawn):
    """As cúspides do SVG são as cúspides do modelo."""
    errs = []
    if len(drawn["cusps"]) != 12:
        errs.append(f"[cúspide] {len(drawn['cusps'])} desenhadas, esperadas 12")
        return errs
    for i, c in enumerate(model["cusps"]):
        if not any(_dd(d["abs_attr"], c) < 0.01 for d in drawn["cusps"]):
            errs.append(f"[cúspide] casa {i+1}: modelo diz {c:.3f}°, "
                        f"nenhuma cúspide do SVG bate")
    for d in drawn["cusps"]:
        if d["wa"] is None:
            continue
        z = zodiac_of(d["wa"], model["seventh"])
        if _dd(z, d["abs_attr"]) > 0.02:
            errs.append(f"[cúspide] desenhada em {z:.2f}° mas declara "
                        f"{d['abs_attr']:.2f}°")
    return errs


TARGET_SEPARATION = 8.0   # o teto da fábrica; ninguém precisa de mais que isso
COMPRESSION_TOL = 0.3     # folga: a caixa aqui é a pura, sem a margem do packing


def _box_of(model, slug):
    """Caixa `casa ∩ signo` do corpo, em longitude — só do MODELO.

    Não lê nada do renderer: as fronteiras da casa são as cúspides Placidus do
    Kerykeion, as do signo são os 30° do zodíaco. É o espaço onde o corpo pode
    ser desenhado sem mentir.
    """
    b = model["bodies"][slug]
    h0 = model["cusps"][b["house"] - 1]
    h1 = model["cusps"][b["house"] % 12]
    s0 = b["sign"] * 30.0
    pos = b["abs_pos"]
    lo = pos - min((pos - h0) % 360, (pos - s0) % 360)
    hi = pos + min((h1 - pos) % 360, ((s0 + 30.0) - pos) % 360)
    return lo, hi


def prop_no_compression(model, drawn):
    """O desenho não aproxima dois corpos além do que a geometria obriga.

    Afastar para separar é o propósito do packing. Aproximar é defeito — a
    menos que seja inevitável, e a inevitabilidade é calculável.

    Para cada par vizinho na ordem zodiacal, o vão desenhado tem que ser pelo
    menos o menor destes três:

      · o vão REAL         — não faz sentido exigir mais do que existe;
      · 8°                 — o teto da fábrica, ninguém precisa de mais;
      · o ÓTIMO GEOMÉTRICO — para toda janela de corpos consecutivos [a,b],
                             todos eles cabem apenas entre o piso da caixa de
                             `a` e o teto da caixa de `b`. Esse espaço dividido
                             por (b-a) vãos é o máximo que qualquer arranjo
                             honesto alcança. O vínculo é o menor entre as
                             janelas que contêm o par.

    O terceiro termo é o que torna a propriedade justa. Na Monica, Vênus, Urano,
    Sol, Marte, Júpiter e Vesta ocupam duas caixas vizinhas (Câncer ∩ casa 12 e
    Leão ∩ casa 12) que juntas dão 28.6° para 6 corpos: 5.72° por vão, e ponto.
    Exigir 8° entre Sol e Marte obrigaria Vênus a sair da casa 12 — trocar uma
    mentira por outra.

    O defeito que ela nasceu para pegar: o packing empacotava cada grupo
    (casa, signo) isolado, sem enxergar o vizinho. Sol (fim do grupo Câncer) e
    Marte (início do grupo Leão) eram empurrados contra a mesma fronteira dos
    120° e desenhados a 0.30° um do outro — as outras seis propriedades passavam,
    porque cada um estava na sua casa e no seu signo, esmagado contra a parede.
    """
    errs = []
    bodies = [(b["abs_pos"], slug) for slug, b in model["bodies"].items()
              if drawn["points"].get(slug, {}).get("display_wa") is not None]
    n = len(bodies)
    if n < 2:
        return errs
    bodies.sort()

    # o círculo vira reta no maior vão real — o mesmo corte, derivado do modelo
    gaps_r = [(bodies[(i + 1) % n][0] - bodies[i][0]) % 360 for i in range(n)]
    k = max(range(n), key=lambda i: gaps_r[i])
    seq = [bodies[(k + 1 + j) % n][1] for j in range(n)]
    cut = (bodies[k][0] + gaps_r[k] / 2.0) % 360

    x = [(model["bodies"][s]["abs_pos"] - cut) % 360 for s in seq]
    L, U = [], []
    for i, s in enumerate(seq):
        lo, hi = _box_of(model, s)
        L.append(x[i] - (model["bodies"][s]["abs_pos"] - lo) % 360)
        U.append(x[i] + (hi - model["bodies"][s]["abs_pos"]) % 360)

    # ótimo geométrico de cada vão: a janela mais restritiva que o contém
    cap = [TARGET_SEPARATION] * (n - 1)
    for a in range(n):
        for b in range(a + 1, n):
            w = (U[b] - L[a]) / (b - a)
            for j in range(a, b):
                if w < cap[j]:
                    cap[j] = w

    disp = {s: zodiac_of(drawn["points"][s]["display_wa"], model["seventh"])
            for s in seq}
    drawn_gaps = [(disp[seq[i + 1]] - disp[seq[i]]) % 360 for i in range(n - 1)]

    # ordem preservada? um par trocado vira um vão de quase 360°
    for i, g in enumerate(drawn_gaps):
        if g > 180.0:
            errs.append(f"[ordem] {seq[i]}/{seq[i+1]}: a ordem zodiacal foi "
                        f"invertida no desenho")

    for i in range(n - 1):
        real = (model["bodies"][seq[i + 1]]["abs_pos"]
                - model["bodies"][seq[i]]["abs_pos"]) % 360
        need = min(real, TARGET_SEPARATION, cap[i])
        if drawn_gaps[i] < need - COMPRESSION_TOL:
            errs.append(
                f"[compressão] {seq[i]}/{seq[i+1]}: reais {real:.2f}°, "
                f"desenhados a {drawn_gaps[i]:.2f}° — exigido {need:.2f}° "
                f"(teto geométrico da janela {cap[i]:.2f}°)")
    return errs


# ---------------------------------------------------------------- CÚSPIDES
# Geometria da linha de divisão de casa e da coluna do planeta, MEDIDA do SVG
# de fábrica (Kerykeion 5.12.8, wheel-only, style modern):
#   · CENTER = 50; a linha de casa vai de y=6.5 a y=28  → raio 43.5 a 22.
#   · a coluna do planeta empilha glifo(r=39) grau(35.5) signo(32) min(28) RX(25)
#     → ocupa a faixa radial [24, 40].
#   · largura angular da coluna: o elemento mais largo mede 2.70u no raio menor
#     (RX, r=25) → 2·asin(1.35/25) = 6.19°, meia-largura 3.10°. Uso 3.2° com
#     folga. Este número é o ESPECIFICADOR de legibilidade: "nenhuma linha de
#     cúspide passa a menos disto do centro de uma coluna". O renderer que vier
#     a interromper as linhas terá de usar o MESMO valor — é de propósito, para
#     que teste e desenho concordem por construção sobre o que é "colar no
#     glifo". A prova de mordida guarda o piso (a linha inteira, sem cortar,
#     viola) e o visual impresso guarda o teto (cortar demais aparece).
try:
    from .geometry import (CUSP_CENTER as _CUSP_CENTER, COLUMN_R_INNER,
                           COLUMN_R_OUTER, COLUMN_HALF_WIDTH_DEG,
                           CUSP_ANGLE_SLUGS)
except ImportError:
    from geometry import (CUSP_CENTER as _CUSP_CENTER, COLUMN_R_INNER,
                          COLUMN_R_OUTER, COLUMN_HALF_WIDTH_DEG,
                          CUSP_ANGLE_SLUGS)


def read_house_lines(svg):
    """As 12 linhas radiais de divisão de casa (y de 6.5 a 28). Ignora os 12
    ticks da régua (y de 28 a 30.5), que ficam DENTRO da coluna e nunca tocam
    glifo. Cada linha vira (wheel_angle, r_out, r_in, width)."""
    out = []
    for m in re.finditer(r"<line\b[^>]*/>", svg):
        tag = m.group(0)
        rot = re.search(r"rotate\((-?[\d.]+)", tag)
        y1 = re.search(r"y1='([\d.]+)'", tag)
        y2 = re.search(r"y2='([\d.]+)'", tag)
        x1 = re.search(r"x1='([\d.]+)'", tag)
        w = re.search(r"stroke-width='([\d.]+)'", tag)
        if not (rot and y1 and y2 and x1):
            continue
        if abs(float(x1.group(1)) - _CUSP_CENTER) > 0.01:
            continue
        ya, yb = float(y1.group(1)), float(y2.group(1))
        # A linha de divisão de casa vive em y∈[6.5,28] (raio 43.5..22). Os ticks
        # da régua vivem em y∈[28,30.5] (dentro de r=22). Filtra pela FAIXA, não
        # pelo comprimento: um segmento interrompido é curto (ex. 6.5..9) e tem
        # de continuar sendo reconhecido — senão a cúspide cortada some do teste.
        if not (min(ya, yb) >= 6.4 and max(ya, yb) <= 28.1):
            continue
        out.append({
            "wa": (-float(rot.group(1))) % 360,
            "r_out": _CUSP_CENTER - min(ya, yb),
            "r_in": _CUSP_CENTER - max(ya, yb),
            "width": float(w.group(1)) if w else None,
        })
    return out


def prop_cusp_collinear(model, drawn):
    """Cada linha de divisão está no ângulo real de uma cúspide, e todos os
    segmentos de uma mesma cúspide são colineares (mesmo ângulo, ±0.01°).

    A interrupção (a linha some atrás do bloco e reaparece do outro lado) só é
    honesta se os dois segmentos continuarem sobre a MESMA reta radial. Um
    segmento que reaparece torto desenha uma cúspide que não existe — é o modo
    de falha específico que esta propriedade tranca antes de a interrupção ser
    escrita.
    """
    errs = []
    lines = read_house_lines(svg=drawn["_svg"])
    cusp_wa = [wheel_angle(c, model["seventh"]) for c in model["cusps"]]
    groups = {}
    for ln in lines:
        j = min(range(12), key=lambda i: _dd(cusp_wa[i], ln["wa"]))
        if _dd(cusp_wa[j], ln["wa"]) > 0.5:
            errs.append(f"[cúspide-solta] segmento em {ln['wa']:.3f}° não "
                        f"corresponde a nenhuma cúspide do modelo")
            continue
        groups.setdefault(j, []).append(ln["wa"])
    for j, angs in groups.items():
        if max(angs) - min(angs) > 0.01:
            errs.append(f"[colinear] casa {j+1}: segmentos em ângulos "
                        f"diferentes ({min(angs):.3f}°..{max(angs):.3f}°) — "
                        f"a cúspide interrompida deixou de ser uma reta só")
    return errs


def prop_cusp_no_overlap(model, drawn):
    """Nenhuma linha de cúspide pinta por cima da coluna de um planeta.

    Uma linha de casa cruza a coluna de um planeta quando (a) sua faixa radial
    invade [24,40] — onde vivem glifo/grau/signo/minutos/RX — e (b) seu ângulo
    cai a menos de 3.2° do centro angular da coluna. Aí a linha risca o texto.

    É o defeito que a interrupção existe para eliminar. Vale para produção
    ATUAL (linha fina 0.07u) e para a versão reforçada (0.6u): a fina viola
    baixinho, a grossa viola alto. Só a versão interrompida zera. Portanto esta
    propriedade fica NÃO-nula até a feature existir — é o alvo, não o estado.

    EXCEÇÃO (geometry.CUSP_ANGLE_SLUGS): a linha da casa 1 não viola por cruzar
    a coluna do Ascendente, nem a da casa 10 pela do Meio-do-Céu. O rótulo do
    ângulo É o indicativo daquela cúspide — linha e rótulo são o mesmo objeto,
    e a interrupção que "protegia" um do outro apagava o eixo ASC/MC em 100%
    dos mapas (visto pela Márcia em 16/07). A exceção é POR PAR: a linha da
    casa 1 continua cedendo passagem a qualquer outro corpo, e a coluna do
    Ascendente continua protegida de qualquer outra linha.
    """
    errs = []
    lines = read_house_lines(svg=drawn["_svg"])
    cusp_wa = [wheel_angle(c, model["seventh"]) for c in model["cusps"]]
    cols = []
    for slug, d in drawn["points"].items():
        if d.get("display_wa") is not None:
            cols.append((slug, d["display_wa"]))
    for ln in lines:
        # a faixa radial da linha invade a faixa da coluna?
        if ln["r_in"] > COLUMN_R_OUTER or ln["r_out"] < COLUMN_R_INNER:
            continue
        # de que casa é esta linha? (mesmo pareamento da prop de colinearidade)
        j = min(range(12), key=lambda i: _dd(cusp_wa[i], ln["wa"]))
        own = CUSP_ANGLE_SLUGS.get(j + 1) if _dd(cusp_wa[j], ln["wa"]) <= 0.5 else None
        for slug, cwa in cols:
            if slug == own:
                continue
            if _dd(ln["wa"], cwa) < COLUMN_HALF_WIDTH_DEG:
                errs.append(
                    f"[sobreposição] linha da cúspide em {ln['wa']:.2f}° risca "
                    f"a coluna de {slug} ({cwa:.2f}°) — a {_dd(ln['wa'], cwa):.2f}° "
                    f"do centro, dentro dos {COLUMN_HALF_WIDTH_DEG:.1f}° do bloco")
    return errs


PROPS = [
    ("todos os corpos desenhados", prop_all_bodies_rendered),
    ("abs_pos do SVG == modelo",   prop_abs_pos_matches),
    ("display dentro do SIGNO",    prop_display_in_sign),
    ("display dentro da CASA",     prop_display_in_house),
    ("tick na longitude real",     prop_tick_is_true),
    ("cúspides == modelo",         prop_cusps_match),
    ("desenho não comprime",       prop_no_compression),
]

# As duas propriedades da feature de cúspide reforçada vivem à parte enquanto a
# feature não existe: prop_cusp_no_overlap fica não-nula de propósito (é o alvo)
# e derrubaria o baseline limpo do prove_bite. Entram em PROPS — e no censo
# 1000×2 = zero — quando a interrupção estiver escrita e passando.
CUSP_PROPS = [
    ("cúspide não sobrepõe glifo",   prop_cusp_no_overlap),
    ("cúspide interrompida colinear", prop_cusp_collinear),
]


def check_all(subject, svg):
    model = read_model(subject)
    drawn = read_svg(svg)
    out = []
    for name, fn in PROPS:
        out.append((name, fn(model, drawn)))
    return out
