"""ASSERÇÕES SOBRE O PDF QUE SAI — não sobre o código que o constrói.

POR QUE EXISTE (11/08, leitura da Marcelle). A tabela de aspectos SUMIU do
relatório e ninguém percebeu. O código que a constrói continuava lá, os
testes de código continuavam verdes, e o filtro devolvia 0 de 27 porque
procurava uma chave (`planet_a_pt`) que a lista de aspectos não tem.

Nenhuma asserção sobre FONTE pegaria isso: a fonte estava certa em
aparência. Só olhando o artefato — o texto que o PDF de fato renderiza —
a ausência aparece. É o mesmo princípio do lint do PDF, levado às peças
estruturais em vez de à tipografia.

O efeito para a leitora não era cosmético: o texto cita "orbe
apertadíssimo" e "sextil com Plutão" e ela não tinha onde conferir.
"""
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)

import _fixture  # noqa: E402
import pdf_generator as pg  # noqa: E402
from pypdf import PdfReader  # noqa: E402

falhas = []


def checa(nome, cond, detalhe=""):
    print(f"{'OK   ' if cond else 'FALHA'} {nome}" + (f"   → {detalhe}" if detalhe else ""))
    if not cond:
        falhas.append(nome)


# ------------------------------------------------------------------
# Um PDF de verdade, do chart de verdade.
# ------------------------------------------------------------------
_ch = _fixture.build_chart()
_TEXTO = ("# Mapa Natal\n\n"
          "## Abertura\n\nUm parágrafo de abertura com tamanho suficiente "
          "para render uma página de texto corrido sem parecer artificial, "
          "cobrindo o começo do relatório.\n\n"
          "## Seu ideal de eu e suas ferramentas\n\nOutro parágrafo, com o "
          "corpo necessário para exercitar a paginação e o fluxo normal "
          "das seções do relatório.\n\n"
          "## Fio Condutor\n\nFechamento do relatório.\n")

# A MANDALA É OBRIGATÓRIA neste teste: o painel de elementos vive na
# página dela. Sem SVG, o painel não é emitido e a asserção 4 acusaria a
# minha montagem em vez do produto — foi o que aconteceu na primeira
# execução.
import app as _app  # noqa: E402

_svg, _err_svg = _app._generate_chart_svg(dict(_ch, name="Helena Penteado",
                                               datetime="1992-09-18T09:50:00",
                                               latitude=-19.92, longitude=-43.94,
                                               timezone="America/Sao_Paulo"))
assert _svg, f"SVG falhou — sem ele o painel de elementos não sai: {_err_svg}"

_lint = []
_pdf = pg.generate_pdf(
    report_text=_TEXTO, client_name="Helena Penteado",
    birth_date="18 de setembro de 1992", birth_place="Belo Horizonte, MG",
    birth_time="09:50", latitude=-19.92, longitude=-43.94,
    chart_image_url=_svg, aspects=_ch["aspects"], points=_ch["points"],
    time_unknown=False, lint_out=_lint,
)
_arq = os.path.join(os.environ.get("TMPDIR", "/tmp"), "prove_artefato.pdf")
open(_arq, "wb").write(_pdf)
_leitor = PdfReader(_arq)
_paginas = [(p.extract_text() or "") for p in _leitor.pages]
_tudo = "\n".join(_paginas)
print(f"PDF: {len(_leitor.pages)} páginas, {len(_pdf)//1024} KB\n")

# ------------------------------------------------------------------ 1
# A TABELA DE ASPECTOS EXISTE. A regressão que motivou o arquivo.
_n_asp = len(_ch["aspects"])
checa("1. a seção 'Aspectos' aparece no PDF", "Aspectos" in _tudo)

# Conta linhas de aspecto de verdade: um par de corpos com um tipo entre
# eles. Contar a palavra "Aspectos" só provaria que o título saiu — foi
# exatamente o que aconteceu: o título prometia e a tabela não estava lá.
_TIPOS = "conjunção|oposição|trígono|quadratura|sextil"
_linhas = re.findall(rf"\b({_TIPOS})\b", _tudo, flags=re.IGNORECASE)
checa(f"2. o PDF traz linhas de aspecto ({_n_asp} no chart)",
      len(_linhas) >= _n_asp * 0.5,
      f"{len(_linhas)} ocorrência(s) de tipo de aspecto no texto renderizado")

# ------------------------------------------------------------------ 3
# A TABELA DE POSIÇÕES EXISTE.
checa("3. a seção 'Posições' aparece no PDF", "Posições" in _tudo)

# ------------------------------------------------------------------ 4
# PAINEL DE ELEMENTOS INTEIRO NA MESMA PÁGINA. "Água 2" sozinha na p.5
# foi o que a Marcelle viu. Contagem partida ao meio não é contagem.
_ELEM = ["Fogo", "Terra", "Ar", "Água"]
_pag_de = {e: [i for i, p in enumerate(_paginas) if re.search(rf"\b{e}\b", p)]
           for e in _ELEM}
_presentes = {e: v for e, v in _pag_de.items() if v}
if len(_presentes) == len(_ELEM):
    # Existe alguma página que contém os QUATRO? Se o painel rachou, não.
    _juntos = [i for i in range(len(_paginas))
               if all(i in _pag_de[e] for e in _ELEM)]
    checa("4. os 4 elementos saem na MESMA página", bool(_juntos),
          f"páginas por elemento: { {e: v for e, v in _pag_de.items()} }")
