"""Constrained packing — a única correção na causa.

NÃO substitui a fábrica. Usa as cúspides Placidus reais, as casas reais, os 18
corpos, os glifos originais e o `_draw_single_planet_in_ring` do Kerykeion.
Troca UM número: a separação, que hoje é a constante 8.0 e passa a ser
calculada por cluster.

A causa, medida: 7 corpos × 8° = 48° de separação exigida para um espaço de
30° (interseção casa 7 ∩ Escorpião, no mapa da Andreia). Transborda 18° por
aritmética. Nenhum ajuste de cor ou de tether conserta isso.

A regra:
    uma label pode se deslocar para evitar colisão,
    mas nunca pode sair da interseção entre seu signo e sua casa.

Ponto de patch: `draw_modern._resolve_planet_collisions` — função de módulo,
resolvida por lookup global em `_draw_planet_ring`, portanto substituível. O
wrapper de `_draw_planet_ring` existe só para passar as cúspides adiante (o
resolver original não as recebe).
"""
import warnings; warnings.filterwarnings("ignore")
import math

import kerykeion.charts.draw_modern as dm

H_NUM = {'First_House':1,'Second_House':2,'Third_House':3,'Fourth_House':4,
         'Fifth_House':5,'Sixth_House':6,'Seventh_House':7,'Eighth_House':8,
         'Ninth_House':9,'Tenth_House':10,'Eleventh_House':11,'Twelfth_House':12}
SIGN_NUM = {'Ari':0,'Tau':1,'Gem':2,'Can':3,'Leo':4,'Vir':5,
            'Lib':6,'Sco':7,'Sag':8,'Cap':9,'Aqu':10,'Pis':11}

MAX_SEPARATION = 8.0     # o teto da fábrica: nunca separa MAIS que isso
MIN_SEPARATION = 0.0     # piso: 0 = corpos coincidentes podem ficar coincidentes
EDGE_MARGIN = 0.15       # folga para não encostar na fronteira do signo/casa

# ESCALA UNIFORME. A fábrica dá um multiplicador por planeta (GLYPH_SCALE_MAP:
# Sol 1.1, Júpiter/Saturno/Urano/Netuno/Marte/Quíron/Nodos 0.95, resto 1.0) com
# a intenção de igualar o "peso visual". Na prática, dentro de um cluster
# apertado a diferença aparece como desigualdade de tamanho. Uniformizamos.
UNIFORM_GLYPH = 1.0

# ESCALA DE GLIFO: DESLIGADA.
# Histórico (2026-07-15): o packing sozinho — só o cálculo da separação, sem
# tocar no tamanho do glifo — produziu o melhor resultado avaliado pela Márcia
# ("Andreia melhor exemplo que vi até agora"). Cada tentativa posterior de
# resolver o amontoamento encolhendo glifos piorou a leitura:
#   · escala por cluster    -> vizinhos de grupos diferentes com tamanhos
#                              diferentes, colados (rek_8: Sol e Nodo grandes
#                              ao lado de um miolo minúsculo)
#   · glifo uniforme        -> miolo encolheu mais ainda, contraste piorou
#   · escala por vizinhança -> corrigiu o Nodo, não o Sol (7.5° de gap)
# O amontoamento é real e continua em aberto, mas encolher glifo NÃO é a saída:
# troca "ilegível por sobreposição" por "ilegível por tamanho".
SCALE_GLYPHS = False

# Largura ANGULAR do glifo — MEDIDA, não estimada:
#   28 unidades × PLANET_SCALE_BASE (0.135) = 3.78u de largura linear,
#   desenhado a y=11 → raio 39 → 2·asin((3.78/2)/39) = 5.56° de arco.
# (o wedge scale 0.92 encolhe glifo e raio juntos, então não afeta o ângulo)
GLYPH_ARC = 5.56

# Piso de legibilidade: a 18cm o wheel tem 5.546 pt/unidade, e o grau é
# DEGREES_FONT_SIZE=2 → 11.09pt em escala cheia. Para o grau não cair abaixo
# de 6pt, a escala não pode passar de 6/11.09 = 0.541.
MIN_GLYPH_SCALE = 0.54

