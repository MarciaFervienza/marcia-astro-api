"""PROVA DA EXTRAÇÃO DO NÚCLEO DE GERAÇÃO (19/07).

O `/generate-report` e o worker da fila têm de gerar o MESMO relatório.
Duas implementações é a R3 — a classe em que o produto e o teste divergem
em silêncio. Estas asserções existem para que a divergência não possa
nascer sem alguém ver.

A asserção que motiva o arquivo é a 4: `worker.py` chamava três funções
de `app.py` que NÃO EXISTIAM. O worker teria subido e morrido na primeira
linha, e isso só apareceria depois de o serviço estar criado. Contrato
entre módulos que ninguém verifica é contrato que não existe.
"""
import ast
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
API = os.path.dirname(AQUI)
sys.path.insert(0, API)

falhas = []


def checa(nome, cond, detalhe=""):
    print(f"{'OK   ' if cond else 'FALHA'} {nome}" + (f"   → {detalhe}" if detalhe else ""))
    if not cond:
        falhas.append(nome)


def _arvore(caminho):
    return ast.parse(open(caminho, encoding="utf-8").read())


# ---------------------------------------------------------------- 1
# O núcleo existe e é UM só.
import app  # noqa: E402

checa("1. executar_geracao existe", hasattr(app, "executar_geracao"))
_t = _arvore(os.path.join(API, "app.py"))
_defs = [n.name for n in _t.body if isinstance(n, ast.FunctionDef)]
checa("1b. executar_geracao definida UMA vez",
      _defs.count("executar_geracao") == 1,
      f"{_defs.count('executar_geracao')} definição(ões)")

# ---------------------------------------------------------------- 2
# O núcleo não toca no request HTTP. Se tocasse, funcionaria no endpoint
# e levantaria no worker — onde não há contexto de requisição.
_fn = next(n for n in _t.body
           if isinstance(n, ast.FunctionDef) and n.name == "executar_geracao")
_PROIBIDO = {"request", "jsonify", "abort", "session", "key_from_body"}
_achados = sorted({n.id for n in ast.walk(_fn)
                   if isinstance(n, ast.Name) and n.id in _PROIBIDO})
checa("2. núcleo sem acoplamento HTTP", not _achados, str(_achados))

# ---------------------------------------------------------------- 3
# Todo return do núcleo devolve (corpo, http) — o par que o Flask espera
# e que o adaptador da fila sabe traduzir. Um return solto quebraria os
# DOIS caminhos de uma vez.
def _returns_do_corpo(fn):
    aninhadas = {id(x) for n in ast.walk(fn) if isinstance(n, ast.FunctionDef)
                 and n is not fn for x in ast.walk(n)}
    return [r for r in ast.walk(fn)
            if isinstance(r, ast.Return) and id(r) not in aninhadas]


_rets = _returns_do_corpo(_fn)
_maus = [r.lineno for r in _rets
         if not (isinstance(r.value, ast.Tuple) and len(r.value.elts) == 2)]
checa("3. todo return do núcleo é (corpo, http)", not _maus,
      f"{len(_rets)} returns; fora do contrato: {_maus or 'nenhum'}")

# ---------------------------------------------------------------- 4
# CONTRATO WORKER → APP. Toda função `_app.X(...)` que o worker chama
# tem de existir em app.py. Esta é a asserção que faltava.
_w = _arvore(os.path.join(API, "worker.py"))
_chamadas = sorted({n.func.attr for n in ast.walk(_w)
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "_app"})
_ausentes = [c for c in _chamadas if not hasattr(app, c)]
checa("4. toda função que o worker chama existe em app.py", not _ausentes,
      f"chama {_chamadas}; ausentes: {_ausentes or 'nenhuma'}")

# Mesma checagem para a fila.
_metodos_fila = sorted({n.func.attr for n in ast.walk(_w)
                        if isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and isinstance(n.func.value, ast.Name)
                        and n.func.value.id in ("fila", "f")})
