"""Tabela de posições + painel de elementos e modalidades.

Duas peças novas do PDF (pedido da Márcia, 17/07, depois da leitura da
Marcelle — a leitora olhou a mandala e não sabia o que os símbolos
significam):

  1. TABELA DE POSIÇÕES — para cada corpo ativo: glifo + nome do planeta,
     glifo + nome do signo, casa. Sem grau nem minuto: a mandala e a tabela
     de aspectos já dão isso.
  2. PAINEL DE ELEMENTOS E MODALIDADES — contagem pura, sem interpretação.

Os glifos vêm dos MESMOS `<symbol>` do SVG que a mandala usa (Kerykeion),
extraídos do wheel gerado. Nada é redesenhado: se o glifo mudar lá, muda
aqui — inclusive a correção do espelhamento da Lua Negra Lilith.

CASA: a tabela mostra a casa GEOMÉTRICA (a que a mandala desenha), não a
casa de leitura da regra dos 5°. `divergences()` reporta quem diverge para
a Márcia decidir se a tabela sinaliza.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("natal-api")

# ---------------------------------------------------------------- ordem
# NOMES em português. A LISTA DE CORPOS vem de app.ACTIVE_POINTS — não é
# copiada aqui (regra R3: uma lista, um dono). A primeira versão desta
# tabela tinha lista própria e trouxe o Nodo Sul, que produção NÃO desenha:
# 20 corpos contra 19. É a mesma armadilha das duas listas de símbolos que
# fez os glifos sumirem do PDF em silêncio.
BODY_NAME_PT = {
    "Sun": "Sol", "Moon": "Lua", "Mercury": "Mercúrio", "Venus": "Vênus",
    "Mars": "Marte", "Jupiter": "Júpiter", "Saturn": "Saturno",
    "Uranus": "Urano", "Neptune": "Netuno", "Pluto": "Plutão",
    "Chiron": "Quíron", "Mean_Lilith": "Lilith",
    "Mean_North_Lunar_Node": "Nodo Norte",
    "Mean_South_Lunar_Node": "Nodo Sul",
    "Ceres": "Ceres", "Pallas": "Palas", "Juno": "Juno", "Vesta": "Vesta",
    "Ascendant": "Ascendente", "Medium_Coeli": "Meio-do-Céu",
}

# Ordem de leitura do relatório. Corpos que produção ativa e que não
# estiverem aqui entram no fim (nunca somem da tabela).
_READING_ORDER = [
    "Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn",
    "Uranus", "Neptune", "Pluto", "Chiron", "Mean_Lilith",
    "Mean_North_Lunar_Node", "Mean_South_Lunar_Node",
    "Ceres", "Pallas", "Juno", "Vesta", "Ascendant", "Medium_Coeli",
]


def _active_points():
    """A lista de produção. Falha alto em vez de assumir uma cópia."""
    import os, sys
    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)
    from app import ACTIVE_POINTS
    return list(ACTIVE_POINTS)


def body_order():
    """[(slug, nome_pt)] — só os corpos ATIVOS em produção, na ordem do
    relatório."""
    ativos = _active_points()
    ordenados = [s for s in _READING_ORDER if s in ativos]
    ordenados += [s for s in ativos if s not in _READING_ORDER]
    return [(s, BODY_NAME_PT.get(s, s)) for s in ordenados]


SIGN_PT = {
    "Ari": "Áries", "Tau": "Touro", "Gem": "Gêmeos", "Can": "Câncer",
    "Leo": "Leão", "Vir": "Virgem", "Lib": "Libra", "Sco": "Escorpião",
    "Sag": "Sagitário", "Cap": "Capricórnio", "Aqu": "Aquário",
    "Pis": "Peixes",
}
SIGN_ORDER = ["Ari", "Tau", "Gem", "Can", "Leo", "Vir",
              "Lib", "Sco", "Sag", "Cap", "Aqu", "Pis"]

# Elemento e modalidade derivam da POSIÇÃO do signo no zodíaco — não de uma
# tabela paralela que possa sair de sincronia (regra R3).
ELEMENTS = ["Fogo", "Terra", "Ar", "Água"]
MODALITIES = ["Cardinal", "Fixo", "Mutável"]


def element_of(sign_abbr: str) -> str:
    """Áries=Fogo, Touro=Terra, Gêmeos=Ar, Câncer=Água, e repete."""
    return ELEMENTS[SIGN_ORDER.index(sign_abbr) % 4]


def modality_of(sign_abbr: str) -> str:
    """Áries=Cardinal, Touro=Fixo, Gêmeos=Mutável, e repete."""
    return MODALITIES[SIGN_ORDER.index(sign_abbr) % 3]


# CONTAGEM: só os 10 planetas tradicionais + Ascendente + Meio-do-Céu.
# Sem asteroides, sem nodos, sem Lilith, sem Quíron (decisão da Márcia).
COUNTED_BODIES = [
    "Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn",
    "Uranus", "Neptune", "Pluto", "Ascendant", "Medium_Coeli",
]

H_NUM = {
    "First_House": 1, "Second_House": 2, "Third_House": 3,
    "Fourth_House": 4, "Fifth_House": 5, "Sixth_House": 6,
    "Seventh_House": 7, "Eighth_House": 8, "Ninth_House": 9,
    "Tenth_House": 10, "Eleventh_House": 11, "Twelfth_House": 12,
}


# ------------------------------------------------------------ glifos
def extract_symbols(wheel_svg: str) -> dict:
    """Extrai os `<symbol id=…>` do SVG da mandala.

    Retorna {id: conteúdo_interno}. Fonte única: o mesmo SVG que a página
    da mandala usa, já com o pós-processamento aplicado (Lilith espelhada).
    """
    out = {}
    for m in re.finditer(r"<symbol id='([^']+)'>(.*?)</symbol>",
                         wheel_svg, re.DOTALL):
        out[m.group(1)] = m.group(2)
    return out


def glyph_drawing(symbol_body: str, size_pt: float, color: str = "#2F2F2F"):
    """Converte um `<symbol>` do Kerykeion num flowable do ReportLab.

    Os símbolos do Kerykeion vivem num espaço de ~25x25 unidades. Escalamos
    para `size_pt` e forçamos a cor (os defs trazem cores próprias que não
    combinam com a tabela).
    """
    from svglib.svglib import svg2rlg
    import tempfile, os
    # RECOLORIR PRESERVANDO O MODO DE PINTURA. Os símbolos do Kerykeion são
    # de dois tipos: preenchidos (`fill: #x`) e traçados (`stroke: #x;
    # fill: none`). Trocar tudo por `fill` apagava os traçados — o `fill:
    # none` que vem depois anulava o desenho, e Lua/Mercúrio/Vênus/Marte/
    # Júpiter/Saturno saíam INVISÍVEIS na tabela (17/07). Cada atributo é
    # recolorido no seu próprio lugar.
    body = re.sub(r"fill:\s*#[0-9a-fA-F]{3,6}", f"fill: {color}", symbol_body)
    body = re.sub(r"stroke:\s*#[0-9a-fA-F]{3,6}", f"stroke: {color}", body)
    body = re.sub(r"fill='#[0-9a-fA-F]{3,6}'", f"fill='{color}'", body)
    body = re.sub(r"stroke='#[0-9a-fA-F]{3,6}'", f"stroke='{color}'", body)
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='25' height='25' "
           f"viewBox='0 0 25 25'>{body}</svg>")
    fd, path = tempfile.mkstemp(suffix=".svg")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(svg)
        d = svg2rlg(path)
    finally:
        os.unlink(path)
    if d is None:
        return None
    k = size_pt / 25.0
    d.scale(k, k)
    d.width, d.height = size_pt, size_pt
    return d


# ------------------------------------------------------------ dados
def read_positions(subject) -> list:
    """[{slug, nome, sign, sign_pt, house, element, modality}] na ordem do
    relatório. `house` é a GEOMÉTRICA do Kerykeion."""
    rows = []
    for slug, nome in body_order():
        p = getattr(subject, slug.lower(), None)
        if p is None:
            continue
        sign = str(p.sign)
        rows.append({
            "slug": slug, "nome": nome, "sign": sign,
            "sign_pt": SIGN_PT.get(sign, sign),
            "house": H_NUM.get(str(p.house)),
            "element": element_of(sign), "modality": modality_of(sign),
            "abs_pos": float(p.abs_pos),
        })
    return rows


def count_elements_modalities(rows: list) -> tuple:
    """Contagem sobre COUNTED_BODIES. Retorna (elementos, modalidades)."""
    el = {e: 0 for e in ELEMENTS}
    mo = {m: 0 for m in MODALITIES}
    for r in rows:
        if r["slug"] not in COUNTED_BODIES:
            continue
        el[r["element"]] += 1
        mo[r["modality"]] += 1
    return el, mo


def divergences(rows: list, house_reading_moves: list) -> list:
    """Corpos cuja casa de LEITURA (regra dos 5°) difere da GEOMÉTRICA.

    A tabela mostra a geométrica — a mesma que a mandala desenha. Esta
    função existe para reportar a divergência à Márcia; ela decide se a
    tabela sinaliza.
    """
    by_key = {m["planet"]: m for m in (house_reading_moves or [])}
    out = []
    for r in rows:
        k = r["slug"].lower().replace("mean_north_lunar_node", "north_node") \
                             .replace("mean_south_lunar_node", "south_node") \
                             .replace("mean_lilith", "lilith")
        mv = by_key.get(k)
        if mv and mv["to_house"] != r["house"]:
            out.append({"nome": r["nome"], "geometrica": r["house"],
                        "leitura": mv["to_house"],
                        "gap": mv.get("gap_to_cusp")})
    return out


# ------------------------------------------------------------ flowables
def positions_table_flowable(rows, symbols, styles, font_size=9.0,
                             glyph_pt=9.0, col_widths=None):
    """Tabela de posições: glifo+nome do planeta · glifo+nome do signo · casa.

    Tipografia da tabela de aspectos (ESTADO §9.2): EB Garamond em tudo,
    sem separadores de linha, padding vertical apertado.
    """
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib.units import cm
    from reportlab.lib.colors import HexColor

    CHARCOAL = HexColor("#2F2F2F")
    IVORY = HexColor("#F8F5EF")

    data = [["", "Corpo", "", "Signo", "Casa"]]
    for r in rows:
        gp = symbols.get(r["slug"])
        gs = symbols.get(r["sign"])
        data.append([
            glyph_drawing(gp, glyph_pt) if gp else "",
            r["nome"],
            glyph_drawing(gs, glyph_pt) if gs else "",
            r["sign_pt"],
            str(r["house"]) if r["house"] else "—",
        ])
    if col_widths is None:
        col_widths = [0.8 * cm, 3.1 * cm, 0.8 * cm, 3.1 * cm, 1.2 * cm]
    t = Table(data, colWidths=col_widths, hAlign="CENTER")
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), IVORY),
        ("FONTNAME",      (0, 0), (-1, 0), "EBGaramond-Italic"),
        ("FONTSIZE",      (0, 0), (-1, 0), font_size - 1),
        ("TEXTCOLOR",     (0, 0), (-1, 0), CHARCOAL),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING",    (0, 0), (-1, 0), 4),
        ("FONTNAME",      (0, 1), (-1, -1), "EBGaramond-Regular"),
        ("FONTSIZE",      (0, 1), (-1, -1), font_size),
        ("TEXTCOLOR",     (0, 1), (-1, -1), CHARCOAL),
        ("ALIGN",         (0, 0), (0, -1), "CENTER"),
        ("ALIGN",         (2, 0), (2, -1), "CENTER"),
        ("ALIGN",         (4, 0), (4, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("TOPPADDING",    (0, 1), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 2),
    ]))
    return t


def elements_panel_flowable(el, mo, styles, font_size=9.0, col_widths=None,
                            group_gap=0.45):
    """Painel de elementos e modalidades — contagem pura, sem interpretação.

    Duas colunas lado a lado (Elementos | Modalidades), tipografia da tabela
    de aspectos. Sem barras nem gráficos: número e nome.
    """
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib.units import cm
    from reportlab.lib.colors import HexColor
    CHARCOAL = HexColor("#2F2F2F")
    IVORY = HexColor("#F8F5EF")

    linhas = max(len(ELEMENTS), len(MODALITIES))
    # Coluna vazia no meio: separa os dois grupos sem inflar os rótulos.
    data = [["Elementos", "", "", "Modalidades", ""]]
    for i in range(linhas):
        e = ELEMENTS[i] if i < len(ELEMENTS) else ""
        m = MODALITIES[i] if i < len(MODALITIES) else ""
        data.append([e, str(el[e]) if e else "", "",
                     m, str(mo[m]) if m else ""])
    # LARGURAS MEDIDAS (stringWidth a 8.5pt, EB Garamond), não estimadas:
    # maior rótulo de elemento "Terra" = 0.64 cm; de modalidade "Cardinal"
    # = 1.01 cm; número "12" = 0.29 cm. Mais 8pt de padding por coluna.
    # As larguras antigas (2.4/0.9) deixavam ~3.2 cm de vazio sem propósito.
    if col_widths is None:
        col_widths = [1.00 * cm, 0.62 * cm, group_gap * cm,
                      1.35 * cm, 0.62 * cm]
    t = Table(data, colWidths=col_widths, hAlign="CENTER")
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), IVORY),
        ("SPAN",          (0, 0), (1, 0)),
        ("SPAN",          (3, 0), (4, 0)),
        ("FONTNAME",      (0, 0), (-1, 0), "EBGaramond-Italic"),
        ("FONTSIZE",      (0, 0), (-1, 0), font_size - 1),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING",    (0, 0), (-1, 0), 4),
        ("FONTNAME",      (0, 1), (-1, -1), "EBGaramond-Regular"),
        ("FONTSIZE",      (0, 1), (-1, -1), font_size),
        ("TEXTCOLOR",     (0, 0), (-1, -1), CHARCOAL),
        ("ALIGN",         (1, 1), (1, -1), "RIGHT"),
        ("ALIGN",         (4, 1), (4, -1), "RIGHT"),
        ("ALIGN",         (0, 0), (1, 0), "LEFT"),
        ("ALIGN",         (3, 0), (4, 0), "LEFT"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("TOPPADDING",    (0, 1), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 2),
    ]))
    return t


def read_positions_from_points(points: dict) -> list:
    """Mesma saída de `read_positions`, mas a partir do dict `points` que a
    produção já monta — sem reconstruir um subject do Kerykeion (uma fonte,
    um dono).

    USA A CASA DE LEITURA — `house` (decisão da Márcia, terceira vez pedida,
    aplicada em 19/07).

    Antes usava `house_geometric`, a casa que a MANDALA desenha. O resultado
    era o relatório se contradizendo: índice "Júpiter · Casa 11", tabela
    "10". O leitor não tem como saber que são duas perguntas diferentes; ele
    lê duas respostas para a mesma.

    A regra passa a ser uma só: índice, tabela e texto usam a casa de
    LEITURA. Consequência aceita: num corpo de fronteira, a mandala desenha
    o glifo 1º a 2º antes da cúspide enquanto a tabela nomeia a casa
    seguinte — mas essa diferença é de milímetros no desenho, e o texto já
    nomeia a fronteira explicitamente ("entre a casa 10 e a 11, com mais
    força na 11").
    """
    _EN2SIGN = {"aries": "Ari", "taurus": "Tau", "gemini": "Gem",
                "cancer": "Can", "leo": "Leo", "virgo": "Vir",
                "libra": "Lib", "scorpio": "Sco", "sagittarius": "Sag",
                "capricorn": "Cap", "aquarius": "Aqu", "pisces": "Pis"}
    _KEY2SLUG = {"lilith": "Mean_Lilith",
                 "north_node": "Mean_North_Lunar_Node",
                 "south_node": "Mean_South_Lunar_Node"}
    rows = []
    for slug, nome in body_order():
        key = slug.lower()
        for k, v in _KEY2SLUG.items():
            if v == slug:
                key = k
        d = points.get(key)
        if not d:
            continue
        sign = _EN2SIGN.get(str(d.get("sign", "")).lower())
        if not sign:
            continue
        rows.append({
            "slug": slug, "nome": nome, "sign": sign,
            "sign_pt": SIGN_PT[sign],
            "house": d.get("house") or d.get("house_geometric"),
            "element": element_of(sign), "modality": modality_of(sign),
        })
    return rows