# ---- CÚSPIDES: só cor e espessura, nenhuma geometria -------------------
# A fábrica desenha as 8 casas não-angulares com NORMAL_STROKE_WIDTH = 0.07u
# = 0.39pt a 18cm, em #b0b0bf. É um fio de cabelo cinza-claro: não dá para ver
# em que casa o planeta está. As angulares (1,4,7,10) vão a 0.6u = 3.33pt —
# 8.5x mais grossas, o que também exagera o contraste entre elas.
# REVERTIDO (2026-07-15). Engrossar de 0.07->0.22 deixou a linha visível — mas
# ela ATRAVESSA a coluna inteira do planeta (raio 43.5 a 22; o glifo está em 39,
# o grau em 35.5, o signo em 32, os minutos em 28). Verificado: nenhum planeta
# se moveu (18/18 em posição idêntica). O que a Márcia viu foi a linha escura
# cortando o meio dos grupos e sendo lida como mais um elemento amontoado.
# O problema não é a espessura, é o traçado atravessar a zona dos glifos.
# Se for retomado: encurtar a linha na faixa dos glifos, ou desenhá-la ANTES
# deles com halo claro. Enquanto isso, valores da fábrica.
CUSP_WIDTH_NORMAL  = None    # None = não mexer
CUSP_WIDTH_ANGULAR = None
CUSP_COLOR = None

_ORIG_RING = dm._draw_planet_ring
_ORIG_RESOLVE = dm._resolve_planet_collisions
_ORIG_SINGLE = dm._draw_single_planet_in_ring
_ORIG_NW = dm.NORMAL_STROKE_WIDTH
_ORIG_AW = dm.ANGULAR_STROKE_WIDTH
_ORIG_COLOR = dm.COLOR_STROKE
_CTX = {}


# ------------------------------------------------------------------ arcos
def _norm(a):
    return a % 360.0


def _arc_intersect(a0, a1, b0, b1):
    """Interseção de dois arcos circulares [a0,a1] e [b0,b1] (sentido
    crescente, podendo cruzar 0°). Retorna (lo, span) ou None.

    Trabalha em coordenada relativa a `a0` para nunca depender de wraparound.
    """
    span_a = _norm(a1 - a0)
    # b relativo a a0
    b0r = _norm(b0 - a0)
    b1r = b0r + _norm(b1 - b0)
    lo_r = max(0.0, b0r)
    hi_r = min(span_a, b1r)
    if hi_r - lo_r <= 1e-9:
        # b pode cruzar o 0 relativo: tentar a volta
        b0r2 = b0r - 360.0
        b1r2 = b0r2 + _norm(b1 - b0)
        lo_r = max(0.0, b0r2)
        hi_r = min(span_a, b1r2)
        if hi_r - lo_r <= 1e-9:
            return None
    return (_norm(a0 + lo_r), hi_r - lo_r)


def allowed_interval(point, cusps):
    """Interseção casa ∩ signo, em longitude zodiacal. Ambas REAIS: a casa vem
    das cúspides Placidus do modelo, o signo dos 30° do zodíaco."""
    h = H_NUM[str(point.house)]
    s = SIGN_NUM[str(point.sign)]
    h0, h1 = cusps[h - 1], cusps[h % 12]
    s0, s1 = s * 30.0, s * 30.0 + 30.0
    return _arc_intersect(h0, h1, s0, s1)


# ------------------------------------------- projeção isotônica (PAVA)
def _pava(u, w=None):
    """Regressão isotônica: menor sequência não-decrescente em L2.
    Pool Adjacent Violators. É o que troca a cascata unilateral por um
    deslocamento simétrico e mínimo."""
    n = len(u)
    if n == 0:
        return []
    vals = list(u)
    wts = [1.0] * n if w is None else list(w)
    blocks = [[vals[i], wts[i], 1] for i in range(n)]   # [média, peso, tamanho]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] <= blocks[i + 1][0] + 1e-12:
            i += 1
            continue
        # viola: funde
        m1, w1, s1 = blocks[i]
        m2, w2, s2 = blocks[i + 1]
        merged = [(m1 * w1 + m2 * w2) / (w1 + w2), w1 + w2, s1 + s2]
        blocks[i:i + 2] = [merged]
        if i > 0:
            i -= 1
    out = []
    for m, _, size in blocks:
        out.extend([m] * size)
    return out