import fila as _filamod  # noqa: E402
_ausentes_f = [m for m in _metodos_fila if not hasattr(_filamod.Fila, m)]
checa("4b. todo método de fila que o worker chama existe", not _ausentes_f,
      f"chama {_metodos_fila}; ausentes: {_ausentes_f or 'nenhum'}")

# ---------------------------------------------------------------- 5
# O núcleo devolve tupla de verdade, não uma resposta Flask. Payload
# vazio → recusa 400, fora de qualquer contexto de requisição.
_r = app.executar_geracao({}, {"ip": "teste", "ua": "teste"})
checa("5. núcleo roda FORA de contexto de requisição",
      isinstance(_r, tuple) and len(_r) == 2 and isinstance(_r[0], dict)
      and isinstance(_r[1], int),
      f"devolveu {type(_r).__name__} http={_r[1] if isinstance(_r, tuple) else '?'}")

# ---------------------------------------------------------------- 6
# O adaptador da fila traduz o par para o vocabulário da fila, e uma
# recusa que NÃO é de língua tem de virar `ok=False`. A primeira versão
# do worker olhava só `falha_lingua` e teria marcado como CONCLUÍDO um
# trabalho recusado por cidade ambígua, idade ou geocoding.
_a = app.executar_geracao_para_fila({}, {"ip": "teste", "ua": "teste"})
checa("6. adaptador marca ok=False em recusa não-linguística",
      _a["ok"] is False and _a["falha_lingua"] is None and bool(_a["erro"]),
      f"ok={_a['ok']} http={_a['http']} falha_lingua={_a['falha_lingua']!r}")
checa("6b. adaptador devolve o chart mutado", isinstance(_a["chart"], dict))

# ---------------------------------------------------------------- 7
# Ligação das rotas. Já me custou um /env-check em 500 porque um decorador
# ficou preso na função errada.
_regras = {r.rule: r.endpoint for r in app.app.url_map.iter_rules()}
for _rota, _alvo in (("/generate-report", "generate_report_endpoint"),
                     ("/status/<tid>", "status_trabalho_endpoint"),
                     ("/remontar-pdf", "remontar_pdf_endpoint")):
    checa(f"7. {_rota} → {_alvo}", _regras.get(_rota) == _alvo,
          f"ligada a {_regras.get(_rota)!r}")

# ---------------------------------------------------------------- 8
# O interruptor nasce DESLIGADO. Enfileirar sem worker de pé é aceitar
# pedido que ninguém processa — pior que demorar.
checa("8. FILA_ASSINCRONA desligada por padrão",
      app.FILA_ASSINCRONA is False or os.environ.get("FILA_ASSINCRONA"),
      f"FILA_ASSINCRONA={app.FILA_ASSINCRONA}")

# ---------------------------------------------------------------- 9
# A falha fechada carrega o markdown. Sem ele o degrau 3 não tem o que
# remontar e a recusa custa uma geração inteira em vez de uma frase.
_src_fc = open(os.path.join(API, "app.py"), encoding="utf-8").read()
_i = _src_fc.index('"code": "lingua_falha_fechada"')
_bloco = _src_fc[_i:_i + 900]
checa("9. falha fechada devolve o markdown para o degrau 3",
      '"report": result.get("report")' in _bloco)

# ---------------------------------------------------------------- 10
# REINJEÇÃO: se o núcleo voltar a depender do request, a asserção 2 tem
# de cair. Prova que ela morde, em vez de passar por vacuidade.
_fn_falso = ast.parse("def executar_geracao(body, ctx=None):\n"
                      "    return jsonify({'a': 1}), 400\n").body[0]
_reinj = sorted({n.id for n in ast.walk(_fn_falso)
                 if isinstance(n, ast.Name) and n.id in _PROIBIDO})
checa("10. reinjeção: núcleo acoplado ao HTTP É detectado",
      _reinj == ["jsonify"], f"detectou {_reinj}")

print()
if falhas:
    print(f">>> {len(falhas)} FALHOU: {falhas}")
    sys.exit(1)
print(">>> extração provada")
