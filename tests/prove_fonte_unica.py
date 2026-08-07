"""FONTE ÚNICA — a fixture reproduz produção, e produção continua alcançável.

Em 19/07 a fixture mentiu QUATRO vezes num dia, sempre pela mesma causa:
uma rotina de produção morava aninhada dentro de generate_report_endpoint,
inalcançável por teste, então a fixture reimplementava — mal.

  1. aspectos de asteróide  → dizia que Juno da Helena não aspectava nada;
     acusei de inventada a quadratura Mercúrio-Juno, que é real (orbe 0,4°)
  2. regra dos 5°           → chart sem _house_moves; acusei a frase de
     fronteira que o próprio prompt EXIGE
  3. house_geometric        → consequência da mesma ausência
  4. cascata de filtro      → aceitava 4 aspectos que produção DESCARTA

As três primeiras me faziam ver defeito onde não havia. A quarta era pior:
esconderia defeito real ("Mercúrio quadratura Marte" passa limpo aqui,
acusado lá).

Este arquivo trava as duas pontas:
  A) o chart da fixture bate com o snapshot REAL de produção
  B) nenhuma rotina de produção nova se esconde dentro de um endpoint
"""
import warnings; warnings.filterwarnings("ignore")
import ast
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app
from _fixture import build_chart, HELENA

falhas = 0


def ok(msg):
    print(f"  OK      {msg}")


def bad(msg):
    global falhas
    falhas += 1
    print(f"  ERRADO  {msg}")


print("=" * 62)
print("A) A FIXTURE REPRODUZ PRODUÇÃO?")
print("=" * 62)

# Snapshot da geração REAL da Helena (19/07). Não é valor inventado: veio do
# meta devolvido pelo endpoint em produção. Regra R1.
PROD_KEPT = 27
PROD_DROPPED = {
    ("mars", "mercury", "square"): "out_of_sign_dissociated",
    ("lilith", "mars", "trine"): "out_of_sign_dissociated",
    ("chiron", "lilith", "opposition"): "above_applying_threshold_not_applying",
    ("juno", "mars", "conjunction"): "out_of_sign_dissociated",
}
PROD_MOVES = {("jupiter", 10, 11), ("ceres", 2, 3)}

ch = build_chart(HELENA)

n = len(ch.get("aspects") or [])
if n == PROD_KEPT:
    ok(f"aspectos mantidos: {n} (igual a produção)")
else:
    bad(f"aspectos mantidos: {n}, produção entrega {PROD_KEPT} — "
        f"a fixture voltou a divergir da cascata de filtro")

got = {(tuple(sorted((d["planet_a"], d["planet_b"]))) + (d["type"],)): d["reason"]
       for d in (ch.get("_dropped_aspects") or [])}
if got == PROD_DROPPED:
    ok(f"descartes: {len(got)}, com os mesmos motivos de produção")
else:
    bad(f"descartes divergem.\n            fixture={got}\n            produção={PROD_DROPPED}")

moves = {(m["planet"], m["from_house"], m["to_house"])
         for m in (ch.get("_house_moves") or [])}
if moves == PROD_MOVES:
    ok(f"regra dos 5°: {sorted(moves)}")
else:
    bad(f"regra dos 5° diverge: fixture={sorted(moves)} produção={sorted(PROD_MOVES)}")

if all(p.get("house_geometric") for p in ch["points"].values() if p.get("house")):
    ok("house_geometric gravado em todos os corpos com casa")
else:
    bad("há corpo com casa e sem house_geometric — a tabela do PDF herdaria "
        "a casa de leitura e contradiria a mandala")

# A quadratura Mercúrio-Juno é REAL (orbe 0,4°) e o Kerykeion não a gera.
# Se a fixture parar de computar aspectos de asteróide, isto apaga.
import text_verifier as tv
if [t for o, t in tv._aspectos_reais_de(ch, "juno") if o == "mercury"]:
    ok("aspectos de asteróide presentes (Juno quadratura Mercúrio)")
else:
    bad("Juno voltou a não aspectar nada — a fixture parou de computar "
        "aspectos de asteróide")

print()
print("=" * 62)
print("B) ALGUMA ROTINA DE PRODUÇÃO NOVA SE ESCONDEU NUM ENDPOINT?")
print("=" * 62)
# Guarda de CLASSE, não do exemplo. Função aninhada pequena é helper legítimo;
# acima deste tamanho é lógica de produção que um teste não alcança — foi
# exatamente essa a forma das quatro mentiras.
LIMITE_LINHAS = 25
tree = ast.parse(open(os.path.join(os.path.dirname(__file__), "..", "app.py"),
                     encoding="utf-8").read())
grandes = []
for top in tree.body:
    if not isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for node in ast.walk(top):
        if node is top or not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        n_linhas = (node.end_lineno or node.lineno) - node.lineno + 1
        if n_linhas > LIMITE_LINHAS:
            grandes.append((top.name, node.name, node.lineno, n_linhas))

if not grandes:
    ok(f"nenhuma função aninhada acima de {LIMITE_LINHAS} linhas")
else:
    for pai, f, l, n in grandes:
        bad(f"{f}() tem {n} linhas dentro de {pai}() (app.py:{l}) — "
            f"extrair para o nível do módulo antes que a fixture a reimplemente")

print()
if falhas:
    print(f">>> {falhas} FALHOU — a fixture e produção divergiram de novo")
    raise SystemExit(1)
print("FONTE ÚNICA 19/07: TUDO PROVADO")