def _solve_chain(x, L, U, seps):
    """Projeção de `x` no conjunto viável, ou None se inviável.

    viável := { d :  L_i <= d_i <= U_i  e  d_{i+1} - d_i >= seps_i }

    A substituição e_i = d_i - Σ_{j<i} seps_j troca a restrição de separação
    por MONOTONICIDADE, e aí o problema é regressão isotônica com caixa. Com
    limites monótonos, a projeção é o PAVA seguido de clip — e os limites são
    monotonizados de graça: e crescente e e_i >= L_i para todo i equivale a
    e_i >= max_{j<=i} L_j. Idem, espelhado, para U.
    """
    n = len(x)
    cum = [0.0] * n
    for i in range(1, n):
        cum[i] = cum[i - 1] + seps[i - 1]

    u = [x[i] - cum[i] for i in range(n)]
    Lp = [L[i] - cum[i] for i in range(n)]
    Up = [U[i] - cum[i] for i in range(n)]

    Lmax = list(Lp)
    for i in range(1, n):
        Lmax[i] = max(Lmax[i], Lmax[i - 1])
    Umin = list(Up)
    for i in range(n - 2, -1, -1):
        Umin[i] = min(Umin[i], Umin[i + 1])

    for i in range(n):
        if Lmax[i] > Umin[i] + 1e-9:
            return None                      # estas separações não cabem

    e = _pava(u)
    e = [min(max(e[i], Lmax[i]), Umin[i]) for i in range(n)]
    return [e[i] + cum[i] for i in range(n)]


def _fit_seps(x, L, U, seps):
    """Encolhe as separações até caberem — só onde não cabem.

    A viabilidade de `_solve_chain` é exatamente esta: para todo par a<b,
    a soma das separações entre a e b não pode passar de U_b - L_a, que é
    todo o espaço que as duas caixas das pontas oferecem à janela.

    Quando uma janela estoura, o encolhimento é aplicado APENAS a ela, na
    proporção exata do que falta. Um α global — encolher tudo até o pior
    caso caber — foi o que deixou o Sol e Marte da Monica a 5.72°: a caixa
    Câncer ∩ casa 12 tem 15.6° para três corpos e não comporta 8°, e esse
    aperto local rebaixava a separação do mapa inteiro. O aperto de um
    aglomerado não é problema dos outros.
    """
    s = list(seps)
    n = len(x)
    for _ in range(4 * n + 20):
        worst = None
        for a in range(n):
            tot = 0.0
            for b in range(a + 1, n):
                tot += s[b - 1]
                deficit = tot - (U[b] - L[a])
                if deficit > 1e-9 and (worst is None or deficit > worst[0]):
                    worst = (deficit, a, b, tot, U[b] - L[a])
        if worst is None:
            return s
        _, a, b, need, avail = worst
        f = max(0.0, avail / need) if need > 1e-12 else 0.0
        for j in range(a, b):
            s[j] *= f
    return [0.0] * (n - 1)          # desistiu: separação nula é sempre viável


def pack_chain(x, L, U, max_sep=MAX_SEPARATION):
    """Empacota uma cadeia de corpos já linearizada e em ordem crescente.

    x: longitudes reais.  L/U: limites da caixa casa ∩ signo de cada corpo.

    A separação alvo é `max_sep` entre CADA par de vizinhos — vizinhos no
    círculo, não dentro de um balde. É essa mudança que conserta o defeito da
    Monica: o Sol (fim do grupo Câncer) e Marte (início do grupo Leão) agora
    se enxergam através da fronteira dos 120° e o par é obrigado a abrir.

    Cada par ganha um teto próprio — nenhum par pode abrir mais do que as duas
    caixas permitem — para que um par geometricamente apertado não derrube a
    separação do mapa inteiro. Se ainda assim não couber, um único fator α
    encolhe todas as separações até caber; α=0 é sempre viável, porque com
    separação nula cada corpo fica na sua própria posição real.
    """
    n = len(x)
    if n == 0:
        return []
    if n == 1:
        return [min(max(x[0], L[0]), U[0])]

    seps = _fit_seps(x, L, U, [max_sep] * (n - 1))
    d = _solve_chain(x, L, U, seps)
    if d is None:                          # não deve acontecer: _fit_seps só
        d = _solve_chain(x, L, U, [0.0] * (n - 1))   # devolve seps viáveis
    return d