else:
    checa("4. os 4 elementos saem na MESMA página", False,
          f"painel ausente ou incompleto: {list(_presentes)}")

# ------------------------------------------------------------------ 5
# O TÍTULO PROMETE SÓ O QUE SAIU. Um título fixo sobrevive ao
# desaparecimento daquilo que nomeia — foi assim que "Posições, aspectos
# e elementos" continuou no sumário depois que os aspectos sumiram.
for _pecas, _esp in (
    (["Posições", "aspectos", "elementos"], "Posições, aspectos e elementos"),
    (["Posições", "elementos"], "Posições e elementos"),
    (["Posições"], "Posições"),
    ([], ""),
):
    checa(f"5. título de {_pecas} → {_esp!r}",
          pg.titulo_referencia(_pecas) == _esp,
          f"deu {pg.titulo_referencia(_pecas)!r}")

# ------------------------------------------------------------------ 6
# REINJEÇÃO: o filtro quebrado volta a derrubar a asserção 2. Prova que
# ela morde, em vez de passar por vacuidade.
_orig = pg.get_in_sign_aspects
try:
    pg.get_in_sign_aspects = lambda a, p=None: [
        x for x in a if x.get("planet_a_pt") and x.get("planet_b_pt")]
    _emitido = []
    pg._positions_page_flowables(_ch["points"], _ch["aspects"],
                                 pg._styles(), emitido_out=_emitido)
    checa("6. reinjeção: com o filtro antigo, 'aspectos' NÃO é emitido",
          "aspectos" not in _emitido, f"emitido={_emitido}")
    checa("6b. e o título deixa de prometer aspectos",
          "aspecto" not in pg.titulo_referencia(_emitido).lower(),
          f"título seria {pg.titulo_referencia(_emitido)!r}")
except Exception as exc:                                # noqa: BLE001
    checa("6. reinjeção roda", False, repr(exc))
finally:
    pg.get_in_sign_aspects = _orig

# ------------------------------------------------------------------ 7
# O lint do artefato segue vazio — a mudança não introduziu defeito de
# tipografia.
checa("7. lint do PDF vazio", not _lint,
      f"{len(_lint)} violação(ões): {[v.get('kind') for v in _lint][:4]}")

# ------------------------------------------------------------------ 8
# TÍTULOS: só a primeira palavra maiúscula, e a primeira DE FATO maiúscula.
#
# O defeito (11/08): o título renderizado é a parte DEPOIS dos
# dois-pontos, que nasce minúscula. Quando a regra mudou de caixa-alta
# para sentença, 14 dos 19 títulos passaram a sair começando em
# minúscula. Índice e corpo compartilham o par, então basta uma asserção
# sobre a função que os dois consomem.
_TITULOS_REAIS = [
    "Abertura", "Sol: sua vitalidade e direção",
    "Sol e Lua: o núcleo emocional-vital", "Sua tríade: Sol, Lua e Ascendente",
    "Mercúrio: como você pensa", "Seu mundo emocional",
    "Casa 4: suas raízes e sua casa interna",
    "Sol e Saturno: seu ideal de eu e suas ferramentas",
    "Vênus e Marte: como você ama e luta pelo que deseja",
    "Júpiter: onde você acredita em si mesmo",
    "Saturno: onde você amadurece com o tempo",
    "Quíron: sua ferida e seu dom", "Urano: onde você não se encaixa",
    "Netuno: onde você se dissolve", "Plutão: onde você precisa de controle",
    "Lilith: onde você deve insistir em ser você",
    "Nodo Sul e Nodo Norte: de onde você vem e para onde vai",
    "Asteroides: Ceres, Vesta, Juno e Palas", "Fio Condutor",
]
_maus = []
for _t in _TITULOS_REAIS:
    _main, _sub = pg._split_section_title(_t, _ch["points"])
    if not _main[:1].isupper():
        _maus.append((_t, _main))
checa(f"8. os {len(_TITULOS_REAIS)} títulos começam com maiúscula",
      not _maus, f"minúsculos: {[m for _, m in _maus][:4]}")

# E o resto NÃO é promovido a caixa-alta: a regra é sentença, não título.
checa("8b. só a PRIMEIRA palavra é tocada",
      pg._split_section_title("Mercúrio: como você pensa",
                              _ch["points"])[0] == "Como você pensa")
# `.capitalize()` rebaixaria "Sol, Lua e Ascendente"; `.title()` devolveria
# a caixa-alta que a Márcia tirou. As duas saídas erradas, travadas.
checa("8c. não rebaixa maiúscula legítima no meio",
      pg.capitaliza_titulo("Sol, Lua e Ascendente") == "Sol, Lua e Ascendente")
checa("8d. não promove o resto a caixa-alta",
      pg.capitaliza_titulo("como você ama e luta") == "Como você ama e luta")

# No PDF de verdade: nenhum título de seção sai começando em minúscula.
_h_minusculos = [ln for ln in _tudo.splitlines()
                 if re.match(r"^[a-zà-ÿ][^.!?]{12,60}$", ln.strip())
                 and ln.strip() in {pg._split_section_title(x, _ch["points"])[0].lower()
                                    for x in _TITULOS_REAIS}]
checa("8e. nenhum título minúsculo no PDF renderizado", not _h_minusculos,
      str(_h_minusculos[:3]))

print()
if falhas:
    print(f">>> {len(falhas)} FALHOU: {falhas}")
    sys.exit(1)
print(">>> ARTEFATO: TUDO PROVADO")
