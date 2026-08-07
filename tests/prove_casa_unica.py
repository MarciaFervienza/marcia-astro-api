"""CASA ÚNICA — índice == tabela == texto, para TODO corpo, em TODO mapa.

Terceira vez que a Márcia reporta a mesma contradição: índice "Júpiter ·
Casa 11", tabela "Casa 10". Nas duas primeiras eu registrei a decisão e não
mexi no código — `read_positions_from_points` seguia lendo
`house_geometric`, e `_subtitle_from_prefix` lia `house`. Duas perguntas
diferentes, uma resposta para cada, e o leitor sem como saber disso.

Decisão da Márcia: os três usam a CASA DE LEITURA (`house`, já
re-atribuída pela regra dos 5°).

Isto é property test, não exemplo: percorre TODOS os corpos de TODOS os
mapas de QA. Um corpo de fronteira novo em qualquer mapa futuro cai aqui.
"""
import warnings; warnings.filterwarnings("ignore")
import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf_generator as pg
import positions_table as pt
from _fixture import build_chart, HELENA, LUCCA

falhas = 0


def bad(msg):
    global falhas
    falhas += 1
    print(f"  ERRADO  {msg}")


MAPAS = [("Helena", HELENA), ("Lucca", LUCCA)]

print("=" * 64)
print("A) TABELA E ÍNDICE DÃO A MESMA CASA PARA TODO CORPO")
print("=" * 64)

for nome_mapa, dados in MAPAS:
    ch = build_chart(dados)
    pts = ch["points"]
    linhas = pt.read_positions_from_points(pts)
    conferidos = 0
    for r in linhas:
        # índice: o subtítulo é montado a partir do nome do corpo
        sub = pg._subtitle_from_prefix(r["nome"], pts)
        m = re.search(r"Casa\s+(\d+)", sub or "")
        if not m:
            continue                     # corpo sem subtítulo (Ascendente etc.)
        casa_indice = int(m.group(1))
        conferidos += 1
        if casa_indice != r["house"]:
            bad(f"{nome_mapa} {r['nome']}: índice diz casa {casa_indice}, "
                f"tabela diz {r['house']}")
    print(f"  {nome_mapa}: {conferidos} corpos com casa nos dois lugares, "
          f"{len(linhas)} linhas na tabela")

print()
print("=" * 64)
print("B) A CASA MOSTRADA É A DE LEITURA, NÃO A GEOMÉTRICA")
print("=" * 64)
# Sem esta parte, (A) passaria se ambos voltassem a usar a geométrica —
# concordariam entre si e contradiriam o TEXTO, que usa a de leitura.
for nome_mapa, dados in MAPAS:
    ch = build_chart(dados)
    pts = ch["points"]
    tabela = {r["slug"].lower(): r["house"] for r in
              pt.read_positions_from_points(pts)}
    for mv in ch["_house_moves"]:
        k = mv["planet"]
        p = pts.get(k) or {}
        leitura, geom = p.get("house"), p.get("house_geometric")
        if leitura == geom:
            bad(f"{nome_mapa} {k}: a regra dos 5° diz {mv['from_house']}→"
                f"{mv['to_house']} mas house == house_geometric")
            continue
        # acha a linha da tabela deste corpo
        alvo = [r for r in pt.read_positions_from_points(pts)
                if r["slug"].lower().replace("mean_", "").startswith(k[:5])]
        if not alvo:
            continue
        casa_tab = alvo[0]["house"]
        if casa_tab != leitura:
            bad(f"{nome_mapa} {k}: tabela mostra {casa_tab}, "
                f"casa de LEITURA é {leitura} (geométrica {geom})")
        else:
            print(f"  {nome_mapa} {k}: leitura {leitura} "
                  f"(geométrica {geom}) — tabela mostra {casa_tab}")

print()
print("=" * 64)
print("C) O TEXTO NOMEIA A MESMA CASA")
print("=" * 64)
# O prompt manda o texto nomear a FRONTEIRA para corpos movidos ("entre a
# casa 10 e a 11, com mais força na 11"). A casa forte tem de ser a de
# leitura — a mesma da tabela e do índice.
import report_generator as rg
for nome_mapa, dados in MAPAS:
    ch = build_chart(dados)
    for mv in ch["_house_moves"]:
        k = mv["planet"]
        instr = rg.fmt_position(ch["points"][k], k, ch)
        casas = [int(x) for x in re.findall(r"casa\s+(\d+)", instr, re.I)]
        if mv["to_house"] not in casas:
            bad(f"{nome_mapa} {k}: a instrução ao modelo não cita a casa de "
                f"leitura {mv['to_house']}. Instrução: {instr[:150]!r}")
        else:
            print(f"  {nome_mapa} {k}: instrução cita a casa de leitura "
                  f"{mv['to_house']} ✓")

print()
if falhas:
    print(f">>> {falhas} FALHOU — índice, tabela e texto divergiram de novo")
    raise SystemExit(1)
print("CASA ÚNICA 19/07: TUDO PROVADO")