def pack_group(trues, lo, span, max_sep=MAX_SEPARATION):
    """Compat: um grupo isolado é uma cadeia cujos corpos partilham a caixa.

    Mantida para os testes que a chamam diretamente. O renderer NÃO usa mais
    esta função — empacotar grupo a grupo era o defeito.
    """
    n = len(trues)
    if n == 0:
        return []
    lo_b, hi_b = EDGE_MARGIN, max(EDGE_MARGIN, span - EDGE_MARGIN)
    return pack_chain(list(trues), [lo_b] * n, [hi_b] * n, max_sep)


# ------------------------------------------------------------- o patch
def _constrained_resolve(planets_with_angles, min_separation=MAX_SEPARATION):
    """Substitui o espalhamento fixo de 8°.

    Agrupa por (casa real, signo real) — não por proximidade angular — e
    empacota cada grupo dentro da interseção casa ∩ signo. Um corpo nunca
    sai do seu signo nem da sua casa; no limite ele fica onde está.
    """
    if not planets_with_angles:
        return planets_with_angles
    cusps = _CTX.get("cusps")
    seventh = _CTX.get("seventh")
    if cusps is None:
        # sem contexto não há como respeitar casa/signo: falha alto em vez de
        # cair silenciosamente no comportamento antigo.
        raise RuntimeError(
            "constrained packing sem cúspides no contexto — o wrapper de "
            "_draw_planet_ring não rodou. Não há fallback silencioso.")

    _CTX["scale_by_slug"] = {}

    items = list(planets_with_angles)
    n = len(items)
    trues = [float(it["point"].abs_pos) for it in items]

    # CORTE: o círculo vira reta no MAIOR vão real entre corpos vizinhos. Com
    # até 18 corpos o maior vão é sempre >= 360/18 = 20°, folgado demais para
    # que o par que ele separa precise de qualquer ajuste — cortar ali não
    # perde restrição nenhuma.
    order = sorted(range(n), key=lambda i: trues[i])
    k, gap = 0, -1.0
    for j in range(n):
        g = _norm(trues[order[(j + 1) % n]] - trues[order[j]])
        if g > gap:
            gap, k = g, j
    cut = _norm(trues[order[k]] + gap / 2.0)
    seq = [order[(k + 1 + j) % n] for j in range(n)]

    x, L, U, keep = [], [], [], []
    for i in seq:
        it = items[i]
        iv = allowed_interval(it["point"], cusps)
        if iv is None:
            # sem interseção casa ∩ signo não há caixa: o corpo fica onde está
            it["display_angle"] = it["angle"]
            continue
        z_lo, span = iv
        t = trues[i]
        xi = _norm(t - cut)
        lo_rel = xi - _norm(t - z_lo)
        hi_rel = lo_rel + span
        m = min(EDGE_MARGIN, span / 4.0)          # margem nunca fecha a caixa
        lo_rel, hi_rel = lo_rel + m, hi_rel - m
        x.append(min(max(xi, lo_rel), hi_rel))
        L.append(lo_rel)
        U.append(hi_rel)
        keep.append(it)

    if keep:
        packed = pack_chain(x, L, U)
        for it, dx in zip(keep, packed):
            z = _norm(cut + dx)
            it["display_angle"] = _norm(dm._zodiac_to_wheel_angle(z, seventh))

    out = items

    # ---- ESCALA POR VIZINHANÇA VISUAL -------------------------------
    # O deslocamento é por grupo (casa, signo) — é isso que garante que nenhum
    # corpo saia da sua casa. Mas o AMONTOAMENTO é visual: o olho não vê a
    # fronteira da casa, vê glifos colados. Escalar por grupo deixava vizinhos
    # de grupos diferentes em tamanhos diferentes, encostados — foi o que
    # aconteceu no rek_8 (Sol e Nodo grandes ao lado de um miolo minúsculo).
    # Aqui a unidade de escala é o aglomerado VISUAL, que atravessa fronteiras.
    out.sort(key=lambda it: it["display_angle"])
    n = len(out)
    if n > 1:
        # encadeia vizinhos cujo gap de DISPLAY é menor que um glifo
        chains = []
        cur = [0]
        for i in range(1, n):
            gap = _norm(out[i]["display_angle"] - out[i-1]["display_angle"])
            if gap < GLYPH_ARC:
                cur.append(i)
            else:
                chains.append(cur); cur = [i]
        chains.append(cur)
        # wraparound: primeiro e último podem ser vizinhos
        if len(chains) > 1:
            wrap = _norm(out[0]["display_angle"] - out[-1]["display_angle"])
            if wrap < GLYPH_ARC:
                chains[0] = chains[-1] + chains[0]
                chains.pop()
        for ch in chains:
            if len(ch) < 2:
                continue
            gaps = [_norm(out[ch[j+1]]["display_angle"] - out[ch[j]]["display_angle"])
                    for j in range(len(ch)-1)]
            tight = min(gaps) if gaps else GLYPH_ARC
            k = max(min(1.0, tight / GLYPH_ARC), MIN_GLYPH_SCALE)
            if SCALE_GLYPHS and k < 1.0:
                for j in ch:
                    _CTX["scale_by_slug"][out[j]["point"].name] = k

    out.sort(key=lambda it: it["angle"])
    return out


def _patched_ring(planets, planets_settings, seventh_house_degree_ut, houses, **kw):
    """Só passa as cúspides adiante. Todo o desenho continua sendo o da fábrica."""
    _CTX["cusps"] = [float(h.abs_pos) for h in houses]
    _CTX["seventh"] = seventh_house_degree_ut
    try:
        return _ORIG_RING(planets, planets_settings, seventh_house_degree_ut,
                          houses, **kw)
    finally:
        _CTX.clear()


def _patched_single(point, display_angle, counter_rotation, color, **kw):
    """Aplica a escala do cluster. TODO o desenho continua sendo o da fábrica —
    a função original já aceita esses cinco parâmetros por chamada."""
    k = _CTX.get("scale_by_slug", {}).get(point.name, 1.0)
    if k >= 1.0:
        return _ORIG_SINGLE(point, display_angle, counter_rotation, color, **kw)

    # O GLIFO é dividido pelo multiplicador do planeta (Sol 1.1, Júpiter 0.95…)
    # porque a função original o multiplica de volta: emitido = base*k/mult*mult
    # = base*k, igual para todo mundo. É o que dá tamanho UNIFORME.
    mult = dm.GLYPH_SCALE_MAP.get(point.name, 1.0) * UNIFORM_GLYPH
    kw["planet_scale_base"] = kw.get("planet_scale_base", dm.PLANET_SCALE_BASE) * k / mult

    # As FONTES levam k puro. Dividi-las por mult também era bug: nada as
    # multiplica de volta, então o grau do Sol saía a 2*k/1.1 — abaixo do piso.
    kw["degrees_font_size"] = kw.get("degrees_font_size", dm.DEGREES_FONT_SIZE) * k
    kw["sign_scale_base"]   = kw.get("sign_scale_base",   dm.SIGN_SCALE_BASE) * k
    kw["minutes_font_size"] = kw.get("minutes_font_size", dm.MINUTES_FONT_SIZE) * k
    kw["rx_font_size"]      = kw.get("rx_font_size",      dm.RX_FONT_SIZE) * k
    return _ORIG_SINGLE(point, display_angle, counter_rotation, color, **kw)


def install():
    if CUSP_WIDTH_NORMAL is not None:
        dm.NORMAL_STROKE_WIDTH = CUSP_WIDTH_NORMAL
    if CUSP_WIDTH_ANGULAR is not None:
        dm.ANGULAR_STROKE_WIDTH = CUSP_WIDTH_ANGULAR
    if CUSP_COLOR is not None:
        dm.COLOR_STROKE = CUSP_COLOR
    dm._draw_planet_ring = _patched_ring
    dm._resolve_planet_collisions = _constrained_resolve
    dm._draw_single_planet_in_ring = _patched_single


def uninstall():
    dm.NORMAL_STROKE_WIDTH = _ORIG_NW
    dm.ANGULAR_STROKE_WIDTH = _ORIG_AW
    dm.COLOR_STROKE = _ORIG_COLOR
    dm._draw_planet_ring = _ORIG_RING
    dm._resolve_planet_collisions = _ORIG_RESOLVE
    dm._draw_single_planet_in_ring = _ORIG_SINGLE
